"""App factory and CLI entry point."""

from __future__ import annotations

import argparse
import dataclasses
import hmac
import importlib
import ipaddress
import logging
import os
import pkgutil
import signal
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

import yt_dlp

from . import fallback, platforms, transcribe, ytdlp
from .cache import CacheMiddleware, ResponseCache
from .config import ConfigError, _env, get_settings
from .errors import (ApiError, Unauthorized, api_error_handler, http_error_handler, unhandled_error_handler,
                     validation_error_handler)
from .portal import PortalStore, clear_active_portal, install_portal
from .registry import IMPLEMENTED, add_unsupported_routes, coverage, mark_unsupported

log = logging.getLogger("socialscrape")

PUBLIC_PATHS = {"/health", "/redoc", "/meta/coverage", "/favicon.svg", "/favicon.ico"}
LEGACY_PUBLIC_PATHS = PUBLIC_PATHS | {"/docs", "/openapi.json"}
PORTAL_PUBLIC_PREFIXES = ("/login", "/setup", "/invite/", "/assets/", "/app", "/docs")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """`x-api-key` auth, same header as ScrapeCreators. Off when no keys are configured."""

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        path = request.url.path.rstrip("/") or "/"
        portal = getattr(request.app.state, "portal", None)
        public_paths = PUBLIC_PATHS if portal else LEGACY_PUBLIC_PATHS
        portal_route = portal and (path == "/" or request.url.path.startswith(PORTAL_PUBLIC_PREFIXES))
        if path in public_paths or portal_route:
            return await call_next(request)
        if portal and (principal := portal.principal_for_session(request.cookies.get("socialscrape_session"))):
            request.state.principal = principal
            selected = request.headers.get("x-portal-key-id", "")
            if selected.isascii() and selected.isdigit() and portal.key_available_to(principal, int(selected)):
                request.state.api_key_id = int(selected)
            return await call_next(request)
        # The header's raw bytes (compare_digest rejects a non-ASCII str), stripped like configured keys: uvicorn's
        # httptools parser keeps trailing spaces and tabs, which aren't part of the value.
        given_text = request.headers.get("x-api-key", "").strip(" \t")
        given = given_text.encode("latin-1")
        if portal and given_text and (identity := portal.authenticate_key(given_text)):
            request.state.principal, request.state.api_key_id = identity
            return await call_next(request)
        if settings.api_keys and any(hmac.compare_digest(given, k.encode()) for k in settings.api_keys):
            return await call_next(request)
        if settings.api_keys or portal:
            err = Unauthorized("Missing or invalid x-api-key header")
            return JSONResponse(err.body(), status_code=err.status)
        return await call_next(request)


class UsageMiddleware(BaseHTTPMiddleware):
    """Record API calls made through a member key for dashboard history and usage."""

    async def dispatch(self, request: Request, call_next):
        started = perf_counter()
        response = await call_next(request)
        portal = getattr(request.app.state, "portal", None)
        if portal and request.url.path.startswith("/v"):
            chunks = [chunk async for chunk in response.body_iterator]  # type: ignore[attr-defined]
            payload = b"".join(chunk if isinstance(chunk, bytes) else chunk.encode() for chunk in chunks)
            try:
                portal.record_request(getattr(request.state, "principal", None),
                                      getattr(request.state, "api_key_id", None), request.method,
                                      request.url.path, request.url.query, response.status_code,
                                      round((perf_counter() - started) * 1000),
                                      response.headers.get("x-cache") == "hit", payload,
                                      response.headers.get(fallback.HEADER) == fallback.PROVIDER)
            except sqlite3.Error as exc:  # a full disk or a locked database loses the history row, not the response
                log.warning("request history write failed: %s", exc)
            headers = {key: value for key, value in response.headers.items() if key.lower() != "content-length"}
            return Response(payload, status_code=response.status_code, headers=headers,
                            media_type=response.media_type, background=response.background)
        return response


def load_platform_modules(app: FastAPI) -> None:
    """Include `router` (and `UNSUPPORTED` reasons) from every socialscrape.platforms module."""
    for mod_info in sorted(pkgutil.iter_modules(platforms.__path__), key=lambda m: m.name):
        if mod_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{platforms.__name__}.{mod_info.name}")
        if router := getattr(module, "router", None):
            app.include_router(router, tags=[mod_info.name.split("_")[0]])
        mark_unsupported(getattr(module, "UNSUPPORTED", {}))


