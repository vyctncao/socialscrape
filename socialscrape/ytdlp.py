"""Async bridge to yt-dlp.

yt-dlp is blocking, so every call runs in a bounded thread pool with a fresh
`YoutubeDL` instance (instances are not safe to share across threads).
yt-dlp exceptions are translated into `ApiError`s so routes never see them.

Entry point: with_ydl(platform, fn) runs fn(ydl), for access to extractor
internals and yt-dlp's networking stack.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, partial
from http.cookiejar import Cookie
from typing import Any, Callable, TypeVar
from urllib.parse import parse_qs, urljoin

import yt_dlp
from yt_dlp.cookies import CookieLoadError, YoutubeDLCookieJar
from yt_dlp.extractor.youtube import YoutubeBaseInfoExtractor, YoutubeIE
from yt_dlp.networking.exceptions import HTTPError, NoSupportingHandlers, ProxyError, RequestError, TransportError
from yt_dlp.utils import (
    DownloadError,
    ExtractorError,
    GeoRestrictedError,
    UnsupportedError,
    UserNotLive,
    YoutubeDLError,
    parse_count,
    parse_duration,
    parse_iso8601,
    traverse_obj,
    unified_strdate,
    variadic,
)
from yt_dlp.utils import _utils as yt_dlp_utils

from .config import get_settings
from .errors import (ApiError, BadRequest, Forbidden, Misconfigured, NotFound, UpstreamBlocked, UpstreamError,
                     UpstreamUnreachable)
from .util import requestable_url

log = logging.getLogger("socialscrape.ytdlp")
T = TypeVar("T")

_executor: ThreadPoolExecutor | None = None

# Only the platform's own extractors may run, so a stray URL can't fall through
# to yt-dlp's generic extractor (or another site's) on a platform route.
ALLOWED_EXTRACTORS = {
    "youtube": [r"youtube(:.*)?"],
    "tiktok": [r"tiktok(:.*)?"],
    "instagram": [r"instagram(:.*)?"],
    "facebook": [r"facebook(:.*)?"],
    # Google ad-library routes use InfoExtractor networking only. No generic URL extraction is permitted.
    "google": [r"$^"],
}


def _html_entity(entity: str, read: Callable[[str], str] = yt_dlp_utils._htmlentity_transform) -> str:
    """yt-dlp's reader of an HTML character reference ('#38;' -> '&'), but one of a number too large for a C int
    ('&#2147483648;') is read as its text, as yt-dlp reads one past Unicode's last character ('&#1114112;'): yt-dlp's
    own reader fails on it (an OverflowError, so a 500 here)."""
    try:
        return read(entity)
    except OverflowError:
        return f"&{entity}"


# unescapeHTML looks its reader up as it runs, so this one reads each page text yt-dlp and we unescape, on every
# platform: meta tags, elements, error screens.
yt_dlp_utils._htmlentity_transform = _html_entity


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=get_settings().max_workers, thread_name_prefix="ytdlp")
    return _executor


def shutdown_pool() -> list[threading.Thread]:
    """Stop the pool without waiting and return its worker threads (the next call starts a new pool)."""
    global _executor
    pool, _executor = _executor, None
    return stop_executor(pool)


def stop_executor(pool: ThreadPoolExecutor | None) -> list[threading.Thread]:
    """Cancel the queued calls and return the worker threads, which exit once their current call ends. A call that
    outlived its request (SOCIALSCRAPE_REQUEST_TIMEOUT) can't be interrupted: a thread can't be stopped."""
    if pool is None:
        return []
    pool.shutdown(wait=False, cancel_futures=True)
    return list(pool._threads)  # no public accessor for them


class _Logger:
    """Route yt-dlp output to `logging` instead of stdout/stderr."""

    def debug(self, msg: str) -> None:
        log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.info(msg)

    def error(self, msg: str) -> None:
        log.info(msg)


def base_options(platform: str, dashboard_proxy: str | None = None,
                 dashboard_cookies: bool = False) -> dict[str, Any]:
    s = get_settings()
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "logger": _Logger(),
        "socket_timeout": s.socket_timeout,
        # Metadata must survive missing/undecipherable formats (e.g. no JS runtime for YouTube).
        "ignore_no_formats_error": True,
        "check_formats": False,
        "extractor_retries": 2,
        "allowed_extractors": ALLOWED_EXTRACTORS.get(platform, ["default"]),
    }
    proxy = dashboard_proxy or s.proxy
    if proxy:
        opts["proxy"] = proxy
    if not dashboard_cookies and (cookie_file := s.cookie_file(platform)):
        if problem := cookie_file_problem(cookie_file):
            raise Misconfigured(f"The server's {platform} cookie file (SOCIALSCRAPE_COOKIES_{platform.upper()}) "
                                f"{problem}", code="cookies_error")
        opts["cookiefile"] = cookie_file
    elif not dashboard_cookies and s.cookies_from_browser:
        browser, _, profile = s.cookies_from_browser.partition(":")
        opts["cookiesfrombrowser"] = (browser, profile or None, None, None)
    return opts


def _run(platform: str, fn: Callable[[yt_dlp.YoutubeDL], T], opts: dict[str, Any]) -> T:
    dashboard_proxy = opts.pop("_dashboard_proxy", None)
    dashboard_cookies = opts.pop("_dashboard_cookies", None)
    merged = base_options(platform, dashboard_proxy, dashboard_cookies is not None)
    extractor_args = opts.pop("extractor_args", None)
    merged.update(opts)
    if extractor_args:
        merged["extractor_args"] = extractor_args
    with yt_dlp.YoutubeDL(merged) as ydl:
        if dashboard_cookies:
            try:
                ydl.cookiejar._really_load(io.StringIO(dashboard_cookies), "dashboard cookies.txt", True, True)
            except Exception as exc:
                raise Misconfigured(
                    f"The saved {platform} cookies.txt file cannot be loaded", code="cookies_error"
                ) from exc
        if platform == "youtube":
            for key in ("Youtube", "YoutubeTab"):
                ie = ydl.get_info_extractor(key)
                ie._extract_alerts, ie._extract_thumbnails = _youtube_alerts, _youtube_thumbnails
            ie = ydl.get_info_extractor("Youtube")
            ie._is_agegated, ie._extract_storyboard = youtube_agegated, _youtube_storyboards
            ie._extract_player_response = partial(_youtube_player_api, ie._extract_player_response)
            ie._invalid_player_response = partial(_youtube_player_response, ie._invalid_player_response)
            ie._download_initial_data = partial(_youtube_initial_data, ie, ie._download_initial_data)
            ie._html_search_meta = partial(_youtube_meta, ie._html_search_meta)
        try:
            # Left out, as yt-dlp leaves out a cookies.txt line it can't read (_cookie_parse_problem logs which).
            for cookie in [c for c in ydl.cookiejar if not _cookie_sendable(c)]:
                ydl.cookiejar.clear(cookie.domain, cookie.path, cookie.name)
            return fn(ydl)
        finally:
            # The operator's cookies.txt is input only. Without this, close() saves the session's jar over it
            # with a plain open(path, "w") from every worker thread at once, truncating it for its readers.
            ydl.params["cookiefile"] = None


def _youtube_alerts(data: Any) -> list[tuple[str, str]]:
    """(type, message) of a YouTube response's alerts, as YoutubeBaseInfoExtractor._extract_alerts reads them, but an
    alert that isn't an object, or whose type isn't a string, is no alert: yt-dlp's own reader fails on one (an
    AttributeError, so a 500 here)."""
    return [(alert["type"], message) for alert in traverse_obj(data, ("alerts", {list}, ..., {dict}, ..., {dict}))
            if alert.get("type") and isinstance(alert["type"], str)
            and (message := YoutubeBaseInfoExtractor._get_text(alert, "text"))]


def _youtube_thumbnails(data: Any, *path_list: Any, final_key: str = "thumbnails") -> list[dict[str, Any]]:
    """YoutubeBaseInfoExtractor._extract_thumbnails, but a thumbnail that isn't an object is none: yt-dlp's own
    reader fails on one (an AttributeError, so a 500 here)."""
    kept = [thumbnail for path in path_list or [()]
            for thumbnail in traverse_obj(data, (*variadic(path), final_key, ..., {dict}))]
    return YoutubeBaseInfoExtractor._extract_thumbnails({final_key: kept}, final_key=final_key)


def youtube_agegated(player_response: Any) -> bool:
    """YoutubeIE._is_agegated, but a playability status or reason that isn't a string is none: yt-dlp's own check,
    which it runs on every player response, fails on one (a TypeError, so a 500 here)."""
    status = traverse_obj(player_response, ("playabilityStatus", {dict})) or {}
    return YoutubeIE._is_agegated({"playabilityStatus": {
        k: v for k, v in status.items() if k not in ("status", "reason") or isinstance(v, str)}})


def _youtube_storyboards(player_responses: Any, duration: Any) -> list[dict[str, Any]]:
    """YoutubeIE._extract_storyboard, but a video has no storyboards (we read no formats): yt-dlp divides by a
    storyboard spec's numbers and the video's length without checking them, and reads the spec's URL as one it can
    request (an AttributeError, an OverflowError, a ZeroDivisionError, a TypeError, a ValueError or a UnicodeError on
    a spec that isn't a string, huge numbers, a URL it can't request or a video of no length, so a 500 here), and it
    lists a fragment for each that a spec counts, however many."""
    return []


def _youtube_player_api(extract: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """YoutubeIE._extract_player_response, but an answer that isn't an object fails as one that isn't JSON does:
    yt-dlp writes its client's details into the answer (an AttributeError, so a 500 here)."""
    player_response = extract(*args, **kwargs)
    if player_response is not None and not isinstance(player_response, dict):
        raise ExtractorError("The player API's response is not an object", expected=True)
    return player_response


