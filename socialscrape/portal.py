"""Invite-only dashboard, sessions, member API keys, and request usage."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

SESSION_COOKIE = "socialscrape_session"
SESSION_SECONDS = 60 * 60 * 24 * 30
PBKDF2_ROUNDS = 600_000


@dataclass(frozen=True)
class Principal:
    id: int
    email: str
    role: str
    csrf: str = ""


class PortalStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('owner', 'member')),
                created_at REAL NOT NULL,
                last_login_at REAL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                csrf TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invites (
                token_hash TEXT PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE,
                created_by INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL,
                accepted_at REAL
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY,
                member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                prefix TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                last_used_at REAL,
                revoked_at REAL
            );
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY,
                member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
                api_key_id INTEGER REFERENCES api_keys(id) ON DELETE SET NULL,
                created_at REAL NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                query TEXT NOT NULL,
                status INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                cached INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_owner ON members(role) WHERE role='owner';
            CREATE INDEX IF NOT EXISTS requests_created_at ON requests(created_at DESC);
            CREATE INDEX IF NOT EXISTS requests_member_id ON requests(member_id, created_at DESC);
        """)
        self._conn.commit()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _password_hash(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
        return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"

    @staticmethod
    def _password_ok(password: str, encoded: str) -> bool:
        try:
            _, rounds, salt, expected = encoded.split("$", 3)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
            return hmac.compare_digest(actual, bytes.fromhex(expected))
        except (ValueError, TypeError):
            return False

    def has_members(self) -> bool:
        with self._lock:
            return bool(self._conn.execute("SELECT 1 FROM members LIMIT 1").fetchone())

    def bootstrap_owner(self, email: str | None, password: str | None) -> None:
        if self.has_members() or not email or not password:
            return
        self.create_member(email, password, "owner")

    def create_member(self, email: str, password: str, role: str = "member") -> int:
        email = email.strip().lower()
        if "@" not in email or len(email) > 254:
            raise ValueError("Enter a valid email address")
        if len(password) < 12:
            raise ValueError("Use at least 12 characters for the password")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO members(email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (email, self._password_hash(password), role, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def login(self, email: str, password: str) -> tuple[str, Principal] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM members WHERE email = ?", (email.strip().lower(),)).fetchone()
            if not row or not self._password_ok(password, row["password_hash"]):
                return None
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self._conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            self._conn.execute(
                "INSERT INTO sessions(token_hash, member_id, csrf, expires_at) VALUES (?, ?, ?, ?)",
                (self._token_hash(token), row["id"], csrf, time.time() + SESSION_SECONDS),
            )
            self._conn.execute("UPDATE members SET last_login_at = ? WHERE id = ?", (time.time(), row["id"]))
            self._conn.commit()
            return token, Principal(row["id"], row["email"], row["role"], csrf)

    def principal_for_session(self, token: str | None) -> Principal | None:
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT m.id, m.email, m.role, s.csrf FROM sessions s JOIN members m ON m.id=s.member_id "
                "WHERE s.token_hash=? AND s.expires_at>?", (self._token_hash(token), time.time())
            ).fetchone()
        return Principal(**dict(row)) if row else None

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (self._token_hash(token),))
                self._conn.commit()

    def create_key(self, member_id: int, name: str) -> tuple[int, str]:
        token = "ss_" + secrets.token_urlsafe(30)
        prefix = token[:10]
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO api_keys(member_id,name,prefix,token_hash,created_at) VALUES(?,?,?,?,?)",
                (member_id, name.strip()[:80] or "Default", prefix, self._token_hash(token), time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid), token

    def authenticate_key(self, token: str) -> tuple[Principal, int] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT k.id key_id,m.id,m.email,m.role FROM api_keys k JOIN members m ON m.id=k.member_id "
                "WHERE k.token_hash=? AND k.revoked_at IS NULL", (self._token_hash(token),)
            ).fetchone()
        return (Principal(row["id"], row["email"], row["role"]), row["key_id"]) if row else None

    def key_available_to(self, principal: Principal, key_id: int) -> bool:
        where, args = (("id=?", (key_id,)) if principal.role == "owner"
                       else ("id=? AND member_id=?", (key_id, principal.id)))
        with self._lock:
            return bool(self._conn.execute(
                f"SELECT 1 FROM api_keys WHERE {where} AND revoked_at IS NULL", args
            ).fetchone())

    def list_keys(self, principal: Principal) -> list[dict[str, Any]]:
        where, args = ("", ()) if principal.role == "owner" else ("WHERE k.member_id=?", (principal.id,))
        with self._lock:
            rows = self._conn.execute(
                "SELECT k.id,k.name,k.prefix,k.created_at,k.last_used_at,k.revoked_at,m.email FROM api_keys k "
                f"JOIN members m ON m.id=k.member_id {where} ORDER BY k.created_at DESC", args
            ).fetchall()
        return [dict(r) for r in rows]

    def revoke_key(self, principal: Principal, key_id: int) -> bool:
        condition, args = (("id=?", (key_id,)) if principal.role == "owner"
                           else ("id=? AND member_id=?", (key_id, principal.id)))
        with self._lock:
            cur = self._conn.execute(f"UPDATE api_keys SET revoked_at=? WHERE {condition} AND revoked_at IS NULL",
                                     (time.time(), *args))
            self._conn.commit()
            return cur.rowcount == 1

    def create_invite(self, owner: Principal, email: str) -> str:
        if owner.role != "owner":
            raise PermissionError
        email = email.strip().lower()
        if "@" not in email or len(email) > 254:
            raise ValueError("Enter a valid email address")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._conn.execute(
                "INSERT INTO invites(token_hash,email,created_by,expires_at) VALUES(?,?,?,?)",
                (self._token_hash(token), email, owner.id, time.time() + 7 * 86400),
            )
            self._conn.commit()
        return token

    def invite_email(self, token: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT email FROM invites WHERE token_hash=? AND accepted_at IS NULL AND expires_at>?",
                (self._token_hash(token), time.time()),
            ).fetchone()
        return row["email"] if row else None

    def accept_invite(self, token: str, password: str) -> None:
        email = self.invite_email(token)
        if not email:
            raise ValueError("This invitation is invalid or expired")
        with self._lock:
            self.create_member(email, password)
            self._conn.execute("UPDATE invites SET accepted_at=? WHERE token_hash=?",
                               (time.time(), self._token_hash(token)))
            self._conn.commit()

    def members(self, owner: Principal) -> list[dict[str, Any]]:
        if owner.role != "owner":
            raise PermissionError
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id,m.email,m.role,m.created_at,m.last_login_at,COUNT(k.id) key_count FROM members m "
                "LEFT JOIN api_keys k ON k.member_id=m.id AND k.revoked_at IS NULL GROUP BY m.id ORDER BY m.created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def remove_member(self, owner: Principal, member_id: int) -> bool:
        if owner.role != "owner" or owner.id == member_id:
            return False
        with self._lock:
            cur = self._conn.execute("DELETE FROM members WHERE id=? AND role='member'", (member_id,))
            self._conn.commit()
            return cur.rowcount == 1

    def record_request(self, principal: Principal | None, key_id: int | None, method: str, path: str, query: str,
                       status: int, duration_ms: int, cached: bool) -> None:
        with self._lock:
            if key_id is not None:
                self._conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (time.time(), key_id))
            self._conn.execute(
                "INSERT INTO requests(member_id,api_key_id,created_at,method,path,query,status,duration_ms,cached) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (principal.id if principal else None, key_id, time.time(), method, path, query[:4000], status,
                 duration_ms, int(cached)),
            )
            self._conn.commit()

    def history(self, principal: Principal, limit: int = 100) -> list[dict[str, Any]]:
        where, args = ("", ()) if principal.role == "owner" else ("WHERE r.member_id=?", (principal.id,))
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.created_at,r.method,r.path,r.query,r.status,r.duration_ms,r.cached,k.prefix,m.email "
                "FROM requests r LEFT JOIN api_keys k ON k.id=r.api_key_id LEFT JOIN members m ON m.id=r.member_id "
                f"{where} ORDER BY r.created_at DESC LIMIT ?", (*args, min(max(limit, 1), 500))
            ).fetchall()
        return [dict(r) for r in rows]

    def usage(self, principal: Principal) -> dict[str, Any]:
        clause, args = ("", ()) if principal.role == "owner" else ("AND member_id=?", (principal.id,))
        now = time.time()
        with self._lock:
            daily = self._conn.execute(
                "SELECT CAST((created_at-?)/86400 AS INTEGER) bucket,COUNT(*) count FROM requests "
                f"WHERE created_at>=? {clause} GROUP BY bucket ORDER BY bucket", (now, now - 29 * 86400, *args)
            ).fetchall()
            routes = self._conn.execute(
                f"SELECT path,COUNT(*) count FROM requests WHERE created_at>=? {clause} "
                "GROUP BY path ORDER BY count DESC,path LIMIT 10", (now - 30 * 86400, *args)
            ).fetchall()
            total = self._conn.execute(f"SELECT COUNT(*) count FROM requests WHERE 1=1 {clause}", args).fetchone()[0]
        points = {int(r["bucket"]): r["count"] for r in daily}
        return {"total": total, "daily": [points.get(i - 29, 0) for i in range(30)],
                "routes": [dict(r) for r in routes]}


