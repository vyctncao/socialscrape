"""YouTube listing endpoints (channel tabs, playlists, search, hashtags):
offline mapper/validation tests + live tests against YouTube (-m live)."""

from __future__ import annotations

import base64
import copy
import io
import json
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

import pytest
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError

from socialscrape.cursor import encode_cursor
from socialscrape.errors import BadRequest
from socialscrape.platforms import _youtube_listings_parse as P
from socialscrape.platforms import youtube_listings as M
from socialscrape.ytdlp import translate_error
from tests.contract import assert_conforms

# Raw responses cached by the live probe runs (too large to commit); replay tests skip without them.
PROBE = Path(os.environ.get("SOCIALSCRAPE_PROBE_DIR", "/tmp/sc")) / "probe-youtube_listings"
MRBEAST = "UCX6OQ3DkcsbYNE6H8uQQuVA"
NASA = "UCLA_DiR1FfKNvjuUpBHmylQ"
TF2 = "PLBB231211A4F62143"  # "Team Fortress 2 [2010 Version]" by Wickman Wish, 29 videos
UPLOADS = "UU" + MRBEAST[2:]  # MrBeast uploads, > 100 videos
SCIENCECASTS = "PL8A2171FA17D43A35"  # NASA, 220 entries; pages carry a section-level token too
PATHS = ["/v1/youtube/channel", "/v1/youtube/channel-videos", "/v1/youtube/channel/playlists",
         "/v1/youtube/channel/lives", "/v1/youtube/channel/community-posts", "/v1/youtube/channel/shorts",
         "/v1/youtube/playlist", "/v1/youtube/search", "/v1/youtube/search/hashtag"]


def cached(name: str) -> dict:
    path = PROBE / f"{name}.json"
    if not path.exists():
        pytest.skip(f"probe cache {path} missing")
    return json.loads(path.read_text())


def browse(browse_id: str, path: str) -> dict:
    return {"browseEndpoint": {"browseId": browse_id, "canonicalBaseUrl": path}}


LOCKUP_VIDEO = {
    "contentId": "plN7JMbadRg", "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
    "contentImage": {"thumbnailViewModel": {
        "image": {"sources": [{"url": "https://i.ytimg.com/vi/plN7JMbadRg/small.jpg", "width": 168, "height": 94},
                              {"url": "https://i.ytimg.com/vi/plN7JMbadRg/hq720.jpg", "width": 720, "height": 404}]},
        "overlays": [{"thumbnailBottomOverlayViewModel": {"badges": [
            {"thumbnailBadgeViewModel": {"text": "57:59"}}]}}]}},
    "metadata": {"lockupMetadataViewModel": {
        "title": {"content": "Eat Everything In A Grocery Store, Win $1,000,000"},
        "metadata": {"contentMetadataViewModel": {"metadataRows": [
            {"metadataParts": [{"text": {"content": "MrBeast", "commandRuns": [
                {"onTap": {"innertubeCommand": browse(MRBEAST, "/@MrBeast")}}]}}]},
            {"metadataParts": [{"text": {"content": "251M views"}, "accessibilityLabel": "251 million views"},
                               {"text": {"content": "10 days ago"}}]}]}}}},
}

LOCKUP_PLAYLIST = {
    "contentId": "PLF98rxtslw6s", "contentType": "LOCKUP_CONTENT_TYPE_PLAYLIST",
    "contentImage": {"collectionThumbnailViewModel": {"primaryThumbnail": {"thumbnailViewModel": {
        "image": {"sources": [{"url": "https://i.ytimg.com/vi/9wq3VHsL_bE/hqdefault.jpg", "width": 480, "height": 270}]},
        "overlays": [{"thumbnailOverlayBadgeViewModel": {"thumbnailBadges": [
            {"thumbnailBadgeViewModel": {"text": "3 videos"}}]}}]}}}},
    "rendererContext": {"commandContext": {"onTap": {"innertubeCommand": {"watchEndpoint": {"videoId": "9wq3VHsL_bE"}}}}},
    "metadata": {"lockupMetadataViewModel": {"title": {"content": "Nancy Grace Roman Space Telescope"}}},
}

VIDEO_RENDERER = {
    "videoId": "jXMISgQq9MM", "title": {"runs": [{"text": "Epic fails #shorts"}]},
    "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/jXMISgQq9MM/hqdefault.jpg", "width": 480, "height": 360}]},
    "ownerText": {"runs": [{"text": "ZZang Funny", "navigationEndpoint": browse("UCvUzWu1Whyw1FWuLl9GOo_g", "/@zzangfunny")}]},
    "viewCountText": {"simpleText": "2,905,080 views"}, "publishedTimeText": {"simpleText": "1 year ago"},
    "lengthText": {"simpleText": "0:31"},
    "navigationEndpoint": {"commandMetadata": {"webCommandMetadata": {"url": "/shorts/jXMISgQq9MM"}}},
    "thumbnailOverlays": [{"thumbnailOverlayTimeStatusRenderer": {"text": {"simpleText": "0:31"}, "style": "SHORTS"}}],
}

CHANNEL_RENDERER = {
    "channelId": "UCSJ4gkVC6NrvII8umztf0Ow", "title": {"simpleText": "Lofi Girl"},
    "navigationEndpoint": browse("UCSJ4gkVC6NrvII8umztf0Ow", "/@LofiGirl"),
    "thumbnail": {"thumbnails": [{"url": "//yt3.ggpht.com/lofi=s176", "width": 176, "height": 176}]},
    "videoCountText": {"simpleText": "15.8M subscribers"}, "descriptionSnippet": {"runs": [{"text": "Lofi House"}]},
}

POST = {"post": {"backstagePostRenderer": {
    "postId": "UgkxHIfkQ3K2my65-kPq4VkRv9KrBRyN7alx",
    "authorText": {"runs": [{"text": "MrBeast", "navigationEndpoint": browse(MRBEAST, "/@MrBeast")}]},
    "contentText": {"runs": [{"text": "NO MORE WAITING, "}, {"text": "this Saturday"}]},
    "backstageAttachment": {"postMultiImageRenderer": {"images": [
        {"backstageImageRenderer": {"image": {"thumbnails": [
            {"url": "https://yt3.ggpht.com/a=s640", "width": 640, "height": 640},
            {"url": "https://yt3.ggpht.com/a=s1080", "width": 1080, "height": 1080}]}}},
        {"backstageImageRenderer": {"image": {"thumbnails": [
            {"url": "https://yt3.ggpht.com/b=s1080", "width": 1080, "height": 1080}]}}}]}},
    "voteCount": {"simpleText": "309K"}, "publishedTimeText": {"runs": [{"text": "2 weeks ago (edited)"}]},
}}}

VIEWERS, YTTV = "UCMDQxm7cUx3yXkfeHa5zJIQ", "UCMvBjCzggK1Xd5TNBxVqZwA"
# A share, as @YouTubeViewers' Posts tab shows one: the sharing channel's own text, no attachment or likes, and
# the shared post (another channel's) as originalPost.
SHARED_POST = {"post": {"sharedPostRenderer": {
    "postId": "UgkxLWl-HZtRwLZR0KlKXNQ7dOHJx5vhqgsd",
    "displayName": {"runs": [{"text": "YouTube Viewers", "navigationEndpoint": browse(VIEWERS, "/@YouTubeViewers")}]},
    "endpoint": browse(VIEWERS, "/@YouTubeViewers"),
    "content": {"runs": [{"text": "Check this out for all the info you need"}]},
    "publishedTimeText": {"runs": [{"text": "shared 1 month ago"}]},
    "navigationEndpoint": browse("FEpost_detail", "/post/UgkxLWl-HZtRwLZR0KlKXNQ7dOHJx5vhqgsd"),
    "originalPost": {"backstagePostRenderer": {
        "postId": "Ugkx5oVzvEFbH8nyTd9AGLSeswKlDOY6jo4j",
        "authorText": {"runs": [{"text": "YouTube TV", "navigationEndpoint": browse(YTTV, "/@youtubetv")}]},
        "authorEndpoint": browse(YTTV, "/@youtubetv"),
        "contentText": {"runs": [{"text": "NFL Sunday Ticket is back"}]},
        "backstageAttachment": {"videoRenderer": {**VIDEO_RENDERER, "videoId": "U_nPGqM3TVg"}},
        "voteCount": {"simpleText": "1.2K"}, "publishedTimeText": {"runs": [{"text": "1 month ago"}]},
    }},
}}}


def link(title: str, shown: str, target: str) -> dict:
    return {"channelExternalLinkViewModel": {"title": {"content": title}, "link": {
        "content": shown, "commandRuns": [{"onTap": {"innertubeCommand": {"urlEndpoint": {"url": target}}}}]}}}


def about_page(*links: dict) -> dict:
    return {
        "metadata": {"channelMetadataRenderer": {"title": "MrBeast", "externalId": MRBEAST,
                                                 "vanityChannelUrl": "http://www.youtube.com/@MrBeast"}},
        "microformat": {"microformatDataRenderer": {"tags": ["mrbeast6000", "beast"]}},
        "onResponseReceivedEndpoints": [{"showEngagementPanelEndpoint": {"engagementPanel": {
            "engagementPanelSectionListRenderer": {"content": {"sectionListRenderer": {"contents": [
                {"itemSectionRenderer": {"contents": [{"aboutChannelRenderer": {"metadata": {"aboutChannelViewModel": {
                    "channelId": MRBEAST, "canonicalChannelUrl": "http://www.youtube.com/@MrBeast",
                    "description": "SUBSCRIBE FOR A COOKIE!", "subscriberCountText": "521M subscribers",
                    "videoCountText": "1,006 videos", "viewCountText": "142,646,308,668 views",
                    "joinedDateText": {"content": "Joined Feb 20, 2012"}, "country": "United States",
                    "links": list(links),
                }}}}]}}]}}}}}}],
    }


ABOUT_PAGE = about_page(
    link("Twitter", "twitter.com/MrBeast", "https://www.youtube.com/redirect?q=https%3A%2F%2Ftwitter.com%2FMrBeast&v=1"),
    link("Shop", "shopmrbeast.com", "https://www.youtube.com/redirect?q=https%3A%2F%2Fshopmrbeast.com"),
    link("Instagram", "instagram.com/mrbeast", "https://www.instagram.com/mrbeast/"),
)


# --- offline: input validation -----------------------------------------------------------


