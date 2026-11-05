"""What a stored request was about, for the dashboard's request history.

The link a request sent (`url`), or the profile, channel, hashtag, search or id its parameters name, with the public
address to open where one can be built for certain. A value no address can be built for is still shown as sent.
Nothing here makes a request.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .errors import ApiError

# A profile's public address per platform; ScrapeCreators' own examples spell Rumble, SoundCloud and Kwai's this way.
_PROFILES = {
    "instagram": "https://www.instagram.com/{}/",
    "tiktok": "https://www.tiktok.com/@{}",
    "twitter": "https://x.com/{}",
    "threads": "https://www.threads.com/@{}",
    "bluesky": "https://bsky.app/profile/{}",
    "truthsocial": "https://truthsocial.com/@{}",
    "github": "https://github.com/{}",
    "twitch": "https://www.twitch.tv/{}",
    "pinterest": "https://www.pinterest.com/{}/",
    "soundcloud": "https://soundcloud.com/{}",
    "snapchat": "https://www.snapchat.com/add/{}",
    "telegram": "https://t.me/{}",
    "kwai": "https://www.kwai.com/@{}",
    "rumble": "https://rumble.com/c/{}",
}
_HASHTAGS = {
    "instagram": "https://www.instagram.com/explore/tags/{}/",
    "tiktok": "https://www.tiktok.com/tag/{}",
    "youtube": "https://www.youtube.com/hashtag/{}",
}
# Routes whose `id` names one public page (Apple Music's need a storefront and a slug the id doesn't carry).
_IDS = {
    "/v1/instagram/user/highlight/detail": ("Highlight", "https://www.instagram.com/stories/highlights/{}/"),
    "/v1/facebook/marketplace/item": ("Listing", "https://www.facebook.com/marketplace/item/{}/"),
    "/v1/facebook/event/details": ("Event", "https://www.facebook.com/events/{}/"),
    "/v1/facebook/adLibrary/ad": ("Ad", "https://www.facebook.com/ads/library/?id={}"),
    "/v1/facebook/adLibrary/ad/transcript": ("Ad", "https://www.facebook.com/ads/library/?id={}"),
    "/v1/spotify/artist": ("Artist", "https://open.spotify.com/artist/{}"),
    "/v1/spotify/track": ("Track", "https://open.spotify.com/track/{}"),
    "/v1/spotify/album": ("Album", "https://open.spotify.com/album/{}"),
    "/v1/spotify/playlist": ("Playlist", "https://open.spotify.com/playlist/{}"),
    "/v1/spotify/podcast": ("Podcast", "https://open.spotify.com/show/{}"),
    "/v1/spotify/podcast/episodes": ("Podcast", "https://open.spotify.com/show/{}"),
    "/v1/apple-music/artist": ("Artist", None),
    "/v1/apple-music/album": ("Album", None),
    "/v1/apple-music/track": ("Track", None),
}
# Other ids, in the order a request is described by them: (param, label, address or None).
_OTHER_IDS = (
    ("playlist_id", "Playlist", "https://www.youtube.com/playlist?list={}"),
    ("audio_id", "Audio", "https://www.instagram.com/reels/audio/{}/"),
    ("ad_id", "Ad", "https://library.tiktok.com/ads/detail/?ad_id={}"),
    ("advertiser_id", "Advertiser", "https://adstransparency.google.com/advertiser/{}"),
    ("group_id", "Group", "https://www.facebook.com/groups/{}/"),
    ("clipId", "Sound", None),
    ("room_id", "Live room", None),
    ("user_id", "User ID", None),
    ("userId", "User ID", None),
    ("product_id", "Product", None),
    ("domain", "Domain", None),
    ("companyId", "Company", None),
    ("company", "Company", None),
    ("companyName", "Advertiser", None),
    ("advertiser_name", "Advertiser", None),
)
_LINK = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)  # the dashboard's own test for a link it may open
_HANDLE = re.compile(r"[\w.\-]{1,100}")
_ID = re.compile(r"[\w\-]{1,100}")


def _link(value: str) -> str | None:
    """`value` as an address to open: http(s), with a host, nothing a link could hide."""
    if not _LINK.fullmatch(value):
        return None
    try:
        return value if urllib.parse.urlsplit(value).hostname else None
    except ValueError:  # 'https://[::1': urllib won't read a broken IPv6 host
        return None


def _fill(template: str | None, value: str, pattern: re.Pattern[str] = _ID) -> str | None:
    return template.format(urllib.parse.quote(value, safe="")) if template and pattern.fullmatch(value) else None


def _target(label: str, value: str, url: str | None) -> dict[str, Any]:
    return {"label": label, "value": value, "url": url}


def _youtube_channel(params: dict[str, str]) -> dict[str, Any] | None:
    """channelId or handle, read as the channel routes read them (a UC… id, an @handle or a channel URL)."""
    channel_id, handle = params.get("channelId", "").strip(), params.get("handle", "").strip()
    if not (channel_id or handle):
        return None
    from .platforms.youtube_listings import channel_base
    try:
        base = channel_base(channel_id or None, None if channel_id else handle)
    except (ApiError, ValueError):
        base = None
    shown = channel_id or (handle if _link(handle) else "@" + handle.lstrip("@＠﹫"))
    return _target("Channel", shown, f"https://www.youtube.com/{base}" if base else None)


def _profile(platform: str, handle: str) -> dict[str, Any]:
    if link := _link(handle):  # some routes take the profile's address in handle too
        return _target("Profile", handle, link)
    name = handle.lstrip("@＠﹫").strip("/")
    return _target("Profile", f"@{name}" if name else handle, _fill(_PROFILES.get(platform), name, _HANDLE))


def describe(path: str, query: str | None) -> dict[str, Any] | None:
    """{label, value, url} for the request's subject, or None for a request without one (credit balance, ...).

    `value` is what was sent, decoded ("https://www.instagram.com/p/…", "@handle", "#tag", a search's words).
    `url` is where it lives on the platform, or None when that can't be told from the request alone. Any stored
    request can be described: whatever a client sent, this never raises, since one row must not fail the history."""
    try:
        return _describe(path, query)
    except (ApiError, ValueError):
        return None


def _describe(path: str, query: str | None) -> dict[str, Any] | None:
    params = {key: value.strip() for key, value in urllib.parse.parse_qsl(query or "") if value.strip()}
    parts = path.split("/")
    platform = parts[2] if len(parts) > 2 else ""
    if url := params.get("url"):
        return _target("Link", url, _link(url))
    if platform == "youtube" and (channel := _youtube_channel(params)):
        return channel
    if handle := params.get("handle"):
        # Lookups across platforms (find-social-profiles, find-creator-emails) name theirs in `platform`.
        return _profile(params.get("platform", "").lower() if platform.startswith("find-") else platform, handle)
    if tag := params.get("hashtag"):
        tag = tag.lstrip("#")
        return _target("Hashtag", f"#{tag}", _fill(_HASHTAGS.get(platform), tag, _HANDLE))
    if subreddit := params.get("subreddit"):
        name = re.sub(r"^/?r/", "", subreddit, flags=re.IGNORECASE).strip("/")
        return _target("Subreddit", f"r/{name}", _fill("https://www.reddit.com/r/{}/", name, _HANDLE))
    if (ident := params.get("id")) and path in _IDS:
        label, template = _IDS[path]
        if label == "Highlight":  # also 'highlight:<id>', its page or the app's share link, as the route reads it
            from .platforms.instagram import clean_highlight_id
            try:
                return _target(label, ident, _fill(template, clean_highlight_id(ident)))
            except (ApiError, ValueError):
                return _target(label, ident, None)
        return _target(label, ident, _link(ident) or _fill(template, ident))
    if page := params.get("pageId"):
        if path.startswith("/v1/facebook/adLibrary/"):
            return _target("Advertiser", page, _fill("https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
                                                     "&country=ALL&search_type=page&view_all_page_id={}", page))
        return _target("Page", page, _fill("https://www.facebook.com/{}", page))
    for param, label, template in _OTHER_IDS:
        if value := params.get(param):
            return _target(label, value, _link(value) or _fill(template, value))
    if words := params.get("query") or params.get("keyword"):
        return _target("Search", f"“{words}”", None)
    return None
