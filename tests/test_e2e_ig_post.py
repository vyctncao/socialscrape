"""End-to-end checks of the Instagram post, comments, transcript and search endpoints (anonymous).

/v1/instagram/post, /v2/instagram/post/comments, /v2/instagram/media/transcript, /v1/instagram/search/popular,
/v1/instagram/search/hashtag and /v2/instagram/reels/search: URL forms, post types (reel, image, carousels),
parameter validation, contract conformance, data accuracy against yt-dlp and error hygiene. Only the tests marked
live reach Instagram. Transcript tests run on an app built with SOCIALSCRAPE_TRANSCRIBE=mlx-whisper (skipped when
mlx_whisper is not importable).
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.contract import assert_conforms
from tests.e2e import LEAKS, app_with, skip_if_blocked, spaced_get, ytdlp_info

IG = "https://www.instagram.com"
REEL = "DLDXI0fylTC"  # jane, 85 s reel with speech
IMAGE = "Deh3_cCl1Qs"  # natgeo single image
VIDEO_CAROUSEL = "DekS-u4GltE"  # natgeo, 7 videos; children 2-7 have has_audio=False
MIXED_CAROUSEL = "DeaMlUSEpgs"  # nasajpl collab, I I V V I I I; the two videos are music only
LONG_VIDEO = "DekBohqDEWz"  # 236 s video
MISSING = "DLDXI0fylTA"  # valid alphabet, no such post

POST = "/v1/instagram/post"
COMMENTS = "/v2/instagram/post/comments"
TRANSCRIPT = "/v2/instagram/media/transcript"
POPULAR = "/v1/instagram/search/popular"
HASHTAG = "/v1/instagram/search/hashtag"
REELS = "/v2/instagram/reels/search"

_SEEN: dict[str, tuple[int, dict]] = {}
_SPACING = 1.5  # seconds between calls that reached Instagram


def call(c, path: str, **params) -> tuple[int, dict]:
    """GET once per distinct client+URL per session, spaced; skips when Instagram throttles this IP."""
    key = json.dumps([id(c), path, params], sort_keys=True, ensure_ascii=False)
    if key not in _SEEN:
        r = spaced_get(c, path, _SPACING, **params)
        try:
            body = r.json()
        except ValueError:
            body = {"success": False, "error": "non_json", "message": r.text[:200]}
        if r.status_code in (429, 503):
            pytest.skip(f"Instagram throttled this IP: {body.get('message')}")
        _SEEN[key] = (r.status_code, body)
    return _SEEN[key]


def ok(c, path: str, **params) -> dict:
    status, body = call(c, path, **params)
    assert status == 200, (path, params, body)
    assert body["success"] is True and body["credits_charged"] == 0
    return body


def err(c, path: str, status: int, code: str | None = None, **params) -> dict:
    got, body = call(c, path, **params)
    assert got == status, (path, params, body)
    assert body["success"] is False and isinstance(body["message"], str) and body["message"]
    assert not LEAKS.search(body["message"]), body
    if code:
        assert body["error"] == code, body
    return body


def media(c, url: str, **params) -> dict:
    return ok(c, POST, url=url, **params)["data"]["xdt_shortcode_media"]


# --- /v1/instagram/post ---------------------------------------------------------------


@pytest.mark.live
def test_reel_conforms_and_matches_ytdlp(client):
    body = ok(client, POST, url=f"{IG}/reel/{REEL}/")
    assert_conforms(body, POST, min_coverage=0.5)
    m = body["data"]["xdt_shortcode_media"]
    assert m["shortcode"] == REEL and m["is_video"] is True and m["product_type"] == "clips"
    assert m["video_url"].startswith("https://") and m["has_audio"] is True

    info = ytdlp_info(f"{IG}/reel/{REEL}/")
    assert m["taken_at_timestamp"] == info["timestamp"]
    assert m["owner"]["username"] == info["channel"]
    assert m["id"] == str(info.get("media_id") or m["id"]) and info["id"] == REEL
    likes = m["edge_media_preview_like"]["count"]
    assert likes == pytest.approx(info["like_count"], rel=0.05)
    assert m["edge_media_to_parent_comment"]["count"] == pytest.approx(info["comment_count"], rel=0.05)


@pytest.mark.live
def test_reel_play_count_comes_from_the_owners_reels_tab(client):
    """The logged-out post has no count; the reel's owner's Reels tab is paged to it (REEL is ~220 reels down)."""
    m = media(client, f"{IG}/reel/{REEL}/")
    assert isinstance(m["video_play_count"], int) and m["video_play_count"] > m["edge_media_preview_like"]["count"]
    assert "video_view_count" not in m  # the legacy 3-second count isn't invented
    # a reel on the tab's first page has the count user/reels shows (both read the same tab)
    newest = [r for r in ok(client, "/v1/instagram/user/reels", handle="jane")["items"] if r.get("play_count")][-1]
    assert media(client, newest["code"])["video_play_count"] == pytest.approx(newest["play_count"], rel=0.01)
    assert "video_play_count" not in media(client, REEL, include_play_count="false")


@pytest.mark.live
@pytest.mark.parametrize("url", [
    REEL,
    f"  {REEL}  ",
    f"{IG}/p/{REEL}/",
    f"{IG}/tv/{REEL}/",
    f"{IG}/reels/{REEL}/",
    f"{IG}/jane/reel/{REEL}/",
    f"instagram.com/reel/{REEL}",
    f"https://m.instagram.com/reel/{REEL}/?igsh=abc123&utm_source=ig_web_copy_link#frag",
    f"{IG}/reel/{REEL}/embed/",
])
def test_post_url_forms_resolve_to_same_post(client, url):
    assert media(client, url)["shortcode"] == REEL


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@scout2015/video/6718335390845095173",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    f"{IG}/jane/",
    f"{IG}/stories/jane/3570766765028588805/",
    f"{IG}/reels/audio/1234567890/",
    f"https://instagram.com.evil.com/p/{REEL}/",
    f"https://notinstagram.com/p/{REEL}/",
    f"ftp://www.instagram.com/p/{REEL}/",
    "abcd",
])
def test_post_rejects_non_post_urls(client, url):
    err(client, POST, 400, "unsupported_url", url=url)


def test_post_missing_or_blank_url(client):
    assert "url" in err(client, POST, 400, url="   ")["message"]
    r = client.get(POST)
    assert r.status_code == 400 and r.json()["message"].startswith("url")


@pytest.mark.live
def test_nonexistent_post_is_404_on_post_and_comments(client):
    err(client, POST, 404, "not_found", url=MISSING)
    err(client, COMMENTS, 404, "not_found", url=MISSING)


@pytest.mark.live
def test_image_post(client):
    body = ok(client, POST, url=IMAGE)
    assert_conforms(body, POST, min_coverage=0.4)
    m = body["data"]["xdt_shortcode_media"]
    assert m["__typename"] == "XDTGraphImage" and m["is_video"] is False and m["display_url"].startswith("https://")


@pytest.mark.live
def test_mixed_carousel_children_and_collab_owner(client):
    body = ok(client, POST, url=f"{IG}/p/{MIXED_CAROUSEL}/")
    assert_conforms(body, POST, min_coverage=0.4)
    m = body["data"]["xdt_shortcode_media"]
    assert m["__typename"] == "XDTGraphSidecar" and m["owner"]["username"] == "nasajpl"
    kids = [e["node"] for e in m["edge_sidecar_to_children"]["edges"]]
    assert [k["is_video"] for k in kids] == [False, False, True, True, False, False, False]
    assert len({k["id"] for k in kids}) == len(kids)
    # a video child has its own audio flag and length; an image child has neither
    assert all(isinstance(k["has_audio"], bool) and k["video_duration"] > 0 for k in kids if k["is_video"])
    assert not any("has_audio" in k or "video_duration" in k for k in kids if not k["is_video"])


@pytest.mark.live
def test_video_carousel_children_keep_their_own_audio_flag(client):
    kids = [e["node"] for e in media(client, VIDEO_CAROUSEL)["edge_sidecar_to_children"]["edges"]]
    assert [k["has_audio"] for k in kids] == [True] + [False] * 6
    assert all(k["is_video"] and k["video_duration"] > 0 for k in kids)


@pytest.mark.live
def test_ignored_and_unsupported_params(client):
    assert media(client, REEL, region="US", trim="true")["shortcode"] == REEL
    for value in ("true", "TRUE", "1"):
        err(client, POST, 501, url=REEL, download_media=value)


# --- /v2/instagram/post/comments --------------------------------------------------------


@pytest.mark.live
def test_comments_first_page(client):
    body = ok(client, COMMENTS, url=f"{IG}/reel/{REEL}/")
    assert_conforms(body, COMMENTS, min_coverage=0.5)
    comments = body["comments"]
    assert 1 <= len(comments) <= 50
    assert len({c["id"] for c in comments}) == len(comments)
    stamps = [c["created_at"] for c in comments]
    assert stamps == sorted(stamps, reverse=True), "newest first"
    assert all(c["text"] is not None and c["user"]["username"] for c in comments)


@pytest.mark.live
def test_comments_cursor_and_replies(client):
    err(client, COMMENTS, 501, "pagination_unavailable", url=REEL, cursor="abc")
    err(client, COMMENTS, 501, url=REEL, include_replies="true")
    assert ok(client, COMMENTS, url=REEL, include_replies="false")["comments"]


@pytest.mark.parametrize("path", [POST, COMMENTS])
@pytest.mark.parametrize("bad_id", ["%2E%2E", "%3Cscript%3E", "a.b.c"])
def test_shortcode_outside_alphabet_is_client_error(client, path, bad_id):
    err(client, path, 400, "unsupported_url", url=f"{IG}/p/{bad_id}/")


# --- search: popular / hashtag / reels ---------------------------------------------------


@pytest.mark.live
def test_popular_topic(client):
    body = ok(client, POPULAR, query="funnydogs")
    assert_conforms(body, POPULAR, min_coverage=0.6)
    posts = body["posts"]
    assert 1 <= len(posts) <= 12 and len({p["id"] for p in posts}) == len(posts)
    assert all(p["url"].startswith(f"{IG}/") and p["shortcode"] in p["url"] for p in posts)
    assert ok(client, POPULAR, query="#funnydogs")["title"] == body["title"]


@pytest.mark.live
def test_popular_multi_word_and_hub(client):
    body = ok(client, POPULAR, query="golden retriever")
    assert body["posts"] and body["title"].lower() == "golden retriever"
    hub = ok(client, POPULAR, query="basketball")
    assert len(hub["posts"]) > 12 and len({p["id"] for p in hub["posts"]}) == len(hub["posts"])
    assert hub["suggested_terms"]


@pytest.mark.live
def test_popular_validation(client):
    for q in ("", "   ", "a/b", "x" * 101):
        err(client, POPULAR, 400, "bad_query", query=q)
    err(client, POPULAR, 501, "pagination_unavailable", query="funnydogs", cursor="abc")
    err(client, POPULAR, 404, "not_found", query="zzqxnonexistenttag987")


@pytest.mark.live
@pytest.mark.parametrize("path,params", [
    (POPULAR, {"query": "Basketball"}),
    (HASHTAG, {"hashtag": "FunnyDogs"}),
    (REELS, {"query": "FunnyDogs"}),
    # retest-instagram-1: 'İ'.lower() is 'i' + U+0307 (400 'not a single word' / 404 path)
    (HASHTAG, {"hashtag": "İstanbul"}),
    (POPULAR, {"query": "İstanbul"}),
    (REELS, {"query": "İstanbul"}),
])
def test_mixed_case_topic_is_found(client, path, params):
    status, body = call(client, path, **params)
    assert status == 200, body


@pytest.mark.live
def test_hashtag_media_type(client):
    body = ok(client, HASHTAG, hashtag="funnydogs")
    assert_conforms(body, HASHTAG, min_coverage=0.3)
    assert body["hashtag"] == "funnydogs" and body["media_type"] == "all"
    assert body["posts"] and len({p["id"] for p in body["posts"]}) == len(body["posts"])
    reels = ok(client, HASHTAG, hashtag="#funnydogs", media_type="REELS")
    assert reels["media_type"] == "reels" and all(p["is_video"] for p in reels["posts"])
    err(client, HASHTAG, 400, "bad_media_type", hashtag="funnydogs", media_type="photos")


@pytest.mark.live
def test_hashtag_post_matches_post_endpoint(client):
    first = ok(client, HASHTAG, hashtag="funnydogs")["posts"][0]
    m = media(client, first["shortcode"])
    assert m["id"] == first["id"] and m["owner"]["id"] == first["owner"]["id"]
    assert m["is_video"] == first["is_video"]


def test_hashtag_validation(client):
    err(client, HASHTAG, 400, "bad_query", hashtag="funny dogs")
    err(client, HASHTAG, 400, hashtag="")
    for value in ("last-hour", "last-day", "last-week", "last-month", "last-year"):
        err(client, HASHTAG, 501, hashtag="funnydogs", date_posted=value)
    err(client, HASHTAG, 501, "pagination_unavailable", hashtag="funnydogs", cursor="2")
    r = client.get(HASHTAG)
    assert r.status_code == 400 and r.json()["message"].startswith("hashtag")


@pytest.mark.live
def test_reels_search(client):
    body = ok(client, REELS, query="funnydogs")
    assert_conforms(body, REELS, min_coverage=0.25)
    assert body["reels"] and all(r["is_video"] for r in body["reels"])
    page1 = ok(client, REELS, query="funnydogs", page="1")["reels"]
    assert {r["id"] for r in page1} == {r["id"] for r in body["reels"]}
    assert ok(client, REELS, query="golden retriever")["reels"]


@pytest.mark.parametrize("page,status", [
    ("2", 501), ("11", 501), ("1e1", 501),
    ("12", 400), ("0", 400), ("-1", 400), ("abc", 400), ("inf", 400), ("nan", 400), ("1e400", 400),
])
def test_reels_search_page(client, page, status):
    err(client, REELS, status, "pagination_unavailable" if status == 501 else "bad_page", query="funnydogs", page=page)


def test_reels_search_documented_date_posted_is_501(client):
    for value in ("last-week", "last-month", "last-year"):
        err(client, REELS, 501, "not_supported", query="funnydogs", date_posted=value)


@pytest.mark.parametrize("path,params", [
    (REELS, {"query": "funnydogs", "date_posted": "last-day"}),
    (REELS, {"query": "funnydogs", "date_posted": "junk"}),
    (HASHTAG, {"hashtag": "funnydogs", "date_posted": "yesterday"}),
])
def test_out_of_enum_date_posted_is_400(client, path, params):
    status, body = call(client, path, **params)
    assert status == 400, body


@pytest.mark.live
def test_search_cache_hit(tmp_path):
    with app_with({}, tmp_path) as c:
        first = c.get(POPULAR, params={"query": "funnydogs", "cache_max_age": "1h"})
        skip_if_blocked(first)
        second = c.get(POPULAR, params={"query": "funnydogs", "cache_max_age": "1h"})
        assert first.status_code == second.status_code == 200
        assert second.json().get("cached") is True and second.json()["cached_at"]
        a, b = first.json(), second.json()
        for k in ("cached", "cached_at"):
            a.pop(k, None), b.pop(k, None)
        assert a == b
        assert c.get(POPULAR, params={"query": "funnydogs", "cache_max_age": "abc"}).status_code == 400


@pytest.mark.live
def test_parallel_requests(client):
    paths = [(POST, {"url": REEL}), (POST, {"url": IMAGE}), (COMMENTS, {"url": REEL}), (POPULAR, {"query": "funnydogs"})]
    time.sleep(_SPACING)
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda p: client.get(p[0], params=p[1]), paths + paths[:1]))
    if any(r.status_code in (429, 503) for r in results):
        pytest.skip("Instagram throttled this IP")
    assert [r.status_code for r in results] == [200] * len(results)
    assert all(r.json()["success"] is True and r.json()["credits_charged"] == 0 for r in results)
    assert results[0].json()["data"]["xdt_shortcode_media"]["shortcode"] == REEL
    assert results[-1].json()["data"]["xdt_shortcode_media"]["id"] == results[0].json()["data"]["xdt_shortcode_media"]["id"]


# --- /v2/instagram/media/transcript (mlx-whisper) -----------------------------------------


def test_transcript_disabled_by_default(client):
    from socialscrape import transcribe

    if transcribe.enabled():
        pytest.skip("SOCIALSCRAPE_TRANSCRIBE is configured in this environment")
    err(client, TRANSCRIPT, 501, "transcription_disabled", url=REEL)


@pytest.mark.live
def test_transcript_short_reel(mlx_client):
    body = ok(mlx_client, TRANSCRIPT, url=f"{IG}/reel/{REEL}/")
    assert_conforms(body, TRANSCRIPT, min_coverage=1.0)
    [entry] = body["transcripts"]
    assert entry["shortcode"] == REEL and entry["id"] == media(mlx_client, REEL)["id"]
    assert entry["text"] and len(entry["text"].split()) > 50


@pytest.mark.live
def test_transcript_mixed_carousel_one_entry_per_video(mlx_client):
    body = ok(mlx_client, TRANSCRIPT, url=MIXED_CAROUSEL)
    kids = [e["node"] for e in media(mlx_client, MIXED_CAROUSEL)["edge_sidecar_to_children"]["edges"]]
    videos = [k for k in kids if k["is_video"]]
    assert [t["id"] for t in body["transcripts"]] == [k["id"] for k in videos]
    assert [t["shortcode"] for t in body["transcripts"]] == [k["shortcode"] for k in videos]


@pytest.mark.live
def test_transcript_no_video_and_bad_input(mlx_client):
    err(mlx_client, TRANSCRIPT, 404, "no_video", url=IMAGE)
    err(mlx_client, TRANSCRIPT, 404, "not_found", url=MISSING)
    err(mlx_client, TRANSCRIPT, 400, "unsupported_url",
        url="https://www.tiktok.com/@scout2015/video/6718335390845095173")
    err(mlx_client, TRANSCRIPT, 400, url="")


@pytest.mark.live
def test_transcript_long_video_needs_literal_true(mlx_client):
    for value in (None, "TRUE", "1", "false"):
        params = {"url": LONG_VIDEO} if value is None else {"url": LONG_VIDEO, "allow_long_transcription": value}
        err(mlx_client, TRANSCRIPT, 400, "transcription_too_long", **params)
    body = ok(mlx_client, TRANSCRIPT, url=LONG_VIDEO, allow_long_transcription="true")
    assert body["transcripts"][0]["text"]


@pytest.mark.live
def test_transcript_carousel_with_silent_videos(mlx_client):
    status, body = call(mlx_client, TRANSCRIPT, url=VIDEO_CAROUSEL)
    assert status == 200, body
    assert body["success"] is True and len(body["transcripts"]) == 7
    assert all(t["text"] is None for t in body["transcripts"][1:])


@pytest.mark.live
def test_transcript_music_only_video_text_is_null(mlx_client):
    body = ok(mlx_client, TRANSCRIPT, url=MIXED_CAROUSEL)
    assert [t["text"] for t in body["transcripts"]] == [None, None]


def test_transcript_shortcode_outside_alphabet_is_client_error(mlx_client):
    err(mlx_client, TRANSCRIPT, 400, "unsupported_url", url=f"{IG}/p/a.b.c/")