@pytest.mark.parametrize("args, base", [
    ((MRBEAST, None, None), f"channel/{MRBEAST}"),
    ((None, "MrBeast", None), "@MrBeast"),
    ((None, "@MrBeast", None), "@MrBeast"),
    ((None, "john.doe", None), "@john.doe"),
    ((None, "https://www.youtube.com/@MrBeast/videos", None), "@MrBeast"),
    ((None, None, f"youtube.com/channel/{MRBEAST}"), f"channel/{MRBEAST}"),
    ((None, None, "https://m.youtube.com/c/MrBeast6000"), "c/MrBeast6000"),
    ((None, None, "https://www.youtube.com/user/MrBeast6000?x=1"), "user/MrBeast6000"),
    ((MRBEAST, "ignored", "ignored"), f"channel/{MRBEAST}"),
    # each parameter takes the other unambiguous forms
    (("@MrBeast", None, None), "@MrBeast"),
    ((f"https://www.youtube.com/channel/{MRBEAST}", None, None), f"channel/{MRBEAST}"),
    ((None, MRBEAST, None), f"channel/{MRBEAST}"),
    ((None, None, "@MrBeast"), "@MrBeast"),
    ((None, None, MRBEAST), f"channel/{MRBEAST}"),
    ((None, None, "https://youtube.com:443/@MrBeast"), "@MrBeast"),
    ((None, None, "https://WWW.YouTube.com/@MrBeast"), "@MrBeast"),
    ((None, None, "https://www.youtube.com/LinusTechTips"), "LinusTechTips"),  # legacy vanity URLs
    ((None, None, "youtube.com/pewdiepie/videos"), "pewdiepie"),
    # Indic / Thai handles: their vowel signs are combining marks, which \w does not match
    ((None, "गाना", None), "@" + quote("गाना")),
    (("@ครัวแม่ณี", None, None), "@" + quote("ครัวแม่ณี")),
    ((None, None, f"https://www.youtube.com/@{quote('தமிழ்')}/videos"), "@" + quote("தமிழ்")),
    ((None, None, "https://www.youtube.com/c/தமிழ்"), "c/" + quote("தமிழ்")),
])
def test_channel_base_accepts(args, base):
    assert M.channel_base(*args) == base


@pytest.mark.parametrize("args", [
    (None, None, "https://www.tiktok.com/@mrbeast"),
    (None, "https://www.instagram.com/mrbeast/", None),
    (None, None, "https://www.youtube.com/watch?v=plN7JMbadRg"),
    (None, None, "https://www.youtube.com/feed/trending"),
    (None, None, "https://www.youtube.com/shorts/aNI2jv7lBPQ"),
    (None, None, "https://youtu.be/plN7JMbadRg"),
    (None, None, "https://youtube.com.evil.example/@MrBeast"),
    ("UC123", None, None),
    ("MrBeast", None, None),
    ("@bad handle!", None, None),
    (None, "bad handle!", None),
    (None, "गाना!", None),
    (None, None, "MrBeast"),
])
def test_channel_base_rejects(args):
    with pytest.raises(BadRequest):
        M.channel_base(*args)


def test_playlist_and_hashtag_inputs():
    assert M.playlist_id_of(TF2) == TF2
    assert M.playlist_id_of(f"https://www.youtube.com/playlist?list={TF2}") == TF2
    assert M.playlist_id_of(f"https://youtu.be/RlJy2Kt8Be0?list={TF2}") == TF2
    assert M.hashtag_of("#Funny") == ("funny", False)
    assert M.hashtag_of("https://www.youtube.com/hashtag/funny") == ("funny", False)
    assert M.hashtag_of("https://www.youtube.com/hashtag/funny/shorts") == ("funny", True)
    assert M.hashtag_of("#गाना") == ("गाना", False)
    assert M.hashtag_of(f"https://www.youtube.com/hashtag/{quote('தமிழ்')}") == ("தமிழ்", False)
    # ZWNJ / ZWJ (Persian, Sinhala spelling) are accepted but dropped, as YouTube's own /hashtag/ pages do
    assert M.hashtag_of("#می\u200cخواهم") == ("میخواهم", False)
    assert M.hashtag_of(f"https://www.youtube.com/hashtag/{quote('ශ්\u200dරීලංකා')}/shorts") == ("ශ්රීලංකා", True)
    # retest-youtube_listings-3: any other spelling than the one YouTube's /hashtag/<tag> page uses is an empty feed
    for given, canonical in (("κόσμος", "κόσμοσ"), ("ΚΟΣΜΟΣ", "κοσμοσ"), ("#İstanbul", "istanbul"), ("ﬁre", "fire"),
                             (unicodedata.normalize("NFD", "türkiye"), "türkiye"), ("ǅemal", "džemal"),
                             ("Straße", "straße"), ("kadıköy", "kadıköy"), ("KADIKÖY", "kadiköy")):
        assert M.hashtag_of(given) == (canonical, False), given
        assert unicodedata.is_normalized("NFC", M.hashtag_of(given)[0]), given
    for bad in ("https://www.tiktok.com/tag/funny", "https://www.youtube.com/watch?v=x", "short"):
        with pytest.raises(BadRequest):
            M.playlist_id_of(bad)
    for bad in ("##", "two words", "https://www.tiktok.com/tag/funny", "https://www.youtube.com/@MrBeast", "#\u200d"):
        with pytest.raises(BadRequest):
            M.hashtag_of(bad)


def test_search_and_hashtag_params():
    assert P.search_params(None, None, None, None) is None
    assert P.search_params(None, "relevance", "shorts", None) == "EgIQCQ=="
    assert P.search_params(None, None, "channels", None) == "EgIQAg=="
    raw = base64.b64decode(P.search_params("this_week", "popular", "videos", "over_20_min"))
    assert raw == b"\x08\x03\x12\x06\x08\x03\x10\x01\x18\x02"
    # YouTube's current filter menu: 4 = under 3 minutes, 5 = 3-20 minutes (1 / 3 are the old 4-minute cut)
    assert base64.b64decode(P.search_params(None, None, None, "under_3_min")) == b"\x12\x02\x18\x04"
    assert base64.b64decode(P.search_params(None, None, None, "between_3_and_20_min")) == b"\x12\x02\x18\x05"
    assert P.hashtag_params("funny", True) == "6gULCgVmdW5ueRAAGAM="
    assert P.hashtag_params("funny", False) == "6gULCgVmdW5ueRAAGAE="
    # URL-safe like the params of YouTube's own /hashtag/தமிழ் page: it answers '/' and '+' with HTTP 400
    assert P.hashtag_params("தமிழ்", False) == "6gUVCg_grqTgrq7grr_grrTgr40QABgB"
    for tag in ("हिंदी", "ಕನ್ನಡ", "മലയാളം", "मराठी", "বাংলা", "россия", "みんな", "هشتگ‌ها"):
        params = P.hashtag_params(tag, True)
        assert not {"+", "/"} & set(params) and base64.urlsafe_b64decode(params)[2:].count(tag.encode()) == 1


def test_cursor_kind_and_seen_ids():
    state = M._state(encode_cursor({"k": "search", "t": "abc"}), "search")
    assert state["t"] == "abc"
    with pytest.raises(BadRequest):
        M._state(encode_cursor({"k": "search", "t": "abc"}), "hashtag")
    with pytest.raises(BadRequest):
        M._state(encode_cursor({"k": "search", "t": 5}), "search")
    assert M._cursor("search", None) is None
    seen = M._Seen(None)
    assert [seen.fresh({"id": i}) for i in ("a", "b", "a")] == [True, True, False] and seen.repeats == 1
    again = M._Seen({"s": seen.dump()})
    assert not again.fresh({"id": "b"}) and again.fresh({"id": "c"})
    with pytest.raises(BadRequest):
        M._Seen({"s": "%%%"})
    # the cursor keeps the last `keep` ids only (as the search and hashtag notes say): older ones can come again
    seen = M._Seen(None)
    assert all(seen.fresh({"id": f"v{i}"}) for i in range(M._Seen.keep + 1))
    again = M._Seen({"s": seen.dump()})
    assert again.fresh({"id": "v0"}) and not again.fresh({"id": "v1"})


@pytest.mark.parametrize("extra", [
    {"t": ""}, {"g": 123}, {"g": "ZZ"}, {"y": "reels"}, {"v": 5}, {"s": ["x"]}, {"x": "tv"}, {"n": -1}, {"n": True},
    {"n": "3"}, {"m": "meta"}, {"c": ["a", "b"]}, {"c": [1, 2, 3]}, {"c": "abc"},
    # playlist meta is echoed on later pages, so it must keep playlist_meta's shape
    {"m": {"totalVideos": "abc"}}, {"m": {"totalVideos": -1}}, {"m": {"totalVideos": True}}, {"m": {"title": 5}},
    {"m": {"owner": "str"}}, {"m": {"owner": {"id": 5}}}, {"m": {"owner": {"x": "y"}}}, {"m": {"x": 1}},
])
def test_cursor_field_types_are_checked(extra):
    with pytest.raises(BadRequest) as err:
        M._state(encode_cursor({"k": "search", "t": "abc", **extra}), "search")
    assert err.value.code == "bad_cursor"


def test_cursor_keeps_valid_playlist_meta():
    meta = {"title": "ScienceCasts", "totalVideos": 220,
            "owner": {"id": NASA, "name": "NASA", "url": "https://www.youtube.com/@NASA", "handle": "NASA"}}
    for m in (meta, {"title": None, "owner": None, "totalVideos": None}, {}):
        assert M._state(encode_cursor({"k": "playlist", "t": "abc", "m": m}), "playlist")["m"] == m


def test_cursor_round_trip_drops_empty_fields():
    token = M._cursor("search", "abc", g=None, y="shorts", x="mweb", c=[MRBEAST, None, None])
    assert M._state(token, "search") == {"k": "search", "t": "abc", "y": "shorts", "x": "mweb",
                                         "c": [MRBEAST, None, None]}


def test_region():
    assert M._region("us") == "US" and M._region(" gb ") == "GB" and M._region(None) is None
    for bad in ("ZZ", "XX", "EU", "AP", "USA", "u1"):
        with pytest.raises(BadRequest):
            M._region(bad)


# --- offline: mappers --------------------------------------------------------------------


def test_lockup_video():
    v = P.lockup_video(LOCKUP_VIDEO)
    assert v["id"] == "plN7JMbadRg" and v["title"].startswith("Eat Everything")
    assert v["thumbnail"].endswith("hq720.jpg")
    assert v["owner"] == {"title": "MrBeast", "id": MRBEAST, "handle": "MrBeast", "url": "https://www.youtube.com/@MrBeast",
                          "thumbnail": None}
    # the visible text, which continuation pages (no a11y labels) also carry
    assert (v["viewCountText"], v["viewCountInt"]) == ("251M views", 251_000_000)
    assert v["publishedTimeText"] == "10 days ago" and v["publishedTime"].endswith("Z")
    assert (v["lengthText"], v["lengthSeconds"]) == ("57:59", 3479)
    assert v["badges"] == [] and v["live"] is False and v["short"] is False
    out = P.sc_video(v, "video", {"title": "MrBeast", "thumbnail": None})
    assert list(out) == ["type", "id", "url", "title", "thumbnail", "channel", "viewCountText", "viewCountInt",
                         "publishedTimeText", "publishedTime", "lengthText", "lengthSeconds", "badges"]
    assert "publishedTime" not in P.sc_video(v, "live", None, published=False)
    assert P.lockup_video({**LOCKUP_VIDEO, "contentType": "LOCKUP_CONTENT_TYPE_PLAYLIST"}) is None


