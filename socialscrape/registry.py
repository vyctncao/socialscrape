"""Route registration with the ScrapeCreators envelope and coverage tracking.

Platform modules declare routes like:

    router = APIRouter()

    @sc_get(router, "/v1/youtube/video", source="yt-dlp YoutubeIE")
    async def youtube_video(url: str = Query(...), language: str | None = None):
        info = await with_ydl("youtube", lambda ydl: ydl.extract_info(url, download=False))
        return {...}          # the payload; "success": true is added here

Pass ``post=True`` to register the same handler for POST. Its JSON object uses
GET's query parameter names; scalar values are normalized to query-string text.

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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import ConfigDict, Field, create_model

from .catalog import CATALOG, UPSTREAM_OPERATIONS
from .errors import NotSupported, UpstreamError, error_responses
from .responses import LenientJSONResponse
from .util import clean_param

log = logging.getLogger("socialscrape.registry")


@dataclass
class RouteInfo:
    source: str
    notes: str | None = None
    status: str = "implemented"


IMPLEMENTED: dict[str, RouteInfo] = {}
UNSUPPORTED_REASONS: dict[str, str] = {}

_POST_SCHEMAS = {
    "/v1/facebook/adLibrary/search/ads": {
        "query": ("string", "Keyword to search for", None),
        "sort_by": ("string", "Sort by impressions or most recent.", ["total_impressions", "relevancy_monthly_grouped"]),
        "search_type": ("string", "Search by exact phrase or not.", ["keyword_unordered", "keyword_exact_phrase"]),
        "ad_type": ("string", "Search all ads or political and issue ads.", ["all", "political_and_issue_ads"]),
        "country": ("string", "Two-letter country code, or ALL.", None),
        "language": ("string", "Two-letter language code.", None),
        "status": ("string", "Ad status.", ["ALL", "ACTIVE", "INACTIVE"]),
        "media_type": ("string", "Ad media type.", ["ALL", "IMAGE", "VIDEO", "MEME", "IMAGE_AND_MEME", "NONE"]),
        "start_date": ("string", "Impressions start date in YYYY-MM-DD format.", None),
        "end_date": ("string", "Impressions end date in YYYY-MM-DD format.", None),
        "trim": ("boolean", "Return a trimmed response.", None),
        "cursor": ("string", "Cursor to paginate through results.", None),
    },
    "/v1/facebook/adLibrary/company/ads": {
        "pageId": ("string", "Ad Library page id; use this or companyName.", None),
        "companyName": ("string", "Company name; use this or pageId.", None),
        "country": ("string", "Two-letter country code, or ALL.", None),
        "status": ("string", "Ad status.", ["ALL", "ACTIVE", "INACTIVE"]),
        "media_type": ("string", "Ad media type.", ["ALL", "IMAGE", "VIDEO", "MEME", "IMAGE_AND_MEME", "NONE"]),
        "language": ("string", "Two-letter language code.", None),
        "sort_by": ("string", "Sort by impressions or most recent.", ["total_impressions", "relevancy_monthly_grouped"]),
        "start_date": ("string", "Start date in YYYY-MM-DD format.", None),
        "end_date": ("string", "End date in YYYY-MM-DD format.", None),
        "trim": ("boolean", "Return a trimmed response.", None),
        "cursor": ("string", "Cursor to paginate through results.", None),
    },
}


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


def _entry(path: str) -> dict[str, Any]:
    return next((entry for entry in CATALOG if entry["path"] == path), {})


def _body_model(path: str, fn: Callable[..., Any]) -> type:
    entry = _entry(path)
    signature = inspect.signature(fn)
    fields: dict[str, tuple[Any, Any]] = {}
    for name in entry.get("params", []):
        if name == "cache_max_age":
            continue
        parameter = signature.parameters.get(name)
        query = parameter.default if parameter else None
        kind, description, enum = _POST_SCHEMAS.get(path, {}).get(
            name, ("string", getattr(query, "description", None), None))
        default = ... if name in entry.get("required", []) else None
        schema = {"type": kind, **({"enum": enum} if enum else {})}
        fields[name] = (Any, Field(default, description=description, json_schema_extra=schema))
    return create_model(f"{fn.__name__.title().replace('_', '')}Body",
                        __config__=ConfigDict(extra="ignore"), **fields)


def _body_value(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError


def sc_get(router: APIRouter, path: str, *, source: str, notes: str | None = None, post: bool = False,
           status: str = "implemented", **route_kwargs: Any):
    """Register GET and, with ``post=True``, equivalent JSON-object POST routes."""

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
        summary = route_kwargs.pop("summary", None) or _summary(path)
        common = {"summary": summary, "tags": [_entry(path).get("tag", path.split("/")[2])],
                  "responses": responses, **route_kwargs}
        router.add_api_route(path, handler, methods=["GET"],
                             dependencies=[Security(_api_key), Depends(_cache_max_age)], **common)
        if post:
            model = _body_model(path, fn)

            async def post_handler(body: Any) -> JSONResponse:
                values = body.model_dump(exclude_unset=True)
                try:
                    kwargs = {name: _body_value(value) for name, value in values.items()}
                except ValueError as exc:
                    raise RequestValidationError([{"type": "string_type", "loc": ("body",),
                                                   "msg": "Input should be a valid string", "input": values}]) from exc
                for name, parameter in inspect.signature(fn).parameters.items():
                    if name not in kwargs and parameter.default is not inspect.Parameter.empty:
                        default = parameter.default
                        kwargs[name] = getattr(default, "default", default)
                return await handler(**kwargs)

            post_handler.__name__ = f"{fn.__name__}_post"
            post_handler.__signature__ = inspect.Signature([inspect.Parameter(
                "body", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=model)])  # type: ignore[attr-defined]
            router.add_api_route(path, post_handler, methods=["POST"], dependencies=[Security(_api_key)], **common)
        IMPLEMENTED[path] = RouteInfo(source, notes, status)
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
        reason = UNSUPPORTED_REASONS.get(path, "No native implementation is available for this endpoint yet.")

        async def not_supported(_reason: str = reason, _path: str = path) -> JSONResponse:
            exc = NotSupported(_reason, endpoint=_path)
            return JSONResponse(exc.body(), status_code=exc.status)

        # Hide the private default args from the OpenAPI schema / query parsing.
        not_supported.__signature__ = inspect.Signature()  # type: ignore[attr-defined]
        for method in entry.get("methods", ["GET"]):
            operation = next((op for op in UPSTREAM_OPERATIONS if op["path"] == path and op["method"] == method), {})
            parameters = operation.get("parameters", [])
            extra = {"x-socialscrape-status": "not_supported",
                     "parameters": [p for p in parameters if p.get("in") == "query" and p["name"] != "cache_max_age"]}
            body = [p for p in parameters if p.get("in") == "body"]
            if body:
                extra["requestBody"] = {"required": any(p.get("required") for p in body), "content": {"application/json": {
                    "schema": {"type": "object", "properties": {p["name"]: p.get("schema", {}) for p in body},
                               "required": [p["name"] for p in body if p.get("required")]}}}}
            app.add_api_route(path, not_supported, methods=[method],
                              summary=f"{entry['summary']} (not supported)", tags=[entry["tag"]],
                              openapi_extra=extra, include_in_schema=True,
                              dependencies=[Security(_api_key), Depends(_cache_max_age)], status_code=501,
                              responses={200: {"description": "Configured fallback response; this endpoint has no native implementation."},
                                         **error_responses(400, 401, 403, 404, 500, 501, 502, 503, "default")})


def coverage() -> list[dict[str, Any]]:
    out = []
    for entry in CATALOG:
        path = entry["path"]
        info = IMPLEMENTED.get(path)
        out.append({
            "path": path,
            "platform": path.split("/")[2],
            "summary": entry["summary"],
            "tag": entry["tag"],
            "methods": entry.get("methods", ["GET"]),
            "featured": entry.get("featured", False),
            "status": info.status if info else "not_supported",
            "source": info.source if info else None,
            "notes": (info.notes if info else UNSUPPORTED_REASONS.get(path, "No native implementation is available for this endpoint yet.")),
        })
    return out
