"""YouTube listings: channel about + tabs, playlists, search and hashtags.

Everything is read through yt-dlp's YoutubeTabIE: first pages are the same
channel/playlist webpages it downloads (ytInitialData), later pages and sort
chips are the InnerTube browse/search continuations it requests itself
(`_extract_response`), on its 'web' client or, where that one hides data,
its 'mweb' (mobile web) client. Native continuation tokens are wrapped in our
own `ss1.` cursor together with the little page context later pages need.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import re
import time
import unicodedata
import urllib.parse
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Query
from yt_dlp.extractor.youtube import YoutubeBaseInfoExtractor
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError, ISO3166Utils, traverse_obj

from ..cursor import decode_cursor, encode_cursor
from ..errors import BadRequest, NotFound, NotSupported, UpstreamError, require_one
from ..registry import sc_get
from ..util import truthy
from ..ytdlp import with_ydl
from . import _youtube_listings_parse as p

router = APIRouter()
UNSUPPORTED: dict[str, str] = {}

T = TypeVar("T")
YT = p.YT
_CHANNEL_ID = re.compile(r"UC[\w-]{22}")
_PLAYLIST_ID = re.compile(r"[\w-]{10,64}")
_ALBUM_ID = re.compile(r"MPREb_[A-Za-z0-9_-]+")  # a YouTube Music album's browse id
# youtubekids.com (channel, watch?…&list= URLs) and youtube-nocookie.com (the privacy-enhanced embeds of YouTube's
# share dialog, /embed/videoseries?list=…) name the same channels and playlists; yt-dlp takes them too.
_YT_HOST = re.compile(r"(?:www\.|m\.|music\.)?youtube\.com|(?:www\.)?youtube(?:kids|-nocookie)\.com|youtu\.be")
# First path segments that are YouTube pages, not legacy channel names (YoutubeTabIE's own list).
_RESERVED = re.compile(rf"(?:{YoutubeBaseInfoExtractor._RESERVED_NAMES})\b")
_SHORT_KEYS = ("shortsLockupViewModel", "reelItemRenderer")
_SHELF_KEYS = ("gridShelfViewModel", "shelfRenderer", "reelShelfRenderer")
_MWEB_FEED = "FEhashtag"  # cursor 't' meaning: start the hashtag feed on the mweb client
_READ_ON_PAGES = 3  # most pages fetched past one that only repeats items already served (see _read_on)
_EMPTY_PAGE_WAITS = (1.0, 1.0)  # s before each re-ask for a hashtag Shorts page that came back empty (see _continue)
_SRC = "yt-dlp YoutubeTabIE"
_APPROX = ("Counts are YouTube's display values (abbreviated ones like '251M' are approximate); "
           "publishedTime is derived from relative text ('3 weeks ago') and is approximate.")
_NO_PAID_PROMOTIONS = ("is_paid_promotions is not supported: YouTube's paid-promotion search surface is not "
                       "reachable through yt-dlp's extractors")


# --- input -------------------------------------------------------------------

def _word(value: str, extra: str = "") -> bool:
    """Every character is a letter, digit or '_' (like \\w), a combining mark (\\w misses the vowel signs that
    Devanagari, Tamil, Thai… words always carry), a ZWNJ/ZWJ (Indic and Persian spelling) or one of `extra`."""
    return all(c.isalnum() or c in "_\u200c\u200d" or c in extra or unicodedata.category(c).startswith("M")
               for c in value)


def _is_handle(value: str) -> bool:
    """@handle / legacy channel name: 1-100 word characters plus '.', '-' and '·'."""
    return 1 <= len(value) <= 100 and _word(value, ".-·")


def _yt_url(value: str) -> urllib.parse.ParseResult | None:
    """Parse URL-looking input (anything with a '/'); 400 if it points at another site."""
    if "/" not in value:
        return None
    try:
        parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
        host = parsed.hostname or ""
    except ValueError:  # a stray bracket ('youtube.com]/@x') or a full-width '／', '：'... in the host (NFKC'd)
        host = ""
    if not _YT_HOST.fullmatch(host):
        raise BadRequest(f"Not a YouTube URL: {value}")
    return parsed


def _base_from_url(value: str) -> str:
    parsed = _yt_url(value)
    if not parsed:
        raise BadRequest(f"Not a YouTube channel URL: {value}")
    parts = [urllib.parse.unquote(x) for x in parsed.path.split("/") if x]
    if parts and parts[0].startswith("@") and _is_handle(parts[0][1:]):
        return "@" + urllib.parse.quote(parts[0][1:])
    if len(parts) >= 2 and parts[0] in ("channel", "browse") and _CHANNEL_ID.fullmatch(parts[1]):
        return f"channel/{parts[1]}"  # music.youtube.com/browse/UC… is the same channel page
    if len(parts) >= 2 and parts[0] in ("c", "user") and _is_handle(parts[1]):
        return f"{parts[0]}/{urllib.parse.quote(parts[1])}"
    if parsed.hostname != "youtu.be" and parts and _is_handle(parts[0]) and not _RESERVED.match(parts[0]):
        return urllib.parse.quote(parts[0])  # legacy vanity URL (youtube.com/LinusTechTips)
    raise BadRequest(f"Not a YouTube channel URL: {value}")


def _base_of(value: str) -> str | None:
    """A UC… id, an @handle (the '@' may be the full-width '＠' of Japanese and Chinese keyboards) or a channel URL
    -> base; None for anything else (e.g. a bare name)."""
    if _CHANNEL_ID.fullmatch(value):
        return f"channel/{value}"
    if value.startswith(("@", "＠", "﹫")) and "/" not in value:
        if not _is_handle(value[1:]):
            raise BadRequest(f"Invalid YouTube handle: {value}")
        return "@" + urllib.parse.quote(value[1:])
    return _base_from_url(value) if "/" in value else None


def channel_base(channel_id: str | None, handle: str | None, url: str | None = None) -> str:
    """channelId / handle / url -> youtube.com path prefix ('channel/UC…', '@handle', 'c/…', a vanity name).

    Each parameter also takes the other unambiguous forms (UC… id, @handle, channel URL)."""
    if channel_id := (channel_id or "").strip():
        if base := _base_of(channel_id):
            return base
        raise BadRequest("channelId must look like UCxxxxxxxxxxxxxxxxxxxxxx (or be an @handle / channel URL)")
    if handle := (handle or "").strip():
        if base := _base_of(handle):
            return base
        if not _is_handle(handle):
            raise BadRequest(f"Invalid YouTube handle: {handle}")
        return "@" + urllib.parse.quote(handle)
    url = (url or "").strip()
    if base := _base_of(url):
        return base
    raise BadRequest(f"Not a YouTube channel URL: {url}")


def playlist_id_of(value: str) -> str:
    """A playlist id, a URL with list= (an embed code's '…&amp;list=…' too) or a YouTube Music /browse/ URL: of a
    playlist (VL<id>) or of an album (MPREb_…, which the playlist route resolves to the playlist of its tracks)."""
    value = value.strip()
    if parsed := _yt_url(value):
        parts = [x for x in parsed.path.split("/") if x]
        if len(parts) == 2 and parts[0] in ("browse", "channel") and parts[1].startswith(("VL", "MPREb_")):
            value = parts[1].removeprefix("VL")
        else:
            query = re.sub(r"&(?:amp;)+", "&", parsed.query)  # copied from an <iframe src="…">, HTML-escaped
            value = (urllib.parse.parse_qs(query).get("list") or [""])[0]
    if not _PLAYLIST_ID.fullmatch(value):
        raise BadRequest("playlist_id must be a YouTube playlist id or a URL with list=")
    return value


# Compatibility forms YouTube's hashtags keep, where NFKC would rewrite them ('CO₂' -> 'CO2', '½' -> '1⁄2').
_KEPT_FORMS = ("<super>", "<sub>", "<circle>", "<vertical>", "<fraction>")


def _decompose(c: str) -> str:
    """c decomposed as NFKC decomposes it, short of the characters YouTube's hashtags keep: superscript,
    subscript, circled, vertical and fraction forms, Roman numerals ('Ⅳ', only lower-cased), Thai SARA AM
    ('ทำ', which NFKC splits into 'ทํา') and the Hangul compatibility jamo keyboards type ('ㅋㅋ', which NFKC
    makes conjoining jamo, 'ᄏᄏ'; half-width ones become them). Other compatibility characters go as NFKC has
    them, the Lao AM ('ຳ') and Armenian 'և' among them."""
    parts = unicodedata.decomposition(c).split()
    if (not parts or parts[0] in _KEPT_FORMS or c == "\u0e33" or "\u2160" <= c <= "\u217f"
            or "\u3131" <= c <= "\u318e"):
        return c
    return "".join(_decompose(chr(int(x, 16))) for x in parts if not x.startswith("<"))


def hashtag_of(value: str) -> tuple[str, bool]:
    """'#Funny' / '…/hashtag/funny' / '…/hashtag/funny/shorts' -> ('funny', whether it named the Shorts tab). The
    '#' may be the full-width '＃' of Japanese and Chinese keyboards ('＃ゲーム' is 'ゲーム' on YouTube's pages too).

    The tag is YouTube's canonical form, the one its own /hashtag/<tag> page puts in the feed params (any other
    spelling is another feed, usually empty): NFKC ('ﬁ' -> 'fi', decomposed 'ü' composed) short of the
    characters YouTube keeps (see _decompose), lower case per character ('İ' -> 'i', not 'i' + U+0307), final
    sigma folded ('κόσμος' -> 'κόσμοσ'), ZWNJ/ZWJ dropped. 'ß' and the dotless 'ı' stay."""
    value, shorts = value.strip(), False
    if parsed := _yt_url(value):
        m = re.fullmatch(r"/hashtag/([^/]+)(/shorts)?/?", parsed.path)
        if not m:
            raise BadRequest(f"Not a YouTube hashtag URL: {value}")
        value, shorts = urllib.parse.unquote(m.group(1)), bool(m.group(2))
    tag = unicodedata.normalize("NFC", "".join(map(_decompose, value))).lstrip("#")  # '＃' is '#' by now
    canonical = re.sub("[\u200c\u200d]", "", "".join("i" if c == "İ" else c.lower() for c in tag).replace("ς", "σ"))
    if not canonical or not _word(tag):
        raise BadRequest(f"Invalid hashtag: {value}")
    return canonical, shorts


def _region(value: str | None) -> str | None:
    """ISO 3166 alpha-2 country code (as yt-dlp's ISO3166Utils lists them) -> upper case; 400 otherwise."""
    if not value:
        return None
    code = value.strip().upper()
    # ASCII only: upper() also makes 'ß' 'SS' (South Sudan), 'ın' 'IN' and 'ﬁ' 'FI'; AP and EU are regions
    if not value.strip().isascii() or code in ("AP", "EU") or not ISO3166Utils.short2full(code):
        raise BadRequest(f"region must be a 2-letter ISO 3166 country code, got {value!r}")
    return code


# --- cursors -----------------------------------------------------------------

_FIELDS = {"t": str, "c": list, "g": str, "y": str, "v": str, "s": str, "p": str, "m": dict, "n": int, "x": str}


def _bad_cursor(message: str = "Invalid cursor/continuation token") -> BadRequest:
    return BadRequest(message, code="bad_cursor")


def _rejected() -> BadRequest:
    return _bad_cursor("continuationToken was rejected by YouTube (invalid or expired)")


def _count_ok(n: Any) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and n >= 0


def _meta_ok(meta: dict[str, Any]) -> bool:
    """Playlist meta carried by a cursor ('m') still has playlist_meta's shape: later pages echo it."""
    owner, total = meta.get("owner"), meta.get("totalVideos")
    return (set(meta) <= {"title", "owner", "totalVideos"} and isinstance(meta.get("title"), (str, type(None)))
            and (total is None or _count_ok(total))
            and (owner is None or (isinstance(owner, dict) and set(owner) <= {"id", "name", "url", "handle"}
                                   and all(isinstance(v, (str, type(None))) for v in owner.values()))))


def _state(token: str | None, kind: str) -> dict[str, Any] | None:
    """Decode one of our cursors and type-check its fields; anything off is a 400, not an upstream call."""
    state = decode_cursor(token)
    if state is None:
        return None
    if state.get("k") != kind:
        raise _bad_cursor("continuationToken belongs to a different endpoint")
    ctx, n, meta = state.get("c"), state.get("n"), state.get("m")
    if (not state.get("t") or any(state.get(k) is not None and not isinstance(state[k], t) for k, t in _FIELDS.items())
            or (ctx is not None and (len(ctx) != 3 or not all(x is None or isinstance(x, str) for x in ctx)))
            or (n is not None and not _count_ok(n)) or (meta is not None and not _meta_ok(meta))
            or state.get("x") not in (None, "mweb") or state.get("y") not in (None, *p.SEARCH_TYPES)):
        raise _bad_cursor()
    try:
        _region(state.get("g"))
    except BadRequest as exc:
        raise _bad_cursor() from exc
    return state


def _cursor(kind: str, token: str | None, **extra: Any) -> str | None:
    return encode_cursor({"k": kind, "t": token, **{k: v for k, v in extra.items() if v is not None}}) \
        if token else None


class _Seen:
    """Ids served on earlier pages, kept in the cursor as 4-byte hashes: YouTube's search and Shorts feeds
    repeat items across (and within) pages, now and then a whole page of them (see _read_on)."""

    keep = 200

    def __init__(self, state: dict[str, Any] | None):
        try:
            blob = base64.b64decode((state or {}).get("s") or "", altchars=b"-_", validate=True)
        except (binascii.Error, TypeError, ValueError) as exc:
            raise _bad_cursor() from exc
        self.order = [blob[i:i + 4] for i in range(0, len(blob) - len(blob) % 4, 4)]
        self.keys = set(self.order)
        self.repeats = 0

    def fresh(self, item: dict[str, Any]) -> bool:
        key = hashlib.blake2b(str(item.get("id")).encode(), digest_size=4).digest()
        if key in self.keys:
            self.repeats += 1
            return False
        self.keys.add(key)
        self.order.append(key)
        return True

    def dump(self) -> str:
        return base64.urlsafe_b64encode(b"".join(self.order[-self.keep:])).decode()


# --- fetching ----------------------------------------------------------------

async def _run(fn: Callable[[Any], T]) -> T:
    """Run fn(YoutubeTabIE) in the yt-dlp worker pool."""

    def go(ydl: Any) -> T:
        ie = ydl.get_info_extractor("YoutubeTab")
        ie.initialize()
        try:
            return fn(ie)
        except ExtractorError as exc:
            if "does the playlist exist" in str(exc):
                raise NotFound("Playlist or channel not found") from exc
            if "playlist type is unviewable" in str(exc):
                raise BadRequest("YouTube does not list this playlist type (mixes / radio playlists such as "
                                 "RD<video id> only exist inside a watch page)", code="unviewable_playlist") from exc
            raise

    return await with_ydl("youtube", go)


def _page(ie: Any, url: str, item_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Webpage -> (ytInitialData, ytcfg) like YoutubeTabIE._extract_data, but a 404 is final (no retries)."""
    webpage, urlh = ie._download_webpage_handle(url, item_id, expected_status=404)
    if urlh.status == 404:
        raise NotFound("Channel or playlist not found")
    data = ie.extract_yt_initial_data(item_id, webpage, fatal=False) or {}
    ie._extract_and_report_alerts(data)
    if traverse_obj(p.selected_tab(data), "tabIdentifier") == "FEwhat_to_watch":
        raise NotFound("Channel or playlist not found (YouTube redirected to its home page)")
    if not traverse_obj(data, "contents", "onResponseReceivedActions"):
        return ie._extract_data(url, item_id)  # yt-dlp's retrying path for incomplete pages
    return data, ie.extract_ytcfg(item_id, webpage) or {}


def _api(ie: Any, query: dict[str, Any], ep: str = "browse", *, client: str = "web", region: str | None = None,
         visitor: str | None = None, ytcfg: dict[str, Any] | None = None, cursor: bool = False) -> dict[str, Any]:
    """One InnerTube call on one of yt-dlp's clients. `visitor` is the visitorData feed tokens are bound to.

    `cursor`: the token came from the API client, so it gets a single attempt; YouTube answers a forged or
    stale one with HTTP 400, which _extract_response would retry before failing as an upstream error. A
    corrupted one can also get an HTTP 200 that reloads the start of the feed instead of a page (see
    p.reloads): channel tabs would read it as an empty last page, search as page 1 again."""
    ytcfg = copy.deepcopy(ytcfg) if ytcfg else ie._get_default_ytcfg(client)
    if region:
        ytcfg["INNERTUBE_CONTEXT"]["client"]["gl"] = region
    headers = ie.generate_api_headers(ytcfg=ytcfg, visitor_data=visitor, default_client=client)
    item_id = query.get("browseId") or "continuation"
    resp = None
    if cursor:
        try:
            resp = ie._call_api(ep, query, item_id, headers=headers, context=ie._extract_context(ytcfg, client),
                                default_client=client) or {}
        except ExtractorError as exc:
            status = exc.cause.status if isinstance(exc.cause, HTTPError) else None
            if status == 400:
                raise _rejected() from exc
            if status in (403, 429):
                raise
        else:
            ie._extract_and_report_alerts(resp, only_once=True)
    if resp is None:
        resp = ie._extract_response(item_id=item_id, query=query, ep=ep, headers=headers, ytcfg=ytcfg,
                                    default_client=client) or {}
    if cursor and p.reloads(resp):
        raise _rejected()
    return resp


def _visitor(resp: dict[str, Any]) -> str | None:
    return traverse_obj(resp, ("responseContext", "visitorData", {str}))


def _continue(ie: Any, token: str, ep: str = "browse", *, again: bool = False, **kw: Any) -> dict[str, Any]:
    """_api on a continuation token (kw are _api's). With `again`, a page with no items and no token is asked for
    again after each of _EMPTY_PAGE_WAITS in turn, until one has either: for a second or so after serving a page,
    a hashtag's Shorts tab answers a request for the next one with such a page, and a script paging through asks
    for it straight away."""
    resp = _api(ie, {"continuation": token}, ep, **kw)
    for wait in _EMPTY_PAGE_WAITS if again else ():
        if any(p.walk(p.continuation_items(resp))):
            break
        time.sleep(wait)
        resp = _api(ie, {"continuation": token}, ep, **kw)
    return resp


def _read_on(ie: Any, resp: dict[str, Any], payload: Any, seen: _Seen,
             serve: Callable[[list[tuple[str, dict]]], dict[str, list]], ep: str = "browse",
             **kw: Any) -> tuple[dict[str, list], list[tuple[str, dict]], list[str], dict[str, Any]]:
    """serve(items) of a continuation page -> (served, items, tokens, last response).

    A page that only repeats items `seen` already served (YouTube's Shorts and search feeds now and then re-send
    a whole batch) is replaced by the page after it, up to _READ_ON_PAGES times; past that the feed is taken to
    have come round again and ends. Only such pages are read past: one that serves nothing without repeating
    anything (it held only items `serve` doesn't map) is returned with its tokens. kw are _continue's again and
    _api's client / region / visitor."""
    items, tokens = p.walk(payload)
    for fetched in range(_READ_ON_PAGES + 1):
        repeats = seen.repeats
        out = serve(items)
        if any(out.values()) or seen.repeats == repeats or not (items and tokens):
            break
        if fetched == _READ_ON_PAGES:
            tokens = []  # page after page of repeats: the feed has come round again
            break
        kw["visitor"] = _visitor(resp) or kw.get("visitor")
        resp = _continue(ie, tokens[0], ep, **kw)
        items, tokens = p.walk(p.continuation_items(resp))
    return out, items, tokens, resp


def _tab_continuation(ie: Any, token: str, client: str, channel_id: str | None) -> dict[str, Any]:
    """_api on a channel tab's token from the API client. YouTube answers some corrupted channel-posts tokens with
    an HTTP 500 on every attempt, as it would any request while failing. One more request, for the channel
    itself, tells the two apart: if it loads, the token is rejected; if not, its error is YouTube's."""
    try:
        return _api(ie, {"continuation": token}, client=client, cursor=True)
    except ExtractorError as exc:
        if not (channel_id and isinstance(exc.cause, HTTPError) and exc.cause.status == 500):
            raise
        _api(ie, {"browseId": channel_id})
        raise _rejected() from exc


async def _channel_tab(kind: str, tabs: tuple[str, ...], channel_id: str | None, handle: str | None,
                       token: str | None, chip: str | None = None,
                       client: str = "web") -> tuple[list[tuple[str, dict]], str | None, list]:
    """(items, next cursor, [channel id, title, handle]) of one page of a channel tab.

    client='mweb' re-requests the tab (or its sort chip) on the mobile-web client after the webpage resolved
    the channel, and keeps paging there."""
    if state := _state(token, kind):
        ctx, client = state.get("c") or [None] * 3, state.get("x") or "web"
        items, tokens = await _run(lambda ie: p.walk(p.continuation_items(_tab_continuation(ie, state["t"], client,
                                                                                          ctx[0]))))
    else:
        base = channel_base(channel_id, handle)

        def first(ie: Any) -> tuple[list, list, list, str]:
            data, ytcfg = _page(ie, f"{YT}/{base}/{tabs[0]}", base)
            ctx = p.channel_ref(data)
            content = next((c for t in tabs if (c := p.tab_content(data, t)) is not None), None)
            if content is None:  # channel has no such tab; YouTube served another one
                return [], [], ctx, "web"
            chip_token = p.sort_chip_token(content, chip) if chip else None
            if client == "mweb" and (query := {"continuation": chip_token} if chip_token else p.tab_browse(data)):
                resp = _api(ie, query, client="mweb")
                items, tokens = p.walk(p.continuation_items(resp) if chip_token else resp.get("contents"))
                if items:
                    return items, tokens, ctx, "mweb"
            if chip_token:
                return *p.walk(p.continuation_items(_api(ie, {"continuation": chip_token}, ytcfg=ytcfg))), ctx, "web"
            return *p.walk(content), ctx, "web"

        items, tokens, ctx, client = await _run(first)
    more = tokens[0] if items and tokens else None
    return items, _cursor(kind, more, c=ctx, x=client if client != "web" else None), ctx


def _channel_link(ctx: list) -> str | None:
    return f"{YT}/@{ctx[2]}" if ctx[2] else (f"{YT}/channel/{ctx[0]}" if ctx[0] else None)


# --- channel -----------------------------------------------------------------

@sc_get(router, "/v1/youtube/channel", source=f"{_SRC} (channel /about page, ytInitialData)",
        notes="channelId, handle and url each accept a UC… id, an @handle or a channel URL (/@x, /channel/UC…, "
              "/c/x, /user/x, legacy /x). subscriberCount is parsed from YouTube's abbreviated text "
              "('521M subscribers') so it is approximate. email is always null (YouTube only reveals it after a "
              "signed-in captcha). store is the first link titled/hosted like a store/shop/merch; links lists "
              "every external link except twitter/instagram.")
async def youtube_channel(channelId: str | None = None, handle: str | None = None, url: str | None = None):
    require_one(channelId=channelId, handle=handle, url=url)
    base = channel_base(channelId, handle, url)
    data = await _run(lambda ie: _page(ie, f"{YT}/{base}/about", base)[0])
    out = p.channel_about(data)
    if not out["channelId"]:
        raise NotFound("Channel not found")
    return out


@sc_get(router, "/v1/youtube/channel-videos",
        source=f"{_SRC} (channel Videos tab webpage, then InnerTube browse on yt-dlp's mweb client)",
        notes="The Videos webpage resolves the channel and its 'Popular' chip (an entry of the sort drop-down on a "
              "tab with members-only videos); the listing itself (first page and continuations) comes from yt-dlp's "
              "mweb InnerTube client, whose items carry exact view counts "
              "('251,675,820 views'). sort=popular falls back to latest when the channel shows no chips. "
              "description is not part of the tab listing and is omitted; channel.thumbnail is null like "
              "ScrapeCreators. includeExtras is ignored (needs one extra request per video); "
              "is_paid_promotions=true returns 501. publishedTime is derived from relative text ('3 weeks ago') "
              "and is approximate.")
async def youtube_channel_videos(channelId: str | None = None, handle: str | None = None,
                                 sort: Literal["latest", "popular"] | None = None,
                                 continuationToken: str | None = None, is_paid_promotions: str | None = None,
                                 includeExtras: str | None = None):
    if truthy(is_paid_promotions):
        raise NotSupported(_NO_PAID_PROMOTIONS)
    require_one(channelId=channelId, handle=handle, continuationToken=continuationToken)
    items, cursor, ctx = await _channel_tab("channel-videos", ("videos",), channelId, handle, continuationToken,
                                            "Popular" if sort == "popular" else None, client="mweb")
    channel = {"title": ctx[1], "thumbnail": None}
    videos = [p.sc_video(v, "video", channel) for k, n in items
              if (v := p.video_item(k, n) or (p.lockup_video(n) if k == "lockupViewModel" else None))]
    return {"videos": videos, "continuationToken": cursor}


@sc_get(router, "/v1/youtube/channel/shorts", source=f"{_SRC} (channel Shorts tab + browse continuations)",
        notes="The Shorts tab grid only carries id, title, thumbnail and view count; description, like/comment "
              "counts, genre, duration and publishDate need a per-video request and are omitted. sort=popular "
              "follows the tab's 'Popular' chip. viewCountInt comes from abbreviated text ('25M views').")
async def youtube_channel_shorts(handle: str | None = None, channelId: str | None = None,
                                 sort: Literal["newest", "popular"] | None = None,
                                 continuationToken: str | None = None):
    require_one(channelId=channelId, handle=handle, continuationToken=continuationToken)
    items, cursor, _ = await _channel_tab("channel-shorts", ("shorts",), channelId, handle, continuationToken,
                                          "Popular" if sort == "popular" else None)
    shorts = [s for k, n in items if k in _SHORT_KEYS and (s := p.shorts_lockup(n))]
    return {"videos": [], "channels": [], "playlists": [], "shorts": shorts, "shelves": [], "lives": [],
            "continuationToken": cursor}


@sc_get(router, "/v1/youtube/channel/lives", source=f"{_SRC} (channel Live tab + browse continuations)",
        notes="Includes live-now ('N watching', viewCountInt null), upcoming ('N waiting') and past streams. "
              "View counts are the tab's abbreviated text ('1.2M views'), as in ScrapeCreators' example. "
              "channel.thumbnail is null like ScrapeCreators. " + _APPROX)
async def youtube_channel_lives(channelId: str | None = None, handle: str | None = None,
                                continuationToken: str | None = None):
    require_one(channelId=channelId, handle=handle, continuationToken=continuationToken)
    items, cursor, ctx = await _channel_tab("channel-lives", ("streams",), channelId, handle, continuationToken)
    channel = {"id": ctx[0], "title": ctx[1], "handle": ctx[2], "thumbnail": None}
    lives = [p.sc_video(v, "live", channel, published=False)
             for k, n in items if k == "lockupViewModel" and (v := p.lockup_video(n))]
    return {"lives": lives, "continuationToken": cursor}


@sc_get(router, "/v1/youtube/channel/playlists", source=f"{_SRC} (channel Playlists tab + browse continuations)",
        notes="Playlists, podcasts and shows (show lockups have no first video, so videoId is null). videoCount "
              "comes from the thumbnail badge ('12 videos', '5 episodes'); null for mixes without one.")
async def youtube_channel_playlists(channelId: str | None = None, handle: str | None = None,
                                    continuationToken: str | None = None):
    require_one(channelId=channelId, handle=handle, continuationToken=continuationToken)
    items, cursor, ctx = await _channel_tab("channel-playlists", ("playlists",), channelId, handle,
                                            continuationToken)
    playlists = []
    for key, node in items:
        if key == "lockupViewModel" and (pl := p.lockup_playlist(node)):
            owner_id = (pl["channel"] or {}).get("id")
            if not str(owner_id).startswith("UC") or owner_id == ctx[0]:
                pl["channel"] = {"id": ctx[0], "title": ctx[1], "handle": ctx[2], "channelUrl": _channel_link(ctx)}
            playlists.append(pl)
    return {"playlists": playlists, "continuationToken": cursor}


@sc_get(router, "/v1/youtube/channel/community-posts",
        source=f"{_SRC} (channel Posts tab + browse continuations)",
        notes="Text, image, multi-image, poll and video posts; poll options are not mapped. A shared post is "
              "listed as the share (the sharing channel, its text and publish time; no attachment, likeCount "
              "null when YouTube shows none) with the post it shares nested as originalPost, an extra field "
              "mapped like a listed post. likeCount comes from abbreviated text ('309K'). " + _APPROX)
async def youtube_channel_posts(channelId: str | None = None, handle: str | None = None,
                                continuationToken: str | None = None):
    require_one(channelId=channelId, handle=handle, continuationToken=continuationToken)
    items, cursor, _ = await _channel_tab("channel-posts", ("posts", "community"), channelId, handle,
                                          continuationToken)
    posts = [x for k, n in items if k == "backstagePostThreadRenderer" and (x := p.post(n))]
    return {"posts": posts, "continuationToken": cursor}


# --- playlist ----------------------------------------------------------------

def _playlist_video(key: str, node: dict[str, Any], uploader: dict[str, Any] | None) -> dict[str, Any] | None:
    """One playlist entry; `uploader` fills in the channel of items that don't name it (Shorts lockups)."""
    if key in _SHORT_KEYS:
        v = p.short_lockup(node)
    elif key == "lockupViewModel":
        v = p.lockup_video(node)
    else:
        v = p.playlist_video_renderer(node) if key == "playlistVideoRenderer" else None
    if not v:
        return None
    owner = v.get("owner") or uploader
    return {"id": v["id"], "title": v["title"], "thumbnail": v["thumbnail"], "url": p.watch_url(v["id"]),
            "lengthText": v["lengthText"], "lengthSeconds": v["lengthSeconds"],
            "channel": {"title": owner.get("title"), "url": owner.get("url")} if owner else None}


def _album_playlist(ie: Any, album_id: str) -> str:
    """A YouTube Music album -> the OLAK5uy_… playlist of its tracks, which its YouTube Music page names as the
    canonical URL (as yt-dlp resolves albums)."""
    resp = _api(ie, {"browseId": album_id}, client="web_music")
    url = traverse_obj(resp, ("microformat", "microformatDataRenderer", "urlCanonical"), expected_type=str)
    pl = (urllib.parse.parse_qs(urllib.parse.urlparse(url or "").query).get("list") or [""])[0]
    if not _PLAYLIST_ID.fullmatch(pl):
        raise NotFound("YouTube Music album not found")
    return pl


@sc_get(router, "/v1/youtube/playlist", source=f"{_SRC} (/playlist page + browse continuations)",
        notes="About 100 videos per page. Unavailable/private entries YouTube hides are skipped, so a page can "
              "hold fewer videos than totalVideos implies. Shorts playlists (UUSH…) are one page of up to about "
              "100 Shorts with no continuation (YouTube offers none), and their items have no length. Mixes "
              "(RD<video id>, RDAMVM…: radio playlists YouTube only builds inside a watch page) return 400 "
              "unviewable_playlist; YouTube Music's curated RDCLAK5uy_… playlists list like any other. A YouTube "
              "Music album (music.youtube.com/browse/MPREb_…) lists its tracks: one more request resolves it to "
              "the OLAK5uy_… playlist that holds them.")
async def youtube_playlist(playlist_id: str = Query(...), continuationToken: str | None = None):
    pid = playlist_id_of(playlist_id)
    if state := _state(continuationToken, "playlist"):
        if state.get("p") != pid:
            raise _bad_cursor("continuationToken belongs to a different playlist")
        meta = state.get("m") or {}
        query = {"continuation": state["t"]}
        items, tokens = await _run(lambda ie: p.walk(p.continuation_items(_api(ie, query, cursor=True))))
    else:
        def first(ie: Any) -> tuple[dict, list, list]:
            pl = _album_playlist(ie, pid) if _ALBUM_ID.fullmatch(pid) else pid
            data, _ = _page(ie, f"{YT}/playlist?list={pl}", pl)
            return p.playlist_meta(data), *p.walk(p.tab_content(data))

        meta, items, tokens = await _run(first)
    owner = meta.get("owner") if isinstance(meta.get("owner"), dict) else None
    # Uploads playlists (UU…, UUSH… Shorts) only hold the owner's own videos.
    uploader = {"title": owner.get("name"), "url": owner.get("url")} if owner and pid.startswith("UU") else None
    videos = [v for k, n in items if (v := _playlist_video(k, n, uploader))]
    served = ((state or {}).get("n") or 0) + len(videos)
    more = tokens[0] if items and tokens else None
    if isinstance(meta.get("totalVideos"), int) and served >= meta["totalVideos"]:
        more = None  # YouTube still hands out a token that leads to an empty page
    return {"title": meta.get("title"), "owner": meta.get("owner"), "totalVideos": meta.get("totalVideos"),
            "videos": videos, "continuationToken": _cursor("playlist", more, p=pid, m=meta, n=served)}


# --- search ------------------------------------------------------------------

def _search_payload(items: list[tuple[str, dict]], kind: str | None, seen: _Seen) -> dict[str, list]:
    """Bucket search results; with type=shorts, Shorts shelves are flattened into shorts."""
    out: dict[str, list] = {"videos": [], "channels": [], "playlists": [], "shorts": [], "shelves": [], "lives": []}
    for key, node in items:
        if kind == "shorts" and key in _SHELF_KEYS:
            inner, _ = p.walk({k: v for k, v in node.items() if k not in ("header", "title")})
            out["shorts"].extend(p.sc_video(v, "short", p.search_owner(v["owner"])) for k, n in inner
                                 if (v := p.short_lockup(n) if k in _SHORT_KEYS else p.video_item(k, n)))
        elif v := p.video_item(key, node):
            bucket = "shorts" if v["short"] else "lives" if v["live"] else "videos"
            out[bucket].append(p.sc_video(v, bucket[:-1] if bucket != "lives" else "live", p.search_owner(v["owner"])))
        elif key == "channelRenderer" and (c := p.channel_renderer(node)):
            out["channels"].append(c)
        elif key == "lockupViewModel":
            if pl := p.lockup_playlist(node):
                out["playlists"].append(pl)
            elif v := p.lockup_video(node):
                out["videos"].append(p.sc_video(v, "video", p.search_owner(v["owner"])))
        elif key in _SHORT_KEYS and (v := p.short_lockup(node)):
            out["shorts"].append(p.sc_video(v, "short", p.search_owner(v["owner"])))
        elif key in _SHELF_KEYS and (sh := p.shelf(key, node)):
            out["shelves"].append(sh)
    for name, values in out.items():
        if name != "shelves":
            out[name] = [x for x in values if seen.fresh(x)]
    for sh in out["shelves"]:
        sh["items"] = [x for x in sh["items"] if seen.fresh(x)]
    out["shelves"] = [sh for sh in out["shelves"] if sh["items"]]
    return out


def _no_region(region: str) -> BadRequest:
    return BadRequest(f"YouTube does not serve search results for region {region}", code="unsupported_region")


def _first_search(ie: Any, query: dict[str, Any], client: str, region: str | None) -> dict[str, Any]:
    """The web client answers a region YouTube does not serve (CN, KP…) with an error page, mweb with HTTP 400."""
    try:
        return _api(ie, query, "search", client=client, region=region)
    except ExtractorError as exc:
        if region and isinstance(exc.cause, HTTPError) and exc.cause.status == 400:
            raise _no_region(region) from exc
        raise


def _hashtag_videos(items: list[tuple[str, dict]], seen: _Seen) -> dict[str, list]:
    videos = []
    for key, node in items:
        if v := p.video_item(key, node) or (p.lockup_video(node) if key == "lockupViewModel" else None):
            owner = v["owner"] or {}
            channel = {"id": owner.get("id"), "title": owner.get("title"), "thumbnail": owner.get("thumbnail")}
            videos.append(p.sc_video(v, "short" if v["short"] else "video", channel, description=True))
        elif key in _SHORT_KEYS and (s := p.shorts_lockup(node)):
            videos.append(s)
    return {"videos": [v for v in videos if seen.fresh(v)]}


@sc_get(router, "/v1/youtube/search",
        source=f"{_SRC} (InnerTube search API, the call YoutubeSearchURLIE/_search_results makes)",
        notes="Filters are encoded into YouTube's own 'sp' search params. region (an ISO 3166 country code) sets "
              "the InnerTube gl (content region) only; requests still leave from this server's IP. A region "
              "YouTube does not serve (e.g. CN, KP) is a 400 unsupported_region. type=shorts "
              "runs on yt-dlp's mweb client: standalone Shorts carry channel, exact views, length and age, while "
              "Shorts from YouTube's shelves (flattened into shorts) have abbreviated views and no length. "
              f"Results among the last {_Seen.keep} served are skipped; a page YouTube fills with nothing but those "
              "is replaced by the next one, and four such pages in a row end the results. A page can still be "
              "empty before the end (e.g. it held only items this endpoint doesn't map): follow continuationToken "
              "until it is null. "
              "includeExtras is ignored (needs one extra request per video); is_paid_promotions=true returns 501, "
              "with a continuationToken too. " + _APPROX)
async def youtube_search(query: str = Query(...),
                         uploadDate: Literal["today", "this_week", "this_month", "this_year"] | None = None,
                         sortBy: Literal["relevance", "popular"] | None = None,
                         kind: Literal["videos", "shorts", "channels", "playlists"] | None = Query(None, alias="type"),
                         duration: Literal["under_3_min", "between_3_and_20_min", "over_20_min"] | None = None,
                         region: str | None = None, continuationToken: str | None = None,
                         includeExtras: str | None = None, is_paid_promotions: str | None = None):
    if truthy(is_paid_promotions):  # ScrapeCreators documents it for search too: refuse it, don't drop it
        raise NotSupported(_NO_PAID_PROMOTIONS)
    if not query.strip():
        raise BadRequest("query must not be empty")
    region = _region(region)
    state = _state(continuationToken, "search")
    seen = _Seen(state)
    if state:
        region, kind, visitor, client = state.get("g"), state.get("y"), state.get("v"), state.get("x") or "web"
        q: dict[str, Any] = {"continuation": state["t"]}

        def page(ie: Any) -> tuple[dict[str, list], list, list, dict[str, Any]]:
            resp = _api(ie, q, "search", client=client, region=region, visitor=visitor, cursor=True)
            return _read_on(ie, resp, p.continuation_items(resp), seen, lambda x: _search_payload(x, kind, seen),
                            "search", client=client, region=region, visitor=visitor)

        out, items, tokens, resp = await _run(page)
    else:
        visitor, client = None, "mweb" if kind == "shorts" else "web"
        q = {"query": query.strip()}
        if sp := p.search_params(uploadDate, sortBy, kind, duration):
            q["params"] = sp
        resp = await _run(lambda ie: _first_search(ie, q, client, region))
        payload = resp.get("contents") if client == "mweb" else \
            traverse_obj(resp, ("contents", "twoColumnSearchResultsRenderer", "primaryContents"))
        items, tokens = p.walk(payload)
        if not items and p.error_page(payload):
            if region:
                raise _no_region(region)
            raise UpstreamError("YouTube answered the search with its 'Something went wrong' page")
        out = _search_payload(items, kind, seen)
    more = tokens[0] if items and tokens else None
    return {**out, "continuationToken": _cursor("search", more, g=region, y=kind, v=_visitor(resp) or visitor,
                                                x=client if client != "web" else None, s=seen.dump())}


@sc_get(router, "/v1/youtube/search/hashtag", source=f"{_SRC} (/hashtag/<tag> browse tabs + continuations)",
        notes="The tag is matched in YouTube's canonical form (NFKC short of the superscripts, Roman numerals, "
              "Thai SARA AM and Hangul compatibility jamo YouTube keeps, lower case, final sigma as σ, ZWNJ/ZWJ "
              "dropped), as on its own /hashtag/ pages; a leading '#' (or the full-width '＃') is optional. type=all "
              "starts with the hashtag page's 'All' tab (exact "
              "view counts; YouTube's hashtag items carry no description snippet, so description is null). "
              "When the web client offers no further page (the usual case for anonymous visitors), later pages "
              "come from the same FEhashtag feed on yt-dlp's mweb client, whose items have abbreviated view "
              "counts. type=shorts (or a …/hashtag/<tag>/shorts URL) pages through the hashtag's Shorts tab; "
              "those items carry only id/title/thumbnail/views. YouTube sends a Shorts page empty for a second or so "
              "after the page before it, so an empty one is asked for again, twice at most, a second apart. "
              f"Items among the last {_Seen.keep} served are "
              "skipped; a page of nothing but those (the Shorts tab re-sends whole batches) is replaced by the next "
              "one, and four such pages in a row end the feed. A page can still be empty before the end (e.g. it "
              "held only items this endpoint doesn't map, such as playlists): follow continuationToken until it is "
              "null. " + _APPROX)
async def youtube_search_hashtag(hashtag: str = Query(...), continuationToken: str | None = None,
                                 kind: Literal["all", "shorts"] | None = Query(None, alias="type")):
    tag, shorts_url = hashtag_of(hashtag)
    shorts = kind == "shorts" or (kind is None and shorts_url)
    state = _state(continuationToken, "hashtag")
    seen = _Seen(state)
    if state:
        if state.get("p") != tag:
            raise _bad_cursor("continuationToken belongs to a different hashtag")
        client, visitor = state.get("x") or "web", state.get("v")  # feed tokens only work for their visitor
        again = client == "web"  # the Shorts tab is paged on web (see _continue)

        def page(ie: Any) -> tuple[dict[str, list], list, list, dict[str, Any]]:
            if client == "mweb" and state["t"] == _MWEB_FEED:
                resp = _api(ie, {"browseId": "FEhashtag", "params": p.hashtag_params(tag, False)}, client="mweb")
                payload = resp.get("contents")
            else:
                resp = _continue(ie, state["t"], client=client, visitor=visitor, cursor=True, again=again)
                payload = p.continuation_items(resp)
            return _read_on(ie, resp, payload, seen, lambda x: _hashtag_videos(x, seen), client=client,
                            visitor=visitor, again=again)

        out, items, tokens, resp = await _run(page)
    else:
        client, visitor = "web", None
        query = {"browseId": "FEhashtag", "params": p.hashtag_params(tag, shorts)}
        resp = await _run(lambda ie: _api(ie, query))
        items, tokens = p.walk(resp.get("contents"))
        out = _hashtag_videos(items, seen)
    more = tokens[0] if items and tokens else None
    if more is None and items and not state and not shorts:
        more, client = _MWEB_FEED, "mweb"
    return {**out, "continuationToken": _cursor("hashtag", more, p=tag, v=_visitor(resp) or visitor,
                                                x=client if client != "web" else None, s=seen.dump())}
