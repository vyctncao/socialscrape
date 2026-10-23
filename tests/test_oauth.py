import base64
import hashlib
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

from socialscrape.config import get_settings
from socialscrape.main import create_app


@pytest.fixture
def oauth_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_PATH", str(tmp_path / "portal.sqlite3"))
    monkeypatch.setenv("SOCIALSCRAPE_PUBLIC_URL", "https://scrape.mimic.host")
    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "")
    monkeypatch.setenv("SOCIALSCRAPE_CACHE_PATH", "")
    monkeypatch.setenv("SOCIALSCRAPE_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("SOCIALSCRAPE_OWNER_PASSWORD", "correct horse battery")
    get_settings.cache_clear()
    with TestClient(create_app(), base_url="https://scrape.mimic.host") as client:
        yield client
    get_settings.cache_clear()


def register(client, redirect="http://127.0.0.1:8765/callback"):
    response = client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none"})
    assert response.status_code == 201
    return response.json()["client_id"]


def pkce(verifier="v" * 43):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def authorize_params(client_id, redirect="http://127.0.0.1:8765/callback", verifier="v" * 43):
    return {"response_type": "code", "client_id": client_id, "redirect_uri": redirect,
            "code_challenge": pkce(verifier), "code_challenge_method": "S256", "scope": "mcp:tools",
            "resource": "https://scrape.mimic.host/mcp", "state": "opaque-state"}


def login(client):
    response = client.post("/login", data={"email": "owner@example.com", "password": "correct horse battery"},
                           follow_redirects=False)
    assert response.status_code == 303


def approve(client, params, csrf=None):
    page = client.get("/oauth/authorize", params=params)
    assert page.status_code == 200
    assert page.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert page.headers["x-frame-options"] == "DENY"
    csrf = csrf if csrf is not None else re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    response = client.post("/oauth/authorize", data={**params, "csrf": csrf, "decision": "approve"},
                           follow_redirects=False)
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)["code"][0]


def exchange(client, client_id, code, verifier="v" * 43, redirect="http://127.0.0.1:8765/callback"):
    return client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": client_id,
        "code": code, "redirect_uri": redirect, "code_verifier": verifier,
        "resource": "https://scrape.mimic.host/mcp"})


def test_full_oauth_flow_and_refresh(oauth_client):
    client = oauth_client
    metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert metadata == {"resource": "https://scrape.mimic.host/mcp",
        "authorization_servers": ["https://scrape.mimic.host/"], "scopes_supported": ["mcp:tools"],
        "resource_name": "SocialScrape MCP Server"}
    assert client.get("/.well-known/oauth-protected-resource").json() == metadata
    server = client.get("/.well-known/oauth-authorization-server").json()
    assert server["issuer"] == "https://scrape.mimic.host/"
    assert server["registration_endpoint"] == "https://scrape.mimic.host/oauth/register"

    client_id = register(client)
    params = authorize_params(client_id)
    unauthenticated = client.get("/oauth/authorize", params=params, follow_redirects=False)
    assert unauthenticated.status_code == 303
    assert unauthenticated.headers["location"].startswith("/login?next=")
    login(client)
    session_only = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert session_only.status_code == 401
    assert "resource_metadata=" in session_only.headers["www-authenticate"]
    code = approve(client, params)
    token_response = exchange(client, client_id, code)
    assert token_response.status_code == 200
    tokens = token_response.json()
    assert tokens["token_type"] == "Bearer" and tokens["scope"] == "mcp:tools"
    assert token_response.headers["cache-control"] == "no-store"
    assert token_response.headers["pragma"] == "no-cache"

    for scheme in ("Bearer", "bearer", "BEARER"):
        listed = client.post("/mcp", headers={"Authorization": scheme + " " + tokens["access_token"]},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert listed.status_code == 200 and len(listed.json()["result"]["tools"]) == 70

    listed = client.post("/mcp", headers={"Authorization": "Bearer " + tokens["access_token"]},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert listed.status_code == 200 and len(listed.json()["result"]["tools"]) == 70
    assert client.get("/v1/account/credit-balance",
                      headers={"Authorization": "Bearer " + tokens["access_token"]}).status_code == 200

    refreshed = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
        "refresh_token": tokens["refresh_token"], "resource": "https://scrape.mimic.host/mcp"})
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]


