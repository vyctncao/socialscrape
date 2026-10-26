"""TikTok keyword and hashtag search, logged out, from TikTok's public keyword pages (tiktok.com/discover/<keyword>).

TikTok's own search answers an anonymous request with an empty HTTP 200 body, cookies or not: the web app's
/api/search/item/full/ and /api/challenge/item_list/ want its X-Bogus/X-Gnarly request signatures (its JS SDK), the app
API's search/item, general/search/single and challenge/aweme (TikTokTagIE's, marked broken in yt-dlp) its X-Gorgon/
X-Argus device signatures. The /tag/<name> page carries no posts (the copy crawlers get has only the challenge id).

The keyword pages are the one keyword-indexed video list TikTok serves unsigned: their web app asks

  /api/seo/kap/video_list/?keyword=<slug>&pageType=7&count=..&offset=..&region=..&cacheSessionId=..

with `needSign: false`, and it answers without cookies: {statusCode, videoList, hasMore, cacheSessionId}. videoList
holds web itemStructs, as /api/creator/item_list/ does, so `_tiktok_map.aweme` re-keys them to the app shape: counts
are TikTok's web rounding (1600000), the author's follower count comes from authorStats, music carries its play URL.
The ranking is TikTok's for that page and region; ~60-90 posts per keyword, then hasMore is false.

Only keywords TikTok made a page for have one: travel, cooking, booktok, how-to-make-pasta, skincare-routine-for-oily-
skin, but not fitness or skincare. For the others TikTok answers status_code 4 ("Server is currently unavailable"),
which stays a 501 platform_auth_required: only the signed-in app API (tiktok_private) could search them.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable

import yt_dlp
from fastapi import APIRouter, Query
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError

from ..errors import AccountRejected, ApiError, BadRequest, NotSupported, UpstreamBlocked, UpstreamError
from ..registry import sc_get
from ..ytdlp import with_ydl
from . import _tiktok_map as M
from . import tiktok_private
from .tiktok import _ID, _REGION, _offset, _status_error

router = APIRouter()
P = "tiktok"

_KAP = "https://www.tiktok.com/api/seo/kap/video_list/"
_KAP_QUERY = {
    "pageType": 7,  # SEO_PAGE_TYPE_DISCOVER in TikTok's web bundle
    "appId": 1233,  # kap.init.config on a /discover page (musical_ly)
    "aid": 1988,  # tiktok_web
    "trafficType": 0,  # not a search-engine crawler
}
_PAGE = 30  # posts per request; the page's own app asks 16, crawlers 72
_NO_PAGE = 4  # status_code for a keyword TikTok has no page for (status_msg "Server is currently unavailable")
_TRIES = 2  # an empty body, a challenge page or HTTP 403/429/5xx is asked once more before it is a 503
_RETRY_DELAY = 1.5
_HOPS = 3  # pages read per hashtag request while none of them has a post with the tag
_SESSION_TTL = 600.0  # s a keyword's cacheSessionId is reused, so its later pages continue the same ranking
_SESSION_MAX = 512
_DATE_POSTED = ("yesterday", "this-week", "this-month", "last-3-months", "last-6-months", "all-time")
_SORT_BY = ("relevance", "most-liked", "date-posted")
# The signed-in app API's own values for them (search/item: publish_time in days, sort_type)
_PUBLISH_TIME = {"yesterday": 1, "this-week": 7, "this-month": 30, "last-3-months": 90, "last-6-months": 180,
                 "all-time": 0}
_SORT_TYPE = {"relevance": 0, "most-liked": 1, "date-posted": 2}
_sessions: dict[tuple[str, str], tuple[float, str]] = {}
_sessions_lock = threading.Lock()


class NoKeywordPage(ApiError):
    """TikTok has no public keyword page for this keyword (status_code 4)."""

    status = 501
    code = "platform_auth_required"


# --- params ------------------------------------------------------------------

def slug(text: str | None) -> str:
    """A keyword as TikTok's keyword pages spell it: lower case, words joined by '-', apostrophes dropped.

    'Skincare routine for oily skin' -> 'skincare-routine-for-oily-skin', "What's in my bag" -> 'whats-in-my-bag';
    letters of any script stay ('旅行'). TikTok answers the spaced or apostrophed form with status_code 4.
    """
    text = re.sub(r"['‘’`]", "", (text or "").strip().lower())
    return re.sub(r"[\W_]+", "-", text).strip("-")


def _region(value: str | None) -> str:
    region = (value or "US").strip().upper()
    if not _REGION.fullmatch(region):
        raise BadRequest("region must be a two-letter country code")
    return region


def _choice(name: str, value: str | None, choices: tuple[str, ...], default: str) -> str:
    value = (value or "").strip() or default
    if value not in choices:
        raise BadRequest(f"{name} must be one of: {', '.join(choices)}")
    return value


def hashtags(item: dict[str, Any]) -> set[str]:
    """The hashtags a web item carries, lower case: its textExtra hashtag names and its challenges' titles."""
    names = [e.get("hashtagName") for e in M.objs(item.get("textExtra"))]
    names += [c.get("title") for c in M.objs(item.get("challenges"))]
    return {n.lower() for n in names if isinstance(n, str) and n}


