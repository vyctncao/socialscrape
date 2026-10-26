import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from yt_dlp.extractor.tiktok import TikTokBaseIE
from yt_dlp.networking import Request, Response
from yt_dlp.networking.exceptions import HTTPError, ProxyError
from yt_dlp.utils import ExtractorError

from socialscrape.catalog import CATALOG
from socialscrape.errors import BadRequest, Forbidden, NotFound, UpstreamBlocked, UpstreamError
from socialscrape.platforms import _tiktok_map as M
from socialscrape.platforms import tiktok, tiktok_ads, tiktok_official, tiktok_shop
from socialscrape.subtitles import Segment
from tests.contract import assert_conforms
from tests.e2e import skip_if_blocked

STOOL_VIDEO = "https://www.tiktok.com/@stoolpresidente/video/7499229683859426602"
SCOUT_VIDEO = "https://www.tiktok.com/@scout2015/video/6718335390845095173"
PHOTO_POST = "https://www.tiktok.com/@chillezy/photo/7240568259186019630"
MISSING_VIDEO = "https://www.tiktok.com/@stoolpresidente/video/7499229683859426001"
STOOL_UID = "6659752019493208069"
LIVE_HANDLE = "weathernewslive"  # 24/7 weather stream
LIVE_UID = "6621496731283095554"
CLIP_ID = "6956990112127585029"
MISSING_USER = "zzqx_no_such_user_8472619"
COLLECTION = "https://www.tiktok.com/@kibblemaster808/collection/Want-to-go-7665668414573546258"
MISSING_COLLECTION = "https://www.tiktok.com/@kibblemaster808/collection/x-7000000000000000001"

TT_PATHS = [e["path"] for e in CATALOG if e["path"].split("/")[2] == "tiktok"]

# Recorded TikTok answers (tests/fixtures/tiktok): item_list.json is @stoolpresidente's creator item_list first page
# (3 of its 15 posts, video trimmed), embed_profile.html its /embed/@stoolpresidente page (the Frontity state, 3 of
# 10 posts), both 2026-10-24. waf_challenge.html is SlardarWAF's "Please wait..." page as yt-dlp's solver reads it
# (built: a server IP's copy wasn't recorded), solvable. _BLOCK_PAGE is Akamai's denial page, which TikTok's CDN
# (X-Akamai-Request-ID) sends a blocked client.
FIXTURES = Path(__file__).parent / "fixtures" / "tiktok"
_FEED = json.loads((FIXTURES / "item_list.json").read_text())
_EMBED_PAGE = (FIXTURES / "embed_profile.html").read_text()
_WAF_PAGE = (FIXTURES / "waf_challenge.html").read_text()
_BLOCK_PAGE = ('<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n<H1>Access Denied</H1>\n \nYou don\'t have '
               'permission to access "http&#58;&#47;&#47;www&#46;tiktok&#46;com&#47;api&#47;creator&#47;item&#95;'
               'list&#47;" on this server.<P>\nReference&#32;&#35;18&#46;5f3c1002&#46;1792816100&#46;2c1b6a3\n'
               '</BODY>\n</HTML>\n')


