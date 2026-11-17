"""Identical requests in flight at the same time share one answer.

Kori's tracking batches can ask for the same thing several times at once: in the Nov 16 04:00 UTC batch, 82 of the
243 requests asked for a URL whose first request was still running. Each copy scraped it again (and, past its native
budget, paid ScrapeCreators again) while holding one of the extractor workers the rest of the batch was waiting for.
Now the first one runs and the copies get its answer: the same status, headers and body, each still authenticated
and recorded in request history as a request of its own.

Only GETs of the data API are shared, and only while the first one is running: a request made after it answered is
asked afresh, as every request without cache_max_age is. Requests share an answer when the path and the exact query
(repeated parameters keep their order, which cursors depend on), the caller and the headers that change the answer
(x-socialscrape-fallback, Via) are the same.
"""
from __future__ import annotations

import asyncio
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

Answer = tuple[int, dict[str, str], bytes]


def _key(request: Request) -> tuple[Any, ...] | None:
    path = request.url.path
    if request.method != "GET" or not path.startswith("/v") or path.startswith(("/v1/account", "/v1/admin")):
        return None  # account routes answer about the caller; admin routes carry secrets
    principal = getattr(request.state, "principal", None)
    return (path, request.url.query, getattr(principal, "id", None),
            request.headers.get("x-socialscrape-fallback", ""), request.headers.get("via", ""))


class CoalesceMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._running: dict[tuple[Any, ...], asyncio.Future[Answer | None]] = {}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        key = _key(request)
        if key is None:
            return await call_next(request)
        first = self._running.get(key)
        if first is not None and first.get_loop() is asyncio.get_running_loop():
            answer = await asyncio.shield(first)  # this request going away must not cancel the first one
            if answer is None:  # the first one ended without an answer (an error, its client gone): the copies ask
                return await self.dispatch(request, call_next)  # again, the first of them for the others
            status, headers, body = answer
            return Response(body, status_code=status, headers=headers)
        future: asyncio.Future[Answer | None] = asyncio.get_running_loop().create_future()
        self._running[key] = future
        answer = None
        try:
            response = await call_next(request)
            chunks = [chunk async for chunk in response.body_iterator]  # type: ignore[attr-defined]
            body = b"".join(chunk if isinstance(chunk, bytes) else chunk.encode() for chunk in chunks)
            headers = {name: value for name, value in response.headers.items() if name.lower() != "content-length"}
            answer = (response.status_code, headers, body)
            return Response(body, status_code=response.status_code, headers=headers, background=response.background)
        finally:
            if self._running.get(key) is future:
                del self._running[key]  # first: a request arriving once this one has answered is asked afresh
            future.set_result(answer)
