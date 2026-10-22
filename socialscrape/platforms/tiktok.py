"""TikTok endpoints.

Everything here goes through yt-dlp's TikTok extractors, either their output or the
exact web/app requests they make (via `ydl.get_info_extractor(...)`):

  TikTokIE           web post page  -> `webapp.video-detail.itemInfo.itemStruct`
  TikTokUserIE       web user page  -> `webapp.user-detail`;  /api/creator/item_list/
  TikTokCollectionIE /api/collection/item_list/
  TikTokLiveIE       live page SIGI_STATE;  webcast.tiktok.com/webcast/room/info
  TikTokSoundIE      app API aweme/v1/music/aweme/

ScrapeCreators returns TikTok's app (`aweme`) objects. /v2/tiktok/video serves the real app
object when the post shows up in its sound's app feed; otherwise web posts are re-keyed by
`_tiktok_map`.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

import yt_dlp
from fastapi import APIRouter, Query
from yt_dlp.networking import HEADRequest
from yt_dlp.utils import ExtractorError

from ..errors import (
    ApiError,
    BadRequest,
    Forbidden,
    NotFound,
    NotSupported,
    UpstreamBlocked,
    UpstreamError,
    require_one,
)
from ..registry import sc_get
from ..transcribe import transcribe
from ..util import compact, truthy
from ..ytdlp import with_ydl
from . import _tiktok_map as M

router = APIRouter()
P = "tiktok"
log = logging.getLogger("socialscrape.tiktok")

# TikTokSoundIE's app API needs an app_info entry; '' selects yt-dlp's built-in defaults.
_APP_API = {"tiktok": {"app_info": [""]}}
_ROOM_INFO = "https://webcast.tiktok.com/webcast/room/info"
_FEED_START_MS = 1472706000000  # TikTokUserIE: nothing on TikTok predates this
_WEEK_MS = 7 * 86_400_000
_FEED_HOPS = 4  # empty-week look-backs per request (TikTokUserIE steps back a week at a time)
_ID = re.compile(r"[0-9]{1,25}")  # ASCII only: str.isdigit()/\d also accept '²', '٣', ...
_OFFSET = re.compile(r"[0-9]{1,9}")
_REGION = re.compile(r"[A-Za-z]{2}")
_LANG = re.compile(r"[A-Za-z]{2,3}")
# A secUid yt-dlp can send in a query: visible ASCII, as TikTok's (base64url) is. Half an emoji fails before asking.
_SEC_UID = re.compile(r"[!-~]+")
_RETRY_DELAY = 1.5  # s before re-asking after a 10204 post or an empty app-API body
_PROFILE_TTL = 600.0  # s a handle's secUid & co. are reused across feed pages
_PROFILE_MAX = 512

# TikTok web statusCodes, by the names TikTok's web bundle gives them (github.com/davidteather/TikTok-Api/issues/403)
_PRIVATE = {10216: "This post is private", 10222: "This account is private"}  # VIDEO_PRIVATE_BY_USER, USER_PRIVATE
_REFUSED = {10000, 10102}  # VERIFY_CODE (captcha); USER_NOT_LOGIN, also sent for public feeds (a retry works)
_GONE = {10202, 10221}  # USER_NOT_EXIST; USER_BAN, also what a handle nobody has gets
# What _extract_web_data_and_status raises for a page it got no data from: a JS challenge it couldn't pass
_UNSOLVED = re.compile(r"challenge|universal data|unexpected response from webpage", re.I)
_WAF_TOKEN = "_waftokenid"  # what TikTok's WAF sets once its challenge is passed, for that page only
_profiles: dict[str, tuple[float, dict[str, Any]]] = {}
_profiles_lock = threading.Lock()


# --- params ------------------------------------------------------------------

def _numeric(name: str, value: str | None) -> str:
    value = (value or "").strip()
    if not _ID.fullmatch(value):
        raise BadRequest(f"{name} must be a numeric TikTok id")
    return value


def _clip(value: str | None) -> str:
    """clipId: a sound's id, the address of its page (https://www.tiktok.com/music/<title>-<id>), or a short link to
    it as the app shares one (vm.tiktok.com/<code>, kept for _sound_id to resolve)."""
    value = (value or "").strip()
    if "/" in value and M.is_short_url(value):
        return M.with_scheme(value)
    clip = (M.music_id(value) or "") if "/" in value else value
    if not _ID.fullmatch(clip):
        raise BadRequest("clipId must be a TikTok sound id, the URL of its page "
                         "(https://www.tiktok.com/music/<title>-<id>) or a short link to it")
    return clip


def _sound_id(ydl: yt_dlp.YoutubeDL, clip: str) -> str:
    """_clip()'s sound id, or that of the sound page its short link leads to."""
    if _ID.fullmatch(clip):
        return clip
    if mid := M.music_id(_resolve_short_link(ydl, clip)):
        return mid
    raise NotFound("This short link does not lead to a TikTok sound")