def test_length():
    assert P.length("57:59") == ("57:59", 3479) and P.length("  4:14 ") == ("4:14", 254)
    # a years-long stream's hours are digit-grouped (LofiGirl's 5qap5aO4i9A: '20,772:51:34'), per UI language
    for shown in ("20,772:51:34", "20.772:51:34", "20 772:51:34", "20\u202f772:51:34", "20\u00a0772:51:34"):
        assert P.length(shown) == (shown, 74782294), shown
    for junk in ("1,234", "20,77:51:34", "1,234:56", "LIVE", "", None, 5):
        assert P.length(junk) == (None, None), junk
    badge = {"thumbnailBadgeViewModel": {"text": "20,772:51:34"}}
    long_stream = copy.deepcopy(LOCKUP_VIDEO)
    long_stream["contentImage"]["thumbnailViewModel"]["overlays"] = [
        {"thumbnailBottomOverlayViewModel": {"badges": [badge]}}]
    v = P.lockup_video(long_stream)
    assert (v["lengthText"], v["lengthSeconds"]) == ("20,772:51:34", 74782294) and v["badges"] == []


def playlist_header(*parts: str) -> dict:
    return {"header": {"pageHeaderRenderer": {"content": {"pageHeaderViewModel": {"metadata": {
        "contentMetadataViewModel": {"metadataRows": [
            {"metadataParts": [{"text": {"content": x}} for x in parts]}]}}}}}}}


@pytest.mark.parametrize("parts, total", [
    (("1,204 videos", "12,345 views"), 1204), (("21 episodes",), 21), (("1 lesson",), 1), (("No views",), None),
])
def test_playlist_meta_totals(parts, total):
    assert P.playlist_meta(playlist_header(*parts))["totalVideos"] == total


COLLAB = "UCVMF2HD4ZgC0QHpU9Yq5Xrw"


def collab_member(name: str, handle: str, browse_id: str) -> dict:
    return {"listItemViewModel": {
        "title": {"content": name}, "subtitle": {"content": f"\u200e\u2068@{handle}\u2069 • \u206823.1K subscribers\u2069"},
        "leadingAccessory": {"avatarViewModel": {"image": {"sources": [
            {"url": f"https://yt3.ggpht.com/{handle}=s68", "width": 68, "height": 68}]}}},
        "rendererContext": {"commandContext": {"onTap": {"innertubeCommand": {
            "commandMetadata": {"webCommandMetadata": {"url": f"/channel/{browse_id}"}},
            "browseEndpoint": {"browseId": browse_id}}}}}}}


def test_lockup_video_collaboration_owner():
    """Collaboration lockups name their channels without links; the avatar stack's dialog lists them."""
    vm = {**LOCKUP_VIDEO, "metadata": {"lockupMetadataViewModel": {
        "title": {"content": "Episode 1"},
        "image": {"avatarStackViewModel": {
            "avatars": [{"avatarViewModel": {"image": {"sources": []}}}],
            "rendererContext": {"commandContext": {"onTap": {"innertubeCommand": {"showDialogCommand": {
                "panelLoadingStrategy": {"inlineContent": {"dialogViewModel": {"customContent": {"listViewModel": {
                    "listItems": [collab_member("99% Invisible", "99percentinvisiblepodcast", COLLAB),
                                  collab_member("BBC Podcasts", "bbcpodcasts", "UCbbc")]}}}}}}}}}}}},
        "metadata": {"contentMetadataViewModel": {"metadataRows": [
            {"metadataParts": [{"text": {"content": "99% Invisible"}}, {"text": {"content": "and BBC Podcasts"}}]},
            {"metadataParts": [{"text": {"content": "1.2K views"}}, {"text": {"content": "1 month ago"}}]}]}}}}}
    v = P.lockup_video(vm)
    assert v["owner"] == {"title": "99% Invisible", "id": COLLAB, "handle": "99percentinvisiblepodcast",
                          "url": "https://www.youtube.com/@99percentinvisiblepodcast",
                          "thumbnail": "https://yt3.ggpht.com/99percentinvisiblepodcast=s68"}
    assert v["viewCountText"] == "1.2K views" and v["publishedTimeText"] == "1 month ago"
    vm["metadata"]["lockupMetadataViewModel"]["image"]["avatarStackViewModel"]["rendererContext"] = {}
    assert P.lockup_video(vm)["owner"] is None


def test_lockup_playlist():
    pl = P.lockup_playlist(LOCKUP_PLAYLIST)
    assert pl == {"type": "playlist", "id": "PLF98rxtslw6s", "title": "Nancy Grace Roman Space Telescope",
                  "thumbnail": "https://i.ytimg.com/vi/9wq3VHsL_bE/hqdefault.jpg",
                  "playlistUrl": "https://www.youtube.com/playlist?list=PLF98rxtslw6s", "videoId": "9wq3VHsL_bE",
                  "videoUrl": "https://www.youtube.com/watch?v=9wq3VHsL_bE", "videoCount": 3, "channel": None}
    assert P.lockup_playlist(LOCKUP_VIDEO) is None


def test_shorts_lockup_titles_and_views():
    full = P.shorts_lockup({"onTap": {"innertubeCommand": {"reelWatchEndpoint": {"videoId": "aNI2jv7lBPQ"}}},
                            "overlayMetadata": {"primaryText": {"content": "Giving $10,000"},
                                                "secondaryText": {"content": "25M views"}}})
    assert full == {"type": "short", "id": "aNI2jv7lBPQ", "url": "https://www.youtube.com/watch?v=aNI2jv7lBPQ",
                    "title": "Giving $10,000", "thumbnail": None, "viewCountText": "25M views",
                    "viewCountInt": 25_000_000}
    slim = P.shorts_lockup({"entityId": "shorts-shelf-item-aNI2jv7lBPQ",
                            "accessibilityText": "Giving $10,000, 25 million views - play Short"})
    assert slim["id"] == "aNI2jv7lBPQ" and slim["title"] == "Giving $10,000" and slim["viewCountInt"] is None
    assert P.shorts_lockup({"entityId": "nope"}) is None


def test_video_renderer_and_search_buckets():
    v = P.video_renderer(VIDEO_RENDERER)
    assert v["short"] and not v["live"]
    assert (v["viewCountInt"], v["lengthSeconds"]) == (2_905_080, 31)
    assert v["owner"]["handle"] == "zzangfunny" and v["owner"]["id"] == "UCvUzWu1Whyw1FWuLl9GOo_g"
    long_video = {**VIDEO_RENDERER, "videoId": "BzSzwqb-OEE", "navigationEndpoint": {}, "thumbnailOverlays": [],
                  "lengthText": {"simpleText": "4:14"},
                  "ownerText": {"runs": [{"text": "NF", "navigationEndpoint": {"browseEndpoint": {"browseId": "UCoRR"}}}]}}
    items = [("videoRenderer", VIDEO_RENDERER), ("videoRenderer", long_video), ("channelRenderer", CHANNEL_RENDERER),
             ("lockupViewModel", LOCKUP_PLAYLIST), ("lockupViewModel", LOCKUP_VIDEO), ("videoRenderer", VIDEO_RENDERER)]
    out = M._search_payload(items, None, M._Seen(None))
    assert [x["id"] for x in out["shorts"]] == ["jXMISgQq9MM"]  # the repeat is dropped
    assert [x["id"] for x in out["videos"]] == ["BzSzwqb-OEE", "plN7JMbadRg"]
    assert out["videos"][0]["channel"]["handle"] == "channel/UCoRR"  # SC's form when there is no @handle
    ch = out["channels"][0]
    assert (ch["handle"], ch["subscriberCount"], ch["thumbnail"]) == ("LofiGirl", 15_800_000, "https://yt3.ggpht.com/lofi=s176")
    assert out["playlists"][0]["id"] == "PLF98rxtslw6s" and out["lives"] == out["shelves"] == []


def test_post():
    post = P.post(POST)
    assert post["id"] == "UgkxHIfkQ3K2my65-kPq4VkRv9KrBRyN7alx"
    assert post["channel"] == {"id": MRBEAST, "title": "MrBeast", "url": "https://www.youtube.com/@MrBeast",
                               "handle": "MrBeast"}
    assert post["content"] == "NO MORE WAITING, this Saturday"
    assert post["images"] == ["https://yt3.ggpht.com/a=s1080", "https://yt3.ggpht.com/b=s1080"]
    assert post["image"] == post["images"][0]
    assert (post["likeCountText"], post["likeCount"]) == ("309K", 309_000)
    assert post["publishedTimeText"] == "2 weeks ago (edited)" and post["publishedTime"]
    assert post["video"] is None and "originalPost" not in post


@pytest.mark.parametrize("linked_name", [True, False], ids=["displayName_run", "endpoint_only"])
def test_post_shared(linked_name):
    """A share is listed (not dropped) with the sharing channel; the post it shares is nested as originalPost, as
    /v1/youtube/community-post does."""
    thread = copy.deepcopy(SHARED_POST)
    if not linked_name:  # the channel then only comes from the share's endpoint
        thread["post"]["sharedPostRenderer"]["displayName"] = {"simpleText": "YouTube Viewers"}
    post = P.post(thread)
    assert post["id"] == "UgkxLWl-HZtRwLZR0KlKXNQ7dOHJx5vhqgsd"
    assert post["url"] == "https://www.youtube.com/post/UgkxLWl-HZtRwLZR0KlKXNQ7dOHJx5vhqgsd"
    assert post["channel"] == {"id": VIEWERS, "title": "YouTube Viewers", "url": "https://www.youtube.com/@YouTubeViewers",
                               "handle": "YouTubeViewers"}
    assert post["content"] == "Check this out for all the info you need"
    assert (post["image"], post["images"], post["video"]) == (None, [], None)
    assert (post["likeCountText"], post["likeCount"]) == (None, None)  # YouTube shows no likes on the share
    assert post["publishedTimeText"] == "shared 1 month ago" and post["publishedTime"]
    original = post["originalPost"]
    assert original["id"] == "Ugkx5oVzvEFbH8nyTd9AGLSeswKlDOY6jo4j"
    assert original["url"] == "https://www.youtube.com/post/Ugkx5oVzvEFbH8nyTd9AGLSeswKlDOY6jo4j"
    assert original["channel"] == {"id": YTTV, "title": "YouTube TV", "url": "https://www.youtube.com/@youtubetv",
                                   "handle": "youtubetv"}
    assert original["content"] == "NFL Sunday Ticket is back" and original["video"]["id"] == "U_nPGqM3TVg"
    assert (original["likeCountText"], original["likeCount"]) == ("1.2K", 1200) and original["publishedTime"]
    assert "originalPost" not in original
    del thread["post"]["sharedPostRenderer"]["originalPost"]  # e.g. the shared post was deleted
    assert P.post(thread)["id"] == post["id"] and "originalPost" not in P.post(thread)


