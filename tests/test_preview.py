"""The playground's response preview (socialscrape/static/preview.js), run under Node.js when it is installed."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PREVIEW = Path(__file__).resolve().parent.parent / "socialscrape" / "static" / "preview.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def _node(expression: str):
    script = f"const P = require({json.dumps(str(PREVIEW))});\nprocess.stdout.write(JSON.stringify({expression}));"
    return json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True,
                                     timeout=30).stdout)


def build(path: str, body, status: int = 200) -> str:
    return _node(f"P.build({json.dumps(path)}, {status}, {json.dumps(body)})")


def test_reel_shows_engagement_and_audio_and_says_what_is_missing():
    html = build("/v1/instagram/post", {"success": True, "data": {"xdt_shortcode_media": {
        "shortcode": "DbSma7kR_p3", "product_type": "clips", "is_video": True, "video_duration": 9.9,
        "thumbnail_src": "https://scontent.cdninstagram.com/thumb.jpg", "taken_at_timestamp": 1785143089,
        "owner": {"username": "j.nelearns", "full_name": "jing", "is_verified": False},
        "edge_media_to_caption": {"edges": [{"node": {"text": "no shame btw"}}]},
        "edge_media_preview_like": {"count": 82630}, "edge_media_to_parent_comment": {"count": 513},
        "clips_music_attribution_info": {"song_name": "Say Yes", "artist_name": "Loco, Punch",
                                         "uses_original_audio": False}}}})
    assert "Reel" in html and "no shame btw" in html and "@j.nelearns" in html
    assert "82,630" in html and ">513<" in html
    assert "Plays" in html and "Not returned" in html  # Instagram didn't send a play count: no invented zero
    assert "Say Yes" in html and "Loco, Punch" in html
    assert 'href="https://www.instagram.com/reel/DbSma7kR_p3/"' in html


def test_tiktok_video_shows_saves_and_shares():
    html = build("/v2/tiktok/video", {"success": True, "aweme_detail": {
        "aweme_id": "6718335390845095173", "desc": "guess my name", "create_time": 1564234358,
        "url": "https://www.tiktok.com/@scout2015/video/6718335390845095173",
        "statistics": {"play_count": 159600, "digg_count": 35200, "comment_count": 5843, "share_count": 1518,
                       "collect_count": 649},
        "author": {"unique_id": "scout2015", "nickname": "Scout"}, "video": {"duration": 10000},
        "music": {"id_str": "6689804660171082501", "title": "original sound", "author": "tiff",
                  "is_original": True}}})
    for label, value in [("Views", "159,600"), ("Likes", "35,200"), ("Comments", "5,843"), ("Shares", "1,518"),
                         ("Saves", ">649<")]:
        assert label in html and value in html
    assert "0:10" in html and "Original audio" in html and "6689804660171082501" in html


def test_platform_text_and_links_cannot_inject_markup():
    html = build("/v2/tiktok/video", {"success": True, "aweme_detail": {
        "desc": '<img src=x onerror=alert(1)>"><script>alert(2)</script>',
        "url": "javascript:alert(3)", "video": {"cover": {"url_list": ['https://x.test/a.jpg" onerror="alert(4)']}},
        "author": {"unique_id": '"><b>me</b>', "nickname": "x", "avatar_thumb": {"url_list": ["data:image/png,x"]}},
        "statistics": {}}})
    assert "<img src=x" not in html and "<script>" not in html and "<b>me</b>" not in html
    # The field list may show such values as text; they must never become a link, an image or an attribute.
    assert 'href="javascript' not in html and 'src="data:' not in html and 'onerror="alert(4)' not in html


def test_errors_and_unknown_shapes_still_preview():
    error = build("/v1/youtube/video", {"success": False, "error": "not_found",
                                        "message": "This video is unavailable"}, 404)
    assert "404 · not found" in error and "This video is unavailable" in error
    summary = build("/v9/example", {"success": True, "items": [1, 2], "name": "x"})
    assert "Response fields" in summary and "2 items" in summary
    assert build("/v1/youtube/video", [1, 2]) is None  # arrays and primitives show the raw text only


@pytest.mark.parametrize("raw", [
    '{"id":6689804660171082501,"n":-1.5e+300,"s":"a,b:{c}[d]\\\\\\"q\\"","e":[],"o":{},"x":[{},[]],"u":"\\u00e9"}',
    '  [ 1 , { "a" : [ ] } , "}\\\\" ]  ',
    '{}',
])
def test_raw_json_keeps_every_digit_and_value(raw):
    pretty = _node(f"P.indent({json.dumps(raw.strip())})")
    assert json.loads(pretty) == json.loads(raw)
    if "6689804660171082501" in raw:
        assert '"id": 6689804660171082501' in pretty  # JSON.parse would have rounded it to ...082500


def test_every_field_stays_reachable_and_links_in_text_are_safe():
    html = build("/v1/instagram/profile", {"success": True, "data": {"user": {
        "username": "x", "full_name": "<b>X</b>", "edge_followed_by": {"count": 5}, "category_name": "Artist",
        "biography": 'see https://example.com/a?b=1 and javascript:alert(1) or http://evil.test/<script>x',
        "bio_links": [{"url": "javascript:alert(1)"}, {"url": "https://ok.test"}]}}})
    assert "<b>X</b>" not in html and "<script>" not in html and 'href="javascript' not in html
    assert 'href="https://example.com/a?b=1"' in html  # web addresses in a bio are clickable
    assert 'class="all-fields"' in html and "category name" in html and "Artist" in html  # nothing hidden


def test_profile_shows_picture_counts_bio_and_links():
    html = build("/v1/instagram/profile", {"success": True, "data": {"user": {
        "username": "natgeo", "full_name": "National Geographic", "is_verified": True,
        "profile_pic_url": "https://scontent.cdninstagram.com/small.jpg",
        "profile_pic_url_hd": "https://scontent.cdninstagram.com/hd.jpg",
        "biography": "Step into wonder", "bio_links": [{"url": "https://visitstore.bio/natgeo", "title": "Store"}],
        "edge_followed_by": {"count": 268303413}, "edge_follow": {"count": 195},
        "edge_owner_to_timeline_media": {"count": 31000, "edges": [{"node": {
            "shortcode": "DeuOR-CjKE9", "display_url": "https://scontent.cdninstagram.com/post.jpg",
            "edge_media_to_caption": {"edges": [{"node": {"text": "Best places to travel"}}]}}}]}}}})
    assert 'src="https://scontent.cdninstagram.com/hd.jpg"' in html  # the sharpest profile picture
    assert "National Geographic" in html and "@natgeo" in html and "Verified" in html
    assert "268,303,413" in html and "Followers" in html and ">195<" in html and "Following" in html
    assert "Step into wonder" in html and 'href="https://visitstore.bio/natgeo"' in html
    assert "Recent posts" in html and "Best places to travel" in html


def test_hostile_captions_and_long_lists_stay_fast_and_complete():
    elapsed = _node("""(() => { const t = Date.now();
        P.build('/v2/tiktok/video', 200, {success: true, aweme_detail: {desc: 'https://' + '.'.repeat(100000) + 'X', statistics: {}}});
        return Date.now() - t; })()""")
    assert elapsed < 500  # a backtracking link pattern took seconds on this caption
    items = [{"id": f"id-{n}", "like_count": n} for n in range(30)]
    html = build("/v1/instagram/user/reels", {"success": True, "items": items})
    assert "id-29" in html and "All fields <span class=\"badge\">60</span>" in html  # every item, not a summary


def test_preview_keeps_every_digit_of_large_ids():
    raw = '{"success":true,"music_info":{"id":6956990112127585029,"title":"x","count":12,"ratio":1.5}}'
    body = _node(f"P.parseLossless({json.dumps(raw)})")
    assert body["music_info"] == {"id": "6956990112127585029", "title": "x", "count": 12, "ratio": 1.5}


def test_long_text_disclosures_say_what_they_expand_and_point_at_it():
    long_caption = "word " * 100
    html = build("/v1/youtube/channel-videos", {"success": True, "videos": [
        {"type": "video", "url": "https://www.youtube.com/watch?v=a", "title": "First video", "description": long_caption},
        {"type": "video", "url": "https://www.youtube.com/watch?v=b", "title": "Second video", "description": long_caption}]})
    controls = re.findall(r'data-expand data-subject="[^"]*" aria-controls="([^"]+)"', html)
    assert len(controls) == 2 and len(set(controls)) == 2  # one target each, never shared
    assert all(f'id="{target}"' in html for target in controls)
    assert 'aria-label="Show more of the caption for First video"' in html


def test_mounted_field_list_waits_until_opened():
    body = {"success": True, "items": [{"id": f"id-{n}"} for n in range(600)]}
    lazy = _node(f"P.build('/v1/instagram/user/reels', 200, {json.dumps(body)}, {{lazy: true}})")
    assert 'data-all-fields' in lazy and "id-1" not in lazy and "All fields</summary>" in lazy  # no walk, no count yet
    full = build("/v1/instagram/user/reels", body)
    assert "All fields <span class=\"badge\">600</span>" in full and "Show all 600 fields" in full  # 500 shown, rest on request
