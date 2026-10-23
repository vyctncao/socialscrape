"""Optional ScrapeCreators fallback: a catalog request this server can't answer is asked of ScrapeCreators instead.

ScrapeCreators (https://api.scrapecreators.com) takes the same paths, params and x-api-key header as this API, so a
request that fails here, or that no route here serves (the 501s), can be sent there as it is, with the server's own
ScrapeCreators key. FallbackMiddleware runs inside ApiKeyMiddleware and CacheMiddleware: only authenticated callers
spend credits, and a successful answer is cached like a local one. The caller's headers are never forwarded.

The key and the options come from SOCIALSCRAPE_SCRAPECREATORS_* or, in their place, from the owner's settings in the
dashboard (/app/fallback), encrypted in the portal database like the platform proxies and cookies. Those apply to the
next request.
"""

from __future__ import annotations

import asyncio
import dataclasses
import http.client
import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from . import cursor, ytdlp
from .cache import CACHE_PARAM
from .catalog import CATALOG
from .config import PLATFORMS, ConfigError, _env, get_settings
from .errors import ApiError
from .portal import PortalStore, _csrf, _owner
from .responses import LenientJSONResponse

log = logging.getLogger("socialscrape.fallback")

PROVIDER = "scrapecreators"
DEFAULT_URL = "https://api.scrapecreators.com"
# On every response ScrapeCreators served (cache hits of one too): HEADER: PROVIDER. A request with HEADER: off is
# never sent there, nor answered from the cache with something it served.
HEADER = "x-socialscrape-fallback"
TRIGGERS = ("unsupported", "errors", "restricted", "not_found")
DEFAULT_TRIGGERS = ("unsupported", "errors")
MAX_BODY_BYTES = 16 * 1024 * 1024
API_KEY_SETTING = "scrapecreators.api_key"
OPTIONS_SETTING = "scrapecreators.options"
_CATALOG_PATHS = frozenset(entry["path"] for entry in CATALOG)
# The catalog's pagination params: the only ones that can carry our own cursors (cursor.PREFIX).
_CURSOR_PARAMS = frozenset({"continuationToken", "cursor", "max_cursor", "max_id", "next_max_id"})
# Sent with every fallback request. One that comes in with it isn't asked of ScrapeCreators again: an upstream URL
# pointed back at this server (or at another that falls back here) would otherwise loop until the timeouts.
_VIA = "1.1 socialscrape"

_executor: ThreadPoolExecutor | None = None


@dataclasses.dataclass(frozen=True)
class FallbackEnv:
    """The SOCIALSCRAPE_SCRAPECREATORS_* variables."""

    api_key: str | None = dataclasses.field(repr=False)  # repr=False: never in a log line or a traceback
    url: str  # no trailing slash
    triggers: tuple[str, ...]
    platforms: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Config:
    """What applies to a request: the owner's saved key and options, each in place of the environment's."""

    api_key: str | None = dataclasses.field(repr=False)
    enabled: bool
    triggers: tuple[str, ...]
    platforms: tuple[str, ...]
    key_source: str | None  # "dashboard", "environment" or None
    key_updated_at: float | None
    options_source: str  # "dashboard" or "environment"
    options_updated_at: float | None

    @property
    def active(self) -> bool:
        return bool(self.api_key and self.enabled and self.triggers and self.platforms)


def _api_key(value: str | None, name: str) -> str | None:
    """A key as yt-dlp's and urllib's headers can carry it: printable ASCII without spaces. ConfigError names `name`
    and never shows the value."""
    if value and (not value.isascii() or any(c.isspace() or not c.isprintable() for c in value)):
        raise ConfigError(f"{name} must be printable ASCII without spaces")
    return value or None


