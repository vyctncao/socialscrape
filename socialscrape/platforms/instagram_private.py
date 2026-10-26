"""Instagram mobile/web API routes that require a saved Instagram cookie account."""
from __future__ import annotations

import re
from typing import Any

import yt_dlp
from fastapi import APIRouter, Query
from yt_dlp.extractor.instagram import InstagramIE
from yt_dlp.utils import traverse_obj

from .. import sessions
from ..errors import BadRequest, LoginRequired, require_one
from ..registry import sc_get
from ..util import compact
from ..ytdlp import with_ydl
from .instagram import WEB, _api_json, map_comment

router = APIRouter()
P = "instagram"


def _require_login(ydl: yt_dlp.YoutubeDL, ie: Any) -> None:
    """The pre-flight of a route that needs a signed-in account: LoginRequired (501) without a session cookie,
    before any request; else the account must pass Instagram's account check (cached, sessions.confirm). A session
    Instagram restricts still passes: its signed-in pages work, so only the calls it refuses fail."""
    if not ie._is_logged_in:
        raise LoginRequired("Add Instagram account cookies in Platform settings to use this endpoint", platform=P)
    sessions.confirm(ydl, P)


def _mobile(path: str, item_id: str, query: dict[str, Any] | None = None):
    def go(ydl: yt_dlp.YoutubeDL):
        ie = ydl.get_info_extractor("Instagram")
        _require_login(ydl, ie)
        return _api_json(ydl, ie, f"{ie._API_BASE_URL}/{path.lstrip('/')}", item_id,
                         f"Downloading Instagram {item_id}", query=query or {})
    return go


@sc_get(router, "/v1/instagram/basic-profile", source="Instagram signed-in profile GraphQL query by numeric ID",
        status="needs_setup", notes="Requires a signed-in Instagram cookie account configured in Platform settings.")
async def basic_profile(userId: str | None = None) -> dict[str, Any]:
    require_one(userId=userId)
    if not re.fullmatch(r"[0-9]{1,20}", userId):
        raise BadRequest("userId must be an Instagram numeric user ID")

    def go(ydl: yt_dlp.YoutubeDL):
        ie = ydl.get_info_extractor("Instagram")
        _require_login(ydl, ie)
        from . import instagram_authenticated
        from .instagram import _check_page, _page_flow

        html, final_url = _page_flow("Instagram", f"{WEB}/", "profile", allow_home=True)(ydl)
        _check_page(html, final_url, "Profile", allow_home=True)
        return instagram_authenticated.Session(ie, html, final_url).profile(None, userId)

    return await with_ydl(P, go)


async def topsearch(query: str, context: str) -> Any:
    """Current signed-in GraphQL search (context user or blended); 501 without saved account cookies."""
    def go(ydl: yt_dlp.YoutubeDL):
        ie = ydl.get_info_extractor("Instagram")
        _require_login(ydl, ie)
        from . import instagram_authenticated
        from .instagram import _check_page, _page_flow

        html, final_url = _page_flow("Instagram", f"{WEB}/", "search", allow_home=True)(ydl)
        _check_page(html, final_url, "Search", allow_home=True)
        return instagram_authenticated.search(ie, html, final_url, query, context)
    return await with_ydl(P, go)


@sc_get(router, "/v1/instagram/search/profiles", source="Instagram signed-in PolarisAPITopsearchQuery (users)",
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
        source="Instagram signed-in tagged-post GraphQL query with native cursors",
        status="needs_setup", notes="Requires a signed-in Instagram cookie account configured in Platform settings.")
async def tagged_posts(user_id: str = Query(...), cursor: str | None = None):
    if not re.fullmatch(r"[0-9]{1,20}", user_id):
        raise BadRequest("user_id must be an Instagram numeric user ID")

    def go(ydl: yt_dlp.YoutubeDL):
        ie = ydl.get_info_extractor("Instagram")
        _require_login(ydl, ie)
        from . import instagram_authenticated
        from .instagram import _check_page, _page_flow

        html, final_url = _page_flow("Instagram", f"{WEB}/", "tagged", allow_home=True)(ydl)
        _check_page(html, final_url, "Profile", allow_home=True)
        return instagram_authenticated.Session(ie, html, final_url).tagged(user_id, cursor)

    return await with_ydl(P, go)


@sc_get(router, "/v1/instagram/audio/reels", source="Instagram signed-in audio-page POST with native media and cursors",
        status="needs_setup", notes="Requires a signed-in Instagram cookie account configured in Platform settings.")
async def audio_reels(audio_id: str = Query(...), cursor: str | None = None):
    if not re.fullmatch(r"[0-9]{1,20}", audio_id):
        raise BadRequest("audio_id must be an Instagram numeric audio ID")

    def go(ydl: yt_dlp.YoutubeDL):
        ie = ydl.get_info_extractor("Instagram")
        _require_login(ydl, ie)
        from . import instagram_authenticated
        from .instagram import _check_page, _page_flow

        html, final_url = _page_flow("Instagram", f"{WEB}/reels/audio/{audio_id}/", audio_id)(ydl)
        _check_page(html, final_url, "Audio")
        return instagram_authenticated.Session(ie, html, final_url).audio(audio_id, cursor)

    return await with_ydl(P, go)
