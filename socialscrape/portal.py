"""Invite-only dashboard, sessions, member API keys, and request usage."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from time import monotonic
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from cryptography.fernet import Fernet, InvalidToken

from .config import PLATFORMS, ConfigError, validate_proxy

SESSION_COOKIE = "socialscrape_session"
SESSION_SECONDS = 60 * 60 * 24 * 30
# Request history keeps every request's details, but its response bodies only this long and up to this much in all,
# oldest first, so they can't fill the volume the members, keys and settings live on. Checked every PRUNE_SECONDS.
HISTORY_BODY_SECONDS = 60 * 60 * 24 * 14
HISTORY_BODY_BYTES = 256 * 1024 * 1024
PRUNE_SECONDS = 600
PBKDF2_ROUNDS = 600_000
BRAND_ASSETS = ("brand/favicon.svg", "brand/favicon.ico", "brand/favicon.png",
                "brand/logomark.svg", "brand/logomark-chalk.svg", "brand/logomark.png", "brand/logomark-chalk.png",
                "brand/logo.svg", "brand/logo-white.svg", "brand/logo.png", "brand/logo-white.png")
_active_store: Any = None


def dt_iso(timestamp: float) -> str:
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Principal:
    id: int
    email: str
    role: str
    csrf: str = ""


class PortalStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        versioned = b"v=" in scope.get("query_string", b"")
        response.headers["cache-control"] = ("public, max-age=31536000, immutable" if versioned
                                             else "public, max-age=3600")
        return response


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
            CREATE TABLE IF NOT EXISTS portal_settings (
                name TEXT PRIMARY KEY,
                encrypted_value BLOB NOT NULL,
                updated_at REAL NOT NULL,
                updated_by INTEGER REFERENCES members(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS platform_account_health (
                platform TEXT NOT NULL,
                entry_id TEXT NOT NULL,
                health TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (platform, entry_id)
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
                cached INTEGER NOT NULL DEFAULT 0,
                response_body BLOB,
                response_truncated INTEGER NOT NULL DEFAULT 0,
                fallback INTEGER NOT NULL DEFAULT 0,
                response_pruned INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY,
                client_name TEXT NOT NULL,
                redirect_uris TEXT NOT NULL,
                grant_types TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code_hash TEXT PRIMARY KEY,
                client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
                member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                redirect_uri TEXT NOT NULL,
                code_challenge TEXT NOT NULL,
                scope TEXT NOT NULL,
                resource TEXT NOT NULL,
                expires_at REAL NOT NULL,
                used_at REAL
            );
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                token_hash TEXT PRIMARY KEY,
                token_type TEXT NOT NULL CHECK(token_type IN ('access', 'refresh')),
                family_id TEXT NOT NULL,
                client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
                member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                scope TEXT NOT NULL,
                resource TEXT NOT NULL,
                expires_at REAL NOT NULL,
                used_at REAL,
                revoked_at REAL
            );
            DROP INDEX IF EXISTS one_owner;  -- a workspace can have several owners
            CREATE INDEX IF NOT EXISTS oauth_tokens_family ON oauth_tokens(family_id);
            CREATE INDEX IF NOT EXISTS requests_created_at ON requests(created_at DESC);
            CREATE INDEX IF NOT EXISTS requests_member_id ON requests(member_id, created_at DESC);
        """)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(requests)")}
        if "response_body" not in columns:
            self._conn.execute("ALTER TABLE requests ADD COLUMN response_body BLOB")
        if "response_truncated" not in columns:
            self._conn.execute("ALTER TABLE requests ADD COLUMN response_truncated INTEGER NOT NULL DEFAULT 0")
        if "fallback" not in columns:  # served by ScrapeCreators (fallback.py)
            self._conn.execute("ALTER TABLE requests ADD COLUMN fallback INTEGER NOT NULL DEFAULT 0")
        if "response_pruned" not in columns:  # the body was cleared by prune_history()
            self._conn.execute("ALTER TABLE requests ADD COLUMN response_pruned INTEGER NOT NULL DEFAULT 0")
        self._conn.commit()
        self._pruned_at = 0.0

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

    def _fernet(self) -> Fernet:
        key = os.environ.get("SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY", "")
        try:
            return Fernet(key.encode())
        except (ValueError, TypeError):
            raise ConfigError(
                "SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY must be a Fernet key when portal settings are used"
            ) from None

    def _setting(self, name: str) -> tuple[bytes, float] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT encrypted_value, updated_at FROM portal_settings WHERE name=?", (name,)
            ).fetchone()
        if not row:
            return None
        try:
            return self._fernet().decrypt(row["encrypted_value"]), row["updated_at"]
        except InvalidToken:
            raise ConfigError(f"SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY cannot decrypt saved {name}") from None

    def _set_setting(self, owner: Principal, name: str, value: str) -> None:
        encrypted = self._fernet().encrypt(value.encode())
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO portal_settings(name,encrypted_value,updated_at,updated_by) VALUES(?,?,?,?)",
                (name, encrypted, time.time(), owner.id),
            )
            self._conn.commit()

    def _clear_setting(self, owner: Principal, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM portal_settings WHERE name=?", (name,))
            self._conn.commit()
        return cur.rowcount == 1

    def _pool(self, platform: str, kind: str) -> list[dict[str, Any]]:
        row = self._setting(f"{platform}.{kind}.pool")
        if row:
            try:
                pool = json.loads(row[0])
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ConfigError(f"The saved {platform} {kind} pool cannot be read") from None
            if isinstance(pool, list):
                return pool
        # Migrate the first single-value implementation without revealing or losing it.
        legacy = self._setting(f"{platform}.{kind}")
        if not legacy:
            return []
        legacy_id = "legacy-" + hashlib.sha256(legacy[0]).hexdigest()[:12]
        return [{"id": legacy_id, "value": legacy[0].decode(), "updated_at": legacy[1]}]

    def _save_pool(self, owner: Principal, platform: str, kind: str, pool: list[dict[str, Any]]) -> None:
        self._set_setting(owner, f"{platform}.{kind}.pool", json.dumps(pool, separators=(",", ":")))
        self._clear_setting(owner, f"{platform}.{kind}")

    def credential_status(self, owner: Principal) -> dict[str, Any]:
        if owner.role != "owner":
            raise PermissionError
        out = {}
        for name in ("tiktok.research_token", "tiktok.shop_credentials"):
            row = self._setting(name)
            out[name] = {"configured": bool(row), "updated_at": row[1] if row else None}
        return out

    def set_credential(self, owner: Principal, name: str, value: str) -> None:
        if owner.role != "owner":
            raise PermissionError
        if name not in ("tiktok.research_token", "tiktok.shop_credentials"):
            raise ValueError("Unknown platform credential")
        value = value.strip()
        if not value or len(value) > 8192:
            raise ValueError("Enter a credential no longer than 8192 characters")
        self._set_setting(owner, name, value)

    def credential(self, name: str) -> str | None:
        row = self._setting(name)
        return row[0].decode() if row else None

    def platform_status(self, owner: Principal) -> dict[str, Any]:
        from . import sessions

        if owner.role != "owner":
            raise PermissionError
        return {platform: {
            kind: [{"id": item["id"], "position": index + 1, "updated_at": item["updated_at"],
                    **({"health": sessions.entry_health(platform, item["id"])} if kind == "cookies" else {})}
                   for index, item in enumerate(self._pool(platform, kind))]
            for kind in ("proxy", "cookies")
        } for platform in PLATFORMS}

    def platform_pool(self, platform: str) -> tuple[list[str], list[tuple[str, str]]]:
        """(proxy URLs, (entry id, cookies.txt) pairs), each in pool order."""
        return ([item["value"] for item in self._pool(platform, "proxy")],
                [(item["id"], item["value"]) for item in self._pool(platform, "cookies")])

    def platform_account(self, owner: Principal, platform: str, entry_id: str) -> tuple[Any, str | None]:
        """A dashboard cookie entry as the sessions.Account with_ydl uses, and the proxy of its pool position."""
        from . import sessions

        if owner.role != "owner":
            raise PermissionError
        cookies = self._pool(platform, "cookies")
        position = next((i for i, item in enumerate(cookies) if item["id"] == entry_id), None)
        if position is None:
            raise ValueError("Platform setting not found")
        proxies = [item["value"] for item in self._pool(platform, "proxy")]
        account = sessions.Account(platform, entry_id, "dashboard", cookies[position]["value"], position + 1)
        return account, proxies[min(position, len(proxies) - 1)] if proxies else None

    def account_health_rows(self) -> list[tuple[str, str, dict[str, Any]]]:
        """The saved health of dashboard accounts (no secrets: statuses, reasons, times, the platform's username)."""
        with self._lock:
            rows = self._conn.execute("SELECT platform, entry_id, health FROM platform_account_health").fetchall()
        out = []
        for row in rows:
            try:
                out.append((row["platform"], row["entry_id"], json.loads(row["health"])))
            except (TypeError, ValueError):
                continue
        return out

    def save_account_health(self, platform: str, entry_id: str, health: dict[str, Any] | None) -> None:
        with self._lock:
            if health is None:
                self._conn.execute("DELETE FROM platform_account_health WHERE platform=? AND entry_id=?",
                                   (platform, entry_id))
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO platform_account_health(platform,entry_id,health,updated_at) "
                    "VALUES(?,?,?,?)", (platform, entry_id, json.dumps(health), time.time()))
            self._conn.commit()

    def add_platform_setting(self, owner: Principal, platform: str, kind: str, value: str) -> str:
        if owner.role != "owner":
            raise PermissionError
        pool = self._pool(platform, kind)
        if len(pool) >= 5:
            raise ValueError(f"{platform.title()} already has the maximum of 5 {kind} entries")
        if kind == "proxy":
            value = validate_proxy(value, name=f"{platform.title()} proxy") or ""
            if not value:
                raise ValueError("Enter a proxy URL")
        else:
            _validate_cookies(platform, value)
        entry_id = secrets.token_urlsafe(9)
        pool.append({"id": entry_id, "value": value, "updated_at": time.time()})
        self._save_pool(owner, platform, kind, pool)
        return entry_id

    def platform_setting_value(self, owner: Principal, platform: str, kind: str, entry_id: str) -> str:
        if owner.role != "owner":
            raise PermissionError
        item = next((item for item in self._pool(platform, kind) if item["id"] == entry_id), None)
        if not item:
            raise ValueError("Platform setting not found")
        return item["value"]

    def remove_platform_setting(self, owner: Principal, platform: str, kind: str, entry_id: str) -> bool:
        if owner.role != "owner":
            raise PermissionError
        pool = self._pool(platform, kind)
        kept = [item for item in pool if item["id"] != entry_id]
        if len(kept) == len(pool):
            return False
        self._save_pool(owner, platform, kind, kept)
        if kind == "cookies":
            from . import sessions

            sessions.forget(platform, entry_id)
        return True

    def move_platform_setting(self, owner: Principal, platform: str, kind: str, entry_id: str,
                              direction: str) -> bool:
        if owner.role != "owner":
            raise PermissionError
        if direction not in ("up", "down"):
            raise ValueError("Direction must be up or down")
        pool = self._pool(platform, kind)
        index = next((i for i, item in enumerate(pool) if item["id"] == entry_id), None)
        target = index + (-1 if direction == "up" else 1) if index is not None else -1
        if index is None or not 0 <= target < len(pool):
            return False
        pool[index], pool[target] = pool[target], pool[index]
        self._save_pool(owner, platform, kind, pool)
        return True

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
        name = name.strip()
        if not name:
            raise ValueError("Enter a name for this API key.")
        token = "ss_" + secrets.token_urlsafe(30)
        prefix = token[:10]
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO api_keys(member_id,name,prefix,token_hash,created_at) VALUES(?,?,?,?,?)",
                (member_id, name[:80], prefix, self._token_hash(token), time.time()),
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

    def oauth_register_client(self, client_name: str, redirect_uris: list[str], grant_types: list[str]) -> str:
        client_id = secrets.token_urlsafe(24)
        with self._lock:
            self._conn.execute(
                "INSERT INTO oauth_clients(client_id,client_name,redirect_uris,grant_types,created_at) VALUES(?,?,?,?,?)",
                (client_id, client_name, json.dumps(redirect_uris), json.dumps(grant_types), time.time()),
            )
            self._conn.commit()
        return client_id

    def oauth_client(self, client_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["redirect_uris"] = json.loads(result["redirect_uris"])
        result["grant_types"] = json.loads(result["grant_types"])
        return result

    def oauth_create_code(self, member_id: int, fields: dict[str, str]) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._conn.execute(
                "INSERT INTO oauth_codes(code_hash,client_id,member_id,redirect_uri,code_challenge,scope,resource,"
                "expires_at) VALUES(?,?,?,?,?,?,?,?)",
                (self._token_hash(token), fields["client_id"], member_id, fields["redirect_uri"],
                 fields["code_challenge"], fields["scope"], fields["resource"], time.time() + 600),
            )
            self._conn.commit()
        return token

    def _oauth_tokens(self, member_id: int, client_id: str, scope: str, resource: str,
                      family_id: str | None = None) -> tuple[str, str]:
        now = time.time()
        family_id = family_id or secrets.token_urlsafe(24)
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(40)
        self._conn.executemany(
            "INSERT INTO oauth_tokens(token_hash,token_type,family_id,client_id,member_id,scope,resource,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [(self._token_hash(access), "access", family_id, client_id, member_id, scope, resource, now + 3600),
             (self._token_hash(refresh), "refresh", family_id, client_id, member_id, scope, resource, now + 2592000)],
        )
        return access, refresh

    def oauth_exchange_code(self, code: str, client_id: str, redirect_uri: str, verifier: str,
                            resource: str) -> tuple[str, str] | None:
        from .oauth import PKCE_VERIFIER, _challenge
        if not PKCE_VERIFIER.fullmatch(verifier):
            return None
        now = time.time()
        with self._lock:
            row = self._conn.execute("SELECT * FROM oauth_codes WHERE code_hash=?", (self._token_hash(code),)).fetchone()
            if (not row or row["used_at"] is not None or row["expires_at"] <= now or row["client_id"] != client_id
                    or row["redirect_uri"] != redirect_uri or row["resource"] != resource
                    or not verifier or not hmac.compare_digest(row["code_challenge"], _challenge(verifier))):
                return None
            self._conn.execute("UPDATE oauth_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL",
                               (now, self._token_hash(code)))
            result = self._oauth_tokens(row["member_id"], client_id, row["scope"], resource)
            self._conn.commit()
            return result

    def oauth_refresh(self, token: str, client_id: str, resource: str) -> tuple[str, str] | None:
        now = time.time()
        with self._lock:
            row = self._conn.execute("SELECT * FROM oauth_tokens WHERE token_hash=? AND token_type='refresh'",
                                     (self._token_hash(token),)).fetchone()
            if not row or row["client_id"] != client_id or row["resource"] != resource:
                return None
            if row["used_at"] is not None:
                self._conn.execute("UPDATE oauth_tokens SET revoked_at=? WHERE family_id=?", (now, row["family_id"]))
                self._conn.commit()
                return None
            if row["expires_at"] <= now or row["revoked_at"] is not None:
                return None
            self._conn.execute("UPDATE oauth_tokens SET used_at=? WHERE token_hash=?", (now, self._token_hash(token)))
            result = self._oauth_tokens(row["member_id"], client_id, row["scope"], resource, row["family_id"])
            self._conn.commit()
            return result

    def authenticate_bearer(self, token: str, resource: str) -> Principal | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT m.id,m.email,m.role FROM oauth_tokens t JOIN members m ON m.id=t.member_id "
                "WHERE t.token_hash=? AND t.token_type='access' AND t.resource=? AND t.scope='mcp:tools' "
                "AND t.expires_at>? AND t.revoked_at IS NULL AND NOT EXISTS "
                "(SELECT 1 FROM oauth_tokens r WHERE r.family_id=t.family_id AND r.revoked_at IS NOT NULL)",
                (self._token_hash(token), resource, time.time()),
            ).fetchone()
        return Principal(**dict(row)) if row else None

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

    def set_member_role(self, owner: Principal, member_id: int, role: str) -> bool:
        """Owners change anyone's role but their own, so the workspace always keeps at least one owner."""
        if owner.role != "owner":
            raise PermissionError
        if role not in ("owner", "member"):
            raise ValueError("Choose owner or member")
        if owner.id == member_id:
            raise ValueError("Another owner has to change your role")
        with self._lock:  # re-check the acting owner here: another owner may have demoted them mid-request
            cur = self._conn.execute(
                "UPDATE members SET role=? WHERE id=? AND EXISTS(SELECT 1 FROM members WHERE id=? AND role='owner')",
                (role, member_id, owner.id),
            )
            self._conn.commit()
            if cur.rowcount == 1:
                return True
            if not self._conn.execute("SELECT 1 FROM members WHERE id=? AND role='owner'", (owner.id,)).fetchone():
                raise PermissionError
            return False

    def record_request(self, principal: Principal | None, key_id: int | None, method: str, path: str, query: str,
                       status: int, duration_ms: int, cached: bool, response_body: bytes = b"",
                       fallback: bool = False) -> None:
        max_body = 2 * 1024 * 1024
        stored_body, truncated = response_body[:max_body], len(response_body) > max_body
        with self._lock:
            if key_id is not None:
                self._conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (time.time(), key_id))
            self._conn.execute(
                "INSERT INTO requests(member_id,api_key_id,created_at,method,path,query,status,duration_ms,cached,"
                "response_body,response_truncated,fallback) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (principal.id if principal else None, key_id, time.time(), method, path, query[:4000], status,
                 duration_ms, int(cached), stored_body, int(truncated), int(fallback)),
            )
            self._conn.commit()
            if time.time() - self._pruned_at >= PRUNE_SECONDS:
                self.prune_history()

    def prune_history(self, now: float | None = None, keep_seconds: float = HISTORY_BODY_SECONDS,
                      max_bytes: int = HISTORY_BODY_BYTES) -> int:
        """Clear stored response bodies older than keep_seconds, then the oldest until the rest fit in max_bytes.
        The requests themselves stay. Returns how many bodies were cleared. SQLite reuses the freed pages, so the
        file stops growing without a VACUUM."""
        now = time.time() if now is None else now
        with self._lock:
            self._pruned_at = now
            cleared = self._conn.execute(
                "UPDATE requests SET response_body=NULL, response_pruned=1 "
                "WHERE response_body IS NOT NULL AND created_at < ?", (now - keep_seconds,)
            ).rowcount
            kept, cutoff = 0, None
            for request_id, size in self._conn.execute(
                "SELECT id, LENGTH(response_body) FROM requests WHERE response_body IS NOT NULL ORDER BY id DESC"
            ):
                kept += size or 0
                if kept > max_bytes:
                    cutoff = request_id
                    break
            if cutoff is not None:
                cleared += self._conn.execute(
                    "UPDATE requests SET response_body=NULL, response_pruned=1 "
                    "WHERE response_body IS NOT NULL AND id <= ?", (cutoff,)
                ).rowcount
            self._conn.commit()
        return cleared

    def history(self, principal: Principal, limit: int = 100) -> list[dict[str, Any]]:
        where, args = ("", ()) if principal.role == "owner" else ("WHERE r.member_id=?", (principal.id,))
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.id,r.created_at,r.method,r.path,r.query,r.status,r.duration_ms,r.cached,r.fallback,"
                "r.response_body IS NOT NULL has_response,r.response_truncated,k.prefix,m.email "
                "FROM requests r LEFT JOIN api_keys k ON k.id=r.api_key_id LEFT JOIN members m ON m.id=r.member_id "
                f"{where} ORDER BY r.created_at DESC LIMIT ?", (*args, min(max(limit, 1), 500))
            ).fetchall()
        result = [dict(r) for r in rows]
        for item in result:
            item["has_response"] = bool(item["has_response"])
        return result

    def history_detail(self, principal: Principal, request_id: int) -> dict[str, Any] | None:
        where, args = (("r.id=?", (request_id,)) if principal.role == "owner"
                       else ("r.id=? AND r.member_id=?", (request_id, principal.id)))
        with self._lock:
            row = self._conn.execute(
                "SELECT r.id,r.created_at,r.method,r.path,r.query,r.status,r.duration_ms,r.cached,r.fallback,"
                "r.response_body,r.response_body IS NOT NULL has_response,r.response_truncated,r.response_pruned,"
                "k.prefix,m.email FROM requests r "
                "LEFT JOIN api_keys k ON k.id=r.api_key_id LEFT JOIN members m ON m.id=r.member_id "
                f"WHERE {where}", args
            ).fetchone()
        if not row:
            return None
        detail = dict(row)
        detail["has_response"] = bool(detail["has_response"])
        detail["response"] = (detail.pop("response_body") or b"").decode("utf-8", "replace")
        return detail

    def api_usage(self, principal: Principal, *, endpoint: str | None = None, status: int | None = None,
                  page: int = 1, page_size: int = 100) -> list[dict[str, Any]]:
        clauses, args = [], []
        if principal.role != "owner":
            clauses.append("member_id=?")
            args.append(principal.id)
        if endpoint:
            clauses.append("path LIKE ?")
            args.append(f"%{endpoint}%")
        if status is not None:
            clauses.append("status=?")
            args.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id,path,query,status,duration_ms,created_at,method,cached,fallback FROM requests {where} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?", (*args, page_size, (page - 1) * page_size)
            ).fetchall()
        return [{"id": str(row["id"]), "endpoint": row["path"] + ("?" + row["query"] if row["query"] else ""),
                 "status_code": row["status"], "duration_ms": row["duration_ms"],
                 "success": 200 <= row["status"] < 400,
                 "request_time": dt_iso(row["created_at"]), "http_method": row["method"],
                 "created_at": dt_iso(row["created_at"]), "credits": 0,
                 "cached": bool(row["cached"]), "fallback": bool(row["fallback"])} for row in rows]

    def daily_usage(self, principal: Principal) -> list[dict[str, str]]:
        clause, args = ("", ()) if principal.role == "owner" else ("AND member_id=?", (principal.id,))
        now = time.time()
        with self._lock:
            rows = self._conn.execute(
                f"SELECT date(created_at,'unixepoch') day,COUNT(*) count FROM requests "
                f"WHERE created_at>=? {clause} GROUP BY day", (now - 30 * 86400, *args)
            ).fetchall()
        counts = {row["day"]: row["count"] for row in rows}
        today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
        return [{"usage_date": f"{day.isoformat()}T00:00:00.000Z", "total_credits": "0",
                 "request_count": str(counts.get(day.isoformat(), 0))}
                for day in (today - dt.timedelta(days=i) for i in range(30))]

    def most_used_routes(self, principal: Principal, start: float, end: float) -> list[dict[str, str]]:
        clause, args = ("", ()) if principal.role == "owner" else ("AND member_id=?", (principal.id,))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT path,COUNT(*) count FROM requests WHERE created_at>=? AND created_at<=? {clause} "
                "GROUP BY path ORDER BY count DESC,path LIMIT 20", (start, end, *args)
            ).fetchall()
        return [{"base_endpoint": row["path"], "call_count": str(row["count"]), "total_credits": "0"}
                for row in rows]

    def usage(self, principal: Principal) -> dict[str, Any]:
        clause, args = ("", ()) if principal.role == "owner" else ("AND member_id=?", (principal.id,))
        now = time.time()
        with self._lock:
            daily = self._conn.execute(
                "SELECT CAST((created_at-?)/86400 AS INTEGER) bucket,"
                "CASE WHEN path LIKE '/v%/instagram/%' THEN 'instagram' "
                "WHEN path LIKE '/v%/tiktok/%' THEN 'tiktok' "
                "WHEN path LIKE '/v%/youtube/%' THEN 'youtube' "
                "WHEN path LIKE '/v%/facebook/%' THEN 'facebook' "
                "WHEN path LIKE '/v%/google/%' THEN 'google' ELSE 'other' END platform,COUNT(*) count "
                f"FROM requests WHERE created_at>=? {clause} GROUP BY bucket,platform ORDER BY bucket,platform",
                (now, now - 29 * 86400, *args),
            ).fetchall()
            routes = self._conn.execute(
                f"SELECT path,COUNT(*) count FROM requests WHERE created_at>=? {clause} "
                "GROUP BY path ORDER BY count DESC,path LIMIT 10", (now - 30 * 86400, *args)
            ).fetchall()
            total = self._conn.execute(f"SELECT COUNT(*) count FROM requests WHERE 1=1 {clause}", args).fetchone()[0]
            fallback_count = self._conn.execute(
                f"SELECT COUNT(*) FROM requests WHERE created_at>=? AND fallback=1 {clause}", (now - 30 * 86400, *args)
            ).fetchone()[0]
        points = {(int(r["bucket"]), r["platform"]): r["count"] for r in daily}
        days = [{platform: points.get((i - 29, platform), 0)
                 for platform in ("instagram", "tiktok", "youtube", "facebook", "google", "other")}
                for i in range(30)]
        for day in days:
            day["total"] = sum(day.values())
        return {"total": total, "daily": days, "routes": [dict(r) for r in routes], "fallback_count": fallback_count}