def _names(name: str, choices: tuple[str, ...], default: tuple[str, ...], *, aliases: bool = False) -> tuple[str, ...]:
    """A comma-separated list of `choices` (case-insensitive), in their order; with `aliases`, also none or all."""
    raw = _env(name)
    if raw is None:
        return default
    values = [value.strip().lower() for value in raw.split(",") if value.strip()]
    if aliases and values in (["none"], ["all"]):
        return () if values == ["none"] else choices
    if unknown := next((value for value in values if value not in choices), None):
        valid = ", ".join((*choices, "none", "all") if aliases else choices)
        raise ConfigError(f"{name} must be a comma-separated list of {valid}, got {unknown!r}")
    return tuple(choice for choice in choices if choice in values)


def env_config() -> FallbackEnv:
    """The fallback's environment variables, checked. create_app() reads them at startup, so a value that can't be used
    stops it with a ConfigError naming the variable, like every other setting."""
    url_name = "SOCIALSCRAPE_SCRAPECREATORS_URL"
    url = (_env(url_name) or DEFAULT_URL).rstrip("/")
    try:
        parts = urlsplit(url)
        usable = (parts.scheme.lower() in ("http", "https") and bool(parts.hostname) and parts.port != 0
                  and not parts.query and not parts.fragment)
    except ValueError:  # a port that isn't a number or is out of range, 'Invalid IPv6 URL'
        usable = False
    if not usable:  # the URL isn't shown: a relay's may carry credentials
        raise ConfigError(f"{url_name} must be an http or https URL with a host and no query or fragment")
    key_name = "SOCIALSCRAPE_SCRAPECREATORS_API_KEY"
    return FallbackEnv(_api_key(_env(key_name), key_name), url,
                       _names("SOCIALSCRAPE_SCRAPECREATORS_FALLBACK", TRIGGERS, DEFAULT_TRIGGERS, aliases=True),
                       _names("SOCIALSCRAPE_SCRAPECREATORS_PLATFORMS", PLATFORMS, PLATFORMS))


def _options(data: Any) -> dict[str, Any]:
    """The dashboard's options, checked: {"enabled": bool, "triggers": [...], "platforms": [...]}, the lists in TRIGGERS'
    and PLATFORMS' order. ValueError says what's wrong (a 400's message)."""
    if not isinstance(data, dict) or set(data) != {"enabled", "triggers", "platforms"}:
        raise ValueError("Send enabled, triggers and platforms")
    if not isinstance(data["enabled"], bool):
        raise ValueError("enabled must be true or false")
    for name, choices in (("triggers", TRIGGERS), ("platforms", PLATFORMS)):
        values = data[name]
        if (not isinstance(values, list) or not all(isinstance(v, str) and v in choices for v in values)
                or len(set(values)) != len(values)):
            raise ValueError(f"{name} must be a list of distinct values from {', '.join(choices)}")
    return {"enabled": data["enabled"], "triggers": [t for t in TRIGGERS if t in data["triggers"]],
            "platforms": [p for p in PLATFORMS if p in data["platforms"]]}


def _saved(store: PortalStore | None) -> tuple[tuple[str, float] | None, tuple[dict[str, Any], float] | None]:
    """The owner's saved (key, updated_at) and (options, updated_at), or None for each that isn't saved. Settings that
    can't be read (SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY changed or unset since they were saved) count as unsaved, with a
    warning: the environment's apply, and the request doesn't fail over its fallback."""
    if store is None:
        return None, None
    try:
        key_row, options_row = store._setting(API_KEY_SETTING), store._setting(OPTIONS_SETTING)
    except ConfigError as exc:
        log.warning("the saved ScrapeCreators fallback settings can't be read, so the environment's apply: %s", exc)
        return None, None
    key = options = None
    try:
        if key_row and (value := _api_key(key_row[0].decode(), "the saved ScrapeCreators API key")):
            key = value, key_row[1]
    except ValueError:  # UnicodeDecodeError, ConfigError: not a value the dashboard saves
        log.warning("the saved ScrapeCreators API key is invalid, so the environment's applies")
    try:
        if options_row:
            options = _options(json.loads(options_row[0])), options_row[1]
    except ValueError:  # UnicodeDecodeError, JSONDecodeError, a bad value
        log.warning("the saved ScrapeCreators fallback options are invalid, so the environment's apply")
    return key, options