def ok(client, path, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    return r.json()


# --- offline: routing and parameter validation ---------------------------------

def test_every_tiktok_path_is_implemented_or_explained(client):
    rows = {r["path"]: r for r in client.get("/meta/coverage").json()["endpoints"]}
    routed = set(client.app.openapi()["paths"])
    assert len(TT_PATHS) == 29
    for path in TT_PATHS:
        row = rows[path]
        assert path in routed, path
        assert row["notes"], path
        if row["status"] == "not_supported":
            assert path in (tiktok.UNSUPPORTED | tiktok_ads.UNSUPPORTED | tiktok_official.UNSUPPORTED
                            | tiktok_shop.UNSUPPORTED)
            r = client.get(path)
            assert r.status_code == 501 and r.json()["error"] == "not_supported", path
        else:
            assert path not in (tiktok.UNSUPPORTED | tiktok_ads.UNSUPPORTED) and row["source"], path


@pytest.mark.parametrize("path", ["/v2/tiktok/video", "/v1/tiktok/video/transcript", "/v1/tiktok/profile",
                                  "/v3/tiktok/profile/videos", "/v1/tiktok/user/live", "/v1/tiktok/live",
                                  "/v1/tiktok/song", "/v1/tiktok/song/videos", "/v1/tiktok/collection/videos",
                                  "/v1/tiktok/profile/region"])
def test_missing_params_are_400(client, path):
    r = client.get(path)
    assert r.status_code == 400 and r.json()["success"] is False


@pytest.mark.parametrize("path, params", [
    ("/v2/tiktok/video", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
    ("/v2/tiktok/video", {"url": "https://www.instagram.com/p/C0xyz/"}),
    ("/v2/tiktok/video", {"url": "https://www.tiktok.com/@stoolpresidente"}),
    ("/v1/tiktok/video/transcript", {"url": "https://vimeo.com/76979871"}),
    ("/v1/tiktok/video/transcript", {"url": STOOL_VIDEO, "language": "english!"}),
    ("/v1/tiktok/profile", {"handle": "bad handle/../x"}),
    ("/v1/tiktok/profile", {"user_id": "abc"}),
    ("/v3/tiktok/profile/videos", {"handle": "stoolpresidente", "max_cursor": "next"}),
    ("/v3/tiktok/profile/videos", {"handle": "stoolpresidente", "region": "USA"}),
    ("/v3/tiktok/profile/videos", {"handle": "stoolpresidente", "sort_by": "oldest"}),
    ("/v1/tiktok/user/live", {"handle": "https://www.youtube.com/@x"}),
    ("/v1/tiktok/live", {"room_id": "abc", "user_id": "1"}),
    ("/v1/tiktok/song", {"clipId": "Build-a-Btch"}),
    ("/v1/tiktok/song", {"clipId": "https://www.tiktok.com/music/Build-a-Btch"}),
    ("/v1/tiktok/song/videos", {"clipId": STOOL_VIDEO}),
    ("/v1/tiktok/song/videos", {"clipId": CLIP_ID, "cursor": "-5"}),
    # non-ASCII digits match str.isdigit() and \d (int("\u00b2") used to raise -> 500): must be a clean 400
    ("/v1/tiktok/song/videos", {"clipId": CLIP_ID, "cursor": "\u00b2"}),
    ("/v1/tiktok/song", {"clipId": "\u00b2"}),
    ("/v1/tiktok/profile", {"user_id": "\u0661\u0662\u0663"}),
    ("/v1/tiktok/live", {"room_id": "\uff11", "user_id": "1"}),
    ("/v3/tiktok/profile/videos", {"handle": "stoolpresidente", "max_cursor": "\u00b2"}),
    ("/v3/tiktok/profile/videos", {"handle": "stoolpresidente", "max_cursor": "1790860188"}),  # seconds, not ms
    ("/v1/tiktok/collection/videos", {"url": STOOL_VIDEO}),
    ("/v1/tiktok/collection/videos", {"url": COLLECTION, "cursor": "next"}),
    ("/v1/tiktok/profile/region", {"handle": "bad handle"}),
])
def test_bad_input_is_400(client, path, params):
    r = client.get(path, params=params)
    assert r.status_code == 400, r.text[:200]
    assert r.json()["success"] is False


def test_popular_sort_is_not_supported(client):
    r = client.get("/v3/tiktok/profile/videos", params={"handle": "stoolpresidente", "sort_by": "popular"})
    assert r.status_code == 501 and r.json()["error"] == "not_supported"


# --- offline: pure mappers -------------------------------------------------------

def test_post_url_parsing():
    assert M.post_url(STOOL_VIDEO) == ("stoolpresidente", "7499229683859426602")
    assert M.post_url("https://www.tiktok.com/@a.b_c/photo/7300000000000000001?lang=en") == ("a.b_c", "7300000000000000001")
    assert M.post_url("https://www.tiktok.com/embed/v2/7300000000000000001") == (None, "7300000000000000001")
    assert M.post_url("https://m.tiktok.com/v/7300000000000000001.html") == (None, "7300000000000000001")
    # TikTok's embed player, as its developer docs give the iframe src
    assert M.post_url("https://www.tiktok.com/player/v1/7300000000000000001?&music_info=1&description=1") == \
        (None, "7300000000000000001")
    assert M.post_url("https://www.tiktok.com/@stoolpresidente") is None
    assert M.post_url("https://www.youtube.com/watch?v=x") is None
    assert M.is_short_url("https://vm.tiktok.com/ZMabc123/") and M.is_short_url("https://www.tiktok.com/t/ZTabc/")
    assert not M.is_short_url("https://www.tiktok.com/@x")
    assert M.web_url(None, "1") == "https://www.tiktok.com/@_/video/1"


def test_url_variants():
    assert M.with_scheme(" www.tiktok.com/@x ") == "https://www.tiktok.com/@x"
    assert M.with_scheme("http://tiktok.com/@x") == "http://tiktok.com/@x"
    assert M.post_url("www.tiktok.com/@stoolpresidente/video/7499229683859426602") == \
        ("stoolpresidente", "7499229683859426602")
    assert M.post_url("HTTPS://WWW.TIKTOK.COM/@Stool/VIDEO/7499229683859426602") == ("Stool", "7499229683859426602")
    assert M.post_url("https://www.tiktok.com/@x/video/\uff17\uff14") is None  # fullwidth digits
    for tail in ("abc", "abc?lang=en", ".0", "-7499229683859426603", "_1", "%20"):  # not an id that ends there
        assert M.post_url(f"https://www.tiktok.com/@x/video/7499229683859426602{tail}") is None, tail
    for tail in ("/", "?lang=en", "/?lang=en#top", "#top", "?"):
        assert M.post_url(f"https://www.tiktok.com/@x/video/7499229683859426602{tail}") == \
            ("x", "7499229683859426602"), tail
    assert M.post_url("https://www.tiktokv.com/share/video/7668090902816017671/") == (None, "7668090902816017671")
    assert M.is_short_url("vm.tiktok.com/ZMabc123/") and M.is_short_url("HTTPS://VT.TIKTOK.COM/ZSabc/")
    assert M.collection_id(COLLECTION) == "7665668414573546258"
    assert M.collection_id("tiktok.com/@a.b/collection/7665668414573546258?lang=en") == "7665668414573546258"
    assert M.collection_id("https://www.tiktok.com/@a/collection/name") is None
    assert M.collection_id(STOOL_VIDEO) is None
    # a sound's page, as TikTokSoundIE's tests give it, with whatever title the address bar shows
    assert M.music_id(f"https://www.tiktok.com/music/Build-a-Btch-{CLIP_ID}?lang=en") == CLIP_ID
    assert M.music_id(f" m.tiktok.com/music/original-sound-2024-{CLIP_ID}/ ") == CLIP_ID
    assert M.music_id(f"HTTPS://WWW.TIKTOK.COM/MUSIC/{CLIP_ID}#top") == CLIP_ID
    assert M.music_id(f"https://www.tiktok.com/music/%E5%8E%9F%E5%A3%B0-{CLIP_ID}") == CLIP_ID
    for url in ("https://www.tiktok.com/music/Build-a-Btch", f"https://www.tiktok.com/music/x-{CLIP_ID}/videos",
                f"https://www.tiktok.com/tag/x-{CLIP_ID}", f"https://vm.tiktok.com/music/x-{CLIP_ID}",
                "https://www.tiktok.com/music/x-\uff16\uff19", STOOL_VIDEO, COLLECTION):
        assert M.music_id(url) is None, url


def test_clean_handle():
    assert M.clean_handle("@stoolpresidente") == "stoolpresidente"
    assert M.clean_handle("https://www.tiktok.com/@scout2015?lang=en") == "scout2015"
    assert M.clean_handle("https://www.tiktok.com/@weathernewslive/live") == "weathernewslive"
    assert M.clean_handle("www.tiktok.com/@scout2015") == "scout2015"
    assert M.clean_handle("https://www.tiktok.com/@scout2015/video/1") is None
    assert M.clean_handle("a.b-c_1") == "a.b-c_1"
    assert M.clean_handle("no spaces") is None and M.clean_handle("") is None
    # the full-width '@' of Japanese and Chinese keyboards (and the small one) made it no handle
    assert M.clean_handle("＠stoolpresidente") == M.clean_handle("﹫stoolpresidente") == "stoolpresidente"
    assert M.clean_handle("＠") is None


def test_image_and_codec_helpers():
    url = "https://p16-sign.tiktokcdn-us.com/tos-useast5-p-0068-tx/abcDEF123~tplv-tiktokx-origin.image?x=1"
    assert M.image(url) == {"uri": "tos-useast5-p-0068-tx/abcDEF123", "url_list": [url]}
    assert M.image("https://example.com/plain.jpg") == {"url_list": ["https://example.com/plain.jpg"]}
    assert M.image("") is None
    assert M.codec_flag("v12044gd0000abc_h264_540p_1015745") == 0
    assert M.codec_flag("v12044gd0000abc_bytevc1_1080p_1500000") == 1
    assert M.codec_flag(None) is None
    assert M.to_int("12") == 12 and M.to_int(True) == 1 and M.to_int("") is None and M.to_int("x") is None


def _web_item(**extra):
    item = {
        "id": "7300000000000000001",
        "desc": "hello #fyp",
        "createTime": "1700000000",
        "textLanguage": "en",
        "locationCreated": "US",
        "isAd": False,
        "author": {"id": "42", "uniqueId": "someone", "nickname": "Some One", "secUid": "MS4w", "secret": False,
                   "avatarThumb": "https://p16.tiktokcdn.com/tos-maliva-avt-0068/abc~tplv-100.jpeg",
                   "commentSetting": 0, "roomId": "", "createTime": 1550000000},
        "authorStats": {"followerCount": 10, "followingCount": 2, "heartCount": 99, "videoCount": 5, "diggCount": 1},
        "stats": {"playCount": 100, "diggCount": 7, "commentCount": 3, "shareCount": 2, "collectCount": 1},
        "statsV2": {"playCount": "123456789012", "repostCount": "0"},
        "music": {"id": "555", "title": "orig sound", "authorName": "someone", "original": True, "duration": 15,
                  "playUrl": "https://sf16.tiktokcdn.com/obj/music.mp3", "preciseDuration": {"preciseVideoDuration": 14.861}},
        "video": {
            "duration": 15, "width": 576, "height": 1024, "ratio": "540p",
            "cover": "https://p16.tiktokcdn.com/tos-maliva-p-0068/cov1~tplv-x.image",
            "downloadAddr": "https://v16.tiktokcdn.com/dl",
            "PlayAddrStruct": {"DataSize": "1234", "Height": 1024, "Width": 576, "Uri": "v1", "FileHash": "fh",
                               "UrlKey": "v1_h264_540p_100", "UrlList": ["https://v16.tiktokcdn.com/play"]},
            "bitrateInfo": [{"Bitrate": 100, "BitrateFPS": 30, "GearName": "normal_540_0", "QualityType": 20,
                             "PlayAddr": {"UrlKey": "v1_bytevc1_540p_100", "UrlList": ["https://v16/b"]}}],
            "claInfo": {"captionInfos": [{"language": "eng-US", "languageCode": "en", "url": "https://cdn/cap.vtt",
                                          "captionFormat": "webvtt", "claSubtitleID": "77", "isAutoGen": True}],
                        "enableAutoCaption": True, "originalLanguageInfo": {"language": "eng-US", "languageCode": "en"}},
            "subtitleInfos": [{"LanguageCodeName": "spa-ES", "Url": "https://cdn/es.vtt", "Format": "webvtt"},
                              {"LanguageCodeName": "eng-US", "Url": "https://cdn/en.vtt", "Format": "webvtt"}],
        },
    }
    item.update(extra)
    return item


def test_aweme_mapping():
    a = M.aweme(_web_item())
    assert a["aweme_id"] == "7300000000000000001" and a["create_time"] == 1700000000
    assert a["create_time_utc"] == "2023-11-14T22:13:20.000Z"
    assert a["url"] == "https://www.tiktok.com/@someone/video/7300000000000000001"
    assert a["region"] == "US" and a["desc_language"] == "en" and a["is_ad"] is False
    s = a["statistics"]
    assert s == {"aweme_id": "7300000000000000001", "collect_count": 1, "comment_count": 3, "digg_count": 7,
                 "play_count": 123456789012, "repost_count": 0, "share_count": 2}
    v = a["video"]
    assert v["duration"] == 14861 and v["download_addr"] == {"url_list": ["https://v16.tiktokcdn.com/dl"]}
    assert v["play_addr"]["data_size"] == 1234 and v["is_bytevc1"] == 0
    assert v["bit_rate"][0]["is_bytevc1"] == 1 and v["bit_rate"][0]["gear_name"] == "normal_540_0"
    assert v["cover"]["uri"] == "tos-maliva-p-0068/cov1"
    assert v["cla_info"]["caption_infos"][0]["cla_subtitle_id"] == 77
    assert v["cla_info"]["enable_auto_caption"] == 1
    au = a["author"]
    assert au["uid"] == "42" and au["unique_id"] == "someone" and au["secret"] == 0
    assert au["total_favorited"] == 99 and au["aweme_count"] == 5 and "room_id" not in au
    assert "music" not in a and "image_post_info" not in a  # profile-feed shape, and no images on a video

    photo = _web_item(imagePost={"images": [{"imageURL": {"urlList": ["https://img/1"]},
                                             "imageWidth": 1080, "imageHeight": 1440}]})
    images = {"images": [{"display_image": {"url_list": ["https://img/1"], "width": 1080, "height": 1440}}]}
    p = M.aweme(photo)  # a photo post in the profile feed keeps its images (wapo 7697300746554838302: 10)
    assert p["image_post_info"] == images and "music" not in p
    d = M.aweme(photo, detail=True)
    assert "cla_info" not in d["video"]
    assert d["music"]["id_str"] == "555" and d["music"]["author"] == "someone" and d["music"]["is_original"] is True
    assert d["image_post_info"] == images


def test_duration_ms():
    assert M.duration_ms(_web_item()) == 14861  # own sound: precise length of this clip
    reused = _web_item(music={"id": "9", "original": False, "preciseDuration": {"preciseVideoDuration": 60.2}})
    assert M.duration_ms(reused) == 15000  # the sound's source video is 60 s: not this post's length
    far = _web_item(music={"id": "9", "authorName": "someone", "original": True,
                           "preciseDuration": {"preciseVideoDuration": 60.2}})
    assert M.duration_ms(far) == 15000
    photo = _web_item(video={"duration": 0, "width": 0, "height": 0})
    assert M.duration_ms(photo) is None and "duration" not in M.aweme(photo)["video"]
    # music.original marks the sound, not the post: miotravelapp 7656321429029555477 reuses kragord's
    # 61.127 s original sound and is 61.6 s long (web duration 61)
    borrowed = _web_item(video={"duration": 61}, music={"id": "7376306553648515872", "authorName": "kragord",
                                                         "original": True,
                                                         "preciseDuration": {"preciseVideoDuration": 61.127}})
    assert M.duration_ms(borrowed) == 61000
    own = _web_item()["music"]
    assert M.duration_ms(_web_item(music={**own, "authorName": "Some One"})) == 14861  # by nickname
    assert M.duration_ms(_web_item(music={**own, "authorName": ""})) == 15000
    assert M.duration_ms(_web_item(music={**own, "authorName": None}, author={})) == 15000
    endless = _web_item(video={**_web_item()["video"], "duration": 10 ** 400})  # past a float's range: was a 500
    assert M.duration_ms(endless) == 10 ** 403


def test_counts_survive_int32_overflow():
    item = _web_item(authorStats={"followerCount": 91538796, "heartCount": -1894967296, "heart": 2400000000,
                                  "videoCount": 146},
                     authorStatsV2={"followerCount": "91538796", "heartCount": "2416749904", "videoCount": "146"})
    au = M.author(item)
    assert au["total_favorited"] == 2416749904 and au["follower_count"] == 91538796 and au["aweme_count"] == 146
    no_v2 = M.author(_web_item(authorStats={"heartCount": -1894967296, "heart": 2400000000}))
    assert no_v2["total_favorited"] == 2400000000
    assert "total_favorited" not in M.author(_web_item(authorStats={"heartCount": -5}))

    stats = {"followerCount": 91538796, "heart": 2400000000, "heartCount": -1894967296, "videoCount": 146,
             "friendCount": 577, "verified": True}
    exact = M.exact_stats(stats, {"followerCount": "91538796", "heart": "2416749904", "heartCount": "2416749904",
                                  "videoCount": "146"})
    assert exact == {"followerCount": 91538796, "heart": 2416749904, "heartCount": 2416749904, "videoCount": 146,
                     "friendCount": 577, "verified": True}
    assert M.exact_stats({"heartCount": -1894967296, "heart": 2400000000}, None)["heartCount"] == 2400000000
    assert M.exact_stats(None, None) is None

    # post counts: zachking 6768504823336815877 has stats.playCount -1894967296, statsV2 "2400000000"
    plays = M.statistics(_web_item(stats={"playCount": -1894967296}, statsV2={"playCount": "2400000000"}))
    assert plays["play_count"] == 2400000000
    assert "play_count" not in M.statistics(_web_item(stats={"playCount": -1894967296}, statsV2={}))


def test_comments_turned_off_leave_the_count_unknown():
    """@xrisit/video/7684746443122412833 has comments turned off: its web item (itemCommentStatus 3) reports
    commentCount 0 and statsV2 '0', its app aweme comment_count 167. The 0 is hidden, not a count."""
    off = _web_item(itemCommentStatus=3, stats={"playCount": 8300000, "diggCount": 203300, "commentCount": 0},
                    statsV2={"commentCount": "0", "diggCount": "203300"})
    s = M.statistics(off)
    assert "comment_count" not in s and s["digg_count"] == 203300 and s["play_count"] == 8300000
    assert "comment_count" not in M.aweme(off)["statistics"]
    # comments open (0, or no status at all): a 0 is a real 0, and a count shown while they are off is kept
    assert M.statistics(_web_item(itemCommentStatus=0, stats={"commentCount": 0}, statsV2={}))["comment_count"] == 0
    assert M.statistics(_web_item(stats={"commentCount": 0}, statsV2={}))["comment_count"] == 0
    assert M.statistics(_web_item(itemCommentStatus=3, stats={"commentCount": 12}, statsV2={}))["comment_count"] == 12
    # the recorded item_list (itemCommentStatus 0) keeps its counts
    assert [M.statistics(it)["comment_count"] for it in _FEED["itemList"]] == [467, 162, 106]


def test_verified_authors_carry_the_app_badge():
    """Stratton reads creator.verified from author.custom_verify / verification_type, which the web author lacks."""
    a = M.author(_FEED["itemList"][0])  # stoolpresidente: author.verified true
    assert (a["verification_type"], a["custom_verify"]) == (1, "Verified account")
    plain = M.author(_web_item(author={**_web_item()["author"], "verified": False}))
    assert plain["verification_type"] == 0 and "custom_verify" not in plain
    assert "verification_type" not in M.author(_web_item()) and "custom_verify" not in M.author(_web_item())


def test_app_detail():
    app = {"aweme_id": "7300000000000000001", "create_time": 1700000000, "is_ads": True,
           "author": {"uid": "42", "unique_id": "someone", "follower_count": 0, "total_favorited": 0,
                      "region": "US"},
           "statistics": {"play_count": 42160618}}
    d = M.app_detail(app)
    assert d["url"] == "https://www.tiktok.com/@someone/video/7300000000000000001"
    assert d["create_time_utc"] == "2023-11-14T22:13:20.000Z" and d["is_ad"] is True
    assert d["author"]["follower_count"] == 0 and d["statistics"] == app["statistics"]
    filled = M.app_detail(app, _web_item(authorStatsV2={"heartCount": "2400000000", "followerCount": "10"}))
    au = filled["author"]
    assert au["total_favorited"] == 2400000000 and au["follower_count"] == 10 and au["aweme_count"] == 5
    assert au["region"] == "US" and au["uid"] == "42"
    assert M.app_detail({**app, "is_ad": False, "url": "u"}, None)["is_ad"] is False
    assert M.app_detail({**app, "is_ad": False, "url": "u"})["url"] == "u"


def test_app_detail_unpins_int32_counts():
    # music/aweme pins counts past int32: zachking 6768504823336815877's 2.4B plays are 2147483597 there
    app = {"aweme_id": "6768504823336815877", "author": {"unique_id": "zachking", "total_favorited": 2147483597},
           "statistics": {"aweme_id": "6768504823336815877", "play_count": 2147483597, "digg_count": 29607718,
                          "download_count": 569289}}
    web = _web_item(stats={"playCount": -1894967296, "diggCount": 29600000},
                    statsV2={"playCount": "2400000000", "diggCount": "29600000"},
                    authorStatsV2={"heartCount": "2689148052"})
    d = M.app_detail(app, web)
    assert d["statistics"] == {"aweme_id": "6768504823336815877", "play_count": 2400000000,
                               "digg_count": 29607718, "download_count": 569289}  # exact app counts are kept
    assert d["author"]["total_favorited"] == 2689148052
    assert app["statistics"]["play_count"] == 2147483597  # the app object isn't mutated
    assert M.app_detail(app)["statistics"]["play_count"] == 2147483597  # no web item: as TikTok sent it
    # near the ceiling but not beaten by the web's count: a real value
    near = {**app, "statistics": {"play_count": 2147483600}}
    assert M.app_detail(near, _web_item(statsV2={"playCount": "2100000000"}))["statistics"]["play_count"] == 2147483600


def test_aweme_never_invents_values():
    a = M.aweme({"id": 1, "author": {}, "video": {}})
    assert a == {"aweme_id": "1", "url": "https://www.tiktok.com/@_/video/1", "statistics": {"aweme_id": "1"},
                 "video": {}, "author": {}}


def test_mappers_read_a_field_of_another_type_as_missing():
    # fix14: a field TikTok sent as another type than usual (an entry of a list null, a string for an object, a
    # number for a list) crashed the mapper: a 500 on every route that maps a post
    odd = _web_item(author="someone", authorStats=[1], stats="7", statsV2=None, music=["555"], createTime=10**20,
                    imagePost={"images": [None, "x", {"imageURL": "https://img/1"},
                                          {"imageURL": {"urlList": ["https://img/2"]}}]})
    odd["video"].update(
        PlayAddrStruct="https://v16/play", bitrateInfo=[None, 7, {"Bitrate": 9, "PlayAddr": "x"}],
        cover={"url": "https://p16/c.jpg"}, dynamicCover=["https://p16/d.jpg"],
        claInfo={"captionInfos": [None, "eng-US", {"language": ["eng-US"], "languageCode": 7}],
                 "originalLanguageInfo": "eng-US"},
        subtitleInfos=[None, {"LanguageCodeName": {"name": "eng-US"}, "Url": "https://cdn/x.vtt", "Format": 7},
                       {"LanguageCodeName": "spa-ES", "Url": ["https://cdn/es.vtt"], "Format": "webvtt"}])
    for detail in (False, True):
        a = M.aweme(odd, detail=detail)
        assert a["url"] == "https://www.tiktok.com/@_/video/7300000000000000001" and a["create_time"] == 10**20
        assert "create_time_utc" not in a and "music" not in a  # no date that far out
        assert a["author"] == {} and a["statistics"] == {"aweme_id": "7300000000000000001"}
        assert a["video"]["bit_rate"] == [{"bit_rate": 9}] and a["video"]["duration"] == 15000
        assert not {"cover", "dynamic_cover", "play_addr", "is_bytevc1"} & set(a["video"])
        assert a["image_post_info"] == {"images": [{"display_image": {"url_list": ["https://img/2"]}}]}
    assert M.aweme(odd)["video"]["cla_info"] == {"caption_infos": [{"lang": ["eng-US"], "language_code": 7}]}
    assert M.caption_track(odd) == {"name": None, "url": "https://cdn/x.vtt", "format": None}
    assert M.caption_track(odd, "en") is None and M.caption_track(odd, "es") is None
    assert M.duration_ms(_web_item(music={**_web_item()["music"], "preciseDuration": 14.861})) == 15000
    assert M.aweme(_web_item(video="x", author={"uniqueId": ["someone"]}))["video"] == {}
    assert M.app_detail({"aweme_id": "1", "create_time": 10**20, "author": {"unique_id": {}}}) == {
        "aweme_id": "1", "create_time": 10**20, "author": {"unique_id": {}}, "url": "https://www.tiktok.com/@_/video/1"}
    assert M.live_user("x", [1]) == {} and M.live_user({"id": "7"}, "x") == {"id": "7"}
    assert M.live_room({"id_str": "9", "owner": "x", "cover": {"url_list": "https://c/1"}}) == {"room_id": "9"}
    assert M.to_int(float("inf")) is None and M.to_int(float("nan")) is None


def test_caption_track_selection():
    item = _web_item()
    assert M.caption_track(item)["url"] == "https://cdn/en.vtt"  # original language wins
    assert M.caption_track(item, "en")["url"] == "https://cdn/en.vtt"
    assert M.caption_track(item, "es")["url"] == "https://cdn/es.vtt"
    assert M.caption_track(item, "eng")["url"] == "https://cdn/en.vtt"  # ISO 639-2 codes work too
    assert M.caption_track(item, "SPA")["url"] == "https://cdn/es.vtt"
    assert M.caption_track(item, "fr") is None
    item["video"].pop("subtitleInfos")
    assert M.caption_track(item, "en")["url"] == "https://cdn/cap.vtt"  # falls back to claInfo
    assert M.caption_track({"id": 1, "video": {}}) is None


def test_caption_track_chinese():
    # gap-tiktok-2: zh (ScrapeCreators' own example) matched neither 'zh-hans' nor 'cmn' (Mandarin's ISO 639-3 code)
    zh, en = {"language": "cmn-Hans-CN", "languageCode": "zh-Hans"}, {"language": "eng-US", "languageCode": "en"}
    item = _web_item(video={
        "claInfo": {"captionInfos": [zh, en], "originalLanguageInfo": zh},
        "subtitleInfos": [{"LanguageCodeName": "eng-US", "Url": "https://cdn/en.vtt", "Format": "webvtt"},
                          {"LanguageCodeName": "cmn-Hans-CN", "Url": "https://cdn/zh.vtt", "Format": "webvtt"}]})
    assert M.caption_track(item)["url"] == "https://cdn/zh.vtt"  # the original language
    for lang in ("zh", "ZH", "zho", "cmn", "CMN", "zh-Hans"):
        assert M.caption_track(item, lang)["url"] == "https://cdn/zh.vtt", lang
    for lang in ("en", "eng"):
        assert M.caption_track(item, lang)["url"] == "https://cdn/en.vtt", lang
    assert M.caption_track(item, "yue") is None and M.caption_track(item, "ja") is None
    item["video"]["claInfo"]["captionInfos"] = [en]  # no claInfo code for the Chinese track: its name decides
    assert M.caption_track(item, "zh")["url"] == "https://cdn/zh.vtt"
    item["video"]["subtitleInfos"].append({"LanguageCodeName": "cmn-Hant-TW", "Url": "https://cdn/tw.vtt"})
    assert M.caption_track(item, "zh")["url"] == "https://cdn/zh.vtt"  # the first Chinese track


def test_caption_track_other_codes_and_norwegian():
    # 'per', Persian's ISO 639-2/B code, read as 'pe'; Nynorsk and Bokmål found no track in the other
    item = _web_item(video={"subtitleInfos": [
        {"LanguageCodeName": "fas-IR", "Url": "https://cdn/fa.vtt", "Format": "webvtt"},
        {"LanguageCodeName": "deu-DE", "Url": "https://cdn/de.vtt", "Format": "webvtt"},
        {"LanguageCodeName": "nno-NO", "Url": "https://cdn/nn.vtt", "Format": "webvtt"}]})
    for lang, track in (("per", "fa"), ("fa", "fa"), ("ger", "de"), ("nn", "nn"), ("no", "nn"), ("nob", "nn")):
        assert M.caption_track(item, lang)["url"] == f"https://cdn/{track}.vtt", lang
    assert M.caption_track(item, "pe") is None and M.caption_track(item, "da") is None
    item["video"]["subtitleInfos"].append({"LanguageCodeName": "nob-NO", "Url": "https://cdn/no.vtt"})
    assert M.caption_track(item, "no")["url"] == "https://cdn/no.vtt"  # a track in the language itself first
    assert M.caption_track(item, "nn")["url"] == "https://cdn/nn.vtt"
    item["video"]["subtitleInfos"].pop(2)
    assert M.caption_track(item, "nn")["url"] == "https://cdn/no.vtt"


# Caption URLs yt-dlp fails on before asking: with a ValueError (an unclosed '['), a UnicodeError (a host label of 64
# characters, half an emoji) or a RequestError that isn't a network error (a port that isn't one, no host, another
# scheme), none of which fatal=False catches
_UNREQUESTABLE = ("http://[", f"https://{'a' * 64}.tiktokcdn.com/x.vtt", "https://cdn/\ud83d.vtt",
                  "https://cdn:abc/x.vtt", "https:///x.vtt", "javascript:alert(1)", "file:///x.vtt")


def test_caption_track_at_a_url_yt_dlp_cant_request_is_left_out():
    item = _web_item()
    item["video"]["subtitleInfos"][:0] = [{"LanguageCodeName": "eng-US", "Url": bad, "Format": "webvtt"}
                                          for bad in _UNREQUESTABLE]
    assert M.caption_track(item)["url"] == M.caption_track(item, "en")["url"] == "https://cdn/en.vtt"
    del item["video"]["subtitleInfos"][len(_UNREQUESTABLE):]
    assert M.caption_track(item, "en")["url"] == "https://cdn/cap.vtt"  # none left: the caption list's
    item["video"]["claInfo"]["captionInfos"][0]["url"] = "http://["
    assert M.caption_track(item) is None and M.caption_track(item, "en") is None


def test_captions_at_a_url_yt_dlp_cant_request_are_none(client, monkeypatch):
    """A post's only caption track at a URL yt-dlp can't request (see _UNREQUESTABLE) was a 500 for its transcript:
    "Internal server error", an upstream_error, or for another scheme a server misconfigured. It reads as no track.
    The real with_ydl and TikTokIE run here, on the post's web data."""
    from yt_dlp.extractor.tiktok import TikTokIE

    url = M.web_url("someone", "7300000000000000001")
    monkeypatch.setattr(tiktok, "_app_aweme", lambda ydl, item: None)
    for bad in _UNREQUESTABLE:
        item = _web_item()
        item["video"].update(claInfo={}, subtitleInfos=[{"LanguageCodeName": "eng-US", "Url": bad, "Format": "webvtt"}])
        monkeypatch.setattr(TikTokIE, "_extract_web_data_and_status", lambda self, page, aweme_id, item=item: (item, 0))
        assert ok(client, "/v1/tiktok/video/transcript", url=url)["transcript"] is None, bad
        body = ok(client, "/v2/tiktok/video", url=url, get_transcript="true")
        assert body["transcript"] is None and body["aweme_detail"]["aweme_id"] == "7300000000000000001", bad


def test_short_lang():
    from socialscrape.util import short_lang

    for given, code in (("en", "en"), ("EN", "en"), ("eng", "en"), ("spa-ES", "es"), ("cmn-Hans-CN", "zh"),
                        ("zh-Hans", "zh"), ("zho", "zh"), ("cmn", "zh"), ("arb", "ar"), ("yue", "yue"), ("", ""),
                        (None, "")):
        assert short_lang(given) == code, given
    # fix3: one code per language, so that 'he' finds YouTube's 'iw' captions and 'no' TikTok's 'nob-NO' ones
    for given, code in (("iw", "he"), ("he", "he"), ("heb-IL", "he"), ("in", "id"), ("ind", "id"), ("ji", "yi"),
                        ("yid", "yi"), ("jw", "jv"), ("jav", "jv"), ("mo", "ro"), ("ron", "ro"), ("nb", "no"),
                        ("nob-NO", "no"), ("nor", "no"), ("nn", "nn"), (" fil ", "tl"), ("fil-PH", "tl"),
                        ("tgl", "tl")):
        assert short_lang(given) == code, given
    # ISO 639-2/B codes, as MKV and ffmpeg tag languages: 'per' was 'pe' (ISO639Utils' own table), 'ger' was 'ger'
    for given, code in (("per", "fa"), ("PER-IR", "fa"), ("fas", "fa"), ("ger", "de"), ("fre-CA", "fr"),
                        ("chi", "zh"), ("cze", "cs"), ("dut", "nl"), ("gre", "el"), ("may", "ms"), ("wel", "cy"),
                        ("alb", "sq"), ("arm", "hy"), ("baq", "eu"), ("bur", "my"), ("geo", "ka"), ("ice", "is"),
                        ("mac", "mk"), ("mao", "mi"), ("rum", "ro"), ("slo", "sk"), ("tib", "bo")):
        assert short_lang(given) == code, given


def test_live_mappers():
    room = {"id_str": "99", "status": 2, "user_count": 2035, "like_count": 0, "title": "t",
            "cover": {"url_list": ["https://c/1"]}, "owner": {"id_str": "7", "display_id": "h", "nickname": "N"}}
    assert M.live_room(room) == {"room_id": "99", "viewer_count": 2035, "status": "live", "title": "t",
                                 "cover_url": "https://c/1", "owner": {"id": "7", "username": "h", "display_name": "N"}}
    assert M.live_room({**room, "status": 4, "like_count": 5})["like_count"] == 5
    assert M.live_room({**room, "status": 4})["status"] == "ended"
    u = M.live_user({"id": "7", "uniqueId": "h", "roomId": "99", "ftc": False}, {"followerCount": 3})
    assert u == {"id": "7", "uniqueId": "h", "roomId": "99", "followerCount": 3}


class _FakeFeed:
    """Stand-in for TikTokUserIE's item_list: inclusive cursor, ~1-week look-back, newest first. The request is built
    as yt-dlp builds it, so a query it can't send fails as it does there: before anything is asked."""

    _API_BASE_URL = "https://www.tiktok.com/api/creator/item_list/"

    def __init__(self, times: list[int], page: int = 3, extra: dict | None = None):
        self.times, self.page, self.calls, self.extra = sorted(times, reverse=True), page, 0, extra or {}

    def _build_web_query(self, sec_uid, cursor):
        return {"secUid": sec_uid, "cursor": cursor, "region": "US"}

    def _download_json(self, url, display, note, query, **kwargs):
        Request(url, query=query)
        self.calls += 1
        cur = query["cursor"]
        hits = [t for t in self.times if cur - tiktok._WEEK_MS < t * 1000 <= cur][: self.page]
        floor = hits[-1] * 1000 if hits else cur - tiktok._WEEK_MS
        return {"statusCode": 0, "hasMorePrevious": any(t * 1000 < floor for t in self.times),
                "itemList": [{"id": str(t), "createTime": t, **self.extra} for t in hits]}


def _walk_feed(times: list[int], start_ms: int) -> tuple[list[str], int]:
    feed, cursor, ids, pages = _FakeFeed(times), start_ms, [], 0
    while cursor is not None and pages < 50:
        items, cursor = tiktok._feed_page(feed, "sec", "h", cursor, "US")
        ids += [it["id"] for it in items]
        pages += 1
    assert cursor is None, "feed never ended"
    return ids, pages


def test_feed_pagination_has_no_gaps_or_overlaps():
    day = 86_400
    base = 1_700_000_000
    times = [base - i * 3600 for i in range(8)]  # 8 posts an hour apart
    times += [base - 10 * day - i for i in range(4)]  # after a 10-day gap (one look-back hop)
    times += [base - 60 * day - i for i in range(3)]  # after a ~7-week gap (needs an empty page in between)
    ids, pages = _walk_feed(times, (base + day) * 1000)
    assert ids == [str(t) for t in sorted(times, reverse=True)]
    assert pages >= 6


def test_feed_page_last_page_has_no_cursor():
    items, cursor = tiktok._feed_page(_FakeFeed([1_700_000_000, 1_700_000_001]), "sec", "h",
                                      1_700_000_001_000, "US")
    assert [it["id"] for it in items] == ["1700000001", "1700000000"] and cursor is None


def test_feed_page_status_codes(monkeypatch):
    # tiktok-status-10102: item_list's statusCode 10102 (USER_NOT_LOGIN) was a 500; a retry soon after works, so a
    # refusal is asked again (twice) before it is a 503
    monkeypatch.setattr(tiktok, "_RETRY_DELAY", 0)
    for status, error, asks in ((10102, UpstreamBlocked, 3), (10000, UpstreamBlocked, 3), (10222, Forbidden, 1),
                                (10221, NotFound, 1), (10202, NotFound, 1), (10201, UpstreamError, 1),
                                (12345, UpstreamError, 1)):
        feed, asked = _FakeFeed([]), []
        feed._download_json = lambda *args, **kwargs: asked.append(1) or {
            "statusCode": status, "status_code": status, "status_msg": ""}
        tiktok._cooling.clear()  # a refusal starts a cooling window (test_item_list_cools_down_after_a_block)
        with pytest.raises(error):
            tiktok._feed_page(feed, "sec", "h", 1_700_000_000_000, "US")
        assert len(asked) == asks, status
    tiktok._cooling.clear()
    feed, answers = _FakeFeed([]), [{"statusCode": 10102}, {"statusCode": 0, "hasMorePrevious": False,
                                                            "itemList": [{"id": "5", "createTime": 1_699_999_999}]}]
    feed._download_json = lambda *args, **kwargs: answers.pop(0)
    assert tiktok._feed_page(feed, "sec", "h", 1_700_000_000_000, "US") == ([{"id": "5", "createTime": 1_699_999_999}],
                                                                           None)


def test_offset():
    assert tiktok._offset("cursor", None) == 0 and tiktok._offset("cursor", "") == 0
    assert tiktok._offset("cursor", "40") == 40
    for bad in ("-1", "1.5", "\u00b2", "\u0663", " 4", "9" * 10, "ss1.abc"):
        with pytest.raises(BadRequest) as exc:
            tiktok._offset("cursor", bad)
        assert exc.value.code == "bad_cursor", bad  # tt_misc-4: same code as every other platform's cursors


def test_max_cursor():
    now = time.time() * 1000
    for first in (None, "", "0", "000"):
        assert abs(tiktok._max_cursor(first) - now) < 5_000, first
    assert tiktok._max_cursor(" 1746050509999 ") == 1746050509999
    assert tiktok._max_cursor(str(tiktok._FEED_START_MS)) == tiktok._FEED_START_MS
    for bad in ("abc", "-5", "1.5e12", "1" * 26, "12345", "1746050510", str(tiktok._FEED_START_MS - 1), "\u00b2",
                "   ", "ss1.abc"):
        with pytest.raises(BadRequest) as exc:
            tiktok._max_cursor(bad)
        assert exc.value.code == "bad_cursor", bad
        assert exc.value.message == "max_cursor must be 0 or the millisecond max_cursor of a previous response"


# --- offline: extractor glue and routes on fake extractors ------------------------

class _FakeYdl:
    def __init__(self, **ies):
        self.ies = ies

    def get_info_extractor(self, name):
        return self.ies[name]


class _FakePost:
    """TikTokIE stand-in: answers _extract_web_data_and_status from a list of (item, status) or exceptions."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def _extract_web_data_and_status(self, url, video_id):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class _FakeCollection:
    _API_BASE_URL = "https://www.tiktok.com/api/collection/item_list/"

    def __init__(self, resp):
        self.resp, self.queries = resp, []

    def _build_web_query(self, cid, cursor):
        return {"collectionId": cid, "cursor": cursor}

    def _download_json(self, url, cid, note, query, **kwargs):
        self.queries.append(query)
        return self.resp


def _user_info(name: str = "someone", **user) -> dict:
    return {"user": {"id": "42", "uniqueId": name, "secUid": f"sec-{name}", "nickname": "Some One", **user},
            "stats": {"videoCount": 5}, "statusCode": 0}


@pytest.fixture
def fake_ydl(monkeypatch):
    """Routes run without the network: with_ydl calls `fn` on a _FakeYdl and records its options."""
    ydl, calls = _FakeYdl(), []

    async def run(platform, fn, **opts):
        calls.append(opts)
        return fn(ydl)

    monkeypatch.setattr(tiktok, "with_ydl", run)
    monkeypatch.setattr(tiktok, "_RETRY_DELAY", 0)
    tiktok._profiles.clear()
    yield ydl, calls
    tiktok._profiles.clear()


def test_post_retries_once_on_10204(fake_ydl):
    ydl, _ = fake_ydl
    item = _web_item()
    ydl.ies["TikTok"] = ie = _FakePost(({}, 10204), (item, 0))
    assert tiktok._load_post(ydl, "www.tiktok.com/@someone/video/7300000000000000001") == (ie, item)
    assert ie.calls == 2
    ydl.ies["TikTok"] = ie = _FakePost(({}, 10204), ({}, 10204))
    with pytest.raises(NotFound, match="retry later"):
        tiktok._load_post(ydl, STOOL_VIDEO)
    ydl.ies["TikTok"] = ie = _FakePost(({}, 10216))
    with pytest.raises(Forbidden):
        tiktok._load_post(ydl, STOOL_VIDEO)
    assert ie.calls == 1
    ydl.ies["TikTok"] = _FakePost(({}, 10102))
    with pytest.raises(UpstreamBlocked):
        tiktok._load_post(ydl, STOOL_VIDEO)


def test_post_page_yt_dlp_could_not_get_past_is_503(fake_ydl):
    # tiktok-live-waf: yt-dlp solves the post page's JS challenge itself; when that fails it was a 500
    ydl, _ = fake_ydl
    for message in ("Unable to extract universal data for rehydration", "Unable to solve JS challenge",
                    "Unable to extract challenge data", "Unexpected response from webpage request"):
        ydl.ies["TikTok"] = _FakePost(ExtractorError(message))
        with pytest.raises(UpstreamBlocked, match=message):
            tiktok._load_post(ydl, STOOL_VIDEO)
    ydl.ies["TikTok"] = _FakePost(ExtractorError("Unable to download webpage: HTTP Error 404: Not Found"))
    with pytest.raises(ExtractorError):  # anything else keeps with_ydl's usual translation
        tiktok._load_post(ydl, STOOL_VIDEO)


class _FakeSound:
    """TikTokSoundIE stand-in: _call_api raises or returns from a list of answers."""

    _DEVICE_ID = "1"

    def __init__(self, *answers):
        self.answers, self.queries = list(answers), []

    def _call_api(self, ep, video_id, note, query):
        self.queries.append(query)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_music_page_retries_an_empty_body(fake_ydl):
    ydl, _ = fake_ydl
    empty = ExtractorError("Failed to parse JSON", cause=json.JSONDecodeError("Expecting value", "", 0))
    ydl.ies["TikTokSound"] = ie = _FakeSound(empty, {"status_code": 0, "aweme_list": [{"aweme_id": "1"}]})
    assert tiktok._music_page(ydl, "555", 20)["aweme_list"] == [{"aweme_id": "1"}]
    assert [q["cursor"] for q in ie.queries] == [20, 20]
    ydl.ies["TikTokSound"] = _FakeSound(empty, empty)
    with pytest.raises(UpstreamBlocked, match="an empty body instead of JSON, twice"):
        tiktok._music_page(ydl, "555", 0)
    # tiktok-discovery: a block page (not empty) was a 500 "Failed to parse JSON" on /v1/tiktok/profile/region
    page = ExtractorError("Failed to parse JSON", cause=json.JSONDecodeError("Expecting value", _BLOCK_PAGE, 0))
    ydl.ies["TikTokSound"] = _FakeSound(empty, page)
    with pytest.raises(UpstreamBlocked, match=r'an HTML page \("Access Denied"\) instead of JSON'):
        tiktok._music_page(ydl, "555", 0)
    ydl.ies["TikTokSound"] = ie = _FakeSound(ExtractorError("HTTP Error 403"))
    with pytest.raises(ExtractorError):
        tiktok._music_page(ydl, "555", 0)
    assert len(ie.queries) == 1  # other errors are not retried
    ydl.ies["TikTokSound"] = _FakeSound({"status_code": 2, "status_msg": "gone"})
    with pytest.raises(NotFound):
        tiktok._music_page(ydl, "555", 0)


def test_profile_cache(monkeypatch):
    tiktok._profiles.clear()
    fetched = []

    def user_page(ydl, name):
        fetched.append(name)
        return _user_info(name)

    monkeypatch.setattr(tiktok, "_user_page", user_page)
    monkeypatch.setattr(tiktok, "_PROFILE_MAX", 2)
    assert tiktok._profile(None, "Someone") == {"id": "42", "secUid": "sec-Someone", "nickname": "Some One",
                                               "private": False, "videoCount": 5}
    assert tiktok._profile(None, "someone")["secUid"] == "sec-Someone"  # handles are case-insensitive
    tiktok._profile(None, "b")
    tiktok._profile(None, "c")
    assert fetched == ["Someone", "b", "c"] and list(tiktok._profiles) == ["b", "c"]  # oldest evicted
    monkeypatch.setattr(tiktok, "_PROFILE_TTL", -1.0)
    tiktok._profile(None, "d")
    tiktok._profile(None, "d")
    assert fetched[-2:] == ["d", "d"]
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: _user_info(name, privateAccount=True))
    assert tiktok._profile(None, "e")["private"] is True
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: {"user": {"id": "1"}, "stats": {}, "statusCode": 0})
    with pytest.raises(UpstreamError):
        tiktok._profile(None, "nosec")
    tiktok._profiles.clear()


def test_song_routes_take_a_sound_page_url(client, fake_ydl, monkeypatch):
    _, calls = fake_ydl
    asked = []

    def music_page(ydl, mid, cursor):
        asked.append((mid, cursor))
        return {"aweme_list": [{"aweme_id": "1", "music": {"id_str": mid, "title": "Build a B*tch"}}],
                "has_more": True, "cursor": cursor + 20}

    monkeypatch.setattr(tiktok, "_music_page", music_page)
    song = ok(client, "/v1/tiktok/song", clipId=f"https://www.tiktok.com/music/Build-a-Btch-{CLIP_ID}?lang=en")
    assert song["music_info"] == {"id_str": CLIP_ID, "title": "Build a B*tch"}
    page = ok(client, "/v1/tiktok/song/videos", clipId=f" tiktok.com/music/{CLIP_ID} ", cursor="20")
    assert page["cursor"] == 40 and page["aweme_list"][0]["music"]["id_str"] == CLIP_ID
    assert asked == [(CLIP_ID, 0), (CLIP_ID, 20)] and calls[-1]["extractor_args"] == tiktok._APP_API
    for clip in ("https://www.tiktok.com/music/Build-a-Btch", STOOL_VIDEO, "abc"):
        r = client.get("/v1/tiktok/song", params={"clipId": clip})
        assert (r.status_code, r.json()["error"]) == (400, "bad_request"), r.text
        assert "tiktok.com/music/<title>-<id>" in r.json()["message"]
    assert len(asked) == 2


class _FakeVM:
    """TikTokVMIE stand-in: answers a short link's HEAD request with the address it leads to."""

    def __init__(self, leads_to):
        self.leads_to, self.asked = leads_to, []

    def _request_webpage(self, req, *args, **kwargs):
        self.asked.append(req.url)
        return SimpleNamespace(url=self.leads_to[req.url])


def test_song_routes_resolve_a_short_link(client, fake_ydl, monkeypatch):
    ydl, _ = fake_ydl
    sound, post, home = "https://vm.tiktok.com/ZMsound1/", "https://www.tiktok.com/t/ZTpost1/", "vt.tiktok.com/ZShome1"
    ydl.ies["TikTokVM"] = vm = _FakeVM({sound: f"https://www.tiktok.com/music/Build-a-Btch-{CLIP_ID}?_r=1&u_code=x",
                                        post: f"{STOOL_VIDEO}?_r=1", f"https://{home}": "https://www.tiktok.com/?_r=1"})
    asked = []

    def music_page(ydl, mid, cursor):
        asked.append((mid, cursor))
        return {"aweme_list": [{"aweme_id": "1", "music": {"id_str": mid}}], "has_more": False}

    monkeypatch.setattr(tiktok, "_music_page", music_page)
    assert ok(client, "/v1/tiktok/song", clipId=sound)["music_info"] == {"id_str": CLIP_ID}
    assert ok(client, "/v1/tiktok/song/videos", clipId=f" {sound} ", cursor="20")["aweme_list"]
    for clip in (post, home):  # a post's short link, or one that TikTok sends to its homepage
        r = client.get("/v1/tiktok/song", params={"clipId": clip})
        assert (r.status_code, r.json()["message"]) == (404, "This short link does not lead to a TikTok sound")
    assert asked == [(CLIP_ID, 0), (CLIP_ID, 20)] and vm.asked == [sound, sound, post, f"https://{home}"]


def test_app_aweme_lookup(monkeypatch):
    item = _web_item()
    app = {"aweme_id": item["id"], "video": {"duration": 14861}}
    monkeypatch.setattr(tiktok, "_music_page", lambda ydl, mid, cursor: {
        "aweme_list": [{"aweme_id": "1", "video": {}}, {"aweme_id": item["id"]}, app]})
    assert tiktok._app_aweme(None, item) is app
    assert tiktok._app_aweme(None, _web_item(music={"id": "\u0663"})) is None

    def broken(ydl, mid, cursor):
        raise ExtractorError("TikTokSound is broken")

    monkeypatch.setattr(tiktok, "_music_page", broken)
    assert tiktok._app_aweme(None, item) is None


def test_account_region(monkeypatch):
    prof = {"id": "42", "nickname": "Some One"}
    own = {"music": {"id": "555", "original": True, "authorName": "Some One"}}
    pages = []

    def music_page(ydl, mid, cursor):
        pages.append(mid)
        if mid == "1":
            raise ExtractorError("blocked")
        return {"aweme_list": [{"aweme_id": "9", "author": {"uid": "7", "region": "GB"}},
                               {"aweme_id": "8", "author": {"uid": "42", "region": "US"}}] if mid == "555" else []}

    monkeypatch.setattr(tiktok, "_music_page", music_page)
    reused = {"id": "3", "music": {"id": "777", "original": False, "authorName": "Some One"}}
    other = {"id": "4", "music": {"id": "666", "original": True, "authorName": "Someone Else"}}
    assert tiktok._account_region(None, prof, [reused, other, {"id": "5", **own}]) == "US"
    assert pages == ["555"]  # only the creator's own sounds are looked up
    pages.clear()
    failing = [{"id": "5", "music": {**own["music"], "id": i}} for i in ("1", "2", "555")]
    assert tiktok._account_region(None, prof, failing) is None and pages == ["1", "2"]  # gives up after 2
    # gap-tiktok-1: when no lookup got an answer, the last failure is raised, not a false "no own sound"
    def raising(**by_sound):
        def music_page(ydl, mid, cursor):
            pages.append(mid)
            raise by_sound[f"s{mid}"]
        return music_page

    proxy = ExtractorError("Tunnel connection failed", cause=ProxyError("Tunnel connection failed: 403 Forbidden"))
    blocked = UpstreamBlocked("The platform returned an empty response; try again later")
    gone = NotFound("TikTok sound not available: gone")  # an answer: the app API has no such sound
    for s1, s2 in ((proxy, proxy), (blocked, blocked), (proxy, blocked)):
        monkeypatch.setattr(tiktok, "_music_page", raising(s1=s1, s2=s2))
        pages.clear()
        with pytest.raises(type(s2)) as raised:
            tiktok._account_region(None, prof, failing)
        assert raised.value is s2 and pages == ["1", "2"]
    for s1, s2 in ((blocked, gone), (gone, proxy), (gone, gone)):
        monkeypatch.setattr(tiktok, "_music_page", raising(s1=s1, s2=s2))
        assert tiktok._account_region(None, prof, failing) is None
    assert tiktok._account_region(None, prof, [reused, other]) is None  # no own-sound post: nothing looked up


def test_video_route_prefers_app_aweme(client, fake_ydl, monkeypatch):
    ydl, calls = fake_ydl
    item = _web_item(authorStatsV2={"heartCount": "2400000000"})
    url = M.web_url("someone", item["id"])
    app = {"aweme_id": item["id"], "create_time": 1700000000, "is_ads": False,
           "video": {"duration": 14861, "has_watermark": True}, "statistics": {"play_count": 42160618},
           "author": {"uid": "42", "unique_id": "someone", "total_favorited": 0, "region": "US"}}
    monkeypatch.setattr(tiktok, "_music_page", lambda ydl, mid, cursor: {"aweme_list": [{**app, "aweme_id": "1"}, app]})
    ydl.ies["TikTok"] = _FakePost((item, 0))
    d = ok(client, "/v2/tiktok/video", url=url)["aweme_detail"]
    assert d["video"] == app["video"] and d["statistics"]["play_count"] == 42160618
    assert d["author"]["total_favorited"] == 2400000000 and d["author"]["region"] == "US"
    assert d["is_ad"] is False and d["url"] == url
    assert calls[-1]["extractor_args"] == tiktok._APP_API

    monkeypatch.setattr(tiktok, "_music_page", lambda ydl, mid, cursor: {"aweme_list": []})
    ydl.ies["TikTok"] = _FakePost((item, 0))
    assert ok(client, "/v2/tiktok/video", url=url)["aweme_detail"] == M.aweme(item, detail=True)


def test_video_length_too_long_to_write_in_ms_is_null(client, fake_ydl, monkeypatch):
    """A web video.duration of 4300 digits (as many as Python reads) is more in ms than it writes out: a 500
    (ValueError) writing /v2/tiktok/video and /v3/tiktok/profile/videos. It is null."""
    ydl, _ = fake_ydl
    item = _web_item(video={**_web_item()["video"], "duration": int("9" * 4300)})
    monkeypatch.setattr(tiktok, "_music_page", lambda ydl, mid, cursor: {"aweme_list": []})
    ydl.ies["TikTok"] = _FakePost((item, 0))
    d = ok(client, "/v2/tiktok/video", url=M.web_url("someone", item["id"]))["aweme_detail"]
    assert d["video"]["duration"] is None and d["aweme_id"] == item["id"]
    now = int(time.time())
    ydl.ies["TikTokUser"] = _FakeFeed([now - 60], extra={"video": {"duration": int("9" * 4300)}})
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: _user_info(name))
    feed = ok(client, "/v3/tiktok/profile/videos", handle="someone")["aweme_list"]
    assert [(a["aweme_id"], a["video"]["duration"]) for a in feed] == [(str(now - 60), None)]


def test_profile_videos_route(client, fake_ydl, monkeypatch):
    ydl, _ = fake_ydl
    now = int(time.time())
    ydl.ies["TikTokUser"] = _FakeFeed([now - 60 * i for i in range(5)])
    fetched = []
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: fetched.append(name) or _user_info(name))
    p1 = ok(client, "/v3/tiktok/profile/videos", handle="someone", max_cursor="0")
    assert [a["aweme_id"] for a in p1["aweme_list"]] == [str(now - 60 * i) for i in range(3)]
    assert p1["has_more"] is True and p1["max_cursor"] == (now - 120) * 1000 - 1
    p2 = ok(client, "/v3/tiktok/profile/videos", handle="someone", max_cursor=str(p1["max_cursor"]))
    assert [a["aweme_id"] for a in p2["aweme_list"]] == [str(now - 180), str(now - 240)]
    assert p2["has_more"] is False and "max_cursor" not in p2
    assert fetched == ["someone"]  # the second page reuses the cached secUid
    ydl.ies["TikTokUser"].times = []
    ydl.ies["TikTokUser"]._download_json = lambda *args, **kwargs: {"statusCode": 10102, "status_code": 10102}
    monkeypatch.setattr(tiktok, "_embed_ids", lambda ydl, name: [])  # the embed page lists nothing either
    r = client.get("/v3/tiktok/profile/videos", params={"handle": "someone"})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked"), r.text
    assert r.json()["message"] == "TikTok refused the anonymous request (status 10102); retry later"


