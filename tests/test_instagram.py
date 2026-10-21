"""Instagram: offline mapper/route tests plus a sparse set of live checks.

Live tests share one cached response per URL and space Instagram requests out,
because Instagram rate-limits logged-out clients per IP quickly.
"""

from __future__ import annotations

import base64
import copy
import dataclasses
import time
import unicodedata
from types import SimpleNamespace
from typing import Any

import pytest

from socialscrape import transcribe
from socialscrape.catalog import CATALOG
from socialscrape.config import get_settings
from socialscrape.errors import BadRequest, NotFound, UpstreamBlocked, UpstreamError
from socialscrape.platforms import instagram as ig
from socialscrape.platforms._instagram_shapes import HIGHLIGHT_REEL, prune
from tests.contract import assert_conforms, check

JANE_PK = "21393171"
REEL_CODE, REEL_PK = "DLDXI0fylTC", "3657869083548472514"
CAROUSEL_CODE = "DeKe2vzD9xZ"
HIGHLIGHT_ID = "18382085791177637"

# --- embedded Instagram data (trimmed from real logged-out responses) -----------------

USER = {"pk": JANE_PK, "id": JANE_PK, "username": "jane", "full_name": "Jane Williamson", "is_verified": True,
        "is_private": False, "profile_pic_url": "https://cdn.example/jane.jpg"}
COMMENT = {"pk": "18151267093500821", "text": "so cute", "created_at": 1750280000, "comment_like_count": 12,
           "child_comment_count": None,
           "user": {"pk": "555", "id": "555", "username": "fan", "is_verified": False, "is_unpublished": False,
                    "profile_pic_url": "https://cdn.example/fan.jpg"}}
REEL = {
    "pk": REEL_PK, "id": f"POLARIS_{REEL_PK}", "code": REEL_CODE, "media_type": 2, "product_type": "clips",
    "taken_at": 1750272151, "like_count": 56102, "comment_count": 435, "view_count": None, "play_count": None,
    "has_audio": True, "accessibility_caption": "Video by Jane Williamson on June 18, 2025.", "location": None,
    "display_uri": "https://cdn.example/reel_640.jpg",
    "image_versions2": {"candidates": [{"url": "https://cdn.example/reel_640.jpg", "width": 640, "height": 1136},
                                       {"url": "https://cdn.example/reel_1080.jpg", "width": 1080, "height": 1920}]},
    "video_versions": [{"url": "https://cdn.example/reel.mp4", "type": 101}],
    "video_dash_manifest": '<MPD mediaPresentationDuration="PT1M24.666664S"></MPD>',
    "user": USER,
    "caption": {"pk": "17894844171126505", "text": "I think I like this goosey life", "created_at": 1750272152},
    "usertags": {"in": [{"user": {"id": "77", "username": "goose", "full_name": "Goose", "is_verified": False,
                                  "profile_pic_url": "https://cdn.example/goose.jpg"}, "position": [0.5, 0.25]}]},
    "clips_metadata": {"music_info": None, "original_sound_info": {
        "audio_asset_id": "3011705342342322", "original_audio_title": "Original audio",
        "ig_artist": {"username": "jane", "id": JANE_PK}, "should_mute_audio": False,
        "consumption_info": {"should_mute_audio_reason": ""}}},
}
REEL_MEDIA = {"if_not_gated_logged_out": REEL, "comments_connection": {
    "page_info": {"has_next_page": True, "end_cursor": '{"server_cursor":"AQH"}'}, "edges": [{"node": COMMENT}]}}
CAROUSEL = {
    "pk": "4002146922441727065", "code": CAROUSEL_CODE, "media_type": 8, "taken_at": 1791331200, "like_count": 10,
    "comment_count": 2, "display_uri": "https://cdn.example/c.jpg", "user": USER, "caption": None,
    "carousel_media": [
        {"pk": "1", "code": "AAAAA", "media_type": 1, "original_width": 1080, "original_height": 1350,
         "display_uri": "https://cdn.example/c1.jpg", "accessibility_caption": "Photo"},
        {"pk": "2", "media_type": 2, "display_uri": "https://cdn.example/c2.jpg",
         "video_versions": [{"url": "https://cdn.example/c2.mp4"}], "video_duration": 7.5, "has_audio": True},
    ],
}
PROFILE = {
    **USER, "id": "17841400515130334", "biography": "THE Utah Mom", "follower_count": 2_000_000,
    "following_count": 1017,
    "bio_links": [{"title": "Steamy", "lynx_url": "https://l.instagram.com/?u=x", "url": "https://drinksteamy.com",
                   "link_type": "external", "is_pinned": False}],
    "polaris_ordered_timeline_connection": {
        "page_info": {"has_next_page": True, "end_cursor": "AQHT"},
        "edges": [
            {"node": {"pk": "3756328113124001496", "code": "DQhKIRakcbY", "media_type": 2, "product_type": "clips",
                      "display_uri": "https://cdn.example/p1.jpg", "caption": {"text": "CHICKENLEIGH"},
                      "user": {"pk": JANE_PK, "username": "jane"}}},
            {"node": {"pk": "42", "code": "ABCDEF", "media_type": 1, "display_uri": "https://cdn.example/p2.jpg",
                      "caption": None, "user": {"pk": JANE_PK, "username": "jane"}}},
            {"node": {"pk": "43", "code": "COLLAB", "media_type": 1, "product_type": "feed",
                      "display_uri": "https://cdn.example/p3.jpg", "caption": {"text": "with bob"},
                      "user": {"pk": "777", "username": "bob", "id": "17841400000000777"}}},
        ]},
    "lox_highlights_connection": {"edges": [{"node": {
        "id": HIGHLIGHT_ID, "title": "Mems", "cover_media_cropped_thumbnail_url": "https://cdn.example/hl.jpg"}}]},
}
REELS_TAB = {**{k: PROFILE[k] for k in ("pk", "id", "username", "full_name", "is_verified", "profile_pic_url")},
             "polaris_clips_connection": {
                 "page_info": {"has_next_page": True, "end_cursor": "AQHT8"},
                 "edges": [{"node": {"__typename": "XIGPolarisVideoMedia", "pk": "3756328113124001496",
                                     "id": "POLARIS_3756328113124001496", "code": "DQhKIRakcbY",
                                     "user": {"pk": JANE_PK, "id": "17841400515130334"}, "product_type": "clips",
                                     "comment_count": 18709, "like_count": 1743588, "play_count": 30850829,
                                     "display_uri": "https://cdn.example/r1.jpg"}}]}}
PROFILE_HTML = ('<meta property="og:description" content="2M Followers, 1,017 Following, 1,800 Posts - '
                'See Instagram photos and videos from Jane Williamson (&#064;jane)" />')
TOPIC = {
    "xig_logged_out_popular_search_media_info": {"edges": [
        {"node": {"__typename": "XIGPolarisVideoMedia", "pk": "3934230212413674973",
                  "id": "POLARIS_3934230212413674973", "code": "DaZMYijhtXd", "caption": {"text": "Max #funnydogs"},
                  "display_uri": "https://cdn.example/t1.jpg", "play_count": 7162320,
                  "video_versions": [{"url": "https://cdn.example/t1.mp4"}],
                  "user": {"pk": "900", "id": "17841400000000900", "username": "maxdog", "is_verified": False,
                           "profile_pic_url": "https://cdn.example/max.jpg"}}},
        {"node": {"__typename": "XIGPolarisImageMedia", "pk": "11", "id": "POLARIS_11", "code": "IMAGE1",
                  "caption": None, "display_uri": "https://cdn.example/t2.jpg",
                  "user": {"pk": "901", "id": "17841400000000901", "username": "pupper"}}},
    ]},
    "popular_search_keyword_description": {"plain_text": "Dogs being silly.", "source_uris": ["https://x.example"],
                                           "linked_terms": [], "other": 1},
    "popular_search_related_keywords_connection": {"edges": [{"node": {"query_text": "funnydog"}, "cursor": "0"}]},
}
TOPIC_HTML = ('<meta property="og:title" content="Golden Retriever &#x2022; 203M reels on Instagram" />'
              '<script>{"total_media_count_intl":"203M"}</script>')
HUB = {"ig_popular_search_category_hub": {
    "category": {"display_name": "Basketball"}, "description": "Basketball is a team sport.",
    "related_interests": [{"display_name": "Motorsports"}],
    "sections": [
        {"keyword_text": "the tallest basketball player", "page_url": "/popular/the-tallest-basketball-player/",
         "media_items": {"edges": [
             {"node": {"__typename": "XIGPolarisVideoMedia", "code": "DW79CMClNYH", "pk": "3871956735859414535",
                       "user": {"pk": "1910866549", "id": "17841400556499857"},
                       "display_uri": "https://cdn.example/h1.jpg", "play_count": 21447,
                       "id": "POLARIS_3871956735859414535"}}]}},
        {"keyword_text": "st john basketball", "page_url": "/popular/st-john-basketball/",
         "media_items": {"edges": [
             {"node": {"__typename": "XIGPolarisVideoMedia", "code": "DW79CMClNYH", "pk": "3871956735859414535",
                       "user": {"pk": "1910866549", "id": "17841400556499857"},
                       "display_uri": "https://cdn.example/h1.jpg", "id": "POLARIS_3871956735859414535"}},
             {"node": {"__typename": "XIGPolarisVideoMedia", "code": "DXXXXXXXXXX", "pk": "2",
                       "user": {"pk": "3", "id": "17841400000000003"}, "display_uri": "https://cdn.example/h2.jpg",
                       "id": "POLARIS_2"}}]}},
    ]}}
HUB_HTML = '<meta property="og:title" content="Basketball on Instagram" />'
HIGHLIGHT = {
    "id": f"highlight:{HIGHLIGHT_ID}", "strong_id__": f"highlight:{HIGHLIGHT_ID}", "title": "Mems",
    "reel_type": "highlight_reel", "media_count": 1, "is_cacheable": True,
    "cover_media": {"crop_rect": [0, 0.21, 1, 0.78], "media_id": f"1_{JANE_PK}",
                    "cropped_image_version": {"width": 150, "height": 150, "url": "https://cdn.example/c.jpg"}},
    "user": {"pk": JANE_PK, "id": JANE_PK, "username": "jane", "account_badges": []},
    "items": [{"pk": "1", "id": f"1_{JANE_PK}", "media_type": 1, "taken_at": 1761578384, "is_arirang_prize": False,
               "image_versions2": {"candidates": [{"url": "https://cdn.example/s.jpg", "width": 1080, "height": 1920,
                                                   "is_spatial_image": False}]},
               "user": {"pk": JANE_PK, "id": JANE_PK, "username": "jane", "full_name": "Jane"}}],
}