def _conform(value: Any, schema: Any) -> bool:
    """Whether `value` fits `schema`, once what doesn't fit is deleted from it: `schema` is a dict of keys to their
    schemas (a key whose value doesn't fit is deleted), a list of one item's schema (an item that doesn't fit is
    dropped), a type, or a check."""
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            return False
        for key in [k for k, v in value.items() if k in schema and not _conform(v, schema[k])]:
            del value[key]
        return True
    if isinstance(schema, list):
        if not isinstance(value, list):
            return False
        value[:] = [item for item in value if _conform(item, schema[0])]
        return True
    return isinstance(value, schema) if isinstance(schema, type) else schema(value)


def _number(value: Any) -> bool:
    """A number, or a string of one, too small to overflow yt-dlp's float math (an OverflowError, so a 500 here)."""
    try:
        return isinstance(value, (int, float, str)) and abs(float(value)) < 2**64
    except (ValueError, OverflowError):
        return False


def _date(value: Any) -> bool:
    """A date string yt-dlp can read: its parse_iso8601 and unified_strdate fail on another type (a TypeError) and on
    a date that its offset, or a year past a C int, puts out of datetime's range (an OverflowError), so a 500 here."""
    if not isinstance(value, str):
        return False
    try:
        parse_iso8601(value), unified_strdate(value)
    except OverflowError:
        return False
    return True