@cache
def _portal_asset_version() -> str:
    assets = Path(__file__).with_name("static")
    digest = hashlib.sha256()
    for name in ("portal.html", "loading.html", "portal.css", "portal.js", "preview.js", "fallback.css", "fallback.js",
                 "selftest.css", "selftest.js", "appearance.js", "brand/tokens.css", "brand/icons.svg",
                 "brand/platforms/instagram.svg", "brand/platforms/tiktok.svg", "brand/platforms/youtube.svg",
                 "brand/platforms/facebook.svg", "brand/platforms/google.svg", *BRAND_ASSETS):
        digest.update(name.encode())
        digest.update((assets / name).read_bytes())
    return digest.hexdigest()[:16]


def _versioned_page(page: str) -> str:
    version = _portal_asset_version()
    page = page.replace("__ASSET_VERSION__", version).replace("__BRAND__", _brand("/app"))
    for name in ("portal.css", "portal.js", "preview.js", "fallback.css", "fallback.js", "selftest.css", "selftest.js",
                 "appearance.js", "brand/tokens.css", "brand/icons.svg", "brand/platforms/facebook.svg",
                 "brand/platforms/google.svg", *BRAND_ASSETS):
        path = f"/assets/{name}"
        page = page.replace(f'{path}"', f'{path}?v={version}"')
        page = page.replace(f"{path}#", f"{path}?v={version}#")
    return page