def test_channel_about():
    out = P.channel_about(ABOUT_PAGE)
    assert out["channelId"] == MRBEAST and out["name"] == "MrBeast"
    assert out["channel"] == "http://www.youtube.com/@MrBeast"
    assert (out["subscriberCount"], out["subscriberCountText"]) == (521_000_000, "521M subscribers")
    assert out["joinedDateText"] == "Joined Feb 20, 2012" and out["country"] == "United States"
    assert out["tags"] == "mrbeast6000, beast" and out["email"] is None
    assert out["twitter"] == "https://twitter.com/MrBeast"
    assert out["instagram"] == "https://www.instagram.com/mrbeast/"
    assert out["store"] == "https://shopmrbeast.com" and out["links"] == ["https://shopmrbeast.com"]


def redirect(q: str) -> str:
    return "https://www.youtube.com/redirect?event=channel_description&q=" + quote(q, safe="")


def test_channel_about_store_hosts_and_scheme_less_links():
    """LinusTechTips: the store is titled 'lttstore.com' and the redirect targets have no scheme."""
    out = P.channel_about(about_page(
        link("Adobe", "photoshop.com", redirect("https://www.photoshop.com/")),
        link("TikTok", "tiktok.com/@linustech", redirect("www.tiktok.com/@linustech")),
        link("X", "x.com/linustech", redirect("x.com/linustech")),
        link("lttstore.com", "lttstore.com", redirect("https://www.lttstore.com/")),
        link("Instagram", "instagram.com/linustech", redirect("instagram.com/linustech")),
        link("Merch", "creator-merch.example", redirect("https://creator-merch.example/")),
    ))
    assert out["store"] == "https://www.lttstore.com/"  # photoshop.com is not a shop
    assert out["twitter"] == "https://x.com/linustech" and out["instagram"] == "https://instagram.com/linustech"
    assert out["links"] == ["https://www.photoshop.com/", "https://www.tiktok.com/@linustech",
                            "https://www.lttstore.com/", "https://creator-merch.example/"]
    for host, store in [("store.example.com", True), ("shop.example", True), ("merch.example", True),
                        ("example-merch.com", True), ("workshop.example", False), ("restore.example", False),
                        # only the word 'restore' is excluded, not every '…re' + 'store' host
                        ("hardwarestore.com", True), ("naturestore.com", True), ("thecarestore.com", True),
                        ("softwarestore.co", True), ("explorestore.com", True), ("lttstore.com", True),
                        ("my-restore.com", False), ("backup.restore.example", False),
                        ("bishopbarron.com", False), ("merchantmaverick.com", False), ("merchandise.example", True)]:
        page = about_page(link("Website", host, redirect(f"https://{host}/")))
        assert (P.channel_about(page)["store"] is not None) is store, host
    for title, store in [("Merch", True), ("Official Store", True), ("Merchant Maverick", False), ("Restore", False)]:
        page = about_page(link(title, "example.com", redirect("https://example.com/")))
        assert (P.channel_about(page)["store"] is not None) is store, title


def cont(token: str) -> dict:
    return {"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {
        "token": token, "request": "CONTINUATION_REQUEST_TYPE_BROWSE"}}}}


def test_walk_skips_header_chips_and_collects_tokens():
    grid = {"richGridRenderer": {
        "header": {"chipBarViewModel": {"chips": [cont("CHIP")]}},
        "contents": [{"richItemRenderer": {"content": {"lockupViewModel": LOCKUP_VIDEO}}}, cont("NEXT")]}}
    items, tokens = P.walk(grid)
    assert [k for k, _ in items] == ["lockupViewModel"] and tokens == ["NEXT"]
    resp = {"onResponseReceivedActions": [{"appendContinuationItemsAction": {"continuationItems": [
        {"richItemRenderer": {"content": {"lockupViewModel": LOCKUP_VIDEO}}}]}}]}
    items, tokens = P.walk(P.continuation_items(resp))
    assert len(items) == 1 and tokens == []
    assert P.walk(P.continuation_items({"responseContext": {}})) == ([], [])  # terminal page


def test_probe_pages():
    """Whole cached pages (from the probe run) still parse into full listings."""
    videos = cached("mrbeast_videos")["data"]
    content = P.tab_content(videos, "videos")
    items, tokens = P.walk(content)
    assert sum(P.lockup_video(n) is not None for k, n in items if k == "lockupViewModel") >= 30 and len(tokens) == 1
    assert P.sort_chip_token(content, "Popular")
    assert P.tab_content(videos, "shorts") is None
    assert P.channel_ref(videos) == [MRBEAST, "MrBeast", "MrBeast"]
    meta = P.playlist_meta(cached("pl_tf2")["data"])
    assert meta["totalVideos"] == 29 and meta["owner"]["name"] == "Wickman Wish"


def test_walk_prefers_the_token_next_to_the_items():
    """Playlist pages carry a section-level token too, which leads to an empty page."""
    section = {"sectionListRenderer": {"contents": [
        {"itemSectionRenderer": {"contents": [{"playlistVideoListRenderer": {"contents": [
            {"lockupViewModel": LOCKUP_VIDEO}, cont("INNER")]}}]}}, cont("OUTER")]}}
    assert P.walk(section)[1] == ["INNER", "OUTER"]
    items, tokens = P.walk(P.tab_content(cached("pl_nasa_196")))
    assert len(items) == 100 and len(tokens) == 2 and len(tokens[0]) > len(tokens[1])


def test_lockup_playlist_shows_and_podcasts():
    pls = [x for _, n in P.walk(P.tab_content(cached("mkbhd_playlists")["page"], "playlists"))[0]
           if (x := P.lockup_playlist(n))]
    shows = [x for x in pls if x["videoId"] is None]
    assert len(pls) == 23 and len(shows) == 8
    assert all(x["videoUrl"] is None and x["playlistUrl"].endswith(x["id"]) for x in shows)
    assert shows[0]["videoCount"] == 5  # '5 episodes'
    pls = [x for _, n in P.walk(P.tab_content(cached("nasa_playlists_podcasts")["page"], "playlists"))[0]
           if (x := P.lockup_playlist(n))]
    podcast = next(x for x in pls if x["id"] == "PL2aBZuCeDwlTxZeGRlKrWpLiX_W7jpt6p")
    assert podcast["channel"] is None and podcast["videoCount"] == 14  # 'View full podcast' is not a channel


def test_video_with_context_exact_views():
    vr = {"videoId": "plN7JMbadRg", "headline": {"runs": [{"text": "Eat Everything"}]},
          "shortViewCountText": {"runs": [{"text": "251M views"}]},
          "lengthText": {"runs": [{"text": "57:59"}]}, "publishedTimeText": {"runs": [{"text": "10 days ago"}]}}
    vr["headline"]["accessibility"] = {"accessibilityData": {
        "label": "Eat Everything by MrBeast 251,675,820 views 10 days ago 57 minutes"}}
    v = P.video_item("videoWithContextRenderer", vr)
    assert (v["title"], v["viewCountText"], v["viewCountInt"]) == ("Eat Everything", "251,675,820 views", 251_675_820)
    assert v["lengthSeconds"] == 3479 and v["publishedTimeText"] == "10 days ago"
    vr["headline"]["accessibility"]["accessibilityData"]["label"] = "Eat Everything"
    assert P.video_item("videoWithContextRenderer", vr)["viewCountText"] == "251M views"
    items, tokens = P.walk(cached("mweb_mrbeast_videos")["contents"])
    videos = [v for k, n in items if (v := P.video_item(k, n))]
    assert len(videos) == 30 and len(tokens) == 1
    assert all(re.fullmatch(r"[\d,]+ views", v["viewCountText"]) and v["viewCountInt"] > 0 for v in videos)


def test_short_lockup_mweb_owner():
    vm = {"onTap": {"innertubeCommand": {"reelWatchEndpoint": {"videoId": "7HA8WB0q4Pw"}}},
          "overlayMetadata": {"primaryText": {"content": "Before vs After"}, "secondaryText": {"content": "3.2M views"}},
          "belowThumbnailMetadata": {"primaryText": {"content": "Jeremy Miller"}, "secondaryText": {"content": "1 year ago"},
                                     "avatar": {"avatarViewModel": {
                                         "image": {"sources": [{"url": "https://yt3.ggpht.com/j=s68", "width": 68}]},
                                         "endpoint": {"innertubeCommand": browse("UCWf22vfOkDc7FTOrkdKI2rw",
                                                                                 "/@jeremy.miller")}}}}}
    v = P.short_lockup(vm)
    assert v["owner"] == {"title": "Jeremy Miller", "id": "UCWf22vfOkDc7FTOrkdKI2rw", "handle": "jeremy.miller",
                          "url": "https://www.youtube.com/@jeremy.miller", "thumbnail": "https://yt3.ggpht.com/j=s68"}
    assert v["publishedTimeText"] == "1 year ago" and v["viewCountInt"] == 3_200_000
    assert P.short_lockup({"onTap": vm["onTap"]})["owner"] is None


def test_search_shorts_flattens_mweb_shelves():
    items, tokens = P.walk(cached("mweb_search_running_shorts")["contents"])
    out = M._search_payload(items, "shorts", M._Seen(None))
    shorts = out["shorts"]
    assert len(shorts) >= 30 and out["shelves"] == out["videos"] == [] and len(tokens) == 1
    assert len({s["id"] for s in shorts}) == len(shorts)
    assert all(s["type"] == "short" and s["channel"]["id"].startswith("UC") and s["channel"]["handle"]
               for s in shorts)
    assert sum(bool(s["lengthSeconds"]) for s in shorts) >= 10  # the standalone ones
    assert list(shorts[0]) == list(P.sc_video(P.lockup_video(LOCKUP_VIDEO), "short", None))


def test_mweb_hashtag_lockups():
    videos = [v for k, n in P.walk(cached("mweb_hashtag_funny")["contents"])[0]
              if k == "lockupViewModel" and (v := P.lockup_video(n))]
    assert len(videos) >= 20 and sum(v["short"] for v in videos) >= len(videos) // 2
    assert all(v["owner"]["id"].startswith("UC") and v["owner"]["title"] and v["owner"]["thumbnail"] for v in videos)
    assert any(v["lengthSeconds"] for v in videos if not v["short"])


# --- offline: routes ---------------------------------------------------------------------


def test_paths_are_routed_and_implemented(client):
    routed = set(client.app.openapi()["paths"])
    status = {e["path"]: e["status"] for e in client.get("/meta/coverage").json()["endpoints"]}
    for path in PATHS:
        assert path in routed and status[path] == "implemented", path


@pytest.mark.parametrize("path", PATHS)
def test_missing_params_is_400(client, path):
    r = client.get(path)
    assert r.status_code == 400, r.text
    assert r.json()["success"] is False