def _offset(name: str, value: str | None) -> int:
    """Item-offset cursor from a previous response ('' / None -> 0)."""
    if value in (None, ""):
        return 0
    if not _OFFSET.fullmatch(value):
        raise BadRequest(f"{name} must be a number from the previous response", code="bad_cursor")
    return int(value)


def _max_cursor(value: str | None) -> int:
    """/v3 profile feed max_cursor (ms) -> item_list cursor; '' / None / 0 start at the newest post."""
    raw = "0" if value in (None, "") else value.strip()
    cursor = int(raw) if _ID.fullmatch(raw) else -1
    if cursor == 0:  # the app API's "start from the newest post"
        return int(time.time() * 1000)
    if cursor < _FEED_START_MS:
        raise BadRequest("max_cursor must be 0 or the millisecond max_cursor of a previous response",
                         code="bad_cursor")
    return cursor


def _handle(value: str | None) -> str:
    handle = M.clean_handle(value or "")
    if not handle:
        raise BadRequest("handle is not a valid TikTok handle")
    return handle


def _check_post_url(url: str) -> None:
    if not (M.post_url(url) or M.is_short_url(url)):
        raise BadRequest("Not a TikTok video/photo URL", code="unsupported_url")


def _status_error(status: Any) -> ApiError | None:
    """403 for a private statusCode, 503 for one refusing anonymous requests, else None."""
    if not isinstance(status, int):  # TikTok's codes are numbers (and a list or object can't be looked up)
        return None
    if status in _PRIVATE:
        return Forbidden(_PRIVATE[status])
    if status in _REFUSED:
        return UpstreamBlocked(f"TikTok refused the anonymous request (status {status}); retry later")
    return None


# --- extractor calls (run inside with_ydl) -----------------------------------

def _web_page(ie: Any, url: str, name: str, note: str, **kwargs: Any) -> str:
    """A tiktok.com page, past the SlardarWAF "Please wait..." JS challenge TikTok may send.

    As in TikTokBaseIE._extract_web_data_and_status: a page without universal data is the challenge. It goes
    through yt-dlp's solver and is fetched again with the challenge cookies, which are then dropped. Still none: 503.
    The token TikTok sets once a page's challenge is passed is dropped before the next page: that page would get
    a 403 for it (as /@name/live, then /@name for one not live, did).
    """
    for cookie in [c for c in ie.cookiejar if c.name == _WAF_TOKEN]:
        ie.cookiejar.clear(cookie.domain, cookie.path, cookie.name)
    page = ie._download_webpage(url, name, note, **kwargs)
    if ie._get_universal_data(page, name):
        return page
    try:
        cookies = ie._solve_challenge_and_set_cookies(page)
    except ExtractorError as exc:  # not a challenge page, or one yt-dlp can't solve
        raise UpstreamBlocked(f"TikTok sent a page without data ({exc.orig_msg}), likely a bot check; "
                              "retry later") from exc
    try:
        page = ie._download_webpage(url, name, f"{note} with challenge cookie", **kwargs)
    finally:
        for cookie in filter(None, cookies):  # the challenge's own JS lets them expire after 1 s
            ie.cookiejar.clear(domain=".tiktok.com", path="/", name=cookie)
    if ie._get_universal_data(page, name):
        return page
    raise UpstreamBlocked("TikTok kept answering with its JS challenge (a bot check); retry later")


def _resolve_short_link(ydl: yt_dlp.YoutubeDL, url: str) -> str:
    """Where a vm./vt.tiktok.com or tiktok.com/t/ short link leads, resolved like TikTokVMIE resolves it."""
    vm = ydl.get_info_extractor("TikTokVM")
    return vm._request_webpage(HEADRequest(url), "short-link", "Resolving short link",
                               headers={"User-Agent": "facebookexternalhit/1.1"}).url


def _load_post(ydl: yt_dlp.YoutubeDL, url: str) -> tuple[Any, dict[str, Any]]:
    """URL -> (TikTokIE instance, web itemStruct), as TikTokIE._real_extract fetches it."""
    url = M.with_scheme(url)
    ref = M.post_url(url)
    if ref is None:  # a short link
        ref = M.post_url(_resolve_short_link(ydl, url))
        if ref is None:
            raise NotFound("This short link does not lead to a TikTok post")
    handle, aweme_id = ref
    ie = ydl.get_info_extractor("TikTok")
    page = M.web_url(handle, aweme_id)

    def web_data() -> tuple[Any, int]:
        try:
            return ie._extract_web_data_and_status(page, aweme_id)
        except ExtractorError as exc:
            if _UNSOLVED.search(exc.orig_msg or ""):
                raise UpstreamBlocked(f"TikTok sent a page without data ({exc.orig_msg}), likely a bot check; "
                                      "retry later") from exc
            raise

    item, status = web_data()
    if status == 10204:  # "not available": TikTok's answer for missing ids, but also sent transiently
        time.sleep(_RETRY_DELAY)
        item, status = web_data()
    if err := _status_error(status):
        raise err
    if status or not isinstance(item, dict) or not item.get("id"):
        hint = "; TikTok also sends this when throttling, so retry later before treating it as deleted" \
            if status == 10204 else ""
        raise NotFound(f"TikTok post not available (status {status}{hint})")
    return ie, item