def _page(title: str, body: str, *, bare: bool = False) -> HTMLResponse:
    shell = body if bare else f'<main class="auth-card">{body}</main>'
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>{title} · SocialScrape</title>
<link rel="stylesheet" href="/assets/portal.css"></head><body class="auth-page">{shell}</body></html>''')


def _session(request: Request) -> Principal | None:
    return request.app.state.portal.principal_for_session(request.cookies.get(SESSION_COOKIE))


def _require(request: Request) -> Principal:
    principal = _session(request)
    if not principal:
        raise HTTPException(401, "Sign in required")
    return principal


def _csrf(request: Request, principal: Principal) -> None:
    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), principal.csrf):
        raise HTTPException(403, "Invalid CSRF token")


def install_portal(app, store: PortalStore) -> None:
    app.state.portal = store
    assets = Path(__file__).with_name("static")
    app.mount("/assets", StaticFiles(directory=assets), name="assets")
    router = APIRouter(include_in_schema=False)

    @router.get("/")
    async def root(request: Request):
        return RedirectResponse("/app" if _session(request) else "/login", 303)

    @router.get("/login")
    async def login_page(request: Request, error: str = ""):
        if _session(request):
            return RedirectResponse("/app", 303)
        setup = not store.has_members()
        setup_enabled = bool(os.environ.get("SOCIALSCRAPE_SETUP_TOKEN"))
        heading = "Create the owner account" if setup else "Welcome back"
        copy = ("Enter the private setup code configured by the operator." if setup_enabled and setup
                else "Owner setup is disabled until SOCIALSCRAPE_SETUP_TOKEN is configured." if setup
                else "Sign in to your private SocialScrape workspace.")
        action = "/setup" if setup else "/login"
        setup_field = ('''<label>Setup code<input name="setup_token" type="password" autocomplete="off" required>