@pytest.mark.parametrize("path, params", [
    ("/v1/youtube/channel", {"url": "https://www.tiktok.com/@mrbeast"}),
    ("/v1/youtube/channel-videos", {"handle": "https://www.instagram.com/mrbeast/"}),
    ("/v1/youtube/channel/shorts", {"channelId": "https://www.tiktok.com/@mrbeast"}),
    ("/v1/youtube/playlist", {"playlist_id": "https://www.tiktok.com/@x/playlist/y-123"}),
    ("/v1/youtube/search/hashtag", {"hashtag": "https://www.tiktok.com/tag/funny"}),
    ("/v1/youtube/search", {"query": "lofi", "type": "reels"}),
    ("/v1/youtube/search", {"query": "lofi", "region": "USA"}),
    ("/v1/youtube/search", {"query": "lofi", "region": "ZZ"}),
    ("/v1/youtube/search", {"query": "lofi", "continuationToken": encode_cursor({"k": "search", "t": "x", "g": 123})}),
    ("/v1/youtube/search", {"query": "lofi", "continuationToken": encode_cursor({"k": "search", "t": "x", "x": "ios"})}),
    ("/v1/youtube/search/hashtag", {"hashtag": "funny", "continuationToken": encode_cursor({"k": "hashtag", "t": ["x"]})}),
    ("/v1/youtube/channel-videos", {"continuationToken": encode_cursor({"k": "channel-videos", "t": "x", "c": "UC"})}),
    ("/v1/youtube/playlist", {"playlist_id": TF2, "continuationToken": encode_cursor({"k": "playlist", "t": "x",
                                                                                      "p": TF2, "n": "5"})}),
    ("/v1/youtube/channel-videos", {"handle": "MrBeast", "sort": "oldest"}),
    ("/v1/youtube/channel/lives", {"continuationToken": "garbage"}),
    ("/v1/youtube/channel/lives", {"continuationToken": encode_cursor({"k": "channel-posts", "t": "x"})}),
    ("/v1/youtube/playlist", {"playlist_id": TF2, "continuationToken": encode_cursor({"k": "playlist", "t": "x",
                                                                                      "p": UPLOADS})}),
])
def test_bad_input_is_400(client, path, params):
    r = client.get(path, params=params)
    assert r.status_code == 400, r.text


def test_paid_promotions_is_501(client):
    r = client.get("/v1/youtube/channel-videos", params={"handle": "MrBeast", "is_paid_promotions": "true"})
    assert r.status_code == 501 and r.json()["error"] == "not_supported"


@pytest.mark.parametrize("params", [
    {"is_paid_promotions": "true"},
    {"is_paid_promotions": "1", "type": "shorts", "region": "gb", "sortBy": "popular"},
    {"is_paid_promotions": "TRUE", "region": "CN"},  # refused before the region is looked at
    {"is_paid_promotions": "yes", "continuationToken": M._cursor("search", "TOKEN")},
], ids=["first_page", "filters", "region", "cursor"])
def test_search_paid_promotions_is_501(client, upstream, params):
    """ScrapeCreators documents is_paid_promotions=true on search too. It is refused there as on channel-videos,
    not dropped in silence (which answered ordinary results as if they were the paid-promotion ones)."""
    r = client.get("/v1/youtube/search", params={"query": "iphone review", **params})
    assert r.status_code == 501 and r.json()["error"] == "not_supported", r.json()
    assert r.json()["message"].startswith("is_paid_promotions is not supported") and upstream.calls == []


def test_search_paid_promotions_false_is_a_normal_search(client, upstream):
    upstream.answers = [{**NO_RESULTS, "estimatedResults": "0"}]
    r = client.get("/v1/youtube/search", params={"query": "iphone review", "is_paid_promotions": "false"})
    assert r.status_code == 200 and r.json()["videos"] == []
    assert upstream.calls[0][1]["query"] == {"query": "iphone review"}


# --- offline: routes against canned YouTube responses -------------------------------------


class Upstream:
    """Stands in for YouTube: _page / _api answer from queued responses and log each call."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch):
        self.calls: list[tuple[str, dict]] = []
        self.answers: list = []

        async def run(fn):
            return fn(None)

        monkeypatch.setattr(M, "_run", run)
        monkeypatch.setattr(M, "_page", lambda ie, url, item_id: self.answer("page", {"url": url}))
        monkeypatch.setattr(M, "_api", lambda ie, query, ep="browse", **kw: self.answer(ep, {"query": query, **kw}))

    def answer(self, kind: str, call: dict):
        self.calls.append((kind, call))
        return self.answers.pop(0)


def appended(items: list) -> dict:
    return {"onResponseReceivedActions": [{"appendContinuationItemsAction": {"continuationItems": items}}],
            "responseContext": {"visitorData": "VISITOR2"}}


@pytest.fixture
def upstream(monkeypatch) -> Upstream:
    return Upstream(monkeypatch)


def test_playlist_route_follows_the_inner_token(client, upstream):
    data = cached("pl_nasa_196")
    items, tokens = P.walk(P.tab_content(data))
    upstream.answers = [(data, {})]
    first = client.get("/v1/youtube/playlist", params={"playlist_id": "PLx196xxxxxxx"}).json()
    assert len(first["videos"]) == 100 and first["totalVideos"] == 196
    state = M._state(first["continuationToken"], "playlist")
    assert state["t"] == tokens[0] and state["n"] == 100
    rest = [{"lockupViewModel": n} for _, n in items[:96]]
    upstream.answers = [appended(rest + [cont("STALE")])]
    second = client.get("/v1/youtube/playlist", params={"playlist_id": "PLx196xxxxxxx",
                                                         "continuationToken": first["continuationToken"]}).json()
    assert upstream.calls[-1] == ("browse", {"query": {"continuation": tokens[0]}, "cursor": True})
    assert len(second["videos"]) == 96 and second["continuationToken"] is None  # 196 served: no empty page 3


def test_shorts_playlist_route_names_the_uploader(client, upstream):
    upstream.answers = [(cached("pl_uush")["data"], {})]
    body = client.get("/v1/youtube/playlist", params={"playlist_id": "UUSH" + MRBEAST[2:]}).json()
    assert len(body["videos"]) == 100 and body["continuationToken"] is None
    assert all(v["channel"] == {"title": "MrBeast", "url": "https://www.youtube.com/@MrBeast"} and v["title"]
               for v in body["videos"])


def test_unviewable_playlist_is_400(client, monkeypatch):
    class IE:
        def initialize(self) -> None:
            pass

    class Ydl:
        def get_info_extractor(self, name: str) -> IE:
            return IE()

    async def with_ydl(platform, go):
        return go(Ydl())

    def page(ie, url, item_id):
        raise ExtractorError("YouTube said: This playlist type is unviewable.", expected=True)

    monkeypatch.setattr(M, "with_ydl", with_ydl)
    monkeypatch.setattr(M, "_page", page)
    r = client.get("/v1/youtube/playlist", params={"playlist_id": "RDdQw4w9WgXcQ"})
    assert r.status_code == 400 and r.json()["error"] == "unviewable_playlist"


@pytest.mark.parametrize("fixture, channel", [
    ("mkbhd_playlists", ["UCBJycsmduvYEL83R_U4JriQ", "Marques Brownlee", "mkbhd"]),
    ("nasa_playlists_podcasts", [NASA, "NASA", "NASA"]),
])
def test_channel_playlists_route_owns_shows_and_podcasts(client, upstream, fixture, channel):
    upstream.answers = [(cached(fixture)["page"], {})]
    pls = client.get("/v1/youtube/channel/playlists", params={"handle": channel[2]}).json()["playlists"]
    want = {"id": channel[0], "title": channel[1], "handle": channel[2],
            "channelUrl": f"https://www.youtube.com/@{channel[2]}"}
    assert len(pls) >= 23 and all(x["channel"] == want for x in pls)


def test_channel_videos_route_lists_on_mweb(client, upstream):
    data, mweb = cached("mrbeast_videos")["data"], cached("mweb_mrbeast_videos")
    upstream.answers = [(data, {}), mweb]
    body = client.get("/v1/youtube/channel-videos", params={"handle": "MrBeast"}).json()
    assert upstream.calls[1] == ("browse", {"query": P.tab_browse(data), "client": "mweb"})
    videos = body["videos"]
    assert len(videos) == 30 and all(re.fullmatch(r"[\d,]+ views", v["viewCountText"]) for v in videos)
    assert all(v["channel"] == {"title": "MrBeast", "thumbnail": None} and v["lengthSeconds"] for v in videos)
    state = M._state(body["continuationToken"], "channel-videos")
    assert state["x"] == "mweb" and state["c"] == [MRBEAST, "MrBeast", "MrBeast"]
    upstream.answers = [appended([{"videoWithContextRenderer": n} for k, n in P.walk(mweb["contents"])[0][:5]])]
    page2 = client.get("/v1/youtube/channel-videos", params={"continuationToken": body["continuationToken"]}).json()
    assert len(page2["videos"]) == 5 and page2["videos"][0]["channel"]["title"] == "MrBeast"
    assert upstream.calls[-1] == ("browse", {"query": {"continuation": state["t"]}, "client": "mweb", "cursor": True})


def posts_tab(*threads: dict) -> dict:
    """A channel's Posts tab webpage holding these backstagePostThreadRenderer threads and a next-page token."""
    tab = {"tabRenderer": {"selected": True, "endpoint": {"commandMetadata": {"webCommandMetadata": {
        "url": "/@YouTubeViewers/posts"}}}, "content": {"sectionListRenderer": {"contents": [{"itemSectionRenderer": {
            "contents": [*({"backstagePostThreadRenderer": t} for t in threads), cont("NEXT")]}}]}}}}
    return {"contents": {"twoColumnBrowseResultsRenderer": {"tabs": [tab]}},
            "metadata": {"channelMetadataRenderer": {"externalId": VIEWERS, "title": "YouTube Viewers",
                                                     "vanityChannelUrl": "http://www.youtube.com/@YouTubeViewers"}}}


def test_channel_posts_route_keeps_shared_posts(client, upstream):
    """A share among ordinary posts stays in the listing, in place, on the first page and on later ones."""
    threads = (POST, SHARED_POST, POST)
    upstream.answers = [(posts_tab(*threads), {})]
    body = client.get("/v1/youtube/channel/community-posts", params={"handle": "YouTubeViewers"}).json()
    ids = [x["id"] for x in body["posts"]]
    assert ids == ["UgkxHIfkQ3K2my65-kPq4VkRv9KrBRyN7alx", "UgkxLWl-HZtRwLZR0KlKXNQ7dOHJx5vhqgsd",
                   "UgkxHIfkQ3K2my65-kPq4VkRv9KrBRyN7alx"]
    share = body["posts"][1]
    assert share["channel"]["handle"] == "YouTubeViewers" and share["originalPost"]["channel"]["handle"] == "youtubetv"
    assert M._state(body["continuationToken"], "channel-posts")["t"] == "NEXT"
    assert_conforms(body, "/v1/youtube/channel/community-posts", min_coverage=0.5)
    upstream.answers = [appended([{"backstagePostThreadRenderer": t} for t in threads[1:]])]
    page2 = client.get("/v1/youtube/channel/community-posts", params={"continuationToken": body["continuationToken"]})
    assert [x["id"] for x in page2.json()["posts"]] == ids[1:]