def _text(value: Any) -> bool:
    """A string that encodes as UTF-8: yt-dlp quotes URLs (a UnicodeEncodeError on half an emoji, so a 500 here)."""
    if not isinstance(value, str):
        return False
    try:
        value.encode()
    except UnicodeEncodeError:
        return False
    return True


def _cipher(value: Any) -> bool:
    """A format's signatureCipher: a query string whose URL, if it has one, is one yt-dlp can request."""
    return _text(value) and all(requestable_url(url) for url in parse_qs(value).get("url", [])[:1])


def _caption_url(value: Any) -> bool:
    """A caption track's baseUrl, which yt-dlp joins to YouTube's: then one it can request."""
    try:
        return _text(value) and requestable_url(urljoin("https://www.youtube.com", value))
    except ValueError:  # an unclosed '['
        return False


_FORMAT = {
    "url": requestable_url, "signatureCipher": _cipher,
    "mimeType": str, "quality": str, "qualityLabel": str, "audioQuality": str,
    "audioTrack": {"id": str, "displayName": str},
    **dict.fromkeys(("bitrate", "averageBitrate", "approxDurationMs", "width", "height"), _number),
}
# The parts of a YouTube player response yt-dlp reads as these types without checking. Anything else is deleted, as
# missing (see _conform): yt-dlp fails on it (a TypeError, an AttributeError, an OverflowError, a ValueError or a
# UnicodeError, so a 500 here), and on a manifest URL it can't request (requestable_url) the video's extraction fails,
# an upstream_error.
_PLAYER_RESPONSE = {
    "videoDetails": {"keywords": [str], "shortDescription": str, "lengthSeconds": _number},
    "microformat": {"playerMicroformatRenderer": {
        "ownerProfileUrl": str, "uploadDate": _date, "lengthSeconds": _number,
        "liveBroadcastDetails": {"startTimestamp": _date, "endTimestamp": _date}}},
    "streamingData": {
        "formats": [_FORMAT], "adaptiveFormats": [_FORMAT],
        "hlsManifestUrl": requestable_url, "dashManifestUrl": requestable_url},
    "captions": {"playerCaptionsTracklistRenderer": {  # the languages key its subtitles, read as strings here too
        "captionTracks": [{"baseUrl": _caption_url, "vssId": _text, "languageCode": _text}],
        "translationLanguages": [{"languageCode": _text}]}},
}


