"""The signed-in Instagram web client's read-only profile, feed and search queries.

Query names, document IDs and variables were read from Instagram's public web
bundles on 2026-10-26. A signed-in page is a client shell, not the logged-out
Relay profile snapshot. Its route supplies the profile ID and its DTSG token
must accompany authenticated GraphQL requests. Tokens stay in this worker.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from yt_dlp.utils import traverse_obj, urlencode_postdata

from ..errors import Forbidden, NotFound, SessionInvalid, UpstreamBlocked, UpstreamError
from . import instagram as ig

_DTSG = re.compile(r'\["DTSGInitialData",\[\],\{"token":"([^"\s]+)"')
PROFILE = ("PolarisProfilePageContentQuery", "28717443601247930")
POSTS = ("PolarisProfilePostsQuery", "29156678094015645")
REELS = ("PolarisProfileReelsTabContentQuery", "28829344613371148")
HIGHLIGHTS = ("PolarisProfileStoryHighlightsTrayContentQuery", "26970053832668570")
SEARCH = ("PolarisAPITopsearchQuery", "40560838120170065")
TAGGED = ("PolarisProfileTaggedTabContentQuery", "39800699849576270")
_PROVIDER = "__relay_internal__pv__"
_PROFILE_FLAGS = (
    "PolarisCannesGuardianExperienceEnabledrelayprovider",
    "PolarisCASB976ProfileEnabledrelayprovider",
    "PolarisProfileInformTreatmentBannerEnabledrelayprovider",
    "PolarisWebSchoolsEnabledrelayprovider",
    "PolarisProfileRingsGradientEnabledrelayprovider",
    "PolarisRepostsConsumptionEnabledrelayprovider",
    "PolarisShortDramaEnabledrelayprovider",
)
_POST_FLAGS = (
    "PolarisMultiCaptionCarouselEnabledrelayprovider",
    "PolarisShortDramaEnabledrelayprovider",
    "PolarisSEOIsCrawlerrelayprovider",
    "PolarisThreadsStyleCommentsEnabledrelayprovider",
    "PolarisReelsRecoDebugOverlayEnabledrelayprovider",
)


class PublicListingRequired(UpstreamBlocked):
    """A signed-in empty listing needs a separately verified public view."""

    code = "signed_in_listing_unavailable"

    def __init__(self, user_id: str) -> None:
        super().__init__(
            "Instagram returned no signed-in listing items for a profile with existing media"
        )
        self.user_id = user_id


def _objects(value: Any, depth: int = 0):
    if depth > 48:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child, depth + 1)


def profile_id(html: str) -> str:
    """Only the requested route's root view ID, never the viewer's account ID."""
    found = set()
    for blob in ig.InstagramIE._SJS_RE.findall(html):
        try:
            data = json.loads(blob)
        except (ValueError, RecursionError):
            continue
        for obj in _objects(data):
            value = traverse_obj(
                obj, ("initialRouteInfo", "route", "rootView", "props", "id")
            )
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                text = str(value)
                if re.fullmatch(r"[0-9]{1,20}", text):
                    found.add(text)
    if len(found) != 1:
        raise UpstreamError(
            "Instagram's signed-in page did not identify one profile route",
            code="profile_id_unavailable",
        )
    return found.pop()


class Session(ig.WebSession):
    def __init__(self, ie: Any, html: str, referer: str) -> None:
        super().__init__(ie, html, referer)
        token = _DTSG.search(html)
        if not self.lsd or not token:
            if ig.served_logged_out(html):
                # The session cookie was sent, but Instagram answered as it answers a logged-out visitor.
                raise SessionInvalid(
                    ig.PLATFORM,
                    "Instagram served its logged-out page to the signed-in session",
                )
            raise UpstreamBlocked(
                "Instagram did not return the signed-in page's request tokens"
            )
        self._dtsg = token.group(1)

    def query(
        self, name: str, doc_id: str, variables: dict[str, Any], *, retries: int = 1
    ) -> dict[str, Any]:
        cookies = self.ie._get_cookies(ig.WEB)
        csrf, viewer = cookies.get("csrftoken"), cookies.get("ds_user_id")
        form = {
            "lsd": self.lsd,
            "fb_dtsg": self._dtsg,
            "jazoest": "2" + str(sum(map(ord, self._dtsg))),
            "__a": "1",
            "__d": "www",
            "__user": "0",
            **({"av": viewer.value} if viewer else {}),
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": name,
            "server_timestamps": "true",
            "variables": json.dumps(variables, separators=(",", ":")),
            "doc_id": doc_id,
        }
        headers = {
            **self.ie._api_headers,
            "X-FB-Friendly-Name": name,
            "X-FB-LSD": self.lsd,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self.referer,
            "Content-Type": "application/x-www-form-urlencoded",
            **({"X-CSRFToken": csrf.value} if csrf else {}),
        }
        for attempt in range(retries + 1):
            body, response = self.ie._download_webpage_handle(
                ig._GRAPHQL_URL,
                name,
                data=urlencode_postdata(form),
                headers=headers,
                impersonate=self.ie._can_impersonate,
                expected_status=lambda _: True,
            )
            try:
                return ig.api_answer(
                    response.status, body, response.url, logged_in=True
                )
            except ig._NoJson:
                if attempt >= retries:
                    raise
                time.sleep(ig._RETRY_DELAY * (attempt + 1))
        raise AssertionError("unreachable")

    def profile(self, handle: str | None, user_id: str) -> dict[str, Any]:
        data = self.query(
            *PROFILE,
            {
                "id": user_id,
                "enable_integrity_filters": True,
                **{_PROVIDER + name: False for name in _PROFILE_FLAGS},
            },
        )
        user = traverse_obj(data, ("data", "user", {dict}))
        if user is None:
            if (
                isinstance(data.get("data"), dict)
                and data["data"].get("user", False) is None
            ):
                raise NotFound("Instagram profile not found")
            raise ig._graphql_failure(data, "signed-in profile")
        username = user.get("username")
        if (
            str(user.get("pk")) != user_id
            or not isinstance(username, str)
            or not ig._HANDLE.fullmatch(username)
            or handle
            and username.lower() != handle.lower()
        ):
            raise UpstreamError(
                "Instagram returned a different profile",
                code="profile_identity_mismatch",
            )
        return user

    def listing(
        self,
        user: dict[str, Any],
        tab: str,
        cursor: str | None,
        *,
        metadata: bool = False,
    ) -> tuple[list[dict], dict]:
        if tab:
            query = REELS
            variables = {
                "user_id": str(user["pk"]),
                "first": 12,
                "data": {
                    "include_feed_video": True,
                    "page_size": 12,
                    "target_user_id": str(user["pk"]),
                },
                _PROVIDER + "PolarisShortDramaEnabledrelayprovider": False,
            }
        else:
            query = POSTS
            variables = {
                "username": user["username"],
                "first": 12,
                "data": {
                    "count": 12,
                    "include_reel_media_seen_timestamp": True,
                    "include_relationship_info": True,
                    "latest_besties_reel_media": True,
                    "latest_reel_media": True,
                },
                **{
                    _PROVIDER + name: name == "PolarisSEOIsCrawlerrelayprovider"
                    for name in _POST_FLAGS
                },
            }
        if cursor:
            variables["after"] = cursor
        data = self.query(*query, variables)
        connection = (
            traverse_obj(
                data, ("data", "fetch__XDTUserDict", "clips_connection", {dict})
            )
            if tab
            else traverse_obj(
                data,
                ("data", "xdt_api__v1__feed__user_timeline_graphql_connection", {dict}),
            )
        )
        if connection is None:
            raise ig._graphql_failure(data, "signed-in profile listing")
        edges, page = connection.get("edges"), connection.get("page_info")
        if (
            not isinstance(edges, list)
            or not isinstance(page, dict)
            or not isinstance(page.get("has_next_page"), bool)
        ):
            raise UpstreamError("Instagram returned an incomplete signed-in listing")
        if page["has_next_page"] and (
            not isinstance(page.get("end_cursor"), str) or not page["end_cursor"]
        ):
            raise UpstreamError("Instagram returned a listing without its next cursor")
        nodes = []
        for edge in edges:
            node = edge.get("node") if isinstance(edge, dict) else None
            if tab:
                node = node.get("media") if isinstance(node, dict) else None
            if not isinstance(node, dict) or not node.get("pk") or not node.get("code"):
                raise UpstreamError(
                    "Instagram returned a listing item without its media identity"
                )
            nodes.append(node)
        if len({str(node["pk"]) for node in nodes}) != len(nodes):
            raise UpstreamError("Instagram returned duplicate media in one page")
        if not nodes and user.get("is_private") is True:
            if metadata:
                return nodes, page
            raise Forbidden("This account is private")
        known_count = user.get("total_clips_count" if tab else "media_count")
        if (
            not nodes
            and not cursor
            and isinstance(known_count, int)
            and known_count > 0
        ):
            raise PublicListingRequired(str(user["pk"]))
        return nodes, page

    def details(self, nodes: list[dict]) -> dict[str, dict]:
        """Reels grids omit dates/captions; read the authenticated media info."""
        result = {}
        for node in nodes:
            pk = str(node["pk"])
            if not re.fullmatch(r"[0-9]{1,20}", pk):
                raise UpstreamError("Instagram returned an invalid media ID")
            kept = ig.MEDIA.get(pk)
            if kept is not None and "taken_at" in kept and "caption" in kept:
                result[pk] = kept
                continue
            body, response = self.ie._download_webpage_handle(
                f"{ig.WEB}/api/v1/media/{pk}/info/",
                pk,
                headers=self.ie._api_headers,
                impersonate=self.ie._can_impersonate,
                expected_status=lambda _: True,
            )
            data = ig.api_answer(response.status, body, response.url, logged_in=True)
            items = data.get("items")
            if (
                not isinstance(items, list)
                or len(items) != 1
                or not isinstance(items[0], dict)
            ):
                raise UpstreamError("Instagram returned no requested media detail")
            media = items[0]
            if str(media.get("pk")) != pk or media.get("code") != node["code"]:
                raise UpstreamError("Instagram returned a different media detail")
            result[pk] = media
            ig.MEDIA.put(pk, media)
        return result

    def highlights(self, user: dict[str, Any]) -> list[dict]:
        data = self.query(*HIGHLIGHTS, {"user_id": str(user["pk"]), "first": 10})
        connection = traverse_obj(data, ("data", "highlights", {dict}))
        if connection is None or not isinstance(connection.get("edges"), list):
            raise ig._graphql_failure(data, "signed-in highlight tray")
        nodes = []
        for edge in connection["edges"]:
            node = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(node, dict) or not node.get("id"):
                raise UpstreamError(
                    "Instagram returned a highlight without its identity"
                )
            nodes.append(
                {
                    **node,
                    "id": str(node["id"]).removeprefix("highlight:"),
                    "cover_media_cropped_thumbnail_url": traverse_obj(
                        node, ("cover_media", "cropped_image_version", "url", {str})
                    ),
                }
            )
        return nodes

    def tagged(self, user_id: str, cursor: str | None) -> dict:
        user = self.profile(None, user_id)
        variables = {
            "user_id": user_id,
            "count": 12,
            "first": 12,
            _PROVIDER + "PolarisShortDramaEnabledrelayprovider": False,
            _PROVIDER + "PolarisSEOIsCrawlerrelayprovider": True,
            **({"after": cursor} if cursor else {}),
        }
        data = self.query(*TAGGED, variables)
        connection = traverse_obj(
            data, ("data", "xdt_api__v1__usertags__user_id__feed_connection", {dict})
        )
        if connection is None:
            raise ig._graphql_failure(data, "tagged posts")
        edges, page = connection.get("edges"), connection.get("page_info")
        if (
            not isinstance(edges, list)
            or not isinstance(page, dict)
            or not isinstance(page.get("has_next_page"), bool)
        ):
            raise UpstreamError("Instagram returned an incomplete tagged-post listing")
        more, next_cursor = page["has_next_page"], page.get("end_cursor")
        if more and (
            not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor
        ):
            raise UpstreamError("Instagram returned a non-advancing tagged-post cursor")
        posts = []
        for edge in edges:
            media = edge.get("node") if isinstance(edge, dict) else None
            if (
                not isinstance(media, dict)
                or not media.get("pk")
                or not media.get("code")
            ):
                raise UpstreamError(
                    "Instagram returned a tagged post without its identity"
                )
            posts.append(
                {
                    "pk": media["pk"],
                    "code": media["code"],
                    "media_type": media.get("media_type"),
                    "caption": traverse_obj(media, ("caption", "text", {str})),
                    "comment_count": ig._count("comment_count", media),
                    "like_count": ig._count("like_count", media),
                    "view_count": ig._count("view_count", media),
                    "user": media.get("user"),
                    "url": f"{ig.WEB}/p/{media['code']}/",
                }
            )
        if len({str(post["pk"]) for post in posts}) != len(posts):
            raise UpstreamError("Instagram returned duplicate tagged posts in one page")
        if not posts and user.get("is_private") is True:
            raise Forbidden("This account is private")
        return {
            "posts": posts,
            "cursor": next_cursor if more else None,
            "has_more": more,
        }

    def audio(self, audio_id: str, cursor: str | None) -> dict:
        """The web app's read-only POST, including its guarded XController envelope."""
        cookies = self.ie._get_cookies(ig.WEB)
        csrf, viewer = cookies.get("csrftoken"), cookies.get("ds_user_id")
        form = {
            "audio_cluster_id": audio_id,
            "original_sound_audio_asset_id": audio_id,
            **({"max_id": cursor} if cursor else {}),
            "fb_dtsg": self._dtsg,
            "lsd": self.lsd,
            "jazoest": "2" + str(sum(map(ord, self._dtsg))),
            "__a": "1",
            "__d": "www",
            "__user": "0",
            **({"av": viewer.value} if viewer else {}),
        }
        body, response = self.ie._download_webpage_handle(
            f"{ig.WEB}/api/v1/clips/music/",
            audio_id,
            data=urlencode_postdata(form),
            headers={
                **self.ie._api_headers,
                "X-Requested-With": "XMLHttpRequest",
                "X-FB-LSD": self.lsd,
                "Referer": self.referer,
                "Content-Type": "application/x-www-form-urlencoded",
                **({"X-CSRFToken": csrf.value} if csrf else {}),
            },
            impersonate=self.ie._can_impersonate,
            expected_status=lambda _: True,
        )
        outer = ig.api_answer(
            response.status,
            body.removeprefix("for (;;);"),
            response.url,
            logged_in=True,
        )
        if outer.get("error"):
            raise UpstreamError("Instagram rejected the audio-page query")
        payload = outer.get("payload")
        if outer.get("__ar") != 1 or not isinstance(payload, dict):
            raise UpstreamError("Instagram returned no audio-page payload")
        if payload.get("is_music_page_restricted") is True:
            raise Forbidden("This Instagram audio page is restricted")
        metadata = payload.get("metadata")
        ids = (
            traverse_obj(
                metadata,
                (
                    "music_info",
                    "music_asset_info",
                    ("audio_cluster_id", "audio_asset_id"),
                ),
            )
            or []
        )
        original = traverse_obj(metadata, ("original_sound_info", "audio_asset_id"))
        if audio_id not in {
            str(value) for value in [*ids, original] if isinstance(value, (str, int))
        }:
            raise UpstreamError("Instagram returned a different audio-page identity")
        items, paging = payload.get("items"), payload.get("paging_info")
        if (
            not isinstance(items, list)
            or not isinstance(paging, dict)
            or not isinstance(paging.get("more_available"), bool)
        ):
            raise UpstreamError("Instagram returned an incomplete audio listing")
        reels = []
        for item in items:
            media = item.get("media") if isinstance(item, dict) else None
            if (
                not isinstance(media, dict)
                or not media.get("pk")
                or not media.get("code")
            ):
                raise UpstreamError(
                    "Instagram returned audio media without its identity"
                )
            # ScrapeCreators exposes these native media objects, including the
            # separate play_count, fb_play_count and ig_play_count semantics.
            reels.append(media)
        if len({str(media["pk"]) for media in reels}) != len(reels):
            raise UpstreamError("Instagram returned duplicate audio media in one page")
        more, next_cursor = paging["more_available"], paging.get("max_id")
        if more and (
            not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor
        ):
            raise UpstreamError("Instagram returned a non-advancing audio cursor")
        return {
            "reels": reels,
            "cursor": next_cursor if more else None,
            "has_more": more,
        }


