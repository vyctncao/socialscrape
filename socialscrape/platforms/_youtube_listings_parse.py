"""Pure mappers from YouTube InnerTube JSON (as fetched through yt-dlp's
YoutubeTabIE) to ScrapeCreators-shaped listing items. No network access here."""

from __future__ import annotations

import base64
import calendar
import math
import re
import urllib.parse
from typing import Any

from yt_dlp.extractor.youtube import YoutubeTabIE
from yt_dlp.utils import parse_duration, parse_iso8601, traverse_obj, unified_timestamp

from ..util import clock, iso_utc, parse_count
from ._youtube_videos_parse import exact_count

YT = "https://www.youtube.com"

# Renderers we map; the walker collects them and does not descend further.
ITEM_KEYS = frozenset({
    "lockupViewModel", "videoRenderer", "videoWithContextRenderer", "shortsLockupViewModel", "reelItemRenderer",
    "channelRenderer", "playlistVideoRenderer", "backstagePostThreadRenderer", "gridShelfViewModel",
    "shelfRenderer", "reelShelfRenderer",
})
CONTINUATION_KEYS = ("continuationItemRenderer", "continuationItemViewModel")
# Where browse/search continuation responses put their append / reload actions.
CONTINUATION_ACTIONS = ("onResponseReceivedActions", "onResponseReceivedEndpoints", "onResponseReceivedCommands")
# Ads, sort/filter chips and headers carry tokens/items that are not results.
SKIP_KEYS = frozenset({"adSlotRenderer", "searchPyvRenderer", "promotedSparklesWebRenderer", "chipBarViewModel",
                       "feedFilterChipBarRenderer", "header"})
_DURATION = re.compile(r"^\d+(?::\d{2}){1,2}$")
# A years-long stream's hours are digit-grouped: '20,772:51:34'.
_GROUPED_HOURS = re.compile(r"^\d{1,3}(?:[,.\u00a0\u202f' ]\d{3})+(?=:\d{2}:\d{2}$)")
PLAYLIST_TYPES = ("LOCKUP_CONTENT_TYPE_PLAYLIST", "LOCKUP_CONTENT_TYPE_PODCAST", "LOCKUP_CONTENT_TYPE_SHOW")


# --- generic helpers ---------------------------------------------------------

