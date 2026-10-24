"""Instagram mobile/web API routes that require a saved Instagram cookie account."""
from __future__ import annotations

from typing import Any

import yt_dlp
from fastapi import APIRouter, Query
from yt_dlp.extractor.instagram import InstagramIE
from yt_dlp.utils import traverse_obj

from ..errors import NotFound, NotSupported, require_one
from ..registry import sc_get
from ..util import compact
from ..ytdlp import with_ydl
from .instagram import WEB, _api_json, map_comment, map_hashtag_post

router = APIRouter()
P = "instagram"


def _require_login(ie: Any) -> None:
    if not ie._is_logged_in:
        raise NotSupported("Add Instagram account cookies in Platform settings to use this endpoint",
                           code="platform_auth_required", platform=P)


def _mobile(path: str, item_id: str, query: dict[str, Any] | None = None):
    def go(ydl: yt_dlp.YoutubeDL):
        ie = ydl.get_info_extractor("Instagram")
        _require_login(ie)
        return _api_json(ydl, ie, f"{ie._API_BASE_URL}/{path.lstrip('/')}", item_id,
                         f"Downloading Instagram {item_id}", query=query or {})
    return go


@sc_get(router, "/v1/instagram/basic-profile", source="Instagram mobile users/<id>/info API with saved cookies",
        status="needs_setup", notes="Requires a signed-in Instagram cookie account configured in Platform settings.")
async def basic_profile(userId: str | None = None) -> dict[str, Any]:
    require_one(userId=userId)
    data = await with_ydl(P, _mobile(f"users/{userId}/info/", userId))
    user = traverse_obj(data, ("user", {dict}))
    if not user:
        raise NotFound("Instagram user not found")
    return user


async def topsearch(query: str, context: str) -> Any:
    """Instagram's web top search (context 'user' or 'blended': users, hashtags and places). Logged-out clients have
    no search: the call answers them with a 404 page, so it needs a saved cookie account (501 without one)."""
    def go(ydl: yt_dlp.YoutubeDL):
        ie = ydl.get_info_extractor("Instagram")
        _require_login(ie)
        return _api_json(ydl, ie, f"{WEB}/web/search/topsearch/", query, "Searching Instagram",
                         query={"context": context, "include_reel": "true", "query": query})
    return await with_ydl(P, go)


@sc_get(router, "/v1/instagram/search/profiles", source="Instagram web top-search API with saved cookies",
        status="needs_setup", notes="Requires a signed-in Instagram cookie account configured in Platform settings.")
async def search_profiles(query: str = Query(..., min_length=1)) -> dict[str, Any]:
    data = await topsearch(query, "user")
    profiles = []
    for item in traverse_obj(data, ("users", ..., {dict})):
        user = item.get("user") if isinstance(item.get("user"), dict) else item
        username = user.get("username")
        if username:
            profiles.append(compact({"id": str(user.get("pk") or user.get("id") or ""),
                                     "username": username, "full_name": user.get("full_name"),
                                     "is_verified": user.get("is_verified"),
                                     "profile_pic_url": user.get("profile_pic_url"),
                                     "matched_from": "instagram_search",
                                     "url": f"{WEB}/{username}/", "bio_links": user.get("bio_links") or []}))
    return {"query": query, "profiles": profiles, "cursor": None}


@sc_get(router, "/v1/instagram/post/comment/replies",
        source="Instagram mobile media/<pk>/comments/<id>/child_comments API with saved cookies",
        status="needs_setup", notes="Requires a signed-in Instagram cookie account configured in Platform settings.")
async def comment_replies(url: str = Query(...), comment_id: str = Query(...), cursor: str | None = None):
    code = InstagramIE._match_id(url)
    from yt_dlp.extractor.instagram import _id_to_pk
    media_pk = _id_to_pk(code)
    query = {"max_id": cursor} if cursor else {}
    data = await with_ydl(P, _mobile(f"media/{media_pk}/comments/{comment_id}/child_comments/", comment_id, query))
    comments = [map_comment(item) for item in traverse_obj(data, ("child_comments", ..., {dict}))]
    return {"comments": comments, "cursor": data.get("next_max_child_cursor"),
            "has_more": bool(data.get("has_more_tail_child_comments"))}


@sc_get(router, "/v1/instagram/user/tagged-posts",
        source="Instagram mobile feed/usertags/<id> API with saved cookies",
        status="needs_setup", notes="Requires a signed-in Instagram cookie account configured in Platform settings.")
async def tagged_posts(user_id: str = Query(...), cursor: str | None = None):
    data = await with_ydl(P, _mobile(f"feed/usertags/{user_id}/", user_id,
                                     {"max_id": cursor} if cursor else None))
    return {"posts": [map_hashtag_post(item) for item in traverse_obj(data, ("items", ..., {dict}))],
            "cursor": data.get("next_max_id")}


@sc_get(router, "/v1/instagram/audio/reels", source="Instagram mobile clips/music API with saved cookies",
        status="needs_setup", notes="Requires a signed-in Instagram cookie account configured in Platform settings.")
async def audio_reels(audio_id: str = Query(...), cursor: str | None = None):
    query = {"audio_cluster_id": audio_id, "original_sound_audio_asset_id": audio_id}
    if cursor:
        query["max_id"] = cursor
    data = await with_ydl(P, _mobile("clips/music/", audio_id, query))
    return {"reels": [map_hashtag_post(item) for item in traverse_obj(data, ("items", ..., {dict}))],
            "cursor": data.get("next_max_id")}