def test_profile_region_route(client, fake_ydl, monkeypatch):
    monkeypatch.setattr(tiktok, "_REGION_TTL", 0.0)  # each call looks the region up again
    ydl, calls = fake_ydl
    now = int(time.time())
    own = {"music": {"id": "555", "original": True, "authorName": "Some One"}}
    ydl.ies["TikTokUser"] = feed = _FakeFeed([now - 60, now - 120], extra=own)
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: _user_info(name))
    monkeypatch.setattr(tiktok, "_music_page", lambda ydl, mid, cursor: {
        "aweme_list": [{"aweme_id": str(now - 60), "author": {"uid": "42", "region": "US"}}]})
    body = ok(client, "/v1/tiktok/profile/region", handle="@someone")
    assert body == {"success": True, "credits_charged": body["credits_charged"], "handle": "someone",
                    "profile_url": "https://www.tiktok.com/@someone", "region": "US"}
    assert calls[-1]["extractor_args"] == tiktok._APP_API
    feed.extra = {"music": {**own["music"], "original": False}}
    r = client.get("/v1/tiktok/profile/region", params={"handle": "someone"})
    assert r.status_code == 404 and r.json()["error"] == "region_unavailable"


def test_profile_region_app_api_failure_is_retryable(client, monkeypatch):
    # gap-tiktok-1: a proxy refusing *.tiktokv.com (or a throttled empty body) was a 404 "no own original sound".
    # The real with_ydl runs here, so raw ExtractorErrors get its usual translation.
    own = {"music": {"id": "555", "original": True, "authorName": "Some One"}}
    items = [{"id": "8", "createTime": 1_700_000_000, **own}, {"id": "9", "createTime": 1_699_999_000,
                                                                 "music": {**own["music"], "id": "556"}}]
    monkeypatch.setattr(tiktok, "_profiles", {})
    monkeypatch.setattr(tiktok, "_REGION_TTL", 0.0)  # each call looks the region up again
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: _user_info(name))
    monkeypatch.setattr(tiktok, "_feed_page", lambda ie, sec_uid, display, cursor, region: (items, None))
    answers, looked_up = {}, []

    def music_page(ydl, mid, cursor):
        looked_up.append(mid)
        if isinstance(answers[mid], Exception):
            raise answers[mid]
        return answers[mid]

    monkeypatch.setattr(tiktok, "_music_page", music_page)
    tunnel = ProxyError("Tunnel connection failed: 403 Forbidden")
    empty = json.JSONDecodeError("Expecting value", "", 0)
    for exc, status, code in (
            (ExtractorError("Unable to download JSON metadata", cause=tunnel), 502, "proxy_error"),
            (ExtractorError("Failed to parse JSON", cause=empty), 503, "upstream_blocked"),
            (UpstreamBlocked("The platform returned an empty response; try again later"), 503, "upstream_blocked")):
        answers.update({"555": exc, "556": exc})
        looked_up.clear()
        r = client.get("/v1/tiktok/profile/region", params={"handle": "someone"})
        assert (r.status_code, r.json()["error"]) == (status, code), r.text
        assert looked_up == ["555", "556"] and "original sound" not in r.json()["message"]
    # one lookup answering is enough: its region, or a 404 when it doesn't list the account
    answers["556"] = {"aweme_list": [{"aweme_id": "9", "author": {"uid": "42", "region": "GB"}}]}
    assert ok(client, "/v1/tiktok/profile/region", handle="someone")["region"] == "GB"
    answers["556"] = {"aweme_list": []}
    r = client.get("/v1/tiktok/profile/region", params={"handle": "someone"})
    assert (r.status_code, r.json()["error"]) == (404, "region_unavailable")


