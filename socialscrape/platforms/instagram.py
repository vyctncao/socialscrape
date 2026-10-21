"""Instagram routes, built on what yt-dlp's Instagram extractors download anonymously.

Posts: InstagramIE runs unchanged (session setup, get_ruling check, the logged-out
`PolarisLoggedOutDesktopWWWPostRootContentQuery` GraphQL call, /p/ page fallback)
while the raw media it parses is recorded, so fields yt-dlp drops (owner, caption
ids, tagged users, the first comments) are still available.
Profiles, tags and highlights: the pages/API InstagramUserIE (profile page, plus
its /reels/ tab), InstagramTagIE (/explore/tags/ -> /popular/) and InstagramStoryIE
download, fetched through those extractor instances and read from the Relay data
Instagram embeds for logged-out visitors.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import unicodedata
import urllib.parse
from collections.abc import Callable
from typing import Any

import yt_dlp
from fastapi import APIRouter, Query
from yt_dlp.extractor.instagram import InstagramBaseIE, InstagramIE, InstagramStoryIE, _id_to_pk
from yt_dlp.networking import HEADRequest
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError, parse_duration, str_or_none, traverse_obj, unescapeHTML

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
    require_one,
)
from ..registry import sc_get
from ..util import compact, iso_utc, parse_count, truthy
from ..ytdlp import with_ydl
from ._instagram_shapes import HIGHLIGHT_REEL, prune

router = APIRouter()
log = logging.getLogger("socialscrape.instagram")

PLATFORM = "instagram"
WEB = "https://www.instagram.com"
RULING_PATH = "/api/v1/web/get_ruling_for_content/"
COOKIE_NOTE = "requires SOCIALSCRAPE_COOKIES_INSTAGRAM; not verified anonymously"

_SHORTCODE = re.compile(r"[A-Za-z0-9_-]{5,64}")
_SHORTCODE_CHARS = re.compile(r"[A-Za-z0-9_-]+")  # _id_to_pk's alphabet
# A link from the app's share sheet (/share/reel/<id>, /share/<id>), whose id (as long as a shortcode, so that
# '/share/reel/' isn't one) redirects to the post.
_SHARE_URL = re.compile(r"https://www\.instagram\.com/share/(?:[a-z]+/)?[A-Za-z0-9_-]{5,64}/?(?:[?#].*)?")
# No leading/trailing '.' and no '..', as on Instagram (and so '.'/'..' can't collapse /<handle>/ to the homepage).
_HANDLE = re.compile(r"(?!\.)(?!.*\.\.)[A-Za-z0-9._]{1,30}(?<!\.)")
_PROFILE_URL = re.compile(
    r"https://www\.instagram\.com/([A-Za-z0-9._]{1,30})(?:/(?:reels|tagged|saved|guides|featured))?/?(?:[?#].*)?")
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
    """'@jane', 'jane', 'jane/' or a profile URL (optionally a tab of it) -> 'jane'. 400 for anything else. The
    '@' may be the full-width '＠' of Japanese and Chinese keyboards."""
    value = (handle or "").strip()
    if re.search(r"instagram\.com", value, re.I):
        m = _PROFILE_URL.fullmatch(canonical_url(value))
        value = m.group(1) if m else ""
    value = value.lstrip("@＠﹫").rstrip("/")
    if not _HANDLE.fullmatch(value) or value.lower() in _RESERVED_PATHS:
        raise BadRequest("Not a valid Instagram handle", code="bad_handle")
    return value


def clean_highlight_id(value: str | None) -> str:
    """'18382085791177637', 'highlight:1838...', a /stories/highlights/<id>/ URL or the app's share link
    (/s/<base64 of 'highlight:<id>'>) -> the numeric id."""
    value = (value or "").strip()
    if re.search(r"instagram\.com", value, re.I):
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
    if re.search(r"instagram\.com", value, re.I) and (m := _TOPIC_URL.fullmatch(canonical_url(value))):
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
        except ValueError:
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
        source = ("SOCIALSCRAPE_COOKIES_INSTAGRAM" if get_settings().cookie_file(PLATFORM)
                  else "SOCIALSCRAPE_COOKIES_FROM_BROWSER")
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
            raise
        if not products:
            raise NotFound("No data returned for this post")
        return products[0], polaris_media(rec.bodies)

    return go


async def fetch_post(url: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(media item yt-dlp extracted, logged-out xig_polaris_media wrapper or None when logged in)."""
    return await with_ydl(PLATFORM, _post_flow(url))