def config(env: FallbackEnv, store: PortalStore | None) -> Config:
    key, options = _saved(store)
    api_key, key_source = (key[0], "dashboard") if key else (env.api_key, "environment" if env.api_key else None)
    if options:
        return Config(api_key, options[0]["enabled"], tuple(options[0]["triggers"]), tuple(options[0]["platforms"]),
                      key_source, key[1] if key else None, "dashboard", options[1])
    return Config(api_key, True, env.triggers, env.platforms, key_source, key[1] if key else None, "environment",
                  None)


def status(env: FallbackEnv, store: PortalStore | None) -> dict[str, Any]:
    """The dashboard's view of the settings. Never the key."""
    c = config(env, store)
    return {"provider": PROVIDER, "active": c.active, "enabled": c.enabled, "triggers": list(c.triggers),
            "platforms": list(c.platforms), "options_source": c.options_source,
            "options_updated_at": c.options_updated_at,
            "api_key": {"configured": bool(c.api_key), "source": c.key_source, "updated_at": c.key_updated_at}}


def describe(app) -> dict[str, Any] | None:
    """For /health: what falls back where, or None when nothing does."""
    c = config(app.state.fallback_env, getattr(app.state, "portal", None))
    return {"provider": PROVIDER, "triggers": list(c.triggers), "platforms": list(c.platforms)} if c.active else None


def install_routes(app, store: PortalStore) -> None:
    """The owner's fallback settings: GET and PUT the options, PUT and DELETE the key, which is never sent back. Writes
    need the CSRF token, as the other portal settings do."""
    router = APIRouter(include_in_schema=False)

    @router.get("/portal/api/settings/fallback")
    async def fallback_settings(request: Request):
        _owner(request)
        return status(app.state.fallback_env, store)

    @router.put("/portal/api/settings/fallback")
    async def set_fallback_options(request: Request):
        owner = _owner(request)
        _csrf(request, owner)
        try:
            options = _options(await request.json())  # a body that isn't JSON is a ValueError too
            store._set_setting(owner, OPTIONS_SETTING, json.dumps(options, separators=(",", ":")))
        except ValueError as exc:  # ConfigError: no usable SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY
            raise HTTPException(400, str(exc)) from None
        return status(app.state.fallback_env, store)

    @router.put("/portal/api/settings/fallback/api-key")
    async def set_fallback_key(request: Request):
        owner = _owner(request)
        _csrf(request, owner)
        try:
            data = await request.json()
        except ValueError:
            data = None
        value = data.get("value") if isinstance(data, dict) else None
        value = value.strip() if isinstance(value, str) else ""
        try:
            if not 0 < len(value) <= 512 or not _api_key(value, "key"):
                raise ValueError
        except ValueError:  # the message never echoes the value
            raise HTTPException(400, "Enter a ScrapeCreators API key: printable ASCII without spaces") from None
        try:
            store._set_setting(owner, API_KEY_SETTING, value)
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"configured": True}

    @router.delete("/portal/api/settings/fallback/api-key")
    async def clear_fallback_key(request: Request):
        owner = _owner(request)
        _csrf(request, owner)
        return {"cleared": store._clear_setting(owner, API_KEY_SETTING)}

    app.include_router(router)


# --- forwarding ---------------------------------------------------------------


def _pool() -> ThreadPoolExecutor:
    """Requests to ScrapeCreators run here, not in asyncio's default pool: one that outlives its timeout (a thread
    can't be stopped) holds a thread of its own until its deadline, never one of anything else's."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=get_settings().max_workers, thread_name_prefix="fallback")
    return _executor


def shutdown_pool() -> list[threading.Thread]:
    """Stop the pool without waiting and return its worker threads, as ytdlp.shutdown_pool does."""
    global _executor
    pool, _executor = _executor, None
    return ytdlp.stop_executor(pool)


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """certifi's CAs, as yt-dlp uses (uv's Python may have no system ones), when it's there."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is the answer, not followed: the key would go along to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _user_agent() -> str:
    try:
        return f"socialscrape/{version('socialscrape')}"
    except PackageNotFoundError:
        return "socialscrape"