def test_collection_route(client, fake_ydl):
    ydl, _ = fake_ydl
    ydl.ies["TikTokCollection"] = ie = _FakeCollection({"statusCode": 0, "hasMore": True, "cursor": 30,
                                                        "itemList": [{"id": "1", "desc": "a"}, {"desc": "no id"}]})
    body = ok(client, "/v1/tiktok/collection/videos", url=COLLECTION, cursor="0")
    assert body["videos"] == [{"id": "1", "desc": "a"}] and body["has_more"] is True and body["max_cursor"] == "30"
    assert body["collection_id"] == "7665668414573546258"
    assert ie.queries == [{"collectionId": "7665668414573546258", "cursor": 0}]
    notes = next(r["notes"] for r in client.get("/meta/coverage").json()["endpoints"]
                 if r["path"] == "/v1/tiktok/collection/videos")
    for status, code, error in ((100001, 404, "not_found"), (10102, 503, "upstream_blocked"),
                                (10222, 403, "forbidden"), (12345, 500, "upstream_error")):
        ydl.ies["TikTokCollection"] = _FakeCollection({"statusCode": status, "status_msg": "", "itemList": None})
        r = client.get("/v1/tiktok/collection/videos", params={"url": COLLECTION})
        assert (r.status_code, r.json()["error"]) == (code, error), status
        assert str(code) in notes, code  # observation-tiktok-collection-notes: the 403 was missing
    assert all(str(s) in notes for s in (*tiktok._PRIVATE, *tiktok._REFUSED))


def test_collection_ids_outside_int64_are_404(client, fake_ydl):
    # tt_misc-3: TikTok answers HTTP 400 for these (and int() refuses > 4300 digits), which used to be a 500
    ydl, _ = fake_ydl
    ydl.ies["TikTokCollection"] = ie = _FakeCollection({"statusCode": 0, "hasMore": False, "itemList": []})
    for cid in ("0", "00", str(2**63), "9" * 20, "1" * 5000):
        r = client.get("/v1/tiktok/collection/videos", params={"url": f"https://www.tiktok.com/@u/collection/x-{cid}"})
        assert r.status_code == 404 and r.json()["error"] == "not_found", (cid[:30], r.status_code, r.text[:200])
    assert ie.queries == []  # refused before asking TikTok
    body = ok(client, "/v1/tiktok/collection/videos", url=f"https://www.tiktok.com/@u/collection/x-00{2**63 - 1}")
    assert body["collection_id"] == str(2**63 - 1) and ie.queries[-1]["collectionId"] == str(2**63 - 1)


@pytest.mark.parametrize("path, params", [
    ("/v1/tiktok/collection/videos", {"url": COLLECTION, "cursor": "abc"}),
    ("/v1/tiktok/collection/videos", {"url": COLLECTION, "cursor": "ss1.tampered"}),
    ("/v1/tiktok/song/videos", {"clipId": CLIP_ID, "cursor": "abc"}),
    ("/v1/tiktok/song/videos", {"clipId": CLIP_ID, "cursor": "9" * 10}),
    ("/v3/tiktok/profile/videos", {"handle": "someone", "max_cursor": "abc"}),
    ("/v3/tiktok/profile/videos", {"handle": "someone", "max_cursor": "12345"}),
])
def test_bad_cursors_are_bad_cursor(client, path, params):
    # tt_misc-4 / tt_video-3: the error code every other platform uses for a cursor it can't resume from
    r = client.get(path, params=params)
    assert r.status_code == 400 and r.json()["error"] == "bad_cursor", r.text[:200]
    assert "TikTok id" not in r.json()["message"]


class _Calls(list):
    settings: SimpleNamespace  # what socialscrape.transcribe sees as the server settings


@pytest.fixture
def fake_transcribe(client, fake_ydl, monkeypatch):
    """A caption-less post whose AI fallback goes through the real socialscrape.transcribe, with the audio download
    and the Whisper run recorded instead of done. Returns the calls: the route's kwargs plus `whisper` (the
    language Whisper got) once it ran."""
    from socialscrape import transcribe as core

    ydl, _ = fake_ydl
    item = _web_item()
    item["video"] = {**item["video"], "subtitleInfos": [], "claInfo": {}}
    ydl.ies["TikTok"] = _FakePost(*[(item, 0)] * 20)
    calls = _Calls()
    calls.settings = settings = SimpleNamespace(transcribe_backend="mlx-whisper", whisper_model="small")

    async def transcribe(url, **kw):
        calls.append(kw)
        return await core.transcribe(url, **kw)

    async def downloaded(platform, fn, **opts):
        return "/nonexistent/audio.m4a", {"acodec": "aac"}

    def backend(name, path, language):
        calls[-1]["whisper"] = language
        return language or "en", [Segment(0, 1, "hello there")]

    monkeypatch.setattr(tiktok, "transcribe", transcribe)
    monkeypatch.setattr(core, "get_settings", lambda: settings)
    monkeypatch.setattr(core, "installed", lambda backend: True)
    monkeypatch.setattr(core, "with_ydl", downloaded)
    monkeypatch.setattr(core, "_run_backend", backend)
    return calls


def _ai(client, **params):
    url = M.web_url("someone", "7300000000000000001")
    return client.get("/v1/tiktok/video/transcript", params={"url": url, "use_ai_as_fallback": "true", **params})