def _sc(payload: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "credits_charged": 0, **payload}


def _sjs(result: dict[str, Any], module: str = "RelayPrefetchedStreamCache") -> str:
    import json

    blob = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"require": [
        [module, "next", [], ["adp_Q", {"__bbox": {"result": result}}]]]}}]]]}
    return f'<script type="application/json" data-sjs>{json.dumps(blob)}</script>'


# --- validators ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [
    REEL_CODE, f"https://www.instagram.com/p/{REEL_CODE}/", f"instagram.com/reel/{REEL_CODE}?igsh=x",
    f"https://www.instagram.com/jane/p/{REEL_CODE}/", f"https://m.instagram.com/reel/{REEL_CODE}/",
    f"HTTPS://WWW.INSTAGRAM.COM/p/{REEL_CODE}/", f"Instagram.com/p/{REEL_CODE}/",
    f"https://www.instagram.com/p/{REEL_CODE}/?img_index=2", f"https://www.instagram.com/p/{REEL_CODE}/embed/",
    f"https://www.instagram.com/p/{REEL_CODE}/c/18151267093500821/", f"http://m.instagram.com/tv/{REEL_CODE}",
    f"https://www.instagram.com/jane/reel/{REEL_CODE}/", f"  {REEL_CODE}  ",
    # fix3: Instagram's old short domain, which redirects to the same path on www
    f"https://instagr.am/p/{REEL_CODE}/", f"HTTP://INSTAGR.AM/p/{REEL_CODE}",
    f"//www.instagram.com/reel/{REEL_CODE}/",  # protocol-relative, like the src of an embed code's script
])
def test_post_url_accepts_post_urls_and_shortcodes(value):
    assert ig.post_url(value) == (REEL_CODE, f"https://www.instagram.com/p/{REEL_CODE}/")


@pytest.mark.parametrize("value", [
    "", "https://www.tiktok.com/@jane/video/7300000000000000000", "https://www.instagram.com/jane/",
    "https://www.instagram.com/stories/highlights/18382085791177637/", "not a url",
    f"https://notinstagram.com/p/{REEL_CODE}/", f"//notinstagram.com/p/{REEL_CODE}/",
    # ids outside the shortcode alphabet (yt-dlp's _id_to_pk raised KeyError -> 500)
    "https://www.instagram.com/p/a.b.c/", "https://www.instagram.com/p/../", "https://www.instagram.com/p/%2E%2E/",
    "https://www.instagram.com/p/%3Cscript%3E/", "https://www.instagram.com/p/abc!def/",
    "https://www.instagram.com/reel/abc+def/", "https://www.instagram.com/p/\u00e9t\u00e9xyz/",
    # share links without an id, with one outside the shortcode alphabet or a path after it, or off Instagram
    "https://www.instagram.com/share/", "https://www.instagram.com/share/reel/", "https://www.instagram.com/share/p/",
    "https://www.instagram.com/share/reel/a.b.c.d/", "https://notinstagram.com/share/reel/_shAre-12/",
    "https://www.instagram.com/share/reel/_shAre-12/extra/",
])
def test_post_url_rejects(value):
    with pytest.raises(BadRequest):
        ig.post_url(value)


SHARE = "https://www.instagram.com/share/reel/_shAre-12"


@pytest.mark.parametrize("value,link", [
    (SHARE, SHARE), (f" {SHARE}/?igsh=ZGUz ", f"{SHARE}/?igsh=ZGUz"), ("instagram.com/share/reel/_shAre-12", SHARE),
    ("HTTPS://M.INSTAGRAM.COM/share/p/_shAre-12/", "https://www.instagram.com/share/p/_shAre-12/"),
    ("https://www.instagram.com/share/_shAre-12", "https://www.instagram.com/share/_shAre-12"),
])
def test_post_url_takes_share_links(value, link):
    """fix3: a link from the app's share sheet (/share/reel/<id>) was a 400. Its id isn't the post's shortcode, so
    the post is only known once fetch_post follows the link."""
    assert ig.post_url(value) == ("", link)


@pytest.mark.parametrize("value,expected", [
    ("jane", "jane"), (" @jane ", "jane"), ("https://www.instagram.com/jane/", "jane"),
    ("instagram.com/jane?hl=en", "jane"), ("jane.doe_1", "jane.doe_1"), ("jane/", "jane"),
    ("https://m.instagram.com/jane/", "jane"), ("HTTPS://WWW.INSTAGRAM.COM/jane", "jane"),
    ("https://www.instagram.com/jane/reels/", "jane"), ("https://www.instagram.com/jane/tagged/", "jane"),
    ("＠jane", "jane"), ("﹫jane", "jane"),  # the full-width '@' of Japanese and Chinese keyboards was a 400
    # the address bar while the profile's follower or following list is open was a 400
    ("https://www.instagram.com/jane/followers/", "jane"), ("instagram.com/jane/following?hl=en", "jane"),
])
def test_clean_handle(value, expected):
    assert ig.clean_handle(value) == expected


@pytest.mark.parametrize("value", ["", "https://www.tiktok.com/@jane", "jane doe", "explore", "a" * 31,
                                   "https://www.instagram.com/p/DLDXI0fylTC/", ".", "..", "@..", "jane.", ".jane",
                                   "ja..ne", "https://notinstagram.com/jane/", "https://www.instagram.com/jane/x/",
                                   # Instagram routes, not profiles (checkpoint page, HTTP 400, login-only)
                                   "challenge", "graphql", "qr", "nametag", "press", "your_activity", "@Challenge",
                                   "https://www.instagram.com/qr/", "notifications", "meta_verified", "＠",
                                   "https://www.instagram.com/jane/followers/x/",
                                   "https://www.instagram.com/jane/follower/"])
def test_clean_handle_rejects(value):
    with pytest.raises(BadRequest):
        ig.clean_handle(value)