def _app_aweme(ydl: yt_dlp.YoutubeDL, item: dict[str, Any]) -> dict[str, Any] | None:
    """The post's app aweme, if it is on the first page of its sound's app feed (TikTokSoundIE).

    TikTokIE's own app route (multi/aweme/detail) gets an empty body anonymously, but
    music/aweme still answers with full app objects - the shape ScrapeCreators serves.
    Needs extractor_args=_APP_API. Any failure just means "use the web data".
    """
    mid = str(M.obj(item.get("music")).get("id") or "")
    if not _ID.fullmatch(mid):
        return None
    try:
        resp = _music_page(ydl, mid, 0)
    except (ApiError, ExtractorError) as exc:
        log.info("tiktok: no app aweme for %s via sound %s: %s", item.get("id"), mid, exc)
        return None
    return next((a for a in M.objs(resp.get("aweme_list"))
                 if str(a.get("aweme_id")) == str(item["id"]) and isinstance(a.get("video"), dict)), None)


def _captions(ie: Any, item: dict[str, Any], language: str | None) -> str | None:
    track = M.caption_track(item, language)
    if not track:
        return None
    raw = ie._download_webpage(track["url"], item["id"], "Downloading captions", fatal=False)
    return raw if raw and raw.lstrip("\ufeff").startswith("WEBVTT") else None


def _user_page(ydl: yt_dlp.YoutubeDL, ident: str) -> dict[str, Any]:
    """TikTokUserIE's user-page request -> userInfo {user, stats, ...} plus `statusCode`."""
    ie = ydl.get_info_extractor("TikTokUser")
    page = _web_page(ie, ie._UPLOADER_URL_FORMAT % ident, ident, "Downloading user webpage",
                     impersonate=True, headers=ie._generate_blockbuster_headers())
    detail = ie._get_universal_data(page, ident).get("webapp.user-detail")
    if not isinstance(detail, dict):
        raise UpstreamBlocked("TikTok returned no profile data (likely a bot check); retry later")
    status = detail.get("statusCode") or 0
    info = M.obj(detail.get("userInfo"))
    if not M.obj(info.get("user")):
        raise _status_error(status) or NotFound(f"TikTok user not found (status {status})")
    return {**info, "stats": M.exact_stats(info.get("stats"), info.get("statsV2")), "statusCode": status}


def _live_page(ie: Any, name: str) -> dict[str, Any] | None:
    """The /@name/live page's SIGI LiveRoom.liveRoomUserInfo {user, stats, liveRoom}, if it has the user.

    TikTok only fills it for the exact-case uniqueId: /@WeatherNewsLive/live or /@<numeric id>/live
    carry just CurrentRoom.
    """
    page = _web_page(ie, f"https://www.tiktok.com/@{name}/live", name, "Downloading live page")
    info = M.obj(M.obj(ie._get_sigi_state(page, name).get("LiveRoom")).get("liveRoomUserInfo"))
    return info if M.obj(info.get("user")) else None


def _profile(ydl: yt_dlp.YoutubeDL, name: str) -> dict[str, Any]:
    """{id, secUid, nickname, private, videoCount} for a handle, from `_user_page`.

    Cached for _PROFILE_TTL so paging a feed doesn't re-download the ~300 KB user page.
    """
    key, now = name.lower(), time.monotonic()
    with _profiles_lock:
        hit = _profiles.get(key)
    if hit and hit[0] > now:
        return hit[1]
    info = _user_page(ydl, name)
    user = info["user"]
    if not _SEC_UID.fullmatch(M.text(user.get("secUid")) or ""):
        raise UpstreamError("TikTok profile has no secUid")
    prof = {"id": str(user.get("id") or ""), "secUid": user["secUid"], "nickname": user.get("nickname"),
            "private": info["statusCode"] == 10222 or bool(user.get("privateAccount")),
            "videoCount": M.obj(info.get("stats")).get("videoCount")}
    with _profiles_lock:
        _profiles.pop(key, None)
        while len(_profiles) >= _PROFILE_MAX:
            _profiles.pop(next(iter(_profiles)))
        _profiles[key] = (now + _PROFILE_TTL, prof)
    return prof