def create_app() -> FastAPI:
    # Read now: a bad setting raises ConfigError here, at startup, instead of a 500 on every request.
    settings = get_settings()
    fallback_env = fallback.env_config()
    if fallback_env.api_key and not (settings.api_keys or settings.portal_path):
        # Anyone who could reach the server would spend the credits. (A dashboard key implies the portal's auth.)
        log.warning("SOCIALSCRAPE_SCRAPECREATORS_API_KEY is ignored: the ScrapeCreators fallback needs "
                    "SOCIALSCRAPE_API_KEYS or SOCIALSCRAPE_PORTAL_PATH, so that only known callers spend its credits")
        fallback_env = dataclasses.replace(fallback_env, api_key=None)
    transcribe.check_model()
    if settings.transcribe_backend != "none" and not transcribe.enabled():
        log.warning("SOCIALSCRAPE_TRANSCRIBE=%s but its package is not installed: AI transcription answers 501 "
                    "transcription_unavailable", settings.transcribe_backend)
    app = FastAPI(
        title="Social Scrape",
        version="0.1.0",
        description="Private, self-hosted social data API for YouTube, TikTok and Instagram, powered by yt-dlp.",
        docs_url=None,
        redoc_url=None,
    )
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.state.fallback_env = fallback_env
    # Starlette runs the last-added middleware first: auth, usage, cache, then the ScrapeCreators fallback, so only
    # authenticated requests spend credits and its answers are cached. The cache is opened here because Starlette
    # builds middleware on the first request, too late to stop startup on an unusable SOCIALSCRAPE_CACHE_PATH.
    app.add_middleware(fallback.FallbackMiddleware, env=fallback_env)
    app.add_middleware(CacheMiddleware, cache=ResponseCache(settings.cache_path) if settings.cache_path else None)
    app.add_middleware(UsageMiddleware)
    app.add_middleware(ApiKeyMiddleware)

    load_platform_modules(app)
    add_unsupported_routes(app)
    clear_active_portal()
    if settings.portal_path:
        portal = PortalStore(settings.portal_path)
        portal.bootstrap_owner(os.environ.get("SOCIALSCRAPE_OWNER_EMAIL"),
                               os.environ.get("SOCIALSCRAPE_OWNER_PASSWORD"))
        install_portal(app, portal)
        fallback.install_routes(app, portal)

    @app.api_route("/favicon.svg", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
    async def favicon(request: Request):
        asset = Path(__file__).with_name("static") / "brand" / request.url.path.rsplit("/", 1)[-1]
        return FileResponse(asset, headers={"cache-control": "public, max-age=0, must-revalidate"})

    if not settings.portal_path:
        @app.api_route("/docs", methods=["GET", "HEAD"], include_in_schema=False)
        async def swagger_docs():
            return get_swagger_ui_html(openapi_url=app.openapi_url, title="Social Scrape API docs",
                                       swagger_favicon_url="/favicon.svg")

        @app.api_route("/redoc", methods=["GET", "HEAD"], include_in_schema=False)
        async def redoc_docs():
            return get_redoc_html(openapi_url=app.openapi_url, title="Social Scrape API reference",
                                  redoc_favicon_url="/favicon.svg")

    # HEAD for uptime probes (curl -I). Not on the data routes: there it would still run the scrape.
    @app.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
    async def health() -> dict:
        s = get_settings()
        # Configured cookie files that can't be used (each request for that platform answers 500 cookies_error).
        cookie_errors = {p: problem for p, path in sorted(s.cookie_files.items())
                         if (problem := ytdlp.cookie_file_problem(path))}
        return {
            "ok": True,
            "yt_dlp": yt_dlp.version.__version__,
            "auth": bool(s.api_keys or s.portal_path),
            "cache": bool(s.cache_path),
            "transcription": s.transcribe_backend if transcribe.enabled() else None,  # null: off or not installed
            "cookies": [p for p in sorted(s.cookie_files) if p not in cookie_errors]
                       + (["browser"] if s.cookies_from_browser else []),
            "cookie_errors": cookie_errors,
            "fallback": fallback.describe(app),
            "implemented_endpoints": len(IMPLEMENTED),
        }

    @app.get("/meta/coverage", tags=["meta"])
    async def meta_coverage() -> dict:
        rows = coverage()
        return {
            "success": True,
            "implemented": sum(r["status"] == "implemented" for r in rows),
            "total": len(rows),
            "endpoints": rows,
        }

    return app


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _ascii_digits(value: str) -> bool:
    """'8000' but not '²' or '٨': str.isdigit() is true for digits that int() rejects (or reads differently)."""
    return value.isascii() and value.isdigit()


def _bind_host(host: str) -> str:
    """'[::1]' -> '::1': uvicorn binds the bare address, and with the brackets it fails without naming HOST."""
    if host.startswith("[") and host.endswith("]"):
        try:
            ipaddress.IPv6Address(host[1:-1])
            return host[1:-1]
        except ValueError:
            pass
    return host


def _parse_args(argv: list[str] | None) -> None:
    from importlib.metadata import version

    parser = argparse.ArgumentParser(
        prog="socialscrape",
        description="Run the socialscrape API server (a self-hosted, ScrapeCreators-compatible API for YouTube, "
                    "TikTok and Instagram). It takes no arguments: it is configured with environment variables "
                    "(HOST, PORT, SOCIALSCRAPE_*), all listed in .env.example.")
    parser.add_argument("--version", action="version",
                        version=f"socialscrape {version('socialscrape')} (yt-dlp {yt_dlp.version.__version__})")
    parser.parse_args(argv)


def _stop_workers(grace: float = 1.0) -> int:
    """After uvicorn has stopped, stop the yt-dlp, Whisper and ScrapeCreators threads; how many are still busy after
    `grace` seconds. Those run calls whose requests are gone (SOCIALSCRAPE_REQUEST_TIMEOUT, or a forced quit)."""
    workers = ytdlp.shutdown_pool() + transcribe.shutdown_pool() + fallback.shutdown_pool()
    deadline = time.monotonic() + grace
    for thread in workers:
        thread.join(max(0.0, deadline - time.monotonic()))
    return sum(thread.is_alive() for thread in workers)


def _ignore_signal(signum: int, frame: object) -> None:
    """A Python-level no-op rather than SIG_IGN, which processes started meanwhile (ffmpeg) would inherit."""


def run(argv: list[str] | None = None) -> None:
    """`socialscrape` CLI. Blank HOST/PORT/SOCIALSCRAPE_LOG_LEVEL mean their defaults, like every other setting;
    a value that can't be used exits with a one-line message instead of a traceback."""
    import uvicorn

    _parse_args(argv)
    host = _bind_host(_env("HOST", "127.0.0.1") or "127.0.0.1")
    raw_port = _env("PORT", "8000") or "8000"
    if not _ascii_digits(raw_port) or not 1 <= int(raw_port) <= 65535:
        raise SystemExit(f"socialscrape: PORT must be an integer from 1 to 65535, got {raw_port!r}")
    level = (_env("SOCIALSCRAPE_LOG_LEVEL", "INFO") or "INFO").upper()
    if not _ascii_digits(level) and level not in logging.getLevelNamesMapping():
        raise SystemExit(f"socialscrape: SOCIALSCRAPE_LOG_LEVEL must be a level name such as DEBUG, INFO or "
                         f"WARNING, got {level!r}")
    level_no = int(level) if _ascii_digits(level) else logging.getLevelNamesMapping()[level]
    try:
        socket.getaddrinfo(host, int(raw_port), type=socket.SOCK_STREAM)
    except (OSError, ValueError):  # gaierror; UnicodeError for a name IDNA can't encode
        raise SystemExit(f"socialscrape: HOST must be an IP address or a host name that resolves, got {host!r}") \
            from None

    logging.basicConfig(level=level_no)
    try:
        # Built here, not by uvicorn (factory=True): a ConfigError from create_app (an unusable cache path, ...)
        # is then this one-line exit instead of a traceback.
        app = create_app()
    except ConfigError as exc:
        raise SystemExit(f"socialscrape: {exc}") from None
    settings = get_settings()
    if not settings.api_keys and not settings.portal_path and not _is_loopback(host):
        log.warning("listening on %s with SOCIALSCRAPE_API_KEYS unset and the portal disabled: anyone who can reach "
                    "this address can use the API", host)
    main_thread = threading.current_thread() is threading.main_thread()  # signal.signal works only there
    try:
        # log_level: uvicorn's own loggers (startup lines, access log) don't follow the root logger's level.
        uvicorn.run(app, host=host, port=int(raw_port), log_level=level_no)
    except KeyboardInterrupt:  # a 2nd Ctrl-C as uvicorn returned (it swallows the KeyboardInterrupt of the 1st)
        pass
    # Ctrl-C does nothing until the workers are stopped (a second at most): a KeyboardInterrupt would skip os._exit,
    # and catching one fails because an interrupted Thread.join() marks the running worker stopped (bpo-45274).
    previous = signal.signal(signal.SIGINT, _ignore_signal) if main_thread else None
    try:
        # Python joins pool threads at exit: don't wait for calls that outlived their requests (a hung proxy, say).
        if busy := _stop_workers():
            log.warning("exiting without waiting for %d yt-dlp/Whisper call(s) that outlived their requests", busy)
            logging.shutdown()
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0)
    finally:
        if previous is not None:  # None: not set from Python, or not the main thread
            signal.signal(signal.SIGINT, previous)