def _fetch(url: str, api_key: str, timeout: float) -> tuple[int | None, bytes, str | None]:
    """GET `url` with the ScrapeCreators key: (status, body, None), or (status if one came, b"", problem) when no
    whole answer did, problem being timeout, network_error or bad_response (a body past MAX_BODY_BYTES).

    The whole answer must come within `timeout`. urllib's timeout is per socket read, so a body trickled a byte at a
    time would keep the thread past it: each read here is one socket read (read1), allowed only the time left."""
    request = urllib.request.Request(url, headers={"x-api-key": api_key, "accept": "application/json",
                                                   "user-agent": _user_agent(), "via": _VIA})
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context()), _NoRedirect())
    deadline = time.monotonic() + timeout
    status = None
    try:
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:  # a non-2xx answer, read like a 2xx
            response = exc
        with response:
            status = response.status
            # The http.client.HTTPResponse (an HTTPError wraps one) and its socket, which has no public accessor.
            answer = response.fp if isinstance(response, urllib.error.HTTPError) else response
            sock = getattr(getattr(getattr(answer, "fp", None), "raw", None), "_sock", None)
            body = bytearray()
            while len(body) <= MAX_BODY_BYTES:
                if (left := deadline - time.monotonic()) <= 0:
                    return status, b"", "timeout"
                if sock is not None and not answer.isclosed():  # closed: the whole body is in, its socket gone
                    sock.settimeout(left)
                if not (chunk := answer.read1(64 * 1024)):
                    return status, bytes(body), None
                body += chunk
            return status, b"", "bad_response"
    except TimeoutError:  # socket.timeout
        return status, b"", "timeout"
    except urllib.error.URLError as exc:  # DNS, refused, TLS; a connect timeout is one too
        return status, b"", "timeout" if isinstance(exc.reason, TimeoutError) else "network_error"
    except (OSError, http.client.HTTPException):  # reset mid-answer, a malformed status line
        return status, b"", "network_error"