def text(node: Any) -> str | None:
    """{content} / simpleText / runs / str -> str."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict) and isinstance(node.get("content"), str):
        return node["content"]
    return YoutubeTabIE._get_text(node) if node else None


def count(value: str | None) -> int | None:
    """'251M' / '2,905,080 views' / 'No views' -> int (abbreviated inputs are approximate)."""
    if not value or not isinstance(value, str):
        return None
    if value.strip().lower().startswith("no "):
        return 0
    return parse_count(value)


def view_count(value: str | None) -> int | None:
    """Only real view counts ('1.2M views', '25M'), not '111 watching' / '5 waiting'."""
    if value and re.search(r"\bviews?\b|^[\d.,]+\s*[KMB]?$", value.strip(), re.I):
        return count(value)
    return None


def approx_time(value: str | None) -> str | None:
    """'3 weeks ago' / 'Streamed 2 days ago' / 'Feb 20, 2024' -> ISO (relative ones are approximate)."""
    if not value or re.match(r"(scheduled|premieres)", value, re.I):
        return None
    try:
        when = YoutubeTabIE.extract_relative_time(value.lower())
        return iso_utc(calendar.timegm(when.timetuple()) if when else unified_timestamp(value))
    except (ValueError, OverflowError):  # a time past what a date holds ('999…9 days ago', '1 Jan 10000 00:00')
        return None


def best_url(sources: Any) -> str | None:
    """Largest image of a thumbnails/sources list (a size that isn't a number counts as 0)."""
    good = [s for s in sources if isinstance(s, dict) and isinstance(s.get("url"), str) and s["url"]] \
        if isinstance(sources, list) else []
    if not good:
        return None
    url = max(good, key=lambda s: _size(s.get("width")) * _size(s.get("height")))["url"]
    return "https:" + url if url.startswith("//") else url


def _size(value: Any) -> int | float:
    """An image's width or height, 0 when it isn't a number."""
    return value if isinstance(value, (int, float)) else 0


def handle_of(path: str | None) -> str | None:
    """'/@MrBeast' or 'http://www.youtube.com/@MrBeast' -> 'MrBeast'."""
    m = re.search(r"/@([^/?#]+)", path) if isinstance(path, str) else None
    return urllib.parse.unquote(m.group(1)) if m else None


def channel_url(path: str | None) -> str | None:
    return urllib.parse.urljoin(YT, path) if path and isinstance(path, str) else None


def watch_url(video_id: str) -> str:
    return f"{YT}/watch?v={video_id}"


def length(value: Any) -> tuple[str | None, int | None]:
    """'57:59' -> ('57:59', 3479), '20,772:51:34' -> ('20,772:51:34', 74782294); anything else -> (None, None)."""
    if not isinstance(value, str):
        return None, None
    shown = value.strip()
    plain = _GROUPED_HOURS.sub(lambda m: re.sub(r"\D", "", m.group()), shown)
    if _DURATION.match(plain) and math.isfinite(seconds := parse_duration(plain)):  # not more hours than a float holds
        return shown, int(seconds)
    return None, None


def continuation_token(key: str, node: dict[str, Any]) -> str | None:
    return traverse_obj(YoutubeTabIE._extract_continuation({"contents": [{key: node}]}), ("continuation", {str}))


def walk(node: Any) -> tuple[list[tuple[str, dict]], list[str]]:
    """Collect (renderer_key, renderer) pairs in document order, and continuation tokens innermost first.

    Like yt-dlp's _extract_entries, the token next to the items wins over a section-level one (playlist
    pages carry both; the outer one leads to an empty page)."""
    items: list[tuple[str, dict]] = []
    tokens: list[tuple[int, str]] = []

    def go(node: Any, depth: int) -> None:
        if isinstance(node, list):
            for x in node:
                go(x, depth + 1)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in SKIP_KEYS:
                    continue
                if key in ITEM_KEYS and isinstance(value, dict):
                    items.append((key, value))
                elif key in CONTINUATION_KEYS and isinstance(value, dict):
                    if tok := continuation_token(key, value):
                        tokens.append((depth, tok))
                elif isinstance(value, (dict, list)):
                    go(value, depth + 1)

    go(node, 0)
    return items, [tok for _, tok in sorted(tokens, key=lambda x: -x[0])]


def continuation_items(response: dict[str, Any] | None) -> list[Any]:
    """Payload of a browse/search continuation response (append or reload actions)."""
    return [*traverse_obj(response, (
        CONTINUATION_ACTIONS, ..., ("appendContinuationItemsAction", "reloadContinuationItemsCommand"),
        "continuationItems")),
        traverse_obj(response, "continuationContents") or {}]


def reloads(response: dict[str, Any]) -> bool:
    """Whether a continuation response reloads the feed's start instead of being a page, as YouTube answers
    some corrupted tokens (the search's first page, or a channel's home shelves in a sectionListContinuation)."""
    return "continuationContents" in response or any(
        "reloadContinuationItemsCommand" in action
        for action in traverse_obj(response, (CONTINUATION_ACTIONS, ..., {dict})))


def browse_ref(run: Any) -> dict[str, str | None]:
    """A text run / commandRun / renderer with a browseEndpoint -> {id, handle, url}."""
    ep = traverse_obj(run, (("navigationEndpoint", ("onTap", "innertubeCommand")), "browseEndpoint", {dict}, any)) or {}
    browse_id = traverse_obj(ep, ("browseId", {str}))
    path = traverse_obj(ep, ("canonicalBaseUrl", {str})) or (f"/channel/{browse_id}" if browse_id else None)
    return {"id": browse_id, "handle": handle_of(path), "url": channel_url(path)}


def error_page(payload: Any) -> bool:
    """A results page that is YouTube's 'Something went wrong' / 'Try again' promo (what search answers for a
    region it does not serve, e.g. CN or KP) rather than results; a real empty search shows the EMPTY_SEARCH
    'No results found' promo instead."""
    promos = traverse_obj(payload, ("sectionListRenderer", "contents", ..., "itemSectionRenderer", "contents", ...,
                                    "backgroundPromoRenderer", {dict}))
    return bool(promos) and not any(traverse_obj(x, ("icon", "iconType")) == "EMPTY_SEARCH" for x in promos)


# --- channel / playlist pages ------------------------------------------------

def selected_tab(data: dict[str, Any]) -> dict[str, Any] | None:
    tabs = traverse_obj(data, ("contents", "twoColumnBrowseResultsRenderer", "tabs", ...,
                               ("tabRenderer", "expandableTabRenderer"), {dict}))
    return next((t for t in tabs if t.get("selected")), None)


def tab_content(data: dict[str, Any], tab: str | None = None) -> Any:
    """Content of the selected tab; None when YouTube fell back to another tab (e.g. no Shorts tab)."""
    sel = selected_tab(data) or {}
    path = traverse_obj(sel, ("endpoint", "commandMetadata", "webCommandMetadata", "url", {str})) or ""
    if tab and not path.rstrip("/").endswith("/" + tab):
        return None
    return sel.get("content")


def tab_browse(data: dict[str, Any]) -> dict[str, str] | None:
    """{browseId, params} that reloads the selected tab (the page carries params URL-encoded)."""
    ep = traverse_obj(selected_tab(data), ("endpoint", "browseEndpoint", {dict})) or {}
    if not ep.get("browseId"):
        return None
    return {"browseId": ep["browseId"], **({"params": urllib.parse.unquote(ep["params"])} if ep.get("params") else {})}


def channel_ref(data: dict[str, Any]) -> list[str | None]:
    """[id, title, handle] of the channel whose page this is."""
    meta = traverse_obj(data, ("metadata", "channelMetadataRenderer", {dict})) or {}
    return [meta.get("externalId"), meta.get("title"), handle_of(meta.get("vanityChannelUrl"))]


_SORT_SHEET = ("tapCommand", "innertubeCommand", "showSheetCommand", "panelLoadingStrategy", "inlineContent",
               "sheetViewModel", "content", "listViewModel", "listItems", ..., "listItemViewModel", {dict})


def sort_chip_token(content: Any, label: str) -> str | None:
    """Continuation token behind a channel tab's 'Latest' / 'Popular' / 'Oldest' chip. A videos tab that also has
    'Members only' / 'Public' chips (LinusTechTips') puts the three in the drop-down of one chip instead."""
    for chip in traverse_obj(content, (..., "header", "chipBarViewModel", "chips", ..., "chipViewModel", {dict})):
        if (traverse_obj(chip, ("text", {str})) or "").lower() == label.lower() and (token := traverse_obj(chip, (
                "tapCommand", "innertubeCommand", "continuationCommand", "token", {str}))):
            return token
        for item in traverse_obj(chip, _SORT_SHEET):
            if (traverse_obj(item, ("title", "content", {str})) or "").lower() == label.lower():
                return traverse_obj(item, ("rendererContext", "commandContext", "onTap", "innertubeCommand",
                                           "commandExecutorCommand", "commands", ..., "continuationCommand", "token",
                                           {str}, any))
    return None


def external_link(vm: dict[str, Any]) -> tuple[str, str] | None:
    """channelExternalLinkViewModel -> (title, target URL), unwrapping youtube.com/redirect."""
    title = text(vm.get("title")) or ""
    target = traverse_obj(vm, ("link", "commandRuns", ..., "onTap", "innertubeCommand", "urlEndpoint", "url",
                               {str}, any))
    if target:
        parsed = urllib.parse.urlparse(target)
        if q := urllib.parse.parse_qs(parsed.query).get("q"):
            return title, _absolute(q[0])  # the redirect target is often scheme-less ('www.tiktok.com/@x')
        if not parsed.netloc.endswith("youtube.com"):
            return title, target
    shown = text(vm.get("link"))
    return (title, _absolute(shown)) if shown else None


def _absolute(url: str) -> str:
    url = url.strip()
    return url if re.match(r"^[a-z][a-z\d+.-]*://", url, re.I) else "https://" + url.lstrip("/")


# 'store.x.com', 'shopmrbeast.com', 'lttstore.com', 'naturestore.com', 'x-merch.com'; not 'photoshop.com',
# 'workshop.…', 'bishop…', 'merchant…' or a word 'restore' (a host label or '-' starts it).
_STORE_HOST = re.compile(r"(?<!photo)(?<!work)(?<!bi)shop|(?:(?<!re)|(?<=[a-z]re))store|merch(?!ant)")
_STORE_TITLE = re.compile(r"\b(store|shop|merch(?!ant))", re.I)


def channel_verified(data: dict[str, Any]) -> bool | None:
    """Verification from the channel header, using yt-dlp's badge signals.

    A complete header without a badge is an unverified channel. A response
    without a header is unknown, rather than evidence of a removed badge.
    Restrict the search to the header so a recommended channel cannot mark
    the requested channel as verified.
    """
    header = data.get("header")
    if not isinstance(header, dict) or not header:
        return None
    styles = traverse_obj(header, (..., "badges", ..., "metadataBadgeRenderer", "style", {str}))
    images = traverse_obj(header, (
        "pageHeaderRenderer", "content", "pageHeaderViewModel", "title", "dynamicTextViewModel", "text",
        "attachmentRuns", ..., "element", "type", "imageType", "image", "sources", ..., "clientResource",
        "imageName", {str}))
    return (any(style in ("BADGE_STYLE_TYPE_VERIFIED", "BADGE_STYLE_TYPE_VERIFIED_ARTIST") for style in styles)
            or any(image in ("CHECK_CIRCLE_FILLED", "AUDIO_BADGE") for image in images))


def subscriber_widget_count(html: str, channel_id: str) -> tuple[int, str] | None:
    """A first-party Subscribe button's visible count, including an explicit 0.

    A widget without a count (or one for another channel) is unknown. In
    particular, do not make the absence of a subscriber label mean zero.
    """
    if not re.search(r'data-channel-external-id=[\'"]' + re.escape(channel_id) + r'[\'"]', html):
        return None
    match = re.search(r'<span\b[^>]*class=[\'"][^\'"]*\byt-subscription-button-subscriber-count-'
                      r'[^\'"]*[\'"][^>]*>([^<]*)</span>', html)
    if not match:
        return None
    shown = match.group(1).strip()
    value = count(shown)
    return (value, shown) if value is not None else None


def channel_about(data: dict[str, Any]) -> dict[str, Any]:
    """/@handle/about page -> /v1/youtube/channel payload."""
    about = traverse_obj(data, (
        "onResponseReceivedEndpoints", ..., "showEngagementPanelEndpoint", "engagementPanel",
        "engagementPanelSectionListRenderer", "content", "sectionListRenderer", "contents", ...,
        "itemSectionRenderer", "contents", ..., "aboutChannelRenderer", "metadata", "aboutChannelViewModel",
        {dict}, any)) or {}
    meta = traverse_obj(data, ("metadata", "channelMetadataRenderer", {dict})) or {}
    avatar = traverse_obj(data, ("header", "pageHeaderRenderer", "content", "pageHeaderViewModel", "image",
                                 "decoratedAvatarViewModel", "avatar", "avatarViewModel", {dict}))
    tags = traverse_obj(data, ("microformat", "microformatDataRenderer", "tags", ..., {str}))
    links = [x for vm in traverse_obj(about, ("links", ..., "channelExternalLinkViewModel", {dict}))
             if (x := external_link(vm))]
    twitter = instagram = store = None
    other = []
    for title, url in links:
        host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
        if host in ("twitter.com", "x.com") and not twitter:
            twitter = url
        elif host == "instagram.com" and not instagram:
            instagram = url
        else:
            other.append(url)
            if not store and (_STORE_TITLE.search(title) or _STORE_HOST.search(host)):
                store = url
    subs = about.get("subscriberCountText")
    description = about.get("description")
    videos = count(about.get("videoCountText"))
    if videos is None and traverse_obj(data, (
            "contents", "twoColumnBrowseResultsRenderer", "tabs", ..., "tabRenderer", "content",
            "sectionListRenderer", "contents", ..., "channelOwnerEmptyStateRenderer", {dict})):
        # YouTube explicitly says this channel has no public content. This is
        # different from a truncated response with no video-count field.
        videos = 0
    return {
        "channelId": about.get("channelId") or meta.get("externalId"),
        "channel": about.get("canonicalChannelUrl") or meta.get("vanityChannelUrl") or meta.get("channelUrl"),
        "name": meta.get("title"),
        "avatar": avatar,
        "description": description if isinstance(description, str) else meta.get("description"),
        "subscriberCount": count(subs),
        "subscriberCountText": subs,
        "isVerified": channel_verified(data),
        "videoCount": videos,
        "videoCountText": about.get("videoCountText"),
        "viewCountText": about.get("viewCountText"),
        "joinedDateText": text(about.get("joinedDateText")),
        "tags": ", ".join(tags) if tags else None,
        "email": None,
        "store": store,
        "twitter": twitter,
        "instagram": instagram,
        "links": other,
        "country": about.get("country"),
    }


def playlist_meta(data: dict[str, Any]) -> dict[str, Any]:
    """Title, owner and video count of a /playlist?list= page."""
    by = traverse_obj(data, ("header", "pageHeaderRenderer", "content", "pageHeaderViewModel", "metadata",
                             "contentMetadataViewModel", "metadataRows", ..., "metadataParts", ...,
                             "avatarStack", "avatarStackViewModel", "text", {dict}, any)) or {}
    ref = browse_ref(traverse_obj(by, ("commandRuns", 0)))
    sidebar = traverse_obj(data, ("sidebar", "playlistSidebarRenderer", "items", ..., {dict}))
    owner = traverse_obj(sidebar, (..., "playlistSidebarSecondaryInfoRenderer", "videoOwner", "videoOwnerRenderer",
                                   {dict}, any)) or {}
    if not ref["id"] and owner:
        ref = browse_ref(traverse_obj(owner, ("title", "runs", 0)) or owner)
    name = text(owner.get("title")) or (re.sub(r"^by\s+", "", by["content"]) if by.get("content") else None)
    stats = traverse_obj(sidebar, (..., "playlistSidebarPrimaryInfoRenderer", "stats", ..., {text}))
    stats += traverse_obj(data, ("header", "pageHeaderRenderer", "content", "pageHeaderViewModel", "metadata",
                                 "contentMetadataViewModel", "metadataRows", ..., "metadataParts", ..., "text",
                                 "content", {str}))
    total = next((count(s) for s in stats if re.search(r"^[\d,.]+\s*(videos?|episodes?|lessons?)$", s.strip())), None)
    return {
        "title": traverse_obj(data, ("metadata", "playlistMetadataRenderer", "title", {str})),
        "owner": {"id": ref["id"], "name": name, "url": ref["url"], "handle": ref["handle"]}
                 if ref["id"] or name else None,
        "totalVideos": total,
    }


# --- items -------------------------------------------------------------------

def _thumb_badges(vm: dict[str, Any]) -> list[dict[str, Any]]:
    return traverse_obj(vm, ("contentImage", "thumbnailViewModel", "overlays", ..., (
        ("thumbnailBottomOverlayViewModel", "badges"), ("thumbnailOverlayBadgeViewModel", "thumbnailBadges")),
        ..., "thumbnailBadgeViewModel", {dict}))


def _avatar_owner(md: dict[str, Any], title: str | None) -> dict[str, Any] | None:
    """Channel of an mweb lockup, which links it from the avatar instead of the name, or the first channel of
    a collaboration lockup, whose avatar stack opens a dialog listing them all (the names carry no link)."""
    avatar = traverse_obj(md, ("image", "decoratedAvatarViewModel", {dict})) or {}
    if not avatar:
        return _collab_owner(md)
    ref = browse_ref(traverse_obj(avatar, ("rendererContext", "commandContext")))
    if not (ref["id"] or "").startswith("UC"):
        return None
    sources = traverse_obj(avatar, ("avatar", "avatarViewModel", "image", "sources"))
    return {"title": title, **ref, "thumbnail": best_url(sources)}


def _collab_owner(md: dict[str, Any]) -> dict[str, Any] | None:
    item = traverse_obj(md, (
        "image", "avatarStackViewModel", "rendererContext", "commandContext", "onTap", "innertubeCommand",
        "showDialogCommand", "panelLoadingStrategy", "inlineContent", "dialogViewModel", "customContent",
        "listViewModel", "listItems", ..., "listItemViewModel", {dict}, any)) or {}
    ref = browse_ref(traverse_obj(item, ("rendererContext", "commandContext")))
    if not (ref["id"] or "").startswith("UC"):
        return None
    # The endpoint has no canonicalBaseUrl; the subtitle shows the handle:
    # '\u200e\u2068@99percentinvisiblepodcast\u2069 • \u206823.1K subscribers\u2069'
    if not ref["handle"] and (shown := re.search(r"@([^\s\u2068\u2069•]+)", text(item.get("subtitle")) or "")):
        ref = {**ref, "handle": shown.group(1), "url": channel_url("/@" + urllib.parse.quote(shown.group(1)))}
    sources = traverse_obj(item, ("leadingAccessory", "avatarViewModel", "image", "sources"))
    return {"title": text(item.get("title")), **ref, "thumbnail": best_url(sources)}


def lockup_video(vm: dict[str, Any]) -> dict[str, Any] | None:
    """lockupViewModel of a video (channel tabs, playlists, mweb hashtag feed) -> normalized dict."""
    if vm.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO") or not vm.get("contentId"):
        return None
    md = traverse_obj(vm, ("metadata", "lockupMetadataViewModel", {dict})) or {}
    parts = traverse_obj(md, ("metadata", "contentMetadataViewModel", "metadataRows", ..., "metadataParts", ...,
                              {dict}))
    owner = views = when = first = None
    for part in parts:
        shown = text(part.get("text"))
        if not shown:
            continue
        run = traverse_obj(part, ("text", "commandRuns", 0, {dict}))
        if owner is None and traverse_obj(run, ("onTap", "innertubeCommand", "browseEndpoint")):
            owner = {"title": shown, **browse_ref(run), "thumbnail": None}
            continue
        # Pages differ in whether they carry a11y labels ('251 million views'); always serve the visible text.
        label = f"{shown} {part.get('accessibilityLabel') or ''}"
        if views is None and re.search(r"\b(views?|watching|waiting)\b", label, re.I):
            views = (shown, label)
        elif when is None and re.search(r"\bago\b|^(streamed|scheduled|premiere)", label, re.I):
            when = shown
        elif first is None:
            first = shown
    badges = _thumb_badges(vm)
    length_text, seconds = next((length(b.get("text")) for b in badges if length(b.get("text"))[0]), (None, None))
    labels = [b["text"] for b in badges if isinstance(b.get("text"), str) and not length(b["text"])[0]]
    labels += traverse_obj(md, ("metadata", "contentMetadataViewModel", "metadataRows", ..., "badges", ...,
                                "badgeViewModel", "badgeText", {str}))
    return {
        "id": vm["contentId"],
        "title": text(md.get("title")),
        "thumbnail": best_url(traverse_obj(vm, ("contentImage", "thumbnailViewModel", "image", "sources"))),
        "owner": owner or _avatar_owner(md, first),
        "viewCountText": views[0] if views else None,
        "viewCountInt": view_count(views[0]) if views and re.search(r"\bviews?\b", views[1], re.I) else None,
        "publishedTimeText": when,
        "publishedTime": approx_time(when),
        "lengthText": length_text,
        "lengthSeconds": seconds,
        "badges": labels,
        "live": any(b.get("badgeStyle") == "THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE" for b in badges),
        "short": bool(traverse_obj(vm, ("rendererContext", "commandContext", "onTap", "innertubeCommand",
                                        "reelWatchEndpoint"))),
    }


def _is_channel(run: Any) -> bool:
    ep = traverse_obj(run, ("onTap", "innertubeCommand", {dict})) or {}
    page = traverse_obj(ep, ("commandMetadata", "webCommandMetadata", "webPageType"))
    return page == "WEB_PAGE_TYPE_CHANNEL" or str(traverse_obj(ep, ("browseEndpoint", "browseId"))).startswith("UC")


def lockup_playlist(vm: dict[str, Any]) -> dict[str, Any] | None:
    """lockupViewModel of a playlist / mix / podcast / show -> SC playlist item (channel filled when the lockup
    names it)."""
    if vm.get("contentType") not in PLAYLIST_TYPES or not vm.get("contentId"):
        return None
    primary = traverse_obj(vm, ("contentImage", "collectionThumbnailViewModel", "primaryThumbnail",
                                "thumbnailViewModel", {dict})) or {}
    badge = traverse_obj(primary, ("overlays", ..., "thumbnailOverlayBadgeViewModel", "thumbnailBadges", ...,
                                   "thumbnailBadgeViewModel", "text", {str}, any))
    first = traverse_obj(vm, ("rendererContext", "commandContext", "onTap", "innertubeCommand", "watchEndpoint",
                              "videoId", {str}))
    md = traverse_obj(vm, ("metadata", "lockupMetadataViewModel", {dict})) or {}
    channel = None
    for part in traverse_obj(md, ("metadata", "contentMetadataViewModel", "metadataRows", ..., "metadataParts", ...,
                                  "text", {dict})):
        run = traverse_obj(part, ("commandRuns", 0, {dict}))
        if _is_channel(run):  # not 'View full playlist' / 'View full podcast' (VL… playlist links)
            ref = browse_ref(run)
            title = part.get("content")
            channel = {"id": ref["id"], "title": None if title in ("Playlist", "Podcast", "Course") else title,
                       "handle": ref["handle"], "channelUrl": ref["url"]}
            break
    pid = vm["contentId"]
    return {
        "type": "playlist",
        "id": pid,
        "title": text(md.get("title")),
        "thumbnail": best_url(traverse_obj(primary, ("image", "sources"))),
        "playlistUrl": f"{YT}/playlist?list={pid}",
        "videoId": first,
        "videoUrl": watch_url(first) if first else None,
        "videoCount": count(badge) if badge and re.search(r"\b(videos?|episodes?|lessons?)\b", badge) else None,
        "channel": channel,
    }


def short_lockup(vm: dict[str, Any]) -> dict[str, Any] | None:
    """shortsLockupViewModel / reelItemRenderer -> normalized dict; channel and age only on mweb lockups."""
    m = re.match(r"shorts-shelf-item-([\w-]{11})$", traverse_obj(vm, ("entityId", {str})) or "")
    vid = (traverse_obj(vm, ("onTap", "innertubeCommand", "reelWatchEndpoint", "videoId", {str}))
           or vm.get("videoId") or (m.group(1) if m else None))
    if not vid:
        return None
    views = text(traverse_obj(vm, ("overlayMetadata", "secondaryText"))) or text(vm.get("viewCountText"))
    # Slim lockups (hashtag pages) only carry the title inside the a11y text, as yt-dlp's own parser reads it.
    a11y = re.fullmatch(r"(.+), (?:[\d,.]+(?:[KMB]| million| billion| thousand)?|No) views? - play Short",
                        traverse_obj(vm, ("accessibilityText", {str})) or "", re.S)
    below = traverse_obj(vm, ("belowThumbnailMetadata", {dict})) or {}
    avatar = traverse_obj(below, ("avatar", "avatarViewModel", {dict})) or {}
    ref = browse_ref({"onTap": avatar.get("endpoint")})
    when = text(below.get("secondaryText"))
    return {
        "id": vid,
        "title": (text(traverse_obj(vm, ("overlayMetadata", "primaryText"))) or text(vm.get("headline"))
                  or (a11y.group(1) if a11y else None)),
        "thumbnail": best_url(traverse_obj(vm, ("thumbnailViewModel", "thumbnailViewModel", "image", "sources"))
                              or traverse_obj(vm, ("thumbnail", "sources"))
                              or traverse_obj(vm, ("thumbnail", "thumbnails"))),
        "owner": {"title": text(below.get("primaryText")), **ref,
                  "thumbnail": best_url(traverse_obj(avatar, ("image", "sources")))} if ref["id"] else None,
        "viewCountText": views,
        "viewCountInt": view_count(views),
        "publishedTimeText": when,
        "publishedTime": approx_time(when),
        "lengthText": None, "lengthSeconds": None, "badges": [], "live": False,
    }


def shorts_lockup(vm: dict[str, Any]) -> dict[str, Any] | None:
    """shortsLockupViewModel / reelItemRenderer -> minimal SC short (grids and shelves)."""
    v = short_lockup(vm)
    return sc_shelf_item(v, "short") if v else None


# The list of channels a collaboration's byline opens instead of linking one: a dialog on web, a sheet on mweb.
_COLLABORATORS = ("navigationEndpoint", (
    ("showDialogCommand", "panelLoadingStrategy", "inlineContent", "dialogViewModel", "customContent"),
    ("showSheetCommand", "panelLoadingStrategy", "inlineContent", "sheetViewModel", "content")),
    "listViewModel", "listItems", ..., "listItemViewModel", {dict})


def _byline_owner(run: dict[str, Any], avatar: dict[str, Any]) -> dict[str, Any]:
    """Channel of a videoRenderer's byline run (avatar: its channelThumbnailWithLinkRenderer). A collaboration's
    run names all its channels ('99% Invisible and BBC Podcasts') and links none: the avatar links the uploader,
    named as the run's list of channels names it."""
    ref, title, uploader = browse_ref(run), run.get("text"), browse_ref(avatar)
    if not ref["id"] and uploader["id"]:
        ref = uploader
        title = next((text(c.get("title")) for c in traverse_obj(run, _COLLABORATORS)
                      if browse_ref(traverse_obj(c, ("rendererContext", "commandContext")))["id"] == ref["id"]), title)
    return {"title": title, **ref, "thumbnail": best_url(traverse_obj(avatar, ("thumbnail", "thumbnails")))}


def video_renderer(vr: dict[str, Any]) -> dict[str, Any] | None:
    """videoRenderer (search, hashtag, post attachments) -> normalized dict."""
    vid = vr.get("videoId")
    if not vid:
        return None
    run = traverse_obj(vr, (("ownerText", "longBylineText", "shortBylineText"), "runs", 0, {dict}, any)) or {}
    views = text(vr.get("viewCountText"))
    overlays = traverse_obj(vr, ("thumbnailOverlays", ..., "thumbnailOverlayTimeStatusRenderer", {dict}))
    length_text, seconds = length(text(vr.get("lengthText")) or traverse_obj(overlays, (..., "text", {text}, any)))
    badge_nodes = traverse_obj(vr, ("badges", ..., "metadataBadgeRenderer", {dict}))
    nav = traverse_obj(vr, ("navigationEndpoint", "commandMetadata", "webCommandMetadata", "url", {str})) or ""
    when = text(vr.get("publishedTimeText"))
    avatar = traverse_obj(vr, ("channelThumbnailSupportedRenderers", "channelThumbnailWithLinkRenderer", {dict})) or {}
    return {
        "id": vid,
        "title": text(vr.get("title")),
        "description": text(traverse_obj(vr, ("detailedMetadataSnippets", 0, "snippetText")))
                       or text(vr.get("descriptionSnippet")),
        "thumbnail": best_url(traverse_obj(vr, ("thumbnail", "thumbnails"))),
        "owner": _byline_owner(run, avatar) if run else None,
        "viewCountText": views,
        "viewCountInt": view_count(views),
        "publishedTimeText": when,
        "publishedTime": approx_time(when),
        "lengthText": length_text,
        "lengthSeconds": seconds,
        "badges": [b["label"] for b in badge_nodes if isinstance(b.get("label"), str)],
        "live": any(b.get("style") == "BADGE_STYLE_TYPE_LIVE_NOW" for b in badge_nodes)
                or any(o.get("style") == "LIVE" for o in overlays),
        "short": nav.startswith("/shorts/") or any(o.get("style") == "SHORTS" for o in overlays),
    }


def video_with_context(vr: dict[str, Any]) -> dict[str, Any] | None:
    """mweb videoWithContextRenderer -> video_renderer's dict, with the exact view count its headline a11y label
    carries ('… by MrBeast 251,675,820 views 10 days ago …')."""
    label = traverse_obj(vr, ("headline", "accessibility", "accessibilityData", "label", {str})) or ""
    exact = re.findall(r"\b(?:\d[\d,]*|No) views?\b", label)
    return video_renderer({**vr, "title": vr.get("headline"),
                           "viewCountText": exact[-1] if exact else vr.get("shortViewCountText"),
                           "channelThumbnailSupportedRenderers": vr.get("channelThumbnail")})


def video_item(key: str, node: dict[str, Any]) -> dict[str, Any] | None:
    """videoRenderer (web) / videoWithContextRenderer (mweb) -> normalized dict."""
    if key == "videoRenderer":
        return video_renderer(node)
    return video_with_context(node) if key == "videoWithContextRenderer" else None


def channel_renderer(cr: dict[str, Any]) -> dict[str, Any] | None:
    """channelRenderer (search) -> SC channel item."""
    if not cr.get("channelId"):
        return None
    ref = browse_ref(cr)
    texts = [text(cr.get(k)) for k in ("subscriberCountText", "videoCountText")]
    subs = next((t for t in texts if t and "subscriber" in t), None)
    return {
        "type": "channel",
        "id": cr["channelId"],
        "title": text(cr.get("title")),
        "handle": ref["handle"],
        "url": ref["url"] or channel_url(f"/channel/{cr['channelId']}"),
        "thumbnail": best_url(traverse_obj(cr, ("thumbnail", "thumbnails"))),
        "subscriberCountText": subs,
        "subscriberCount": count(subs),
        "description": text(cr.get("descriptionSnippet")),
    }


def post(thread: dict[str, Any]) -> dict[str, Any] | None:
    """backstagePostThreadRenderer -> SC community post.

    A share (sharedPostRenderer) names the sharing channel in displayName/endpoint and carries its own text as
    content, with no attachment or like count; the post it shares is mapped the same way, as originalPost (the
    extra field /v1/youtube/community-post returns for a share)."""
    p = traverse_obj(thread, ("post", ("backstagePostRenderer", "sharedPostRenderer"), {dict}, any))
    if not p or not p.get("postId"):
        return None
    author = traverse_obj(p, (("authorText", "displayName"), "runs", 0, {dict}, any)) or {}
    endpoint = p.get("authorEndpoint") or p.get("endpoint")
    ref = browse_ref(author if author.get("navigationEndpoint") else {"navigationEndpoint": endpoint})
    att = traverse_obj(p, ("backstageAttachment", {dict})) or {}
    nodes = [att.get("backstageImageRenderer"),
             *traverse_obj(att, ("postMultiImageRenderer", "images", ..., "backstageImageRenderer"))]
    images = [u for n in nodes if (u := best_url(traverse_obj(n, ("image", "thumbnails"))))]
    when = text(p.get("publishedTimeText"))
    likes = text(p.get("voteCount"))
    video = video_renderer(att["videoRenderer"]) if isinstance(att.get("videoRenderer"), dict) else None
    out = {
        "id": p["postId"],
        "url": f"{YT}/post/{p['postId']}",
        "channel": {"id": ref["id"], "title": author.get("text") or text(p.get("displayName")), "url": ref["url"],
                    "handle": ref["handle"]},
        "content": text(p.get("contentText") or p.get("content")),
        "image": images[0] if images else None,
        "images": images,
        "likeCountText": likes,
        "likeCount": count(likes),
        "publishedTimeText": when,
        "publishedTime": approx_time(re.sub(r"\s*\(edited\)\s*$", "", when or "")),
        "video": {
            "id": video["id"], "title": video["title"], "thumbnail": video["thumbnail"], "url": watch_url(video["id"]),
            "viewCountText": video["viewCountText"], "viewCountInt": video["viewCountInt"],
            "publishedTimeText": video["publishedTimeText"], "lengthText": video["lengthText"],
            "lengthSeconds": video["lengthSeconds"],
        } if video else None,
    }
    if original := post({"post": p.get("originalPost")}):
        out["originalPost"] = original
    return out


def playlist_video_renderer(pv: dict[str, Any]) -> dict[str, Any] | None:
    """Legacy playlistVideoRenderer -> the lockup_video keys a playlist page needs."""
    if not pv.get("videoId"):
        return None
    run = traverse_obj(pv, ("shortBylineText", "runs", 0, {dict})) or {}
    length_text, seconds = length(text(pv.get("lengthText")))
    return {
        "id": pv["videoId"], "title": text(pv.get("title")),
        "thumbnail": best_url(traverse_obj(pv, ("thumbnail", "thumbnails"))),
        "owner": {"title": run.get("text"), **browse_ref(run)} if run else None,
        "lengthText": length_text, "lengthSeconds": seconds,
    }


# --- per-video extras (reel_item_watch) --------------------------------------

REEL_OK = "REEL_ITEM_WATCH_STATUS_SUCCEEDED"
REEL_GONE = "REEL_ITEM_WATCH_STATUS_REEL_ERROR"  # what it answers for an id that is no video YouTube shows
_DIGITS = re.compile(r"[0-9]{1,19}")
EXTRA_KEYS = ("description", "viewCountInt", "likeCountText", "likeCountInt", "commentCountText", "commentCountInt",
              "publishDate", "genre", "durationMs", "durationFormatted")


def _digits(value: Any) -> int | None:
    """'285970' -> 285970; None for anything but a string of ASCII digits (YouTube's counts are strings)."""
    return int(value) if isinstance(value, str) and _DIGITS.fullmatch(value) else None


def _first(*values: int | None) -> int | None:
    return next((v for v in values if v is not None), None)


def reel_buttons(resp: dict[str, Any]) -> list[dict[str, Any]]:
    """The buttonViewModels of a reel_item_watch answer's action bar (like, comments, share, remix...)."""
    return traverse_obj(resp, ("overlay", "reelPlayerOverlayRenderer", "playerOverlay", "reelPlayerOverlayViewModel",
                               "actionBar", "reelActionBarViewModel", "buttonViewModels", ..., "buttonViewModel",
                               {dict}))


def reel_extras(resp: dict[str, Any], vid: str) -> dict[str, Any] | None:
    """A reel_item_watch answer (what YouTube's Shorts player asks for each Short; it answers for any video id) ->
    what a listing's grid doesn't carry, under EXTRA_KEYS; None when it holds no details of video `vid`.

    The player response in it is cut short of the formats (no PO token: 'UNPLAYABLE') but keeps videoDetails
    (exact views, length, description) and the microformat: publishDate with its offset
    ('2026-10-23T11:00:08-07:00', the form ScrapeCreators returns), exact likeCount (absent when the likes are
    hidden) and category (SC's genre). The comment count is only in the comments button: its label is exact
    ('View 40,525 comments'), its title rounded from 10,000 on ('40K'); one without a number (comments turned off)
    is null."""
    pr = traverse_obj(resp, ("playerResponse", {dict})) or {}
    details = traverse_obj(pr, ("videoDetails", {dict})) or {}
    like = traverse_obj(resp, ("overlay", "reelPlayerOverlayRenderer", "playerOverlay", "reelPlayerOverlayViewModel",
                              "actionBar", "reelActionBarViewModel", "buttonViewModels", ..., "likeButtonViewModel",
                              {dict}, any)) or {}
    identity = details.get("videoId") == vid
    if not details.get("videoId") and traverse_obj(pr, ("playabilityStatus", "reason")) == "Please sign in":
        # The sign-in-gated player omits videoDetails, but its public action bar
        # can still show counts. Its entity key binds those counts to this id.
        raw_key = like.get("likeStatusEntityKey")
        key = urllib.parse.unquote(raw_key) if isinstance(raw_key, str) else ""
        try:
            entity = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        except (ValueError, TypeError):
            entity = b""
        identity = entity.startswith(b"\x12\x0b" + vid.encode()) and len(vid) == 11
    if resp.get("status") != REEL_OK or not identity:
        return None
    mf = traverse_obj(pr, ("microformat", "playerMicroformatRenderer", {dict})) or {}
    button = next((b for b in reel_buttons(resp)
                   if b.get("accessibilityId") == "id.reel_comment_button" or b.get("iconName") == "SHORTS_COMMENT"),
                  {})
    shown = button.get("title") if isinstance(button.get("title"), str) and re.search(r"\d", button["title"]) else None
    comments = exact_count(button.get("accessibilityText")) if shown and isinstance(
        button.get("accessibilityText"), str) else None
    # Zero-comment Shorts show title="0" but the label is just "View comments",
    # without the number used on positive counts. The visible zero is evidence;
    # a "Comments" title (disabled/unknown) still stays null.
    if comments is None and shown is not None and shown.strip() == "0":
        comments = 0
    like_button = traverse_obj(like, ("toggleButtonViewModel", "toggleButtonViewModel", "defaultButtonViewModel",
                                     "buttonViewModel", {dict})) or {}
    likes = _first(_digits(mf.get("likeCount")), exact_count(like_button.get("accessibilityText")),
                   _digits(like_button.get("title")))
    seconds = _first(_digits(details.get("lengthSeconds")), _digits(mf.get("lengthSeconds")))
    published = next((d for d in (mf.get("publishDate"), mf.get("uploadDate"))
                      if isinstance(d, str) and parse_iso8601(d) is not None), None)
    description = details.get("shortDescription")
    return {
        "description": description if isinstance(description, str) else None,
        "viewCountInt": _first(_digits(details.get("viewCount")), _digits(mf.get("viewCount"))),
        "likeCountText": str(likes) if likes is not None else None,
        "likeCountInt": likes,
        "commentCountText": shown,
        "commentCountInt": comments,
        "publishDate": published,
        "genre": mf.get("category") if isinstance(mf.get("category"), str) and mf["category"] else None,
        "durationMs": seconds * 1000 if seconds is not None else None,
        "durationFormatted": clock(seconds, always_hours=True),
    }


def reel_reason(resp: dict[str, Any]) -> str | None:
    """Why the player in a reel_item_watch answer shows no video ('Sign in to confirm you’re not a bot')."""
    return traverse_obj(resp, ("playerResponse", "playabilityStatus", "reason", {text}))


# --- SC item shapes ----------------------------------------------------------

def sc_video(v: dict[str, Any], kind: str, channel: dict[str, Any] | None, *,
             description: bool = False, published: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"type": kind, "id": v["id"], "url": watch_url(v["id"]), "title": v["title"]}
    if description:
        out["description"] = v.get("description")
    out.update({
        "thumbnail": v["thumbnail"], "channel": channel,
        "viewCountText": v["viewCountText"], "viewCountInt": v["viewCountInt"],
        "publishedTimeText": v["publishedTimeText"],
    })
    if published:
        out["publishedTime"] = v["publishedTime"]
    out.update({"lengthText": v["lengthText"], "lengthSeconds": v["lengthSeconds"], "badges": v["badges"]})
    return out


def sc_short(v: dict[str, Any], extras: dict[str, Any] | None) -> dict[str, Any]:
    """A channel Shorts grid item (sc_shelf_item's) with its extras (reel_extras) in ScrapeCreators' order. Without
    them every extra is null, never 0: the grid only has id, title, thumbnail and the abbreviated view count."""
    x = extras or {}
    views = x.get("viewCountInt")
    return {
        "type": "short", "id": v["id"], "url": v["url"], "title": v["title"], "description": x.get("description"),
        "thumbnail": v["thumbnail"], "viewCountText": v["viewCountText"],
        "viewCountInt": views if views is not None else v["viewCountInt"],
        **{k: x.get(k) for k in EXTRA_KEYS if k not in ("description", "viewCountInt")},
    }


def with_extras(v: dict[str, Any], extras: dict[str, Any] | None) -> dict[str, Any]:
    """An sc_video item with includeExtras' fields (reel_extras): description after the title as in SC's items,
    exact counts, publishDate, and publishedTime made exact from it. Unknown ones are null, never 0."""
    x = extras or {}
    out: dict[str, Any] = {}
    for key, value in v.items():
        out[key] = value
        if key == "title":
            out["description"] = x.get("description")
    if x.get("viewCountInt") is not None:
        out["viewCountInt"] = x["viewCountInt"]
    if x.get("publishDate") and "publishedTime" in out:
        out["publishedTime"] = iso_utc(parse_iso8601(x["publishDate"]))
    out.update({k: x.get(k) for k in ("likeCountText", "likeCountInt", "commentCountText", "commentCountInt",
                                      "publishDate")})
    return out


def sc_shelf_item(v: dict[str, Any], kind: str = "video") -> dict[str, Any]:
    return {"type": kind, "id": v["id"], "url": watch_url(v["id"]), "title": v["title"], "thumbnail": v["thumbnail"],
            "viewCountText": v["viewCountText"], "viewCountInt": v["viewCountInt"]}


def search_owner(owner: dict[str, Any] | None) -> dict[str, Any] | None:
    """SC search channel: the @handle, else the channel path ('channel/UC...') as SC returns it."""
    if not owner:
        return None
    path = urllib.parse.urlparse(owner.get("url") or "").path
    return {"id": owner.get("id"), "title": owner.get("title"),
            "handle": owner.get("handle") or (path.lstrip("/") or None), "thumbnail": owner.get("thumbnail")}


def shelf(key: str, node: dict[str, Any]) -> dict[str, Any] | None:
    """gridShelfViewModel / shelfRenderer / reelShelfRenderer -> SC shelf."""
    if key == "gridShelfViewModel":
        title = text(traverse_obj(node, ("header", "sectionHeaderViewModel", "headline")))
    else:
        title = text(node.get("title"))
    items, _ = walk({k: v for k, v in node.items() if k not in ("header", "title")})
    out = []
    for k, item in items:
        if k in ("shortsLockupViewModel", "reelItemRenderer"):
            if s := shorts_lockup(item):
                out.append(s)
        elif (v := video_item(k, item)) or (k == "lockupViewModel" and (v := lockup_video(item))):
            out.append(sc_shelf_item(v, "short" if v["short"] else "video"))
    return {"type": "shelf", "title": title, "items": out} if out else None


# --- request params ----------------------------------------------------------

UPLOAD_DATES = {"today": 2, "this_week": 3, "this_month": 4, "this_year": 5}
SEARCH_TYPES = {"videos": 1, "channels": 2, "playlists": 3, "shorts": 9}
# 1 / 3 are YouTube's legacy "under 4 minutes" / "4-20 minutes" filters; 4 / 5 are the current 3-minute cut.
DURATIONS = {"under_3_min": 4, "between_3_and_20_min": 5, "over_20_min": 2}


def search_params(upload: str | None, sort: str | None, kind: str | None, duration: str | None) -> str | None:
    """Search filter protobuf (the 'sp=' value YouTube's own filter menu builds)."""
    filters = b""
    if upload:
        filters += bytes([0x08, UPLOAD_DATES[upload]])
    if kind:
        filters += bytes([0x10, SEARCH_TYPES[kind]])
    if duration:
        filters += bytes([0x18, DURATIONS[duration]])
    raw = (b"\x08\x03" if sort == "popular" else b"") + (b"\x12" + bytes([len(filters)]) + filters if filters else b"")
    return base64.b64encode(raw).decode() if raw else None


def hashtag_params(tag: str, shorts: bool) -> str:
    """FEhashtag browse params of /hashtag/<tag> (tab 1) or /hashtag/<tag>/shorts (tab 3).

    URL-safe base64 like YouTube's own: it answers '+' / '/' (which many non-Latin tags' bytes produce) with
    HTTP 400."""
    t = tag.encode()
    inner = b"\x0a" + _varint(len(t)) + t + b"\x10\x00\x18" + bytes([3 if shorts else 1])
    return base64.urlsafe_b64encode(b"\xea\x05" + _varint(len(inner)) + inner).decode()


def _varint(n: int) -> bytes:
    out = b""
    while True:
        byte, n = n & 0x7F, n >> 7
        out += bytes([byte | (0x80 if n else 0)])
        if not n:
            return out