@cache
def _loading_templates() -> tuple[str, dict[str, str]]:
    markup = Path(__file__).with_name("static").joinpath("loading.html").read_text()
    def rows(match: re.Match) -> str:
        cells = '<td><i class="skeleton sk-line"></i></td>' * int(match[2])
        return '<tbody aria-hidden="true">' + ('<tr>' + cells + '</tr>') * int(match[1]) + '</tbody>'
    markup = re.sub(r'<tbody aria-hidden="true" data-skeleton-rows="(\d+)" data-skeleton-columns="(\d+)"></tbody>', rows, markup)
    markup = markup.replace('<th>', '<th scope="col">')
    pages = dict(re.findall(r'<template data-loading-page="([a-z]+)">(.*?)</template>', markup, re.S))
    return markup, pages


def _brand(href: str) -> str:
    return (f'<a class="brand" href="{href}" '
            'aria-label="Social Scrape by Mimic" title="Social Scrape">'
            '<img class="brand-mark brand-mark-dark" src="/assets/brand/logomark-chalk.svg" '
            'width="36" height="36" alt="">'
            '<img class="brand-mark brand-mark-light" src="/assets/brand/logomark.svg" '
            'width="36" height="36" alt="">'
            '<span class="brand-copy"><span class="brand-name">Social Scrape</span>'
            '<small>by Mimic</small></span></a>')


