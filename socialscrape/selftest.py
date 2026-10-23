"""Endpoint tests: a preset request for every catalog endpoint, which the dashboard runs to show what works now.

Each preset asks for public content that should stay up (mostly the live e2e tests' fixtures) and names what a passing
answer must hold. The dashboard (/app/tests, static/selftest.js) sends the requests itself, with the member's session,
one at a time per platform, and judges each answer:

- passed: the preset's status (200), success: true, and a value at every `expect` key path;
- not supported: a route this server leaves out answers 501 not_supported;
- needs setup: a feature this server has switched off answers 501 (AI transcription);
- failed: anything else, with the error the API gave.

Key paths use the contracts' syntax (tests/contract.py): `videos[].id` is the id of any element of videos, and a value
is there unless it is null, "", [] or {}. A `derive` param is read from another endpoint's passing answer (a
continuation token, say), so that endpoint runs first.

Unless the member allows it, test requests carry `x-socialscrape-fallback: off`: they measure this server alone and
spend no ScrapeCreators credits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request

from . import fallback
from .portal import _require
from .registry import coverage


@dataclass(frozen=True)
class Preset:
    params: dict[str, str]
    expect: tuple[str, ...] = ()
    # param: (source endpoint, key path in its answer), on the same platform
    derive: dict[str, tuple[str, str]] = field(default_factory=dict)
    note: str = ""


# Fixtures of the live e2e tests (tests/test_e2e_*.py), where most of them are described.
YT_FIRST = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", English captions
YT_SPONSORED = "https://www.youtube.com/watch?v=AVO0ifle-OU"  # paid-promotion badge, a named sponsor
YT_SHORT = "https://www.youtube.com/watch?v=AwMy11R3au0"  # a Short whose comments have replies
YT_TEXT_POST = "https://www.youtube.com/post/Ugkx3w8sfDJf_NDdhUAtu7MTLX3d_Dt1V9UK"  # MrBeast
NASA_CHANNEL = "UCLA_DiR1FfKNvjuUpBHmylQ"
TF2_PLAYLIST = "PLBB231211A4F62143"  # "Team Fortress 2 [2010 Version]", one page
STOOL = "stoolpresidente"
STOOL_VIDEO = "https://www.tiktok.com/@stoolpresidente/video/7499229683859426602"
KHABY_VIDEO = "https://www.tiktok.com/@khaby.lame/video/7683944847840922911"  # Spanish and English captions
TT_COLLECTION = "https://www.tiktok.com/@kibblemaster808/collection/Want-to-go-7665668414573546258"
ENDED_ROOM, ENDED_ROOM_UID = "7523685855395842871", "6742945285876515845"
LICENSED_SOUND = "6956990112127585029"  # "Build a B*tch", Bella Poarch
SMALL_SOUND = "6689804660171082501"  # 10 videos over 3 pages
JANE, JANE_ID = "jane", "21393171"
JANE_REEL = "https://www.instagram.com/reel/DLDXI0fylTC/"  # 85 s of speech

PRESETS: dict[str, Preset] = {
    # Instagram
    "/v1/instagram/audio/reels": Preset({"audio_id": "3011705342342322"}, ("reels[].id",)),
    "/v1/instagram/basic-profile": Preset({"userId": JANE_ID}, ("username", "follower_count")),
    "/v1/instagram/post": Preset({"url": JANE_REEL}, ("data.xdt_shortcode_media.shortcode",
                                                      "data.xdt_shortcode_media.owner.username",
                                                      "data.xdt_shortcode_media.video_url"),
                                 note="A reel by @jane."),
    "/v1/instagram/post/comment/replies": Preset({"url": JANE_REEL, "comment_id": "18045871199120344"},
                                                 ("comments[].id",)),
    "/v1/instagram/profile": Preset({"handle": JANE}, ("data.user.username", "data.user.edge_followed_by.count",
                                                       "data.user.edge_owner_to_timeline_media.edges[].node.id")),
    "/v1/instagram/profile/post-count": Preset({"handle": JANE}, ("handle", "media_count")),
    "/v1/instagram/reels/trending": Preset({}, ("reels[].id",)),
    "/v1/instagram/search": Preset({"query": "cats"}, ("data.users[].username",)),
    "/v1/instagram/search/hashtag": Preset({"hashtag": "cats"}, ("posts[].id", "posts[].shortcode")),
    "/v1/instagram/search/popular": Preset({"query": "cats"}, ("title", "posts[].shortcode")),
    "/v1/instagram/search/profiles": Preset({"query": "nasa"}, ("profiles[].username",)),
    "/v1/instagram/user/embed": Preset({"handle": "nasa"}, ("html",)),
    "/v1/instagram/user/highlight/detail": Preset({}, ("id", "items[].id", "items[].code"),
                                                  {"id": ("/v1/instagram/user/highlights", "highlights[].id")},
                                                  note="The first highlight @jane's profile lists."),
    "/v1/instagram/user/highlights": Preset({"handle": JANE}, ("highlights[].id", "highlights[].title")),
    "/v1/instagram/user/reels": Preset({"handle": JANE}, ("items[].id", "items[].code")),
    "/v1/instagram/user/tagged-posts": Preset({"user_id": JANE_ID}, ("posts[].code",)),
    "/v2/instagram/media/transcript": Preset({"url": JANE_REEL}, ("transcripts[].text",),
                                             note="Needs AI transcription (SOCIALSCRAPE_TRANSCRIBE)."),
    "/v2/instagram/post/comments": Preset({"url": JANE_REEL}, ("comments[].id", "comments[].text")),
    "/v2/instagram/reels/search": Preset({"query": "cats"}, ("reels[].id", "reels[].shortcode")),
    "/v2/instagram/user/posts": Preset({"handle": JANE}, ("items[].id", "items[].code")),
    # TikTok
    "/v1/tiktok/ad-library/ad": Preset({"ad_id": "7239990052547747841"}, ("id",)),
    "/v1/tiktok/ad-library/search": Preset({"query": "nike"}, ("ads[].id",)),
    "/v1/tiktok/collection/videos": Preset({"url": TT_COLLECTION}, ("collection_id", "videos[].id"),
                                           note="A small public collection."),
    "/v1/tiktok/creators/popular": Preset({"page": "1", "creatorCountry": "US"}, ("creators[].user_id",)),
    "/v1/tiktok/get-trending-feed": Preset({"region": "US"}, ("aweme_list[].aweme_id",)),
    "/v1/tiktok/live": Preset({"room_id": ENDED_ROOM, "user_id": ENDED_ROOM_UID}, ("room_id", "owner.id", "status"),
                              note="A live room that has ended, so the test doesn't need anyone to be live."),
    "/v1/tiktok/product": Preset({"url": "https://shop.tiktok.com/view/product/1729382921546421731",
                                  "region": "US"}, ("product_info.product_id",)),
    "/v1/tiktok/profile": Preset({"handle": STOOL}, ("user.uniqueId", "stats.followerCount")),
    "/v1/tiktok/profile/region": Preset({"handle": STOOL}, ("handle", "region")),
    "/v1/tiktok/search/hashtag": Preset({"hashtag": "travel", "region": "US"}, ("aweme_list[].aweme_id",)),
    "/v1/tiktok/search/keyword": Preset({"query": "travel", "region": "US"}, ("search_item_list[]",)),
    "/v1/tiktok/search/suggestions": Preset({"query": "travel", "region": "US"}, ("suggestions[]",)),
    "/v1/tiktok/search/top": Preset({"query": "travel", "region": "US"}, ("items[]",)),
    "/v1/tiktok/search/users": Preset({"query": STOOL}, ("users[]",)),
    "/v1/tiktok/shop/product/reviews": Preset({"product_id": "1729382921546421731", "region": "US"},
                                              ("product_reviews[]",)),
    "/v1/tiktok/shop/products": Preset({"url": "https://www.tiktok.com/@tiktokshop", "region": "US"},
                                       ("products[].product_id",)),
    "/v1/tiktok/shop/search": Preset({"query": "phone case", "region": "US"}, ("products[].product_id",)),
    "/v1/tiktok/song": Preset({"clipId": LICENSED_SOUND}, ("music_info.id_str", "music_info.title"),
                              note="“Build a B*tch” by Bella Poarch."),
    "/v1/tiktok/song/videos": Preset({"clipId": SMALL_SOUND}, ("aweme_list[].aweme_id",),
                                     note="A small original sound with a handful of videos."),
    "/v1/tiktok/user/audience": Preset({"handle": "khaby.lame"}, ("audienceLocations[]",)),
    "/v1/tiktok/user/followers": Preset({"handle": STOOL}, ("followers[]",)),
    "/v1/tiktok/user/following": Preset({"handle": STOOL}, ("followings[]",)),
    "/v1/tiktok/user/live": Preset({"handle": STOOL}, ("is_live", "liveRoomUserInfo.uniqueId"),
                                   note="Passes whether or not the account is live now."),
    "/v1/tiktok/user/showcase": Preset({"handle": "khaby.lame", "region": "US"}, ("products[].product_id",)),
    "/v1/tiktok/video/comment/replies": Preset({"comment_id": "7499562840200344363", "url": STOOL_VIDEO},
                                               ("comments[].text",)),
    "/v1/tiktok/video/comments": Preset({"url": STOOL_VIDEO}, ("comments[].text",)),
    "/v1/tiktok/video/transcript": Preset({"url": KHABY_VIDEO, "language": "en"}, ("id", "transcript"),
                                          note="Native English captions on a Khaby Lame video."),
    "/v2/tiktok/video": Preset({"url": STOOL_VIDEO}, ("aweme_detail.aweme_id", "aweme_detail.author.unique_id",
                                                      "aweme_detail.statistics.play_count")),
    "/v3/tiktok/profile/videos": Preset({"handle": STOOL}, ("aweme_list[].aweme_id", "max_cursor")),
    # YouTube
    "/v1/youtube/channel": Preset({"handle": "MrBeast"}, ("channelId", "name", "subscriberCount")),
    "/v1/youtube/channel-videos": Preset({"handle": "MrBeast"}, ("videos[].id", "videos[].title")),
    "/v1/youtube/channel/community-posts": Preset({"handle": "MrBeast"}, ("posts[].id", "posts[].content")),
    "/v1/youtube/channel/lives": Preset({"channelId": NASA_CHANNEL}, ("lives[].id", "lives[].title"),
                                        note="NASA's past and upcoming streams."),
    "/v1/youtube/channel/playlists": Preset({"handle": "NASA"}, ("playlists[].id", "playlists[].title")),
    "/v1/youtube/channel/shorts": Preset({"handle": "MrBeast"}, ("shorts[].id", "shorts[].title")),
    "/v1/youtube/community-post": Preset({"url": YT_TEXT_POST}, ("id", "channel.id", "content"),
                                         note="A MrBeast text post."),
    "/v1/youtube/playlist": Preset({"playlist_id": TF2_PLAYLIST}, ("title", "videos[].id"),
                                   note="“Team Fortress 2 [2010 Version]”."),
    "/v1/youtube/search": Preset({"query": "lofi"}, ("videos[].id", "videos[].title")),
    "/v1/youtube/search/hashtag": Preset({"hashtag": "funny"}, ("videos[].id", "videos[].title")),
    "/v1/youtube/search/typeahead": Preset({"query": "mrbeast"}, ("data.suggestions[].text",)),
    "/v1/youtube/shorts/trending": Preset({}, ("shorts[].id",)),
    "/v1/youtube/video": Preset({"url": YT_FIRST}, ("id", "title", "channel.id"),
                                note="“Me at the zoo”, YouTube's first video."),
    "/v1/youtube/video/comment/replies": Preset({}, ("comments[].id", "comments[].content"),
                                                {"continuationToken": ("/v1/youtube/video/comments",
                                                                       "comments[].repliesContinuationToken")},
                                                note="Replies to the first comment that has some."),
    "/v1/youtube/video/comments": Preset({"url": YT_SHORT}, ("comments[].id", "comments[].content"),
                                         note="A Short whose comments have replies."),
    "/v1/youtube/video/sponsors": Preset({"url": YT_SPONSORED}, ("suspectedSponsors[].name",
                                                                 "sponsorDetection.status"),
                                         note="A paid promotion with a named sponsor."),
    "/v1/youtube/video/transcript": Preset({"url": YT_FIRST}, ("videoId", "transcript[].text"),
                                           note="“Me at the zoo”, which has English captions."),
}


def tests() -> list[dict[str, Any]]:
    """Every catalog endpoint, in catalog order, with its preset."""
    out = []
    for row in coverage():
        preset = PRESETS[row["path"]]
        implemented = row["status"] == "implemented"
        out.append({
            "path": row["path"], "platform": row["platform"], "summary": row["summary"], "implemented": implemented,
            "reason": None if implemented else row["notes"], "params": preset.params, "expect": list(preset.expect),
            "derive": [{"param": name, "source": source, "pick": pick}
                       for name, (source, pick) in preset.derive.items()],
            "note": preset.note,
        })
    return out


def install_routes(app) -> None:
    """GET /portal/api/endpoint-tests for any signed-in member: the presets, and what falls back to ScrapeCreators
    (null when nothing does)."""
    router = APIRouter(include_in_schema=False)

    @router.get("/portal/api/endpoint-tests")
    async def endpoint_tests(request: Request):
        _require(request)
        return {"fallback": fallback.describe(app), "tests": tests()}

    app.include_router(router)