def test_ai_fallback_language(client, fake_transcribe):
    # tt_video-2: 'eng' went to Whisper as-is and crashed it (plain-text 500); the core's rules are in test_core
    # ISO 639-2/B codes ('ger', and 'per', which was read as 'pe') were a 400 'not supported'
    for given, code in (("eng", "en"), ("zho", "zh"), ("cmn", "zh"), ("ger", "de"), ("per", "fa"), (None, None)):
        r = _ai(client, **({"language": given} if given else {}))
        assert r.status_code == 200 and r.json()["transcript"] == "hello there", (given, r.text[:200])
        assert fake_transcribe[-1]["whisper"] == code
    fake_transcribe.clear()
    for bad in ("zz", "pe"):
        r = _ai(client, language=bad)
        assert r.status_code == 400 and r.json()["error"] == "bad_request", (bad, r.text[:200])
        assert "not supported" in r.json()["message"]
    r = _ai(client, language="yue")  # retest-tiktok-1: in Whisper's table, but small is known to lack it
    assert r.status_code == 400 and r.json()["error"] == "bad_request" and "Whisper model" in r.json()["message"]
    assert not any("whisper" in call for call in fake_transcribe)  # all refused before any audio download
    fake_transcribe.settings.transcribe_backend = "none"  # off: 501 before the language is even looked at
    r = _ai(client, language="zz")
    assert r.status_code == 501 and r.json()["error"] == "transcription_disabled"


def test_ai_fallback_allow_long_is_literal_true(client, fake_transcribe):
    # tt_video-1: ScrapeCreators only honours the literal 'true'
    for given, allowed in (("true", True), ("TRUE", False), ("True", False), ("1", False), ("yes", False),
                           (None, False)):
        _ai(client, **({"allow_long_transcription": given} if given else {}))
        assert fake_transcribe[-1]["allow_long"] is allowed, given
        assert fake_transcribe[-1]["duration"] == 15


_CHALLENGE = "<title>Please wait...</title>"  # SlardarWAF's JS challenge page: no SIGI state, no universal data


class _FakeJar:
    """Cookie names: yt-dlp's solver sets its two on .tiktok.com, TikTok its _waftokenid (for page `token`) on www."""

    def __init__(self):
        self.cookies, self.token = set(), None

    def __iter__(self):
        return iter([SimpleNamespace(name=n, domain=self._domain(n), path="/") for n in self.cookies])

    @staticmethod
    def _domain(name):
        return "www.tiktok.com" if name == "_waftokenid" else ".tiktok.com"

    def clear(self, domain, path, name):
        assert (domain, path) == (self._domain(name), "/")
        self.cookies.remove(name)


class _FakeWaf:
    """yt-dlp's challenge solver on a TikTok IE stand-in: the first `waf` fetches get the challenge page, as does
    every fetch after one when `stays`; solving sets the two challenge cookies, and the page they get sets TikTok's
    token for it, which another page answers with a 403."""

    def __init__(self, waf=0, stays=False):
        self.waf, self.stays, self.solved, self.cookiejar = waf, stays, 0, _FakeJar()

    def _page(self, url, name):
        jar = self.cookiejar
        if "_waftokenid" in jar.cookies and jar.token != url:
            raise ExtractorError("Unable to download webpage: HTTP Error 403: Forbidden", video_id=name)
        if self.waf or (self.stays and self.solved):
            self.waf = max(self.waf - 1, 0)
            return _CHALLENGE
        if "_wafchallengeid" in jar.cookies:
            jar.cookies.add("_waftokenid")
            jar.token = url
        return name

    def _solve_challenge_and_set_cookies(self, page):
        if page != _CHALLENGE:
            raise ExtractorError("Unexpected response from webpage request")
        self.solved += 1
        self.cookiejar.cookies |= {"_wafchallengeid", "waforiginalreid"}
        return "_wafchallengeid", "waforiginalreid"


class _FakeLive(_FakeWaf):
    """TikTokLiveIE stand-in: /@name/live pages keyed by name, room/info answers recorded. Requests are built as
    yt-dlp builds them (see _FakeFeed)."""

    def __init__(self, pages, room=None, **waf):
        super().__init__(**waf)
        self.pages, self.room, self.fetched = pages, room, []

    def _download_webpage(self, url, name, note, fatal=True):
        Request(url)
        self.fetched.append(url)
        return self._page(url, name)

    def _get_sigi_state(self, page, name):
        return self.pages.get(page) or {}

    def _get_universal_data(self, page, name):  # every page but the challenge has it, SIGI state or not
        return {} if page == _CHALLENGE else {"webapp.app-context": {}}

    def _download_json(self, url, room_id, note, fatal=True, query=None, **kwargs):
        Request(url, query=query)
        self.fetched.append(url)
        return self.room


def _live_state(name, status, **user):
    return {"LiveRoom": {"liveRoomUserInfo": {
        "user": {"id": "42", "uniqueId": name, "nickname": "Some One", "roomId": "77", "status": status, **user},
        "stats": {"followerCount": 9, "followingCount": 1},
        "liveRoom": {"status": status, "title": "on air", "startTime": 1700000000}}}}


def test_user_live_route(client, fake_ydl, monkeypatch):
    ydl, _ = fake_ydl
    looked_up = []
    monkeypatch.setattr(tiktok, "_user_page",
                        lambda ydl, name: looked_up.append(name) or _user_info("weathernewslive", roomId="77"))
    # the live page only has the user for the exact uniqueId; otherwise just CurrentRoom
    ydl.ies["TikTokLive"] = ie = _FakeLive({"weathernewslive": _live_state("weathernewslive", 2),
                                            "WeatherNewsLive": {"CurrentRoom": {}}, "42": {"CurrentRoom": {}}})
    expected = ok(client, "/v1/tiktok/user/live", handle="weathernewslive")
    assert expected["is_live"] is True and expected["liveRoom"]["status"] == 2 and looked_up == []
    assert expected["liveRoomUserInfo"]["followerCount"] == 9
    for handle in ("WeatherNewsLive", "@WeatherNewsLive", "42"):  # tt_misc-1: liveRoom used to be dropped
        ie.fetched.clear()
        body = ok(client, "/v1/tiktok/user/live", handle=handle)
        assert body == expected, handle
        assert ie.fetched == [f"https://www.tiktok.com/@{handle.lstrip('@')}/live",
                              "https://www.tiktok.com/@weathernewslive/live"]
    assert looked_up == ["WeatherNewsLive", "WeatherNewsLive", "42"]

    # tt_misc-2: an ended room still has the live page data, so liveRoom is there with status 4 (use is_live)
    ydl.ies["TikTokLive"] = _FakeLive({"weathernewslive": _live_state("weathernewslive", 4)})
    body = ok(client, "/v1/tiktok/user/live", handle="weathernewslive")
    assert body["is_live"] is False and body["liveRoom"]["status"] == 4

    # no live room on the page: profile page + room/info, and no liveRoom
    ydl.ies["TikTokLive"] = ie = _FakeLive({"weathernewslive": {"CurrentRoom": {}}}, room={"data": {"status": 4}})
    body = ok(client, "/v1/tiktok/user/live", handle="weathernewslive")
    assert body["is_live"] is False and "liveRoom" not in body
    assert body["liveRoomUserInfo"]["uniqueId"] == "weathernewslive"
    assert ie.fetched == ["https://www.tiktok.com/@weathernewslive/live", tiktok._ROOM_INFO]


def test_user_live_route_solves_the_live_pages_js_challenge(client, fake_ydl, monkeypatch):
    # tiktok-live-waf: /@name/live became SlardarWAF's "Please wait..." page, answered as is_live:true sans liveRoom
    ydl, _ = fake_ydl
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: _user_info(name, roomId="77"))
    ydl.ies["TikTokLive"] = ie = _FakeLive({"weathernewslive": _live_state("weathernewslive", 2)}, waf=1)
    body = ok(client, "/v1/tiktok/user/live", handle="weathernewslive")
    assert body["is_live"] is True and body["liveRoom"]["status"] == 2
    assert ie.fetched == ["https://www.tiktok.com/@weathernewslive/live"] * 2
    assert ie.solved == 1 and ie.cookiejar.cookies == {"_waftokenid"}  # the challenge cookies don't outlive it

    # tiktok-discovery: a live page that stays a challenge was a 503; the profile page and room/info still answer
    ydl.ies["TikTokLive"] = live = _FakeLive({"weathernewslive": _live_state("weathernewslive", 2)}, waf=1,
                                             stays=True, room={"data": {"status": 2}})
    body = ok(client, "/v1/tiktok/user/live", handle="weathernewslive")
    assert body["is_live"] is True and "liveRoom" not in body and body["liveRoomUserInfo"]["roomId"] == "77"
    assert live.fetched == ["https://www.tiktok.com/@weathernewslive/live"] * 2 + [tiktok._ROOM_INFO]
    assert live.cookiejar.cookies == set()

    def blocked(ydl, name):
        raise UpstreamBlocked("TikTok kept answering with its JS challenge (a bot check); retry later")

    monkeypatch.setattr(tiktok, "_user_page", blocked)  # the profile page too: then it is a 503
    ydl.ies["TikTokLive"] = live = _FakeLive({}, waf=1, stays=True, room={"data": {"status": 2}})
    r = client.get("/v1/tiktok/user/live", params={"handle": "weathernewslive"})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked"), r.text
    assert tiktok._ROOM_INFO not in live.fetched
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: _user_info(name, roomId="77"))

    # tiktok-live-bad-handle-503: a page with universal data but no SIGI state (what a handle TikTok's router
    # rejects gets, e.g. a-b) is no challenge: the profile page and room/info answer, as without the challenge
    ydl.ies["TikTokLive"] = live = _FakeLive({}, room={"data": {"status": 2}})
    body = ok(client, "/v1/tiktok/user/live", handle="weathernewslive")
    assert body["is_live"] is True and "liveRoom" not in body and live.solved == 0
    assert live.fetched == ["https://www.tiktok.com/@weathernewslive/live", tiktok._ROOM_INFO]

    def no_user(ydl, name):
        raise NotFound("TikTok user not found (status 10221)")

    monkeypatch.setattr(tiktok, "_user_page", no_user)  # so a handle nobody has is a 404, as on /v1/tiktok/profile
    ydl.ies["TikTokLive"] = _FakeLive({})
    r = client.get("/v1/tiktok/user/live", params={"handle": "a-b"})
    assert (r.status_code, r.json()["error"]) == (404, "not_found"), r.text


def test_user_live_route_asks_only_for_a_handle_and_a_room_id(client, fake_ydl, monkeypatch):
    # The profile page's uniqueId and roomId went into the next request as they were: half an emoji in either failed
    # as yt-dlp built it, a 500, and a uniqueId with a '/' or a '?' asked for some other page
    ydl, _ = fake_ydl
    for unique in ("\ud83d", "a\ud83d", "a/b", "a?x=1", "a#b", "a b", "x" * 65, {"a": 1}, ["a"]):
        ydl.ies["TikTokLive"] = live = _FakeLive({}, room={"data": {"status": 2}})
        monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name, unique=unique: _user_info(unique, roomId="77"))
        body = ok(client, "/v1/tiktok/user/live", handle="someone")
        assert body["is_live"] is True and "liveRoom" not in body, unique
        assert live.fetched == ["https://www.tiktok.com/@someone/live", tiktok._ROOM_INFO], unique
    for room_id in ("\ud83d", "7\ud83d", "a/b", "77 ", True, {"a": 1}, ["77"], 7.5, "", None, 0):
        ydl.ies["TikTokLive"] = live = _FakeLive({}, room={"data": {"status": 2}})
        monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name, room_id=room_id: _user_info(name, roomId=room_id))
        body = ok(client, "/v1/tiktok/user/live", handle="someone")
        assert body["is_live"] is False and live.fetched == ["https://www.tiktok.com/@someone/live"], room_id
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: _user_info(name, roomId=77))  # a number is an id
    assert ok(client, "/v1/tiktok/user/live", handle="someone")["is_live"] is True and tiktok._ROOM_INFO in live.fetched


def test_profile_feeds_without_a_sec_uid_yt_dlp_can_send_are_an_upstream_error(client, fake_ydl, monkeypatch):
    # Half an emoji in the profile's secUid failed as yt-dlp built the feed's request: a 500 "Internal server error"
    ydl, _ = fake_ydl
    ydl.ies["TikTokUser"] = feed = _FakeFeed([int(time.time()) - 60])
    for sec_uid in ("\ud83d", "MS4w\ud83d", "MS4w LjAB", "MS4w\n", "", {"a": 1}, ["MS4w"], 7, True):
        monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name, sec_uid=sec_uid: _user_info(name, secUid=sec_uid))
        for path in ("/v3/tiktok/profile/videos", "/v1/tiktok/profile/region"):
            tiktok._profiles.clear()
            r = client.get(path, params={"handle": "someone"})
            assert (r.status_code, r.json()["error"], r.json()["message"]) == (
                500, "upstream_error", "TikTok profile has no secUid"), (sec_uid, path)
    assert feed.calls == 0


def test_user_live_route_drops_the_live_pages_waf_token(client, fake_ydl):
    # tiktok-waf-token: past the live page's challenge, TikTok's token for it got the profile page a 403 (and the
    # other way round), so /v1/tiktok/user/live was a 403 for anyone not live
    ydl, _ = fake_ydl
    user = {"id": "42", "uniqueId": "someone", "secUid": "sec", "roomId": "77"}
    ydl.ies["TikTokLive"] = live = _FakeLive({"someone": {"CurrentRoom": {}}}, room={"data": {"status": 4}}, waf=1)
    ydl.ies["TikTokUser"] = page = _FakeUserPage({"someone": _user_detail(0, user=user, stats={})}, waf=1)
    page.cookiejar = live.cookiejar  # one yt-dlp session's
    body = ok(client, "/v1/tiktok/user/live", handle="someone")
    assert body["is_live"] is False and body["liveRoomUserInfo"]["id"] == "42"
    assert live.solved == page.solved == 1 and live.cookiejar.token == "https://www.tiktok.com/@someone"
    assert page.fetched == ["https://www.tiktok.com/@someone"] * 2

    ydl.ies["TikTokUser"] = page = _FakeUserPage({"Someone": _user_detail(0, user=user, stats={})}, waf=1)
    ydl.ies["TikTokLive"] = live = _FakeLive({"someone": _live_state("someone", 2)})
    page.cookiejar = live.cookiejar  # the live page for the real uniqueId comes after the profile page's challenge
    body = ok(client, "/v1/tiktok/user/live", handle="Someone")
    assert body["is_live"] is True and body["liveRoom"]["status"] == 2 and page.solved == 1
    assert live.fetched == ["https://www.tiktok.com/@Someone/live", "https://www.tiktok.com/@someone/live"]


class _FakeUserPage(_FakeWaf):
    """TikTokUserIE stand-in for _user_page: the user page's universal data keyed by handle."""

    _UPLOADER_URL_FORMAT = "https://www.tiktok.com/@%s"

    def __init__(self, pages, **waf):
        super().__init__(**waf)
        self.pages, self.fetched = pages, []

    def _generate_blockbuster_headers(self):
        return {"User-Agent": "x"}

    def _download_webpage(self, url, name, note, impersonate=False, headers=None):
        assert impersonate and headers
        self.fetched.append(url)
        return self._page(url, name)

    def _get_universal_data(self, page, name):
        return self.pages.get(page) or {}


def _user_detail(status, **info):
    return {"webapp.user-detail": {"statusCode": status, "userInfo": info}}


def test_user_page_challenge_and_status_codes(fake_ydl):
    ydl, _ = fake_ydl
    user = {"id": "1", "uniqueId": "someone", "secUid": "sec"}
    ydl.ies["TikTokUser"] = ie = _FakeUserPage({"someone": _user_detail(0, user=user, stats={})}, waf=1)
    assert tiktok._user_page(ydl, "someone")["user"] == user  # tiktok-live-waf: the user page gets solved too
    assert ie.solved == 1 and ie.cookiejar.cookies == {"_waftokenid"} and len(ie.fetched) == 2
    ydl.ies["TikTokUser"] = _FakeUserPage({"someone": _user_detail(0, user=user)}, waf=1, stays=True)
    with pytest.raises(UpstreamBlocked):
        tiktok._user_page(ydl, "someone")

    # tiktok-status-10102: a user-detail without a user, by statusCode
    for status, error in ((10102, UpstreamBlocked), (10000, UpstreamBlocked), (10222, Forbidden),
                          (10221, NotFound), (10202, NotFound), (0, NotFound)):
        ydl.ies["TikTokUser"] = _FakeUserPage({"someone": _user_detail(status)})
        with pytest.raises(error):
            tiktok._user_page(ydl, "someone")


@pytest.mark.parametrize("path,params", [("/v1/tiktok/profile", {"handle": "someone"}),
                                         ("/v2/tiktok/video", {"url": STOOL_VIDEO})], ids=["profile", "video"])
def test_a_challenge_page_with_a_character_reference_yt_dlp_cant_read_is_503(client, monkeypatch, path, params):
    """yt-html-entity-500: yt-dlp reads the JS challenge page's data element as text, unescaped, and a character
    reference in it past a C int ('&#2147483648;') was a 500 (OverflowError) wherever a tiktok.com page is read:
    our user and live pages, yt-dlp's post page. It reads as its text, so the page is a challenge yt-dlp can't
    solve: a 503, as one with a readable reference is."""
    for text in ("&#2147483648;", "&#38;"):
        page = f'<html><title>Please wait...</title><p id="cs" class="e30">{text}</p></html>'
        monkeypatch.setattr(TikTokBaseIE, "_download_webpage_handle", lambda self, url, *args, page=page, **kwargs: (
            page, SimpleNamespace(url=url, extensions={})))  # the page, not a redirect to /login
        r = client.get(path, params=params)
        assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked"), (text, r.text)
        assert "Unable to extract challenge data" in r.json()["message"]