def _feed_page(ie: Any, sec_uid: str, display: str, cursor: int, region: str) -> tuple[list[dict], int | None]:
    """One page of /api/creator/item_list/ (newest first) at or before `cursor` (ms).

    The API's cursor is inclusive, so the next cursor is one ms before the oldest item
    returned; items newer than `cursor` are dropped so pages never overlap. Like
    TikTokUserIE, an empty week is skipped by stepping the cursor back 7 days.
    """
    for _ in range(_FEED_HOPS):
        query = {**ie._build_web_query(sec_uid, cursor), "region": region}
        resp = ie._download_json(ie._API_BASE_URL, display, "Downloading video list", query=query)
        if not isinstance(resp, dict):
            raise UpstreamError("TikTok returned no video list")
        status = resp.get("statusCode") or resp.get("status_code")
        if isinstance(status, int) and status in _GONE:
            raise NotFound(f"TikTok user not found (status {status})")
        if status:  # 10201 is a bad query parameter; no other code's meaning here is established
            raise _status_error(status) or UpstreamError(f"TikTok item_list failed: {resp.get('status_msg') or status}")
        seen: set[str] = set()
        items = []
        for it in M.objs(resp.get("itemList")):
            aweme_id, created = str(it.get("id") or ""), M.to_int(it.get("createTime"))
            if _ID.fullmatch(aweme_id) and aweme_id not in seen and created and created * 1000 <= cursor:
                seen.add(aweme_id)
                items.append(it)
        more = bool(resp.get("hasMorePrevious"))
        if items:
            nxt = min(int(it["createTime"]) for it in items) * 1000 - 1
            return items, nxt if more and nxt >= _FEED_START_MS else None
        cursor -= _WEEK_MS
        if not more or cursor < _FEED_START_MS:
            return [], None
    return [], cursor  # still walking back through empty weeks: let the client continue


def _account_region(ydl: yt_dlp.YoutubeDL, prof: dict[str, Any], items: list[dict[str, Any]],
                    tries: int = 2) -> str | None:
    """App `author.region` of one of the creator's posts, found via the creator's own sound.

    Web data has no account region; TikTokSoundIE's app feed does. A post with the creator's
    own original sound is (almost always) on the first page of that sound's feed.

    None only when there is no own-sound post or the app API answered without the region (a
    sound it reports as unavailable counts as an answer). If every lookup failed (proxy, network,
    throttled empty body), the last failure is raised: the region may well be there.
    """
    def own_sound(it: dict[str, Any]) -> bool:
        m = M.obj(it.get("music"))
        return (m.get("original") is True and bool(_ID.fullmatch(str(m.get("id") or "")))
                and m.get("authorName") in (prof.get("nickname"), M.obj(it.get("author")).get("nickname")))

    own = [it for it in items if own_sound(it)]
    failure: Exception | None = None
    answered = False
    for it in own[:tries]:
        try:
            resp = _music_page(ydl, str(it["music"]["id"]), 0)
        except NotFound as exc:  # the app API answered: the sound is gone
            log.info("tiktok: region lookup via sound %s: %s", it["music"]["id"], exc)
            answered = True
            continue
        except (ApiError, ExtractorError) as exc:
            log.info("tiktok: region lookup via sound %s failed: %s", it["music"]["id"], exc)
            failure = exc
            continue
        answered = True
        for a in M.objs(resp.get("aweme_list")):
            au = M.obj(a.get("author"))
            if (str(au.get("uid")) == prof["id"] or str(a.get("aweme_id")) == str(it.get("id"))) and au.get("region"):
                return str(au["region"])
    if failure is not None and not answered:
        raise failure  # with_ydl turns a raw ExtractorError into 502 proxy_error/network_error, 503, ...
    return None


def _music_page(ydl: yt_dlp.YoutubeDL, clip_id: str, cursor: int) -> dict[str, Any]:
    """TikTokSoundIE's app-API request: videos using a sound, 20 per page.

    The app API intermittently answers with an empty body; yt-dlp only retries that by
    switching app_info, so with the single default app_info it is retried once here.
    """
    ie = ydl.get_info_extractor("TikTokSound")
    query = {"music_id": clip_id, "cursor": cursor, "count": 20, "type": 5, "device_id": ie._DEVICE_ID}
    for attempt in range(2):
        try:
            resp = ie._call_api("music/aweme", clip_id, note="Downloading sound videos", query=query)
            break
        except ExtractorError as exc:
            if attempt or not isinstance(exc.cause, json.JSONDecodeError):
                raise
            time.sleep(_RETRY_DELAY)
    if not isinstance(resp, dict):
        raise UpstreamError("TikTok app API returned no data")
    if resp.get("status_code"):
        raise NotFound(f"TikTok sound not available: {resp.get('status_msg') or resp['status_code']}")
    return resp


# --- routes ------------------------------------------------------------------