def _page_flow(ie_name: str, url: str, item_id: str) -> Callable[[yt_dlp.YoutubeDL], tuple[str, str]]:
    def go(ydl: yt_dlp.YoutubeDL) -> tuple[str, str]:
        ie = ydl.get_info_extractor(ie_name)
        logged_in = ie._is_logged_in
        html, urlh = ie._download_webpage_handle(url, item_id, impersonate=ie._can_impersonate)
        if logged_in and InstagramBaseIE._is_login_redirect(urlh.url):
            raise SessionRejected()  # not the logged-out rate limit fetch_page reports for this redirect
        return html, urlh.url

    return go


async def fetch_page(ie_name: str, url: str, item_id: str, what: str) -> tuple[str, list[dict[str, Any]]]:
    """Download a logged-out page the way `ie_name` does -> (html, relay results)."""
    html, final_url = await with_ydl(PLATFORM, _page_flow(ie_name, url, item_id))
    if urllib.parse.urlparse(final_url).netloc.lower() not in _INSTAGRAM_HOSTS:
        # Reserved names such as /developer/ redirect off Instagram (developers.facebook.com).
        raise NotFound(f"{what} not found")
    if InstagramBaseIE._is_login_redirect(final_url):
        raise UpstreamBlocked(_LOGIN_WALL)
    page = _PAGE_ID.search(html)
    if page and page.group(1) == "httpErrorPage":
        raise NotFound(f"{what} not found")
    return html, page_relay(html)


def profile_restriction(html: str) -> str | None:
    """Why Instagram hides a profile page from logged-out visitors ('Restricted profile: You must be 21 years old
    or over to see this profile'), or None."""
    for blob in _GATING.findall(html):
        try:
            gating = json.loads(blob)
        except ValueError:
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
    return UpstreamError("Instagram returned the profile page without profile data", code="no_profile_data")


async def fetch_profile(handle: str, tab: str = "") -> tuple[dict[str, Any], str]:
    """Profile page InstagramUserIE downloads (or its /reels/ tab) -> (merged xig_user_by_username, html)."""
    url = f"{WEB}/{handle}/{tab + '/' if tab else ''}"
    html, results = await fetch_page("InstagramUser", url, handle, "Profile")
    user: dict[str, Any] = {}
    for part in traverse_obj(results, (..., "data", "xig_user_by_username", {dict})):
        user.update(part)  # root query (bio, counts, highlights) + the tab's query (timeline or clips)
    if not user.get("pk"):
        raise missing_profile_error(html)
    return user, html


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
    candidates = traverse_obj(media, ("image_versions2", "candidates", lambda _, v: v["url"]))
    if candidates:
        return max(candidates, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))["url"]
    return media.get("display_uri")


def _video_url(media: dict[str, Any]) -> str | None:
    return traverse_obj(media, ("video_versions", 0, "url", {str}))


def video_duration(media: dict[str, Any]) -> float | None:
    """Item's video_duration, else the DASH manifest's mediaPresentationDuration."""
    if isinstance(media.get("video_duration"), (int, float)):
        return float(media["video_duration"])
    m = re.search(r'mediaPresentationDuration="([^"]+)"', media.get("video_dash_manifest") or "")
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
        asset = music.get("music_asset_info") or {}
        consumption = music.get("music_consumption_info") or {}
        return compact({
            "artist_name": asset.get("display_artist"),
            "song_name": asset.get("title"),
            "uses_original_audio": False,
            "should_mute_audio": consumption.get("should_mute_audio"),
            "should_mute_audio_reason": consumption.get("should_mute_audio_reason"),
            "audio_id": str_or_none(asset.get("audio_asset_id")),
        })
    return None