def _json_object(payload: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(payload)
    except (ValueError, RecursionError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        return None
    return value if isinstance(value, dict) else None


def _trigger(status: int, code: str | None) -> str | None:
    """The trigger (TRIGGERS) a local error falls under, or None: a caller's own mistake (400), a missing key (401),
    a wrong method (405), estimate_only (422) and paths outside the catalog never fall back."""
    if status == 501 or (status == 400 and code == "bad_cursor"):  # a token we can't read may be ScrapeCreators'
        return "unsupported"
    if status in (500, 502, 503):
        return "errors"
    if status == 403:
        return "restricted"
    if status == 404 and code == "not_found":
        return "not_found"
    return None


_PROBLEMS = {
    "timeout": ("timeout", "ScrapeCreators didn't answer in time"),
    "network_error": ("network_error", "ScrapeCreators couldn't be reached"),
    "bad_response": ("bad_response", "ScrapeCreators' answer wasn't a JSON object"),
}
_STATUSES = {
    401: ("unauthorized", "ScrapeCreators refused the API key"),
    402: ("payment_required", "The ScrapeCreators account has no credits left"),
    429: ("rate_limited", "ScrapeCreators rate-limited the request"),
}


def _failure(status: int | None, answer: dict[str, Any] | None, problem: str | None, api_key: str) -> dict[str, Any]:
    """The "fallback" detail added to the local error when ScrapeCreators' answer doesn't take its place. Its error and
    message are ScrapeCreators' own where it gave them, cut short, with the key taken out of them (only of them: a
    short key would take letters out of our own words)."""
    if problem:
        error, message = _PROBLEMS[problem]
    elif status is not None and 200 <= status < 300:  # a 2xx that isn't a JSON object
        error, message = _PROBLEMS["bad_response"]
    else:
        error, message = _STATUSES.get(status, ("upstream_error", f"ScrapeCreators answered HTTP {status}"))
    if answer:
        def scrub(text: str, limit: int) -> str:
            return (text.replace(api_key, "[key]") if api_key else text)[:limit]

        if isinstance(answer.get("error"), str) and answer["error"]:
            error = scrub(answer["error"], 80)
        if own := next((v for v in (answer.get("message"), answer.get("error")) if isinstance(v, str) and v), None):
            message = scrub(own, 300)
    return {"provider": PROVIDER, "status": status, "error": error, "message": message}


class FallbackMiddleware(BaseHTTPMiddleware):
    """Asks ScrapeCreators when a catalog GET fails with an enabled trigger (see _trigger), and answers with:

    - its success (2xx, a JSON object) as it is, with HEADER;
    - its 400, 403, 404 or 422 (a JSON object) as it is, with HEADER, when we had no answer of our own (a 501 or
      bad_cursor): its verdict on the request or the content is the answer;
    - otherwise the local error, with a "fallback" detail of what ScrapeCreators said (see _failure).

    Added before CacheMiddleware, so it runs after it (Starlette runs the last-added middleware first)."""

    def __init__(self, app, env: FallbackEnv):
        super().__init__(app)
        self.env = env

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if request.method != "GET" or path not in _CATALOG_PATHS:
            return await call_next(request)
        c = config(self.env, getattr(request.app.state, "portal", None))
        if (not c.active or path.split("/")[2] not in c.platforms or _VIA in request.headers.get("via", "")
                # The caller asked for this server's own answer (the dashboard's endpoint tests do).
                or request.headers.get(HEADER, "").strip().lower() == "off"
                # Our own cursors mean nothing to ScrapeCreators.
                or any(name in _CURSOR_PARAMS and value.startswith(cursor.PREFIX)
                       for name, value in request.query_params.multi_items())):
            return await call_next(request)
        try:
            response = await call_next(request)
        except Exception:  # not an ApiError: those are responses by now
            if "errors" not in c.triggers:
                raise
            log.exception("%s failed; asking ScrapeCreators", path)  # the traceback ServerErrorMiddleware would log
            local_status, local, headers = 500, ApiError("Internal server error").body(), {}
        else:
            if response.status_code < 400:
                return response
            local_status = response.status_code
            payload = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            local = _json_object(payload)
            if local is None or _trigger(local_status, local.get("error")) not in c.triggers:
                return Response(payload, local_status, headers=headers)
            headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
        code = local.get("error")
        status, body, problem = await self._ask(request, c.api_key or "")
        answer = _json_object(body) if problem is None else None
        no_local_answer = local_status == 501 or code == "bad_cursor"
        if answer is not None and status is not None and (
                200 <= status < 300 or (no_local_answer and status in (400, 403, 404, 422))):
            log.info("%s: local %s %s, served by ScrapeCreators (%s)", path, local_status, code, status)
            return Response(body, status, headers={HEADER: PROVIDER}, media_type="application/json")
        detail = _failure(status, answer, problem, c.api_key or "")
        log.info("%s: local %s %s, ScrapeCreators failed too (%s %s)", path, local_status, code, status,
                 detail["error"])
        return LenientJSONResponse({**local, "fallback": detail}, local_status, headers=headers)

    async def _ask(self, request: Request, api_key: str) -> tuple[int | None, bytes, str | None]:
        # Without cache_max_age: an answer from ScrapeCreators' cache would go into ours as new, and then be served to a
        # cache_max_age it is older than.
        query = urlencode([(k, v) for k, v in request.query_params.multi_items() if k != CACHE_PARAM])
        url = f"{self.env.url}{request.url.path}" + (f"?{query}" if query else "")
        timeout = get_settings().request_timeout
        loop = asyncio.get_running_loop()
        try:  # the time queued for a thread counts too
            return await asyncio.wait_for(loop.run_in_executor(_pool(), _fetch, url, api_key, timeout), timeout)
        except TimeoutError:
            return None, b"", "timeout"
