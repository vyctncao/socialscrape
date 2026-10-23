"""OAuth 2.1 authorization-code support for MCP clients."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import sqlite3
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .config import get_settings
from .portal import SESSION_COOKIE, Principal, _page

SCOPE = "mcp:tools"
CODE_SECONDS = 600
ACCESS_SECONDS = 3600
REFRESH_SECONDS = 60 * 60 * 24 * 30


def origin_for(request: Request) -> str:
    return get_settings().public_url or str(request.base_url).rstrip("/")


def resource_for(request: Request) -> str:
    return origin_for(request) + "/mcp"


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _redirect(uri: str, **values: str) -> RedirectResponse:
    parsed = urlsplit(uri)
    query = parse_qsl(parsed.query, keep_blank_values=True) + [(key, value) for key, value in values.items() if value]
    return RedirectResponse(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")), 303,
                            headers={"Cache-Control": "no-store"})


def _valid_redirect(uri: str) -> bool:
    try:
        parsed = urlsplit(uri)
        _ = parsed.port
    except ValueError:
        return False
    if not parsed.scheme or not parsed.hostname or parsed.fragment or parsed.username or parsed.password:
        return False
    if "*" in parsed.hostname or parsed.scheme not in ("http", "https"):
        return False
    loopback = parsed.hostname.lower() == "localhost"
    if not loopback:
        try:
            import ipaddress
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
    return parsed.scheme == "https" or loopback


def safe_next(value: str) -> str:
    """Accept only a local absolute-path target, never //host or a control-character URL."""
    if not value.startswith("/") or value.startswith("//") or any(ord(char) < 32 for char in value):
        return "/app"
    parsed = urlsplit(value)
    return value if not parsed.scheme and not parsed.netloc else "/app"


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def install_oauth(app: FastAPI) -> None:
    if not hasattr(app.state, "portal"):
        return
    store = app.state.portal

    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    async def protected_resource(request: Request):
        origin = origin_for(request)
        return {"resource": origin + "/mcp", "authorization_servers": [origin + "/"],
                "scopes_supported": [SCOPE], "resource_name": "SocialScrape MCP Server"}

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    async def authorization_server(request: Request):
        origin = origin_for(request)
        return {"issuer": origin + "/", "authorization_endpoint": origin + "/oauth/authorize",
                "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
                "token_endpoint": origin + "/oauth/token", "token_endpoint_auth_methods_supported": ["none"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "scopes_supported": [SCOPE], "registration_endpoint": origin + "/oauth/register"}

    @app.post("/oauth/register", include_in_schema=False)
    async def register(request: Request):
        try:
            data = await request.json()
        except Exception:
            return _oauth_error("invalid_client_metadata", "Request body must be JSON")
        uris = data.get("redirect_uris") if isinstance(data, dict) else None
        if not isinstance(uris, list) or not uris or not all(isinstance(uri, str) and _valid_redirect(uri) for uri in uris):
            return _oauth_error("invalid_redirect_uri", "redirect_uris must contain valid HTTPS or loopback URLs")
        if data.get("token_endpoint_auth_method", "none") != "none":
            return _oauth_error("invalid_client_metadata", "Only public clients using token_endpoint_auth_method none are supported")
        grants = data.get("grant_types", ["authorization_code", "refresh_token"])
        responses = data.get("response_types", ["code"])
        if not isinstance(grants, list) or not set(grants) <= {"authorization_code", "refresh_token"} or responses != ["code"]:
            return _oauth_error("invalid_client_metadata", "Unsupported grant_types or response_types")
        client_name = str(data.get("client_name", "OAuth client"))[:120]
        client_id = store.oauth_register_client(client_name, uris, grants)
        return JSONResponse({"client_id": client_id, "client_name": client_name, "redirect_uris": uris,
                             "grant_types": grants, "response_types": ["code"],
                             "token_endpoint_auth_method": "none"}, status_code=201,
                            headers={"Cache-Control": "no-store"})

    def validate_authorize(data: Any, request: Request) -> tuple[dict[str, str] | None, JSONResponse | None]:
        fields = {key: str(data.get(key, "")) for key in
                  ("response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method",
                   "scope", "resource", "state")}
        client = store.oauth_client(fields["client_id"])
        if not client:
            return None, _oauth_error("invalid_request", "Unknown client_id")
        if fields["redirect_uri"] not in client["redirect_uris"]:
            return None, _oauth_error("invalid_request", "redirect_uri is not registered")
        checks = ((fields["response_type"] == "code", "response_type must be code"),
                  (fields["code_challenge_method"] == "S256" and bool(fields["code_challenge"]),
                   "PKCE with code_challenge_method S256 is required"),
                  (fields["scope"] == SCOPE, f"scope must be {SCOPE}"),
                  (fields["resource"] == resource_for(request), "resource does not match this MCP server"))
        for valid, description in checks:
            if not valid:
                return None, _oauth_error("invalid_request", description)
        fields["client_name"] = client["client_name"]
        return fields, None

    @app.get("/oauth/authorize", include_in_schema=False)
    async def authorize_page(request: Request):
        fields, error = validate_authorize(request.query_params, request)
        if error:
            return error
        principal = store.principal_for_session(request.cookies.get(SESSION_COOKIE))
        if not principal:
            target = request.url.path + ("?" + request.url.query if request.url.query else "")
            return RedirectResponse("/login?" + urlencode({"next": target}), 303)
        assert fields is not None
        host = urlsplit(fields["redirect_uri"]).hostname or fields["redirect_uri"]
        hidden = "".join(f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(value)}">'
                         for key, value in fields.items() if key != "client_name")
        return _page("Authorize", f'''<div class="eyebrow">Agent integration</div>
<h1>Authorize {html.escape(fields["client_name"])}</h1>
<p><strong>{html.escape(fields["client_name"])}</strong> wants to use Social Scrape as you.</p>
<p>Redirect host: <strong>{html.escape(host)}</strong><br>Scope: <code>{SCOPE}</code></p>
<form method="post" action="/oauth/authorize">{hidden}
<input type="hidden" name="csrf" value="{html.escape(principal.csrf)}">
<div class="form-actions"><button class="primary" name="decision" value="approve" type="submit">Approve</button>
<button class="ghost" name="decision" value="deny" type="submit">Deny</button></div></form>''')

    @app.post("/oauth/authorize", include_in_schema=False)
    async def authorize(request: Request):
        form = await request.form()
        fields, error = validate_authorize(form, request)
        if error:
            return error
        principal = store.principal_for_session(request.cookies.get(SESSION_COOKIE))
        if not principal:
            return _oauth_error("access_denied", "Sign in required", 401)
        if not hmac.compare_digest(str(form.get("csrf", "")), principal.csrf):
            return _oauth_error("access_denied", "Invalid CSRF token", 403)
        assert fields is not None
        if form.get("decision") != "approve":
            return _redirect(fields["redirect_uri"], error="access_denied", state=fields["state"])
        code = store.oauth_create_code(principal.id, fields)
        return _redirect(fields["redirect_uri"], code=code, state=fields["state"])

    @app.post("/oauth/token", include_in_schema=False)
    async def token(request: Request):
        form = await request.form()
        grant = str(form.get("grant_type", ""))
        resource = str(form.get("resource", ""))
        if resource != resource_for(request):
            return _oauth_error("invalid_target", "resource does not match this MCP server")
        if grant == "authorization_code":
            result = store.oauth_exchange_code(str(form.get("code", "")), str(form.get("client_id", "")),
                                               str(form.get("redirect_uri", "")), str(form.get("code_verifier", "")),
                                               resource)
        elif grant == "refresh_token":
            result = store.oauth_refresh(str(form.get("refresh_token", "")), str(form.get("client_id", "")), resource)
        else:
            return _oauth_error("unsupported_grant_type", "Supported grants are authorization_code and refresh_token")
        if not result:
            return _oauth_error("invalid_grant", "Grant is invalid, expired, already used, or does not match the request")
        access, refresh = result
        return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_SECONDS,
                             "refresh_token": refresh, "scope": SCOPE}, headers={"Cache-Control": "no-store"})