def test_pages_and_feeds_of_another_shape(fake_ydl):
    # fix14: an item_list, user page or live page whose fields TikTok sent as another type (an itemList entry
    # null, a statusCode list, a userInfo string) was a 500
    for status in ([10202], {"code": 10202}, "10202"):
        feed = _FakeFeed([])
        feed._download_json = lambda *args, **kwargs: {"statusCode": status, "itemList": []}
        with pytest.raises(UpstreamError):
            tiktok._feed_page(feed, "sec", "h", 1_700_000_000_000, "US")
        assert tiktok._status_error(status) is None
    for body in ([], "x", None):
        feed._download_json = lambda *args, **kwargs: body
        with pytest.raises(UpstreamError, match="no video list"):
            tiktok._feed_page(feed, "sec", "h", 1_700_000_000_000, "US")
    when = 1_699_999_999
    feed._download_json = lambda *args, **kwargs: {"statusCode": 0, "hasMorePrevious": False, "itemList": [
        None, "x", 7, {"id": ["5"], "createTime": when}, {"id": {"5": 1}, "createTime": when},
        {"id": "x5", "createTime": when}, {"id": "5", "createTime": [1]}, {"id": "5", "createTime": when}]}
    items, cursor = tiktok._feed_page(feed, "sec", "h", 1_700_000_000_000, "US")
    assert items == [{"id": "5", "createTime": when}] and cursor is None
    feed._download_json = lambda *args, **kwargs: {"statusCode": 0, "itemList": 7}
    assert tiktok._feed_page(feed, "sec", "h", 1_700_000_000_000, "US") == ([], None)

    ydl, _ = fake_ydl
    for info in ("x", [1], {"user": "someone"}, {"user": [{"id": "1"}]}):
        ydl.ies["TikTokUser"] = _FakeUserPage({"someone": {"webapp.user-detail": {"statusCode": 0, "userInfo": info}}})
        with pytest.raises(NotFound):
            tiktok._user_page(ydl, "someone")
    user = {"id": "1", "uniqueId": "someone", "secUid": "sec"}
    ydl.ies["TikTokUser"] = _FakeUserPage({"someone": _user_detail(0, user=user, stats="x")})
    assert tiktok._profile(ydl, "someone")["videoCount"] is None
    for state in ({"LiveRoom": "x"}, {"LiveRoom": {"liveRoomUserInfo": [1]}},
                  {"LiveRoom": {"liveRoomUserInfo": {"user": "someone"}}}):
        assert tiktok._live_page(_FakeLive({"someone": state}), "someone") is None

    def music_page(ydl, mid, cursor):
        return {"aweme_list": [None, "x", {"aweme_id": "8", "author": "someone"},
                               {"aweme_id": "9", "author": {"uid": "42", "region": "US"}}]}

    tiktok_music_page, tiktok._music_page = tiktok._music_page, music_page
    try:
        own = {"id": "5", "music": {"id": "555", "original": True, "authorName": "Some One"}, "author": "x"}
        assert tiktok._account_region(None, {"id": "42", "nickname": "Some One"}, [{"music": "x"}, own]) == "US"
        assert tiktok._app_aweme(None, _web_item(music="x")) is None
        assert tiktok._app_aweme(None, _web_item(id="9")) is None  # the entry has no video object
    finally:
        tiktok._music_page = tiktok_music_page


def test_routes_answer_responses_of_another_shape(client, fake_ydl, monkeypatch):
    # fix14: the same on the song, collection, live and user/live routes
    ydl, _ = fake_ydl
    monkeypatch.setattr(tiktok, "_music_page", lambda ydl, mid, cursor: {
        "aweme_list": [None, "x", {"music": "x"}, {"aweme_id": "1", "music": {"id_str": CLIP_ID, "title": "t"}}]})
    assert ok(client, "/v1/tiktok/song", clipId=CLIP_ID)["music_info"] == {"id_str": CLIP_ID, "title": "t"}
    monkeypatch.setattr(tiktok, "_music_page", lambda ydl, mid, cursor: {"aweme_list": 7})
    r = client.get("/v1/tiktok/song", params={"clipId": CLIP_ID})
    assert r.status_code == 404 and r.json()["error"] == "not_found", r.text
    ydl.ies["TikTokCollection"] = _FakeCollection({"statusCode": 0, "hasMore": False, "itemList": 7})
    assert ok(client, "/v1/tiktok/collection/videos", url=COLLECTION)["videos"] == []
    ydl.ies["TikTokCollection"] = _FakeCollection({"statusCode": [100001], "itemList": []})
    r = client.get("/v1/tiktok/collection/videos", params={"url": COLLECTION})
    assert (r.status_code, r.json()["error"]) == (500, "upstream_error"), r.text
    ydl.ies["TikTokLive"] = _FakeLive({}, room={"data": {"id_str": "99", "owner": "7", "status": 2}})
    r = client.get("/v1/tiktok/live", params={"room_id": "99", "user_id": "7"})
    assert r.status_code == 404 and r.json()["message"] == "This live room does not belong to user_id", r.text
    ydl.ies["TikTokLive"] = _FakeLive({}, room={"data": {"id_str": "99", "owner": {"id_str": "7"}, "cover": "x"}})
    assert ok(client, "/v1/tiktok/live", room_id="99", user_id="7")["room_id"] == "99"

    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: _user_info(name, roomId="77", uniqueId=["x"]))
    for room in ({"data": "x"}, [1], "x"):
        ydl.ies["TikTokLive"] = _FakeLive({}, room=room)
        body = ok(client, "/v1/tiktok/user/live", handle="someone")
        assert body["is_live"] is False and body["liveRoomUserInfo"]["roomId"] == "77"
    state = _live_state("someone", 2)
    state["LiveRoom"]["liveRoomUserInfo"].update(liveRoom="x", stats=[9])
    ydl.ies["TikTokLive"] = _FakeLive({"someone": state})
    body = ok(client, "/v1/tiktok/user/live", handle="someone")
    assert body["is_live"] is True and "liveRoom" not in body and "followerCount" not in body["liveRoomUserInfo"]


# --- offline: throttled and bot-checked TikTok answers (tiktok-discovery) ----------------------------------------
# Production (a server IP, every platform at once) got /v3/tiktok/profile/videos, /v1/tiktok/collection/videos and
# /v1/tiktok/profile/region as 500 "Failed to parse JSON" while the same calls pass from a residential IP: TikTok's
# /api/ endpoints answered with something other than JSON while its pages still loaded. These feed those answers to
# yt-dlp's real extractors (only _download_webpage_handle is replaced) through the real with_ydl.

def _post_page(item: dict) -> str:
    """A TikTok post page as TikTokIE._extract_web_data_and_status reads it: just its universal data."""
    data = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"statusCode": 0, "itemInfo": {"itemStruct": item}}}}
    return f'<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(data)}</script>'


def _http(status: int) -> ExtractorError:
    response = Response(io.BytesIO(b""), "https://www.tiktok.com/api/x", {}, status=status)
    return ExtractorError(f"Unable to download JSON metadata: HTTP Error {status}", cause=HTTPError(response))


class _Net:
    """TikTok as yt-dlp's TikTok extractors download it: `routes` maps a URL prefix to its answers, given in turn
    (the last one repeats); an exception is raised. `asked` records each URL (sans query) with the names of the
    cookies yt-dlp would send to it."""

    def __init__(self, monkeypatch, routes: dict[str, list]):
        self.routes, self.asked = {k: list(v) for k, v in routes.items()}, []

        def download(ie, url, video_id, note=None, errnote=None, fatal=True, encoding=None, data=None, headers={},
                     query={}, expected_status=None, impersonate=None, require_impersonation=False):
            self.asked.append((url, set(ie._get_cookies(url))))
            answers = next(v for k, v in self.routes.items() if url.startswith(k))
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(answer, Exception):
                raise answer
            return answer, SimpleNamespace(url=url, extensions={}, headers={}, status=200)

        monkeypatch.setattr(TikTokBaseIE, "_download_webpage_handle", download)

    def urls(self, prefix: str = "") -> list[str]:
        return [url for url, _ in self.asked if url.startswith(prefix)]


_ITEM_LIST = "https://www.tiktok.com/api/creator/item_list/"
_COLLECTION_API = "https://www.tiktok.com/api/collection/item_list/"
_STOOL_EMBED = "https://www.tiktok.com/embed/@stoolpresidente"
_STOOL_POSTS = {f"https://www.tiktok.com/@stoolpresidente/video/{it['id']}": [_post_page(it)]
                for it in _FEED["itemList"]}


@pytest.fixture
def real_ydl(monkeypatch):
    """The real with_ydl and yt-dlp extractors; no waiting between asks; a stand-in user page for @stoolpresidente."""
    monkeypatch.setattr(tiktok, "_RETRY_DELAY", 0)
    monkeypatch.setattr(tiktok, "_profiles", {})
    author = _FEED["itemList"][0]["author"]
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: {
        "user": {"id": author["id"], "uniqueId": author["uniqueId"], "secUid": author["secUid"],
                 "nickname": author["nickname"]}, "stats": {"videoCount": 2577}, "statusCode": 0})


def test_web_api_gets_past_throttled_and_bot_checked_answers(monkeypatch):
    import yt_dlp
    from socialscrape.ytdlp import base_options

    slept = []
    monkeypatch.setattr(tiktok.time, "sleep", slept.append)
    ok_json, refused = json.dumps({"statusCode": 0, "itemList": []}), json.dumps({"statusCode": 10102})

    def ask(*answers):
        ask.net = _Net(monkeypatch, {_ITEM_LIST: list(answers)})
        slept.clear()
        with yt_dlp.YoutubeDL(base_options("tiktok")) as ydl:
            ie = ydl.get_info_extractor("TikTokUser")
            try:
                return tiktok._web_api(ie, _ITEM_LIST, "h", "Downloading video list", {"secUid": "s"}, "video list")
            finally:  # the challenge's cookies don't outlive the ask they were solved for
                assert not {"_wafchallengeid", "waforiginalreid"} & {c.name for c in ie.cookiejar}

    # throttled (empty), then JSON: asked again after 1.5 s
    assert ask("", ok_json) == {"statusCode": 0, "itemList": []} and slept == [1.5]
    # SlardarWAF's challenge in place of JSON: solved by yt-dlp, asked again with its cookies, which are then dropped
    assert ask(_WAF_PAGE, ok_json)["statusCode"] == 0 and slept == []
    assert [cookies for _, cookies in ask.net.asked] == [set(), {"_wafchallengeid", "waforiginalreid"}]
    # a refusal of the anonymous request is asked again; the last one is TikTok's answer for _status_error
    assert ask(refused, ok_json)["statusCode"] == 0 and slept == [1.5]
    assert ask(refused)["statusCode"] == 10102 and slept == [1.5, 3.0] and len(ask.net.asked) == 3
    # what never turns into JSON is a 503 naming what came back (3 asks, backing off), never a 500
    for answers, what in (([""], "an empty body"), ([_BLOCK_PAGE], 'an HTML page ("Access Denied")'),
                          (["{\"itemList\": [{\"id\""], "a 19-character body"),
                          ([_WAF_PAGE], 'its JS challenge page, then an HTML page ("Please wait...")'),
                          ([_http(429)], "HTTP 429"), ([_http(503)], "HTTP 503")):
        with pytest.raises(UpstreamBlocked) as exc:
            ask(*answers)
        assert f"TikTok answered the video list request with {what} instead of JSON, 3 times" in exc.value.message
        assert slept == [1.5, 3.0] and len(ask.net.urls(_ITEM_LIST)) == 3 * (2 if answers == [_WAF_PAGE] else 1)
    # other HTTP errors are yt-dlp's (with_ydl translates them), asked once
    with pytest.raises(ExtractorError, match="HTTP Error 404"):
        ask(_http(404))
    assert len(ask.net.asked) == 1 and slept == []


def test_profile_videos_from_a_bot_checked_item_list(client, real_ydl, monkeypatch):
    # tiktok-discovery: the challenge in place of item_list's JSON is solved, and the recorded page served
    net = _Net(monkeypatch, {_ITEM_LIST: [_WAF_PAGE, json.dumps(_FEED)]})
    body = ok(client, "/v3/tiktok/profile/videos", handle="stoolpresidente", sort_by="latest")
    assert [a["aweme_id"] for a in body["aweme_list"]] == [it["id"] for it in _FEED["itemList"]]
    assert body["aweme_list"][0]["statistics"]["digg_count"] == 4958 and body["has_more"] is True
    assert net.urls() == [_ITEM_LIST, _ITEM_LIST]
    assert_conforms(body, "/v3/tiktok/profile/videos")


def test_profile_videos_fall_back_to_the_embed_page(client, real_ydl, monkeypatch):
    # tiktok-discovery: item_list still not JSON after 3 asks: the embed page's posts, read from their own pages
    net = _Net(monkeypatch, {_ITEM_LIST: [_BLOCK_PAGE], _STOOL_EMBED: [_EMBED_PAGE], **_STOOL_POSTS})
    body = ok(client, "/v3/tiktok/profile/videos", handle="stoolpresidente", sort_by="latest")
    feed = [tiktok.M.aweme(it) for it in _FEED["itemList"]]
    assert body["aweme_list"] == feed  # the post objects item_list itself serves: dates, likes, comments
    assert [(a["create_time"], a["statistics"]["play_count"], a["statistics"]["comment_count"])
            for a in body["aweme_list"]][0] == (1792725755, 86800, 467)
    assert body["has_more"] is True and body["max_cursor"] == 1792620444 * 1000 - 1
    assert net.urls() == [_ITEM_LIST] * 3 + [_STOOL_EMBED, *_STOOL_POSTS]
    assert_conforms(body, "/v3/tiktok/profile/videos")

    # the next page, within item_list's cooling window: straight to the embed, which has nothing older than the
    # cursor (only the post uploaded before it is read again)
    net = _Net(monkeypatch, {_ITEM_LIST: [""], _STOOL_EMBED: [_EMBED_PAGE], **_STOOL_POSTS})
    r = client.get("/v3/tiktok/profile/videos", params={"handle": "stoolpresidente",
                                                        "max_cursor": str(body["max_cursor"])})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked"), r.text
    assert "not asking again until it cools down" in r.json()["message"] and int(r.headers["Retry-After"]) <= 60
    assert net.urls() == [_STOOL_EMBED, list(_STOOL_POSTS)[-1]]

    # once the window is over item_list is asked again
    tiktok._cooling.clear()
    net = _Net(monkeypatch, {_ITEM_LIST: [""], _STOOL_EMBED: [_EMBED_PAGE], **_STOOL_POSTS})
    r = client.get("/v3/tiktok/profile/videos", params={"handle": "stoolpresidente",
                                                        "max_cursor": str(body["max_cursor"])})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked"), r.text
    assert "video list request with an empty body instead of JSON, 3 times" in r.json()["message"]
    assert net.urls() == [_ITEM_LIST] * 3 + [_STOOL_EMBED, list(_STOOL_POSTS)[-1]]  # uploaded before the cursor

    # the embed page blocked too: item_list's 503, not a 500
    tiktok._cooling.clear()
    net = _Net(monkeypatch, {_ITEM_LIST: [_BLOCK_PAGE], _STOOL_EMBED: [_WAF_PAGE]})
    r = client.get("/v3/tiktok/profile/videos", params={"handle": "stoolpresidente"})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked"), r.text
    assert 'with an HTML page ("Access Denied") instead of JSON' in r.json()["message"]


def _embed_posts(monkeypatch, posts: dict[str, int], answers: dict[str, Exception] | None = None) -> list[str]:
    """The embed page lists `posts` (id -> createTime) in that order; reading one raises its `answers` entry. Returns
    the list of ids read, in order."""
    read = []
    monkeypatch.setattr(tiktok, "_embed_ids", lambda ydl, name: list(posts))

    def load_post(ydl, url):
        aweme_id = url.rsplit("/", 1)[1]
        read.append(aweme_id)
        if aweme_id in (answers or {}):
            raise answers[aweme_id]
        return None, {"id": aweme_id, "createTime": posts[aweme_id]}

    monkeypatch.setattr(tiktok, "_load_post", load_post)
    return read


def _tid(created: int, n: int = 1) -> str:
    """A real-shaped post id: its top 32 bits are its upload time."""
    return str(created << 32 | n)