def _page(title: str, body: str, *, bare: bool = False) -> HTMLResponse:
    shell = body if bare else f'<main class="auth-card">{body}</main>'
    return HTMLResponse(_versioned_page(f'''<!doctype html><html lang="en" data-asset-version="__ASSET_VERSION__"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>{title} · Social Scrape</title>
<link rel="icon" href="/assets/brand/favicon.ico" sizes="16x16 32x32 48x48" type="image/x-icon">
<link rel="icon" href="/assets/brand/favicon.svg" sizes="any" type="image/svg+xml">
<link rel="preload" href="/assets/brand/InterVariable.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/assets/brand/tokens.css"><link rel="stylesheet" href="/assets/portal.css">
<script src="/assets/appearance.js"></script></head><body class="auth-page">
<div class="auth-shell"><div class="auth-main"><header class="auth-header">
{_brand("/")}
<button class="icon-button" data-theme-toggle aria-label="Switch theme"></button></header>{shell}
<footer class="auth-footer"><span>Social Scrape</span><span>Private · Invite-only</span></footer>
</div><aside class="auth-art" aria-label="Social Scrape artwork">
<img class="art-dark" src="/assets/brand/courtyard-dark.png" alt="" decoding="async">
<img class="art-light" src="/assets/brand/courtyard-light.png" alt="" decoding="async" loading="lazy">
</aside></div><div class="sr-only" id="loading-status" role="status" aria-live="polite" aria-atomic="true"></div></body></html>'''),
                        headers={"Content-Security-Policy": "frame-ancestors 'none'", "X-Frame-Options": "DENY"})


