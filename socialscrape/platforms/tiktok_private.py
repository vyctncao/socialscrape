"""TikTok mobile API routes requiring a signed-in TikTok cookie account (keyword and hashtag search: tiktok_search)."""
from __future__ import annotations

from typing import Any

import yt_dlp
from fastapi import APIRouter, Query

from .. import sessions
from ..errors import BadRequest, LoginRequired, UpstreamError
from ..registry import sc_get
from ..ytdlp import with_ydl
from . import _tiktok_map as M
from .tiktok import _APP_API, SESSION_COOKIES, _handle, _profile

router = APIRouter()
P = "tiktok"


def _signed_in(ydl: yt_dlp.YoutubeDL) -> None:
    """The pre-flight of a route that needs a signed-in account: LoginRequired (501) without a session cookie,
    before any request; else the account must pass TikTok's account check (cached, sessions.confirm). The app API
    answers a dead session exactly as it answers no cookies ("Please upgrade your TikTok app.", status 5, an empty
    list), so its answers can't show it: the check is made before the call."""
    ie = ydl.get_info_extractor("TikTok")
    if not any(name in ie._get_cookies("https://www.tiktok.com/") for name in SESSION_COOKIES):
        raise LoginRequired("Add TikTok account cookies in Platform settings to use this endpoint", platform=P)
    sessions.confirm(ydl, P)


def _app(path: str, item_id: str, query: dict[str, Any]):
    def go(ydl: yt_dlp.YoutubeDL):
        _signed_in(ydl)
        ie = ydl.get_info_extractor("TikTok")
        data = ie._call_api(path, item_id, query=query, fatal=False)
        if not isinstance(data, dict):
            raise UpstreamError("TikTok returned no API data")
        status = data.get("status_code")
        if status:
            raise UpstreamError(f"TikTok API failed: {data.get('status_msg') or data.get('message') or status}")
        return data
    return go


@sc_get(router, "/v1/tiktok/video/comments", source="TikTok mobile comment/list API with saved cookies",
        status="needs_setup", notes="Requires a signed-in TikTok cookie account configured in Platform settings.")
async def comments(url: str = Query(...), cursor: str | None = None, trim: str | None = None):
    await _require_account()
    ref = M.post_url(url)
    if not ref:
        raise BadRequest("url must be a TikTok video URL")
    aweme_id = ref[1]
    data = await with_ydl(P, _app("comment/list", aweme_id,
                                  {"aweme_id": aweme_id, "cursor": cursor or "0", "count": "20"}),
                           extractor_args=_APP_API)
    return {"comments": M.objs(data.get("comments")), "cursor": str(data.get("cursor") or "0"),
            "has_more": bool(data.get("has_more"))}


@sc_get(router, "/v1/tiktok/video/comment/replies",
        source="TikTok mobile comment/list/reply API with saved cookies",
        status="needs_setup", notes="Requires a signed-in TikTok cookie account configured in Platform settings.")
async def comment_replies(comment_id: str = Query(...), url: str = Query(...), cursor: str | None = None):
    await _require_account()
    ref = M.post_url(url)
    if not ref:
        raise BadRequest("url must be a TikTok video URL")
    aweme_id = ref[1]
    data = await with_ydl(P, _app("comment/list/reply", comment_id,
                                  {"item_id": aweme_id, "comment_id": comment_id,
                                   "cursor": cursor or "0", "count": "20"}), extractor_args=_APP_API)
    return {"comments": M.objs(data.get("comments")), "cursor": str(data.get("cursor") or "0"),
            "has_more": bool(data.get("has_more"))}


async def _require_account() -> None:
    """501 unless a usable signed-in TikTok account is configured (AccountRejected when TikTok rejected it)."""
    await with_ydl(P, _signed_in)


async def _graph(kind: str, handle: str | None, user_id: str | None, cursor: str | None):
    if not handle:
        raise BadRequest("handle is required")
    await _require_account()
    name = _handle(handle)
    prof = await with_ydl(P, lambda ydl: _profile(ydl, name))
    uid = user_id or prof["id"]
    data = await with_ydl(P, _app(f"user/{kind}/list", uid,
                                  {"user_id": uid, "sec_user_id": prof["secUid"],
                                   "max_time": cursor or "0", "count": "20"}), extractor_args=_APP_API)
    users = M.objs(data.get("followers") or data.get("followings") or data.get("user_list"))
    return {kind: users, "min_time": str(data.get("min_time") or data.get("max_time") or "0")}


@sc_get(router, "/v1/tiktok/user/followers", source="TikTok mobile user/follower/list API with saved cookies",
        status="needs_setup", notes="Requires a signed-in TikTok cookie account configured in Platform settings.")
async def followers(handle: str | None = None, user_id: str | None = None,
                    min_time: str | None = None, trim: str | None = None):
    return await _graph("followers", handle, user_id, min_time)


@sc_get(router, "/v1/tiktok/user/following", source="TikTok mobile user/following/list API with saved cookies",
        status="needs_setup", notes="Requires a signed-in TikTok cookie account configured in Platform settings.")
async def following(handle: str = Query(...), min_time: str | None = None, trim: str | None = None):
    return await _graph("followings", handle, None, min_time)


@sc_get(router, "/v1/tiktok/search/suggestions", source="TikTok mobile search/sug API with saved cookies",
        status="needs_setup", notes="Requires a signed-in TikTok cookie account configured in Platform settings.")
async def suggestions(query: str = Query(...), region: str | None = None):
    data = await with_ydl(P, _app("search/sug", query, {"keyword": query}), extractor_args=_APP_API)
    rows = []
    for index, item in enumerate(M.objs(data.get("sug_list"))):
        text = item.get("content") or item.get("word") or item.get("query")
        if text:
            rows.append({"text": text, "position": index, "score": M.to_int(item.get("score")),
                         "language": item.get("language")})
    return {"suggestions": rows}


@sc_get(router, "/v1/tiktok/get-trending-feed", source="TikTok mobile feed API with saved cookies",
        status="needs_setup", notes="Requires a signed-in TikTok cookie account configured in Platform settings.")
async def trending(region: str = Query(...), trim: str | None = None):
    data = await with_ydl(P, _app("feed", "trending", {"count": "20", "type": "0", "pull_type": "2",
                                                        "region": (region or "US").upper()}),
                           extractor_args=_APP_API)
    return {"aweme_list": M.objs(data.get("aweme_list"))}


async def _search(path: str, query: str, cursor: str | None, region: str | None, **extra: Any):
    """The app's search `path` with saved cookies. /v1/tiktok/search/keyword and /hashtag (tiktok_search) come here
    only for what TikTok's public keyword pages can't answer."""
    data = await with_ydl(P, _app(path, query, {"keyword": query, "cursor": cursor or "0", "count": "20",
                                                **extra, "region": (region or "US").upper()}),
                          extractor_args=_APP_API)
    return data


@sc_get(router, "/v1/tiktok/search/top", source="TikTok mobile search/general/full API with saved cookies",
        status="needs_setup", notes="Requires a signed-in TikTok cookie account configured in Platform settings.")
async def search_top(query: str = Query(...), publish_time: str | None = None, sort_by: str | None = None,
                     region: str | None = None, cursor: str | None = None):
    data = await _search("search/general/full", query, cursor, region)
    return {"items": M.objs(data.get("data") or data.get("items")),
            "cursor": str(data.get("cursor") or "0"), "has_more": bool(data.get("has_more"))}
