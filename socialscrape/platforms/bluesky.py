"""Public Bluesky AppView XRPC, preserving the ScrapeCreators response shapes.

No user credentials, paid-provider requests, or media downloads are involved.
The API origin and method names are fixed; user input is sent as query values.
"""

from __future__ import annotations

import json
import re
from time import monotonic
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

import yt_dlp
from fastapi import APIRouter, Query
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError, TransportError

from ..config import get_settings
from ..errors import BadRequest, Forbidden, NotFound, UpstreamBlocked, UpstreamError
from ..registry import sc_get
from ..ytdlp import with_ydl

router = APIRouter()
ORIGIN = "https://public.api.bsky.app/xrpc/"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_HANDLE = re.compile(
    r"(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]*[a-z0-9]$", re.I
)
_DID = re.compile(r"did:[a-z0-9]+:[A-Za-z0-9._:%-]+$")
_RECORD = re.compile(r"[A-Za-z0-9._~:-]{1,512}$")


def actor(value: str | None) -> str:
    value = (value or "").strip().removeprefix("@")
    if _DID.fullmatch(value):
        return value
    if _HANDLE.fullmatch(value):
        return value.lower()
    raise BadRequest("handle must be a Bluesky handle or DID")


def _read_public(ydl: yt_dlp.YoutubeDL, request: Request) -> bytes:
    settings = get_settings()
    deadline = monotonic() + settings.request_timeout
    for attempt in range(2):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise UpstreamError("Timed out waiting for Bluesky", code="timeout")
        request.extensions["timeout"] = min(settings.socket_timeout, remaining)
        try:
            with ydl.urlopen(request) as response:
                return response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            if attempt or exc.status not in (500, 502, 503, 504):
                raise
            exc.response.close()
        except TransportError:
            if attempt:
                raise
    raise AssertionError("unreachable")


def _json(ydl: yt_dlp.YoutubeDL, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        ORIGIN + method + "?" + urlencode(params),
        headers={"Accept": "application/json"},
    )
    try:
        raw = _read_public(ydl, request)
    except HTTPError as exc:
        if exc.status == 429:
            raise UpstreamBlocked("Bluesky is rate-limiting this connection") from exc
        if exc.status == 404:
            raise NotFound("Bluesky resource not found") from exc
        if exc.status in (401, 403):
            raise Forbidden(
                "Bluesky does not expose this resource to anonymous requests"
            ) from exc
        if exc.status == 400:
            # AppView uses InvalidRequest/400 for a well-formed missing actor.
            try:
                error = json.loads(exc.response.read(65536))
            except (ValueError, OSError):
                error = {}
            if isinstance(error, dict) and (
                error.get("error") in {"ProfileNotFound", "NotFound"}
                or "not found" in str(error.get("message", "")).lower()
            ):
                raise NotFound("Bluesky resource not found") from exc
            raise BadRequest("Bluesky rejected the handle, post URI or cursor") from exc
        raise
    if len(raw) > MAX_RESPONSE_BYTES:
        raise UpstreamError("Bluesky response exceeded the size limit")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise UpstreamError("Bluesky returned an invalid JSON response") from exc
    if not isinstance(data, dict) or data.get("error"):
        raise UpstreamError("Bluesky returned no usable resource data")
    return data


@sc_get(
    router,
    "/v1/bluesky/profile",
    source="Bluesky public AppView app.bsky.actor.getProfile",
    notes="Public profile metadata from AppView; no account login or paid provider.",
)
async def profile(handle: str = Query(...)):
    name = actor(handle)

    def go(ydl):
        data = _json(ydl, "app.bsky.actor.getProfile", {"actor": name})
        if not isinstance(data.get("did"), str) or not isinstance(
            data.get("handle"), str
        ):
            raise UpstreamError("Bluesky returned a profile without its identity")
        if (name.startswith("did:") and data["did"] != name) or (
            not name.startswith("did:") and data["handle"].lower() != name
        ):
            raise UpstreamError("Bluesky returned a different profile")
        return data

    return await with_ydl("bluesky", go, use_cookies=False)


@sc_get(
    router,
    "/v1/bluesky/user/posts",
    source="Bluesky public AppView app.bsky.feed.getAuthorFeed",
    notes="Real AppView cursors; each feed wrapper is flattened to its original post object, including reposts.",
)
async def posts(
    handle: str | None = None, user_id: str | None = None, cursor: str | None = None
):
    name = actor(user_id or handle)

    def go(ydl):
        params = {"actor": name, "limit": 50, **({"cursor": cursor} if cursor else {})}
        data = _json(ydl, "app.bsky.feed.getAuthorFeed", params)
        items = data.get("feed")
        if not isinstance(items, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("post"), dict)
            or not item["post"].get("uri")
            for item in items
        ):
            raise UpstreamError("Bluesky returned a feed without its post identities")
        return {"feed": [item["post"] for item in items], "cursor": data.get("cursor")}

    return await with_ydl("bluesky", go, use_cookies=False)


def post_ref(value: str) -> tuple[str, str]:
    try:
        url = urlsplit(value)
        parts = [unquote(p) for p in url.path.strip("/").split("/")]
        if (
            url.scheme not in ("http", "https")
            or url.hostname != "bsky.app"
            or url.username
            or url.password
            or url.port not in (None, 80, 443)
            or len(parts) != 4
            or parts[0] != "profile"
            or parts[2] != "post"
            or not _RECORD.fullmatch(parts[3])
        ):
            raise ValueError
        return actor(parts[1]), parts[3]
    except ValueError:
        raise BadRequest("url must be a Bluesky post URL") from None


@sc_get(
    router,
    "/v1/bluesky/post",
    source="Bluesky public AppView app.bsky.feed.getPostThread",
    notes="Original post and public reply tree, using the resolved DID and exact record key.",
)
async def post(url: str = Query(...)):
    name, key = post_ref(url)

    def go(ydl):
        if name.startswith("did:"):
            did = name
        else:
            resolved = _json(
                ydl, "com.atproto.identity.resolveHandle", {"handle": name}
            )
            did = resolved.get("did")
            if not isinstance(did, str) or not _DID.fullmatch(did):
                raise UpstreamError("Bluesky did not resolve the requested handle")
        uri = f"at://{did}/app.bsky.feed.post/{key}"
        data = _json(
            ydl,
            "app.bsky.feed.getPostThread",
            {"uri": uri, "depth": 6, "parentHeight": 0},
        )
        thread = data.get("thread")
        if not isinstance(thread, dict):
            raise UpstreamError("Bluesky returned no post thread")
        kind = str(thread.get("$type", ""))
        if kind.endswith("#notFoundPost"):
            raise NotFound("Bluesky post not found")
        if kind.endswith("#blockedPost"):
            raise Forbidden("Bluesky does not expose this post to anonymous requests")
        item = thread.get("post")
        if not isinstance(item, dict) or item.get("uri") != uri:
            raise UpstreamError("Bluesky returned a different post")
        return {"post": item, "replies": thread.get("replies", [])}

    return await with_ydl("bluesky", go, use_cookies=False)
