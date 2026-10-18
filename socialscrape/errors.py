"""API errors. Status codes follow ScrapeCreators where it defines one
(400/401/403/404/500) and use 405/501/502/503 for cases it has no code for."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    status = 500
    code = "server_error"

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None, **details: Any):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        self.details = details

    def body(self) -> dict[str, Any]:
        return {"success": False, "error": self.code, "message": self.message, **self.details}


class BadRequest(ApiError):
    status = 400
    code = "bad_request"


class Unauthorized(ApiError):
    status = 401
    code = "unauthorized"


class Forbidden(ApiError):
    """Content exists but is private, login-walled, age-gated or geo-blocked."""

    status = 403
    code = "forbidden"


class NotFound(ApiError):
    status = 404
    code = "not_found"


class UpstreamError(ApiError):
    """The platform or yt-dlp failed in a way we could not classify. Retryable."""

    status = 500
    code = "upstream_error"


class Misconfigured(ApiError):
    """A server setting (cookie file, proxy, Whisper backend) can't be used. Not the caller's fault."""

    status = 500
    code = "config_error"


class UpstreamUnreachable(ApiError):
    """No HTTP answer from the platform: connection refused/reset, DNS, TLS or the proxy. Retryable."""

    status = 502
    code = "network_error"


class NotSupported(ApiError):
    """Endpoint (or a feature of it) has no yt-dlp-backed implementation."""

    status = 501
    code = "not_supported"


class UpstreamBlocked(ApiError):
    """Rate limited or bot-checked by the platform. Retry later or via a proxy/cookies."""

    status = 503
    code = "upstream_blocked"


class ErrorResponse(BaseModel):  # ApiError.body(), for the OpenAPI spec (the docstring is its description)
    """Every error response. Some add fields, e.g. a 501's endpoint."""

    model_config = ConfigDict(extra="allow")
    success: Literal[False]
    error: str = Field(description="Error code: bad_request, unauthorized, not_found, timeout, not_supported, ...")
    message: str = Field(description="What went wrong (a missing or bad param is named)")


_ERROR_DOCS: dict[int | str, str] = {
    400: "Bad or missing params (the message names the param), a URL for the wrong platform, a bad cursor or "
         "cache_max_age",
    401: "Bad or missing x-api-key (when the server has API keys)",
    403: "Private, login-gated, age-restricted or geo-blocked content",
    404: "Nonexistent or removed content",
    500: "Unclassified upstream error or timeout, an unusable server setting, or an internal error",
    501: "Not supported here (the message says why): the endpoint, a param value, or AI transcription",
    502: "No answer from the platform: the proxy or the connection failed",
    503: "The platform rate-limited or bot-checked the server",
    "default": "Any other error, in the same envelope",
}


def error_responses(*statuses: int | str) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses` for these statuses, each with the error envelope. A route without any 4XX/default entry
    gets FastAPI's 422 HTTPValidationError, which is never sent: validation errors are 400s in this envelope."""
    return {s: {"model": ErrorResponse, "description": _ERROR_DOCS[s]} for s in statuses}


def require_one(**params: Any) -> None:
    """Raise 400 unless at least one of the given params is non-empty."""
    if not any(v not in (None, "") for v in params.values()):
        raise BadRequest(f"Provide one of: {', '.join(params)}")


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.status)


# First element of a validation error's loc: where the parameter came from, not its name.
_PARAM_LOCATIONS = {"query", "path", "header", "cookie", "body"}


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    problems = []
    for err in exc.errors():
        parts = tuple(err.get("loc", ()))
        if parts and parts[0] in _PARAM_LOCATIONS:
            parts = parts[1:]  # ("query", "query") is the param named query
        loc = ".".join(str(p) for p in parts)
        problems.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return JSONResponse(BadRequest("; ".join(problems) or "Invalid parameters").body(), status_code=400)


_HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed"}


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Router-level errors (unknown path, wrong method) in the same envelope as ours. Keeps `Allow` on 405."""
    message = str(exc.detail)
    headers = exc.headers
    if exc.status_code == 404:
        message = f"No endpoint at {request.url.path}; GET /meta/coverage lists them"
    elif exc.status_code == 405 and headers and "Allow" in headers:
        headers = {**headers, "Allow": ", ".join(sorted(headers["Allow"].split(", ")))}  # Starlette's is set order
        message = f"Method {request.method} not allowed here; use {headers['Allow']}"
    err = ApiError(message, status=exc.status_code, code=_HTTP_ERROR_CODES.get(exc.status_code, "http_error"))
    return JSONResponse(err.body(), status_code=err.status, headers=headers)


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Anything that isn't an ApiError (a bug or a misconfiguration). Starlette re-raises it after this
    response is sent, so the server still logs the traceback."""
    return JSONResponse(ApiError("Internal server error").body(), status_code=500)