def _status(row: tuple[bytes, float] | None) -> dict[str, Any]:
    return {"configured": bool(row), "updated_at": row[1] if row else None}


def _check_platform_setting(platform: str, kind: str) -> None:
    if platform not in PLATFORMS or kind not in ("proxy", "cookies"):
        raise HTTPException(404, "Unknown platform setting")


def _validate_cookies(platform: str, value: str) -> None:
    import io

    from yt_dlp.cookies import YoutubeDLCookieJar

    if not value.strip() or len(value.encode()) > 2 * 1024 * 1024:
        raise ValueError("Upload a Netscape cookies.txt file no larger than 2 MiB")
    jar = YoutubeDLCookieJar()
    try:
        jar._really_load(io.StringIO(value), "dashboard cookies.txt", True, True)
    except Exception:
        raise ValueError("Upload a valid Netscape cookies.txt file") from None
    suffix = {"instagram": "instagram.com", "tiktok": "tiktok.com", "youtube": "youtube.com",
              "facebook": "facebook.com", "google": "google.com"}[platform]
    if not any((cookie.domain or "").lstrip(".").lower().endswith(suffix) for cookie in jar):
        raise ValueError(f"The cookies.txt file has no {suffix} cookies")


def _test_proxy(platform: str, proxy: str) -> dict[str, Any]:
    import requests

    targets = {
        "instagram": "https://www.instagram.com/instagram/",
        "tiktok": "https://www.tiktok.com/@tiktok",
        "youtube": "https://www.youtube.com/robots.txt",
        "facebook": "https://www.facebook.com/ads/library/",
        "google": "https://adstransparency.google.com/",
    }
    proxies = {"http": proxy, "https": proxy}
    started = monotonic()
    try:
        exit_response = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=10)
        exit_response.raise_for_status()
        exit_ip = exit_response.json().get("ip")
        response = requests.get(
            targets[platform], proxies=proxies, timeout=12, allow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 SocialScrape proxy test"},
        )
        location = response.headers.get("location", "").lower()
        blocked = response.status_code in (401, 403, 429) or "login" in location or "checkpoint" in location
        return {"ok": response.status_code < 500 and not blocked, "status": response.status_code,
                "blocked": blocked, "exit_ip": exit_ip, "latency_ms": round((monotonic() - started) * 1000)}
    except requests.RequestException as exc:
        return {"ok": False, "status": None, "blocked": False, "exit_ip": None,
                "latency_ms": round((monotonic() - started) * 1000), "error": type(exc).__name__}


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