def _youtube_player_response(invalid: Callable[[Any, str], Any], player_response: Any, video_id: str) -> Any:
    """YoutubeIE._invalid_player_response, the first thing yt-dlp does with each player response, once the response
    is made to fit _PLAYER_RESPONSE. The other video's id it returns for a response of another video is a string:
    yt-dlp joins those ids into a warning (a TypeError on one of another type, so a 500 here)."""
    _conform(player_response, _PLAYER_RESPONSE)
    other_id = invalid(player_response, video_id)
    return str(other_id) if other_id else other_id


# Where yt-dlp reads a video's series, season and episode in its super title.
_EPISODE = re.compile(r"(.+?)\s*S(\d+)\s*•?\s*E(\d+)")


def _super_title(value: Any) -> bool:
    """A video's super title: YouTube text (a simpleText, or runs) that has some, or nothing. yt-dlp searches it (see
    _EPISODE) without checking it has any (a TypeError), and reads the season and the episode it finds without
    checking int() can (a ValueError past 4300 digits), so a 500 here."""
    if not value:
        return True
    text = YoutubeBaseInfoExtractor._get_text(value)
    episode = _EPISODE.search(text) if text else None
    try:
        episode and (int(episode[2]), int(episode[3]))
    except ValueError:
        return False
    return bool(text)


# Where yt-dlp reads each toggle button's label, in which it matches a like count (a TypeError on a label that isn't a
# string, so a 500 here).
_LABELS = (("defaultText", "accessibility", "accessibilityData"), "accessibility",
           ("accessibilityData", "accessibilityData"))


def _toggles(value: Any) -> bool:
    """A toggleButtonRenderer whose toggle buttons' labels (see _LABELS) are strings. yt-dlp reads the renderer's
    values as its toggle buttons."""
    return all(isinstance(label, str) for label in traverse_obj(value, (..., _LABELS, "label")) if label)


def _segmented_toggles(value: Any) -> bool:
    """A segmentedLikeDislikeButtonRenderer whose toggle buttons' labels (see _LABELS) are strings."""
    return _toggles(traverse_obj(value, (..., "toggleButtonRenderer")))