</label>''' if setup_enabled and setup else "")
        return _page("Sign in", f'''<a class="brand" href="/"><b>{{SS}}</b><span>SocialScrape</span></a>
<h1>{heading}</h1><p>{copy}</p>{f'<div class="form-error">{html.escape(error)}</div>' if error else ''}
<form method="post" action="{action}">{setup_field}
<label>Email<input name="email" type="email" autocomplete="email" required></label>
<label>Password<input name="password" type="password"
autocomplete="{'new-password' if setup else 'current-password'}" minlength="12" required></label>
<button type="submit" {'disabled' if setup and not setup_enabled else ''}>
{'Create owner account' if setup else 'Sign in'}</button></form>''')

    @router.post("/setup")
    async def setup(request: Request, email: str = Form(...), password: str = Form(...),
                    setup_token: str = Form("")):
        expected = os.environ.get("SOCIALSCRAPE_SETUP_TOKEN", "")
        if store.has_members() or not expected or not hmac.compare_digest(setup_token, expected):
            return RedirectResponse("/login?error=Setup+code+is+invalid", 303)
        try:
            store.create_member(email, password, "owner")
        except (ValueError, sqlite3.IntegrityError) as exc:
            return RedirectResponse(f"/login?error={str(exc)}", 303)
        return await _login_response(email, password, request.url.scheme == "https")

    async def _login_response(email: str, password: str, secure: bool = False):
        result = store.login(email, password)
        if not result:
            return RedirectResponse("/login?error=Email+or+password+is+incorrect", 303)
        token, _ = result
        response = RedirectResponse("/app", 303)
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=secure,
                            samesite="strict", path="/")
        return response

    @router.post("/login")
    async def login(request: Request, email: str = Form(...), password: str = Form(...)):
        return await _login_response(email, password, request.url.scheme == "https")

    @router.post("/logout")
    async def logout(request: Request):
        store.logout(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/login", 303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @router.get("/invite/{token}")
    async def invite_page(token: str):
        email = store.invite_email(token)
        if not email:
            return _page("Invitation", '<h1>Invitation unavailable</h1><p>This link is invalid or expired.</p>')
        return _page("Join", f'''<a class="brand" href="/"><b>{{SS}}</b><span>SocialScrape</span></a>