def share_code(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest.mark.parametrize("value", [HIGHLIGHT_ID, f"highlight:{HIGHLIGHT_ID}",
                                   f"https://www.instagram.com/stories/highlights/{HIGHLIGHT_ID}/",
                                   f"https://m.instagram.com/stories/highlights/{HIGHLIGHT_ID}/",
                                   # the app's share link (was a 400): /s/<base64 of 'highlight:<id>'>
                                   f"https://www.instagram.com/s/{share_code(f'highlight:{HIGHLIGHT_ID}')}"
                                   "?story_media_id=3815154085155445315_8663171404&igsh=MWQ1ZGUxMzBkMA==",
                                   f"instagram.com/s/{share_code(f'highlight:{HIGHLIGHT_ID}')}/"])
def test_clean_highlight_id(value):
    assert ig.clean_highlight_id(value) == HIGHLIGHT_ID
    assert ig.clean_highlight_id("9" * 19) == "9" * 19


def test_clean_highlight_id_share_link_padding():
    code = share_code("highlight:" + "9" * 18)  # 'aGln…OQ==': the '=' may be there, missing or percent-encoded
    assert code.endswith("==")
    for link in (code, code.rstrip("="), code.replace("=", "%3D")):
        assert ig.clean_highlight_id(f"https://www.instagram.com/s/{link}") == "9" * 18


FULLWIDTH_ID = HIGHLIGHT_ID.translate({ord(d): 0xFF10 + int(d) for d in "0123456789"})  # \d matches these


@pytest.mark.parametrize("value", ["", "abc", "https://www.instagram.com/stories/jane/3815154085155445315/",
                                   "https://www.youtube.com/watch?v=x", "1" * 20, f"highlight:{'1' * 25}",
                                   f"https://www.instagram.com/stories/highlights/{'1' * 20}/", FULLWIDTH_ID,
                                   f"highlight:{FULLWIDTH_ID}", HIGHLIGHT_ID.translate({ord("1"): "\u0661"}),
                                   f"https://www.instagram.com/stories/highlights/{FULLWIDTH_ID}/",
                                   # share links that don't hold 'highlight:<id>', or are cut off
                                   f"https://www.instagram.com/s/{share_code('highlight')}",
                                   f"https://www.instagram.com/s/{share_code('highlight:abc')}",
                                   f"https://www.instagram.com/s/{share_code(f'highlight:{HIGHLIGHT_ID}')[:-3]}",
                                   f"https://www.instagram.com/s/{share_code(f'highlights:{HIGHLIGHT_ID}')}",
                                   f"https://www.instagram.com/s/{share_code(f'story:{HIGHLIGHT_ID}')}"])
def test_clean_highlight_id_rejects(value):
    with pytest.raises(BadRequest):
        ig.clean_highlight_id(value)


def test_clean_topic():
    assert ig.clean_topic("  golden   retriever ") == "golden retriever"
    assert ig.clean_topic("#funnydogs", single_word=True) == "funnydogs"
    # only the lower-case /explore/tags/ and /popular/ paths exist (mixed case -> login redirect / 404 page)
    assert ig.clean_topic("  Golden  Retriever") == "golden retriever"
    assert ig.clean_topic("#FunnyDogs", single_word=True) == "funnydogs"
    # the full-width '#' of Japanese and Chinese keyboards: '＃ねこ' was a 400 ('＃' isn't a letter)
    assert ig.clean_topic("＃ねこ", single_word=True) == ig.clean_topic("#ねこ", single_word=True) == "ねこ"
    assert ig.clean_topic("＃Golden Retriever") == "golden retriever"
    assert ig.clean_topic("CAFÉ") == "café"
    # str.lower() turns 'İ' into 'i' + U+0307 (a 404 tag path, and not \w for the single-word check)
    assert ig.clean_topic("İstanbul") == ig.clean_topic("ISTANBUL") == "istanbul"
    assert ig.clean_topic("#İSTANBUL", single_word=True) == "istanbul"
    # combining marks (Devanagari vowel signs, Tamil virama) are part of a word
    assert ig.clean_topic("#भारत", single_word=True) == "भारत"
    assert ig.clean_topic("தமிழ்", single_word=True) == "தமிழ்"
    # retest-instagram-3: decomposed spellings name the same tag (NFD 'españa' was a 404 path)
    nfd = unicodedata.normalize("NFD", "España")
    assert nfd != "España" and ig.clean_topic(nfd, single_word=True) == ig.clean_topic("españa") == "españa"
    assert ig.clean_topic(unicodedata.normalize("NFD", "İSTANBUL"), single_word=True) == "istanbul"
    # the address of a tag page, or of the Popular page Instagram sends logged-out visitors on to, was a 400
    assert ig.clean_topic("https://www.instagram.com/explore/tags/FunnyDogs/", single_word=True) == "funnydogs"
    assert ig.clean_topic("instagram.com/explore/tags/funnydogs?hl=en", single_word=True) == "funnydogs"
    assert ig.clean_topic("https://www.instagram.com/popular/basketball/?utm_source=explore_tag") == "basketball"
    assert ig.clean_topic("https://m.instagram.com/explore/tags/%E0%A4%AD%E0%A4%BE%E0%A4%B0%E0%A4%A4/") == "भारत"
    assert ig.clean_topic("https://www.instagram.com/popular/st-john-basketball/") == "st-john-basketball"
    for bad in ("", "#", "a/b", "x?y", "a" * 101, "https://www.instagram.com/explore/tags/",
                "https://www.instagram.com/explore/tags/a%2Fb/", "https://www.instagram.com/explore/tags/cats/top/",
                "https://www.instagram.com/explore/locations/212988663/", "https://www.instagram.com/p/DaZMYijhtXd/"):
        with pytest.raises(BadRequest):
            ig.clean_topic(bad)
    for bad in ("two words", "https://www.instagram.com/popular/st-john-basketball/"):
        with pytest.raises(BadRequest):
            ig.clean_topic(bad, single_word=True)


# --- pure mappers ---------------------------------------------------------------------------


def test_page_relay_reads_prefetched_results():
    html = _sjs({"data": {"xig_user_by_username": {"pk": "1"}}}) + "<script data-sjs>{broken</script>"
    assert ig.page_relay(html) == [{"data": {"xig_user_by_username": {"pk": "1"}}}]


def test_page_relay_accepts_hashed_module_name():
    """~1-3% of real profile pages name the module RelayPrefetchedStreamCache@<hash> (JSON-escaped '@')."""
    hashed = _sjs({"data": {"xig_user_by_username": {"pk": "1"}}},
                  module="RelayPrefetchedStreamCache@2d59f5a78e6f242b0cf731dd819b49d5").replace("@", "\\u0040")
    assert "\\u0040" in hashed
    assert ig.page_relay(hashed) == [{"data": {"xig_user_by_username": {"pk": "1"}}}]
    other = _sjs({"data": {"x": 1}}, module="RelayPrefetchedStreamCacheX") + _sjs({"data": {"y": 1}}, module="Other@1")
    assert ig.page_relay(other) == []


def test_polaris_media_prefers_graphql_then_page():
    import json

    gql = json.dumps({"data": {"xig_polaris_media": REEL_MEDIA}})
    assert ig.polaris_media([("https://www.instagram.com/", "<html>"),
                             ("https://www.instagram.com/api/graphql", gql)]) == REEL_MEDIA
    page = _sjs({"data": {"xig_polaris_media": REEL_MEDIA}})
    assert ig.polaris_media([("https://www.instagram.com/p/X/", page)]) == REEL_MEDIA
    assert ig.polaris_media([("https://www.instagram.com/api/graphql", '{"data": {}}')]) is None


def test_video_duration_and_music():
    assert ig.video_duration(REEL) == pytest.approx(84.666664)
    assert ig.video_duration({"video_duration": 7}) == 7.0
    assert ig.video_duration({}) is None
    assert ig.music_attribution(REEL["clips_metadata"]) == {
        "artist_name": "jane", "song_name": "Original audio", "uses_original_audio": True,
        "should_mute_audio": False, "should_mute_audio_reason": "", "audio_id": "3011705342342322"}
    licensed = {"music_info": {"music_asset_info": {"display_artist": "Mariah Carey", "title": "All I Want",
                                                    "audio_asset_id": 123},
                               "music_consumption_info": {"should_mute_audio": False}}}
    assert ig.music_attribution(licensed) == {"artist_name": "Mariah Carey", "song_name": "All I Want",
                                              "uses_original_audio": False, "should_mute_audio": False,
                                              "audio_id": "123"}
    assert ig.music_attribution(None) is None


def test_map_post_reel():
    post = ig.map_post(REEL, REEL_MEDIA)
    assert post["id"] == REEL_PK and post["shortcode"] == REEL_CODE
    assert post["__typename"] == "XDTGraphVideo" and post["is_video"] is True
    assert post["display_url"] == "https://cdn.example/reel_1080.jpg"
    assert post["thumbnail_src"] == "https://cdn.example/reel_640.jpg"
    assert post["video_url"] == "https://cdn.example/reel.mp4"
    assert post["video_duration"] == pytest.approx(84.666664)
    assert "video_view_count" not in post and "video_play_count" not in post  # absent anonymously, not invented
    assert post["owner"]["id"] == JANE_PK and post["owner"]["username"] == "jane"
    assert post["taken_at_timestamp"] == 1750272151 and post["created_at"].startswith("2025-06-18T18:42:31")
    assert post["edge_media_to_caption"]["edges"][0]["node"] == {
        "created_at": "1750272152", "text": "I think I like this goosey life", "id": "17894844171126505"}
    comments = post["edge_media_to_parent_comment"]
    assert comments["count"] == 435 and comments["page_info"]["has_next_page"] is True
    assert comments["edges"][0]["node"]["owner"]["username"] == "fan"
    assert comments["edges"][0]["node"]["edge_liked_by"] == {"count": 12}
    assert post["edge_media_preview_like"] == {"count": 56102}
    assert post["edge_media_preview_comment"] == {"count": 435, "edges": comments["edges"][:3]}
    assert post["edge_media_to_tagged_user"]["edges"][0]["node"]["x"] == 0.5
    assert post["location"] is None and "edge_sidecar_to_children" not in post
    assert_conforms(_sc({"data": {"xdt_shortcode_media": post}}), "/v1/instagram/post", min_coverage=0.5)


def test_map_post_carousel_and_video_items():
    post = ig.map_post(CAROUSEL, None)
    assert post["__typename"] == "XDTGraphSidecar" and post["is_video"] is False
    assert "video_url" not in post and post["edge_media_to_caption"] == {"edges": []}
    first, second = (e["node"] for e in post["edge_sidecar_to_children"]["edges"])
    assert first["__typename"] == "XDTGraphImage" and first["dimensions"] == {"height": 1350, "width": 1080}
    assert second["__typename"] == "XDTGraphVideo" and second["video_url"] == "https://cdn.example/c2.mp4"
    # each video child has its own audio flag and length, as a video post does at the top level
    assert second["has_audio"] is True and second["video_duration"] == 7.5
    assert "has_audio" not in first and "video_duration" not in first
    assert "edges" not in post["edge_media_to_parent_comment"]  # logged-in item: no comments embedded
    assert post["edge_media_preview_comment"] == {"count": 2}
    assert [i for i, _ in ig.video_items(CAROUSEL)] == [2]
    assert ig.video_items(REEL) == [(1, REEL)]
    assert ig.video_items({"media_type": 1}) == []
    report = assert_conforms(_sc({"data": {"xdt_shortcode_media": post}}), "/v1/instagram/post", min_coverage=0.25)
    # extras are the ones SC documents for this endpoint (published time, tagged users, carousel children)
    assert set(report.extras) <= {f"data.xdt_shortcode_media.{k}" for k in (
        "created_at", "edge_media_to_tagged_user", "edge_sidecar_to_children")}, report


def test_map_post_play_count_switch():
    counted = {**REEL, "play_count": 10, "view_count": 5}
    assert ig.map_post(counted)["video_play_count"] == 10
    assert "video_play_count" not in ig.map_post(counted, play_count=False)
    # a logged-in media/info item's play_count adds Facebook plays; video_play_count is Instagram's alone
    assert ig.map_post({**counted, "ig_play_count": 7})["video_play_count"] == 7


def test_map_comment():
    c = ig.map_comment(COMMENT)
    assert c == {"id": "18151267093500821", "text": "so cute", "created_at": "2025-06-18T20:53:20.000Z",
                 "comment_like_count": 12, "child_comment_count": None,
                 "user": {"is_verified": False, "id": "555", "pk": "555", "is_unpublished": False,
                          "profile_pic_url": "https://cdn.example/fan.jpg", "username": "fan"}}
    assert_conforms(_sc({"comments": [c]}), "/v2/instagram/post/comments", min_coverage=0.7)


def test_post_count_from_meta():
    assert ig.post_count_from_meta(PROFILE_HTML) == (1800, False)
    assert ig.post_count_from_meta('<meta content="1M Followers, 5 Following, 12K Posts - x" '
                                   'property="og:description" />') == (12000, True)
    assert ig.post_count_from_meta("<html></html>") is None


def test_map_profile():
    user = ig.map_profile(PROFILE, (1800, False))
    assert user["id"] == JANE_PK and user["fbid"] == "17841400515130334" and user["username"] == "jane"
    assert user["edge_followed_by"] == {"count": 2_000_000} and user["edge_follow"] == {"count": 1017}
    assert user["bio_links"] == [{"title": "Steamy", "lynx_url": "https://l.instagram.com/?u=x",
                                  "url": "https://drinksteamy.com", "link_type": "external"}]
    timeline = user["edge_owner_to_timeline_media"]
    assert timeline["count"] == 1800 and timeline["page_info"] == {"has_next_page": True, "end_cursor": "AQHT"}
    node = timeline["edges"][0]["node"]
    assert node["__typename"] == "GraphVideo" and node["shortcode"] == "DQhKIRakcbY"
    assert node["edge_media_to_caption"]["edges"][0]["node"]["text"] == "CHICKENLEIGH"
    assert "count" not in ig.map_profile(PROFILE, (12000, True))["edge_owner_to_timeline_media"]
    assert_conforms(_sc({"data": {"user": user}}), "/v1/instagram/profile", min_coverage=0.25)


def test_map_user_posts():
    body = ig.map_user_posts(PROFILE, ig.clip_counts(REELS_TAB))
    assert body["num_results"] == 3 and body["user"]["pk"] == JANE_PK and body["user"]["username"] == "jane"
    first, second, collab = body["items"]
    assert first["id"] == f"3756328113124001496_{JANE_PK}" and first["code"] == "DQhKIRakcbY"
    assert first["caption"] == {"text": "CHICKENLEIGH"} and first["url"].endswith("/p/DQhKIRakcbY/")
    assert first["user"]["fbid_v2"] == "17841400515130334" and "caption" not in second
    assert first["user"]["full_name"] == "Jane Williamson" and first["user"]["is_verified"] is True
    assert (first["like_count"], first["comment_count"], first["play_count"]) == (1743588, 18709, 30850829)
    assert first["ig_play_count"] == 30850829  # the logged-out count is Instagram's alone
    assert "like_count" not in second and "ig_play_count" not in second
    # a collab post on jane's grid is authored by the collaborator, not by the profile owner
    assert collab["id"] == "43_777" and collab["user"] == collab["owner"] == {
        "pk": "777", "pk_id": "777", "id": "777", "username": "bob", "fbid_v2": "17841400000000777"}
    assert "like_count" not in ig.map_user_posts(PROFILE)["items"][0]
    assert_conforms(_sc(body), "/v2/instagram/user/posts", min_coverage=0.2)


def test_map_user_reels():
    body = ig.map_user_reels(REELS_TAB)
    (reel,) = body["items"]
    assert reel["id"] == f"3756328113124001496_{JANE_PK}" and reel["url"].endswith("/reel/DQhKIRakcbY/")
    assert (reel["play_count"], reel["ig_play_count"], reel["like_count"]) == (30850829, 30850829, 1743588)
    assert reel["user"]["username"] == "jane" and reel["owner"]["fbid_v2"] == "17841400515130334"
    assert "max_id" not in body
    assert_conforms(_sc(body), "/v1/instagram/user/reels", min_coverage=0.1)
    # a collaborator's reel on the tab only carries their pk/id, never the profile owner's username
    collab = {"pk": "44", "code": "COLLAB", "user": {"pk": "777", "id": "17841400000000777"}, "play_count": 5}
    (reel,) = ig.map_user_reels({**REELS_TAB, "polaris_clips_connection": {"edges": [{"node": collab}]}})["items"]
    assert reel["id"] == "44_777" and reel["user"] == {"pk": "777", "pk_id": "777", "id": "777",
                                                      "fbid_v2": "17841400000000777"}


def test_map_highlights():
    highlights = ig.map_highlights(PROFILE)
    assert highlights == [{"__typename": "GraphHighlightReel", "id": HIGHLIGHT_ID,
                           "cover_media_cropped_thumbnail": {"url": "https://cdn.example/hl.jpg"},
                           "owner": {"__typename": "GraphUser", "id": JANE_PK,
                                     "profile_pic_url": "https://cdn.example/jane.jpg", "username": "jane"},
                           "title": "Mems"}]
    assert_conforms(_sc({"highlights": highlights}), "/v1/instagram/user/highlights", min_coverage=0.8)


def test_highlight_prune_keeps_sc_keys_only():
    body = _sc(prune(copy.deepcopy(HIGHLIGHT), HIGHLIGHT_REEL))
    assert "strong_id__" not in body and "is_cacheable" not in body
    assert "is_arirang_prize" not in body["items"][0]
    assert "is_spatial_image" not in body["items"][0]["image_versions2"]["candidates"][0]
    assert body["items"][0]["user"] == {"id": JANE_PK, "username": "jane", "full_name": "Jane"}
    assert body["cover_media"]["crop_rect"] == [0, 0.21, 1, 0.78]
    report = check(body, "/v1/instagram/user/highlight/detail")
    assert not report.extras and not report.mismatches, report


def test_map_popular_and_hashtag():
    body = ig.map_popular("golden retriever", TOPIC, TOPIC_HTML)
    assert body["title"] == "Golden Retriever"
    assert body["total_media_count"] == 203_000_000 and body["suggested_terms"] == ["funnydog"]
    assert body["description"] == {"plain_text": "Dogs being silly.", "source_uris": ["https://x.example"],
                                   "linked_terms": []}
    reel, image = body["posts"]
    assert reel["type"] == "reel" and reel["url"].endswith("/reel/DaZMYijhtXd/") and reel["play_count"] == 7162320
    assert reel["owner"]["id"] == "17841400000000900" and image["type"] == "post" and "caption" not in image
    assert_conforms(_sc(body), "/v1/instagram/search/popular", min_coverage=0.7)

    posts = [ig.map_hashtag_post(n) for n in ig.topic_nodes(TOPIC)]
    assert posts[0]["id"] == "3934230212413674973" and posts[0]["__typename"] == "XDTGraphVideo"
    assert posts[0]["owner"]["id"] == "900" and posts[1]["is_video"] is False
    assert posts[0]["video_play_count"] == 7162320 and "video_play_count" not in posts[1]
    assert_conforms(_sc({"hashtag": "funnydogs", "posts": posts}), "/v1/instagram/search/hashtag",
                    min_coverage=0.25)


def test_topic_title():
    props = ('<meta property="og:title" content="Goldenretriever &#x2022; 203M reels on Instagram" />'
             '"page_logging":{"name":"popularSearchPage","params":{}},"title":"Golden \\"Goldie\\" Retriever"')
    assert ig._topic_title(props) == 'Golden "Goldie" Retriever'
    assert ig._topic_title(TOPIC_HTML) == "Golden Retriever"
    assert ig._topic_title(HUB_HTML) == "Basketball"
    assert ig._topic_title('<meta property="og:title" content="Funnydogs" />') == "Funnydogs"
    assert ig._topic_title("<html></html>") is None


def test_map_popular_category_hub():
    nodes = ig.topic_nodes(HUB)
    assert [n["pk"] for n in nodes] == ["3871956735859414535", "2"]  # all sections, deduped
    body = ig.map_popular("basketball", HUB, HUB_HTML)
    assert body["title"] == "Basketball" and body["description"] == {"plain_text": "Basketball is a team sport."}
    assert body["suggested_terms"] == ["the tallest basketball player", "st john basketball"]
    assert [p["shortcode"] for p in body["posts"]] == ["DW79CMClNYH", "DXXXXXXXXXX"]
    assert body["posts"][0]["type"] == "reel" and body["posts"][0]["play_count"] == 21447
    assert "total_media_count" not in body
    assert_conforms(_sc(body), "/v1/instagram/search/popular", min_coverage=0.4)


# --- routes, offline (fetchers stubbed with the data above) ---------------------------------


def _err(r, status: int, code: str | None = None) -> dict[str, Any]:
    assert r.status_code == status, (r.status_code, r.text[:300])
    body = r.json()
    assert body["success"] is False
    if code:
        assert body["error"] == code, body
    return body


@pytest.fixture
def stubbed(monkeypatch):
    calls: list[str] = []

    async def fetch_post(url):
        calls.append(url)
        return (copy.deepcopy(CAROUSEL), None) if CAROUSEL_CODE in url else (copy.deepcopy(REEL), REEL_MEDIA)

    async def fetch_profile(handle, tab=""):
        calls.append(f"{handle}/{tab}")
        return copy.deepcopy(REELS_TAB if tab == "reels" else PROFILE), PROFILE_HTML

    async def fetch_topic(query):
        calls.append(query)
        return copy.deepcopy(TOPIC), TOPIC_HTML

    async def with_ydl(platform, fn, **opts):
        calls.append("reels_media")
        return {"reels": {f"highlight:{HIGHLIGHT_ID}": copy.deepcopy(HIGHLIGHT)}, "status": "ok"}

    monkeypatch.setattr(ig, "fetch_post", fetch_post)
    monkeypatch.setattr(ig, "fetch_profile", fetch_profile)
    monkeypatch.setattr(ig, "fetch_topic", fetch_topic)
    monkeypatch.setattr(ig, "with_ydl", with_ydl)
    return calls


def test_routes_offline(client, stubbed):
    body = client.get("/v1/instagram/post", params={"url": REEL_CODE}).json()
    assert body["data"]["xdt_shortcode_media"]["id"] == REEL_PK
    assert_conforms(body, "/v1/instagram/post", min_coverage=0.5)

    body = client.get("/v2/instagram/post/comments", params={"url": REEL_CODE}).json()
    assert body["comments"][0]["id"] == COMMENT["pk"] and "cursor" not in body
    assert_conforms(body, "/v2/instagram/post/comments", min_coverage=0.7)

    body = client.get("/v1/instagram/profile", params={"handle": "@jane"}).json()
    assert body["data"]["user"]["username"] == "jane"
    assert_conforms(body, "/v1/instagram/profile", min_coverage=0.25)

    body = client.get("/v1/instagram/profile/post-count", params={"handle": "jane"}).json()
    assert (body["handle"], body["media_count"], body["is_estimated"]) == ("jane", 1800, False)
    assert_conforms(body, "/v1/instagram/profile/post-count", min_coverage=0.5)

    stubbed.clear()
    body = client.get("/v2/instagram/user/posts", params={"handle": "jane"}).json()
    assert body["num_results"] == 3 and "next_max_id" not in body and body["items"][0]["like_count"] == 1743588
    assert stubbed == ["jane/", "jane/reels"]
    assert_conforms(body, "/v2/instagram/user/posts", min_coverage=0.2)

    body = client.get("/v1/instagram/user/reels", params={"handle": "https://www.instagram.com/jane/reels/"}).json()
    assert body["items"][0]["code"] == "DQhKIRakcbY" and "max_id" not in body
    assert stubbed[-1] == "jane/reels"
    assert_conforms(body, "/v1/instagram/user/reels", min_coverage=0.1)

    body = client.get("/v1/instagram/user/highlights", params={"handle": "jane"}).json()
    assert body["highlights"][0]["id"] == HIGHLIGHT_ID
    assert_conforms(body, "/v1/instagram/user/highlights", min_coverage=0.8)

    r = client.get("/v1/instagram/user/highlight/detail", params={"id": f"highlight:{HIGHLIGHT_ID}"})
    body = r.json()
    assert body["success"] is True and body["id"] == f"highlight:{HIGHLIGHT_ID}" and "strong_id__" not in body

    body = client.get("/v1/instagram/search/popular", params={"query": "golden retriever"}).json()
    assert body["query"] == "golden retriever" and len(body["posts"]) == 2 and "cursor" not in body
    assert_conforms(body, "/v1/instagram/search/popular", min_coverage=0.7)

    body = client.get("/v1/instagram/search/hashtag", params={"hashtag": "#funnydogs"}).json()
    assert body["hashtag"] == "funnydogs" and len(body["posts"]) == 2 and "cursor" not in body
    assert_conforms(body, "/v1/instagram/search/hashtag", min_coverage=0.25)
    reels = client.get("/v1/instagram/search/hashtag", params={"hashtag": "funnydogs", "media_type": "reels"}).json()
    assert [p["shortcode"] for p in reels["posts"]] == ["DaZMYijhtXd"]

    body = client.get("/v2/instagram/reels/search", params={"query": "funny dogs", "page": "1"}).json()
    assert [r["shortcode"] for r in body["reels"]] == ["DaZMYijhtXd"] and body["reels"][0]["is_video"] is True
    assert body["reels"][0]["video_play_count"] == 7162320
    assert stubbed[-1] == "funny dogs"
    assert_conforms(body, "/v2/instagram/reels/search", min_coverage=0.25)


def test_topics_are_requested_lower_case(client, stubbed):
    assert client.get("/v1/instagram/search/popular", params={"query": "Golden  Retriever"}).json()["query"] == (
        "golden retriever")
    assert client.get("/v1/instagram/search/hashtag", params={"hashtag": "#FunnyDogs"}).json()["hashtag"] == (
        "funnydogs")
    assert client.get("/v2/instagram/reels/search", params={"query": "FUNNYDOGS"}).json()["success"] is True
    assert stubbed == ["golden retriever", "funnydogs", "funnydogs"]


def test_topic_page_urls(client, stubbed):
    """The address of a tag page (or of the Popular page Instagram sends logged-out visitors on to) was a 400."""
    tag = "https://www.instagram.com/explore/tags/FunnyDogs/"
    assert client.get("/v1/instagram/search/hashtag", params={"hashtag": tag}).json()["hashtag"] == "funnydogs"
    popular = "https://www.instagram.com/popular/basketball/?utm_source=explore_tag"
    assert client.get("/v1/instagram/search/popular", params={"query": popular}).json()["query"] == "basketball"
    assert client.get("/v2/instagram/reels/search", params={"query": tag}).json()["success"] is True
    assert stubbed == ["funnydogs", "basketball", "funnydogs"]


def test_topic_turkish_dotted_capital_i(client, stubbed):
    """'İ'.lower() is 'i' + U+0307: the hashtag was refused as not a single word, the topics requested a 404 path."""
    assert client.get("/v1/instagram/search/hashtag", params={"hashtag": "İstanbul"}).json()["hashtag"] == "istanbul"
    assert client.get("/v1/instagram/search/popular", params={"query": "İstanbul"}).json()["query"] == "istanbul"
    assert client.get("/v2/instagram/reels/search", params={"query": "İSTANBUL"}).json()["success"] is True
    assert client.get("/v1/instagram/search/hashtag", params={"hashtag": "भारत"}).json()["hashtag"] == "भारत"
    assert stubbed == ["istanbul", "istanbul", "istanbul", "भारत"]


def test_private_account_without_reels_or_highlights_is_403(client, stubbed, monkeypatch):
    private = {**{k: PROFILE[k] for k in ("pk", "id", "username", "full_name")}, "is_private": True,
               "has_any_clips": False, "polaris_clips_connection": {"edges": []}, "lox_highlights_connection": {
                   "edges": []}}

    async def fetch_profile(handle, tab=""):
        return copy.deepcopy(private), PROFILE_HTML

    monkeypatch.setattr(ig, "fetch_profile", fetch_profile)
    for path in ("/v1/instagram/user/reels", "/v1/instagram/user/highlights"):
        assert _err(client.get(path, params={"handle": "krys_midnight"}), 403, "forbidden")["message"] == (
            "This account is private")

    public_empty = {**private, "is_private": False}
    monkeypatch.setattr(ig, "fetch_profile", lambda handle, tab="": _async((copy.deepcopy(public_empty), "")))
    assert client.get("/v1/instagram/user/reels", params={"handle": "lilaltomtas"}).json()["items"] == []
    assert client.get("/v1/instagram/user/highlights", params={"handle": "lilaltomtas"}).json()["highlights"] == []

    # a private account's visible (e.g. collab) items are still returned
    visible = {**REELS_TAB, **PROFILE, "polaris_clips_connection": REELS_TAB["polaris_clips_connection"],
               "is_private": True}
    monkeypatch.setattr(ig, "fetch_profile", lambda handle, tab="": _async((copy.deepcopy(visible), "")))
    assert len(client.get("/v1/instagram/user/reels", params={"handle": "jane"}).json()["items"]) == 1
    assert len(client.get("/v1/instagram/user/highlights", params={"handle": "jane"}).json()["highlights"]) == 1


async def _async(value: Any) -> Any:
    return value


def test_user_posts_without_reels_tab(client, stubbed, monkeypatch):
    async def fetch_profile(handle, tab=""):
        stubbed.append(f"{handle}/{tab}")
        if tab:
            raise UpstreamBlocked("login redirect")
        return copy.deepcopy(PROFILE), PROFILE_HTML

    monkeypatch.setattr(ig, "fetch_profile", fetch_profile)
    body = client.get("/v2/instagram/user/posts", params={"handle": "jane"}).json()
    assert body["success"] is True and body["num_results"] == 3 and "like_count" not in body["items"][0]

    no_reels = copy.deepcopy(PROFILE)
    no_reels["polaris_ordered_timeline_connection"]["edges"] = no_reels[
        "polaris_ordered_timeline_connection"]["edges"][1:]

    async def images_only(handle, tab=""):
        stubbed.append(f"{handle}/{tab}")
        return copy.deepcopy(no_reels), PROFILE_HTML

    monkeypatch.setattr(ig, "fetch_profile", images_only)
    stubbed.clear()
    assert client.get("/v2/instagram/user/posts", params={"handle": "jane"}).json()["num_results"] == 2
    assert stubbed == ["jane/"]  # no reels among the posts: the reels tab isn't requested


def test_comments_with_cookies_use_comments_api(client, stubbed, monkeypatch):
    seen: list[Any] = []

    async def with_ydl(platform, fn, **opts):
        seen.append(fn)
        return {"comments": [COMMENT], "has_more_comments": True}

    monkeypatch.setattr(ig, "with_ydl", with_ydl)
    body = client.get("/v2/instagram/post/comments", params={"url": CAROUSEL_CODE}).json()
    assert [c["id"] for c in body["comments"]] == [COMMENT["pk"]] and len(seen) == 1
    assert_conforms(body, "/v2/instagram/post/comments", min_coverage=0.7)

    requests: list[tuple[str, dict[str, Any]]] = []
    ie = SimpleNamespace(_API_BASE_URL="https://i.instagram.com/api/v1", _api_headers={"X-IG-App-ID": "1"},
                         _is_logged_in=True, _download_webpage_handle=None,
                         _download_json=lambda url, *a, **kw: requests.append((url, kw)) or {})
    ig._comments_api_flow(CAROUSEL_CODE)(SimpleNamespace(get_info_extractor=lambda name: ie, urlopen=None))
    url, kwargs = requests[0]
    assert url.startswith("https://i.instagram.com/api/v1/media/") and "/comments/?can_support_threading" in url
    assert url.split("/media/")[1].split("/")[0].isdigit() and kwargs["headers"] == {"X-IG-App-ID": "1"}


@pytest.fixture
def pages(monkeypatch):
    """fetch_page with the network replaced: {url: (html, final_url)}; records the requested URLs."""
    served: dict[str, tuple[str, str]] = {}
    requested: list[str] = []

    async def with_ydl(platform, url, **opts):
        requested.append(url)
        return served[url]

    monkeypatch.setattr(ig, "_page_flow", lambda ie_name, url, item_id: url)
    monkeypatch.setattr(ig, "with_ydl", with_ydl)
    return SimpleNamespace(served=served, requested=requested)


def test_fetch_page_outcomes(pages):
    import asyncio

    url = "https://www.instagram.com/developer/"
    pages.served[url] = ("<html>", "https://developers.facebook.com/")
    with pytest.raises(NotFound):
        asyncio.run(ig.fetch_page("InstagramUser", url, "developer", "Profile"))
    pages.served[url] = ("<html>", "https://www.instagram.com/accounts/login/?next=%2Fdeveloper%2F")
    with pytest.raises(UpstreamBlocked):
        asyncio.run(ig.fetch_page("InstagramUser", url, "developer", "Profile"))
    pages.served[url] = ('{"pageID":"httpErrorPage"}', url)
    with pytest.raises(NotFound):
        asyncio.run(ig.fetch_page("InstagramUser", url, "developer", "Profile"))
    pages.served[url] = (_sjs({"data": {"xig_user_by_username": {"pk": "1"}}}), url)
    assert asyncio.run(ig.fetch_profile("developer"))[0] == {"pk": "1"}
    pages.served["https://www.instagram.com/developer/reels/"] = pages.served[url]
    asyncio.run(ig.fetch_profile("developer", "reels"))
    assert pages.requested[-1] == "https://www.instagram.com/developer/reels/"
    pages.served[url] = (_sjs({"data": {"xig_user_by_username": {"pk": "2"}}},
                              module="RelayPrefetchedStreamCache@3a1eccb0df6641a8a1c10367033d9b67"), url)
    assert asyncio.run(ig.fetch_profile("developer"))[0] == {"pk": "2"}
    pages.served[url] = ('<script>{"pageID":"profilePage"}</script>', url)
    with pytest.raises(UpstreamError):
        asyncio.run(ig.fetch_profile("developer"))


def test_fetch_topic_retries_popular_page(pages):
    import asyncio

    login = ("<html>", "https://www.instagram.com/accounts/login/")
    pages.served["https://www.instagram.com/explore/tags/caf%C3%A9/"] = login
    pages.served["https://www.instagram.com/popular/caf%C3%A9/"] = (
        '{"pageID":"httpErrorPage"}', "https://www.instagram.com/popular/caf%C3%A9/")
    with pytest.raises(NotFound):
        asyncio.run(ig.fetch_topic("café"))
    assert pages.requested == ["https://www.instagram.com/explore/tags/caf%C3%A9/",
                               "https://www.instagram.com/popular/caf%C3%A9/"]

    pages.served["https://www.instagram.com/explore/tags/basketball/"] = (
        _sjs({"data": HUB}) + HUB_HTML, "https://www.instagram.com/popular/basketball/?utm_source=explore_tag")
    data, _ = asyncio.run(ig.fetch_topic("basketball"))
    assert "ig_popular_search_category_hub" in data

    pages.served["https://www.instagram.com/explore/tags/x/"] = login
    pages.served["https://www.instagram.com/popular/x/"] = login
    with pytest.raises(UpstreamBlocked):
        asyncio.run(ig.fetch_topic("x"))


# Restricted (age-gated) profiles: logged-out pages with pageID profilePage, no xig_user_by_username, and either a
# props "gating" object (heineken) or only the server-rendered "Restricted profile" icon (jackdaniels_us).
_NO_USER = _sjs({"data": {"xig_user_by_username": None}}) + '<script>{"pageID":"profilePage"}</script>'
GATED_HTML = (_NO_USER + '<script>{"props":{"id":"264204243","gating":{"title":"Restricted profile","description":'
              '"You must be 21 years old or over to see this profile","category_id":null,"gating_type_thrift":3}}}'
              '</script><meta property="og:description" content="1M Followers, 9 Following, 1,127 Posts - x" />')
RESTRICTED_ICON_HTML = (_NO_USER + '<script>{"gating":null}</script><svg aria-label="Restricted profile" role="img">'
                        '<title>Restricted profile</title></svg><meta property="og:description" '
                        'content="713K Followers, 70 Following, 2,370 Posts - See Instagram photos" />')


def test_restricted_profile_is_403(client, pages):
    """gap-instagram-2: a restricted profile answered 500 no_profile_data on every profile route."""
    for handle, html in (("heineken", GATED_HTML), ("jackdaniels_us", RESTRICTED_ICON_HTML)):
        for tab in ("", "reels/"):
            pages.served[f"{ig.WEB}/{handle}/{tab}"] = (html, f"{ig.WEB}/{handle}/{tab}")
        for path in ("/v1/instagram/profile", "/v2/instagram/user/posts", "/v1/instagram/user/reels",
                     "/v1/instagram/user/highlights"):
            body = _err(client.get(path, params={"handle": handle}), 403, "restricted_profile")
            assert "Restricted profile" in body["message"] and "logged-out" in body["message"], body
    assert "You must be 21 years old" in _err(
        client.get("/v1/instagram/profile", params={"handle": "heineken"}), 403)["message"]
    assert ig.profile_restriction(PROFILE_HTML) is None
    assert ig.profile_restriction('{"gating":null}') is None


def test_restricted_profile_post_count_reads_the_page(client, pages):
    for handle, html, count in (("heineken", GATED_HTML, 1127), ("jackdaniels_us", RESTRICTED_ICON_HTML, 2370)):
        pages.served[f"{ig.WEB}/{handle}/"] = (html, f"{ig.WEB}/{handle}/")
        body = client.get("/v1/instagram/profile/post-count", params={"handle": handle}).json()
        assert (body["success"], body["media_count"], body["is_estimated"]) == (True, count, False), body
    pages.served[f"{ig.WEB}/nocount/"] = (GATED_HTML.split("<meta")[0], f"{ig.WEB}/nocount/")
    _err(client.get("/v1/instagram/profile/post-count", params={"handle": "nocount"}), 403, "restricted_profile")


def test_non_profile_pages_are_not_profiles(client, pages):
    """gap-instagram-3: an app page at /<name>/ (e.g. /meta_verified/, pageID MetaVerifiedUpdate) answered 500."""
    pages.served[f"{ig.WEB}/some_app/"] = ('<title>Meta Verified • Instagram</title>{"pageID":"MetaVerifiedUpdate"}',
                                           f"{ig.WEB}/some_app/")
    for path in ("/v1/instagram/profile", "/v1/instagram/profile/post-count", "/v1/instagram/user/highlights"):
        _err(client.get(path, params={"handle": "some_app"}), 404, "not_found")
    # the login page served in place of the profile is still the logged-out rate limit, not a missing profile
    pages.served[f"{ig.WEB}/walled/"] = ('{"pageID":"CAAIGLoginHomepage"}', f"{ig.WEB}/walled/")
    _err(client.get("/v1/instagram/profile", params={"handle": "walled"}), 503, "upstream_blocked")
    # app routes that redirect to login are refused before any request (they'd read as a rate limit)
    pages.requested.clear()
    for handle in ("notifications", "meta_verified", "Notifications"):
        _err(client.get("/v1/instagram/profile", params={"handle": handle}), 400, "bad_handle")
    assert pages.requested == []


def test_hashtag_cursor_is_a_page_number(client, stubbed):
    """gap-instagram-1: ScrapeCreators' cursor is a Google results page (1-11); 12 or more is its documented 400."""
    for cursor in ("12", "99", "0", "-1", "abc"):
        body = _err(client.get("/v1/instagram/search/hashtag", params={"hashtag": "dogs", "cursor": cursor}), 400,
                    "bad_cursor")
        assert "cursor" in body["message"]
    for cursor in ("2", "11"):
        _err(client.get("/v1/instagram/search/hashtag", params={"hashtag": "dogs", "cursor": cursor}), 501,
             "pagination_unavailable")
    assert stubbed == []
    for cursor in ("1", ""):
        assert client.get("/v1/instagram/search/hashtag", params={"hashtag": "dogs", "cursor": cursor}).json()[
            "posts"]
    assert stubbed == ["dogs", "dogs"]


# --- a session Instagram rejects (gap-instagram-4): login redirects with a sessionid cookie ----------------------

_LOGIN = "https://www.instagram.com/accounts/login/?next=%2Fapi%2Fv1%2Fmedia%2F1%2Finfo%2F"


def _session_ie(logged_in: bool, final_url: str = _LOGIN, **extra: Any) -> SimpleNamespace:
    """An extractor stand-in whose every download lands on `final_url` and whose JSON calls then fail to parse."""
    from yt_dlp.utils import ExtractorError

    ie = SimpleNamespace(_is_logged_in=logged_in, _can_impersonate=False, _is_web_app=True, _api_headers={},
                         _API_BASE_URL="https://www.instagram.com/api/v1", _extract_product=None, **extra)
    ie._download_webpage_handle = lambda url, *a, **kw: ("<html>login</html>", SimpleNamespace(url=final_url))

    def download_json(url, *args, **kwargs):
        ie._download_webpage_handle(url)
        raise ExtractorError("Failed to parse JSON")

    def extract(url):
        download_json(f"{ie._API_BASE_URL}/media/1/info/")

    ie._download_json, ie.extract = download_json, extract
    return ie


def _ydl(ie: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(get_info_extractor=lambda name: ie, urlopen=lambda req: None)


def test_rejected_session_is_a_cookie_error(monkeypatch):
    from yt_dlp.utils import ExtractorError

    monkeypatch.setattr(ig, "get_settings", lambda: SimpleNamespace(cookie_file=lambda platform: "/x/ig.txt"))
    flows = [lambda: ig._page_flow("InstagramUser", f"{ig.WEB}/jane/", "jane"), lambda: ig._post_flow(REEL_CODE),
             lambda: ig._comments_api_flow(REEL_CODE), lambda: ig._reels_media_flow(HIGHLIGHT_ID)]
    for flow in flows:
        with pytest.raises(ig.SessionRejected) as exc:
            flow()(_ydl(_session_ie(True)))
        err = exc.value
        assert (err.status, err.code) == (500, "cookies_rejected")
        assert "SOCIALSCRAPE_COOKIES_INSTAGRAM" in err.message and "rate limit" not in err.message
        assert "anonymous" not in err.message
    # without a session the same redirect stays what it was: fetch_page's logged-out rate limit, yt-dlp's errors
    assert ig._page_flow("InstagramUser", f"{ig.WEB}/jane/", "jane")(_ydl(_session_ie(False)))[1] == _LOGIN
    for flow in flows[1:]:
        with pytest.raises(ExtractorError):
            flow()(_ydl(_session_ie(False)))
        with pytest.raises(ExtractorError):  # logged in, but a real parse failure (no login redirect)
            flow()(_ydl(_session_ie(True, f"{ig.WEB}/api/v1/media/1/info/")))


def _share_ie(final_url: str, logged_in: bool = False) -> SimpleNamespace:
    """An extractor stand-in whose share-link request ends on `final_url` and that extracts REEL from any URL."""
    ie = SimpleNamespace(_is_logged_in=logged_in, _can_impersonate=True, _download_webpage_handle=None,
                         _extract_product=lambda info, *args, **kwargs: info, requests=[], extracted=[])

    def request_webpage(req, video_id, note=None, **kwargs):
        ie.requests.append((req.method, req.url, kwargs.get("impersonate")))
        return SimpleNamespace(url=final_url)

    def extract(url):
        ie.extracted.append(url)
        ie._extract_product(copy.deepcopy(REEL))

    ie._request_webpage, ie.extract = request_webpage, extract
    return ie


def test_share_link_is_followed_to_its_post(monkeypatch):
    """fix3: yt-dlp leaves share links to its generic extractor, which follows their redirect, and the service runs
    Instagram's extractors only. A share link redirects (anonymously) to the post's /reel/ URL."""
    ie = _share_ie(f"{ig.WEB}/reel/{REEL_CODE}/?igsh=ZGUz")
    assert ig._post_flow(SHARE)(_ydl(ie))[0]["code"] == REEL_CODE
    assert ie.requests == [("HEAD", SHARE, True)] and ie.extracted == [f"{ig.WEB}/p/{REEL_CODE}/"]

    ie = _share_ie(f"{ig.WEB}/reel/{REEL_CODE}/")
    ig._post_flow(f"{ig.WEB}/p/{REEL_CODE}/")(_ydl(ie))  # any other URL goes straight to InstagramIE
    assert ie.requests == [] and ie.extracted == [f"{ig.WEB}/p/{REEL_CODE}/"]

    # an unknown share id answers 200 without a redirect; a link can also lead somewhere that isn't a post
    for final in (SHARE, f"{ig.WEB}/jane/", f"{ig.WEB}/stories/jane/3700000000000000000/", "https://example.com/"):
        ie = _share_ie(final)
        with pytest.raises(NotFound, match="does not lead to an Instagram post"):
            ig._post_flow(SHARE)(_ydl(ie))
        assert ie.extracted == []

    for final in (_LOGIN, f"{ig.WEB}/"):  # yt-dlp's login redirects: the logged-out rate limit, or a dead session
        with pytest.raises(UpstreamBlocked, match="login page"):
            ig._post_flow(SHARE)(_ydl(_share_ie(final)))
        monkeypatch.setattr(ig, "get_settings", lambda: SimpleNamespace(cookie_file=lambda platform: "/x/ig.txt"))
        with pytest.raises(ig.SessionRejected):
            ig._post_flow(SHARE)(_ydl(_share_ie(final, logged_in=True)))


def test_share_link_routes(client, stubbed, monkeypatch):
    link = f"{SHARE}/?igsh=ZGUz"
    body = client.get("/v1/instagram/post", params={"url": link}).json()
    assert body["data"]["xdt_shortcode_media"]["shortcode"] == REEL_CODE
    assert client.get("/v2/instagram/post/comments", params={"url": link}).json()["comments"]
    assert stubbed == [link, link]  # fetch_post follows the link itself

    seen: list[str] = []

    async def fake_transcribe(url, **kwargs):
        seen.append(url)
        return SimpleNamespace(text="hello")

    monkeypatch.setattr(transcribe, "enabled", lambda: True)
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    body = client.get("/v2/instagram/media/transcript", params={"url": link}).json()
    assert body["transcripts"] == [{"id": REEL_PK, "shortcode": REEL_CODE, "text": "hello"}]
    assert seen == [f"{ig.WEB}/p/{REEL_CODE}/"]  # the post itself: yt-dlp can't open the share link


def test_rejected_session_envelope(client, monkeypatch):
    async def rejected(*args, **kwargs):
        raise ig.SessionRejected()

    monkeypatch.setattr(ig, "fetch_post", rejected)
    body = _err(client.get("/v1/instagram/post", params={"url": REEL_CODE}), 500, "cookies_rejected")
    assert "session cookies" in body["message"] and "Replace" in body["message"]

    requested: list[str] = []

    async def with_ydl(platform, fn, **opts):
        requested.append(fn)
        raise ig.SessionRejected()

    monkeypatch.setattr(ig, "_page_flow", lambda ie_name, url, item_id: url)
    monkeypatch.setattr(ig, "with_ydl", with_ydl)
    import asyncio

    with pytest.raises(ig.SessionRejected):  # /explore/tags/ then /popular/, as for the logged-out redirect
        asyncio.run(ig.fetch_topic("cats"))
    assert requested == [f"{ig.WEB}/explore/tags/cats/", f"{ig.WEB}/popular/cats/"]


def test_post_count_estimate_needs_opt_in(client, stubbed, monkeypatch):
    async def fetch_profile(handle):
        return copy.deepcopy(PROFILE), PROFILE_HTML.replace("1,800 Posts", "12K Posts")

    monkeypatch.setattr(ig, "fetch_profile", fetch_profile)
    body = _err(client.get("/v1/instagram/profile/post-count", params={"handle": "jane"}), 422, "estimate_only")
    assert body["estimated_media_count"] == 12000
    ok = client.get("/v1/instagram/profile/post-count", params={"handle": "jane", "allow_estimated": "true"}).json()
    assert (ok["media_count"], ok["is_estimated"]) == (12000, True)


def test_transcript_route(client, stubbed, monkeypatch):
    off = dataclasses.replace(get_settings(), transcribe_backend="none")  # whatever the shell exports
    monkeypatch.setattr(transcribe, "get_settings", lambda: off)
    _err(client.get("/v2/instagram/media/transcript", params={"url": REEL_CODE}), 501, "transcription_disabled")
    assert stubbed == []  # refused before any Instagram request

    seen: list[dict[str, Any]] = []

    async def fake_transcribe(url, **kwargs):
        seen.append({"url": url, **kwargs})
        return SimpleNamespace(text="hello there")

    monkeypatch.setattr(transcribe, "enabled", lambda: True)
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    body = client.get("/v2/instagram/media/transcript", params={"url": REEL_CODE}).json()
    assert body["transcripts"] == [{"id": REEL_PK, "shortcode": REEL_CODE, "text": "hello there"}]
    assert seen[0]["duration"] == pytest.approx(84.666664) and seen[0]["allow_long"] is False
    assert_conforms(body, "/v2/instagram/media/transcript", min_coverage=0.9)

    body = client.get("/v2/instagram/media/transcript",
                      params={"url": CAROUSEL_CODE, "allow_long_transcription": "true"}).json()
    assert body["transcripts"] == [{"id": "2", "shortcode": CAROUSEL_CODE, "text": "hello there"}]
    assert seen[1]["playlist_items"] == "2" and seen[1]["allow_long"] is True

    async def image_post(url):
        return {"media_type": 1, "code": "IMG01"}, None

    monkeypatch.setattr(ig, "fetch_post", image_post)
    _err(client.get("/v2/instagram/media/transcript", params={"url": "IMG01"}), 404, "no_video")


def test_transcript_skips_videos_without_audio(client, stubbed, monkeypatch):
    """natgeo DekS-u4GltE: 7 carousel videos, children 2-7 has_audio=false (ffmpeg found no audio stream -> 500)."""
    video = {"media_type": 2, "video_versions": [{"url": "https://cdn.example/v.mp4"}], "video_duration": 10.0}
    carousel = {"pk": "9", "code": "DekS-u4GltE", "media_type": 8, "carousel_media": [
        {**video, "pk": "1", "code": "DekS7t7AHfZ", "has_audio": True},
        {**video, "pk": "2", "code": "DekS743gFGX", "has_audio": False},
        {"pk": "3", "media_type": 1, "display_uri": "https://cdn.example/i.jpg"},
        {**video, "pk": "4", "code": "DekS8AAAAAA", "has_audio": False}]}
    seen: list[str] = []

    async def fetch_post(url):
        return copy.deepcopy(carousel), None

    async def fake_transcribe(url, **kwargs):
        seen.append(kwargs["playlist_items"])
        return SimpleNamespace(text="hello")

    monkeypatch.setattr(ig, "fetch_post", fetch_post)
    monkeypatch.setattr(transcribe, "enabled", lambda: True)
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    body = client.get("/v2/instagram/media/transcript", params={"url": "DekS-u4GltE"}).json()
    assert body["transcripts"] == [{"id": "1", "shortcode": "DekS7t7AHfZ", "text": "hello"},
                                   {"id": "2", "shortcode": "DekS743gFGX", "text": None},
                                   {"id": "4", "shortcode": "DekS8AAAAAA", "text": None}]
    assert seen == ["1"]
    assert_conforms(body, "/v2/instagram/media/transcript", min_coverage=0.9)

    carousel = {**video, "pk": "5", "code": "SILENT", "has_audio": False}
    body = client.get("/v2/instagram/media/transcript", params={"url": "SILENT"}).json()
    assert body["transcripts"] == [{"id": "5", "shortcode": "SILENT", "text": None}] and seen == ["1"]


@pytest.mark.parametrize("path,params", [
    ("/v1/instagram/post", {"url": "https://www.tiktok.com/@jane/video/7300000000000000000"}),
    ("/v2/instagram/post/comments", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
    ("/v2/instagram/media/transcript", {"url": "https://x.com/jane/status/1"}),
    ("/v1/instagram/profile", {"handle": "https://www.tiktok.com/@jane"}),
    ("/v1/instagram/profile/post-count", {"handle": "https://www.youtube.com/@jane"}),
    ("/v2/instagram/user/posts", {"handle": "jane doe"}),
    ("/v1/instagram/user/highlights", {"handle": "https://www.threads.net/@jane"}),
    ("/v1/instagram/user/highlight/detail", {"id": "https://www.tiktok.com/@jane"}),
    ("/v1/instagram/search/popular", {"query": "a/b"}),
    ("/v1/instagram/search/hashtag", {"hashtag": "two words"}),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "media_type": "photos"}),
    ("/v1/instagram/profile", {"handle": ".."}),
    ("/v1/instagram/user/reels", {"handle": "jane doe"}),
    ("/v2/instagram/reels/search", {"query": "a/b"}),
    ("/v2/instagram/reels/search", {"query": "dogs", "page": "12"}),
    ("/v2/instagram/reels/search", {"query": "dogs", "page": "0"}),
    ("/v2/instagram/reels/search", {"query": "dogs", "page": "two"}),
    ("/v2/instagram/reels/search", {"query": "dogs", "page": "inf"}),
    ("/v1/instagram/post", {"url": "https://www.instagram.com/p/a.b.c/"}),
    ("/v2/instagram/post/comments", {"url": "https://www.instagram.com/p/%3Cscript%3E/"}),
    ("/v2/instagram/media/transcript", {"url": "https://www.instagram.com/p/../"}),
    ("/v1/instagram/profile", {"handle": "challenge"}),
    ("/v1/instagram/profile", {"handle": "graphql"}),
    ("/v1/instagram/profile/post-count", {"handle": "qr"}),
    ("/v2/instagram/user/posts", {"handle": "your_activity"}),
    ("/v1/instagram/user/reels", {"handle": "nametag"}),
    ("/v1/instagram/user/highlights", {"handle": "press"}),
    ("/v1/instagram/user/highlight/detail", {"id": FULLWIDTH_ID}),
    ("/v2/instagram/reels/search", {"query": "dogs", "date_posted": "last-day"}),
    ("/v2/instagram/reels/search", {"query": "dogs", "date_posted": "last-hour"}),
    ("/v2/instagram/reels/search", {"query": "dogs", "date_posted": "junk", "page": "2"}),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "date_posted": "yesterday"}),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "date_posted": "LAST-WEEK"}),
    # ScrapeCreators: "cursor 12 or greater returns a 400 response" (the cursor is a page number, 1-11)
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "cursor": "12"}),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "cursor": "99"}),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "cursor": "0"}),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "cursor": "abc"}),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "cursor": "inf"}),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "cursor": "12", "date_posted": "last-week"}),
])
def test_bad_input_is_400(client, stubbed, path, params):
    _err(client.get(path, params=params), 400)
    assert stubbed == []


