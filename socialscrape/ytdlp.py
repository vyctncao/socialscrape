"""Async bridge to yt-dlp.

yt-dlp is blocking, so every call runs in a bounded thread pool with a fresh
`YoutubeDL` instance (instances are not safe to share across threads).
yt-dlp exceptions are translated into `ApiError`s so routes never see them.

Entry point: with_ydl(platform, fn) runs fn(ydl), for access to extractor
internals and yt-dlp's networking stack.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Callable, TypeVar

import yt_dlp
from yt_dlp.cookies import CookieLoadError, YoutubeDLCookieJar
from yt_dlp.networking.exceptions import HTTPError, NoSupportingHandlers, ProxyError, RequestError, TransportError
from yt_dlp.utils import (
    DownloadError,
    ExtractorError,
    GeoRestrictedError,
    UnsupportedError,
    UserNotLive,
    YoutubeDLError,
)

from .config import get_settings
from .errors import (ApiError, BadRequest, Forbidden, Misconfigured, NotFound, UpstreamBlocked, UpstreamError,
                     UpstreamUnreachable)

log = logging.getLogger("socialscrape.ytdlp")
T = TypeVar("T")

_executor: ThreadPoolExecutor | None = None

# Only the platform's own extractors may run, so a stray URL can't fall through
# to yt-dlp's generic extractor (or another site's) on a platform route.
ALLOWED_EXTRACTORS = {
    "youtube": [r"youtube(:.*)?"],
    "tiktok": [r"tiktok(:.*)?"],
    "instagram": [r"instagram(:.*)?"],
}


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


def base_options(platform: str) -> dict[str, Any]:
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
    if s.proxy:
        opts["proxy"] = s.proxy
    if cookie_file := s.cookie_file(platform):
        if problem := cookie_file_problem(cookie_file):
            raise Misconfigured(f"The server's {platform} cookie file (SOCIALSCRAPE_COOKIES_{platform.upper()}) "
                                f"{problem}", code="cookies_error")
        opts["cookiefile"] = cookie_file
    elif s.cookies_from_browser:
        browser, _, profile = s.cookies_from_browser.partition(":")
        opts["cookiesfrombrowser"] = (browser, profile or None, None, None)
    return opts


def _run(platform: str, fn: Callable[[yt_dlp.YoutubeDL], T], opts: dict[str, Any]) -> T:
    merged = base_options(platform)
    extractor_args = opts.pop("extractor_args", None)
    merged.update(opts)
    if extractor_args:
        merged["extractor_args"] = extractor_args
    with yt_dlp.YoutubeDL(merged) as ydl:
        try:
            return fn(ydl)
        finally:
            # The operator's cookies.txt is input only. Without this, close() saves the session's jar over it
            # with a plain open(path, "w") from every worker thread at once, truncating it for its readers.
            ydl.params["cookiefile"] = None


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
    try:
        YoutubeDLCookieJar(path).load()
    except Exception as exc:  # LoadError, UnicodeDecodeError, ...: the detail (and the path) stays in the log
        log.warning("cookie file %s can't be loaded: %s", path, exc)
        return "is not a valid Netscape cookies.txt file"
    return None


async def with_ydl(platform: str, fn: Callable[[yt_dlp.YoutubeDL], T], **ydl_opts: Any) -> T:
    """Run `fn(ydl)` in the worker pool and translate any yt-dlp error."""
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_pool(), _run, platform, fn, ydl_opts),
            timeout=get_settings().request_timeout,
        )
    except ApiError:
        raise
    except asyncio.TimeoutError as exc:
        raise UpstreamError("Timed out waiting for the platform", code="timeout") from exc
    except (YoutubeDLError, RequestError) as exc:
        raise translate_error(exc) from exc


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
    if isinstance(root, json.JSONDecodeError) and not root.doc.strip():
        # Platforms answer throttled API calls with an empty 200 body.
        return UpstreamBlocked("The platform returned an empty response; try again later")
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