<h1>Join SocialScrape</h1><p>Your invitation is for <strong>{html.escape(email)}</strong>.</p>
<form method="post"><label>Create password<input name="password" type="password" minlength="12" required></label>
<button type="submit">Accept invitation</button></form>''')

    @router.post("/invite/{token}")
    async def accept_invite(request: Request, token: str, password: str = Form(...)):
        email = store.invite_email(token)
        try:
            store.accept_invite(token, password)
        except (ValueError, sqlite3.IntegrityError) as exc:
            return _page("Join", f"<h1>Could not join</h1><p>{exc}</p>")
        return await _login_response(email or "", password, request.url.scheme == "https")

    @router.get("/docs")
    async def docs(request: Request):
        return RedirectResponse("/app/docs" if _session(request) else "/login", 303)

    @router.get("/app")
    @router.get("/app/{rest:path}")
    async def dashboard(request: Request, rest: str = ""):
        principal = _session(request)
        if not principal:
            return RedirectResponse("/login", 303)
        page = (assets / "portal.html").read_text().replace("__USER_EMAIL_HTML__", html.escape(principal.email))
        portal_data = json.dumps({"email": principal.email, "role": principal.role, "csrf": principal.csrf})
        return HTMLResponse(page.replace("__PORTAL__", portal_data))

    @router.get("/portal/api/me")
    async def me(request: Request):
        p = _require(request)
        return {"email": p.email, "role": p.role, "csrf": p.csrf}

    @router.get("/portal/api/keys")
    async def keys(request: Request):
        return {"keys": store.list_keys(_require(request))}

    @router.post("/portal/api/keys")
    async def create_key(request: Request):
        p = _require(request)
        _csrf(request, p)
        data = await request.json()
        key_id, token = store.create_key(p.id, str(data.get("name", "Default")))
        return {"id": key_id, "key": token}

    @router.delete("/portal/api/keys/{key_id}")
    async def revoke_key(request: Request, key_id: int):
        p = _require(request)
        _csrf(request, p)
        return {"revoked": store.revoke_key(p, key_id)}

    @router.get("/portal/api/team")
    async def team(request: Request):
        p = _require(request)
        return {"members": store.members(p) if p.role == "owner" else []}

    @router.delete("/portal/api/team/{member_id}")
    async def remove_member(request: Request, member_id: int):
        p = _require(request)
        _csrf(request, p)
        return {"removed": store.remove_member(p, member_id)}

    @router.post("/portal/api/invites")
    async def invite(request: Request):
        p = _require(request)
        _csrf(request, p)
        data = await request.json()
        token = store.create_invite(p, str(data.get("email", "")))
        return {"url": str(request.base_url).rstrip("/") + "/invite/" + token}

    @router.get("/portal/api/history")
    async def history(request: Request, limit: int = 100):
        return {"requests": store.history(_require(request), limit)}

    @router.get("/portal/api/usage")
    async def usage(request: Request):
        return store.usage(_require(request))

    app.include_router(router)