@pytest.mark.parametrize("path,params", [
    ("/v1/instagram/post", {}), ("/v2/instagram/post/comments", {}), ("/v2/instagram/media/transcript", {}),
    ("/v1/instagram/profile", {}), ("/v1/instagram/profile/post-count", {}), ("/v2/instagram/user/posts", {}),
    ("/v1/instagram/user/highlights", {}), ("/v1/instagram/user/highlight/detail", {}),
    ("/v1/instagram/search/popular", {}), ("/v1/instagram/search/hashtag", {}),
    ("/v1/instagram/user/reels", {}), ("/v2/instagram/reels/search", {}),
])
def test_missing_params_is_400(client, stubbed, path, params):
    _err(client.get(path, params=params), 400)


@pytest.mark.parametrize("path,params,code", [
    ("/v2/instagram/post/comments", {"url": REEL_CODE, "cursor": "abc"}, "pagination_unavailable"),
    ("/v2/instagram/post/comments", {"url": REEL_CODE, "include_replies": "true"}, None),
    ("/v1/instagram/post", {"url": REEL_CODE, "download_media": "true"}, None),
    ("/v2/instagram/user/posts", {"handle": "jane", "next_max_id": "QVFE"}, "pagination_unavailable"),
    ("/v1/instagram/search/popular", {"query": "dogs", "cursor": "abc"}, "pagination_unavailable"),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "cursor": "2"}, "pagination_unavailable"),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "cursor": "11"}, "pagination_unavailable"),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "date_posted": "last-week"}, None),
    ("/v1/instagram/search/hashtag", {"hashtag": "dogs", "date_posted": "last-hour"}, None),
    ("/v1/instagram/user/highlights", {"user_id": JANE_PK}, None),
    ("/v1/instagram/user/reels", {"user_id": JANE_PK}, None),
    ("/v1/instagram/user/reels", {"handle": "jane", "max_id": "QVFE"}, "pagination_unavailable"),
    ("/v2/instagram/reels/search", {"query": "dogs", "page": "2"}, "pagination_unavailable"),
    ("/v2/instagram/reels/search", {"query": "dogs", "date_posted": "last-week"}, None),
    ("/v2/instagram/reels/search", {"query": "dogs", "date_posted": "last-year", "page": "2"},
     "pagination_unavailable"),
])
def test_unavailable_options_are_501(client, stubbed, path, params, code):
    _err(client.get(path, params=params), 501, code or "not_supported")
    assert stubbed == []


