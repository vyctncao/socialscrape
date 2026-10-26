"""SoundCloud public metadata via yt-dlp's guest client and native API cursors."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi import APIRouter, Query
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError

from ..errors import BadRequest, Forbidden, UpstreamError, require_one
from ..registry import sc_get
from ..ytdlp import with_ydl

router = APIRouter()
API = "https://api-v2.soundcloud.com/"
WEB = "https://soundcloud.com/"
_SLUG = re.compile(r"[A-Za-z0-9_-]{1,200}$")


def _path(url: str, *, track=False) -> str:
    try:
        parsed = urlsplit(url)
        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname
            not in {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 80, 443)
            or len(parts) != (2 if track else 1)
            or any(not _SLUG.fullmatch(p) for p in parts)
            or "secret_token" in parse_qs(parsed.query)
        ):
            raise ValueError
        return "/".join(parts)
    except ValueError:
        raise BadRequest(
            "url must identify a public SoundCloud " + ("track" if track else "artist")
        ) from None


def _artist(handle, url):
    require_one(handle=handle, url=url)
    name = (handle or "").strip().removeprefix("@")
    if url:
        path = _path(url)
        if name and name.lower() != path.lower():
            raise BadRequest("handle and url refer to different SoundCloud artists")
        name = path
    if not _SLUG.fullmatch(name):
        raise BadRequest("handle must be a SoundCloud artist permalink")
    return name


def _extractor(ydl):
    ie = ydl.get_info_extractor("Soundcloud")
    # Do not reuse an OAuth header cached by an unrelated extractor instance.
    ie._HEADERS = {}
    ie.initialize()
    return ie


def _resolve(ie, path, kind):
    data = ie._call_api(
        API + "resolve",
        path,
        query={"url": WEB + path},
        headers={"Accept": "application/json"},
    )
    if (
        not isinstance(data, dict)
        or data.get("kind") != kind
        or not isinstance(data.get("id"), int)
        or isinstance(data.get("id"), bool)
    ):
        raise UpstreamError("SoundCloud returned no requested " + kind + " identity")
    if data.get("sharing") == "private":
        raise Forbidden("This SoundCloud resource is private")
    if kind == "user" and str(data.get("permalink", "")).lower() != path.lower():
        raise UpstreamError("SoundCloud returned a different artist")
    return data


async def _run(fn):
    return await with_ydl(
        "soundcloud",
        lambda ydl: fn(_extractor(ydl)),
        use_cookies=False,
    )


@sc_get(
    router,
    "/v1/soundcloud/artist",
    source="yt-dlp SoundcloudIE public guest resolve API",
    notes="Unmodified public user metadata and counters from SoundCloud. No OAuth, browser cookies or media downloads.",
)
async def artist(handle: str | None = None, url: str | None = None):
    name = _artist(handle, url)
    return await _run(lambda ie: _resolve(ie, name, "user"))


@sc_get(
    router,
    "/v1/soundcloud/track",
    source="yt-dlp SoundcloudIE public guest resolve API",
    notes="Unmodified public track metadata, artist, counters and media metadata. Private sharing is refused; no audio is downloaded.",
)
async def track(url: str = Query(...)):
    path = _path(url, track=True)
    return await _run(lambda ie: _resolve(ie, path, "track"))


@sc_get(
    router,
    "/v1/soundcloud/artist/tracks",
    source="SoundCloud public user tracks API through yt-dlp guest client",
    notes="All items from each native 50-track page. The real next_href offset is returned as cursor; the client ID and API URL are not exposed as a cursor.",
)
async def artist_tracks(
    handle: str | None = None, url: str | None = None, cursor: str | None = None
):
    name = _artist(handle, url)
    if cursor is not None and (
        not cursor or len(cursor) > 2048 or any(not 33 <= ord(c) <= 126 for c in cursor)
    ):
        raise BadRequest(
            "cursor must be a SoundCloud pagination offset", code="bad_cursor"
        )

    def go(ie):
        user = _resolve(ie, name, "user")
        path = f"users/{user['id']}/tracks"
        query = {
            "limit": 50,
            "linked_partitioning": 1,
            **({"offset": cursor} if cursor else {}),
        }
        try:
            data = ie._call_api(
                API + path,
                str(user["id"]),
                query=query,
                headers={"Accept": "application/json"},
            )
        except ExtractorError as exc:
            if cursor and isinstance(exc.cause, HTTPError) and exc.cause.status == 400:
                raise BadRequest(
                    "SoundCloud rejected the pagination offset", code="bad_cursor"
                ) from exc
            raise
        if not isinstance(data, dict) or not isinstance(data.get("collection"), list):
            raise UpstreamError("SoundCloud returned no public track list")
        tracks = data["collection"]
        if any(
            not isinstance(item, dict)
            or item.get("kind") != "track"
            or not item.get("id")
            for item in tracks
        ):
            raise UpstreamError(
                "SoundCloud returned a track list without its identities"
            )
        if any(item.get("sharing") == "private" for item in tracks):
            raise Forbidden("Only public SoundCloud tracks are available")
        token = None
        if more := data.get("next_href"):
            parsed = urlsplit(more)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "api-v2.soundcloud.com"
                or parsed.path.strip("/") != path
            ):
                raise UpstreamError(
                    "SoundCloud returned a different track-list continuation"
                )
            token = (parse_qs(parsed.query).get("offset") or [None])[0]
            if not token or token == cursor:
                raise UpstreamError(
                    "SoundCloud returned a non-advancing track-list cursor"
                )
        return {"tracks": tracks, "cursor": token}

    return await _run(go)