def test_embed_feed_page_leaves_a_pinned_post_for_item_lists_date_order(monkeypatch):
    now = 1_792_800_000
    pinned, a, b, c = _tid(now - 400 * 86_400), _tid(now - 60), _tid(now - 120), _tid(now - 7200)
    gone = _tid(now - 9000)
    posts = {pinned: now - 400 * 86_400, a: now - 60, b: now - 120, c: now - 7200, gone: now - 9000}
    read = _embed_posts(monkeypatch, posts, {gone: NotFound("TikTok post not available (status 10201)")})
    items, cursor = tiktok._embed_feed_page(None, "h", now * 1000, 50)
    assert [it["id"] for it in items] == [a, b, c] and cursor == (now - 7200) * 1000 - 1
    assert read == [pinned, a, b, c, gone]  # the removed one is passed over
    assert tiktok._embed_feed_page(None, "h", now * 1000, 3)[1] is None  # every post served: the last page
    assert tiktok._embed_feed_page(None, "h", now * 1000, 4)[1] == cursor  # the pinned one is still to come
    items, _ = tiktok._embed_feed_page(None, "h", (now - 100) * 1000, None)
    assert [it["id"] for it in items] == [b, c]
    # verify-merged: the next page, item_list still blocked, served the pinned post alone and set max_cursor 400 days
    # back, past the ~46 posts in between. Out of date order over the whole list (the posts newer than the cursor at
    # their upload time, unread), it is left for item_list.
    read.clear()
    assert tiktok._embed_feed_page(None, "h", cursor, 50) is None and read == [pinned, gone]
    assert tiktok._embed_feed_page(None, "h", (now - 400 * 86_400 - 1) * 1000, None) is None


def test_embed_feed_page_serves_a_pinned_post_within_its_run(monkeypatch):
    """A pinned post dated among the embed's own posts is served in its date place (paging past it would skip it)."""
    now = 1_792_800_000
    a, b, pinned, c = _tid(now - 60), _tid(now - 120), _tid(now - 150), _tid(now - 200)
    _embed_posts(monkeypatch, {pinned: now - 150, a: now - 60, b: now - 120, c: now - 200})
    items, cursor = tiktok._embed_feed_page(None, "h", now * 1000, 9)
    assert [it["id"] for it in items] == [a, b, pinned, c] and cursor == (now - 200) * 1000 - 1
    assert tiktok._embed_feed_page(None, "h", now * 1000, 4)[1] is None


def test_embed_feed_page_ends_before_a_post_it_couldnt_read(monkeypatch):
    """verify-load tt-embed-10204: a listed post answering 10204 (as TikTok answers a throttled IP) was skipped and
    max_cursor set below it, so no page ever returned it. The page now ends before it."""
    now = 1_792_800_000
    a, b, x, c = _tid(now - 60), _tid(now - 120), _tid(now - 180), _tid(now - 240)
    posts = {a: now - 60, b: now - 120, x: now - 180, c: now - 240}
    for failure in (tiktok._Unavailable("TikTok post not available (status 10204)"),
                    UpstreamBlocked("TikTok kept answering with its JS challenge (a bot check); retry later")):
        read = _embed_posts(monkeypatch, posts, {x: failure})
        items, cursor = tiktok._embed_feed_page(None, "h", now * 1000, 4)  # videoCount says this is everything
        assert [it["id"] for it in items] == [a, b] and cursor == (now - 120) * 1000 - 1 and read == [a, b, x]
        assert (int(x) >> 32) * 1000 <= cursor  # the next page asks for it again
        # the first post unreadable: nothing to serve, so the caller's 503 stands
        read = _embed_posts(monkeypatch, posts, {a: failure})
        assert tiktok._embed_feed_page(None, "h", now * 1000, 4) is None and read == [a]


def test_embed_feed_page_stops_at_its_deadline(monkeypatch):
    now = 1_792_800_000
    a, b, c = _tid(now - 60), _tid(now - 120), _tid(now - 180)
    read = _embed_posts(monkeypatch, {a: now - 60, b: now - 120, c: now - 180})
    clock = iter([0.0, 10.0, 31.0])
    monkeypatch.setattr(tiktok.time, "monotonic", lambda: next(clock))
    items, cursor = tiktok._embed_feed_page(None, "h", now * 1000, 3, deadline=30.0)
    assert [it["id"] for it in items] == [a, b] and cursor == (now - 120) * 1000 - 1 and read == [a, b]


def test_embed_own_sound_posts_pass_over_unreadable_posts(monkeypatch):
    now = 1_792_800_000
    own = {"music": {"id": "555", "original": True, "authorName": "Some One"}}
    a, b, c = _tid(now - 60), _tid(now - 120), _tid(now - 180)
    monkeypatch.setattr(tiktok, "_embed_ids", lambda ydl, name: [a, b, c])

    def load_post(ydl, url):
        aweme_id = url.rsplit("/", 1)[1]
        if aweme_id in failing:
            raise tiktok._Unavailable("TikTok post not available (status 10204)")
        return None, {"id": aweme_id, "createTime": now, **own}

    monkeypatch.setattr(tiktok, "_load_post", load_post)
    prof = {"nickname": "Some One"}
    failing = {a}
    assert [it["id"] for it in tiktok._embed_own_sound_posts(None, "h", prof)] == [b, c]
    failing = {a, b, c}  # none read: the 503 stands rather than a 404 region_unavailable
    assert tiktok._embed_own_sound_posts(None, "h", prof) is None


def test_item_list_cools_down_after_a_block(monkeypatch):
    """verify-load: every call under a blocked item_list repeated 3 asks and 4.5 s of backoff before the embed page."""
    monkeypatch.setattr(tiktok, "_RETRY_DELAY", 0)
    clock = [1000.0]
    monkeypatch.setattr(tiktok.time, "monotonic", lambda: clock[0])
    asked = []

    def ie(answer, proxy=None):
        feed = _FakeFeed([])
        feed._downloader = SimpleNamespace(params={"proxy": proxy} if proxy else {})

        def download_json(*args, **kwargs):
            asked.append(proxy)
            if isinstance(answer, Exception):
                raise answer
            return answer
        feed._download_json = download_json
        return feed

    empty = ExtractorError("Failed to parse JSON", cause=json.JSONDecodeError("Expecting value", "", 0))
    with pytest.raises(UpstreamBlocked, match="instead of JSON, 3 times"):
        tiktok._feed_page(ie(empty), "sec", "h", 1_700_000_000_000, "US")
    assert len(asked) == 3
    with pytest.raises(UpstreamBlocked, match="until it cools down") as exc:  # within the window: not asked
        tiktok._feed_page(ie({"statusCode": 0, "itemList": []}), "sec", "h", 1_700_000_000_000, "US")
    assert len(asked) == 3 and exc.value.retry_after == 60
    # another proxy has its own window
    assert tiktok._feed_page(ie({"statusCode": 0, "itemList": []}, "http://p2:8080"), "sec", "h",
                             1_700_000_000_000, "US") == ([], None)
    # blocked again right after the window: twice as long; a JSON answer later ends it
    clock[0] += 61
    with pytest.raises(UpstreamBlocked, match="instead of JSON"):
        tiktok._feed_page(ie(empty), "sec", "h", 1_700_000_000_000, "US")
    assert tiktok._cooling[""][1] == 120
    clock[0] += 121
    assert tiktok._feed_page(ie({"statusCode": 0, "itemList": []}), "sec", "h", 1_700_000_000_000, "US") == ([], None)
    assert "" not in tiktok._cooling
    # TikTok still refusing the anonymous request after 3 asks cools down too
    with pytest.raises(UpstreamBlocked, match="status 10102"):
        tiktok._feed_page(ie({"statusCode": 10102}), "sec", "h", 1_700_000_000_000, "US")
    assert tiktok._cooling[""][1] == 60


def test_profile_region_from_a_blocked_item_list(client, real_ydl, monkeypatch):
    # tiktok-discovery: /v1/tiktok/profile/region was a 500 "Failed to parse JSON"; also at /v1/tiktok/profile-region
    looked_up = []

    def music_page(ydl, mid, cursor):
        looked_up.append(mid)
        return {"aweme_list": [{"aweme_id": "1", "author": {"uid": _FEED["itemList"][0]["author"]["id"],
                                                            "region": "US"}}]}

    monkeypatch.setattr(tiktok, "_music_page", music_page)
    net = _Net(monkeypatch, {_ITEM_LIST: [_BLOCK_PAGE], _STOOL_EMBED: [_EMBED_PAGE], **_STOOL_POSTS})
    r = client.get("/v1/tiktok/profile-region", params={"handle": "stoolpresidente"}, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/v1/tiktok/profile/region?handle=stoolpresidente"
    body = ok(client, "/v1/tiktok/profile-region", handle="stoolpresidente")
    assert (body["handle"], body["region"]) == ("stoolpresidente", "US")
    assert looked_up == [_FEED["itemList"][0]["music"]["id"]]
    assert net.urls() == [_ITEM_LIST] * 3 + [_STOOL_EMBED, *list(_STOOL_POSTS)[:2]]  # read until two use its sound
    assert_conforms(body, "/v1/tiktok/profile/region")


def test_profile_videos_page_ends_before_a_post_answering_10204(client, real_ydl, monkeypatch):
    """verify-load tt-embed-10204: item_list blocked and the embed's third post answering 10204 (twice, as a
    throttled IP gets it) gave 200 without that post and a max_cursor below it."""
    unavailable = _post_page({}).replace('"statusCode": 0', '"statusCode": 10204')
    third = list(_STOOL_POSTS)[2]
    net = _Net(monkeypatch, {_ITEM_LIST: [_BLOCK_PAGE], _STOOL_EMBED: [_EMBED_PAGE], **_STOOL_POSTS,
                             third: [unavailable]})
    body = ok(client, "/v3/tiktok/profile/videos", handle="stoolpresidente")
    assert [a["aweme_id"] for a in body["aweme_list"]] == [it["id"] for it in _FEED["itemList"][:2]]
    created = _FEED["itemList"][1]["createTime"]
    assert body["has_more"] is True and body["max_cursor"] == int(created) * 1000 - 1
    missing = third.rsplit("/", 1)[1]
    assert (int(missing) >> 32) * 1000 <= body["max_cursor"]  # the next page asks for it again
    assert net.urls(third) == [third, third]  # _load_post's one retry, then the page ends


def test_profile_region_answers_are_reused_briefly(client, real_ydl, monkeypatch):
    """verify-consumer: Stratton retries /v1/tiktok/profile/region's failure at /v1/tiktok/profile-region at once,
    which repeated every TikTok request (5-10 more) into a throttled IP. The handle's answer is reused for 90 s."""
    monkeypatch.setattr(tiktok, "_music_page", lambda ydl, mid, cursor: {"aweme_list": []})
    for embed, status, code in (([_WAF_PAGE], 503, "upstream_blocked"), ([_EMBED_PAGE], 404, "region_unavailable")):
        tiktok._regions.clear()
        tiktok._cooling.clear()
        net = _Net(monkeypatch, {_ITEM_LIST: [_BLOCK_PAGE], _STOOL_EMBED: embed, **_STOOL_POSTS})
        r = client.get("/v1/tiktok/profile/region", params={"handle": "stoolpresidente"})
        assert (r.status_code, r.json()["error"]) == (status, code), r.text
        asked = len(net.asked)
        again = client.get("/v1/tiktok/profile-region", params={"handle": "StoolPresidente"})
        assert (again.status_code, again.json()["error"], again.json()["message"]) == (
            status, code, r.json()["message"])
        assert len(net.asked) == asked  # answered without asking TikTok
        if status == 503:
            assert 0 < int(again.headers["Retry-After"]) <= 90
    tiktok._regions.clear()
    tiktok._cooling.clear()
    monkeypatch.setattr(tiktok, "_music_page", lambda ydl, mid, cursor: {"aweme_list": [
        {"aweme_id": "1", "author": {"uid": _FEED["itemList"][0]["author"]["id"], "region": "US"}}]})
    net = _Net(monkeypatch, {_ITEM_LIST: [json.dumps(_FEED)]})
    assert ok(client, "/v1/tiktok/profile/region", handle="stoolpresidente")["region"] == "US"
    assert ok(client, "/v1/tiktok/profile-region", handle="stoolpresidente")["region"] == "US"
    assert net.urls() == [_ITEM_LIST]


def test_collection_from_throttled_and_bot_checked_answers(client, real_ydl, monkeypatch):
    # tiktok-discovery: /v1/tiktok/collection/videos was a 500 "Failed to parse JSON"
    page = json.dumps({"statusCode": 0, "hasMore": True, "cursor": 30, "itemList": _FEED["itemList"][:1]})
    net = _Net(monkeypatch, {_COLLECTION_API: ["", _WAF_PAGE, page]})
    body = ok(client, "/v1/tiktok/collection/videos", url=COLLECTION)
    assert [v["id"] for v in body["videos"]] == [_FEED["itemList"][0]["id"]] and body["max_cursor"] == "30"
    assert len(net.urls(_COLLECTION_API)) == 3
    assert_conforms(body, "/v1/tiktok/collection/videos")
    for answer in ("", _BLOCK_PAGE, "<html><body>captcha</body></html>"):
        _Net(monkeypatch, {_COLLECTION_API: [answer]})
        r = client.get("/v1/tiktok/collection/videos", params={"url": COLLECTION})
        assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked"), r.text
        assert "collection's video list request" in r.json()["message"]


def test_user_live_answers_from_the_profile_when_the_live_page_is_blocked(client, real_ydl, monkeypatch):
    # tiktok-discovery: /v1/tiktok/user/live was a 503 "bot check" while the profile page and room/info still answer
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, name: _user_info(name, roomId="77"))
    room = "https://webcast.tiktok.com/webcast/room/info"
    net = _Net(monkeypatch, {"https://www.tiktok.com/@someone/live": [_WAF_PAGE],
                             room: [_BLOCK_PAGE, json.dumps({"data": {"status": 2}})]})
    body = ok(client, "/v1/tiktok/user/live", handle="someone")
    assert body["is_live"] is True and "liveRoom" not in body and body["liveRoomUserInfo"]["uniqueId"] == "someone"
    assert net.urls() == ["https://www.tiktok.com/@someone/live"] * 2 + [room] * 2
    assert_conforms(body, "/v1/tiktok/user/live")
    # room/info never answering is a 503, not a guessed is_live: false
    _Net(monkeypatch, {"https://www.tiktok.com/@someone/live": [_WAF_PAGE], room: [_BLOCK_PAGE]})
    r = client.get("/v1/tiktok/user/live", params={"handle": "someone"})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked"), r.text
    assert "live room info request" in r.json()["message"]


# --- live -------------------------------------------------------------------------

def live_ok(client, path, **params):
    """ok(), but a skip when TikTok throttles this IP (429/503): not a bug here."""
    r = client.get(path, params=params)
    skip_if_blocked(r)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    return r.json()


@pytest.fixture(scope="module")
def stool_video(client):
    """The app aweme (own sound). Now and then TikTok's app feed fails or leaves the post out and the web shape is
    served instead, so that is retried once."""
    body = live_ok(client, "/v2/tiktok/video", url=STOOL_VIDEO, get_transcript="true")
    if "without_watermark" not in body["aweme_detail"]:  # an app-only field
        body = live_ok(client, "/v2/tiktok/video", url=STOOL_VIDEO, get_transcript="true")
    return body


@pytest.fixture(scope="module")
def stool_feed(client):
    p1 = live_ok(client, "/v3/tiktok/profile/videos", handle="stoolpresidente")
    p2 = live_ok(client, "/v3/tiktok/profile/videos", handle="stoolpresidente", max_cursor=p1["max_cursor"])
    return p1, p2


@pytest.fixture(scope="module")
def collection(client):
    return live_ok(client, "/v1/tiktok/collection/videos", url=COLLECTION)


@pytest.fixture(scope="module")
def song_pages(client):
    p1 = live_ok(client, "/v1/tiktok/song/videos", clipId=CLIP_ID)
    p2 = live_ok(client, "/v1/tiktok/song/videos", clipId=CLIP_ID, cursor=p1["cursor"])
    return p1, p2


@pytest.mark.live
def test_video(stool_video):
    assert_conforms(stool_video, "/v2/tiktok/video", min_coverage=0.9)  # the app aweme (own sound)
    d = stool_video["aweme_detail"]
    assert d["aweme_id"] == "7499229683859426602" and d["url"] == STOOL_VIDEO
    au = d["author"]
    assert au["unique_id"] == "stoolpresidente" and au["uid"] == STOOL_UID and au["region"] == "US"
    assert au["follower_count"] > 0 and au["total_favorited"] > 0  # zeroed by the app feed, filled from web
    assert d["statistics"]["play_count"] > 0 and d["statistics"]["digg_count"] > 0
    assert "download_count" in d["statistics"] and isinstance(d["video"]["has_watermark"], bool)
    assert d["video"]["play_addr"]["url_list"] and 89_000 < d["video"]["duration"] < 91_000  # 89.963 s
    assert d["music"]["id_str"] and isinstance(d["is_ad"], bool)
    assert stool_video["transcript"].startswith("WEBVTT") and "pizza" in stool_video["transcript"].lower()


@pytest.mark.live
def test_video_without_transcript(client):
    body = live_ok(client, "/v2/tiktok/video", url=SCOUT_VIDEO)
    assert_conforms(body, "/v2/tiktok/video", min_coverage=0.2)
    assert body["aweme_detail"]["aweme_id"] == "6718335390845095173" and "transcript" not in body


@pytest.mark.live
def test_photo_post(client):
    body = live_ok(client, "/v2/tiktok/video", url=PHOTO_POST)
    assert_conforms(body, "/v2/tiktok/video", min_coverage=0.14)
    d = body["aweme_detail"]
    assert d["aweme_id"] == "7240568259186019630" and d["author"]["unique_id"] == "chillezy"
    images = d["image_post_info"]["images"]
    assert len(images) > 1 and all(i["display_image"]["url_list"] and i["display_image"]["width"] > 0 for i in images)
    assert not d["video"].get("width") and not d["video"].get("duration") and d["statistics"]["digg_count"] > 0