def test_instagram_catalog_paths_are_routed(client):
    """Instagram slice of test_core.test_every_catalog_path_is_routed (reads paths from the OpenAPI schema)."""
    paths = set(client.app.openapi()["paths"])
    rows = {r["path"]: r for r in client.get("/meta/coverage").json()["endpoints"]}
    ours = [e["path"] for e in CATALOG if e["path"].split("/")[2] == "instagram"]
    assert len(ours) == 20
    for path in ours:
        assert path in paths, path
        row = rows[path]
        assert row["notes"], path
        if row["status"] == "implemented":
            assert row["source"], path
        else:
            assert path in ig.UNSUPPORTED, path
            r = client.get(path)
            assert r.status_code == 501 and r.json()["error"] == "not_supported", path


# --- live (anonymous, shared IP: one request per URL, spaced out) -----------------------------

_LIVE: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[int, dict[str, Any]]] = {}
_LAST = [0.0]
_SPACING = 4.0


def live_get(client, path: str, **params: str) -> tuple[int, dict[str, Any]]:
    key = (path, tuple(sorted(params.items())))
    if key not in _LIVE:
        wait = _LAST[0] + _SPACING - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        r = client.get(path, params=params)
        _LAST[0] = time.monotonic()
        _LIVE[key] = (r.status_code, r.json())
    status, body = _LIVE[key]
    if status == 503:
        pytest.skip(f"Instagram rate-limited this IP: {body.get('message')}")
    return status, body


