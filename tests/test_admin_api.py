import base64
import json

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

SECRET_PROXY = "http://user:secret-pass@proxy.example:8080"


def admin_client(monkeypatch, tmp_path):
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_PATH", str(tmp_path / "portal.sqlite3"))
    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "legacy")
    get_settings.cache_clear()
    app = create_app()
    store = app.state.portal
    owner = store.create_member("owner@example.com", "owner-password", "owner")
    member = store.create_member("member@example.com", "member-password")
    _, owner_key = store.create_key(owner, "owner")
    _, member_key = store.create_key(member, "member")
    monkeypatch.setattr("socialscrape.portal._test_proxy", lambda platform, proxy: {
        "ok": True, "connected": True, "status": 200, "blocked": False, "exit_ip": "203.0.113.4",
        "error": None, "message": "", "latency_ms": 12})

    async def check(platform, account, proxy):
        return {"ok": True, "status": "alive", "reason": None, "account": "fixture", "limited": False,
                "checked_at": 1.0, "latency_ms": 5, "supported": True}

    monkeypatch.setattr("socialscrape.sessions.check", check)
    return TestClient(app), store, owner, owner_key, member_key, get_settings


def test_owner_key_manages_proxies_without_exposing_or_recording_them(monkeypatch, tmp_path):
    client, store, owner, owner_key, _, clear = admin_client(monkeypatch, tmp_path)
    headers = {"x-api-key": owner_key}
    try:
        empty = client.get("/v1/admin/platforms", headers=headers).json()
        assert empty["success"] and empty["max_entries"] == 5
        assert empty["platforms"]["instagram"] == {"proxies": [], "accounts": []}
        first = client.post("/v1/admin/platforms/instagram/proxies", headers=headers, json={"proxy": SECRET_PROXY})
        assert first.status_code == 201 and first.json()["position"] == 1 and first.json()["test"]["ok"]
        second = client.post("/v1/admin/platforms/instagram/proxies", headers=headers,
                             json={"proxy": "socks5://proxy-two.example:1080", "test": False})
        assert second.status_code == 201 and second.json()["test"] is None
        listed = client.get("/v1/admin/platforms", headers=headers)
        assert [item["id"] for item in listed.json()["platforms"]["instagram"]["proxies"]] == \
            [first.json()["id"], second.json()["id"]]
        assert "secret-pass" not in first.text + listed.text
        tested = client.post(f"/v1/admin/platforms/instagram/proxies/{first.json()['id']}/test", headers=headers)
        assert tested.json() == {"success": True, "ok": True, "connected": True, "status": 200, "blocked": False,
                                 "exit_ip": "203.0.113.4", "error": None, "message": "", "latency_ms": 12}
        moved = client.post(f"/v1/admin/platforms/instagram/proxies/{second.json()['id']}/move", headers=headers,
                            json={"direction": "up"})
        assert moved.json() == {"success": True, "moved": True}
        assert client.post(f"/v1/admin/platforms/instagram/proxies/{second.json()['id']}/move", headers=headers,
                           json={"direction": "up"}).status_code == 404
        removed = client.delete(f"/v1/admin/platforms/instagram/proxies/{first.json()['id']}", headers=headers)
        assert removed.json() == {"success": True, "removed": True}
        assert client.delete(f"/v1/admin/platforms/instagram/proxies/{first.json()['id']}",
                             headers=headers).status_code == 404
        principal = store.authenticate_key(owner_key)[0]
        assert not [row for row in store.history(principal, 200) if row["path"].startswith("/v1/admin")]
        assert b"secret-pass" not in (tmp_path / "portal.sqlite3").read_bytes()
    finally:
        clear.cache_clear()


def test_admin_api_requires_an_owner_api_key(monkeypatch, tmp_path):
    client, _, _, _, member_key, clear = admin_client(monkeypatch, tmp_path)
    try:
        for headers in ({"x-api-key": member_key}, {"x-api-key": "legacy"}):
            denied = client.get("/v1/admin/platforms", headers=headers)
            assert denied.status_code == 403 and denied.json()["error"] == "owner_api_key_required"
        assert client.get("/v1/admin/platforms").status_code == 401
        signed_in = client.post("/login", data={"email": "owner@example.com", "password": "owner-password"},
                                follow_redirects=False)
        assert signed_in.status_code == 303
        csrf = client.get("/portal/api/me").json()["csrf"]
        session = client.post("/v1/admin/platforms/instagram/proxies", headers={"x-csrf-token": csrf},
                              json={"proxy": SECRET_PROXY})
        assert session.status_code == 403 and session.json()["error"] == "owner_api_key_required"
    finally:
        clear.cache_clear()