@pytest.mark.live
def test_transcript(client):
    body = live_ok(client, "/v1/tiktok/video/transcript", url=STOOL_VIDEO, language="en")
    assert_conforms(body, "/v1/tiktok/video/transcript", min_coverage=0.95)
    assert body["id"] == "7499229683859426602" and body["url"] == STOOL_VIDEO
    assert body["transcript"].startswith("WEBVTT") and "-->" in body["transcript"]


@pytest.mark.live
def test_transcript_missing_captions(client):
    from socialscrape import transcribe

    body = live_ok(client, "/v1/tiktok/video/transcript", url=SCOUT_VIDEO)
    assert body["id"] == "6718335390845095173" and body["transcript"] is None
    if not transcribe.enabled():
        r = client.get("/v1/tiktok/video/transcript", params={"url": SCOUT_VIDEO, "use_ai_as_fallback": "true"})
        assert r.status_code == 501 and r.json()["error"] == "transcription_disabled"


@pytest.mark.live
def test_profile(client):
    body = live_ok(client, "/v1/tiktok/profile", handle="@stoolpresidente")
    assert_conforms(body, "/v1/tiktok/profile", min_coverage=0.95)
    assert body["user"]["uniqueId"] == "stoolpresidente" and body["user"]["id"] == STOOL_UID
    assert body["stats"]["followerCount"] > 0 and body["stats"]["videoCount"] > 0
    assert all(v >= 0 for v in body["stats"].values() if isinstance(v, int))  # no int32 overflow
    by_id = live_ok(client, "/v1/tiktok/profile", user_id=STOOL_UID)
    assert by_id["user"]["uniqueId"] == "stoolpresidente"


@pytest.mark.live
def test_profile_videos_pagination(stool_feed):
    p1, p2 = stool_feed
    for page in (p1, p2):
        assert_conforms(page, "/v3/tiktok/profile/videos", min_coverage=0.25)
        assert page["aweme_list"] and all(a["author"]["unique_id"] == "stoolpresidente" for a in page["aweme_list"])
    ids1 = [a["aweme_id"] for a in p1["aweme_list"]]
    ids2 = [a["aweme_id"] for a in p2["aweme_list"]]
    assert len(set(ids1)) == len(ids1) and not set(ids1) & set(ids2)
    assert min(a["create_time"] for a in p1["aweme_list"]) > max(a["create_time"] for a in p2["aweme_list"])
    assert p1["max_cursor"] < min(a["create_time"] for a in p1["aweme_list"]) * 1000
    assert p1["has_more"] is True and p1["aweme_list"][0]["statistics"]["play_count"] > 0
    assert all(a["author"]["total_favorited"] > 0 for a in p1["aweme_list"])


@pytest.mark.live
def test_profile_videos_cursor_zero_is_first_page(client, stool_feed):
    p0 = live_ok(client, "/v3/tiktok/profile/videos", handle="stoolpresidente", max_cursor="0")
    assert p0["aweme_list"][0]["aweme_id"] == stool_feed[0]["aweme_list"][0]["aweme_id"]


@pytest.mark.live
def test_profile_region(client):
    body = live_ok(client, "/v1/tiktok/profile/region", handle="stoolpresidente")
    assert_conforms(body, "/v1/tiktok/profile/region", min_coverage=0.75)
    assert body["region"] == "US" and body["handle"] == "stoolpresidente"


@pytest.mark.live
def test_collection_videos(collection):
    assert_conforms(collection, "/v1/tiktok/collection/videos", min_coverage=0.9)
    assert collection["collection_id"] == "7665668414573546258" and isinstance(collection["has_more"], bool)
    ids = [v["id"] for v in collection["videos"]]
    assert ids and len(set(ids)) == len(ids) and all(v["author"]["uniqueId"] for v in collection["videos"])


@pytest.mark.live
def test_user_live(client):
    body = live_ok(client, "/v1/tiktok/user/live", handle=LIVE_HANDLE)
    info = body["liveRoomUserInfo"]
    assert info["uniqueId"] == LIVE_HANDLE and info["id"] == LIVE_UID and info["followerCount"] > 0
    if body["is_live"]:
        assert_conforms(body, "/v1/tiktok/user/live", min_coverage=0.9)
        assert info["roomId"] and body["liveRoom"]["status"] == 2
    else:
        assert_conforms(body, "/v1/tiktok/user/live", min_coverage=0.2)


@pytest.mark.live
def test_user_live_not_live(client):
    body = live_ok(client, "/v1/tiktok/user/live", handle="stoolpresidente")
    assert_conforms(body, "/v1/tiktok/user/live", min_coverage=0.2)
    assert isinstance(body["is_live"], bool) and body["liveRoomUserInfo"]["id"] == STOOL_UID


@pytest.mark.live
def test_live_room(client):
    user = live_ok(client, "/v1/tiktok/user/live", handle=LIVE_HANDLE)
    if not user["is_live"]:
        pytest.skip(f"{LIVE_HANDLE} is not live right now")
    room_id = user["liveRoomUserInfo"]["roomId"]
    body = live_ok(client, "/v1/tiktok/live", room_id=room_id, user_id=LIVE_UID)
    assert_conforms(body, "/v1/tiktok/live", min_coverage=0.85)
    assert body["room_id"] == room_id and body["status"] == "live" and body["viewer_count"] > 0
    assert body["owner"] == {"id": LIVE_UID, "username": LIVE_HANDLE, "display_name": body["owner"]["display_name"]}
    wrong_owner = client.get("/v1/tiktok/live", params={"room_id": room_id, "user_id": STOOL_UID})
    assert wrong_owner.status_code == 404


@pytest.mark.live
def test_song(client):
    body = live_ok(client, "/v1/tiktok/song", clipId=CLIP_ID)
    assert_conforms(body, "/v1/tiktok/song", min_coverage=0.6)
    m = body["music_info"]
    assert m["id_str"] == CLIP_ID and m["title"] and m["duration"] > 0 and m["play_url"]["url_list"]


@pytest.mark.live
def test_song_videos_pagination(song_pages):
    p1, p2 = song_pages
    for page in (p1, p2):
        assert_conforms(page, "/v1/tiktok/song/videos", min_coverage=0.85)
        assert all(a["music"]["id_str"] == CLIP_ID for a in page["aweme_list"])
    ids1 = [a["aweme_id"] for a in p1["aweme_list"]]
    ids2 = [a["aweme_id"] for a in p2["aweme_list"]]
    assert ids1 and ids2 and not set(ids1) & set(ids2)
    assert p1["has_more"] and p1["cursor"] > 0
    assert p1["aweme_list"][0]["statistics"]["play_count"] > 0


@pytest.mark.live
@pytest.mark.parametrize("path, params", [
    ("/v2/tiktok/video", {"url": MISSING_VIDEO}),
    ("/v1/tiktok/video/transcript", {"url": MISSING_VIDEO}),
    ("/v1/tiktok/profile", {"handle": MISSING_USER}),
    ("/v3/tiktok/profile/videos", {"handle": MISSING_USER}),
    ("/v1/tiktok/user/live", {"handle": MISSING_USER}),
    ("/v1/tiktok/live", {"room_id": "1", "user_id": LIVE_UID}),
    ("/v1/tiktok/song/videos", {"clipId": "1000000000000000001"}),
    ("/v1/tiktok/collection/videos", {"url": MISSING_COLLECTION}),
    ("/v1/tiktok/profile/region", {"handle": MISSING_USER}),
])
def test_not_found_is_404(client, path, params):
    r = client.get(path, params=params)
    skip_if_blocked(r)
    assert r.status_code == 404, (path, r.status_code, r.text[:200])
    assert r.json()["error"] == "not_found"


def test_user_search_maps_an_exact_profile(client, monkeypatch):
    async def with_ydl(platform, fn, **opts):
        return {"user": {"id": "1", "secUid": "sec", "uniqueId": "nasa", "nickname": "NASA",
                         "avatarMedium": {"uri": "avatar", "urlList": ["https://img"]}},
                "stats": {"followerCount": 10, "followingCount": 2}}
    monkeypatch.setattr(tiktok, "with_ydl", with_ydl)
    body = client.get("/v1/tiktok/search/users", params={"query": "nasa"}).json()
    assert body["users"] == [{"avatar_medium": {"uri": "avatar", "url_list": ["https://img"]},
                               "follower_count": 10, "following_count": 2, "nickname": "NASA",
                               "sec_uid": "sec", "uid": "1", "unique_id": "nasa"}]


def test_popular_creators_ranks_a_regional_recommended_feed(client, monkeypatch):
    from socialscrape import fallback
    from socialscrape.platforms import tiktok

    feed = {"success": True, "aweme_list": [
        {"aweme_id": "2", "create_time": 2, "url": "https://www.tiktok.com/@beta/video/2",
         "author": {"uid": "20", "unique_id": "beta", "nickname": "Beta",
                    "avatar_medium": {"url_list": ["https://img/beta"]}},
         "statistics": {"play_count": 200, "digg_count": 80}, "video": {"cover": {"url_list": ["b"]}}},
        {"aweme_id": "1", "create_time": 1, "url": "https://www.tiktok.com/@alpha/video/1",
         "author": {"uid": "10", "unique_id": "alpha", "nickname": "Alpha",
                    "avatar_medium": {"url_list": ["https://img/alpha"]}},
         "statistics": {"play_count": 100, "digg_count": 10}, "video": {"cover": {"url_list": ["a"]}}},
    ]}
    async def ask_path(app, path, params):
        assert (path, params) == ("/v1/tiktok/get-trending-feed", {"region": "US"})
        return 200, feed, None
    async def with_ydl(platform, fn, **opts):
        handle = next((cell.cell_contents for cell in fn.__closure__ or () if isinstance(cell.cell_contents, str)), "")
        return {"stats": {"followerCount": 1_000 if handle == "beta" else 2_000, "heartCount": 3_000}}
    monkeypatch.setattr(fallback, "ask_path", ask_path)
    monkeypatch.setattr(tiktok, "with_ydl", with_ydl)
    body = client.get("/v1/tiktok/creators/popular", params={"creatorCountry": "US"}).json()
    assert [creator["user_id"] for creator in body["creators"]] == ["10", "20"]
    assert body["creators"][0]["items"][0]["item_id"] == "1"
    assert body["pagination"] == {"has_more": False, "page": 1, "size": 2, "total": 2}


def test_popular_creators_need_a_signed_in_feed(client, monkeypatch):
    # tiktok-discovery: without a feed source this was a 500 "TikTok's regional recommended feed is unavailable".
    # TikTok serves the regional feed only to a signed-in account (anonymous web/app feeds answer empty, Creative
    # Center's chart answers 40101 "no permission"), so it is a 501 setup error, as /v1/tiktok/get-trending-feed is
    from socialscrape import fallback
    from socialscrape.platforms import tiktok_private

    asked = []

    async def ask_path(app, path, params):
        asked.append(path)
        return None, None, "disabled"

    monkeypatch.setattr(fallback, "ask_path", ask_path)
    r = client.get("/v1/tiktok/creators/popular", params={"creatorCountry": "US"})
    assert (r.status_code, r.json()["error"]) == (501, "platform_auth_required"), r.text
    assert "TikTok account cookies" in r.json()["message"] and asked == ["/v1/tiktok/get-trending-feed"]
    r = client.get("/v1/tiktok/creators/popular", headers={fallback.HEADER: "off"})
    assert (r.status_code, r.json()["error"]) == (501, "platform_auth_required") and len(asked) == 1  # never asked
    row = next(r for r in client.get("/meta/coverage").json()["endpoints"]
               if r["path"] == "/v1/tiktok/creators/popular")
    assert row["status"] == "needs_setup"
    for bad in ("1k-10k", "10M", "x"):
        r = client.get("/v1/tiktok/creators/popular", params={"followerCount": bad})
        assert (r.status_code, r.json()["error"]) == (400, "bad_request"), bad
    assert len(asked) == 1  # refused before any feed is asked for

    # with saved TikTok cookies the account's own feed is read (here: as get-trending-feed returns it)
    regions = []

    async def trending(region, trim=None):
        regions.append(region)
        return {"aweme_list": [
            {"aweme_id": str(i), "create_time": 1_792_000_000 + i, "url": f"https://www.tiktok.com/@{h}/video/{i}",
             "author": {"uid": uid, "unique_id": h, "nickname": h.title()},
             "statistics": {"play_count": views, "digg_count": likes}}
            for i, (uid, h, views, likes) in enumerate([("1", "big", 1_000, 10), ("2", "mid", 9_000, 900),
                                                        ("2", "mid", 1_000, 100), ("3", "gone", 50, 1)])]}

    async def with_ydl(platform, fn, **opts):
        handle = next((cell.cell_contents for cell in fn.__closure__ or () if isinstance(cell.cell_contents, str)), "")
        if handle == "gone":
            raise NotFound("TikTok user not found (status 10221)")
        return {"stats": {"followerCount": {"big": 20_000_000, "mid": 5_000_000}[handle], "heartCount": 7}}

    monkeypatch.setattr(tiktok_private, "trending", trending)
    monkeypatch.setattr(tiktok, "with_ydl", with_ydl)
    body = ok(client, "/v1/tiktok/creators/popular", creatorCountry="gb", followerCount="10M+")
    assert regions == ["GB"] and [c["user_id"] for c in body["creators"]] == ["1"] and len(asked) == 1
    body = ok(client, "/v1/tiktok/creators/popular", sortBy="avg_views")
    assert [c["user_id"] for c in body["creators"]] == ["2", "1", "3"]  # 5000 mean views, 1000, 50
    unknown = body["creators"][2]  # its profile didn't load: no counts rather than the sample's sums
    assert "follower_cnt" not in unknown and "liked_cnt" not in unknown and unknown["items"][0]["vv"] == 50
    assert ok(client, "/v1/tiktok/creators/popular", followerCount="1M-10M")["creators"][0]["user_id"] == "2"
    assert_conforms(body, "/v1/tiktok/creators/popular")


def test_user_live_doesnt_repeat_a_blocked_pass(client, monkeypatch):
    """_web_api asks room/info 3 times itself: the route's own retry of the whole pass made that 6 (2N passes with N
    proxies, as with_ydl already moves through them)."""
    calls = 0

    async def with_ydl(platform, fn, **opts):
        nonlocal calls
        calls += 1
        raise UpstreamBlocked("TikTok answered the live room info request with an empty body instead of JSON, 3 times")

    monkeypatch.setattr(tiktok, "with_ydl", with_ydl)
    r = client.get("/v1/tiktok/user/live", params={"handle": "someone"})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked") and calls == 1


def test_private_tiktok_routes_need_saved_account_cookies(client):
    requests = [
        ("/v1/tiktok/video/comments", {"url": STOOL_VIDEO}),
        ("/v1/tiktok/video/comment/replies", {"url": STOOL_VIDEO, "comment_id": "1"}),
        ("/v1/tiktok/user/followers", {"handle": "stoolpresidente"}),
        ("/v1/tiktok/user/following", {"handle": "stoolpresidente"}),
        ("/v1/tiktok/search/suggestions", {"query": "travel"}),
        ("/v1/tiktok/get-trending-feed", {"region": "US"}),
    ]
    for path, params in requests:
        response = client.get(path, params=params)
        assert response.status_code == 501 and response.json()["error"] == "platform_auth_required"


def test_all_credential_gated_tiktok_routes_are_registered(client):
    # search/keyword and /hashtag answer from TikTok's public keyword pages first (tests/test_tiktok_search.py)
    expected = {
        "/v1/tiktok/video/comments": "platform_auth_required",
        "/v1/tiktok/video/comment/replies": "platform_auth_required",
        "/v1/tiktok/user/followers": "platform_auth_required",
        "/v1/tiktok/user/following": "platform_auth_required",
        "/v1/tiktok/search/suggestions": "platform_auth_required",
        "/v1/tiktok/search/top": "platform_auth_required",
        "/v1/tiktok/get-trending-feed": "platform_auth_required",
        "/v1/tiktok/user/audience": "not_supported",
        "/v1/tiktok/product": "not_supported",
        "/v1/tiktok/shop/product/reviews": "not_supported",
        "/v1/tiktok/shop/products": "not_supported",
        "/v1/tiktok/shop/search": "not_supported",
        "/v1/tiktok/user/showcase": "not_supported",
    }
    params = {
        "url": "https://www.tiktok.com/@x/video/123", "comment_id": "1", "handle": "x", "query": "x",
        "hashtag": "x", "region": "US", "ad_id": "1", "product_id": "1",
    }
    for path, error in expected.items():
        names = {p["name"] for p in client.app.openapi()["paths"][path]["get"].get("parameters", [])}
        response = client.get(path, params={name: params[name] for name in names if name in params})
        assert response.status_code == 501 and response.json()["error"] == error, path


def test_tiktok_api_tokens_stay_out_of_settings_repr(monkeypatch):
    from socialscrape.config import get_settings
    monkeypatch.setenv("SOCIALSCRAPE_TIKTOK_RESEARCH_ACCESS_TOKEN", "research-secret")
    monkeypatch.setenv("SOCIALSCRAPE_TIKTOK_SHOP_ACCESS_TOKEN", "shop-secret")
    get_settings.cache_clear()
    try:
        shown = repr(get_settings())
        assert "research-secret" not in shown and "shop-secret" not in shown
    finally:
        get_settings.cache_clear()