@pytest.mark.live
def test_live_post_reel(client):
    status, body = live_get(client, "/v1/instagram/post", url=f"https://www.instagram.com/reel/{REEL_CODE}/")
    assert status == 200, body
    post = body["data"]["xdt_shortcode_media"]
    assert post["id"] == REEL_PK and post["shortcode"] == REEL_CODE and post["__typename"] == "XDTGraphVideo"
    assert post["owner"]["username"] == "jane" and post["owner"]["id"] == JANE_PK
    assert post["taken_at_timestamp"] == 1750272151 and post["video_url"].startswith("https://")
    assert post["video_duration"] == pytest.approx(84.67, abs=0.1)
    assert post["clips_music_attribution_info"]["uses_original_audio"] is True
    assert post["edge_media_preview_like"]["count"] > 1000
    assert post["edge_media_to_parent_comment"]["edges"]
    assert_conforms(body, "/v1/instagram/post", min_coverage=0.5)


@pytest.mark.live
def test_live_post_carousel(client):
    status, body = live_get(client, "/v1/instagram/post", url=f"https://www.instagram.com/p/{CAROUSEL_CODE}/")
    assert status == 200, body
    post = body["data"]["xdt_shortcode_media"]
    assert post["__typename"] == "XDTGraphSidecar" and post["owner"]["username"] == "jane"
    assert len(post["edge_sidecar_to_children"]["edges"]) > 1
    assert_conforms(body, "/v1/instagram/post", min_coverage=0.4)