def _owner(request: Request) -> Principal:
    principal = _require(request)
    if principal.role != "owner":
        raise HTTPException(403, "Workspace owner access required")
    return principal


def active_platform_credential(name: str) -> str | None:
    return _active_store.credential(name) if _active_store else None


def active_platform_pool(platform: str) -> tuple[list[str], list[tuple[str, str]]]:
    """The dashboard's proxies and (entry id, cookies.txt) accounts for `platform`, in pool order."""
    return _active_store.platform_pool(platform) if _active_store else ([], [])


def clear_active_portal() -> None:
    from . import sessions

    global _active_store
    _active_store = None
    sessions.detach()


def install_portal(app, store: PortalStore) -> None:
    from . import sessions

    global _active_store
    _active_store = store
    sessions.attach(store.account_health_rows(), store.save_account_health)
    app.state.portal = store
    assets = Path(__file__).with_name("static")
    app.mount("/assets", PortalStaticFiles(directory=assets), name="assets")
    router = APIRouter(include_in_schema=False)

    @router.get("/")
    async def root(request: Request):
        return RedirectResponse("/app" if _session(request) else "/login", 303)

    @router.get("/login")
    async def login_page(request: Request, error: str = "", next: str = ""):
        from .oauth import safe_next
        target = safe_next(next)
        if _session(request):
            return RedirectResponse(target, 303)
        setup = not store.has_members()
        setup_enabled = bool(os.environ.get("SOCIALSCRAPE_SETUP_TOKEN"))
        heading = "Create the owner account" if setup else "Sign in"
        copy = ("Enter the private setup code configured by the operator." if setup_enabled and setup
                else "Owner setup is disabled until SOCIALSCRAPE_SETUP_TOKEN is configured." if setup
                else "Sign in to your private Social Scrape workspace.")
        action = "/setup" if setup else "/login"
        setup_field = ('''<label>Setup code<input name="setup_token" type="password" autocomplete="off" required>
</label>''' if setup_enabled and setup else "")
        return _page(heading if setup else "Sign in", f'''<div class="eyebrow">Your workspace awaits</div>
<h1>{heading}</h1><p>{copy}</p>{f'<div class="form-error" role="alert">{html.escape(error)}</div>' if error else ''}
<form method="post" action="{action}"><input type="hidden" name="next" value="{html.escape(target)}">{setup_field}
<label>Email<input name="email" type="email" autocomplete="email" required></label>
<label>Password<input name="password" type="password"
autocomplete="{'new-password' if setup else 'current-password'}" required
{'minlength="12" aria-describedby="password-hint"' if setup else ''}></label>{'<p class="help" id="password-hint">Use at least 12 characters.</p>' if setup else ''}
<button class="primary" type="submit" {'disabled' if setup and not setup_enabled else ''}>
{'Create owner account' if setup else 'Sign in'}</button></form>
<div class="auth-hint">Access is by invitation. Contact your workspace owner to join.</div>''')

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

    async def _login_response(email: str, password: str, secure: bool = False, next: str = ""):
        from .oauth import safe_next
        result = store.login(email, password)
        if not result:
            query = urlencode({"error": "Email or password is incorrect", "next": safe_next(next)})
            return RedirectResponse("/login?" + query, 303)
        token, _ = result
        response = RedirectResponse(safe_next(next), 303)
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=secure,
                            samesite="strict", path="/")
        return response

    @router.post("/login")
    async def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("")):
        return await _login_response(email, password, request.url.scheme == "https", next)

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
            return _page("Invitation", '<div class="eyebrow">Workspace invitation</div><h1>Invitation unavailable</h1>'
                         '<p>This link is invalid or expired. Ask your workspace owner for a new invitation.</p>'
                         '<a class="ghost" href="/login">Back to sign in</a>')
        return _page("Join", f'''<div class="eyebrow">You’re invited</div>
<h1>Join Social Scrape</h1><p>Your invitation is for <strong>{html.escape(email)}</strong>.</p>
<form method="post"><label>Create password<input name="password" type="password" minlength="12"
autocomplete="new-password" aria-describedby="password-hint" required></label>
<p class="help" id="password-hint">Use at least 12 characters.</p>
<button class="primary" type="submit">Accept invitation</button></form>''')

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
        return RedirectResponse("/app/docs", 303)

    @router.get("/app")
    @router.get("/app/{rest:path}")
    async def dashboard(request: Request, rest: str = ""):
        principal = _session(request)
        # The documentation is public: a signed-out visitor gets the same page with only the docs and a sign-in link.
        public_docs = principal is None and rest.split("/")[0] == "docs"
        if not principal and not public_docs:
            return RedirectResponse("/login", 303)
        email = principal.email if principal else ""
        page = (assets / "portal.html").read_text().replace("__USER_EMAIL_HTML__", html.escape(email))
        templates, skeletons = _loading_templates()
        route = rest.split('/')[0] or 'overview'
        loading_route = f"{route}member" if principal and principal.role == "member" and route in ("team", "settings", "fallback") else route
        page = page.replace("__INITIAL_LOADING__", skeletons.get(loading_route, skeletons['overview']))
        page = page.replace("__LOADING_TEMPLATES__", templates)
        portal_data = json.dumps({"email": principal.email, "role": principal.role, "csrf": principal.csrf} if principal
                                 else {"email": "", "role": "public", "csrf": "", "public": True})
        return HTMLResponse(_versioned_page(page.replace("__PORTAL__", portal_data)))

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
        try:
            key_id, token = store.create_key(p.id, str(data.get("name", "")))
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        return {"id": key_id, "key": token}

    @router.delete("/portal/api/keys/{key_id}")
    async def revoke_key(request: Request, key_id: int):
        p = _require(request)
        _csrf(request, p)
        if not store.revoke_key(p, key_id):
            return JSONResponse({"detail": "This API key no longer exists or was already revoked."}, status_code=404)
        return {"revoked": True}

    @router.get("/portal/api/team")
    async def team(request: Request):
        p = _require(request)
        return {"members": store.members(p) if p.role == "owner" else []}

    @router.delete("/portal/api/team/{member_id}")
    async def remove_member(request: Request, member_id: int):
        p = _require(request)
        _csrf(request, p)
        if not store.remove_member(p, member_id):
            return JSONResponse({"detail": "This member was already removed."}, status_code=404)
        return {"removed": True}

    @router.patch("/portal/api/team/{member_id}")
    async def set_member_role(request: Request, member_id: int):
        p = _owner(request)
        _csrf(request, p)
        data = await request.json()
        role = str(data.get("role", ""))
        try:
            changed = store.set_member_role(p, member_id, role)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        except PermissionError:
            raise HTTPException(403, "Workspace owner access required") from None
        if not changed:
            return JSONResponse({"detail": "This member was removed."}, status_code=404)
        return {"role": role}

    @router.post("/portal/api/invites")
    async def invite(request: Request):
        p = _require(request)
        _csrf(request, p)
        data = await request.json()
        token = store.create_invite(p, str(data.get("email", "")))
        return {"url": str(request.base_url).rstrip("/") + "/invite/" + token}

    @router.get("/portal/api/settings/platform-credentials")
    async def platform_credentials(request: Request):
        return store.credential_status(_owner(request))

    @router.put("/portal/api/settings/platform-credentials/{name}")
    async def set_platform_credential(request: Request, name: str):
        p = _owner(request)
        _csrf(request, p)
        data = await request.json()
        try:
            store.set_credential(p, name.replace("-", "."), str(data.get("value", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"configured": True}

    @router.delete("/portal/api/settings/platform-credentials/{name}")
    async def clear_platform_credential(request: Request, name: str):
        p = _owner(request)
        _csrf(request, p)
        key = name.replace("-", ".")
        if key not in ("tiktok.research_token", "tiktok.shop_credentials"):
            raise HTTPException(404, "Unknown platform credential")
        if not store._clear_setting(p, key):
            return JSONResponse({"detail": "This credential was already removed."}, status_code=404)
        return {"removed": True}

    @router.get("/portal/api/settings/platforms")
    async def platform_status(request: Request):
        return store.platform_status(_owner(request))

    @router.post("/portal/api/settings/platforms/{platform}/{kind}")
    async def add_platform_setting(request: Request, platform: str, kind: str):
        p = _owner(request)
        _csrf(request, p)
        _check_platform_setting(platform, kind)
        data = await request.json()
        try:
            entry_id = store.add_platform_setting(p, platform, kind, str(data.get("value", "")))
        except (ConfigError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        if kind != "cookies":
            return {"id": entry_id}
        # The new account is checked at once. The entry is kept whatever the check says: a rejected account is
        # skipped by requests until it passes a later check, and the dashboard shows why.
        return {"id": entry_id, "check": await _check_account(p, platform, entry_id)}

    async def _check_account(p: Principal, platform: str, entry_id: str) -> dict[str, Any]:
        from . import sessions

        try:
            account, proxy = store.platform_account(p, platform, entry_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        try:
            return await sessions.check(platform, account, proxy)
        except Exception as exc:  # the check never fails the request that asked for it
            return {"ok": None, "status": "unknown", "reason": f"The account check failed ({type(exc).__name__})",
                    "account": None, "limited": False, "checked_at": time.time(), "latency_ms": None,
                    "supported": True, "health": sessions.entry_health(platform, entry_id)}

    @router.post("/portal/api/settings/platforms/{platform}/cookies/{entry_id}/check")
    async def check_platform_account(request: Request, platform: str, entry_id: str):
        """Ask the platform whether the saved account's session still works (its account check, run with the
        account's cookies through the proxy of its pool position) and keep the verdict as the account's health."""
        p = _owner(request)
        _csrf(request, p)
        _check_platform_setting(platform, "cookies")
        return await _check_account(p, platform, entry_id)

    @router.post("/portal/api/settings/platforms/{platform}/proxy/{entry_id}/test")
    async def test_platform_proxy(request: Request, platform: str, entry_id: str):
        p = _owner(request)
        _csrf(request, p)
        _check_platform_setting(platform, "proxy")
        try:
            proxy = store.platform_setting_value(p, platform, "proxy", entry_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        return await asyncio.to_thread(_test_proxy, platform, proxy)

    @router.delete("/portal/api/settings/platforms/{platform}/{kind}/{entry_id}")
    async def remove_platform_setting(request: Request, platform: str, kind: str, entry_id: str):
        p = _owner(request)
        _csrf(request, p)
        _check_platform_setting(platform, kind)
        if not store.remove_platform_setting(p, platform, kind, entry_id):
            return JSONResponse({"detail": "This platform setting was already removed."}, status_code=404)
        return {"removed": True}

    @router.post("/portal/api/settings/platforms/{platform}/{kind}/{entry_id}/move")
    async def move_platform_setting(request: Request, platform: str, kind: str, entry_id: str):
        p = _owner(request)
        _csrf(request, p)
        _check_platform_setting(platform, kind)
        data = await request.json()
        try:
            moved = store.move_platform_setting(p, platform, kind, entry_id, str(data.get("direction", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if not moved:
            return JSONResponse({"detail": "This platform setting could not move in that direction."}, status_code=404)
        return {"moved": True}

    @router.get("/portal/api/history")
    async def history(request: Request, limit: int = 100):
        return {"requests": store.history(_require(request), limit)}

    @router.get("/portal/api/history/{request_id}")
    async def history_detail(request: Request, request_id: int):
        detail = store.history_detail(_require(request), request_id)
        if not detail:
            raise HTTPException(404, "Request history entry not found")
        return detail

    @router.get("/portal/api/usage")
    async def usage(request: Request):
        return store.usage(_require(request))

    app.include_router(router)