# --- keyword-page requests (run inside with_ydl) ----------------------------------

def _session(key: tuple[str, str], value: str | None = None) -> str | None:
    """The cacheSessionId kept for (slug, region); with `value`, keep that one."""
    now = time.monotonic()
    with _sessions_lock:
        if value is None:
            hit = _sessions.get(key)
            return hit[1] if hit and hit[0] > now else None
        _sessions.pop(key, None)
        while len(_sessions) >= _SESSION_MAX:
            _sessions.pop(next(iter(_sessions)))
        _sessions[key] = (now + _SESSION_TTL, value)
    return value


def _kap_json(ie: Any, keyword: str, region: str, offset: int, session: str | None) -> dict[str, Any]:
    """One /api/seo/kap/video_list/ answer.

    TikTok throttles with an empty HTTP 200 body, a WAF challenge page, a cut-off body that isn't JSON or an HTTP
    403/429/5xx: each is asked once more, _RETRY_DELAY later, then is a 503 upstream_blocked (with_ydl moves on to
    the next configured proxy). Valid JSON of another shape is a 500.
    """
    query = {**_KAP_QUERY, "keyword": keyword, "count": _PAGE, "offset": offset, "region": region}
    if session:
        query["cacheSessionId"] = session
    problem = ""
    for attempt in range(_TRIES):
        if attempt:
            time.sleep(_RETRY_DELAY)
        try:
            raw = ie._download_webpage(_KAP, keyword, "Downloading keyword page videos", query=query, impersonate=True,
                                       headers={"Referer": f"https://www.tiktok.com/discover/{keyword}"})
        except ExtractorError as exc:
            status = exc.cause.status if isinstance(exc.cause, HTTPError) else None
            if status is None or not (status in (403, 429) or status >= 500):
                raise  # with_ydl's usual translation: network, proxy, 404 ...
            problem = f"HTTP {status}"
            continue
        text = (raw or "").strip()
        if not text or text.startswith("<"):
            problem = "an empty body" if not text else "a page instead of JSON (a bot check)"
            continue
        try:
            resp = json.loads(text)
        except (ValueError, RecursionError):  # cut off mid-answer, as throttled answers are (or nested too deep)
            problem = f"a {len(text)}-character body that isn't JSON"
            continue
        if not isinstance(resp, dict):
            raise UpstreamError("TikTok returned malformed keyword search data")
        return resp
    raise UpstreamBlocked(f"TikTok answered the keyword search with {problem} twice (throttling); retry later")


def _kap_page(ie: Any, keyword: str, region: str, offset: int,
              session: str | None) -> tuple[list[dict[str, Any]], bool, str | None]:
    """(web items with an id, has_more, cacheSessionId) of one keyword-page request."""
    resp = _kap_json(ie, keyword, region, offset, session)
    status = resp.get("statusCode") or resp.get("status_code")
    if status == _NO_PAGE:
        raise NoKeywordPage(f"TikTok has no public keyword page for {keyword!r}")
    if status:
        raise _status_error(status) or UpstreamError(
            f"TikTok keyword search failed: {resp.get('status_msg') or status}")
    listed = resp.get("videoList")
    if listed is not None and not isinstance(listed, list):
        raise UpstreamError("TikTok returned malformed keyword search data")
    items = [it for it in M.objs(listed) if _ID.fullmatch(str(it.get("id") or ""))]
    session = resp.get("cacheSessionId")
    return items, bool(resp.get("hasMore")) and bool(listed), session if isinstance(session, str) and session else None


def keyword_posts(ydl: yt_dlp.YoutubeDL, keyword: str, region: str, offset: int,
                  keep: Callable[[dict[str, Any]], bool] | None = None,
                  hops: int = 1) -> tuple[list[dict[str, Any]], int | None]:
    """Posts on `keyword`'s page from `offset`, in the app shape, and the offset of the next page (None at the end).

    Reads up to `hops` pages while `keep` (a filter on the web items) keeps none of them. The first page's
    cacheSessionId is kept for the later ones: without it TikTok re-ranks, and a later page repeats or skips posts.
    """
    ie = ydl.get_info_extractor("TikTok")
    key = (keyword, region)
    session = _session(key) if offset else None
    seen: set[str] = set()
    posts: list[dict[str, Any]] = []
    nxt: int | None = offset
    for _ in range(hops):
        items, more, new_session = _kap_page(ie, keyword, region, nxt, session)
        if new_session and new_session != session:
            session = _session(key, new_session)
        for it in items:
            if it["id"] not in seen and (keep is None or keep(it)):
                seen.add(it["id"])
                posts.append(M.aweme(it, detail=True))
        nxt = nxt + _PAGE if more else None
        if posts or nxt is None:
            break
    return posts, nxt