@sc_get(router, "/v2/tiktok/video",
        source="yt-dlp TikTokIE (web itemStruct via _extract_web_data_and_status) + TikTokSoundIE app API "
               "(music/aweme)",
        notes="The post is read from its web page, then looked up on the first page of its sound's app feed "
              "(TikTokSoundIE's music/aweme). When found - typical for original sounds - aweme_detail is TikTok's "
              "app object as-is (exact counts, has_watermark, download_no_watermark_addr, author.region, ...) plus "
              "url/create_time_utc/is_ad (from is_ads); the author counts the app feed zeroes, and any count past "
              "int32 it pins at 2147483597, are taken from the web post (rounded there: 2400000000). Otherwise it "
              "is the web post re-keyed to the app shape: app-only fields are absent, download_addr is the "
              "watermarked web URL, counts are TikTok's web "
              "rounding (42160618 plays -> 42200000) and is_ad is the web isAd flag, which can disagree with the "
              "app's is_ads. The app feed now and then fails or leaves the post out, so the same post can come back "
              "in either shape. Photo/slideshow posts carry image_post_info; vm./vt./t/ short links are resolved like "
              "TikTokVMIE. get_transcript returns native WEBVTT captions only. region, trim and download_media are "
              "ignored. TikTokSoundIE is marked broken in yt-dlp, so the app path may disappear without notice.")
async def tiktok_video(url: str = Query(...), get_transcript: str | None = None, region: str | None = None,
                       trim: str | None = None, download_media: str | None = None) -> dict[str, Any]:
    _check_post_url(url)
    want = truthy(get_transcript)

    def go(ydl: yt_dlp.YoutubeDL) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
        ie, item = _load_post(ydl, url)
        return item, _app_aweme(ydl, item), _captions(ie, item, None) if want else None

    item, app, vtt = await with_ydl(P, go, extractor_args=_APP_API)
    body: dict[str, Any] = {"aweme_detail": M.app_detail(app, item) if app else M.aweme(item, detail=True)}
    if want:
        body["transcript"] = vtt
    return body


@sc_get(router, "/v1/tiktok/video/transcript",
        source="yt-dlp TikTokIE (web itemStruct video.subtitleInfos, as TikTokIE._get_subtitles)",
        notes="Native captions as WEBVTT; `language` is an ISO 639-1/639-2 code matched via claInfo (en or eng -> "
              "eng-US; zh, zho, chi or cmn -> cmn-Hans-CN; Nynorsk and Bokmål stand in for each other), default "
              "is the original-language track. use_ai_as_fallback runs local Whisper on the yt-dlp audio download "
              "(501 if SOCIALSCRAPE_TRANSCRIBE is off) and returns plain text, usually null when there is no speech "
              "(best effort); a `language` the configured Whisper model can't transcribe is a 400. Media over 120 s "
              "needs allow_long_transcription=true (that literal value only).")
async def tiktok_transcript(url: str = Query(...), language: str | None = None,
                            use_ai_as_fallback: str | None = None,
                            allow_long_transcription: str | None = None) -> dict[str, Any]:
    _check_post_url(url)
    if language and not _LANG.fullmatch(language):
        raise BadRequest("language must be a 2- or 3-letter language code, e.g. en or eng")

    def go(ydl: yt_dlp.YoutubeDL) -> tuple[dict[str, Any], str | None]:
        ie, item = _load_post(ydl, url)
        return item, _captions(ie, item, language)

    item, transcript = await with_ydl(P, go)
    page = M.web_url(M.obj(item.get("author")).get("uniqueId"), str(item["id"]))
    if transcript is None and truthy(use_ai_as_fallback):
        duration = M.obj(item.get("video")).get("duration")
        result = await transcribe(page, platform=P, language=language,  # the core maps eng -> en, or answers 400
                                  allow_long=allow_long_transcription == "true",
                                  duration=duration if isinstance(duration, (int, float)) and duration > 0 else None)
        transcript = result.text or None
    return {"id": str(item["id"]), "url": page, "transcript": transcript}


@sc_get(router, "/v1/tiktok/profile", source="yt-dlp TikTokUserIE (user page webapp.user-detail)",
        notes="TikTok's web userInfo as-is (user, stats, itemList — itemList is usually empty on the web page). "
              "user_id must be the numeric id; it is looked up through the same page (/@<id>).")