@pytest.mark.live
def test_live_comments(client):
    status, body = live_get(client, "/v2/instagram/post/comments", url=REEL_CODE)
    assert status == 200, body
    assert body["comments"] and all(c["id"] and c["user"]["username"] for c in body["comments"])
    assert "cursor" not in body
    assert_conforms(body, "/v2/instagram/post/comments", min_coverage=0.7)


@pytest.mark.live
def test_live_transcript(client):
    if not transcribe.enabled():
        pytest.skip("SOCIALSCRAPE_TRANSCRIBE is not configured")
    status, body = live_get(client, "/v2/instagram/media/transcript", url=REEL_CODE)
    assert status == 200, body
    assert body["transcripts"][0]["shortcode"] == REEL_CODE
    assert_conforms(body, "/v2/instagram/media/transcript", min_coverage=0.9)


@pytest.mark.live
def test_live_profile(client):
    status, body = live_get(client, "/v1/instagram/profile", handle="jane")
    assert status == 200, body
    user = body["data"]["user"]
    assert user["username"] == "jane" and user["id"] == JANE_PK and user["is_verified"] is True
    assert user["edge_followed_by"]["count"] > 100_000
    assert user["edge_owner_to_timeline_media"]["edges"]
    assert_conforms(body, "/v1/instagram/profile", min_coverage=0.25)


