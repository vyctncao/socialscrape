"""Response cache implementing ScrapeCreators' `cache_max_age` semantics.

Every successful GET is stored. A request carrying `cache_max_age=7d` gets the
stored body (plus `"cached": true, "cached_at": ...`) if it is at most that
old; otherwise it is fetched live. Requests without the param are always live.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import ConfigError
from .errors import BadRequest
from .responses import LenientJSONResponse
from .util import clean_param

log = logging.getLogger("socialscrape.cache")

_AGE = re.compile(r"^([0-9]{1,6})\s*([dh]?)$", re.I)
CACHE_PARAM = "cache_max_age"
MAX_BODY_BYTES = 8 * 1024 * 1024


def parse_max_age(value: str) -> float:
    """'7d' -> seconds. Bare numbers are days; 'h' suffix is hours."""
    if m := _AGE.match(value.strip()):
        return int(m.group(1)) * (3600 if m.group(2).lower() == "h" else 86400)
    raise BadRequest(f"Invalid {CACHE_PARAM}: use e.g. 1d, 3d, 7d, 14d, 30d")


class ResponseCache:
    def __init__(self, path: str):
        """Open (or create) the SQLite file, and check that it can be written: ConfigError naming
        SOCIALSCRAPE_CACHE_PATH if not (a directory, a file that isn't SQLite, a read-only file or directory)."""
        self.path = path
        self._lock = threading.Lock()
        try:
            if os.path.dirname(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("CREATE TABLE IF NOT EXISTS responses "
                               "(key TEXT PRIMARY KEY, stored_at REAL NOT NULL, body TEXT NOT NULL)")
            # A write that changes nothing: an existing read-only file passes the statements above, and so does one
            # in a read-only directory (where WAL mode can't create its -wal file).
            self._conn.execute("DELETE FROM responses WHERE key = ''")
            self._conn.commit()
        except (sqlite3.Error, OSError) as exc:
            raise ConfigError(f"SOCIALSCRAPE_CACHE_PATH {path!r} can't be used as a SQLite cache: {exc}") from None

    def close(self) -> None:
        self._conn.close()

    # A cache that fails after startup (disk full, I/O error) is skipped with a warning: the request is served live
    # and its response is still sent.
    def get(self, key: str, max_age: float) -> tuple[dict[str, Any], datetime] | None:
        try:
            with self._lock:
                row = self._conn.execute("SELECT body, stored_at FROM responses WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error as exc:
            log.warning("cache read failed (%s): %s", self.path, exc)
            return None
        if not row:
            return None
        try:
            if time.time() - row[1] > max_age:
                return None
            body, stored_at = json.loads(row[0]), datetime.fromtimestamp(row[1], timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError, RecursionError):
            body = None
        if not isinstance(body, dict):
            # A row put() didn't write (another program's, or edited by hand: text that isn't a JSON object, a
            # stored_at that isn't a time) is skipped too: the request is served live, and its response replaces it.
            log.warning("cache read failed (%s): the row for %s isn't a stored response", self.path, key)
            return None
        return body, stored_at

    def put(self, key: str, body: str) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO responses (key, stored_at, body) VALUES (?, ?, ?)", (key, time.time(), body)
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            log.warning("cache write failed (%s): %s", self.path, exc)


def cache_key(request: Request) -> str:
    # Sort by name only: the sort is stable, so a repeated param keeps its order (?a=1&a=2 and ?a=2&a=1 are
    # different requests; handlers read the last value).
    params = sorted(((k, v) for k, v in request.query_params.multi_items() if k != CACHE_PARAM), key=lambda kv: kv[0])
    return f"{request.url.path}?{urlencode(params)}"


class CacheMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, cache: ResponseCache | None = None):
        """`cache` None: the cache is off and requests pass through. create_app() opens it, at startup."""
        super().__init__(app)
        self.cache = cache

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method != "GET" or not request.url.path.startswith("/v"):
            return await call_next(request)
        try:  # checked with the cache off too: a bad value is a 400 on every route
            raw_age = clean_param(request.query_params.get(CACHE_PARAM) or "")  # as the routes read their params
            max_age = parse_max_age(raw_age) if raw_age else None
        except BadRequest as exc:
            return JSONResponse(exc.body(), status_code=exc.status)
        if self.cache is None:
            return await call_next(request)

        key = cache_key(request)
        if max_age is not None and (hit := self.cache.get(key, max_age)):
            body, stored_at = hit
            body["cached"] = True
            body["cached_at"] = stored_at.isoformat().replace("+00:00", "Z")
            # Lenient: a row put() didn't write can hold what JSON can't write (a NaN, half an emoji)
            return LenientJSONResponse(body, headers={"x-cache": "hit"})

        response = await call_next(request)
        if response.status_code != 200 or "application/json" not in response.headers.get("content-type", ""):
            return response

        chunks = [chunk async for chunk in response.body_iterator]  # type: ignore[attr-defined]
        payload = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
        if len(payload) <= MAX_BODY_BYTES:
            self.cache.put(key, payload.decode())
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        headers["x-cache"] = "miss"
        return Response(payload, status_code=200, headers=headers, media_type="application/json")