def _owner(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
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
        user, pos = tag["user"], tag.get("position") or [None, None]
        edges.append({"node": compact({
            "user": compact({k: user.get(k)
                             for k in ("full_name", "id", "is_verified", "profile_pic_url", "username")}),
            "x": pos[0] if len(pos) > 0 else None,
            "y": pos[1] if len(pos) > 1 else None,
        })})
    return edges


def _sidecar_child(media: dict[str, Any]) -> dict[str, Any]:
    is_video = media.get("media_type") == 2
    return compact({
        "__typename": "XDT" + _GRAPH_TYPE.get(media.get("media_type"), "GraphImage"),
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
    user = node.get("user") or {}
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
    caption = product.get("caption") or {}
    page_info = traverse_obj(media, ("comments_connection", "page_info", {dict}))
    carousel = traverse_obj(product, ("carousel_media", ..., {dict}))
    out = compact({
        "id": str_or_none(product.get("pk")),
        "shortcode": product.get("code"),
        "__typename": "XDT" + _GRAPH_TYPE.get(mtype, "GraphImage"),
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
    user = node.get("user") or {}
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
        "__typename": _GRAPH_TYPE.get(node.get("media_type"), "GraphImage"),
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
    timeline = user.get("polaris_ordered_timeline_connection") or {}
    page_info = timeline.get("page_info") or {}
    return compact({
        "biography": user.get("biography"),
        "bio_links": [compact({k: link.get(k) for k in ("title", "lynx_url", "url", "link_type")})
                      for link in traverse_obj(user, ("bio_links", ..., {dict}))],
        "fbid": str_or_none(user.get("id")),
        "full_name": user.get("full_name"),
        "id": str_or_none(user.get("pk")),
        "is_private": user.get("is_private"),
        "is_verified": user.get("is_verified"),
        "profile_pic_url": user.get("profile_pic_url"),
        "username": user.get("username"),
        "edge_followed_by": compact({"count": user.get("follower_count")}),
        "edge_follow": compact({"count": user.get("following_count")}),
        "edge_owner_to_timeline_media": compact({
            "count": post_count[0] if post_count and not post_count[1] else None,
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
        "fbid_v2": str_or_none(user.get("id")),
    })


def _node_author(node: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """A timeline/clips node's own author (collab posts list the collaborator), filled from the profile."""
    user = node.get("user") if isinstance(node.get("user"), dict) else {}
    if not user.get("pk") or str(user["pk"]) == str(profile.get("pk")):
        return _user_v2({**profile, **{k: v for k, v in user.items() if v is not None}})
    return _user_v2(user)


def _media_id(node: dict[str, Any], author: dict[str, Any]) -> str | None:
    return f"{node['pk']}_{author['pk']}" if node.get("pk") and author.get("pk") else str_or_none(node.get("pk"))


_COUNT_KEYS = ("like_count", "comment_count", "play_count")


def clip_counts(user: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Reels-tab clips by pk -> their like/comment/play counts (the posts tab has none).

    The logged-out play_count is Instagram's alone, so it is also ig_play_count (as in user/reels).
    """
    out = {}
    for node in traverse_obj(user, ("polaris_clips_connection", "edges", ..., "node", {dict})):
        if not node.get("pk"):
            continue
        counts = {k: node[k] for k in _COUNT_KEYS if isinstance(node.get(k), int)}
        if "play_count" in counts:
            counts["ig_play_count"] = counts["play_count"]
        out[str(node["pk"])] = counts
    return out


def map_user_posts(user: dict[str, Any], counts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Profile page's posts-tab timeline -> SC v2 user/posts (first page only); counts joined by pk."""
    items = []
    for node in traverse_obj(user, ("polaris_ordered_timeline_connection", "edges", ..., "node", {dict})):
        text = traverse_obj(node, ("caption", "text", {str}))
        author = _node_author(node, user)
        items.append(compact({
            "id": _media_id(node, author),
            "code": node.get("code"),
            "media_type": node.get("media_type"),
            "caption": {"text": text} if text is not None else None,
            "display_uri": node.get("display_uri"),
            "user": author,
            "owner": author,
            "product_type": node.get("product_type"),
            **(counts or {}).get(str(node.get("pk")), {}),
            "url": f"{WEB}/p/{node['code']}/" if node.get("code") else None,
        }))
    profile = _user_v2(user)
    top = {k: profile[k] for k in ("pk", "pk_id", "full_name", "is_private", "id", "username", "is_verified",
                                   "profile_pic_url") if k in profile}
    return {"items": items, "num_results": len(items), "user": top}


def map_user_reels(user: dict[str, Any]) -> dict[str, Any]:
    """Profile reels tab (polaris_clips_connection) -> SC user/reels (first page only)."""
    items = []
    for node in traverse_obj(user, ("polaris_clips_connection", "edges", ..., "node", {dict})):
        author = _node_author(node, user)
        items.append(compact({
            "id": _media_id(node, author),
            "code": node.get("code"),
            "play_count": node.get("play_count"),
            "ig_play_count": node.get("play_count"),
            "comment_count": node.get("comment_count"),
            "like_count": node.get("like_count"),
            "display_uri": node.get("display_uri"),
            "user": author,
            "owner": author,
            "url": f"{WEB}/reel/{node['code']}/" if node.get("code") else None,
        }))
    return {"items": items}


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
        user = node.get("user") or {}
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
    user = node.get("user") or {}
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


# --- routes ---------------------------------------------------------------------------


def _no_pagination(value: str | None, name: str) -> None:
    if value:
        raise NotSupported(f"{name} pagination is not available: the next page needs an Instagram query "
                           "yt-dlp never makes", code="pagination_unavailable")


@sc_get(router, "/v1/instagram/post", source="yt-dlp InstagramIE (logged-out PostRootContentQuery / media info)",
        notes="Mapped from the media InstagramIE parses (logged-out GraphQL, else the /p/ page). Anonymous "
              "responses have no video_play_count/video_view_count (only the logged-in media/info item has them: "
              f"{COOKIE_NOTE}); edge_media_to_parent_comment.edges holds the ~15 comments the logged-out query "
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
        data = await with_ydl(PLATFORM, _comments_api_flow(product.get("code") or shortcode))
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
    if not shortcode:  # a share link: yt-dlp downloads the post fetch_post found behind it
        canonical = f"{WEB}/p/{product['code']}/"
    videos = video_items(product)
    if not videos:
        raise NotFound("This post has no video to transcribe", code="no_video")
    allow_long = allow_long_transcription == "true"
    carousel = bool(product.get("carousel_media"))
    transcripts = []
    for index, item in videos:
        entry = {"id": str_or_none(item.get("pk")), "shortcode": item.get("code") or product.get("code"), "text": None}
        transcripts.append(entry)
        if item.get("has_audio") is False:  # video-only stream: nothing to transcribe (ffmpeg finds no audio)
            continue
        opts = {"playlist_items": str(index)} if carousel else {}
        result = await transcribe.transcribe(canonical, platform=PLATFORM, allow_long=allow_long,
                                             duration=video_duration(item), **opts)
        entry["text"] = result.text or None
    return {"transcripts": transcripts}


@sc_get(router, "/v1/instagram/profile", source="yt-dlp InstagramUserIE profile page (logged-out Relay data)",
        notes="From the logged-out profile page: bio, bio_links, follower/following counts, verified/private, "
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


@sc_get(router, "/v1/instagram/profile/post-count", source="yt-dlp InstagramUserIE profile page (og:description)",
        responses={422: {"model": ErrorResponse, "description": "Only an abbreviated post count is shown and "
                         "allow_estimated isn't true (estimate_only, with estimated_media_count)"}},
        notes="Parses the 'N Posts' figure of the logged-out profile page. Abbreviated values (12K, 1.2M) need "
              "allow_estimated=true, otherwise 422 estimate_only; a page without the figure returns 500. A "
              "restricted (age-gated) profile still shows the figure, so it is counted too (403 "
              "restricted_profile only when its page has none).")
async def instagram_post_count(handle: str = Query(...), allow_estimated: str | None = None) -> dict[str, Any]:
    handle = clean_handle(handle)
    try:
        _, html = await fetch_profile(handle)
        found = post_count_from_meta(html)
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


@sc_get(router, "/v2/instagram/user/posts", source="yt-dlp InstagramUserIE profile page (posts + reels tab Relay data)",
        notes="First 12 posts of the logged-out profile page only: id, code, media_type, product_type, caption "
              "text, display_uri, url and each post's own author (collaborators included). like_count, "
              "comment_count and play_count (= ig_play_count) are joined from the profile's /reels/ tab (a second "
              "page request) and so only appear on reels among its first 12 clips; they are left out if that tab "
              "fails. No taken_at or image/video versions. next_max_id is never returned (InstagramUserIE's "
              "timeline query is rejected by Instagram) and passing one returns 501. A restricted (age-gated) "
              "profile returns 403 restricted_profile. trim is ignored.")
async def instagram_user_posts(handle: str = Query(...), next_max_id: str | None = None,
                               trim: str | None = None) -> dict[str, Any]:
    handle = clean_handle(handle)
    _no_pagination(next_max_id, "Post")
    user, _ = await fetch_profile(handle)
    counts: dict[str, dict[str, Any]] = {}
    product_types = traverse_obj(user, ("polaris_ordered_timeline_connection", "edges", ..., "node", "product_type"))
    if user.get("has_any_clips") is not False and "clips" in product_types:
        try:
            counts = clip_counts((await fetch_profile(handle, "reels"))[0])
        except ApiError as exc:
            log.info("instagram: reels tab for %s failed, posts returned without counts: %s", handle, exc)
    return map_user_posts(user, counts)


@sc_get(router, "/v1/instagram/user/reels", source="yt-dlp InstagramUserIE profile page (/reels/ tab Relay data)",
        notes="First 12 reels of the logged-out profile's reels tab: id, code, play_count (= ig_play_count), "
              "comment_count, like_count, display_uri, url and the author (a collaborator's reel carries only "
              "their pk/id: the tab has no username for them). No caption, taken_at or image/video versions on "
              "that tab. handle only: user_id alone returns 501. max_id is never "
              "returned and passing one returns 501. A private account with nothing visible returns 403, as does a "
              "restricted (age-gated) profile (restricted_profile). trim is ignored.")
async def instagram_user_reels(user_id: str | None = None, handle: str | None = None, max_id: str | None = None,
                               trim: str | None = None) -> dict[str, Any]:
    require_one(user_id=user_id, handle=handle)
    if not handle:
        raise NotSupported("Lookup by user_id is not supported; pass handle")
    handle = clean_handle(handle)
    _no_pagination(max_id, "Reels")
    user, _ = await fetch_profile(handle, "reels")
    reels = map_user_reels(user)
    if not reels["items"] and user.get("is_private") is True:  # collab media of a private account stays visible
        raise Forbidden("This account is private")
    return reels


@sc_get(router, "/v1/instagram/user/highlights", source="yt-dlp InstagramUserIE profile page (highlights Relay data)",
        notes="handle only: user_id alone returns 501 (no extractor resolves an id to a profile). Returns the "
              "highlights the logged-out page embeds (first 10); a private account without any returns 403, as does "
              "a restricted (age-gated) profile (restricted_profile). "
              "cover_media.thumbnail_src is not exposed, only cover_media_cropped_thumbnail.url.")
async def instagram_highlights(user_id: str | None = None, handle: str | None = None) -> dict[str, Any]:
    require_one(user_id=user_id, handle=handle)
    if not handle:
        raise NotSupported("Lookup by user_id is not supported; pass handle")
    user, _ = await fetch_profile(clean_handle(handle))
    highlights = map_highlights(user)
    if not highlights and user.get("is_private") is True:
        raise Forbidden("This account is private")
    return {"highlights": highlights}


@sc_get(router, "/v1/instagram/user/highlight/detail", source="yt-dlp InstagramStoryIE (feed/reels_media API)",
        notes="Reel object from the reels_media call InstagramStoryIE makes for highlight:<id> (works "
              "anonymously), trimmed to ScrapeCreators' keys. cover_media.crop_rect holds fractions (SC's example "
              "shows integers). Accepts the numeric id, 'highlight:<id>', a /stories/highlights/<id>/ URL or the "
              "app's share link (/s/<base64 of 'highlight:<id>'>).")
async def instagram_highlight_detail(id: str | None = None) -> dict[str, Any]:
    require_one(id=id)
    highlight_id = clean_highlight_id(id)
    data = await with_ydl(PLATFORM, _reels_media_flow(highlight_id))
    reel = traverse_obj(data, ("reels", f"highlight:{highlight_id}", {dict}))
    if not reel:
        raise NotFound("Highlight not found (or not visible without login)")
    return prune(reel, HIGHLIGHT_REEL)


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


UNSUPPORTED = {
    "/v1/instagram/basic-profile": (
        "Looks a profile up by numeric user id; no yt-dlp Instagram extractor resolves a user id (InstagramUserIE "
        "and InstagramStoryIE start from a username)."),
    "/v1/instagram/user/tagged-posts": (
        "The tagged tab is a separate logged-in query; no yt-dlp extractor fetches it and the logged-out profile "
        "page only embeds the posts tab."),
    "/v1/instagram/search": (
        "Instagram's top search (users/hashtags/places) API is not used by any yt-dlp extractor."),
    "/v1/instagram/search/profiles": (
        "Profile search needs Instagram's top search API, which no yt-dlp extractor calls."),
    "/v1/instagram/audio/reels": (
        "Reels-by-audio comes from Instagram's clips/music API; InstagramIE explicitly excludes /reels/audio/ URLs "
        "and no extractor calls that API."),
    "/v1/instagram/reels/trending": (
        "ScrapeCreators builds this from the topics Instagram currently features on its /popular/ index, a page "
        "no yt-dlp extractor fetches (InstagramTagIE only opens one known tag), and filters reels by post date, "
        "which the Popular pages don't carry (one InstagramIE extraction per reel)."),
    "/v1/instagram/post/comment/replies": (
        "Replies come from Instagram's child_comments API, which yt-dlp never calls; the logged-out post query "
        "it uses returns no reply threads."),
    "/v1/instagram/user/embed": (
        "The profile embed page (/<handle>/embed/) is not fetched by any yt-dlp extractor."),
}