def _badge(value: Any) -> bool:
    """A badge whose renderers' styles are strings: yt-dlp looks each one up in a dict (a TypeError on a list or an
    object, so a 500 here)."""
    return all(isinstance(style, str) for style in traverse_obj(value, (..., "style")))


def _count(value: Any) -> bool:
    """Not a count yt-dlp can't hold: it reads one past a float's range ('999…9K likes') as a float it rounds to an
    int (an OverflowError, so a 500 here)."""
    try:
        isinstance(value, str) and parse_count(value)
    except OverflowError:
        return False
    return True


def _count_text(value: Any) -> bool:
    """YouTube text (a simpleText, or runs) of a count, as _count."""
    return _count(YoutubeBaseInfoExtractor._get_text(value))


def _length(value: Any) -> bool:
    """A length yt-dlp can read: it reads one ('1.5 hours') as a number without checking it is one (a ValueError on
    '1.2.3 hours'), and a video's as a number (see _number) it formats as h:mm:ss and multiplies by a format's
    bitrate (a ValueError or an OverflowError past a float's range), so a 500 here."""
    try:
        return _number(parse_duration(value) or 0)
    except ValueError:
        return False


def _length_text(value: Any) -> bool:
    """YouTube text of a length (a chapter's), as _length."""
    return _length(YoutubeBaseInfoExtractor._get_text(value))


def _at(*path: Any) -> Any:
    """The schema (see _conform) of one value deep in a response, the path's last item: _at("a", ..., "b", str) is
    {"a": [{"b": str}]}."""
    *keys, schema = path
    for key in reversed(keys):
        schema = [schema] if key is ... else {key: schema}
    return schema


_WATCH = ("contents", "twoColumnWatchNextResults", "results", "results", "contents")  # a watch page's renderers
# The parts of a YouTube watch page's data (ytInitialData) yt-dlp reads as these types without checking. Anything else
# is deleted, as missing (see _conform): yt-dlp fails on it (an AttributeError, a TypeError, a ValueError or an
# OverflowError, so a 500 here).
_INITIAL_DATA = {
    **_at(*_WATCH, ..., {
        "videoPrimaryInfoRenderer": {
            "superTitleLink": _super_title, "viewCount": {"videoViewCountRenderer": {"viewCount": _count_text}},
            "badges": [_badge],
            "videoActions": {"menuRenderer": {"topLevelButtons": [{
                "toggleButtonRenderer": _toggles, "segmentedLikeDislikeButtonRenderer": _segmented_toggles,
                "segmentedLikeDislikeButtonViewModel": _at(
                    "likeButtonViewModel", "likeButtonViewModel", "toggleButtonViewModel", "toggleButtonViewModel",
                    "defaultButtonViewModel", "buttonViewModel", "accessibilityText", _count)}]}}},
        "videoSecondaryInfoRenderer": {
            "owner": {"videoOwnerRenderer": {
                "badges": [_badge], "subscriberCountText": _count_text,
                "attributedTitle": _at(  # a collab's channels: yt-dlp reads the first one's subscribers
                    "commandRuns", ..., "onTap", "innertubeCommand", "showDialogCommand", "panelLoadingStrategy",
                    "inlineContent", "dialogViewModel", "customContent", "listViewModel", "listItems", ...,
                    "listItemViewModel", "rendererContext", "accessibilityContext", "label", _count)}},
            "metadataRowContainer": {"metadataRowContainerRenderer": {"rows": [{"metadataRowRenderer": dict}]}}},
        "itemSectionRenderer": _at("contents", ..., "commentsEntryPointHeaderRenderer", "commentCount", _count_text),
    }),
    "engagementPanels": [{"engagementPanelSectionListRenderer": {
        "header": {"engagementPanelTitleHeaderRenderer": {"contextualInfo": _count_text}},  # the comments'
        "content": _at("macroMarkersListRenderer", "contents", ..., "macroMarkersListItemRenderer", "timeDescription",
                       _length_text)}}],  # the chapters'
    "frameworkUpdates": _at(  # the most replayed parts' (the heatmap's)
        "entityBatchUpdate", "mutations", ..., "payload", "macroMarkersListEntity", "markersList", "markers", ...,
        {"startMillis": _number, "durationMillis": _number}),
}