@pytest.mark.live
def test_live_post_count(client):
    status, body = live_get(client, "/v1/instagram/profile/post-count", handle="jane", allow_estimated="true")
    assert status == 200, body
    assert body["handle"] == "jane" and body["media_count"] > 100 and isinstance(body["is_estimated"], bool)
    assert_conforms(body, "/v1/instagram/profile/post-count", min_coverage=0.5)


@pytest.mark.live
def test_live_user_posts(client):
    status, body = live_get(client, "/v2/instagram/user/posts", handle="jane")
    assert status == 200, body
    assert body["items"] and body["num_results"] == len(body["items"])
    assert all(i["id"].endswith(f"_{i['user']['pk']}") and i["user"]["username"] for i in body["items"])
    mine = [i for i in body["items"] if i["user"]["pk"] == JANE_PK]  # the others are collabs (their author)
    assert mine and all(i["user"]["username"] == "jane" for i in mine)
    assert any(isinstance(i.get("like_count"), int) for i in body["items"])  # joined from the reels tab
    assert_conforms(body, "/v2/instagram/user/posts", min_coverage=0.2)


@pytest.mark.live
def test_live_user_reels(client):
    status, body = live_get(client, "/v1/instagram/user/reels", handle="jane")
    assert status == 200, body
    assert body["items"] and all(i["id"].endswith(f"_{i['user']['pk']}") and isinstance(i["play_count"], int)
                                 for i in body["items"])
    mine = [i for i in body["items"] if i["user"]["pk"] == JANE_PK]  # collabs carry their author's pk only
    assert mine and all(i["user"]["username"] == "jane" for i in mine)
    assert_conforms(body, "/v1/instagram/user/reels", min_coverage=0.1)


@pytest.mark.live
def test_live_highlights(client):
    status, body = live_get(client, "/v1/instagram/user/highlights", handle="jane")
    assert status == 200, body
    assert body["highlights"] and all(h["id"].isdigit() and h["owner"]["username"] == "jane"
                                      for h in body["highlights"])
    assert_conforms(body, "/v1/instagram/user/highlights", min_coverage=0.8)


@pytest.mark.live
def test_live_highlight_detail(client):
    status, body = live_get(client, "/v1/instagram/user/highlight/detail", id=HIGHLIGHT_ID)
    assert status == 200, body
    assert body["id"] == f"highlight:{HIGHLIGHT_ID}" and body["user"]["username"] == "jane" and body["items"]
    report = check(body, "/v1/instagram/user/highlight/detail")
    assert not report.mismatches, report
    assert not report.extras and report.coverage >= 0.7, report


@pytest.mark.live
def test_live_search_popular(client):
    status, body = live_get(client, "/v1/instagram/search/popular", query="funnydogs")
    assert status == 200, body
    assert body["query"] == "funnydogs" and body["title"] and body["posts"]
    assert all(p["shortcode"] and p["owner"]["username"] for p in body["posts"])
    assert_conforms(body, "/v1/instagram/search/popular", min_coverage=0.6)
    assert "\u2022" not in body["title"] and not body["title"].endswith("on Instagram")


@pytest.mark.live
def test_live_search_popular_category_hub(client):
    status, body = live_get(client, "/v1/instagram/search/popular", query="basketball")
    assert status == 200, body
    assert body["title"] == "Basketball" and body["posts"] and body["suggested_terms"]
    assert_conforms(body, "/v1/instagram/search/popular", min_coverage=0.4)


@pytest.mark.live
def test_live_reels_search(client):
    status, body = live_get(client, "/v2/instagram/reels/search", query="funnydogs")
    assert status == 200, body
    assert body["reels"] and all(r["is_video"] and r["url"].startswith("https://www.instagram.com/reel/")
                                 for r in body["reels"])
    assert_conforms(body, "/v2/instagram/reels/search", min_coverage=0.25)


@pytest.mark.live
def test_live_search_hashtag(client):
    status, body = live_get(client, "/v1/instagram/search/hashtag", hashtag="funnydogs")
    assert status == 200, body
    assert body["hashtag"] == "funnydogs" and body["posts"]
    assert all(p["id"].isdigit() and p["url"].startswith("https://www.instagram.com/") for p in body["posts"])
    assert_conforms(body, "/v1/instagram/search/hashtag", min_coverage=0.25)


@pytest.mark.live
@pytest.mark.parametrize("path,params", [
    ("/v1/instagram/post", {"url": "https://www.instagram.com/p/DLDXI0fylTA/"}),
    ("/v1/instagram/profile", {"handle": "zzqx_no_such_user_8811"}),
    ("/v1/instagram/user/highlight/detail", {"id": "17999999999999999"}),
    ("/v1/instagram/search/hashtag", {"hashtag": "zzqxnonexistenttag8811"}),
])
def test_live_not_found_is_404(client, path, params):
    status, body = live_get(client, path, **params)
    assert status == 404, body
    assert body["success"] is False and body["error"] == "not_found"