def test_channel_videos_route_falls_back_to_web(client, upstream):
    upstream.answers = [(cached("mrbeast_videos")["data"], {}), {}]
    body = client.get("/v1/youtube/channel-videos", params={"handle": "MrBeast"}).json()
    assert len(body["videos"]) >= 30 and "x" not in M._state(body["continuationToken"], "channel-videos")


def test_search_shorts_route_runs_on_mweb(client, upstream):
    upstream.answers = [cached("mweb_search_running_shorts")]
    body = client.get("/v1/youtube/search", params={"query": "running", "type": "shorts", "region": "gb"}).json()
    ep, call = upstream.calls[0]
    assert ep == "search" and call["client"] == "mweb" and call["region"] == "GB"
    assert call["query"] == {"query": "running", "params": P.search_params(None, None, "shorts", None)}
    assert len(body["shorts"]) >= 30 and all(s["channel"]["id"] for s in body["shorts"])
    state = M._state(body["continuationToken"], "search")
    assert (state["x"], state["y"], state["g"]) == ("mweb", "shorts", "GB")


def search_page(promo: dict) -> dict:
    return {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {"sectionListRenderer": {"contents": [
        {"itemSectionRenderer": {"contents": [{"backgroundPromoRenderer": promo}]}}]}}}}}


WENT_WRONG = search_page({"title": {"simpleText": "Something went wrong"}, "ctaButton": {"buttonRenderer": {
    "text": {"runs": [{"text": "Try again"}]}}}})
NO_RESULTS = search_page({"title": {"runs": [{"text": "No results found"}]}, "icon": {"iconType": "EMPTY_SEARCH"}})


def test_search_unsupported_region_is_400(client, upstream):
    """YouTube answers regions it does not serve (CN, KP…) with a 'Something went wrong' page: not an empty
    (and cached) success."""
    upstream.answers = [WENT_WRONG]
    r = client.get("/v1/youtube/search", params={"query": "lofi", "region": "cn"})
    assert r.status_code == 400 and r.json()["error"] == "unsupported_region" and "CN" in r.json()["message"]
    upstream.answers = [WENT_WRONG]
    r = client.get("/v1/youtube/search", params={"query": "lofi"})
    assert r.status_code == 500 and r.json()["error"] == "upstream_error"
    upstream.answers = [{**NO_RESULTS, "estimatedResults": "0"}]  # a real empty search stays a 200
    body = client.get("/v1/youtube/search", params={"query": "zqxjvkwp", "region": "US"}).json()
    assert body["videos"] == [] and body["continuationToken"] is None


def test_search_unsupported_region_on_mweb_is_400(client, monkeypatch):
    def api(ie, query, ep="browse", **kw):  # mweb answers an unserved region with HTTP 400
        response = Response(io.BytesIO(b""), "https://m.youtube.com/youtubei/v1/search", {}, status=400)
        raise ExtractorError("HTTP Error 400", cause=HTTPError(response))

    async def run(fn):
        return fn(None)

    monkeypatch.setattr(M, "_run", run)
    monkeypatch.setattr(M, "_api", api)
    r = client.get("/v1/youtube/search", params={"query": "lofi", "type": "shorts", "region": "KP"})
    assert r.status_code == 400 and r.json()["error"] == "unsupported_region"


def test_hashtag_cursor_is_bound_to_its_tag(client, upstream):
    token = M._cursor("hashtag", "TOKEN", p="funny")
    for tag, cursor in [("cooking", token), ("funny", M._cursor("hashtag", "TOKEN"))]:
        r = client.get("/v1/youtube/search/hashtag", params={"hashtag": tag, "continuationToken": cursor})
        assert r.status_code == 400 and r.json()["error"] == "bad_cursor"
    assert "different hashtag" in client.get("/v1/youtube/search/hashtag", params={
        "hashtag": "cooking", "continuationToken": token}).json()["message"]
    assert upstream.calls == []
    upstream.answers = [appended([])]
    body = client.get("/v1/youtube/search/hashtag", params={"hashtag": "#FUNNY", "continuationToken": token}).json()
    assert body["videos"] == [] and upstream.calls[0][1]["query"] == {"continuation": "TOKEN"}


def test_hashtag_all_continues_on_the_mweb_feed(client, upstream):
    web = cached("hashtag_funny_api")
    upstream.answers = [web]
    first = client.get("/v1/youtube/search/hashtag", params={"hashtag": "#funny"}).json()
    assert upstream.calls[0][1]["query"] == {"browseId": "FEhashtag", "params": P.hashtag_params("funny", False)}
    assert len(first["videos"]) == 17 and all(v["description"] is not None or v["title"] for v in first["videos"])
    state = M._state(first["continuationToken"], "hashtag")
    assert (state["t"], state["x"], state["p"]) == ("FEhashtag", "mweb", "funny")
    mweb = cached("mweb_hashtag_funny")
    upstream.answers = [mweb]
    second = client.get("/v1/youtube/search/hashtag", params={"hashtag": "funny",
                                                              "continuationToken": first["continuationToken"]}).json()
    assert upstream.calls[1] == ("browse", {"query": {"browseId": "FEhashtag",
                                                      "params": P.hashtag_params("funny", False)}, "client": "mweb"})
    videos = second["videos"]
    assert videos and not {v["id"] for v in videos} & {v["id"] for v in first["videos"]}
    assert all(v["channel"]["id"] and v["channel"]["thumbnail"] for v in videos)
    assert {v["type"] for v in videos} == {"short", "video"}
    state = M._state(second["continuationToken"], "hashtag")
    assert state["x"] == "mweb" and state["v"] == M._visitor(mweb) and state["t"] != "FEhashtag"
    upstream.answers = [appended([])]
    third = client.get("/v1/youtube/search/hashtag", params={"hashtag": "funny",
                                                             "continuationToken": second["continuationToken"]}).json()
    assert upstream.calls[2] == ("browse", {"query": {"continuation": state["t"]}, "client": "mweb",
                                            "visitor": state["v"], "cursor": True})
    assert third == {"success": True, "credits_charged": third["credits_charged"], "videos": [],
                     "continuationToken": None}


def test_hashtag_shorts_url_selects_the_shorts_tab(client, upstream):
    upstream.answers = [cached("hashtag_funny_shorts_api")]
    body = client.get("/v1/youtube/search/hashtag",
                      params={"hashtag": "https://www.youtube.com/hashtag/funny/shorts"}).json()
    assert upstream.calls[0][1]["query"]["params"] == P.hashtag_params("funny", True)
    assert body["videos"] and all(v["type"] == "short" for v in body["videos"])
    state = M._state(body["continuationToken"], "hashtag") if body["continuationToken"] else {}
    assert state.get("t") != "FEhashtag"


def short(video_id: str) -> dict:
    return {"shortsLockupViewModel": {"onTap": {"innertubeCommand": {"reelWatchEndpoint": {"videoId": video_id}}},
                                      "overlayMetadata": {"primaryText": {"content": video_id}}}}


SERVED = [short(f"served{i:05d}") for i in range(3)]


def served_cursor(kind: str, token: str, **extra) -> str:
    """A cursor whose seen ids are SERVED's and VIDEO_RENDERER's."""
    seen = M._Seen(None)
    for i in [*(f"served{i:05d}" for i in range(3)), VIDEO_RENDERER["videoId"]]:
        seen.fresh({"id": i})
    return M._cursor(kind, token, s=seen.dump(), **extra)


def test_hashtag_reads_past_a_page_of_repeats(client, upstream):
    """The Shorts tab now and then re-sends a whole batch: that page is replaced by the next one rather than
    served empty with a continuationToken."""
    cursor = served_cursor("hashtag", "T1", p="funny", v="VISITOR1")
    upstream.answers = [appended(SERVED + [cont("T2")]), appended(SERVED[:1] + [short("fresh000001"), cont("T3")])]
    body = client.get("/v1/youtube/search/hashtag", params={"hashtag": "funny", "continuationToken": cursor}).json()
    assert upstream.calls == [
        ("browse", {"query": {"continuation": "T1"}, "client": "web", "visitor": "VISITOR1", "cursor": True}),
        ("browse", {"query": {"continuation": "T2"}, "client": "web", "visitor": "VISITOR2"})]
    assert [v["id"] for v in body["videos"]] == ["fresh000001"]  # a page with a fresh item is served as is
    state = M._state(body["continuationToken"], "hashtag")
    assert (state["t"], state["v"]) == ("T3", "VISITOR2") and not M._Seen(state).fresh({"id": "fresh000001"})


def test_hashtag_read_on_is_bounded(client, upstream):
    cursor = served_cursor("hashtag", "T0", p="funny")
    upstream.answers = [appended(SERVED + [cont(f"T{i}")]) for i in range(1, 6)]
    body = client.get("/v1/youtube/search/hashtag", params={"hashtag": "funny", "continuationToken": cursor}).json()
    assert len(upstream.calls) == 1 + M._READ_ON_PAGES  # four pages of repeats in a row: the feed came round
    assert body["videos"] == [] and body["continuationToken"] is None
    upstream.answers, upstream.calls = [appended(SERVED)], []  # the feed's end: nothing to read on to
    body = client.get("/v1/youtube/search/hashtag", params={"hashtag": "funny", "continuationToken": cursor}).json()
    assert len(upstream.calls) == 1 and body["videos"] == [] and body["continuationToken"] is None
    upstream.answers, upstream.calls = [appended([{"lockupViewModel": LOCKUP_PLAYLIST}, cont("T1")])], []
    body = client.get("/v1/youtube/search/hashtag", params={"hashtag": "funny", "continuationToken": cursor}).json()
    # nothing mapped but no repeats either: not read past, so an empty page keeps its token (as the notes say)
    assert len(upstream.calls) == 1 and body["videos"] == []
    assert M._state(body["continuationToken"], "hashtag")["t"] == "T1"


def test_search_reads_past_a_page_of_repeats(client, upstream):
    cursor = served_cursor("search", "S1", g="GB", v="VISITOR1")
    upstream.answers = [appended([{"videoRenderer": VIDEO_RENDERER}, *SERVED, cont("S2")]),
                        appended([{"lockupViewModel": LOCKUP_VIDEO}])]
    body = client.get("/v1/youtube/search", params={"query": "funny", "continuationToken": cursor}).json()
    assert upstream.calls == [
        ("search", {"query": {"continuation": "S1"}, "client": "web", "region": "GB", "visitor": "VISITOR1",
                    "cursor": True}),
        ("search", {"query": {"continuation": "S2"}, "client": "web", "region": "GB", "visitor": "VISITOR2"})]
    assert [v["id"] for v in body["videos"]] == ["plN7JMbadRg"] and body["shorts"] == []
    assert body["continuationToken"] is None