def test_registration_and_authorization_validation(oauth_client):
    client = oauth_client
    for redirect in ("http://evil.example/cb", "https://user@evil.example/cb", "https://*.example/cb",
                     "https://example.com/cb#fragment", "not-a-uri"):
        assert client.post("/oauth/register", json={"redirect_uris": [redirect]}).status_code == 400
    client_id = register(client)
    good = authorize_params(client_id)
    login(client)
    assert client.get("/oauth/authorize", params={**good, "client_id": "unknown"}).status_code == 400
    assert client.get("/oauth/authorize", params={**good, "redirect_uri": "https://evil.example/cb"}).status_code == 400
    assert client.get("/oauth/authorize", params={**good, "code_challenge_method": "plain"}).status_code == 400
    assert client.get("/oauth/authorize", params={k: v for k, v in good.items() if k != "code_challenge"}).status_code == 400
    for challenge in ("a" * 42, "a" * 44, "a" * 42 + "."):
        response = client.get("/oauth/authorize", params={**good, "code_challenge": challenge})
        assert response.status_code == 400
        assert response.headers["pragma"] == "no-cache"
    assert client.post("/oauth/authorize", data={**good, "decision": "approve"}).status_code == 403


def test_pkce_verifier_boundaries(oauth_client):
    client = oauth_client
    client_id = register(client)
    login(client)
    for verifier in ("a" * 43, "b" * 128, "A._~-" * 9):
        code = approve(client, authorize_params(client_id, verifier=verifier))
        assert exchange(client, client_id, code, verifier=verifier).status_code == 200
    for verifier in ("a", "a" * 42, "a" * 129, "a" * 42 + "!"):
        code = approve(client, authorize_params(client_id))
        response = exchange(client, client_id, code, verifier=verifier)
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"
        assert response.headers["pragma"] == "no-cache"


def test_code_binding_single_use_and_expiry(oauth_client):
    client = oauth_client
    client_id = register(client)
    login(client)
    params = authorize_params(client_id)
    code = approve(client, params)
    assert exchange(client, client_id, code, verifier="wrong" * 9).status_code == 400
    assert exchange(client, client_id, code, redirect="http://127.0.0.1:9999/callback").status_code == 400
    assert exchange(client, "unknown", code).status_code == 400
    first = exchange(client, client_id, code)
    assert first.status_code == 200
    assert exchange(client, client_id, code).status_code == 400

    expired = approve(client, params)
    store = client.app.state.portal
    store._conn.execute("UPDATE oauth_codes SET expires_at=0 WHERE code_hash=?", (store._token_hash(expired),))
    store._conn.commit()
    assert exchange(client, client_id, expired).status_code == 400


def test_refresh_reuse_revokes_family(oauth_client):
    client = oauth_client
    client_id = register(client)
    login(client)
    tokens = exchange(client, client_id, approve(client, authorize_params(client_id))).json()
    request = {"grant_type": "refresh_token", "client_id": client_id,
               "refresh_token": tokens["refresh_token"], "resource": "https://scrape.mimic.host/mcp"}
    rotated = client.post("/oauth/token", data=request)
    assert rotated.status_code == 200
    assert client.post("/oauth/token", data=request).status_code == 400
    access = rotated.json()["access_token"]
    denied = client.post("/mcp", headers={"Authorization": "Bearer " + access},
                         json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert denied.status_code == 401


def test_removed_member_and_safe_login_next(oauth_client):
    client = oauth_client
    assert client.get("/login", params={"next": "//evil.example/"}).status_code == 200
    response = client.post("/login", data={"email": "owner@example.com", "password": "correct horse battery",
                                           "next": "https://evil.example/"}, follow_redirects=False)
    assert response.headers["location"] == "/app"

    store = client.app.state.portal
    member_id = store.create_member("member@example.com", "another strong password")
    client.cookies.clear()
    client.post("/login", data={"email": "member@example.com", "password": "another strong password"})
    client_id = register(client)
    tokens = exchange(client, client_id, approve(client, authorize_params(client_id))).json()
    store._conn.execute("DELETE FROM members WHERE id=?", (member_id,))
    store._conn.commit()
    denied = client.post("/mcp", headers={"Authorization": "Bearer " + tokens["access_token"]},
                         json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert denied.status_code == 401
    assert "resource_metadata=" in denied.headers["www-authenticate"]