async def tiktok_profile(handle: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    require_one(handle=handle, user_id=user_id)
    ident = _handle(handle) if handle else _numeric("user_id", user_id)
    info = await with_ydl(P, lambda ydl: _user_page(ydl, ident))
    return compact({"user": info["user"], "stats": info.get("stats"), "itemList": info.get("itemList") or []})


@sc_get(router, "/v3/tiktok/profile/videos",
        source="yt-dlp TikTokUserIE (/api/creator/item_list/ via _build_web_query)",
        notes="Newest first, ~15 per page; max_cursor is a ms timestamp (one ms before the page's oldest post), "
              "so posts sharing that exact second with the page's oldest post can be skipped; max_cursor=0 or "
              "omitted starts at the newest post; any other value that isn't such a timestamp is a 400 bad_cursor. "
              "has_more is false (and max_cursor absent) on the last page. After "
              "4 empty weeks a page may be empty but still carry max_cursor. "
              "sort_by=popular is unsupported (501): the creator feed TikTokUserIE uses is time-ordered only. "
              "region is passed to the feed (default US). include_pinned=false can't be honored: the creator feed "
              "doesn't mark pinned posts (isPinnedItem is never set), so they stay in date order and are not "
              "removed. user_id and trim are ignored. Posts are web data re-keyed to the app shape (app-only "
              "fields absent; counts use TikTok's web rounding, e.g. 42160618 -> 42200000; is_ad is the web isAd "
              "flag, which can disagree with the app's is_ads). Photo posts carry their images (image_post_info, "
              "as on /v2/tiktok/video); no post carries music. The handle's secUid is cached for 10 minutes, so "
              "pages after the first make one TikTok request. TikTok sometimes refuses a public feed to anonymous "
              "requests (statusCode 10102); that is a 503 upstream_blocked, and a retry soon after works.")
async def tiktok_profile_videos(handle: str = Query(...), user_id: str | None = None, sort_by: str | None = None,
                                max_cursor: str | None = None, region: str | None = None,
                                include_pinned: str | None = None, trim: str | None = None) -> dict[str, Any]:
    name = _handle(handle)
    if sort_by == "popular":
        raise NotSupported("sort_by=popular: TikTok's creator item_list (used by yt-dlp) is time-ordered only")
    if sort_by not in (None, "", "latest"):
        raise BadRequest("sort_by must be latest or popular")
    cursor = _max_cursor(max_cursor)
    if region and not _REGION.fullmatch(region):
        raise BadRequest("region must be a 2-letter country code")
    keep_pinned = include_pinned in (None, "") or truthy(include_pinned)

    def go(ydl: yt_dlp.YoutubeDL) -> tuple[list[dict], int | None]:
        prof = _profile(ydl, name)
        if prof["private"]:
            raise Forbidden("This account is private")
        if prof["videoCount"] == 0:
            return [], None
        ie = ydl.get_info_extractor("TikTokUser")
        return _feed_page(ie, prof["secUid"], name, cursor, (region or "US").upper())

    items, next_cursor = await with_ydl(P, go)
    if not keep_pinned:  # a no-op today (see notes), kept in case TikTok starts flagging pinned posts
        items = [it for it in items if not it.get("isPinnedItem")]
    return compact({"aweme_list": [M.aweme(it) for it in items], "has_more": next_cursor is not None,
                    "max_cursor": next_cursor})


@sc_get(router, "/v1/tiktok/profile/region",
        source="yt-dlp TikTokUserIE (user page, /api/creator/item_list/) + TikTokSoundIE app API "
               "(music/aweme author.region)",
        notes="Web data has no account region, so it is read from the app API's author.region on one of the "
              "creator's recent posts, found through the creator's own original sound (music/aweme). That costs "
              "about 3-4 TikTok requests. 404 region_unavailable when none of the latest posts uses the "
              "creator's own sound (or it isn't on that sound's first page). When the app API can't be reached "
              "for any of those sounds, the error is the retryable one /v1/tiktok/song gives (502 proxy_error/"
              "network_error, 503 when throttled), not a 404. TikTokSoundIE is marked broken in yt-dlp, so this "
              "may stop working without notice.")
async def tiktok_profile_region(handle: str = Query(...)) -> dict[str, Any]:
    name = _handle(handle)

    def go(ydl: yt_dlp.YoutubeDL) -> str | None:
        prof = _profile(ydl, name)
        if prof["private"]:
            raise Forbidden("This account is private")
        if prof["videoCount"] == 0:
            return None
        ie, cursor, items = ydl.get_info_extractor("TikTokUser"), int(time.time() * 1000), []
        for _ in range(2):  # a second round only for accounts idle for over _FEED_HOPS weeks
            items, cursor = _feed_page(ie, prof["secUid"], name, cursor, "US")
            if items or cursor is None:
                break
        return _account_region(ydl, prof, items)

    region = await with_ydl(P, go, extractor_args=_APP_API)
    if not region:
        raise NotFound("Could not read this account's region: none of its recent posts uses its own original "
                       "sound, or the sound's app feed doesn't list the post", code="region_unavailable")
    return {"handle": name, "profile_url": f"https://www.tiktok.com/@{name}", "region": region}


@sc_get(router, "/v1/tiktok/user/live",
        source="yt-dlp TikTokLiveIE (live page SIGI_STATE LiveRoom; user page + webcast/room/info)",
        notes="Use is_live. liveRoomUserInfo/liveRoom are the /@handle/live page's data when it has the user (while "
              "live, and for a recently ended room: liveRoom.status 4); otherwise liveRoomUserInfo comes from the "
              "profile page, is_live from webcast/room/info, and liveRoom is omitted. The live page answers only to "
              "the exact uniqueId, so a mixed-case handle or numeric id costs two more requests.")
async def tiktok_user_live(handle: str = Query(...)) -> dict[str, Any]:
    name = _handle(handle)

    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any]:
        ie = ydl.get_info_extractor("TikTokLive")
        info, profile = _live_page(ie, name), None
        if info is None:
            profile = _user_page(ydl, name)
            unique = profile["user"].get("uniqueId")
            if M.is_handle(unique) and unique != name:  # 'WeatherNewsLive' or a numeric id: retry with the uniqueId
                info = _live_page(ie, unique)
        if info:
            room = M.obj(info.get("liveRoom"))
            live = M.to_int(room.get("status")) == 2 or M.to_int(info["user"].get("status")) == 2
            return compact({"is_live": live, "liveRoomUserInfo": M.live_user(info["user"], info.get("stats")),
                            "liveRoom": room or None})
        user, live = profile["user"], False
        room_id = str(user.get("roomId") or "")
        if _ID.fullmatch(room_id):  # "" when not live; a roomId that isn't an id can't be asked about
            resp = ie._download_json(_ROOM_INFO, room_id, "Downloading room info", fatal=False,
                                     query={"aid": "1988", "room_id": room_id})
            live = M.to_int(M.obj(M.obj(resp).get("data")).get("status")) == 2
        return {"is_live": live, "liveRoomUserInfo": M.live_user(user, profile.get("stats"))}

    return await with_ydl(P, go)