def test_accounts_accept_browser_exports_and_validate_inputs(monkeypatch, tmp_path):
    client, _, _, owner_key, _, clear = admin_client(monkeypatch, tmp_path)
    headers = {"x-api-key": owner_key}
    try:
        export = [{"domain": ".instagram.com", "name": "sessionid", "value": "42%3Asecret-session", "path": "/",
                   "secure": True, "hostOnly": False, "expirationDate": 1900000000.5},
                  {"domain": ".instagram.com", "name": "ds_user_id", "value": "42", "path": "/"}]
        encoded = base64.b64encode(json.dumps(export).encode()).decode()
        added = client.post("/v1/admin/platforms/instagram/accounts", headers=headers, json={"cookies": encoded})
        assert added.status_code == 201 and added.json()["check"]["status"] == "alive"
        assert "secret-session" not in added.text
        header = client.post("/v1/admin/platforms/tiktok/accounts", headers=headers,
                             json={"cookies": "Cookie: sessionid=secret-tiktok; sid_tt=secret-tiktok"})
        assert header.status_code == 201
        listed = client.get("/v1/admin/platforms", headers=headers).json()["platforms"]
        assert listed["instagram"]["accounts"][0]["id"] == added.json()["id"]
        assert "secret" not in json.dumps(listed)
        checked = client.post(f"/v1/admin/platforms/instagram/accounts/{added.json()['id']}/check", headers=headers)
        assert checked.json()["success"] and checked.json()["status"] == "alive"
        bad = client.post("/v1/admin/platforms/instagram/accounts", headers=headers,
                          json={"cookies": "not a cookie export secret-input"})
        assert bad.status_code == 400 and "secret-input" not in bad.text
        assert client.post("/v1/admin/platforms/myspace/proxies", headers=headers,
                           json={"proxy": SECRET_PROXY}).status_code == 404
        assert client.post("/v1/admin/platforms/instagram/keys", headers=headers, json={}).status_code == 404
        assert client.post("/v1/admin/platforms/instagram/proxies", headers=headers, json={}).status_code == 400
        assert client.post("/v1/admin/platforms/instagram/proxies", headers=headers,
                           content="not json").status_code == 400
        assert client.post(f"/v1/admin/platforms/instagram/accounts/{added.json()['id']}/move", headers=headers,
                           json={"direction": "sideways"}).status_code == 400
    finally:
        clear.cache_clear()


def rpc(method, params=None):
    return {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params is not None else {})}


def test_mcp_admin_tools_are_owner_key_only(monkeypatch, tmp_path):
    client, _, _, owner_key, member_key, clear = admin_client(monkeypatch, tmp_path)
    try:
        owner_tools = client.post("/mcp", headers={"x-api-key": owner_key}, json=rpc("tools/list")).json()
        names = {tool["name"] for tool in owner_tools["result"]["tools"]}
        assert {"admin_add_proxy", "admin_add_account", "admin_remove_platform_setting"} <= names
        remove_tool = next(tool for tool in owner_tools["result"]["tools"]
                           if tool["name"] == "admin_remove_platform_setting")
        assert remove_tool["annotations"]["destructiveHint"] is True
        for key in (member_key, "legacy"):
            tools = client.post("/mcp", headers={"x-api-key": key}, json=rpc("tools/list")).json()["result"]["tools"]
            assert not [tool for tool in tools if tool["name"].startswith("admin_")]
        call = rpc("tools/call", {"name": "admin_add_proxy",
                                  "arguments": {"platform": "youtube", "proxy": SECRET_PROXY, "test": False}})
        added = client.post("/mcp", headers={"x-api-key": owner_key}, json=call).json()["result"]
        assert not added.get("isError") and "secret-pass" not in json.dumps(added)
        entry = json.loads(added["content"][0]["text"])["id"]
        denied = client.post("/mcp", headers={"x-api-key": member_key}, json=call).json()["result"]
        assert denied["isError"]
        invalid = client.post("/mcp", headers={"x-api-key": owner_key}, json=rpc("tools/call", {
            "name": "admin_remove_platform_setting",
            "arguments": {"platform": "youtube", "kind": "proxies", "id": "../keys"}})).json()["result"]
        assert invalid["isError"]
        removed = client.post("/mcp", headers={"x-api-key": owner_key}, json=rpc("tools/call", {
            "name": "admin_remove_platform_setting",
            "arguments": {"platform": "youtube", "kind": "proxies", "id": entry}})).json()["result"]
        assert json.loads(removed["content"][0]["text"]) == {"success": True, "removed": True}
    finally:
        clear.cache_clear()
