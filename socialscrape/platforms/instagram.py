"""Instagram routes, built on what yt-dlp's Instagram extractors download anonymously.

Posts: InstagramIE runs unchanged (session setup, get_ruling check, the logged-out
`PolarisLoggedOutDesktopWWWPostRootContentQuery` GraphQL call, /p/ page fallback)
while the raw media it parses is recorded, so fields yt-dlp drops (owner, caption
ids, tagged users, the first comments) are still available.
With saved account cookies, instagram_authenticated reads the signed-in client's
profile/feed/search queries and media-info API. Its page is a client shell and
requires different request tokens and response shapes from the logged-out app.
Without cookies, profiles, tags and highlights use the pages InstagramUserIE (profile page, plus
its /reels/ tab), InstagramTagIE (/explore/tags/ -> /popular/) and InstagramStoryIE
(/stories/highlights/<id>/) download, fetched through those extractor instances and
read from the Relay data Instagram embeds for logged-out visitors.
Posts and reels listings go on past their first page the way the logged-out web
client scrolls (the tab's own GraphQL query with `after`), and each item is
completed by InstagramIE's logged-out post query (date, caption, comment count).
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import logging
import math
import re
import secrets
import threading
import time
import unicodedata
import urllib.parse
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import yt_dlp
from fastapi import APIRouter, Query, Request
from yt_dlp.extractor.instagram import InstagramBaseIE, InstagramIE, InstagramStoryIE, _id_to_pk
from yt_dlp.networking import HEADRequest
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError, parse_duration, str_or_none, traverse_obj, unescapeHTML, urlencode_postdata

from .. import transcribe
from ..config import get_settings
from ..errors import (
    ApiError,
    BadRequest,
    ErrorResponse,
    Forbidden,
    Misconfigured,
    NotFound,
    NotSupported,
    UpstreamBlocked,
    UpstreamError,
    error_responses,
    require_one,
)
from ..registry import sc_get
from ..util import compact, iso_utc, parse_count, truthy
from ..ytdlp import with_ydl
from ._instagram_shapes import HIGHLIGHT_REEL, IMAGE_VERSIONS, VIDEO_VERSION, prune

router = APIRouter()
log = logging.getLogger("socialscrape.instagram")

PLATFORM = "instagram"
WEB = "https://www.instagram.com"
RULING_PATH = "/api/v1/web/get_ruling_for_content/"
COOKIE_NOTE = "requires SOCIALSCRAPE_COOKIES_INSTAGRAM; not verified anonymously"

_SHORTCODE = re.compile(r"[A-Za-z0-9_-]{5,64}")
_SHORTCODE_CHARS = re.compile(r"[A-Za-z0-9_-]{1,64}")  # _id_to_pk's alphabet, up to a shortcode's length
# A link from the app's share sheet (/share/reel/<id>, /share/<id>), whose id (as long as a shortcode, so that
# '/share/reel/' isn't one) redirects to the post.
_SHARE_URL = re.compile(r"https://www\.instagram\.com/share/(?:[a-z]+/)?[A-Za-z0-9_-]{5,64}/?(?:[?#].*)?")
# No leading/trailing '.' and no '..', as on Instagram (and so '.'/'..' can't collapse /<handle>/ to the homepage).
_HANDLE = re.compile(r"(?!\.)(?!.*\.\.)[A-Za-z0-9._]{1,30}(?<!\.)")
# The address of a profile, one of its tabs, its follower or following list (a modal over the profile page) or one
# of its saved collections; or that of its story, or of one item in it (/stories/<handle>/<pk>/)
_PROFILE_URL = re.compile(r"https://www\.instagram\.com/(?:(?P<user>[A-Za-z0-9._]{1,30})"
                          r"(?:/(?:reels|tagged|guides|featured|followers|following"
                          r"|saved(?:/all-posts|/[^/?#]+/[0-9]+)?))?"
                          r"|stories/(?P<story>[A-Za-z0-9._]{1,30})(?:/[0-9]+)?)/?(?:[?#].*)?")
# Instagram routes that look like handles: /challenge/ is the checkpoint page, /graphql/ answers 400, and the
# login-only app routes always redirect to the login page (which would read as a rate limit). Other app pages
# are told apart by their pageID (missing_profile_error).
_RESERVED_PATHS = {"p", "reel", "reels", "tv", "explore", "stories", "accounts", "popular", "direct", "about",
                   "challenge", "graphql", "qr", "nametag", "press", "your_activity", "notifications",
                   "meta_verified"}
_HIGHLIGHT_ID = re.compile(r"(?:highlight:)?([0-9]{5,19})")  # highlight ids are 64-bit integers (ASCII digits only)
# The app's share link for a highlight: /s/<base64 of 'highlight:<id>'>?story_media_id=<item>&igsh=…
_HIGHLIGHT_SHARE = re.compile(r"https://www\.instagram\.com/s/(aGlnaGxpZ2h0[A-Za-z0-9]*(?:=|%3[Dd])*)/?(?:[?#].*)?")
# A tag page's address, or that of the Popular page Instagram sends logged-out visitors on to (/popular/<slug>/).
_TOPIC_URL = re.compile(r"https://www\.instagram\.com/(?:explore/tags|popular)/([^/?#]+)/?(?:[?#].*)?")
# instagr.am is Instagram's old short domain: instagr.am/p/<code>/ redirects to the same path on www.
_INSTAGRAM_HOSTS = {"www.instagram.com", "instagram.com", "m.instagram.com", "instagr.am"}
# A value that is an Instagram address rather than a handle, id or topic ('instagr.am' alone may be a handle)
_INSTAGRAM_LINK = re.compile(r"instagram\.com|instagr\.am/", re.I)
_PAGE_ID = re.compile(r'"pageID":"(\w+)"')
# A profile Instagram hides from logged-out visitors (age-gated brands): its page's props carry a "gating" object,
# or the server-rendered page shows the "Restricted profile" icon.
_GATING = re.compile(r'"gating":(\{[^{}]*\})')
_RESTRICTED_ICON = re.compile(r'aria-label="Restricted profile"|<title>Restricted profile</title>')
_POSTS_IN_META = re.compile(r"(\d[\d.,]*)\s*([KMB])?\s+Posts\b", re.I)
_GRAPH_TYPE = {1: "GraphImage", 2: "GraphVideo", 8: "GraphSidecar"}
_TOPIC_KEYS = ("xig_logged_out_popular_search_media_info", "ig_popular_search_category_hub")
_PREVIEW_COMMENTS = 3


# --- input validation ----------------------------------------------------------


def canonical_url(value: str) -> str:
    """Add https:// (also to a protocol-relative '//www.instagram.com/…'), lower-case scheme and host, and map
    instagram.com, m.instagram.com and instagr.am to www."""
    if not re.match(r"https?://", value, re.I):
        value = "https://" + value.removeprefix("//")
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return value
    host = parts.netloc.lower()
    if host in _INSTAGRAM_HOSTS:
        host = "www.instagram.com"
    return urllib.parse.urlunsplit(("https", host, parts.path, parts.query, parts.fragment))


def post_url(url: str | None) -> tuple[str, str]:
    """Post/reel URL or bare shortcode -> (shortcode, canonical /p/ URL); a share link -> ('', the link), which
    fetch_post follows to its post. 400 otherwise."""
    value = (url or "").strip()
    if not value:
        raise BadRequest("url is required")
    if _SHORTCODE.fullmatch(value):
        return value, f"{WEB}/p/{value}/"
    value = canonical_url(value)
    if _SHARE_URL.fullmatch(value):
        return "", value
    m = InstagramIE._match_valid_url(value)
    if not m:
        raise BadRequest("Not an Instagram post or reel URL", code="unsupported_url")
    if not _SHORTCODE_CHARS.fullmatch(m.group("id")):  # InstagramIE's id group takes any [^/?#&]+
        raise BadRequest("Not a valid Instagram shortcode", code="unsupported_url")
    return m.group("id"), f"{WEB}/p/{m.group('id')}/"


def clean_handle(handle: str | None) -> str:
    """'@jane', 'jane', 'jane/' or a profile URL (optionally a tab of it, its /followers/ or /following/ list or a
    saved collection), or that of jane's story (/stories/jane/) -> 'jane'. 400 for anything else. The '@' may be
    the full-width '＠' of Japanese and Chinese keyboards."""
    value = (handle or "").strip()
    if _INSTAGRAM_LINK.search(value):
        m = _PROFILE_URL.fullmatch(canonical_url(value))
        # /stories/highlights/<id>/ is a highlight, not the story of an account named 'highlights'
        value = (m["user"] or m["story"]) if m and (m["story"] or "").lower() != "highlights" else ""
    value = value.lstrip("@＠﹫").rstrip("/")
    if not _HANDLE.fullmatch(value) or value.lower() in _RESERVED_PATHS:
        raise BadRequest("Not a valid Instagram handle", code="bad_handle")
    return value


def clean_highlight_id(value: str | None) -> str:
    """'18382085791177637', 'highlight:1838...', a /stories/highlights/<id>/ URL or the app's share link
    (/s/<base64 of 'highlight:<id>'>) -> the numeric id."""
    value = (value or "").strip()
    if _INSTAGRAM_LINK.search(value):
        value = canonical_url(value)
    if m := _HIGHLIGHT_SHARE.fullmatch(value):
        code = urllib.parse.unquote(m.group(1)).rstrip("=")
        try:
            value = base64.b64decode(code + "=" * (-len(code) % 4), validate=True).decode("latin-1")
        except ValueError:  # binascii.Error: a cut-off link
            raise BadRequest("Not a valid Instagram highlight share link", code="bad_id") from None
    if m := InstagramStoryIE._match_valid_url(value):
        if m.group("id") and not _HIGHLIGHT_ID.fullmatch(m.group("id")):
            raise BadRequest("Not a valid Instagram highlight id", code="bad_id")
        if m.group("user") != "highlights" or not m.group("id"):
            raise BadRequest("Not an Instagram highlight URL", code="unsupported_url")
        return m.group("id")
    if m := _HIGHLIGHT_ID.fullmatch(value):
        return m.group(1)
    raise BadRequest("Not a valid Instagram highlight id", code="bad_id")


def clean_topic(query: str | None, *, single_word: bool = False) -> str:
    """Popular-topic query or hashtag ('#', or the full-width '＃' Japanese and Chinese keyboards type, optional),
    lower-cased. The address of its /explore/tags/<tag>/ or /popular/<slug>/ page gives that tag or slug. 400 if
    it can't be a /explore/tags/ path.

    Tags are case-insensitive, but only the lower-case /explore/tags/ and /popular/ paths exist
    (/explore/tags/FunnyDogs/ redirects to login, /popular/FunnyDogs/ is a 404 page). 'İ' becomes 'i':
    str.lower() makes it 'i' + U+0307, a path that doesn't exist. Words may hold combining marks (Devanagari
    vowel signs, Tamil virama), which \\w doesn't match. Decomposed (NFD) spellings are composed first: only
    /explore/tags/espa%C3%B1a/ exists, not /explore/tags/espan%CC%83a/."""
    value = (query or "").strip()
    if _INSTAGRAM_LINK.search(value) and (m := _TOPIC_URL.fullmatch(canonical_url(value))):
        value = urllib.parse.unquote(m.group(1)).strip()
    value = unicodedata.normalize("NFC", re.sub(r"\s+", " ", value.lstrip("#＃﹟")).strip())
    value = value.replace("İ", "i").lower()
    if not value or len(value) > 100 or re.search(r"[/?#&%\\]", value):
        raise BadRequest("Not a valid Instagram topic/hashtag", code="bad_query")
    if single_word and not all(c.isalnum() or c == "_" or unicodedata.category(c).startswith("M") for c in value):
        raise BadRequest("A hashtag is a single word (letters, digits, underscores)", code="bad_query")
    return value


# --- raw Instagram data, via the extractors ----------------------------------------


def page_relay(html: str) -> list[dict[str, Any]]:
    """Relay query results Instagram prefetches into logged-out pages (InstagramIE's SJS parsing).

    Some pages name the module 'RelayPrefetchedStreamCache@<hash>'; InstagramIE's exact match misses those."""
    results = []
    for blob in InstagramIE._SJS_RE.findall(html):
        try:
            data = json.loads(blob)
        except (ValueError, RecursionError):  # not JSON, or nested deeper than Python reads it
            continue
        for entry in traverse_obj(data, (
                "require", ..., ..., ..., "__bbox", "require",
                lambda _, v: v[0].partition("@")[0] == "RelayPrefetchedStreamCache", ...)):
            if result := traverse_obj(entry, (1, "__bbox", "result", {dict})):
                results.append(result)
    return results


def polaris_media(bodies: list[tuple[str, str]]) -> dict[str, Any] | None:
    """The logged-out `xig_polaris_media` InstagramIE parsed: GraphQL response, else the /p/ page."""
    for url, body in bodies:
        path = urllib.parse.urlparse(url).path
        if path == "/api/graphql":
            try:
                media = traverse_obj(json.loads(body), ("data", "xig_polaris_media", {dict}))
            except ValueError:
                continue
        elif path.startswith("/p/"):
            media = traverse_obj(page_relay(body), (..., "data", "xig_polaris_media", {dict}, any))
        else:
            continue
        if traverse_obj(media, ("if_not_gated_logged_out", {dict})):
            return media
    return None


class _Recorder:
    """Keeps every body an extractor instance downloads, and HTTP error codes by URL path."""

    def __init__(self, ydl: yt_dlp.YoutubeDL, ie: Any) -> None:
        self.bodies: list[tuple[str, str]] = []
        self.http_errors: dict[str, int] = {}
        download, urlopen = ie._download_webpage_handle, ydl.urlopen

        def recording_download(*args: Any, **kwargs: Any) -> Any:
            res = download(*args, **kwargs)
            if res:
                self.bodies.append((res[1].url, res[0]))
            return res

        def recording_urlopen(req: Any) -> Any:
            try:
                return urlopen(req)
            except HTTPError as exc:
                self.http_errors[urllib.parse.urlparse(getattr(req, "url", str(req))).path] = exc.status
                raise

        ie._download_webpage_handle = recording_download
        ydl.urlopen = recording_urlopen


class RestrictedProfile(Forbidden):
    """A profile Instagram hides from logged-out visitors ("Restricted profile", e.g. age-gated alcohol brands).
    Keeps the page, whose og:description still has the follower and post counts."""

    code = "restricted_profile"

    def __init__(self, message: str, html: str) -> None:
        super().__init__(message)
        self.html = html


class SessionRejected(Misconfigured):
    """Instagram sent a request made with the server's session cookies to its login page: the session is dead.
    Retrying never helps (unlike the logged-out rate limit the same redirect means without a session)."""

    code = "cookies_rejected"

    def __init__(self) -> None:
        from ..portal import active_platform_pool
        _, dashboard_cookies = active_platform_pool(PLATFORM)
        source = ("Platform settings" if dashboard_cookies else "SOCIALSCRAPE_COOKIES_INSTAGRAM"
                  if get_settings().cookie_file(PLATFORM) else "SOCIALSCRAPE_COOKIES_FROM_BROWSER")
        super().__init__(f"Instagram rejected the server's session cookies ({source}): it sent the logged-in "
                         "request to its login page, so the session has expired or was logged out. Replace them "
                         "with a fresh session's cookies")


def _check_session(logged_in: bool, rec: _Recorder) -> None:
    """SessionRejected if a request sent with a session cookie (yt-dlp's own logged-in test, taken before the
    request) was redirected to the login page."""
    if logged_in and any(InstagramBaseIE._is_login_redirect(url) for url, _ in rec.bodies):
        raise SessionRejected() from None


_LOGIN_WALL = "Instagram redirected the anonymous request to its login page (logged-out rate limit); retry later " \
              "or configure cookies"
_EMPTY_PAGE = "Instagram returned an empty page, as it does for anonymous requests it is throttling; try again later"
_NOT_APP = ("Instagram answered with a page that isn't its web app (a block page), as it does for anonymous requests "
            "it is throttling; try again later")


def _follow_share(ie: Any, url: str) -> str:
    """The /p/ URL of the post a share link redirects to. yt-dlp leaves these links to its generic extractor,
    which follows the redirect, and the service runs Instagram's extractors only."""
    logged_in = ie._is_logged_in
    final = canonical_url(ie._request_webpage(HEADRequest(url), "share", "Following share link",
                                              impersonate=ie._can_impersonate).url)
    m = InstagramIE._match_valid_url(final)
    if m and _SHORTCODE_CHARS.fullmatch(m.group("id")):
        return f"{WEB}/p/{m.group('id')}/"
    if final.startswith(f"{WEB}/") and InstagramBaseIE._is_login_redirect(final):  # it only looks at the path
        raise SessionRejected() if logged_in else UpstreamBlocked(_LOGIN_WALL)
    raise NotFound("This share link does not lead to an Instagram post or reel")  # an unknown id answers 200


def _unrendered(bodies: list[tuple[str, str]]) -> UpstreamBlocked | None:
    """The block a recorded Instagram page stands for: an empty body, or HTML that isn't a page of its web app (every
    one names its pageID). API answers and the login page (whose meaning depends on the session) are the caller's;
    the home page, which InstagramIE reads its LSD token from logged out, is checked."""
    for url, body in bodies:
        parts = urllib.parse.urlparse(url)
        if (parts.netloc.lower() not in _INSTAGRAM_HOSTS
                or parts.path.startswith(("/api/", "/graphql", "/accounts/login"))):
            continue
        if not body.strip():
            return UpstreamBlocked(_EMPTY_PAGE)
        if body.lstrip().startswith("<") and not _PAGE_ID.search(body):
            return UpstreamBlocked(_NOT_APP)
    return None


def _post_flow(url: str) -> Callable[[yt_dlp.YoutubeDL], tuple[dict[str, Any], dict[str, Any] | None]]:
    def go(ydl: yt_dlp.YoutubeDL) -> tuple[dict[str, Any], dict[str, Any] | None]:
        ie = ydl.get_info_extractor("Instagram")
        target = _follow_share(ie, url) if _SHARE_URL.fullmatch(url) else url
        rec = _Recorder(ydl, ie)
        products: list[dict[str, Any]] = []
        extract_product = ie._extract_product

        def capture(info: Any, *args: Any, **kwargs: Any) -> Any:
            products.append(info[0] if isinstance(info, list) else info)
            return extract_product(info, *args, **kwargs)

        ie._extract_product = capture
        logged_in = ie._is_logged_in
        try:
            ie.extract(target)
        except ExtractorError:
            # yt-dlp reports deleted/nonexistent posts as an "empty media response";
            # the accessibility check it ran first answered 404 "Media cannot be found".
            if rec.http_errors.get(RULING_PATH) == 404:
                raise NotFound("Post not found") from None
            # A dead session's media/info call lands on the login page, which yt-dlp reports as "Failed to parse JSON".
            _check_session(logged_in, rec)
            # The page it reads its LSD token from came back empty or as a block page: "Unable to extract LSD token"
            if blocked := _unrendered(rec.bodies):
                raise blocked from None
            raise
        if not products:
            raise NotFound("No data returned for this post")
        return products[0], polaris_media(rec.bodies)

    return go


async def fetch_post(url: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(media item yt-dlp extracted, logged-out xig_polaris_media wrapper or None when logged in)."""
    return await with_ydl(PLATFORM, _post_flow(url))


def _page_flow(ie_name: str, url: str, item_id: str, *, allow_home: bool = False
               ) -> Callable[[yt_dlp.YoutubeDL], tuple[str, str]]:
    def go(ydl: yt_dlp.YoutubeDL) -> tuple[str, str]:
        ie = ydl.get_info_extractor(ie_name)
        logged_in = ie._is_logged_in
        html, urlh = ie._download_webpage_handle(url, item_id, impersonate=ie._can_impersonate)
        requested_home = (allow_home and urllib.parse.urlsplit(url).path == "/"
                          and urllib.parse.urlsplit(urlh.url).path == "/"
                          and urllib.parse.urlsplit(urlh.url).netloc.lower() in _INSTAGRAM_HOSTS)
        if logged_in and InstagramBaseIE._is_login_redirect(urlh.url) and not requested_home:
            raise SessionRejected()  # not the logged-out rate limit fetch_page reports for this redirect
        return html, urlh.url

    return go


def _check_page(html: str, final_url: str, what: str, *, allow_home: bool = False) -> list[dict[str, Any]]:
    """The relay results of a logged-out page, unless it isn't one: NotFound for an address that left Instagram or
    its error page, UpstreamBlocked for the login page Instagram sends rate-limited anonymous visitors to."""
    if urllib.parse.urlparse(final_url).netloc.lower() not in _INSTAGRAM_HOSTS:
        # Reserved names such as /developer/ redirect off Instagram (developers.facebook.com).
        raise NotFound(f"{what} not found")
    if (InstagramBaseIE._is_login_redirect(final_url)
            and not (allow_home and urllib.parse.urlsplit(final_url).path == "/")):
        raise UpstreamBlocked(_LOGIN_WALL)
    if not html.strip():
        raise UpstreamBlocked(_EMPTY_PAGE)
    page = _PAGE_ID.search(html)
    if page and page.group(1) == "httpErrorPage":
        raise NotFound(f"{what} not found")
    return page_relay(html)


async def fetch_page(ie_name: str, url: str, item_id: str, what: str) -> tuple[str, list[dict[str, Any]]]:
    """Download a logged-out page the way `ie_name` does -> (html, relay results)."""
    def go(ydl: yt_dlp.YoutubeDL) -> tuple[str, list[dict[str, Any]]]:
        html, final_url = _page_flow(ie_name, url, item_id)(ydl)
        # Classify the HTTP-200 login/block page before leaving the retry layer,
        # so a refused primary can advance to the configured backup profiles.
        return html, _check_page(html, final_url, what)

    return await with_ydl(PLATFORM, go)


def profile_restriction(html: str) -> str | None:
    """Why Instagram hides a profile page from logged-out visitors ('Restricted profile: You must be 21 years old
    or over to see this profile'), or None."""
    for blob in _GATING.findall(html):
        try:
            gating = json.loads(blob)
        except (ValueError, RecursionError):  # not JSON, or nested deeper than Python reads it
            continue
        return ": ".join(s for s in (gating.get("title"), gating.get("description"))
                         if isinstance(s, str) and s) or "Restricted profile"
    return "Restricted profile" if _RESTRICTED_ICON.search(html) else None


def missing_profile_error(html: str) -> ApiError:
    """Why a page fetched as a profile has no xig_user_by_username: an app page, a restricted profile or unknown."""
    page = _PAGE_ID.search(html)
    if page and page.group(1) != "profilePage":
        if "login" in page.group(1).lower():  # the login page served in place of the profile
            return UpstreamBlocked(_LOGIN_WALL)
        # An app page served at /<name>/ (e.g. pageID MetaVerifiedUpdate).
        return NotFound("Profile not found: Instagram serves an app page, not a profile, at this name")
    if reason := profile_restriction(html):
        return RestrictedProfile(f"Instagram hides this profile from logged-out visitors ({reason})", html)
    if page is None:  # every page of Instagram's web app names its pageID: this is a block page served in its place
        return UpstreamBlocked(_EMPTY_PAGE if not html.strip() else _NOT_APP)
    return UpstreamError("Instagram returned the profile page without profile data", code="no_profile_data")


def _profile_user(html: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """A profile page's xig_user_by_username: its root query (bio, counts, highlights) merged with the tab's (posts
    timeline or reels clips)."""
    user: dict[str, Any] = {}
    for part in traverse_obj(results, (..., "data", "xig_user_by_username", {dict})):
        user.update(part)
    if not user.get("pk"):
        raise missing_profile_error(html)
    return user


async def fetch_profile(handle: str, tab: str = "", *, highlights: bool = False) -> tuple[dict[str, Any], str]:
    """The profile's appropriate client: authenticated queries or logged-out Relay data."""
    url = f"{WEB}/{handle}/{tab + '/' if tab else ''}"

    def go(ydl: yt_dlp.YoutubeDL) -> tuple[dict[str, Any], str]:
        html, final_url = _page_flow("InstagramUser", url, handle)(ydl)
        results = _check_page(html, final_url, "Profile")
        ie = ydl.get_info_extractor("InstagramUser")
        if ie._is_logged_in:
            from . import instagram_authenticated

            return instagram_authenticated.profile(ie, html, final_url, handle, tab, highlights=highlights), html
        # Missing Relay data on an app/block page can also be a retryable
        # refusal. Validate the resolved user inside the same profile attempt.
        return _profile_user(html, results), html

    # Instagram can serve its missing-profile page to one connection while
    # another resolves the same public creator. Confirm within the finite pool.
    return await with_ydl(PLATFORM, go, confirm_not_found=True)


async def fetch_topic(query: str) -> tuple[dict[str, Any], str]:
    """InstagramTagIE's /explore/tags/<tag> page (redirected to /popular/<tag>/) -> (data, html).

    Instagram sends tags it has no Popular page for (and most non-ASCII ones) from /explore/tags/ to the
    login page, so that redirect is retried once on the /popular/ page it normally lands on, which then
    answers with a real 404 page. (With a rejected session the /popular/ page redirects too.)"""
    quoted = urllib.parse.quote(query)
    try:
        html, results = await fetch_page("InstagramTag", f"{WEB}/explore/tags/{quoted}/", query, "Topic")
    except (UpstreamBlocked, SessionRejected):
        html, results = await fetch_page("InstagramTag", f"{WEB}/popular/{quoted}/", query, "Topic")
    data = next((r["data"] for r in results
                 if isinstance(r.get("data"), dict) and any(k in r["data"] for k in _TOPIC_KEYS)), None)
    if data is None:
        if not _PAGE_ID.search(html):  # not a page of Instagram's web app at all
            raise UpstreamBlocked(_NOT_APP)
        raise NotFound("Instagram has no popular page for this topic")
    return data, html


def _api_json(ydl: yt_dlp.YoutubeDL, ie: Any, url: str, item_id: str, note: str, **kwargs: Any) -> Any:
    """`ie._download_json(url)`. A logged-in call that lands on the login page fails as "Failed to parse JSON"
    in yt-dlp; that is reported as the rejected session it is."""
    rec = _Recorder(ydl, ie)
    logged_in = ie._is_logged_in
    try:
        return ie._download_json(url, item_id, note, headers=ie._api_headers, **kwargs)
    except ExtractorError:
        _check_session(logged_in, rec)
        raise


def _comments_api_flow(shortcode: str) -> Callable[[yt_dlp.YoutubeDL], Any]:
    """The media/<pk>/comments/ call InstagramIE's comment extraction makes (answers only with cookies)."""
    def go(ydl: yt_dlp.YoutubeDL) -> Any:
        ie = ydl.get_info_extractor("Instagram")
        return _api_json(
            ydl, ie, f"{ie._API_BASE_URL}/media/{_id_to_pk(shortcode)}/comments/?can_support_threading=true"
            "&permalink_enabled=false", shortcode, "Downloading comments info")

    return go


def _reels_media_flow(highlight_id: str) -> Callable[[yt_dlp.YoutubeDL], Any]:
    def go(ydl: yt_dlp.YoutubeDL) -> Any:
        ie = ydl.get_info_extractor("InstagramStory")
        return _api_json(
            ydl, ie, f"{ie._API_BASE_URL}/feed/reels_media/?reel_ids=highlight:{highlight_id}", highlight_id,
            "Downloading highlight", impersonate=ie._can_impersonate and ie._is_web_app)

    return go


# --- the logged-out web client's own GraphQL calls ----------------------------------------
# A profile page names the Relay queries it preloads (its posts or reels tab) with their ids in expectedPreloaders,
# and scrolling the tab repeats that query with the connection's end_cursor as `after`: anonymous pagination. The
# ids below were the pages' in 2026-10, for a page that doesn't name one; the post query's is InstagramIE's.
_TAB_QUERY = {"": "PolarisLoggedOutDesktopWWWProfilePostsTabContentQuery",
              "reels": "PolarisLoggedOutDesktopWWWProfileReelsTabContentQuery"}
_TAB_DOC_ID = {"": "29483846714553007", "reels": "27838951732404191"}
_TAB_CONNECTION = {"": "polaris_ordered_timeline_connection", "reels": "polaris_clips_connection"}
_POST_QUERY, _POST_DOC_ID = "PolarisLoggedOutDesktopWWWPostRootContentQuery", "27130156389949648"
# A Relay provider of the posts-tab query: true selects the fields Instagram serves search crawlers, among them
# each post's like_count and play_count (the reels tab has those without it)
_CRAWLER = "__relay_internal__pv__PolarisSEOIsCrawlerrelayprovider"
_PRELOADER = re.compile(r'"queryID":"(\d+)","variables":(\{[^{}]*\}),"queryName":"(\w+)"')
_LSD = re.compile(r'\["LSD",\[\],\{"token":"([^"]+)"')
_GRAPHQL_URL = f"{WEB}/api/graphql"
_RETRY_DELAY = 2.0  # seconds before the one retry of an empty or non-JSON GraphQL answer
# Post queries (one request each) a listing page makes for its items' dates, captions and comment counts. They stop
# after this many seconds so the page still answers within the minute API clients usually wait.
_DETAILS_BUDGET = 40.0


_PLEASE_WAIT = re.compile(r"please wait", re.I)  # "Please wait a few minutes before you try again."
_KEPT_SECONDS = 900.0  # a post query's media, and a posts-tab caption, are reused this long
_KEPT_ITEMS = 1024  # of each, the oldest dropped first


class _Kept:
    """key -> value for _KEPT_SECONDS, the oldest dropped past _KEPT_ITEMS. Thread-safe: listings fill it from yt-dlp's
    workers. lock(key) is the lock of the key's stripe, held while one listing asks Instagram for it, so a listing
    running alongside waits and reuses the answer instead of asking again."""

    def __init__(self) -> None:
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._stripes = [threading.Lock() for _ in range(64)]

    def get(self, key: str) -> Any:
        with self._lock:
            hit = self._items.get(key)
            if hit and time.monotonic() - hit[0] < _KEPT_SECONDS:
                return hit[1]
            self._items.pop(key, None)
            return None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = (time.monotonic(), value)
            self._items.move_to_end(key)
            while len(self._items) > _KEPT_ITEMS:
                self._items.popitem(last=False)

    def lock(self, key: str) -> threading.Lock:
        return self._stripes[hash(key) % len(self._stripes)]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


# The logged-out post query's media by pk, and the posts tab's (crawler) captions by shortcode. A client listing an
# account's posts and its reels (Stratton: posts+reels merged by shortcode, reels then posts when refreshing) asks for
# most items twice; the second listing reuses the first one's answers, which saves those post queries and, when
# Instagram turns them away (throttling), keeps what the first listing got: dates, captions, comment counts.
MEDIA = _Kept()
CAPTIONS = _Kept()


class _NoJson(UpstreamBlocked):
    """An empty body or a web page where Instagram's API answers JSON: how it turns away anonymous calls it is
    throttling (also transiently). Retried once; a 503 like any block, so with_ydl tries the next proxy."""


def api_answer(status: int, body: str, final_url: str, *, logged_in: bool = False) -> dict[str, Any]:
    """The JSON object an Instagram API call (/api/graphql, /api/v1) answered, else the ApiError the answer means.

    Calls Instagram refuses anonymously come back as a redirect to the login page, an empty body, a web page (its
    httpErrorPage) instead of JSON, 401 {"require_login": true, "message": "Please wait a few minutes before you
    try again."} (also without require_login, at any status), or a GraphQL `errors` list without data: blocks (503,
    retryable), as tab_connection reads them, not the "Failed to parse JSON" 500 yt-dlp reports."""
    if InstagramBaseIE._is_login_redirect(final_url):
        raise SessionRejected() if logged_in else UpstreamBlocked(_LOGIN_WALL)
    if status == 429:
        raise UpstreamBlocked("Instagram rate-limited the request (HTTP 429); try again later")
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):  # not JSON, or nested deeper than Python reads it
        data = None
    if not isinstance(data, dict):
        if status >= 500:
            raise UpstreamError(f"Instagram answered HTTP {status}")
        if not body.strip():
            raise _NoJson("Instagram returned an empty response; try again later")
        raise _NoJson("Instagram answered its API with a web page instead of JSON, as it does for anonymous "
                      "requests it is throttling; try again later")
    message = data.get("message") if isinstance(data.get("message"), str) else None
    if (data.get("require_login") is True or status in (401, 403)
            or (data.get("status") == "fail" and _PLEASE_WAIT.search(message or ""))):
        raise UpstreamBlocked(f"Instagram refused the anonymous request: {message or f'HTTP {status}'}")
    if status >= 400 or data.get("status") == "fail":
        raise UpstreamError(f"Instagram answered HTTP {status}: {message or 'error'}")
    errors = [m for m in traverse_obj(data, ("errors", ..., "message")) if isinstance(m, str)]
    if errors and not isinstance(data.get("data"), dict) and not any("was not found" in m for m in errors):
        # GraphQL's refusal of an anonymous query it is throttling: {"errors": [{"message": "execution error",
        # "code": 1675004}]} and no data (a retired query id, "...was not found", is _graphql_failure's)
        raise UpstreamBlocked(f"Instagram refused the anonymous request: {errors[0]}")
    return data


def _graphql_failure(data: dict[str, Any], what: str) -> ApiError:
    """The error a GraphQL answer without the expected data stands for (an unknown query id is our bug, or a
    query Instagram retired)."""
    message = traverse_obj(data, ("errors", ..., "message", {str}, any)) or "no data"
    if "was not found" in message:
        return UpstreamError(f"Instagram no longer serves the {what} query: {message}", code="query_unavailable")
    return UpstreamError(f"Instagram returned no {what}: {message}")


class WebSession:
    """The /api/graphql calls Instagram's logged-out web client makes from a page, with what that page set up: the
    LSD token in its HTML, the csrftoken cookie and the ids of the Relay queries it preloads."""

    def __init__(self, ie: Any, html: str, referer: str) -> None:
        self.ie, self.referer = ie, referer
        m = _LSD.search(html)
        self.lsd = m.group(1) if m else None
        self.preloaded: dict[str, tuple[str, dict[str, Any]]] = {}
        for doc_id, variables, name in _PRELOADER.findall(html):
            try:
                self.preloaded[name] = (doc_id, json.loads(variables))
            except ValueError:
                continue

    def query(self, name: str, doc_id: str, variables: dict[str, Any], *, retries: int = 1) -> dict[str, Any]:
        """POST the query as the web client does -> its JSON answer (api_answer's errors otherwise). An empty or
        non-JSON answer is retried `retries` times after a pause; explicit refusals are not."""
        if not self.lsd:
            raise UpstreamError("Instagram's page carried no LSD token for its API calls", code="no_session_token")
        csrf = self.ie._get_cookies(WEB).get("csrftoken")
        headers = {**self.ie._api_headers, "X-FB-Friendly-Name": name, "X-FB-LSD": self.lsd,
                   "X-Requested-With": "XMLHttpRequest", "Referer": self.referer,
                   "Content-Type": "application/x-www-form-urlencoded",
                   **({"X-CSRFToken": csrf.value} if csrf else {})}
        form = urlencode_postdata({"lsd": self.lsd, "fb_api_caller_class": "RelayModern",
                                   "fb_api_req_friendly_name": name, "server_timestamps": "true",
                                   "variables": json.dumps(variables, separators=(",", ":")), "doc_id": doc_id})
        for attempt in range(retries + 1):
            body, urlh = self.ie._download_webpage_handle(
                _GRAPHQL_URL, name, f"Downloading {name}", data=form, headers=headers,
                impersonate=self.ie._can_impersonate, expected_status=lambda _: True)
            try:
                return api_answer(urlh.status, body, urlh.url, logged_in=self.ie._is_logged_in)
            except _NoJson:
                if attempt >= retries:
                    raise
                time.sleep(_RETRY_DELAY * (attempt + 1))
        raise AssertionError("unreachable")

    def tab_page(self, tab: str, handle: str, cursor: str | None) -> dict[str, Any]:
        """The posts (tab '') or reels tab's connection, after `cursor` (its first page without one), as the page's
        own scrolling asks for it; the posts tab as served to crawlers, with each post's like and play counts.
        BadRequest bad_cursor for a cursor Instagram can't continue from."""
        name = _TAB_QUERY[tab]
        known = {"first": 12, "username": handle, **({_CRAWLER: False} if not tab else {})}  # as the pages preload
        doc_id, variables = self.preloaded.get(name, (_TAB_DOC_ID[tab], known))
        variables = {**variables, **({_CRAWLER: True} if _CRAWLER in variables else {}),
                     **({"after": cursor} if cursor else {})}
        data = self.query(name, doc_id, variables)
        user = traverse_obj(data, ("data", "xig_user_by_username", {dict}))
        connection = traverse_obj(user, (_TAB_CONNECTION[tab], {dict}))
        if connection is None:
            if cursor and user and user.get("pk"):  # the profile answered, only the cursor's page didn't
                raise BadRequest("Instagram can't continue the listing from this cursor", code="bad_cursor")
            raise _graphql_failure(data, "profile listing")
        return connection

    def media_details(self, nodes: list[dict[str, Any]], *, ask: bool = True) -> dict[str, dict[str, Any]]:
        """pk -> the logged-out post query's media (InstagramIE's PolarisLoggedOutDesktopWWWPostRootContentQuery:
        taken_at, caption with its ids, like and comment counts, image and video versions, the author) for each
        node, one request each unless MEDIA has it from another listing. A refusal stops the requests (more would
        only deepen the block), as does _DETAILS_BUDGET or ask=False; the nodes left out simply lack those fields."""
        out: dict[str, dict[str, Any]] = {}
        start = time.monotonic()
        for index, node in enumerate(nodes):
            pk = str_or_none(node.get("pk"))
            if not pk or not pk.isdecimal() or pk in out:
                continue
            if (kept := MEDIA.get(pk)) is not None:
                out[pk] = kept
                continue
            if not ask:
                continue
            if time.monotonic() - start > _DETAILS_BUDGET:
                log.info("instagram: post details stopped after %.0f s, %d of %d items without them",
                         _DETAILS_BUDGET, len(nodes) - index, len(nodes))
                ask = False
                continue
            with MEDIA.lock(pk):
                if (kept := MEDIA.get(pk)) is not None:  # a listing running alongside just asked for it
                    out[pk] = kept
                    continue
                try:
                    data = self.query(_POST_QUERY, _POST_DOC_ID, {"media_id": pk}, retries=0)
                except (ApiError, ExtractorError) as exc:
                    log.info("instagram: post query for %s failed (%s), %d of %d items without details", pk,
                             getattr(exc, "message", exc), len(nodes) - index, len(nodes))
                    ask = False
                    continue
                media = traverse_obj(data, ("data", "xig_polaris_media", "if_not_gated_logged_out", {dict}))
                if media and str_or_none(media.get("pk")) == pk:
                    user = traverse_obj(media, ("user", {dict})) or {}  # it nests more of their posts: not kept
                    out[pk] = {**media, "user": {k: v for k, v in user.items() if not isinstance(v, (dict, list))}}
                    MEDIA.put(pk, out[pk])
        return out


@dataclasses.dataclass
class Feed:
    """One page of a profile's posts or reels tab."""

    user: dict[str, Any]  # the profile page's xig_user_by_username (its own first page of the tab included)
    nodes: list[dict[str, Any]]  # the page's media nodes
    page_info: dict[str, Any]  # Instagram's: end_cursor, has_next_page
    media: dict[str, dict[str, Any]]  # WebSession.media_details for the nodes


def _feed_flow(handle: str, tab: str, cursor: str | None) -> Callable[[yt_dlp.YoutubeDL], Feed]:
    def go(ydl: yt_dlp.YoutubeDL) -> Feed:
        ie = ydl.get_info_extractor("InstagramUser")
        url = f"{WEB}/{handle}/{tab + '/' if tab else ''}"
        html, final_url = _page_flow("InstagramUser", url, handle)(ydl)
        results = _check_page(html, final_url, "Profile")
        if ie._is_logged_in:
            from . import instagram_authenticated

            return instagram_authenticated.feed(ie, html, final_url, handle, tab, cursor)
        user = _profile_user(html, results)
        session = WebSession(ie, html, url)
        connection = traverse_obj(user, (_TAB_CONNECTION[tab], {dict})) or {}
        blocked = False
        if cursor or not tab:  # the posts tab's first page again, for its like and play counts
            try:
                connection = session.tab_page(tab, handle, cursor)
            except ApiError as exc:
                if cursor:
                    raise
                blocked = isinstance(exc, UpstreamBlocked)  # its post queries would be turned away too
                log.info("instagram: counts for %s's posts unavailable: %s", handle, exc.message)
        nodes = traverse_obj(connection, ("edges", ..., "node", {dict})) or []
        page_info = traverse_obj(connection, ("page_info", {dict})) or {}
        _keep_captions(nodes)  # the posts tab has each caption; the reels tab has none
        nodes = [_with_kept_caption(node) for node in nodes]
        media = session.media_details(nodes, ask=not blocked)
        if tab and not cursor and any(_needs_caption(node, media) for node in nodes):
            # Instagram turned the post queries away: the posts tab's first page has the captions of the newest
            # posts, most of them these reels (11 of jane's 12), in one request it answers when it refuses those
            try:
                posts = session.tab_page("", handle, None)
            except ApiError as exc:
                log.info("instagram: posts tab for %s's reel captions unavailable: %s", handle, exc.message)
            else:
                _keep_captions(traverse_obj(posts, ("edges", ..., "node", {dict})) or [])
                nodes = [_with_kept_caption(node) for node in nodes]
        return Feed(user, nodes, page_info, media)

    return go


def _keep_captions(nodes: list[dict[str, Any]]) -> None:
    for node in nodes:
        code, caption = node.get("code"), traverse_obj(node, ("caption", {dict}))
        if isinstance(code, str) and caption and isinstance(caption.get("text"), str):
            CAPTIONS.put(code, caption)


def _needs_caption(node: dict[str, Any], media: dict[str, dict[str, Any]]) -> bool:
    """A node with neither a caption of its own nor post-query media (which has it)."""
    return not traverse_obj(node, ("caption", {dict})) and str(node.get("pk")) not in media


def _with_kept_caption(node: dict[str, Any]) -> dict[str, Any]:
    """The node, with the caption the posts tab gave its shortcode when it has none of its own (a reels-tab node):
    what the post query would add, for when Instagram turns that query away."""
    code = node.get("code")
    if traverse_obj(node, ("caption", {dict})) or not isinstance(code, str) or (kept := CAPTIONS.get(code)) is None:
        return node
    return {**node, "caption": kept}


async def fetch_feed(handle: str, tab: str = "", cursor: str | None = None) -> Feed:
    """A page of the profile's posts tab (tab '') or reels tab: the first one is the page's own, later ones the
    tab's query after `cursor`; with each item's post-query details."""
    return await with_ydl(PLATFORM, _feed_flow(handle, tab, cursor), confirm_not_found=True)


def _highlight_flow(highlight_id: str) -> Callable[[yt_dlp.YoutubeDL], dict[str, Any] | None]:
    """The reel of /stories/highlights/<id>/, the page InstagramStoryIE opens first: Instagram prefetches the
    feed/reels_media answer into it (xdt_api__v1__feed__reels_media__connection) for logged-out visitors, while the
    API call itself answers them with a web page. With a session, a page without it falls back to that call."""
    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any] | None:
        ie = ydl.get_info_extractor("InstagramStory")
        logged_in = ie._is_logged_in
        html, final_url = _page_flow("InstagramStory", f"{WEB}/stories/highlights/{highlight_id}/", highlight_id)(ydl)
        results = _check_page(html, final_url, "Highlight")
        for node in traverse_obj(results, (..., "data", "xdt_api__v1__feed__reels_media__connection", "edges", ...,
                                           "node", {dict})):
            if node.get("id") == f"highlight:{highlight_id}":
                return node
        if not logged_in:
            raise UpstreamBlocked("Instagram served the highlight page without its stories, as it does for "
                                  "anonymous visitors it is throttling; try again later")
        try:
            data = _reels_media_flow(highlight_id)(ydl)
        except ExtractorError as exc:
            if isinstance(exc.cause, json.JSONDecodeError):  # a web page or nothing where JSON belongs
                raise _NoJson("Instagram answered the highlight API with a web page instead of JSON; try again "
                              "later") from exc
            raise
        return traverse_obj(data, ("reels", f"highlight:{highlight_id}", {dict}))

    return go


# --- pure mappers -------------------------------------------------------------------


def post_count_from_meta(html: str) -> tuple[int, bool] | None:
    """'2M Followers, 1,017 Following, 1,800 Posts - ...' -> (1800, False); '12K Posts' -> (12000, True)."""
    for tag in re.findall(r"<meta\b[^>]*og:description[^>]*>", html):
        content = re.search(r'content="([^"]*)"', tag)
        if content and (m := _POSTS_IN_META.search(unescapeHTML(content.group(1)))):
            count = parse_count(m.group(1) + (m.group(2) or ""))
            if count is not None:
                return count, bool(m.group(2))
    return None


def _display(media: dict[str, Any]) -> str | None:
    return media.get("display_uri") or _best_image(media)


def _best_image(media: dict[str, Any]) -> str | None:
    """Largest image_versions2 candidate (the full-size frame), else display_uri."""
    candidates = traverse_obj(media, ("image_versions2", "candidates",
                                      lambda _, v: v["url"] and isinstance(v["url"], str)))
    if candidates:
        return max(candidates, key=lambda c: _size(c.get("width")) * _size(c.get("height")))["url"]
    return media.get("display_uri")


def _size(value: Any) -> int | float:
    """An image's width or height, 0 when it isn't a number."""
    return value if isinstance(value, (int, float)) else 0


def _video_url(media: dict[str, Any]) -> str | None:
    return traverse_obj(media, ("video_versions", 0, "url", {str}))


def video_duration(media: dict[str, Any]) -> float | None:
    """Item's video_duration, else the DASH manifest's mediaPresentationDuration."""
    if isinstance(media.get("video_duration"), (int, float)):
        try:
            return float(media["video_duration"])
        except OverflowError:  # an int past a float's range: the manifest's, if any
            pass
    manifest = media.get("video_dash_manifest")
    m = re.search(r'mediaPresentationDuration="([^"]+)"', manifest) if isinstance(manifest, str) else None
    return parse_duration(m.group(1)) if m else None


def music_attribution(clips: dict[str, Any] | None) -> dict[str, Any] | None:
    """clips_metadata -> SC's clips_music_attribution_info."""
    if sound := traverse_obj(clips, ("original_sound_info", {dict})):
        return compact({
            "artist_name": traverse_obj(sound, ("ig_artist", "username", {str})),
            "song_name": sound.get("original_audio_title"),
            "uses_original_audio": True,
            "should_mute_audio": sound.get("should_mute_audio"),
            "should_mute_audio_reason": traverse_obj(sound, ("consumption_info", "should_mute_audio_reason")),
            "audio_id": str_or_none(sound.get("audio_asset_id")),
        })
    if music := traverse_obj(clips, ("music_info", {dict})):
        asset = traverse_obj(music, ("music_asset_info", {dict})) or {}
        consumption = traverse_obj(music, ("music_consumption_info", {dict})) or {}
        return compact({
            "artist_name": asset.get("display_artist"),
            "song_name": asset.get("title"),
            "uses_original_audio": False,
            "should_mute_audio": consumption.get("should_mute_audio"),
            "should_mute_audio_reason": consumption.get("should_mute_audio_reason"),
            "audio_id": str_or_none(asset.get("audio_asset_id")),
        })
    return None


def _owner(user: Any) -> dict[str, Any] | None:
    if not isinstance(user, dict) or not user:
        return None
    return compact({
        "id": str_or_none(user.get("pk") or user.get("id")),
        "username": user.get("username"),
        "is_verified": user.get("is_verified"),
        "profile_pic_url": user.get("profile_pic_url"),
        "full_name": user.get("full_name"),
        "is_private": user.get("is_private"),
        "is_unpublished": user.get("is_unpublished"),
    })


def _tagged_users(media: dict[str, Any]) -> list[dict[str, Any]]:
    edges = []
    for tag in traverse_obj(media, ("usertags", "in", lambda _, v: isinstance(v["user"], dict))):
        user, pos = tag["user"], tag.get("position")
        pos = pos if isinstance(pos, list) else []
        edges.append({"node": compact({
            "user": compact({k: user.get(k)
                             for k in ("full_name", "id", "is_verified", "profile_pic_url", "username")}),
            "x": pos[0] if len(pos) > 0 else None,
            "y": pos[1] if len(pos) > 1 else None,
        })})
    return edges


def _graph_type(media_type: Any) -> str:
    """media_type 1, 2 or 8 -> GraphImage, GraphVideo or GraphSidecar; GraphImage for any other value."""
    return _GRAPH_TYPE.get(media_type, "GraphImage") if isinstance(media_type, int) else "GraphImage"


def _sidecar_child(media: dict[str, Any]) -> dict[str, Any]:
    is_video = media.get("media_type") == 2
    return compact({
        "__typename": "XDT" + _graph_type(media.get("media_type")),
        "id": str_or_none(media.get("pk")),
        "shortcode": media.get("code"),
        "dimensions": compact({"height": media.get("original_height"), "width": media.get("original_width")}) or None,
        "display_url": _best_image(media),
        "is_video": is_video,
        "has_audio": media.get("has_audio") if is_video else None,
        "video_url": _video_url(media) if is_video else None,
        "video_duration": video_duration(media) if is_video else None,
        "accessibility_caption": media.get("accessibility_caption"),
        "edge_media_to_tagged_user": {"edges": _tagged_users(media)},
    })


def _comment_node(node: dict[str, Any]) -> dict[str, Any]:
    user = traverse_obj(node, ("user", {dict})) or {}
    replies = node.get("child_comment_count")
    return compact({
        "id": str_or_none(node.get("pk") or node.get("id")),
        "text": node.get("text"),
        "created_at": node.get("created_at"),
        "owner": compact({
            "id": str_or_none(user.get("pk") or user.get("id")),
            "is_verified": user.get("is_verified"),
            "profile_pic_url": user.get("profile_pic_url"),
            "username": user.get("username"),
        }),
        "edge_liked_by": compact({"count": node.get("comment_like_count")}),
        "edge_threaded_comments": {"count": replies} if isinstance(replies, int) else None,
    })


def comment_nodes(media: dict[str, Any] | None) -> list[dict[str, Any]]:
    return traverse_obj(media, ("comments_connection", "edges", ..., "node", {dict})) or []


def map_post(product: dict[str, Any], media: dict[str, Any] | None = None, *,
             play_count: bool = True) -> dict[str, Any]:
    """Logged-out XIG media (or a logged-in media/info item) -> SC's data.xdt_shortcode_media."""
    mtype = product.get("media_type")
    is_video = mtype == 2
    caption = traverse_obj(product, ("caption", {dict})) or {}
    page_info = traverse_obj(media, ("comments_connection", "page_info", {dict}))
    carousel = traverse_obj(product, ("carousel_media", ..., {dict}))
    out = compact({
        "id": str_or_none(product.get("pk")),
        "shortcode": product.get("code"),
        "__typename": "XDT" + _graph_type(mtype),
        "thumbnail_src": _display(product),
        "display_url": _best_image(product),
        "video_url": _video_url(product) if is_video else None,
        "has_audio": product.get("has_audio") if is_video else None,
        "video_view_count": product.get("view_count") if is_video else None,
        # a logged-in item's play_count adds Facebook plays; ig_play_count is Instagram's alone
        "video_play_count": traverse_obj(product, "ig_play_count", "play_count") if is_video and play_count else None,
        "product_type": product.get("product_type"),
        "video_duration": video_duration(product) if is_video else None,
        "clips_music_attribution_info": music_attribution(product.get("clips_metadata")),
        "is_video": is_video,
        "owner": _owner(product.get("user")),
        "edge_media_to_caption": {"edges": [{"node": compact({
            "created_at": str_or_none(caption.get("created_at")),
            "text": caption.get("text"),
            "id": str_or_none(caption.get("pk")),
        })}] if caption.get("text") is not None else []},
        "edge_media_to_parent_comment": compact({
            "count": product.get("comment_count"),
            "page_info": compact({"has_next_page": page_info.get("has_next_page"),
                                  "end_cursor": page_info.get("end_cursor")}) if page_info else None,
            "edges": [{"node": _comment_node(n)} for n in comment_nodes(media)] if media else None,
        }),
        "edge_media_preview_comment": compact({
            "count": product.get("comment_count"),
            "edges": [{"node": _comment_node(n)} for n in comment_nodes(media)[:_PREVIEW_COMMENTS]] if media else None,
        }),
        "taken_at_timestamp": product.get("taken_at"),
        "created_at": iso_utc(product.get("taken_at")),
        "edge_media_preview_like": compact({"count": product.get("like_count")}),
        "edge_media_to_tagged_user": {"edges": _tagged_users(product)},
        "edge_sidecar_to_children": {"edges": [{"node": _sidecar_child(m)} for m in carousel]} if carousel else None,
        "is_paid_partnership": product.get("is_paid_partnership"),
    })
    out["accessibility_caption"] = product.get("accessibility_caption")
    location = product.get("location")
    out["location"] = compact({
        "id": str_or_none(location.get("pk") or location.get("id")),
        "name": location.get("name"),
    }) if isinstance(location, dict) else None
    return out


def map_comment(node: dict[str, Any]) -> dict[str, Any]:
    """comments_connection node -> SC v2 comment."""
    user = traverse_obj(node, ("user", {dict})) or {}
    pk = str_or_none(user.get("pk") or user.get("id"))
    return {
        **compact({
            "id": str_or_none(node.get("pk") or node.get("id")),
            "text": node.get("text"),
            "created_at": iso_utc(node.get("created_at")),
            "comment_like_count": node.get("comment_like_count"),
        }),
        "child_comment_count": node.get("child_comment_count"),  # null when Instagram doesn't expose it
        "user": compact({
            "is_verified": user.get("is_verified"),
            "id": pk,
            "pk": pk,
            "is_unpublished": user.get("is_unpublished"),
            "profile_pic_url": user.get("profile_pic_url"),
            "username": user.get("username"),
        }),
    }


def _timeline_node(node: dict[str, Any]) -> dict[str, Any]:
    text = traverse_obj(node, ("caption", "text", {str}))
    return compact({
        "__typename": _graph_type(node.get("media_type")),
        "id": str_or_none(node.get("pk")),
        "shortcode": node.get("code"),
        "display_url": node.get("display_uri"),
        "is_video": node.get("media_type") == 2,
        "product_type": node.get("product_type"),
        "accessibility_caption": node.get("accessibility_caption"),
        "owner": compact({"id": str_or_none(traverse_obj(node, ("user", "pk"))),
                          "username": traverse_obj(node, ("user", "username"))}),
        "edge_media_to_caption": {"edges": [{"node": {"text": text}}] if text is not None else []},
    })


def map_profile(user: dict[str, Any], post_count: tuple[int, bool] | None = None) -> dict[str, Any]:
    """Merged xig_user_by_username -> SC's data.user (GraphQL-style profile)."""
    timeline = traverse_obj(user, ("polaris_ordered_timeline_connection", {dict})) or {}
    page_info = traverse_obj(timeline, ("page_info", {dict})) or {}
    return compact({
        "biography": user.get("biography"),
        "bio_links": [compact({k: link.get(k) for k in ("title", "lynx_url", "url", "link_type")})
                      for link in traverse_obj(user, ("bio_links", ..., {dict}))],
        "fbid": str_or_none(user.get("fbid_v2") or user.get("id")),
        "full_name": user.get("full_name"),
        "id": str_or_none(user.get("pk")),
        "is_private": user.get("is_private"),
        "is_verified": user.get("is_verified"),
        "profile_pic_url": user.get("profile_pic_url"),
        "profile_pic_url_hd": traverse_obj(user, ("hd_profile_pic_url_info", "url", {str})),
        "external_url": user.get("external_url"),
        "external_url_linkshimmed": user.get("external_lynx_url"),
        "category_name": user.get("category"),
        "is_business_account": user.get("is_business"),
        "is_professional_account": user.get("is_professional_account"),
        "username": user.get("username"),
        "edge_followed_by": compact({"count": user.get("follower_count")}),
        "edge_follow": compact({"count": user.get("following_count")}),
        "edge_owner_to_timeline_media": compact({
            "count": (user["media_count"] if isinstance(user.get("media_count"), int)
                      and not isinstance(user["media_count"], bool) and user["media_count"] >= 0
                      else post_count[0] if post_count and not post_count[1] else None),
            "page_info": compact({"has_next_page": page_info.get("has_next_page"),
                                  "end_cursor": page_info.get("end_cursor")}) if page_info else None,
            "edges": [{"node": _timeline_node(n)}
                      for n in traverse_obj(timeline, ("edges", ..., "node", {dict}))],
        }),
    })


def _user_v2(user: dict[str, Any]) -> dict[str, Any]:
    pk = str_or_none(user.get("pk"))
    return compact({
        "pk": pk,
        "pk_id": pk,
        "id": pk,
        "username": user.get("username"),
        "full_name": user.get("full_name"),
        "is_private": user.get("is_private"),
        "is_verified": user.get("is_verified"),
        "is_unpublished": user.get("is_unpublished"),
        "profile_pic_url": user.get("profile_pic_url"),
        "fbid_v2": str_or_none(user.get("fbid_v2") or user.get("id")),
    })


def _node_author(node: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """A timeline/clips node's own author (collab posts list the collaborator), filled from the profile."""
    user = node.get("user") if isinstance(node.get("user"), dict) else {}
    if not user.get("pk") or str(user["pk"]) == str(profile.get("pk")):
        return _user_v2({**profile, **{k: v for k, v in user.items() if v is not None}})
    return _user_v2(user)


def _media_id(node: dict[str, Any], author: dict[str, Any]) -> str | None:
    return f"{node['pk']}_{author['pk']}" if node.get("pk") and author.get("pk") else str_or_none(node.get("pk"))


def _count(key: str, *sources: dict[str, Any]) -> int | None:
    """The first source's integer `key`. A like or play count Instagram hides (like_and_view_counts_disabled) and
    reports as 0 is unknown, not 0."""
    for source in sources:
        value = source.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            hidden = key != "comment_count" and any(s.get("like_and_view_counts_disabled") is True for s in sources)
            return None if hidden and value == 0 else value
    return None


def _timestamp(*values: Any) -> int | None:
    return next((v for v in values if isinstance(v, int) and not isinstance(v, bool) and v > 0), None)


def map_feed_item(node: dict[str, Any], profile: dict[str, Any], media: dict[str, Any] | None = None, *,
                  reel: bool = False) -> dict[str, Any]:
    """A posts- or reels-tab node -> SC user/posts (v2) or user/reels item, its own author (collaborators included)
    filled from the profile.

    `media`, the logged-out post query's answer for it when fetched (WebSession.media_details), adds taken_at and
    created_at, the caption with its ids (the reels tab has none), the comment count, image/video versions,
    has_audio, video_duration and the author's name. Like and play counts are the tab's (the posts tab's crawler
    query has like_count and play_count, the reels tab also comment_count): the post query has no play count
    anonymously. play_count, logged out Instagram's alone, is also ig_play_count."""
    media = media or {}
    user = {**(traverse_obj(node, ("user", {dict})) or {}),
            **{k: v for k, v in (traverse_obj(media, ("user", {dict})) or {}).items()
               if v is not None and not isinstance(v, (dict, list))}}  # the media's user nests more of their posts
    author = _node_author({"user": user}, profile)
    pk = str_or_none(node.get("pk") or media.get("pk"))
    code = node.get("code") or media.get("code")
    mtype = traverse_obj(media, ("media_type", {int})) or traverse_obj(node, ("media_type", {int}))
    video = mtype == 2 or _is_video_node(node) or bool(media.get("video_versions"))
    caption = traverse_obj(media, ("caption", {dict})) or traverse_obj(node, ("caption", {dict})) or {}
    text = caption.get("text") if isinstance(caption.get("text"), str) else None
    caption_pk = str_or_none(caption.get("pk"))
    taken_at = _timestamp(media.get("taken_at"), node.get("taken_at"))
    play_count = _count("play_count", node, media)
    result = compact({
        "id": _media_id({"pk": pk}, author),
        "code": code,
        "media_type": mtype,
        "product_type": node.get("product_type") or media.get("product_type"),
        "caption": compact({
            "text": text,
            "pk": caption_pk,
            "created_at": _timestamp(caption.get("created_at")),
            "media_id": pk if caption_pk else None,
            "user_id": author.get("pk") if caption_pk else None,
        }) if text is not None else None,
        "taken_at": taken_at,
        "taken_at_timestamp": taken_at,
        "created_at": iso_utc(taken_at),
        "like_count": _count("like_count", media, node),
        "comment_count": _count("comment_count", media, node),
        "play_count": play_count,
        "ig_play_count": play_count,
        "has_audio": media.get("has_audio") if video and isinstance(media.get("has_audio"), bool) else None,
        "video_duration": video_duration(media) if video and media else None,
        "is_paid_partnership": media.get("is_paid_partnership") if isinstance(media.get("is_paid_partnership"),
                                                                              bool) else None,
        "display_uri": node.get("display_uri") or media.get("display_uri"),
        "thumbnail_url": node.get("display_uri") or media.get("display_uri"),
        "image_versions2": prune(traverse_obj(media, ("image_versions2", {dict})), IMAGE_VERSIONS) or None,
        "video_versions": prune(traverse_obj(media, ("video_versions", ..., {dict})), VIDEO_VERSION) or None,
        "user": author,
        "owner": author,
        "url": f"{WEB}/{'reel' if reel else 'p'}/{code}/" if code else None,
    })
    # An answered post query can explicitly say that a post has no caption.
    # Keep that known null distinct from a detail request that never answered.
    caption_source = media if "caption" in media else node
    if text is None and "caption" in caption_source and caption_source["caption"] is None:
        result["caption"] = None
    return result


def _cursor(page_info: dict[str, Any]) -> str | None:
    """Instagram's end_cursor while it says there is a next page."""
    end = page_info.get("end_cursor")
    return end if page_info.get("has_next_page") is True and isinstance(end, str) and end else None


def map_user_posts(feed: Feed) -> dict[str, Any]:
    """A posts-tab page -> SC v2 user/posts; next_max_id is Instagram's own cursor for the next page."""
    items = [map_feed_item(node, feed.user, feed.media.get(str(node.get("pk")))) for node in feed.nodes]
    profile = _user_v2(feed.user)
    top = {k: profile[k] for k in ("pk", "pk_id", "full_name", "is_private", "id", "username", "is_verified",
                                   "profile_pic_url") if k in profile}
    has_next = feed.page_info.get("has_next_page")
    return compact({"items": items, "num_results": len(items), "next_max_id": _cursor(feed.page_info),
                    "more_available": has_next if isinstance(has_next, bool) else None, "user": top})


def map_user_reels(feed: Feed) -> dict[str, Any]:
    """A reels-tab page -> SC user/reels; max_id is Instagram's own cursor for the next page."""
    items = [map_feed_item(node, feed.user, feed.media.get(str(node.get("pk"))), reel=True) for node in feed.nodes]
    return compact({"items": items, "max_id": _cursor(feed.page_info)})


def map_highlights(user: dict[str, Any]) -> list[dict[str, Any]]:
    owner = compact({"__typename": "GraphUser", "id": str_or_none(user.get("pk")),
                     "profile_pic_url": user.get("profile_pic_url"), "username": user.get("username")})
    return [compact({
        "__typename": "GraphHighlightReel",
        "id": str_or_none(node.get("id")),
        "cover_media_cropped_thumbnail": compact({"url": node.get("cover_media_cropped_thumbnail_url")}) or None,
        "owner": owner,
        "title": node.get("title"),
    }) for node in traverse_obj(user, ("lox_highlights_connection", "edges", ..., "node", {dict}))]


def map_highlight_reel(reel: dict[str, Any]) -> dict[str, Any]:
    """A feed/reels_media reel (the highlight page's prefetched answer, or the API's) -> SC highlight detail, trimmed
    to ScrapeCreators' keys. The page's items name their author by pk and id only; as InstagramStoryIE does, the
    reel's user (a highlight holds its owner's stories) fills in their user and owner. media_count and media_ids,
    which the page leaves out, count and list the items: the reel carries all of them."""
    owner = {k: v for k, v in (traverse_obj(reel, ("user", {dict})) or {}).items() if v is not None}
    items = []
    for item in traverse_obj(reel, ("items", ..., {dict})):
        user = traverse_obj(item, ("user", {dict})) or {}
        if owner.get("pk") and str(user.get("pk") or owner["pk"]) == str(owner["pk"]):
            user = {**owner, **{k: v for k, v in user.items() if v is not None}}
            item = {**item, "user": user, "owner": item.get("owner") or user}
        items.append(item)
    out = prune({**reel, "items": items}, HIGHLIGHT_REEL)
    if "media_count" not in out:
        out["media_count"] = len(items)
    if "media_ids" not in out and all(item.get("pk") for item in items):
        out["media_ids"] = [str(item["pk"]) for item in items]
    fbid = traverse_obj(out, ("user", "interop_messaging_user_fbid", {str}))
    if fbid and fbid.isdecimal():  # the page's GraphQL types it as a string, the API (and SC) as a number
        out["user"]["interop_messaging_user_fbid"] = int(fbid)
    return out


_PAGE_TITLE = re.compile(
    r'"page_logging":\{"name":"popularSearch\w*","params":\{[^{}]*\}\s*\},"title":("(?:[^"\\]|\\.)*")')


def _topic_title(html: str) -> str | None:
    """The page's own title ('Golden Retriever'), else og:title minus ' • 203M reels on Instagram'."""
    if m := _PAGE_TITLE.search(html):
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    m = re.search(r'<meta\b[^>]*property="og:title"[^>]*content="([^"]*)"', html)
    if not m:
        return None
    return re.sub(r"(?:\s*•[^•]*)?\s+on Instagram$", "", unescapeHTML(m.group(1))).strip() or None


def _total_media_count(html: str) -> int | None:
    m = re.search(r'"total_media_count_intl":"([^"]*)"', html)
    return parse_count(m.group(1)) if m and m.group(1) else None


def topic_nodes(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Popular page media, or every section of a category hub page (/popular/basketball/), deduped."""
    nodes = traverse_obj(data, ("xig_logged_out_popular_search_media_info", "edges", ..., "node", {dict}))
    if not nodes:
        nodes = traverse_obj(data, ("ig_popular_search_category_hub", "sections", ...,
                                    "media_items", "edges", ..., "node", {dict}))
    seen: set[str] = set()
    unique = []
    for node in nodes or []:
        key = str(node.get("pk") or node.get("id"))
        if key not in seen:
            seen.add(key)
            unique.append(node)
    return unique


def _is_video_node(node: dict[str, Any]) -> bool:
    return node.get("__typename") == "XIGPolarisVideoMedia" or bool(node.get("video_versions"))


def map_popular(query: str, data: dict[str, Any], html: str) -> dict[str, Any]:
    """PopularSearchPage (or category hub) relay data -> SC search/popular (first page)."""
    hub = traverse_obj(data, ("ig_popular_search_category_hub", {dict})) or {}
    desc = data.get("popular_search_keyword_description")
    if isinstance(hub.get("description"), str):
        desc = {"plain_text": hub["description"]}
    terms = traverse_obj(data, ("popular_search_related_keywords_connection", "edges", ..., "node", "query_text",
                                {str}))
    posts = []
    for node in topic_nodes(data):
        video = _is_video_node(node)
        user = traverse_obj(node, ("user", {dict})) or {}
        posts.append(compact({
            "id": node.get("id"),
            "shortcode": node.get("code"),
            "url": f"{WEB}/{'reel' if video else 'p'}/{node['code']}/" if node.get("code") else None,
            "type": "reel" if video else "post",
            "caption": traverse_obj(node, ("caption", "text", {str})),
            "display_url": node.get("display_uri"),
            "video_url": _video_url(node),
            "play_count": node.get("play_count"),
            "owner": compact({"id": str_or_none(user.get("id")), "username": user.get("username"),
                              "is_verified": user.get("is_verified"), "profile_pic_url": user.get("profile_pic_url")}),
        }))
    return compact({
        "query": query,
        "title": traverse_obj(hub, ("category", "display_name", {str})) or _topic_title(html),
        "total_media_count": _total_media_count(html),
        "description": compact({k: desc.get(k) for k in ("plain_text", "source_uris", "linked_terms")})
        if isinstance(desc, dict) else None,
        "suggested_terms": terms or traverse_obj(hub, ("sections", ..., "keyword_text", {str})),
        "posts": posts,
    })


def map_hashtag_post(node: dict[str, Any]) -> dict[str, Any]:
    """Tag-page media node -> SC search/hashtag post."""
    video = _is_video_node(node)
    user = traverse_obj(node, ("user", {dict})) or {}
    return compact({
        "id": str_or_none(node.get("pk")),
        "__typename": "XDTGraphVideo" if video else "XDTGraphImage",
        "shortcode": node.get("code"),
        "url": f"{WEB}/{'reel' if video else 'p'}/{node['code']}/" if node.get("code") else None,
        "caption": traverse_obj(node, ("caption", "text", {str})),
        "thumbnail_src": node.get("display_uri"),
        "display_url": node.get("display_uri"),
        "video_url": _video_url(node),
        "video_play_count": node.get("play_count") if video else None,
        "is_video": video,
        "owner": compact({"id": str_or_none(user.get("pk")), "username": user.get("username"),
                          "is_verified": user.get("is_verified"), "profile_pic_url": user.get("profile_pic_url")}),
    })


def video_items(product: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """(1-based playlist index, media) for every video in a post; carousels count all children."""
    carousel = traverse_obj(product, ("carousel_media", ..., {dict}))
    if carousel:
        return [(i, m) for i, m in enumerate(carousel, 1) if m.get("media_type") == 2 or m.get("video_versions")]
    return [(1, product)] if product.get("media_type") == 2 or product.get("video_versions") else []


# --- reel play counts, from the owner's logged-out Reels tab -----------------------------
# Nothing a logged-out visitor gets for one post carries a play count: the post query that had video_play_count was
# retired in 2026, PolarisPostRootQuery answers view_count null and media/<pk>/info/ redirects to the login page. The
# owner's Reels tab lists every reel with Instagram's own play_count (as on user/reels), and its pagination query
# answers logged out back to the account's first reel. The tab is ordered newest first (pinned reels ahead), and a
# media pk grows with its time, so paging stops at the reel, at the end of the tab, or once it has passed the reel's pk.

REELS_TAB_QUERY = "PolarisLoggedOutDesktopWWWProfileReelsTabContentQuery"
# The tab's pagination query (REELS_TAB_QUERY + "_connection"; variables after, first, id = the profile's Relay id
# '17841…', not its pk). Its doc_id rotates with Instagram's bundle deploys and isn't in the page; a retired one answers
# "The GraphQL document with ID … was not found", and the lookup then pages with the tab's own query, whose doc_id the
# /<handle>/reels/ page names (its RelayPreloader's queryID) but which returns 12 reels per request whatever `first` is.
REELS_TAB_DOC = "27420265870989234"
_TAB_QUERY_ID = re.compile(r'ProfileReelsTabContentQueryRelayPreloader_[^"]*","queryID":"([0-9]+)"')
_TAB_DOC_GONE = re.compile(r"GraphQL document with ID [0-9]+ was not found", re.I)
_TAB_FIRST, _TAB_NEXT = 50, 150  # reels per request: the newest 50 (most lookups), then 150 a time (~1 KB each)
_TAB_SMALL = 50  # a page Instagram fails with a 5xx is asked once more at this size (huge tabs fail deep at >=200)
_TAB_ROOT_FIRST = 12
_TAB_REQUESTS = 6  # per lookup: 50 + 5 x 150 reaches the 800th reel; a cached tab continues where it stopped
_TAB_TTL = 300.0  # seconds a tab's pages are reused, so refreshing an account's reels pages its tab once
_TAB_PAUSE = 1.0  # seconds between the requests of one lookup
_TAB_KEEP = 64  # accounts whose tabs are kept


class _TabQueryGone(Exception):
    """Instagram no longer knows the pagination query's doc_id. Not an ApiError: no other proxy profile would help."""


@dataclasses.dataclass
class ReelsTab:
    """What has been read of one account's Reels tab: play counts by shortcode, and where the tab continues."""

    handle: str
    user_id: str | None  # the profile's Relay id, which the pagination query takes
    read_at: float = dataclasses.field(default_factory=time.monotonic)
    plays: dict[str, int | None] = dataclasses.field(default_factory=dict)
    cursor: str | None = None
    more: bool = True
    newest: int = 0  # pk range of the unpinned reels read
    oldest: int | None = None
    root_doc: str | None = None  # the tab's own query, once its page has been read

    def add(self, connection: dict[str, Any]) -> None:
        for node in traverse_obj(connection, ("edges", ..., "node", {dict})):
            if not isinstance(code := node.get("code"), str):
                continue
            plays = node.get("play_count")
            self.plays[code] = plays if isinstance(plays, int) and not isinstance(plays, bool) else None
            pk = int(node["pk"]) if str(node.get("pk") or "").isdigit() else None
            if pk and not node.get("clips_tab_pinned_user_ids"):  # a pinned reel sits ahead of its date
                self.newest = max(self.newest, pk)
                self.oldest = pk if self.oldest is None else min(self.oldest, pk)
        page = traverse_obj(connection, ("page_info", {dict})) or {}
        self.cursor = page.get("end_cursor") if isinstance(page.get("end_cursor"), str) else None
        self.more = page.get("has_next_page") is True and bool(self.cursor)

    def find(self, code: str, pk: int) -> bool | None:
        """True once the reel has been read; False when it can't be further down (the tab ended, or it is newer than
        reels already read); None while it may be."""
        if code in self.plays:
            return True
        if not self.more or (self.oldest is not None and pk > self.oldest):
            return False
        return None


_tabs: dict[str, ReelsTab] = {}
_tab_locks: dict[str, asyncio.Lock] = {}
_retired_docs: set[str] = set()


def tab_connection(status: int, url: str, body: str, owner: str) -> dict[str, Any] | None:
    """A Reels tab query's answer -> its polaris_clips_connection (`owner` is the key holding the profile: node for the
    pagination query, xig_user_by_username for the tab's own); None when Instagram has no such profile.

    Instagram refuses logged-out requests with a 429, its login page, an HTML page with a 200 (also its answer to a
    form `lsd` that doesn't match the X-FB-LSD header), an empty body, `{"message": "Please wait a few minutes…",
    "status": "fail"}` or an `errors` list: all of them are blocks (503, so the next proxy profile is tried)."""
    if InstagramBaseIE._is_login_redirect(url):
        raise UpstreamBlocked(_LOGIN_WALL)
    if status == 429:
        raise UpstreamBlocked("Instagram rate-limited the Reels tab query (HTTP 429)")
    if status >= 500:
        raise UpstreamError(f"Instagram failed the Reels tab query (HTTP {status})")
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise UpstreamBlocked("Instagram answered the Reels tab query with " + (
            "an empty body" if not body.strip() else "a web page, not data") + " (logged-out limit); retry later")
    errors = [m for m in traverse_obj(data, ("errors", ..., "message")) if isinstance(m, str)]
    if any(_TAB_DOC_GONE.search(m) for m in errors):
        raise _TabQueryGone(errors[0])
    if errors or not isinstance(data.get("data"), dict):
        reason = "; ".join(errors) or (data["message"] if isinstance(data.get("message"), str) else "no data")
        raise UpstreamBlocked(f"Instagram refused the Reels tab query: {reason}")
    profile = data["data"].get(owner)
    if profile is None:
        return None
    return traverse_obj(profile, ("polaris_clips_connection", {dict})) or {}


def _tab_query_flow(handle: str, doc: str, name: str, variables: dict[str, Any],
                    owner: str) -> Callable[[yt_dlp.YoutubeDL], dict[str, Any] | None]:
    """POST one Reels tab query to /api/graphql as the logged-out page does. `lsd` is a double-submit token: any value
    works when the form field and the X-FB-LSD header carry the same one. Read inside the worker, so a block moves
    on to the next configured proxy profile."""
    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any] | None:
        ie = ydl.get_info_extractor("InstagramUser")
        lsd = secrets.token_urlsafe(8)
        body, urlh = ie._download_webpage_handle(
            f"{WEB}/api/graphql", handle, "Downloading Reels tab", impersonate=ie._can_impersonate,
            expected_status=lambda _: True,
            headers={**ie._api_headers, "X-FB-Friendly-Name": name, "X-FB-LSD": lsd,
                     "X-Requested-With": "XMLHttpRequest", "Referer": f"{WEB}/{handle}/reels/",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data=urlencode_postdata({
                "lsd": lsd, "fb_api_caller_class": "RelayModern", "fb_api_req_friendly_name": name,
                "server_timestamps": "true", "variables": json.dumps(variables, separators=(",", ":")), "doc_id": doc,
            }))
        return tab_connection(urlh.status, urlh.url, body, owner)

    return go


async def _read_tab(tab: ReelsTab, first: int) -> None:
    """Read the tab's next page into it: with the pagination query while its doc_id lives, else from the
    /<handle>/reels/ page (the first 12, the profile's Relay id and the tab's own query) and then that query."""
    if tab.user_id and REELS_TAB_DOC not in _retired_docs:
        variables = {"after": tab.cursor, "first": first, "id": tab.user_id}
        try:
            try:
                connection = await with_ydl(PLATFORM, _tab_query_flow(
                    tab.handle, REELS_TAB_DOC, f"{REELS_TAB_QUERY}_connection", variables, "node"))
            except UpstreamError:  # a 5xx: very large tabs fail deep pages of 200+ reels; ask once for fewer
                if first <= _TAB_SMALL:
                    raise
                await asyncio.sleep(_TAB_PAUSE)
                connection = await with_ydl(PLATFORM, _tab_query_flow(
                    tab.handle, REELS_TAB_DOC, f"{REELS_TAB_QUERY}_connection", {**variables, "first": _TAB_SMALL},
                    "node"))
        except _TabQueryGone as exc:
            _retired_docs.add(REELS_TAB_DOC)
            log.warning("instagram: Reels tab pagination doc_id %s is retired (%s); paging 12 reels a request with "
                        "the tab's own query until REELS_TAB_DOC is updated", REELS_TAB_DOC, exc)
        else:
            if connection is None:
                raise NotFound("Instagram has no profile with this Relay id")
            tab.add(connection)
            return
    if tab.root_doc is None:
        user, html = await fetch_profile(tab.handle, "reels")
        tab.user_id = tab.user_id or str_or_none(user.get("id"))
        if not (m := _TAB_QUERY_ID.search(html)):
            raise UpstreamError("The Reels tab page names no query to page the tab with", code="reels_tab_unavailable")
        tab.root_doc = m.group(1)
        if tab.cursor is None:  # the page holds the first 12
            tab.add(traverse_obj(user, ("polaris_clips_connection", {dict})) or {})
            return
    connection = await with_ydl(PLATFORM, _tab_query_flow(
        tab.handle, tab.root_doc, REELS_TAB_QUERY,
        {"after": tab.cursor, "first": _TAB_ROOT_FIRST, "username": tab.handle}, "xig_user_by_username"))
    if connection is None:
        raise NotFound("Profile not found")
    tab.add(connection)


def needs_tab_plays(product: dict[str, Any]) -> bool:
    """A reel whose item has no play count (every logged-out one): only reels are on the Reels tab, never feed videos,
    IGTV or carousels."""
    return (product.get("media_type") == 2 and product.get("product_type") == "clips"
            and traverse_obj(product, "ig_play_count", "play_count") is None)


async def reel_play_count(product: dict[str, Any]) -> int | None:
    """Instagram's play count of a reel, read from its owner's Reels tab. None when the tab doesn't list it within
    _TAB_REQUESTS requests (a cached tab continues where it stopped), lists it without one, or can't be read: the
    count is best effort, and the post is returned either way."""
    user = traverse_obj(product, ("user", {dict})) or {}
    handle, code = user.get("username"), product.get("code")
    if not (isinstance(handle, str) and _HANDLE.fullmatch(handle)
            and isinstance(code, str) and _SHORTCODE_CHARS.fullmatch(code)):
        return None
    pk = int(product["pk"]) if str(product.get("pk") or "").isdigit() else _id_to_pk(code)
    # the logged-in item's user.id is its pk; the logged-out one's is the Relay id the pagination query takes
    relay_id = str_or_none(user.get("id"))
    key = str_or_none(user.get("pk")) or handle.lower()
    async with _tab_locks.setdefault(key, asyncio.Lock()):
        tab, now = _tabs.get(key), time.monotonic()
        if tab is None or now - tab.read_at > _TAB_TTL or (tab.newest and pk > tab.newest and code not in tab.plays):
            tab = ReelsTab(handle, relay_id if relay_id != str_or_none(user.get("pk")) else None)
        _tabs[key] = tab
        while len(_tabs) > _TAB_KEEP:  # the account read longest ago goes
            stale = min((k for k in _tabs if k != key), key=lambda k: _tabs[k].read_at)
            del _tabs[stale]
            if (lock := _tab_locks.get(stale)) and not lock.locked():
                del _tab_locks[stale]
        requests = 0
        try:
            while (found := tab.find(code, pk)) is None and requests < _TAB_REQUESTS:
                if requests:
                    await asyncio.sleep(_TAB_PAUSE)
                await _read_tab(tab, _TAB_NEXT if tab.plays else _TAB_FIRST)
                requests += 1
        except ApiError as exc:
            log.info("instagram: no play count for %s: @%s's Reels tab failed: %s", code, handle, exc)
            return None
        if not found:
            log.info("instagram: no play count for %s: %s", code, f"@{handle}'s Reels tab doesn't list it"
                     if found is False else f"not among @{handle}'s {len(tab.plays)} newest reels")
        elif tab.plays[code] is None:
            log.info("instagram: no play count for %s: @%s's Reels tab lists it without one", code, handle)
        return tab.plays.get(code)


# --- routes ---------------------------------------------------------------------------


def _post_code(product: dict[str, Any], shortcode: str) -> str:
    """The shortcode of the post fetch_post returned, else the URL's ('' for a share link)."""
    code = traverse_obj(product, ("code", {str})) or ""
    if not _SHORTCODE_CHARS.fullmatch(code):  # _id_to_pk can't read it
        code = shortcode
    if code:
        return code
    raise UpstreamError("Instagram returned the post without its shortcode")


def _no_pagination(value: str | None, name: str) -> None:
    if value:
        raise NotSupported(f"{name} pagination is not available: the next page needs an Instagram query "
                           "yt-dlp never makes", code="pagination_unavailable")


@sc_get(router, "/v1/instagram/post", source="yt-dlp InstagramIE (logged-out PostRootContentQuery / media info) "
        "+ the owner's logged-out Reels tab query (reel play counts)",
        notes="Mapped from the media InstagramIE parses (logged-out GraphQL, else the /p/ page). The logged-out post "
              "has no play or view count, so a reel's video_play_count (Instagram's own plays, as on user/reels) is "
              "read from its owner's Reels tab, paged to the reel (up to 6 requests, ~800 reels deep; pages are "
              "reused for 5 minutes, so an account's reels cost one pass). It is left out when the tab doesn't "
              "list the reel within that, the tab can't be read, or include_play_count=false; feed videos, IGTV and "
              "carousels are never on the tab and have none. video_view_count (the legacy 3-second count) comes "
              f"only from the logged-in media/info item ({COOKIE_NOTE}). edge_media_to_parent_comment.edges holds "
              "the ~15 comments the logged-out query "
              "returns. created_at, edge_media_to_tagged_user and edge_sidecar_to_children (carousels) included; "
              "a video child has its own has_audio and video_duration. region and trim are ignored; "
              "download_media=true returns 501. A bare shortcode, an instagr.am link and a share link "
              "(/share/reel/<id>, followed to its post) are accepted.")
async def instagram_post(url: str = Query(...), region: str | None = None, trim: str | None = None,
                         download_media: str | None = None, include_play_count: str | None = None
                         ) -> dict[str, Any]:
    _, canonical = post_url(url)
    if truthy(download_media):
        raise NotSupported("download_media (re-hosting media) is not supported; use the returned CDN URLs")
    product, media = await fetch_post(canonical)
    play_count = include_play_count is None or truthy(include_play_count)
    if play_count and needs_tab_plays(product):
        # the tab's play_count is Instagram's alone, as a logged-in item's ig_play_count is
        product = {**product, "ig_play_count": await reel_play_count(product)}
    return {"data": {"xdt_shortcode_media": map_post(product, media, play_count=play_count)}}


@sc_get(router, "/v2/instagram/post/comments", source="yt-dlp InstagramIE (logged-out PostRootContentQuery comments)",
        notes="Only the first page (~15 comments) the logged-out post query returns; no cursor is returned and "
              "passing one returns 501. child_comment_count is null anonymously; include_replies=true returns "
              "501. user.fbid_v2 is not exposed. With cookies InstagramIE takes the media/info path, which "
              "carries no comments, so the first page comes from the media/<pk>/comments/ call yt-dlp's comment "
              f"extraction makes ({COOKIE_NOTE}; anonymously that API redirects to login).")
async def instagram_post_comments(url: str = Query(...), cursor: str | None = None,
                                  include_replies: str | None = None) -> dict[str, Any]:
    shortcode, canonical = post_url(url)
    _no_pagination(cursor, "Comment")
    if truthy(include_replies):
        raise NotSupported("Replies are not available anonymously: the logged-out post query returns no reply "
                           "threads and yt-dlp makes no replies request")
    product, media = await fetch_post(canonical)
    if media is None:
        data = await with_ydl(PLATFORM, _comments_api_flow(_post_code(product, shortcode)))
        return {"comments": [map_comment(n) for n in traverse_obj(data, ("comments", ..., {dict}))]}
    return {"comments": [map_comment(n) for n in comment_nodes(media)]}


@sc_get(router, "/v2/instagram/media/transcript", source="yt-dlp InstagramIE + local Whisper (socialscrape.transcribe)",
        notes="Needs SOCIALSCRAPE_TRANSCRIBE (faster-whisper/mlx-whisper); otherwise 501 transcription_disabled "
              "(501 transcription_unavailable when the backend's package isn't installed). "
              "One entry per video (carousel videos via playlist_items; carousel images are skipped). text is "
              "null for videos Instagram marks has_audio=false, and usually null when Whisper finds no speech "
              "(best effort). Posts without a video (images, image-only carousels) return 404 no_video. "
              "Media over 120 s needs allow_long_transcription=true (400 otherwise). Verified with mlx-whisper only.")
async def instagram_transcript(url: str = Query(...), allow_long_transcription: str | None = None
                               ) -> dict[str, Any]:
    shortcode, canonical = post_url(url)
    transcribe.require_enabled()  # 501 before any Instagram request
    product, _ = await fetch_post(canonical)
    code = _post_code(product, shortcode)
    if not shortcode:  # a share link: yt-dlp downloads the post fetch_post found behind it
        canonical = f"{WEB}/p/{code}/"
    videos = video_items(product)
    if not videos:
        raise NotFound("This post has no video to transcribe", code="no_video")
    allow_long = allow_long_transcription == "true"
    carousel = bool(product.get("carousel_media"))
    transcripts = []
    for index, item in videos:
        entry = {"id": str_or_none(item.get("pk")), "shortcode": item.get("code") or code, "text": None}
        transcripts.append(entry)
        if item.get("has_audio") is False:  # video-only stream: nothing to transcribe (ffmpeg finds no audio)
            continue
        opts = {"playlist_items": str(index)} if carousel else {}
        result = await transcribe.transcribe(canonical, platform=PLATFORM, allow_long=allow_long,
                                             duration=video_duration(item), **opts)
        entry["text"] = result.text or None
    return {"transcripts": transcripts}


@sc_get(router, "/v1/instagram/profile", source="Instagram signed-in profile/posts GraphQL queries or logged-out Relay page",
        notes="With saved account cookies, reads the signed-in client's profile and posts queries: exact media "
              "count, follower/following counts, bio, links, HD avatar and public business fields when exposed. "
              "The returned profile ID and username must match the requested route. "
              "Without cookies, from the logged-out profile page: bio, bio_links, follower/following counts, verified/private, "
              "fbid, the first 12 posts (slim nodes: no counts/timestamps/video URLs, has_audio or "
              "comments_disabled) with Instagram's own page_info. edge_owner_to_timeline_media.count comes from "
              "the page's 'N Posts' text and is omitted when Instagram abbreviates it. Not on the page: "
              "business/category fields, external_url, profile_pic_url_hd, reels/collections/related-profile "
              "edges. A profile Instagram restricts for logged-out visitors (age-gated brands: 'Restricted "
              "profile') returns 403 restricted_profile here and on user/posts, user/reels and user/highlights. "
              "trim is ignored.")
async def instagram_profile(handle: str = Query(...), trim: str | None = None) -> dict[str, Any]:
    user, html = await fetch_profile(clean_handle(handle))
    return {"data": {"user": map_profile(user, post_count_from_meta(html))}}


@router.post("/v1/instagram/profile", summary="Profile (POST compatibility)", include_in_schema=True,
             tags=["Instagram"], responses=error_responses(400, 401, 403, 404, 500, 502, 503, "default"))
async def instagram_profile_post(request: Request) -> Any:
    try:
        payload = await request.json()
    except ValueError:
        raise BadRequest("Send a JSON object with handle") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("handle"), str) or not payload["handle"].strip():
        raise BadRequest("handle is required")
    return {"success": True, "credits_charged": 0, **await instagram_profile(payload["handle"], payload.get("trim"))}


@sc_get(router, "/v1/instagram/profile/post-count", source="Instagram signed-in profile media_count or logged-out page metadata",
        responses={422: {"model": ErrorResponse, "description": "Only an abbreviated post count is shown and "
                         "allow_estimated isn't true (estimate_only, with estimated_media_count)"}},
        notes="With saved account cookies, uses the exact numeric media_count from the signed-in profile query. "
              "Without cookies, parses the 'N Posts' figure of the logged-out profile page. Abbreviated values (12K, 1.2M) need "
              "allow_estimated=true, otherwise 422 estimate_only; a page without the figure returns 500. A "
              "restricted (age-gated) profile still shows the figure, so it is counted too (403 "
              "restricted_profile only when its page has none).")
async def instagram_post_count(handle: str = Query(...), allow_estimated: str | None = None) -> dict[str, Any]:
    handle = clean_handle(handle)
    try:
        user, html = await fetch_profile(handle)
        count = user.get("media_count")
        found = ((count, False) if isinstance(count, int) and not isinstance(count, bool) and count >= 0
                 else post_count_from_meta(html))
    except RestrictedProfile as exc:  # no profile data for logged-out visitors, but og:description keeps the count
        found = post_count_from_meta(exc.html)
        if found is None:
            raise
    if found is None:
        raise UpstreamError("Instagram did not show a post count for this profile", code="post_count_unavailable")
    count, estimated = found
    if estimated and not truthy(allow_estimated):
        raise ApiError("Instagram only shows an abbreviated post count for this profile; pass "
                       "allow_estimated=true to accept the scaled estimate", status=422, code="estimate_only",
                       estimated_media_count=count)
    return {"handle": handle, "media_count": count, "is_estimated": estimated}


_DETAILS_NOTE = ("Each item is completed by the logged-out post query InstagramIE makes (one request per item, in "
                 "turn): taken_at and created_at, the caption with its pk/created_at, comment_count, like_count, "
                 "image_versions2, video_versions, has_audio and video_duration. An item user/posts or user/reels "
                 "got in the last 15 minutes isn't asked again (listing an account's posts and its reels shares "
                 "them), and a reel without one takes the caption the posts tab gave its shortcode. Those requests "
                 "stop at the first one Instagram refuses (a GraphQL errors list or 'Please wait a few minutes' "
                 "included) or after 40 s; the items left (and any Instagram doesn't answer for) lack those fields "
                 "rather than carry guesses.")


def _require_play_counts(items: list[dict], *, reels: bool = False) -> None:
    """The documented opt-in rejects partial video metrics; visible zero is valid."""
    missing = 0
    for item in items:
        media = item.get("media") if isinstance(item.get("media"), dict) else item
        if not reels and media.get("media_type") != 2 and media.get("is_video") is not True:
            continue
        value = media.get("play_count")
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or isinstance(value, float) and not math.isfinite(value) or value < 0):
            missing += 1
    if missing:
        raise UpstreamBlocked("Instagram did not return a numeric nonnegative play_count for every video. "
                              "No partial success was returned.", code="play_counts_unavailable", missing_count=missing)


@sc_get(router, "/v2/instagram/user/posts",
        source="Instagram signed-in profile/posts GraphQL queries or logged-out profile/tab/post queries",
        notes="With saved account cookies, reads complete native pages from the signed-in posts query with its "
              "real cursor, dates, captions and crawler-visible play counts. Every returned item is kept; the "
              "platform may return fewer than the requested 12. require_play_counts=true rejects a page with "
              "unknown video play counts. Without cookies, 12 posts per page, newest first after any pinned ones, from the logged-out profile's posts tab: id, "
              "code, media_type, product_type, caption, display_uri, url and each post's own author "
              "(collaborators included); like_count and play_count (= ig_play_count) from the tab's query as "
              "Instagram serves it to crawlers (hidden counts are left out). " + _DETAILS_NOTE + " next_max_id "
              "is Instagram's cursor for the next page (the tab's query with `after`, as the page's own scrolling "
              "makes it) and more_available says whether there is one; a cursor Instagram can't continue from "
              "returns 400 bad_cursor. A restricted (age-gated) profile returns 403 restricted_profile. trim is "
              "ignored.")
async def instagram_user_posts(handle: str = Query(...), next_max_id: str | None = None,
                               trim: str | None = None, require_play_counts: str | None = None) -> dict[str, Any]:
    posts = map_user_posts(await fetch_feed(clean_handle(handle), "", next_max_id or None))
    if require_play_counts == "true":
        _require_play_counts(posts["items"])
    return posts


@sc_get(router, "/v1/instagram/user/reels",
        source="Instagram signed-in reels GraphQL/media-info queries or logged-out reels-tab/post queries",
        notes="With saved account cookies, reads the signed-in reels grid with its real cursor, and completes "
              "dates/captions using authenticated media info. A failed or mismatched detail is an error. "
              "require_play_counts=true rejects unknown play counts. Without cookies, 12 reels per page from the logged-out profile's reels tab: id, code, play_count (= ig_play_count), "
              "comment_count, like_count, display_uri, url and the author. " + _DETAILS_NOTE + " max_id is "
              "Instagram's cursor for the next page (the tab's query with `after`); a cursor Instagram can't "
              "continue from returns 400 bad_cursor. handle only: user_id alone returns 501. A private account "
              "with nothing visible returns 403, as does a restricted (age-gated) profile (restricted_profile). "
              "trim is ignored.")
async def instagram_user_reels(user_id: str | None = None, handle: str | None = None, max_id: str | None = None,
                               trim: str | None = None, require_play_counts: str | None = None) -> dict[str, Any]:
    require_one(user_id=user_id, handle=handle)
    if not handle:
        raise NotSupported("Lookup by user_id is not supported; pass handle")
    feed = await fetch_feed(clean_handle(handle), "reels", max_id or None)
    reels = map_user_reels(feed)
    if not reels["items"] and feed.user.get("is_private") is True:  # collab media of a private account stays visible
        raise Forbidden("This account is private")
    if require_play_counts == "true":
        _require_play_counts(reels["items"], reels=True)
    return reels


@sc_get(router, "/v1/instagram/user/highlights", source="Instagram signed-in highlight-tray query or logged-out Relay profile",
        notes="With saved account cookies, reads the signed-in highlight tray and cover URLs. "
              "handle only: user_id alone returns 501 (no extractor resolves an id to a profile). Returns the "
              "highlights the logged-out page embeds (first 10); a private account without any returns 403, as does "
              "a restricted (age-gated) profile (restricted_profile). "
              "cover_media.thumbnail_src is not exposed, only cover_media_cropped_thumbnail.url.")
async def instagram_highlights(user_id: str | None = None, handle: str | None = None) -> dict[str, Any]:
    require_one(user_id=user_id, handle=handle)
    if not handle:
        raise NotSupported("Lookup by user_id is not supported; pass handle")
    user, _ = await fetch_profile(clean_handle(handle), highlights=True)
    highlights = map_highlights(user)
    if not highlights and user.get("is_private") is True:
        raise Forbidden("This account is private")
    return {"highlights": highlights}


@sc_get(router, "/v1/instagram/user/highlight/detail",
        source="yt-dlp InstagramStoryIE's highlight page (its prefetched feed/reels_media answer)",
        notes="The reel Instagram prefetches into the logged-out /stories/highlights/<id>/ page (the answer of the "
              "feed/reels_media call InstagramStoryIE makes, which itself answers anonymous clients with a web "
              "page), trimmed to ScrapeCreators' keys; media_count is the number of items. With cookies a page "
              "without it falls back to that call. cover_media.crop_rect holds fractions (SC's example shows "
              "integers). Accepts the numeric id, 'highlight:<id>', a /stories/highlights/<id>/ URL or the app's "
              "share link (/s/<base64 of 'highlight:<id>'>). The aliases /v1/instagram/highlights/details and "
              "/v1/instagram/highlight/details redirect here, their highlight_id param becoming id.")
async def instagram_highlight_detail(id: str | None = None) -> dict[str, Any]:
    require_one(id=id)
    highlight_id = clean_highlight_id(id)
    reel = await with_ydl(PLATFORM, _highlight_flow(highlight_id))
    if not reel:
        raise NotFound("Highlight not found (or not visible without login)")
    return map_highlight_reel(reel)


@sc_get(router, "/v1/instagram/search/popular", source="yt-dlp InstagramTagIE page (/explore/tags/ -> /popular/)",
        notes="First page of Instagram's logged-out Popular page for the topic (usually 12 posts), title, "
              "total_media_count (when shown, abbreviated -> scaled), description and suggested_terms. Broad topics "
              "Instagram serves as a category hub (e.g. basketball) return every post of the hub's sections (up to "
              "~220), without captions or usernames, and the section keywords as suggested_terms. The query (or the "
              "address of its /explore/tags/ or /popular/ page) is matched case-insensitively and echoed "
              "lower-cased. Next pages need a query yt-dlp never makes: no cursor/has_more is returned and passing "
              "a cursor returns 501.")
async def instagram_search_popular(query: str = Query(...), cursor: str | None = None) -> dict[str, Any]:
    topic = clean_topic(query)
    _no_pagination(cursor, "Popular search")
    data, html = await fetch_topic(topic)
    return map_popular(topic, data, html)


_HASHTAG_DATES = ("last-hour", "last-day", "last-week", "last-month", "last-year")
_REELS_DATES = ("last-week", "last-month", "last-year")  # ScrapeCreators doesn't take hour/day for reels


def _check_date_posted(value: str | None, allowed: tuple[str, ...]) -> None:
    """400 for a date_posted outside ScrapeCreators' enum (the in-enum values are then refused with 501)."""
    if value and value not in allowed:
        raise BadRequest(f"date_posted must be one of: {', '.join(allowed)}", code="bad_date_posted")


def _page_number(page: str | None, name: str = "page", code: str = "bad_page") -> int:
    """ScrapeCreators' Google results page number (`page`, or the hashtag search's `cursor`): 1-11, blank is 1."""
    try:
        number = int(float(page)) if page not in (None, "") else 1
    except (ValueError, OverflowError):
        raise BadRequest(f"{name} must be a number between 1 and 11", code=code) from None
    if not 1 <= number <= 11:
        raise BadRequest(f"{name} must be between 1 and 11", code=code)
    return number


@sc_get(router, "/v1/instagram/search/hashtag", source="yt-dlp InstagramTagIE page (/explore/tags/<tag>)",
        notes="Not Google-indexed like ScrapeCreators: the posts Instagram's logged-out tag page shows (usually 12, "
              "mostly reels), filtered by media_type=reels. Broad tags Instagram serves as a category hub (e.g. "
              "basketball) return every post of the hub's sections (up to ~220), without captions or usernames. "
              "The hashtag (or the address of its /explore/tags/ page) is matched case-insensitively and echoed "
              "lower-cased. date_posted (one of ScrapeCreators' values; others return 400) and cursor 2-11 return "
              "501 (cursor 1 is the first page; a cursor outside 1-11 or not a number returns 400, as in "
              "ScrapeCreators); no cursor is returned. Videos carry video_play_count (Instagram's play count); no "
              "like/comment counts, timestamps or comments.")
async def instagram_search_hashtag(hashtag: str = Query(...), date_posted: str | None = None,
                                   media_type: str | None = None, cursor: str | None = None) -> dict[str, Any]:
    tag = clean_topic(hashtag, single_word=True)
    kind = (media_type or "all").lower()
    if kind not in ("all", "reels"):
        raise BadRequest("media_type must be 'all' or 'reels'", code="bad_media_type")
    number = _page_number(cursor, "cursor", "bad_cursor")
    _check_date_posted(date_posted, _HASHTAG_DATES)
    if date_posted:
        raise NotSupported("date_posted needs Google-indexed results; the Instagram tag page has no date filter")
    if number > 1:
        raise NotSupported("Hashtag search pagination is not available: the next page needs an Instagram query "
                           "yt-dlp never makes", code="pagination_unavailable")
    data, _ = await fetch_topic(tag)
    posts = [map_hashtag_post(n) for n in topic_nodes(data) if kind == "all" or _is_video_node(n)]
    return {"hashtag": tag, "media_type": kind, "posts": posts}


@sc_get(router, "/v2/instagram/reels/search", source="yt-dlp InstagramTagIE page (/explore/tags/ -> /popular/)",
        notes="Not Google-indexed like ScrapeCreators: the reels on Instagram's logged-out Popular page for the "
              "query (first page, usually 12). Broad topics Instagram serves as a category hub (e.g. basketball) "
              "return every reel of the hub's sections (up to ~220), without captions or usernames. The query (or "
              "the address of its /explore/tags/ or /popular/ page) is matched case-insensitively. Each reel "
              "carries video_play_count (Instagram's play count); no like/comment counts, taken_at, location or "
              "audio. page 2-11 and date_posted (last-week/last-month/last-year) return 501; page outside 1-11 or "
              "another date_posted returns 400.")
async def instagram_reels_search(query: str = Query(...), date_posted: str | None = None,
                                 page: str | None = None) -> dict[str, Any]:
    topic = clean_topic(query)
    number = _page_number(page)
    _check_date_posted(date_posted, _REELS_DATES)
    if number > 1:
        raise NotSupported("Only page 1 is available: later Popular pages need a query yt-dlp never makes",
                           code="pagination_unavailable")
    if date_posted:
        raise NotSupported("date_posted needs Google-indexed results; the Instagram Popular page has no date filter")
    data, _ = await fetch_topic(topic)
    return {"reels": [map_hashtag_post(n) for n in topic_nodes(data) if _is_video_node(n)]}


def map_search(query: str, data: dict[str, Any]) -> dict[str, Any]:
    """Blended top-search answer -> SC search's data: users, hashtags and places, each with its position."""
    def entries(key: str, inner: str) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
        """(position, entry, its user/hashtag/place) for each entry, the position its index when it has none."""
        out = []
        for index, entry in enumerate(traverse_obj(data, (key, ..., {dict}))):
            position = entry["position"] if isinstance(entry.get("position"), int) else index
            out.append((position, entry, traverse_obj(entry, (inner, {dict})) or {}))
        return out

    users = [compact({
        "full_name": user.get("full_name"), "id": str_or_none(user.get("pk") or user.get("id")),
        "is_verified": user.get("is_verified"), "position": position, "profile_pic_url": user.get("profile_pic_url"),
        "username": user.get("username"),
    }) for position, _, user in entries("users", "user") if user.get("username")]
    hashtags = [compact({
        "id": str_or_none(tag.get("id")), "media_count": tag.get("media_count"), "name": tag.get("name"),
        "position": position, "search_result_subtitle": tag.get("search_result_subtitle"),
    }) for position, _, tag in entries("hashtags", "hashtag") if tag.get("name")]
    places = [compact({
        "facebook_places_id": str_or_none(traverse_obj(place, ("location", "facebook_places_id"))),
        "id": str_or_none(traverse_obj(place, ("location", "pk"))), "name": traverse_obj(place, ("location", "name")),
        "position": position, "subtitle": place.get("subtitle"), "title": place.get("title"),
    }) for position, _, place in entries("places", "place") if place]
    return {"hashtags": hashtags, "keywords": [], "places": places, "query": query,
            "rank_token": str_or_none(data.get("rank_token")), "users": users}


@sc_get(router, "/v1/instagram/search", source="Instagram signed-in PolarisAPITopsearchQuery (blended)",
        status="needs_setup",
        notes="Instagram's blended top search (users, hashtags, places), which it serves to signed-in sessions only: "
              "the logged-out call answers with a 404 page and the logged-out web has no search box. Without an "
              "Instagram cookie account in Platform settings this returns 501 platform_auth_required (a setup "
              "gap, which the ScrapeCreators fallback takes when enabled), not a 500. keywords is always empty.")
async def search_instagram(query: str = Query(..., min_length=1)) -> dict[str, Any]:
    from .instagram_private import topsearch  # instagram_private imports this module

    data = await topsearch(query, "blended")
    return {"data": map_search(query, data if isinstance(data, dict) else {})}


_EMBED_PROFILE = re.compile(r'"isProfileEmbed":true|<div class="Embed" ')  # nasa's embed page, 2026-10-24
_EMBED_APP = re.compile(r'"PolarisEmbedSimple"')


@sc_get(router, "/v1/instagram/user/embed", source="public Instagram profile embed page",
        notes="Returns Instagram's public profile embed HTML. The caller embeds or sanitizes it according to context. "
              "404 when the embed page has no profile to show (an unknown name, as Instagram answers it); an empty "
              "or block page in its place is a 503 upstream_blocked, not HTML returned as success.")
async def instagram_user_embed(handle: str = Query(...)) -> dict[str, Any]:
    name = clean_handle(handle)
    html, final_url = await with_ydl(PLATFORM, _page_flow("InstagramUser", f"{WEB}/{name}/embed/", name))
    if InstagramBaseIE._is_login_redirect(final_url):
        raise UpstreamBlocked(_LOGIN_WALL)
    if _EMBED_PROFILE.search(html):
        return {"html": html}
    if _EMBED_APP.search(html):  # the embed app, without a profile to show (isProfileEmbed false, contextJSON null)
        raise NotFound("Instagram has no embeddable profile at this name (unknown, private or restricted)")
    raise UpstreamBlocked(_EMPTY_PAGE if not html.strip() else _NOT_APP)


@sc_get(router, "/v1/instagram/reels/trending", source="public Instagram Popular reels composition",
        notes="Current reels surfaced by Instagram's public Popular topic; not an official global ranking.")
async def instagram_reels_trending() -> dict[str, Any]:
    data, _ = await fetch_topic("reels")
    return {"reels": [map_hashtag_post(node) for node in topic_nodes(data) if _is_video_node(node)]}


UNSUPPORTED = {
}