def _youtube_initial_data(ie: YoutubeIE, download: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """YoutubeIE._download_initial_data, but the watch page's data (from the page, or the next API) is made to fit
    _INITIAL_DATA, and a date (dateText) yt-dlp can't read is deleted, as missing: it reads one (an age it takes from
    today, '9 days ago', or a date) without checking a date holds it (an OverflowError, or a ValueError on a year past
    9999, so a 500 here)."""
    initial_data = download(*args, **kwargs)
    _conform(initial_data, _INITIAL_DATA)
    for info in traverse_obj(initial_data, (*_WATCH, ..., "videoPrimaryInfoRenderer", {dict})):
        try:
            ie._parse_time_text(ie._get_text(info, "dateText"), report_failure=False)
        except (ValueError, OverflowError):
            del info["dateText"]
    return initial_data


# The watch page's meta tags yt-dlp reads, where a player response has no length (a live stream's is '0') or no upload
# date, as these without checking.
_META = {"duration": _length, "uploadDate": _date}


def _youtube_meta(search: Callable[..., Any], name: Any, *args: Any, **kwargs: Any) -> Any:
    """YoutubeIE._html_search_meta, but a watch page's meta tag that doesn't fit _META is missing: yt-dlp fails on it
    (a ValueError or an OverflowError, so a 500 here)."""
    content = search(name, *args, **kwargs)
    check = _META.get(name) if isinstance(name, str) else None
    return None if content is not None and check and not check(content) else content


def cookie_file_problem(path: str) -> str | None:
    """Why a configured cookies.txt can't be used, or None. Each version of the file is parsed once."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return "does not exist"
    except OSError as exc:
        return f"can't be read ({exc.strerror})"
    if not stat.S_ISREG(st.st_mode):
        return "is not a file"
    if not os.access(path, os.R_OK):
        return "is not readable by the server"
    return _cookie_parse_problem(path, st.st_mtime_ns, st.st_ctime_ns, st.st_size)


@lru_cache(maxsize=16)
def _cookie_parse_problem(path: str, *_version: int) -> str | None:
    jar = YoutubeDLCookieJar(path)
    try:
        jar.load()
    except Exception as exc:  # LoadError, UnicodeDecodeError, ...: the detail (and the path) stays in the log
        log.warning("cookie file %s can't be loaded: %s", path, exc)
        return "is not a valid Netscape cookies.txt file"
    if unsendable := sorted({c.domain for c in jar if not _cookie_sendable(c)}):  # the rest of the file still works
        log.warning("cookie file %s has cookies for %s that yt-dlp can't send (a name or value that isn't Latin-1 "
                    "text): they are left out", path, ", ".join(unsendable))
    return None


def _cookie_sendable(cookie: Cookie) -> bool:
    """Whether yt-dlp can send the cookie. Its requests and urllib handlers write headers as Latin-1 and fail on a
    cookie whose name or value has a character past it, so every request carrying one did (a 500 upstream_error)."""
    try:
        f"{cookie.name}={cookie.value or ''}".encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


async def with_ydl(platform: str, fn: Callable[[yt_dlp.YoutubeDL], T], *, confirm_not_found: bool = False,
                   **ydl_opts: Any) -> T:
    """Run `fn(ydl)` in the worker pool, retrying configured connection profiles.

    confirm_not_found is opt-in for TikTok user lookups: its public profile page
    can report a real user as missing on some egresses. Confirm using the finite
    configured pool before returning 404. Other lookups keep missing content final.
    """
    from .portal import active_platform_pool

    proxies, cookies = active_platform_pool(platform)
    attempts = max(len(proxies), len(cookies), 1)
    loop = asyncio.get_running_loop()
    for index in range(attempts):
        options = dict(ydl_opts)
        if proxies:
            options["_dashboard_proxy"] = proxies[min(index, len(proxies) - 1)]
        if cookies:
            options["_dashboard_cookies"] = cookies[min(index, len(cookies) - 1)]
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_pool(), _run, platform, fn, options),
                timeout=get_settings().request_timeout,
            )
        except asyncio.TimeoutError as exc:
            error: ApiError = UpstreamError("Timed out waiting for the platform", code="timeout")
            cause: BaseException = exc
        except ApiError as exc:
            error, cause = exc, exc
        except (YoutubeDLError, RequestError) as exc:
            error, cause = translate_error(exc), exc
        retryable = _retry_profile(error) or (confirm_not_found and isinstance(error, NotFound))
        if index + 1 >= attempts or not retryable:
            raise error from cause
        log.warning("%s profile %d failed with %s; retrying profile %d", platform, index + 1, error.code, index + 2)
    raise AssertionError("unreachable")


def _retry_profile(error: ApiError) -> bool:
    return error.code in {
        "upstream_blocked", "upstream_error", "network_error", "proxy_error", "forbidden", "cookies_rejected",
        "no_profile_data",  # Instagram served a partial profile page rather than its resolved user.
    }


# --- error translation -------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_BLOCKED = re.compile(
    r"not a bot|rate.?limit|too many requests|http error 429|\b429\b|temporarily blocked|"
    r"try again later|unusual traffic|captcha",
    re.I,
)
_FORBIDDEN = re.compile(
    r"private|log ?in|sign ?in|cookies|age.?(restrict|gate|verif)|confirm your age|inappropriate|"
    r"members.?only|join this channel|premium|empty media response|requires authentication|"
    r"not available in your country|geo|http error 403|\b403\b",
    re.I,
)
_NOT_FOUND = re.compile(
    r"not found|unavailable|does not exist|doesn't exist|no longer available|has been removed|"
    r"been deleted|terminated|http error 404|\b404\b|no video|unable to find|could not find|"
    r"isn't available|is not available|not currently live|not live",
    re.I,
)


def _message(exc: BaseException) -> str:
    if isinstance(exc, ExtractorError):
        msg = exc.orig_msg
    else:
        msg = str(exc)
    msg = _ANSI.sub("", msg)
    msg = re.sub(r"^ERROR:\s*", "", msg)
    # Drop yt-dlp's bug-report boilerplate.
    return re.split(r";\s*please report this issue|\s*Confirm you are on the latest version", msg)[0].strip()


def _root(exc: BaseException) -> BaseException:
    """Unwrap DownloadError -> ExtractorError -> underlying cause."""
    seen = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        nxt = None
        if isinstance(exc, DownloadError) and exc.exc_info and exc.exc_info[1] is not None:
            nxt = exc.exc_info[1]
        elif isinstance(exc, ExtractorError) and exc.cause is not None and not isinstance(exc, UnsupportedError):
            nxt = exc.cause
        if nxt is None or nxt is exc:
            break
        exc = nxt
    return exc


def _timed_out(exc: BaseException | None) -> bool:
    """A socket/curl timeout in a TransportError's cause chain."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:  # requests/urllib3/curl_cffi
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _network_error(root: RequestError, message: str) -> ApiError:
    """No HTTP answer from the platform. The raw text has curl/urllib3 internals and the proxy's host:port, so
    it goes to the log only. A proxy refusing the tunnel (even with 403) says nothing about the content."""
    log.warning("network error (%s): %s", type(root).__name__, message)
    if isinstance(root, NoSupportingHandlers):
        return Misconfigured("yt-dlp has no network handler for this request; check SOCIALSCRAPE_PROXY")
    # Through a proxy every connection starts at the proxy, and the error text doesn't always name it.
    if isinstance(root, ProxyError) or re.search(r"\bproxy\b|\bsocks", message, re.I) or get_settings().proxy:
        return UpstreamUnreachable("Could not reach the platform through the configured proxy", code="proxy_error")
    if _timed_out(root) or re.search(r"timed out", message, re.I):
        return UpstreamError("Timed out waiting for the platform", code="timeout")
    return UpstreamUnreachable("Network error while reaching the platform")


def _without_proxy(message: str) -> str:
    """`message` with the configured proxy URL (and its user:password) cut out: it may reach API clients."""
    proxy = get_settings().proxy
    if not proxy or not message:
        return message
    authority = proxy.split("://", 1)[-1].removeprefix("//")
    userinfo = authority.rpartition("@")[0]
    # The bare user:password too, but not a bare user name: that may be a word of the message.
    for secret in sorted({proxy, authority, userinfo if ":" in userinfo else ""} - {""}, key=len, reverse=True):
        message = message.replace(secret, "<proxy>")
    return message


_PAGE_TITLE = re.compile(r"<title[^>]*>([^<]{1,60})", re.I)


def not_json(body: str) -> str:
    """What a body that isn't JSON is, for a 503's message: throttled answers are empty, bot checks and block pages
    an HTML page (TikTok's SlardarWAF challenge is titled "Please wait...", Akamai's denial "Access Denied")."""
    text = body.strip()
    if not text:
        return "an empty body"
    if text.startswith("<"):
        title = _PAGE_TITLE.search(text[:4096])
        return f'an HTML page ("{title.group(1).strip()}")' if title and title.group(1).strip() else "an HTML page"
    return f"a {len(text)}-character body"  # a cut-off answer, or text


def translate_error(exc: BaseException) -> ApiError:
    root = _root(exc)
    outer = exc.exc_info[1] if isinstance(exc, DownloadError) and exc.exc_info else exc
    message = _without_proxy(_message(outer if isinstance(outer, BaseException) else exc))

    if (isinstance(outer, UnsupportedError) or isinstance(root, UnsupportedError)
            or "no suitable extractor" in message.lower()):
        return BadRequest("URL is not supported by this endpoint", code="unsupported_url")
    if isinstance(outer, UserNotLive) or isinstance(root, UserNotLive):
        return NotFound(message or "User is not live", code="not_live")
    if isinstance(outer, GeoRestrictedError) or isinstance(root, GeoRestrictedError):
        return Forbidden(message, code="geo_restricted")
    if isinstance(outer, CookieLoadError) or isinstance(root, CookieLoadError):
        # Checked before _FORBIDDEN, which would read "failed to load cookies" as login-gated content.
        return Misconfigured("The server's cookies for this platform could not be loaded", code="cookies_error")
    if isinstance(root, json.JSONDecodeError) and isinstance(root.doc, str):
        # Platforms answer throttled API calls with an empty 200 body, and blocked ones with a web page (a bot check,
        # a CDN's denial) where the JSON belongs: yt-dlp's "Failed to parse JSON" for either is a block, not a 500.
        if not root.doc.strip():
            return UpstreamBlocked("The platform returned an empty response; try again later")
        if root.doc.lstrip().startswith("<"):
            return UpstreamBlocked(f"The platform answered with {not_json(root.doc)} instead of data, as it does "
                                   "for requests it is blocking or bot-checking; try again later")
    if isinstance(root, HTTPError):
        status = root.status
        if status == 429:
            return UpstreamBlocked(message)
        if status in (401, 403):
            return Forbidden(message)
        if status in (404, 410):
            return NotFound(message)
    if isinstance(root, (TransportError, NoSupportingHandlers)):
        return _network_error(root, message)
    if _BLOCKED.search(message):
        return UpstreamBlocked(message)
    if _FORBIDDEN.search(message):
        return Forbidden(message)
    if _NOT_FOUND.search(message):
        return NotFound(message)
    log.warning("unclassified yt-dlp error: %s", message)
    return UpstreamError(message or type(root).__name__)