class FakeIE:
    """The YoutubeTabIE surface _api uses; _call_api fails with `status`, else both calls answer `body`
    (by default a real continuation response's shape: an append action, here of an empty last page)."""

    def __init__(self, status: int | None, body: dict | None = None):
        self.status, self.calls = status, []
        self.body = appended([]) if body is None else body

    @staticmethod
    def http_error(status: int) -> ExtractorError:
        response = Response(io.BytesIO(b""), "https://www.youtube.com/youtubei/v1/browse", {}, status=status)
        return ExtractorError(f"HTTP Error {status}", cause=HTTPError(response))

    def _get_default_ytcfg(self, client: str) -> dict:
        return {"INNERTUBE_CONTEXT": {"client": {"clientName": client}}}

    def generate_api_headers(self, **kw) -> dict:
        return {}

    def _extract_context(self, ytcfg: dict, client: str) -> dict:
        return ytcfg["INNERTUBE_CONTEXT"]

    def _call_api(self, ep, query, item_id, **kw) -> dict:
        self.calls.append(("once", kw["context"]["client"]))
        if self.status:
            raise self.http_error(self.status)
        return {**self.body, "once": True}

    def _extract_and_report_alerts(self, resp, only_once=False) -> None:
        pass

    def _extract_response(self, **kw) -> dict:
        self.calls.append(("retrying", kw["ytcfg"]["INNERTUBE_CONTEXT"]["client"]))
        return {**self.body, "retried": True}


def test_api_gives_client_tokens_one_attempt():
    ie = FakeIE(400)
    with pytest.raises(BadRequest) as exc:
        M._api(ie, {"continuation": "tampered"}, cursor=True)
    assert exc.value.code == "bad_cursor" and len(ie.calls) == 1  # no retries before failing
    ie = FakeIE(429)
    with pytest.raises(ExtractorError):
        M._api(ie, {"continuation": "x"}, cursor=True)
    assert len(ie.calls) == 1
    ie = FakeIE(503)  # transient: falls back to yt-dlp's retrying call
    assert M._api(ie, {"continuation": "x"}, client="mweb", cursor=True)["retried"]
    assert [c for c, _ in ie.calls] == ["once", "retrying"]
    ie = FakeIE(None)
    assert M._api(ie, {"continuation": "x"}, cursor=True)["once"]
    ie = FakeIE(None)
    assert M._api(ie, {"query": "x"}, "search", region="GB")["retried"]
    assert ie.calls == [("retrying", {"clientName": "web", "gl": "GB"})]


class TokenFailsIE(FakeIE):
    """YouTube answering a continuation token with HTTP 500 on every attempt; `down`: every other request too."""

    def __init__(self, down: bool):
        super().__init__(500)
        self.down = down

    def _extract_response(self, **kw) -> dict:
        self.calls.append(("retrying", kw["query"]))
        if self.down or "continuation" in kw["query"]:
            raise self.http_error(500)
        return {"contents": {}}


@pytest.mark.parametrize("down, ctx, expected", [
    (False, [MRBEAST, "MrBeast", "MrBeast"], (400, "bad_cursor")),
    (True, [MRBEAST, "MrBeast", "MrBeast"], (500, "upstream_error")),
    (False, None, (500, "upstream_error")),  # no channel to check it against
], ids=["token", "youtube_down", "no_channel"])
def test_channel_tab_token_failing_with_http_500(client, monkeypatch, down, ctx, expected):
    """yt-community-posts-tampered-token-500: YouTube answers some corrupted channel-posts tokens with HTTP 500
    ('Internal error encountered.') on every attempt, which was a 500 upstream_error. The channel's own page
    tells that from YouTube failing, which stays a 500."""
    ie = TokenFailsIE(down)

    async def run(fn):
        try:
            return fn(ie)
        except ExtractorError as exc:  # as with_ydl does
            raise translate_error(exc) from exc

    monkeypatch.setattr(M, "_run", run)
    token = M._cursor("channel-posts", "CORRUPTED", c=ctx)
    r = client.get("/v1/youtube/channel/community-posts", params={"continuationToken": token})
    assert (r.status_code, r.json()["error"]) == expected, r.json()
    assert [q for c, q in ie.calls if c == "retrying"] == [{"continuation": "CORRUPTED"},
                                                            *([{"browseId": MRBEAST}] if ctx else [])]


# What YouTube answered channel-posts / channel-playlists tokens with 'AAAA' written mid-way (trimmed): an
# HTTP 200 that reloads the tab's section with the channel's home shelves, instead of an HTTP 400.
RELOADED_HOME = {
    "continuationContents": {"sectionListContinuation": {"contents": [{"itemSectionRenderer": {"contents": [
        {"channelVideoPlayerRenderer": {"videoId": "plN7JMbadRg"}}]}}], "disablePullToRefresh": True}},
    "metadata": {"channelMetadataRenderer": {"externalId": MRBEAST}}, "microformat": {},
    "onResponseReceivedActions": [{"reloadContinuationItemsCommand": {
        "targetId": "backstage-item-section", "slot": "RELOAD_CONTINUATION_SLOT_BODY", "continuationItems": [
            {"itemSectionRenderer": {"contents": [{"shelfRenderer": {
                "title": {"runs": [{"text": "New Uploads"}]},
                "content": {"horizontalListRenderer": {"items": [{"lockupViewModel": LOCKUP_VIDEO}]}}}}]}}]}}],
}
# ... and a search token's: page 1 of the same search again (25 items and a token, all already served).
RELOADED_SEARCH = {"onResponseReceivedCommands": [
    {"reloadContinuationItemsCommand": {"targetId": "search-feed", "slot": "RELOAD_CONTINUATION_SLOT_BODY",
                                        "continuationItems": [{"twoColumnSearchResultsRenderer": {
                                            "primaryContents": {"sectionListRenderer": {"contents": [
                                                {"itemSectionRenderer": {"contents": [
                                                    {"videoRenderer": VIDEO_RENDERER}]}}, cont("NEXT")]}}}}]}},
    {"reloadContinuationItemsCommand": {"targetId": "search-feed", "slot": "RELOAD_CONTINUATION_SLOT_HEADER",
                                        "continuationItems": [{"searchHeaderRenderer": {}}]}},
    {"adsControlFlowOpportunityReceivedCommand": {}}]}


# What YouTube answers the section-level token of some playlists that hide unavailable videos (so fewer than
# totalVideos are served and the token is handed out): no items and no actions. That is the end, not a rejection.
BARE = {"responseContext": {"visitorData": "VISITOR2"}, "trackingParams": "x"}


def test_reloads_tells_reloaded_feeds_from_pages():
    assert P.reloads(RELOADED_HOME) and P.reloads(RELOADED_SEARCH)
    assert P.reloads({"continuationContents": {"sectionListContinuation": {"contents": []}}})
    for key in P.CONTINUATION_ACTIONS:
        assert P.reloads({key: [{"clickTrackingParams": "x"}, {"reloadContinuationItemsCommand": {}}]})
        assert not P.reloads({key: [{"clickTrackingParams": "x"}, {"appendContinuationItemsAction": {}}]})
    # real next pages, and empty last pages with or without an append action, are pages
    assert not P.reloads(BARE) and not P.reloads({"onResponseReceivedActions": []}) and not P.reloads(appended([]))
    for name in ("mrbeast_videos_p2", "search_lofi_p2", "hashtag_funny_shorts_p2", "pl_tf2_p2"):
        assert not P.reloads(cached(name))


@pytest.mark.parametrize("status", [None, 503])
@pytest.mark.parametrize("fallback", [
    RELOADED_HOME, RELOADED_SEARCH,
    {"continuationContents": {"sectionListContinuation": {"contents": []}}, "metadata": {}, "microformat": {}},
], ids=["home_shelves", "search_page_1", "section_list"])
def test_api_rejects_a_continuation_answer_that_reloads(status, fallback):
    """A corrupted token can get an HTTP 200 fallback instead of a 400; served as is, a channel tab read it as
    an empty last page and search as page 1 again."""
    with pytest.raises(BadRequest) as exc:
        M._api(FakeIE(status, fallback), {"continuation": "corrupted"}, cursor=True)
    assert exc.value.code == "bad_cursor"
    # first pages are not continuations: their shape is the caller's business
    assert M._api(FakeIE(None, fallback), {"browseId": "UCx"})["retried"]


@pytest.mark.parametrize("path, kind, fallback", [
    ("/v1/youtube/channel/community-posts", "channel-posts", RELOADED_HOME),
    ("/v1/youtube/channel/playlists", "channel-playlists", RELOADED_HOME),
    ("/v1/youtube/channel-videos", "channel-videos", RELOADED_HOME),
    ("/v1/youtube/search", "search", RELOADED_SEARCH),
])
def test_routes_reject_a_reloaded_feed(client, monkeypatch, path, kind, fallback):
    ie = FakeIE(None, fallback)

    async def run(fn):
        return fn(ie)

    monkeypatch.setattr(M, "_run", run)
    if kind == "search":
        params = {"query": "lofi", "continuationToken": M._cursor(kind, "CORRUPTED")}
    else:
        params = {"continuationToken": M._cursor(kind, "CORRUPTED", c=[MRBEAST, "MrBeast", "MrBeast"])}
    r = client.get(path, params=params)
    assert r.status_code == 400 and r.json()["error"] == "bad_cursor", r.json()
    ie.body = appended([])  # a real empty last page is still the end of the listing
    body = client.get(path, params=params).json()
    assert body["success"] and body["continuationToken"] is None


def test_playlist_bare_last_page_is_the_end(client, monkeypatch):
    """A playlist that hides unavailable videos serves fewer than totalVideos, so its section-level token is handed
    out; YouTube answers that with no items and no actions. It was a 400 bad_cursor at the end of the listing."""
    ie = FakeIE(None, BARE)

    async def run(fn):
        return fn(ie)

    monkeypatch.setattr(M, "_run", run)
    meta = {"title": "Old AMV's", "owner": None, "totalVideos": 26}
    cursor = M._cursor("playlist", "SECTION", p="PL19E958F7827FD94A", m=meta, n=24)
    r = client.get("/v1/youtube/playlist", params={"playlist_id": "PL19E958F7827FD94A", "continuationToken": cursor})
    body = r.json()
    assert r.status_code == 200, body
    assert body["videos"] == [] and body["continuationToken"] is None and body["totalVideos"] == 26
    assert ie.calls == [("once", {"clientName": "web"})]  # one attempt, no retries


# --- live --------------------------------------------------------------------------------

_LIVE: dict[str, dict] = {}


def upstream_down(status: int, body: dict) -> bool:
    """YouTube throttled us (429/503) or failed (a 500 upstream_error relaying its HTTP 5xx), not our bug."""
    return status in (429, 503) or (status == 500 and body.get("error") == "upstream_error"
                                    and bool(re.search(r"HTTP Error 5\d\d", body.get("message") or "")))


def get(client, path: str, status: int = 200, **params) -> dict:
    """One request per distinct URL per test session (shared IP: be gentle); skips when YouTube is down."""
    key = json.dumps([path, params], sort_keys=True)
    if key not in _LIVE:
        r = client.get(path, params=params)
        if r.status_code != status and upstream_down(r.status_code, r.json()):
            pytest.skip(f"YouTube throttled or failed ({r.status_code}): {r.json().get('message')}")
        _LIVE[key] = {"status": r.status_code, "body": r.json()}
    hit = _LIVE[key]
    assert hit["status"] == status, (path, params, hit["body"])
    return hit["body"]


