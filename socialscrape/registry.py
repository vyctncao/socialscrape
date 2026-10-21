"""Route registration with the ScrapeCreators envelope and coverage tracking.

Platform modules declare routes like:

    router = APIRouter()

    @sc_get(router, "/v1/youtube/video", source="yt-dlp YoutubeIE")
    async def youtube_video(url: str = Query(...), language: str | None = None):
        info = await with_ydl("youtube", lambda ydl: ydl.extract_info(url, download=False))
        return {...}          # the payload; "success": true is added here

and list what they deliberately leave out:

    UNSUPPORTED = {"/v1/instagram/basic-profile": "why it can't be done with yt-dlp"}

Any catalog path without a route becomes a 501 with that reason.
"""

from __future__ import annotations

import functools
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI, Query, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from .catalog import CATALOG
from .errors import NotSupported, UpstreamError, error_responses
from .responses import LenientJSONResponse
from .util import clean_param

log = logging.getLogger("socialscrape.registry")


@dataclass
class RouteInfo:
    source: str
    notes: str | None = None


IMPLEMENTED: dict[str, RouteInfo] = {}
UNSUPPORTED_REASONS: dict[str, str] = {}


# Dependencies that only document the x-api-key header (ScrapeCreators' apiKeyAuth scheme, so /docs gets an
# Authorize button) and cache_max_age: ApiKeyMiddleware and CacheMiddleware do the work.
_api_key = APIKeyHeader(name="x-api-key", scheme_name="apiKeyAuth", auto_error=False,
                        description="One of the server's SOCIALSCRAPE_API_KEYS (not needed when that is empty).")


async def _cache_max_age(
    cache_max_age: str | None = Query(None, description=(
        "Serve a cached response at most this old: 1d, 3d, 7d, 14d, 30d, or hours like 12h. Without it the "
        "response is always fresh.")),
) -> None:
    """Documents the param; CacheMiddleware validates it."""


def envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "credits_charged": 0, **payload}


def sc_get(router: APIRouter, path: str, *, source: str, notes: str | None = None, **route_kwargs: Any):
    """Register a GET route whose handler returns the response payload dict."""

    def decorate(fn: Callable[..., Awaitable[dict[str, Any]]]):
        @functools.wraps(fn)
        async def handler(*args: Any, **kwargs: Any) -> JSONResponse:
            # Every param is a str or None: a pasted URL or handle loses its invisible ends and <brackets> first
            kwargs = {k: clean_param(v) if isinstance(v, str) else v for k, v in kwargs.items()}
            try:
                return LenientJSONResponse(envelope(await fn(*args, **kwargs)))
            except RecursionError as exc:
                # A platform response nested deeper than Python reads JSON (about 10000 levels; yt-dlp only takes
                # a body that isn't JSON for an error) or than our walks of it recurse (1000). The traceback stays
                # in the log, as it was for the 500 this used to be.
                log.warning("%s: a response nested too deeply to read", path, exc_info=exc)
                raise UpstreamError("The platform's response is nested too deeply to read") from exc

        responses = {**error_responses(400, 401, 403, 404, 500, 501, 502, 503, "default"),
                     **route_kwargs.pop("responses", {})}
        router.add_api_route(path, handler, methods=["GET"], summary=route_kwargs.pop("summary", None)
                             or _summary(path), dependencies=[Security(_api_key), Depends(_cache_max_age)],
                             responses=responses, **route_kwargs)
        IMPLEMENTED[path] = RouteInfo(source, notes)
        return fn

    return decorate


def mark_unsupported(reasons: dict[str, str]) -> None:
    UNSUPPORTED_REASONS.update(reasons)


def _summary(path: str) -> str | None:
    return next((e["summary"] for e in CATALOG if e["path"] == path), None)


def add_unsupported_routes(app: FastAPI) -> None:
    for entry in CATALOG:
        path = entry["path"]
        if path in IMPLEMENTED:
            continue
        reason = UNSUPPORTED_REASONS.get(path, "Not implemented: yt-dlp has no extractor for this data.")

        async def not_supported(_reason: str = reason, _path: str = path) -> JSONResponse:
            exc = NotSupported(_reason, endpoint=_path)
            return JSONResponse(exc.body(), status_code=exc.status)

        # Hide the private default args from the OpenAPI schema / query parsing.
        not_supported.__signature__ = inspect.Signature()  # type: ignore[attr-defined]
        app.add_api_route(path, not_supported, methods=["GET"], summary=f"{entry['summary']} (not supported)",
                          tags=["not supported"], include_in_schema=True, dependencies=[Security(_api_key)],
                          status_code=501, responses=error_responses(400, 401, 501))


def coverage() -> list[dict[str, Any]]:
    out = []
    for entry in CATALOG:
        path = entry["path"]
        info = IMPLEMENTED.get(path)
        out.append({
            "path": path,
            "platform": path.split("/")[2],
            "summary": entry["summary"],
            "status": "implemented" if info else "not_supported",
            "source": info.source if info else None,
            "notes": (info.notes if info else UNSUPPORTED_REASONS.get(path)),
        })
    return out