@sc_get(router, "/v1/tiktok/live", source="yt-dlp TikTokLiveIE (webcast.tiktok.com/webcast/room/info)",
        notes="404 unless the room exists and its owner is user_id. like_count is omitted when the anonymous "
              "room info reports 0 (it does even for busy rooms).")
async def tiktok_live(room_id: str = Query(...), user_id: str = Query(...)) -> dict[str, Any]:
    rid, uid = _numeric("room_id", room_id), _numeric("user_id", user_id)

    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any]:
        ie = ydl.get_info_extractor("TikTokLive")
        return ie._download_json(_ROOM_INFO, rid, "Downloading room info", query={"aid": "1988", "room_id": rid})

    resp = await with_ydl(P, go)
    data = resp.get("data") if isinstance(resp, dict) else None
    if not isinstance(data, dict) or resp.get("status_code") or not data.get("id_str"):
        raise NotFound("TikTok live room not found")
    if str(M.obj(data.get("owner")).get("id_str") or "") != uid:
        raise NotFound("This live room does not belong to user_id")
    return M.live_room(data)


@sc_get(router, "/v1/tiktok/song", source="yt-dlp TikTokSoundIE (app API aweme/v1/music/aweme)",
        notes="clipId is the sound's id, the address of its page (https://www.tiktok.com/music/<title>-<id>) "
              "or a short link to it (vm.tiktok.com/<code>; one that leads elsewhere is a 404). "
              "music_info is the app's music object embedded in the sound's first video (there is no music "
              "detail call in yt-dlp); user_count is omitted when the embed reports 0.")
async def tiktok_song(clipId: str = Query(...)) -> dict[str, Any]:
    clip = _clip(clipId)

    def go(ydl: yt_dlp.YoutubeDL) -> tuple[str, dict[str, Any]]:
        mid = _sound_id(ydl, clip)
        return mid, _music_page(ydl, mid, 0)

    clip, resp = await with_ydl(P, go, extractor_args=_APP_API)
    music = next((a["music"] for a in M.objs(resp.get("aweme_list"))
                  if str(M.obj(a.get("music")).get("id_str")) == clip), None)
    if not music:
        raise NotFound("TikTok sound not found")
    if not music.get("user_count"):
        music = {k: v for k, v in music.items() if k != "user_count"}
    return {"music_info": music}


@sc_get(router, "/v1/tiktok/song/videos", source="yt-dlp TikTokSoundIE (app API aweme/v1/music/aweme)",
        notes="clipId is the sound's id, the address of its page (https://www.tiktok.com/music/<title>-<id>) "
              "or a short link to it (vm.tiktok.com/<code>; one that leads elsewhere is a 404). "
              "TikTok app response as-is (~20 per page), so counts past int32 are the app's pinned 2147483597. "
              "cursor is the offset from the previous page and is omitted on the last page; a cursor that isn't "
              "such an offset is a 400 bad_cursor.")
async def tiktok_song_videos(clipId: str | None = None, cursor: str | None = None) -> dict[str, Any]:
    require_one(clipId=clipId)
    clip = _clip(clipId)
    offset = _offset("cursor", cursor)
    resp = await with_ydl(P, lambda ydl: _music_page(ydl, _sound_id(ydl, clip), offset), extractor_args=_APP_API)
    if not resp.get("aweme_list") and not offset:
        raise NotFound("TikTok sound not found or has no videos")
    body = {k: v for k, v in resp.items() if not (k == "cursor" and not resp.get("has_more"))}
    body.setdefault("aweme_list", [])
    return body