# --- routes ------------------------------------------------------------------

async def _signed_in(reason: str) -> None:
    """501 platform_auth_required naming `reason`, unless TikTok account cookies are configured."""
    try:
        await tiktok_private._require_account()
    except AccountRejected:  # an account is configured, and TikTok rejected it: that message says which
        raise
    except NotSupported as exc:
        raise NotSupported(f"{reason}; TikTok's own search needs a signed-in account: add TikTok account cookies "
                           "in Platform settings", code="platform_auth_required", platform=P) from exc


_NOTES = ("Logged out, from TikTok's public keyword page for the {what} (tiktok.com/discover/<keyword>, its "
          "/api/seo/kap/video_list/): TikTok's own search wants request signatures. The {what} is spelled as those "
          "pages are ('Skincare routine' -> skincare-routine). Posts are TikTok web items re-keyed to the app shape "
          "(statistics are TikTok's web rounding, 1600000; comment_count is absent for a post with comments turned "
          "off, whose web count reads 0; author.follower_count comes with them, and a verified author carries "
          "verification_type 1 and custom_verify; music carries play_url), 30 per request, ~60-90 per keyword; "
          "region (default US) picks the market's ranking. cursor is "
          "the offset to pass back, and is omitted on the last page. A keyword TikTok has no page for (fitness, "
          "skincare) answers 501 platform_auth_required{filters}: only the signed-in app API (TikTok account "
          "cookies) searches those. An empty body, a challenge page, a cut-off body or HTTP 403/429/5xx is asked "
          "once more, then is a 503 upstream_blocked.")


@sc_get(router, "/v1/tiktok/search/keyword",
        source="TikTok keyword pages (/api/seo/kap/video_list/), else the mobile search/item/full API with saved "
               "cookies",
        notes=_NOTES.format(what="query", filters=", and so do date_posted/sort_by other than all-time/relevance")
        + " Items are the aweme objects themselves, as in ScrapeCreators' example (no aweme_info wrapper).")
async def search_keyword(query: str = Query(...), date_posted: str | None = None, sort_by: str | None = None,
                         region: str | None = None, cursor: str | None = None, trim: str | None = None):
    keyword, area, offset = slug(query), _region(region), _offset("cursor", cursor)
    if not keyword:
        raise BadRequest("query must contain a letter or digit")
    posted = _choice("date_posted", date_posted, _DATE_POSTED, "all-time")
    order = _choice("sort_by", sort_by, _SORT_BY, "relevance")
    if posted == "all-time" and order == "relevance":
        try:
            posts, nxt = await with_ydl(P, lambda ydl: keyword_posts(ydl, keyword, area, offset))
        except NoKeywordPage as exc:
            reason = exc.message
        else:
            return {"search_item_list": posts, **({"cursor": nxt} if nxt is not None else {})}
    else:
        reason = "TikTok's public keyword pages can't filter by date_posted or sort by anything but relevance"
    await _signed_in(reason)
    data = await tiktok_private._search("search/item/full", query, cursor, region,
                                        publish_time=_PUBLISH_TIME[posted], sort_type=_SORT_TYPE[order])
    return {"search_item_list": M.objs(data.get("data") or data.get("item_list") or data.get("search_item_list")),
            "cursor": str(data.get("cursor") or "0"), "has_more": bool(data.get("has_more"))}


@sc_get(router, "/v1/tiktok/search/hashtag",
        source="TikTok keyword pages (/api/seo/kap/video_list/), else the mobile challenge/search API with saved "
               "cookies",
        notes=_NOTES.format(what="hashtag", filters="") + " Only posts carrying the hashtag (in their caption's "
        "hashtags or challenges) are kept, so a page can hold fewer than 30, and up to three pages are read for one "
        "that has any; an empty aweme_list with a cursor means none of those did.")
async def search_hashtag(hashtag: str = Query(...), region: str | None = None, cursor: str | None = None,
                         trim: str | None = None):
    tag = re.sub(r"\s+", "", hashtag).lstrip("#").lower()
    keyword, area, offset = slug(tag), _region(region), _offset("cursor", cursor)
    if not keyword:
        raise BadRequest("hashtag must contain a letter or digit")
    try:
        posts, nxt = await with_ydl(P, lambda ydl: keyword_posts(
            ydl, keyword, area, offset, keep=lambda it: tag in hashtags(it), hops=_HOPS))
    except NoKeywordPage as exc:
        await _signed_in(exc.message)
    else:
        return {"aweme_list": posts, **({"cursor": nxt} if nxt is not None else {})}
    data = await tiktok_private._search("challenge/search", tag, cursor, region)
    return {"aweme_list": M.objs(data.get("aweme_list") or data.get("item_list")),
            "cursor": str(data.get("cursor") or "0"), "has_more": bool(data.get("has_more"))}