def test_live_tests_skip_only_when_youtube_is_down():
    assert upstream_down(503, {"error": "upstream_blocked"}) and upstream_down(500, {
        "error": "upstream_error", "message": "Unable to download API page: HTTP Error 500: Internal Server Error"})
    assert not upstream_down(500, {"error": "server_error", "message": "HTTP Error 500"})  # our own bug
    assert not upstream_down(500, {"error": "upstream_error",
                                   "message": "YouTube answered the search with its 'Something went wrong' page"})
    assert not upstream_down(400, {"error": "bad_cursor"})


def pages(client, path: str, key: str, **params) -> tuple[list, list, dict]:
    first = get(client, path, **params)
    assert first["continuationToken"], "first page should have a next page"
    keep = {k: v for k, v in params.items() if k in ("query", "hashtag", "playlist_id")}
    second = get(client, path, continuationToken=first["continuationToken"], **keep)
    ids1, ids2 = [x["id"] for x in first[key]], [x["id"] for x in second[key]]
    assert ids2 and ids1 != ids2
    assert len(set(ids1 + ids2)) == len(ids1) + len(ids2), "pages overlap or repeat items"
    return ids1, ids2, second


@pytest.mark.live
def test_channel_live(client):
    body = get(client, "/v1/youtube/channel", handle="MrBeast")
    assert_conforms(body, "/v1/youtube/channel", min_coverage=0.9)
    assert body["channelId"] == MRBEAST and body["name"] == "MrBeast"
    assert body["channel"].endswith("/@MrBeast") and body["subscriberCount"] > 100_000_000
    assert body["subscriberCountText"].endswith("subscribers") and body["joinedDateText"].startswith("Joined")
    assert body["email"] is None and isinstance(body["links"], list)
    by_id = get(client, "/v1/youtube/channel", channelId=MRBEAST)
    assert by_id["channelId"] == MRBEAST and by_id["name"] == "MrBeast"
    vanity = get(client, "/v1/youtube/channel", url="https://www.youtube.com/LinusTechTips")  # legacy /<name> URL
    assert vanity["channelId"].startswith("UC") and "Linus" in vanity["name"]


@pytest.mark.live
def test_channel_videos_live(client):
    body = get(client, "/v1/youtube/channel-videos", handle="MrBeast")
    assert_conforms(body, "/v1/youtube/channel-videos", min_coverage=0.9)
    videos = body["videos"]
    assert len(videos) >= 20 and all(v["type"] == "video" and len(v["id"]) == 11 for v in videos)
    assert all(v["channel"] == {"title": "MrBeast", "thumbnail": None} for v in videos)
    assert all(v["url"] == f"https://www.youtube.com/watch?v={v['id']}" for v in videos)
    assert sum(isinstance(v["viewCountInt"], int) and v["viewCountInt"] > 0 for v in videos) >= len(videos) - 2
    assert sum(bool(re.fullmatch(r"[\d,]+ views", v["viewCountText"] or "")) for v in videos) >= len(videos) - 2
    assert all(v["lengthSeconds"] for v in videos[1:])


@pytest.mark.live
def test_channel_videos_popular_pages_live(client):
    _, _, second = pages(client, "/v1/youtube/channel-videos", "videos", handle="MrBeast", sort="popular")
    first = get(client, "/v1/youtube/channel-videos", handle="MrBeast", sort="popular")
    top = max(v["viewCountInt"] or 0 for v in first["videos"])
    assert top > 500_000_000 and top >= max(v["viewCountInt"] or 0 for v in second["videos"])


@pytest.mark.live
def test_channel_shorts_live(client):
    body = get(client, "/v1/youtube/channel/shorts", handle="MrBeast")
    assert_conforms(body, "/v1/youtube/channel/shorts", min_coverage=0.45)
    shorts = body["shorts"]
    assert len(shorts) >= 20 and all(s["type"] == "short" and s["title"] and s["thumbnail"] for s in shorts)
    assert body["videos"] == body["lives"] == body["shelves"] == []
    pages(client, "/v1/youtube/channel/shorts", "shorts", handle="MrBeast")


@pytest.mark.live
def test_channel_lives_live(client):
    body = get(client, "/v1/youtube/channel/lives", channelId=NASA)
    assert_conforms(body, "/v1/youtube/channel/lives", min_coverage=0.9)
    lives = body["lives"]
    assert len(lives) >= 10 and all(x["type"] == "live" for x in lives)
    assert all(x["channel"]["id"] == NASA and x["channel"]["handle"] == "NASA" for x in lives)
    assert any(x["lengthSeconds"] for x in lives)
    pages(client, "/v1/youtube/channel/lives", "lives", channelId=NASA)


@pytest.mark.live
def test_channel_playlists_live(client):
    body = get(client, "/v1/youtube/channel/playlists", handle="NASA")
    assert_conforms(body, "/v1/youtube/channel/playlists", min_coverage=0.9)
    pls = body["playlists"]
    assert len(pls) >= 10 and all(p["type"] == "playlist" and p["playlistUrl"].endswith(p["id"]) for p in pls)
    assert all(p["channel"]["id"] == NASA for p in pls)
    assert any(isinstance(p["videoCount"], int) and p["videoCount"] > 0 for p in pls)
    pages(client, "/v1/youtube/channel/playlists", "playlists", handle="NASA")


@pytest.mark.live
def test_channel_playlists_shows_live(client):
    pls = get(client, "/v1/youtube/channel/playlists", handle="mkbhd")["playlists"]
    assert len(pls) >= 15 and all(p["channel"]["handle"] == "mkbhd" for p in pls)
    assert any(p["videoId"] is None and p["videoCount"] for p in pls)  # shows: no first video, 'N episodes'


@pytest.mark.live
def test_channel_community_posts_live(client):
    body = get(client, "/v1/youtube/channel/community-posts", handle="MrBeast")
    assert_conforms(body, "/v1/youtube/channel/community-posts", min_coverage=0.5)
    posts = body["posts"]
    assert posts and all(p["url"].endswith(p["id"]) and p["channel"]["id"] == MRBEAST for p in posts)
    assert all(p["content"] or p["images"] or p["video"] for p in posts)
    assert any(isinstance(p["likeCount"], int) for p in posts)
    pages(client, "/v1/youtube/channel/community-posts", "posts", handle="MrBeast")


@pytest.mark.live
def test_playlist_live(client):
    body = get(client, "/v1/youtube/playlist", playlist_id=TF2)
    assert_conforms(body, "/v1/youtube/playlist", min_coverage=0.9)
    assert body["title"] == "Team Fortress 2 [2010 Version]" and body["totalVideos"] == 29
    assert body["owner"]["name"] == "Wickman Wish" and body["owner"]["id"].startswith("UC")
    assert len(body["videos"]) == 29 and body["continuationToken"] is None
    assert all(v["lengthSeconds"] and v["channel"]["title"] == "Wickman Wish" for v in body["videos"])


@pytest.mark.live
def test_playlist_pages_live(client):
    first = get(client, "/v1/youtube/playlist", playlist_id=SCIENCECASTS)
    second = get(client, "/v1/youtube/playlist", playlist_id=SCIENCECASTS, continuationToken=first["continuationToken"])
    ids1, ids2 = [v["id"] for v in first["videos"]], [v["id"] for v in second["videos"]]
    # the playlist itself lists one video twice, so pages are compared as sets
    assert len(ids1) == 100 and len(set(ids2) - set(ids1)) >= 90 and second["continuationToken"]


@pytest.mark.live
def test_shorts_playlist_live(client):
    body = get(client, "/v1/youtube/playlist", playlist_id="UUSH" + MRBEAST[2:])
    assert body["owner"]["id"] == MRBEAST and len(body["videos"]) >= 50
    assert all(v["title"] and v["channel"]["title"] == "MrBeast" for v in body["videos"])


@pytest.mark.live
def test_mix_and_tampered_cursor_are_400_live(client):
    assert get(client, "/v1/youtube/playlist", 400, playlist_id="RDdQw4w9WgXcQ")["error"] == "unviewable_playlist"
    tampered = encode_cursor({"k": "channel-lives", "t": "garbage"})
    assert get(client, "/v1/youtube/channel/lives", 400, continuationToken=tampered)["error"] == "bad_cursor"


@pytest.mark.live
def test_search_live(client):
    body = get(client, "/v1/youtube/search", query="lofi")
    assert_conforms(body, "/v1/youtube/search", min_coverage=0.5)
    assert len(body["videos"]) >= 5 and all(v["type"] == "video" and v["channel"]["id"] for v in body["videos"])
    assert any(isinstance(v["viewCountInt"], int) and v["viewCountInt"] > 1000 for v in body["videos"])
    assert any("lofi" in (v["title"] or "").lower() for v in body["videos"])
    pages(client, "/v1/youtube/search", "videos", query="lofi")


@pytest.mark.live
def test_search_shorts_pages_live(client):
    body = get(client, "/v1/youtube/search", query="running", type="shorts")
    assert len(body["shorts"]) >= 20 and body["videos"] == [] and body["shelves"] == []
    assert all(s["type"] == "short" and s["channel"]["id"] and s["channel"]["handle"] for s in body["shorts"])
    assert any(s["lengthSeconds"] and s["publishedTimeText"] for s in body["shorts"])
    pages(client, "/v1/youtube/search", "shorts", query="running", type="shorts")


@pytest.mark.live
def test_hashtag_live(client):
    body = get(client, "/v1/youtube/search/hashtag", hashtag="funny")
    assert_conforms(body, "/v1/youtube/search/hashtag", min_coverage=0.9)
    videos = body["videos"]
    assert len(videos) >= 5 and all(v["type"] in ("video", "short") and v["channel"]["id"] for v in videos)
    _, _, second = pages(client, "/v1/youtube/search/hashtag", "videos", hashtag="funny")  # continues on mweb
    assert all(v["channel"]["id"] for v in second["videos"])


@pytest.mark.live
def test_hashtag_shorts_pages_live(client):
    body = get(client, "/v1/youtube/search/hashtag", hashtag="funny", type="shorts")
    assert_conforms(body, "/v1/youtube/search/hashtag", min_coverage=0.5)
    assert all(v["type"] == "short" for v in body["videos"])
    pages(client, "/v1/youtube/search/hashtag", "videos", hashtag="funny", type="shorts")


@pytest.mark.live
@pytest.mark.parametrize("path, params", [
    ("/v1/youtube/channel", {"handle": "thishandledoesnotexist987654321zz"}),
    ("/v1/youtube/channel-videos", {"channelId": "UCzzzzzzzzzzzzzzzzzzzzzz"}),
    ("/v1/youtube/playlist", {"playlist_id": "PLzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"}),
])
def test_nonexistent_is_404_live(client, path, params):
    body = get(client, path, status=404, **params)
    assert body["success"] is False and body["error"] == "not_found"