@sc_get(router, "/v1/tiktok/collection/videos",
        source="yt-dlp TikTokCollectionIE (/api/collection/item_list/ via _build_web_query)",
        notes="videos are TikTok's web item objects as-is, 30 per page. max_cursor is the item offset TikTok "
              "returns; pass it back as cursor while has_more is true (anything else is a 400 bad_cursor). A "
              "removed or non-public collection (TikTok statusCode 100001), or an id of 0 or beyond int64, is 404. "
              "Of TikTok's other statusCodes, 10216/10222 (private) are 403, 10000/10102 (TikTok refusing "
              "anonymous requests) 503, and any other 500.")
async def tiktok_collection_videos(url: str = Query(...), cursor: str | None = None) -> dict[str, Any]:
    cid = M.collection_id(url)
    if not cid:
        raise BadRequest("Not a TikTok collection URL (https://www.tiktok.com/@user/collection/<name>-<id>)",
                         code="unsupported_url")
    cid = cid.lstrip("0")
    if not cid or len(cid) > 19 or int(cid) >= 2**63:  # TikTok answers HTTP 400 for ids outside int64
        raise NotFound("TikTok collection not available (no collection has this id)")
    offset = _offset("cursor", cursor)

    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any]:
        ie = ydl.get_info_extractor("TikTokCollection")
        return ie._download_json(ie._API_BASE_URL, cid, "Downloading collection page",
                                 query=ie._build_web_query(cid, offset))

    resp = await with_ydl(P, go)
    if not isinstance(resp, dict):
        raise UpstreamError("TikTok returned no collection data")
    status = resp.get("statusCode") or resp.get("status_code")
    if status == 100001:  # what both removed and never-existing ids get
        raise NotFound(f"TikTok collection not available (status {status})")
    if status:  # no other code's meaning here is established
        raise _status_error(status) or UpstreamError(f"TikTok collection item_list failed (status {status})")
    nxt = resp.get("cursor")
    return compact({
        "collection_id": cid,
        "has_more": bool(resp.get("hasMore")),
        "max_cursor": str(nxt) if nxt not in (None, "") else None,
        "status_code": 0,
        "status_msg": resp.get("status_msg") or "",
        "videos": [it for it in M.objs(resp.get("itemList")) if it.get("id")],
    })


_NO_EXTRACTOR = "yt-dlp has no TikTok extractor that calls"

UNSUPPORTED = {
    "/v1/tiktok/video/comments": "yt-dlp's TikTok extractors don't extract comments; TikTok's "
                                 "/api/comment/list/ is never called by yt-dlp.",
    "/v1/tiktok/video/comment/replies": "yt-dlp's TikTok extractors don't extract comments or replies; "
                                        "/api/comment/list/reply/ is never called by yt-dlp.",
    "/v1/tiktok/user/audience": "Audience demographics are creator-analytics data; " + _NO_EXTRACTOR + " it.",
    "/v1/tiktok/user/following": "Following lists come from /api/user/list/; " + _NO_EXTRACTOR + " it.",
    "/v1/tiktok/user/followers": "Follower lists come from /api/user/list/; " + _NO_EXTRACTOR + " it.",
    "/v1/tiktok/search/users": "yt-dlp has no TikTok search extractor (/api/search/user/ is never called).",
    "/v1/tiktok/search/suggestions": "yt-dlp has no TikTok search extractor (search suggestions are never "
                                     "fetched).",
    "/v1/tiktok/search/hashtag": "yt-dlp has no TikTok search; TikTokTagIE (marked broken) lists a tag's feed via "
                                 "the app API challenge/aweme, which returned an empty body anonymously "
                                 "(probed 2026-10-13).",
    "/v1/tiktok/search/keyword": "yt-dlp has no TikTok search extractor (/api/search/item/ is never called).",
    "/v1/tiktok/search/top": "yt-dlp has no TikTok search extractor (/api/search/general/ is never called).",
    "/v1/tiktok/creators/popular": "Popular creators come from TikTok Creative Center; " + _NO_EXTRACTOR + " it.",
    "/v1/tiktok/get-trending-feed": "The For You/trending feed (/api/recommend/item_list/) is never called by "
                                    "yt-dlp.",
    "/v1/tiktok/shop/search": "TikTok Shop is not touched by any yt-dlp extractor.",
    "/v1/tiktok/shop/products": "TikTok Shop is not touched by any yt-dlp extractor.",
    "/v1/tiktok/product": "TikTok Shop product pages are not touched by any yt-dlp extractor.",
    "/v1/tiktok/shop/product/reviews": "TikTok Shop reviews are not touched by any yt-dlp extractor.",
    "/v1/tiktok/user/showcase": "The profile Shop showcase is TikTok Shop data; " + _NO_EXTRACTOR + " it.",
    "/v1/tiktok/ad-library/search": "TikTok's Ad Library (library.tiktok.com) has no yt-dlp extractor.",
    "/v1/tiktok/ad-library/ad": "TikTok's Ad Library (library.tiktok.com) has no yt-dlp extractor.",
}
