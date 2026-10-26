"""ScrapeCreators-compatible account usage endpoints."""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .catalog import UPSTREAM_OPERATIONS
from .portal import PortalStore, Principal

router = APIRouter(tags=["account"])


def _query_schema(path: str) -> dict:
    return {"parameters": next(op["parameters"] for op in UPSTREAM_OPERATIONS if op["path"] == path)}


def _identity(request: Request) -> tuple[PortalStore, Principal] | JSONResponse:
    store = getattr(request.app.state, "portal", None)
    principal = getattr(request.state, "principal", None)
    if store is None or principal is None:
        return JSONResponse(
            {"success": False, "error": "member_identity_required",
             "message": "Account usage requires a portal member API key; legacy keys cannot access workspace usage."},
            status_code=403,
        )
    return store, principal


@router.get("/v1/account/credit-balance", summary="Get credit balance")
async def credit_balance() -> dict[str, Any]:
    return {
        "success": True,
        "creditCount": None,
        "unlimited": True,
        "message": "SocialScrape doesn't charge credits; requests are unlimited.",
    }


@router.get("/v1/account/get-api-usage", summary="Get request history",
            openapi_extra=_query_schema("/v1/account/get-api-usage"))
async def get_api_usage(request: Request):
    identity = _identity(request)
    if isinstance(identity, JSONResponse):
        return identity
    store, principal = identity
    endpoint = request.query_params.get("endpoint")
    status_code = request.query_params.get("statusCode")
    raw_page = request.query_params.get("page", "1")
    try:
        page = int(raw_page)
        status = int(status_code) if status_code not in (None, "") else None
    except ValueError:
        return JSONResponse({"success": False, "error": "invalid_parameter",
                             "message": "page and statusCode must be integers"}, status_code=400)
    if page < 1 or page > 100:
        return JSONResponse({"success": False, "error": "invalid_parameter",
                             "message": "page must be between 1 and 100"}, status_code=400)
    return store.api_usage(principal, endpoint=endpoint, status=status, page=page)


@router.get("/v1/account/get-daily-usage-count", summary="Get daily usage")
async def get_daily_usage_count(request: Request):
    identity = _identity(request)
    if isinstance(identity, JSONResponse):
        return identity
    store, principal = identity
    return store.daily_usage(principal)


def _timestamp(value: str | None, default: float) -> float:
    if not value:
        return default
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


@router.get("/v1/account/get-most-used-routes", summary="Get most used routes",
            openapi_extra=_query_schema("/v1/account/get-most-used-routes"))
async def get_most_used_routes(request: Request):
    identity = _identity(request)
    if isinstance(identity, JSONResponse):
        return identity
    store, principal = identity
    now = time.time()
    start_time = request.query_params.get("start_time")
    end_time = request.query_params.get("end_time")
    try:
        end = _timestamp(end_time, now)
        start = _timestamp(start_time, end - 86400)
    except (ValueError, OverflowError):
        return JSONResponse({"success": False, "error": "invalid_parameter",
                             "message": "start_time and end_time must be ISO 8601 timestamps"}, status_code=400)
    if end < start or end - start > 366 * 86400:
        return JSONResponse({"success": False, "error": "invalid_parameter",
                             "message": "The time range must be ordered and no longer than one year"}, status_code=400)
    return store.most_used_routes(principal, start, end)