def profile(
    ie: Any,
    html: str,
    referer: str,
    handle: str,
    tab: str = "",
    *,
    highlights: bool = False,
) -> dict:
    session = Session(ie, html, referer)
    user = session.profile(handle, profile_id(html))
    if highlights:
        user["lox_highlights_connection"] = {
            "edges": [{"node": node} for node in session.highlights(user)]
        }
    else:
        nodes, page = session.listing(user, tab, None, metadata=True)
        user[ig._TAB_CONNECTION[tab]] = {
            "edges": [{"node": node} for node in nodes],
            "page_info": page,
        }
    return user


def feed(
    ie: Any, html: str, referer: str, handle: str, tab: str, cursor: str | None
) -> ig.Feed:
    session = Session(ie, html, referer)
    user = session.profile(handle, profile_id(html))
    nodes, page = session.listing(user, tab, cursor)
    details = (
        session.details(nodes) if tab else {str(node["pk"]): node for node in nodes}
    )
    return ig.Feed(user, nodes, page, details)


def search(ie: Any, html: str, referer: str, query: str, context: str) -> dict:
    session = Session(ie, html, referer)
    data = session.query(
        *SEARCH,
        {
            "data": {
                "context": context,
                "include_reel": "true",
                "query": query,
                "rank_token": str(uuid.uuid4()),
                "search_session_id": str(uuid.uuid4()),
                "search_surface": "web_top_search",
            }
        },
    )
    found = traverse_obj(
        data, ("data", "xdt_api__v1__fbsearch__topsearch_connection", {dict})
    )
    if found is None:
        raise ig._graphql_failure(data, "signed-in search results")
    if not isinstance(found.get("users"), list):
        raise UpstreamError("Instagram returned incomplete search results")
    return found
