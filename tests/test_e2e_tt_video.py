"""End-to-end checks of the TikTok post, transcript and profile endpoints (anonymous).

/v2/tiktok/video, /v1/tiktok/video/transcript, /v1/tiktok/profile, /v3/tiktok/profile/videos and
/v1/tiktok/profile/region: URL forms and short links, app-path vs web-fallback posts, parameter validation,
max_cursor walks, contract conformance, data accuracy against yt-dlp, cache_max_age, concurrency and error
hygiene. Only the tests marked live reach TikTok. AI-transcript tests run on an app built with
SOCIALSCRAPE_TRANSCRIBE=mlx-whisper (skipped when mlx_whisper is not importable).
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import yt_dlp

from tests.contract import assert_conforms
from tests.e2e import LEAKS, app_with, mark_upstream, skip_if_blocked, spaced_get, ytdlp_info

STOOL = "stoolpresidente"
STOOL_UID = "6659752019493208069"
STOOL_ID = "7499229683859426602"
STOOL_VIDEO = f"https://www.tiktok.com/@{STOOL}/video/{STOOL_ID}"  # original sound -> app path; English captions
SCOUT_ID = "6718335390845095173"
SCOUT_VIDEO = f"https://www.tiktok.com/@scout2015/video/{SCOUT_ID}"  # not on its sound's page -> web; no captions
PHOTO_ID = "7240568259186019630"
PHOTO_POST = f"https://www.tiktok.com/@chillezy/photo/{PHOTO_ID}"
WAPO = "washingtonpost"
WAPO_PHOTO_ID = "7697300746554838302"  # a 10-image photo post in its profile feed
WAPO_PHOTO_TIME = 1792170000  # its create_time: scheduled, 2513 s after the time in its id's top 32 bits
MISSING_VIDEO = f"https://www.tiktok.com/@{STOOL}/video/7499229683859426001"
LONG_TT = f"https://www.tiktok.com/@{STOOL}/video/7691685734738119950"  # 124 s, English captions only
SHORT_LINKS = {
    "https://vm.tiktok.com/ZMkyTWLpV/": "7179163174057364742",
    "https://www.tiktok.com/t/ZP89Khv9t": "7607174901778107662",
}
DEAD_SHORT_LINK = "https://vm.tiktok.com/ZTR45GpSF/"  # yt-dlp's TikTokVMIE test link; now redirects to the homepage
PRIVATE_USER = "private"  # privateAccount=True
EMPTY_USER = "sarah"  # public, videoCount 0
SMALL_USER = "jessica"  # ~17 posts: two feed pages
NO_OWN_SOUND_USER = "7clouds"  # lyric channel: posts only use label tracks
MISSING_USER = "zzqx_no_such_user_8472619"

VIDEO = "/v2/tiktok/video"
TRANSCRIPT = "/v1/tiktok/video/transcript"
PROFILE = "/v1/tiktok/profile"
VIDEOS = "/v3/tiktok/profile/videos"
REGION = "/v1/tiktok/profile/region"

_FEED_START_MS = 1472706000000
_SPACING = 1.0  # seconds between calls that reached TikTok


def get(c, path: str, **params):
    """GET, spaced after calls that reached TikTok; skips when TikTok throttles this IP."""
    r = spaced_get(c, path, _SPACING, **params)
    skip_if_blocked(r)
    return r


def ok(c, path: str, **params) -> dict:
    r = get(c, path, **params)
    assert r.status_code == 200, (path, params, r.text[:500])
    body = r.json()
    assert body["success"] is True and body["credits_charged"] == 0
    return body


def err(c, path: str, status: int, code: str, **params) -> dict:
    r = get(c, path, **params)
    assert r.headers["content-type"].startswith("application/json"), (path, params, r.text[:300])
    body = r.json()
    assert (r.status_code, body.get("success"), body.get("error")) == (status, False, code), (path, params, body)
    assert not LEAKS.search(body["message"]), body["message"]
    return body


# --- /v2/tiktok/video --------------------------------------------------------------------------


def app_post(c, **params) -> dict:
    """/v2/tiktok/video for STOOL_VIDEO, a post on its sound's app feed. Now and then TikTok's app feed fails or leaves
    the post out, and the web shape is served instead (2026-10-19: once in 47 calls), so that is retried once."""
    body = ok(c, VIDEO, url=STOOL_VIDEO, **params)
    if "without_watermark" not in body["aweme_detail"]:  # an app-only field
        body = ok(c, VIDEO, url=STOOL_VIDEO, **params)
    return body


@pytest.fixture(scope="module")
def stool_post(client) -> dict:
    return app_post(client)


@pytest.mark.live
def test_video_app_path_matches_ytdlp(stool_post):
    assert_conforms(stool_post, VIDEO, min_coverage=0.9)
    d = stool_post["aweme_detail"]
    assert d["aweme_id"] == STOOL_ID and d["url"] == STOOL_VIDEO
    assert len(d) > 100 and "without_watermark" in d and d["author"]["region"] == "US"  # app-only fields
    assert d["author"]["unique_id"] == STOOL and d["author"]["uid"] == STOOL_UID
    info = ytdlp_info(STOOL_VIDEO)
    assert d["create_time"] == info["timestamp"]
    assert abs(d["video"]["duration"] / 1000 - info["duration"]) < 1  # ms vs yt-dlp's whole seconds
    assert d["desc"] == info["description"]
    plays, web_plays = d["statistics"]["play_count"], info["view_count"]  # exact vs TikTok's web rounding
    assert abs(plays - web_plays) <= max(0.01 * web_plays, 100), (plays, web_plays)


@pytest.mark.live
def test_video_web_fallback_has_rounded_counts(client):
    body = ok(client, VIDEO, url=SCOUT_VIDEO)
    assert_conforms(body, VIDEO, min_coverage=0.2)
    d = body["aweme_detail"]
    assert d["aweme_id"] == SCOUT_ID and "without_watermark" not in d and len(d) < 30
    assert d["statistics"]["play_count"] >= 10_000 and d["statistics"]["play_count"] % 100 == 0
    assert d["create_time"] == 1564234358 and d["video"]["duration"] == 10_000


@pytest.mark.live
def test_photo_post(client):
    body = ok(client, VIDEO, url=PHOTO_POST, get_transcript="true")
    d = body["aweme_detail"]
    assert d["aweme_id"] == PHOTO_ID and len(d["image_post_info"]["images"]) > 1
    assert body["transcript"] is None
    assert_conforms(body, VIDEO, min_coverage=0.5)


@pytest.mark.live
@pytest.mark.parametrize("url", [
    f"https://m.tiktok.com/v/{STOOL_ID}.html",
    f"tiktok.com/@{STOOL}/video/{STOOL_ID}",
    f"https://www.tiktok.com/embed/v2/{STOOL_ID}",
    f"https://www.tiktok.com/share/video/{STOOL_ID}",
    f"https://www.tiktok.com/player/v1/{STOOL_ID}?&music_info=1&description=1",
    f"https://www.tiktok.com/@/video/{STOOL_ID}?is_from_webapp=1&lang=en#top",
    f"  HTTPS://WWW.TIKTOK.COM/@{STOOL.upper()}/VIDEO/{STOOL_ID}  ",
])
def test_video_url_forms(client, url):
    assert ok(client, VIDEO, url=url)["aweme_detail"]["aweme_id"] == STOOL_ID


@pytest.mark.live
@pytest.mark.parametrize("url,aweme_id", SHORT_LINKS.items())
def test_video_short_links(client, url, aweme_id):
    assert ok(client, VIDEO, url=url)["aweme_detail"]["aweme_id"] == aweme_id


@pytest.mark.live
def test_video_dead_short_link_is_404(client):
    err(client, VIDEO, 404, "not_found", url=DEAD_SHORT_LINK)


@pytest.mark.parametrize("url", [
    "", "   ", "not a url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", f"https://www.tiktok.com/@{STOOL}",
    "https://www.tiktok.com/music/original-sound-7499229705166163754", "https://www.tiktok.com.evil.com/@a/video/1",
    "javascript:alert(1)", "https://www.tiktok.com/@a/video/１２３", "https://vm.tiktok.com/",
])
def test_video_bad_urls_are_400(client, url):
    err(client, VIDEO, 400, "unsupported_url", url=url)
    err(client, TRANSCRIPT, 400, "unsupported_url", url=url)


def test_video_and_transcript_need_url(client):
    assert err(client, VIDEO, 400, "bad_request")["message"] == "url: Field required"
    assert err(client, TRANSCRIPT, 400, "bad_request")["message"] == "url: Field required"


@pytest.mark.live
def test_missing_post_is_404(client):
    err(client, VIDEO, 404, "not_found", url=MISSING_VIDEO)


@pytest.mark.live
def test_get_transcript_flag(client, stool_post):
    assert "transcript" not in stool_post
    assert ok(client, VIDEO, url=STOOL_VIDEO, get_transcript="TRUE")["transcript"].startswith("WEBVTT")
    assert "transcript" not in ok(client, VIDEO, url=STOOL_VIDEO, get_transcript="false")


@pytest.mark.live
def test_video_ignored_params(client, stool_post):
    body = app_post(client, region="GB", trim="true", download_media="true")
    assert body["aweme_detail"]["aweme_id"] == STOOL_ID
    assert set(body["aweme_detail"]) == set(stool_post["aweme_detail"])


@pytest.mark.live
def test_concurrent_requests(client):
    time.sleep(_SPACING)
    calls = [(VIDEO, {"url": STOOL_VIDEO})] * 3 + [(PROFILE, {"handle": SMALL_USER}), (TRANSCRIPT, {"url": STOOL_VIDEO})]
    with ThreadPoolExecutor(5) as ex:
        results = list(ex.map(lambda p: client.get(p[0], params=p[1]), calls))
    mark_upstream()
    if any(r.status_code in (429, 503) for r in results):
        pytest.skip("TikTok throttled this IP")
    assert [r.status_code for r in results] == [200] * len(results)
    assert {r.json()["aweme_detail"]["aweme_id"] for r in results[:3]} == {STOOL_ID}
    assert results[3].json()["user"]["uniqueId"] == SMALL_USER
    assert results[4].json()["transcript"].startswith("WEBVTT")


# --- /v1/tiktok/video/transcript (native captions) ---------------------------------------------


@pytest.mark.live
def test_transcript_matches_ytdlp_subtitles(client):
    body = ok(client, TRANSCRIPT, url=STOOL_VIDEO)
    assert_conforms(body, TRANSCRIPT, min_coverage=1.0)
    assert body["id"] == STOOL_ID and body["url"] == STOOL_VIDEO and body["transcript"].startswith("WEBVTT")
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "writesubtitles": True}
    with yt_dlp.YoutubeDL(opts) as ydl:  # writesubtitles: yt-dlp only extracts subtitles when asked to
        info = ydl.extract_info(STOOL_VIDEO, download=False)
        track = next(t for lang, ts in (info.get("subtitles") or {}).items() if lang.startswith("eng")
                     for t in ts if t.get("ext") == "vtt")
        vtt = ydl.urlopen(track["url"]).read().decode("utf-8")
    assert body["transcript"].strip() == vtt.strip()
    for language in ("en", "eng", "EN"):
        assert ok(client, TRANSCRIPT, url=STOOL_VIDEO, language=language)["transcript"] == body["transcript"]


@pytest.mark.live
def test_transcript_missing_language_is_null(client):
    assert ok(client, TRANSCRIPT, url=STOOL_VIDEO, language="es")["transcript"] is None
    body = ok(client, TRANSCRIPT, url=SCOUT_VIDEO)
    assert body["transcript"] is None and body["id"] == SCOUT_ID


@pytest.mark.parametrize("language", ["english!", "e", "pt-BR", "engl", "e n"])
def test_transcript_bad_language_is_400(client, language):
    err(client, TRANSCRIPT, 400, "bad_request", url=STOOL_VIDEO, language=language)


@pytest.mark.live
def test_transcript_short_link_and_missing_post(client):
    url, aweme_id = next(iter(SHORT_LINKS.items()))
    assert ok(client, TRANSCRIPT, url=url)["id"] == aweme_id
    err(client, TRANSCRIPT, 404, "not_found", url=MISSING_VIDEO, use_ai_as_fallback="true")


@pytest.mark.live
def test_ai_fallback_disabled_is_501():
    with app_with({"SOCIALSCRAPE_TRANSCRIBE": "none"}) as c:
        err(c, TRANSCRIPT, 501, "transcription_disabled", url=SCOUT_VIDEO, use_ai_as_fallback="true")
        assert ok(c, TRANSCRIPT, url=SCOUT_VIDEO, use_ai_as_fallback="false")["transcript"] is None


# --- /v1/tiktok/video/transcript (mlx-whisper) -------------------------------------------------


@pytest.mark.live
def test_ai_fallback_on_post_without_captions(mlx_client, monkeypatch):
    from socialscrape.platforms import tiktok

    calls, real = [], tiktok.transcribe

    async def spy(*args, **kw):
        calls.append(args)
        return await real(*args, **kw)

    monkeypatch.setattr(tiktok, "transcribe", spy)
    body = ok(mlx_client, TRANSCRIPT, url=SCOUT_VIDEO, use_ai_as_fallback="true")
    assert body["id"] == SCOUT_ID and len(calls) == 1  # no captions, so the AI fallback ran
    # plain text, or null: the Scout video has no speech Whisper keeps
    assert body["transcript"] is None or not body["transcript"].startswith("WEBVTT")


@pytest.mark.live
def test_ai_fallback_unused_when_captions_exist(mlx_client):
    start = time.monotonic()
    body = ok(mlx_client, TRANSCRIPT, url=LONG_TT, language="en", use_ai_as_fallback="true")
    assert body["transcript"].startswith("WEBVTT") and time.monotonic() - start < 30


@pytest.mark.live
def test_long_media_needs_allow_long(mlx_client):  # the literal-true rule is in test_tiktok.py
    body = err(mlx_client, TRANSCRIPT, 400, "transcription_too_long", url=LONG_TT, language="es",
               use_ai_as_fallback="true")
    assert "124s" in body["message"] and "allow_long_transcription=true" in body["message"]


@pytest.mark.live
def test_long_media_with_allow_long_true(mlx_client):
    start = time.monotonic()
    body = ok(mlx_client, TRANSCRIPT, url=LONG_TT, language="es", use_ai_as_fallback="true",
              allow_long_transcription="true")
    elapsed = time.monotonic() - start
    assert isinstance(body["transcript"], str) and len(body["transcript"]) > 200
    assert not body["transcript"].startswith("WEBVTT")
    assert elapsed < 60, f"long transcription took {elapsed:.0f}s"


@pytest.mark.live
@pytest.mark.parametrize("language, status", [("eng", 200), ("zho", 200), ("zz", 400)])
def test_ai_fallback_language_codes_do_not_crash(mlx_client, language, status):
    start = time.monotonic()
    r = get(mlx_client, TRANSCRIPT, url=SCOUT_VIDEO, use_ai_as_fallback="true", language=language)
    assert r.status_code == status, r.text[:200]
    assert r.headers["content-type"].startswith("application/json"), r.text[:200]
    if status == 400:  # refused before downloading any audio
        assert r.json()["error"] == "bad_request" and "not supported" in r.json()["message"]
        assert time.monotonic() - start < 10
    else:
        assert r.json()["id"] == SCOUT_ID and not (r.json()["transcript"] or "").startswith("WEBVTT")


@pytest.mark.live
def test_ai_fallback_yue_on_small_model_is_refused_before_download(mlx_client, monkeypatch):
    from socialscrape import transcribe

    calls, real = [], transcribe.with_ydl

    async def spy(*args, **kw):  # records any audio download (the language check runs in core transcribe())
        calls.append(kw)
        return await real(*args, **kw)

    monkeypatch.setattr(transcribe, "with_ydl", spy)
    body = err(mlx_client, TRANSCRIPT, 400, "bad_request", url=SCOUT_VIDEO, use_ai_as_fallback="true", language="yue")
    assert "Whisper model" in body["message"] and calls == []  # mlx_client runs the small model, which lacks yue


# --- /v1/tiktok/profile ------------------------------------------------------------------------


@pytest.mark.live
def test_profile_by_handle_and_user_id(client):
    by_handle = ok(client, PROFILE, handle=STOOL)
    assert_conforms(by_handle, PROFILE, min_coverage=0.9)
    by_id = ok(client, PROFILE, user_id=STOOL_UID)
    for body in (by_handle, by_id):
        assert body["user"]["id"] == STOOL_UID and body["user"]["uniqueId"] == STOOL
        assert body["stats"]["followerCount"] > 1_000_000 and body["stats"]["videoCount"] > 1000
        assert body["itemList"] == []


@pytest.mark.live
@pytest.mark.parametrize("handle", [f"@{STOOL}", STOOL.upper(), f"  {STOOL} ", f"https://www.tiktok.com/@{STOOL}?lang=en",
                                    f"tiktok.com/@{STOOL}"])
def test_profile_handle_forms(client, handle):
    assert ok(client, PROFILE, handle=handle)["user"]["uniqueId"] == STOOL


@pytest.mark.parametrize("params", [
    {}, {"handle": ""}, {"handle": "@"}, {"handle": "a b"}, {"handle": STOOL_VIDEO},
    {"handle": "https://www.youtube.com/@stoolpresidente"}, {"user_id": "abc"}, {"user_id": "-1"},
    {"user_id": "٣٤"}, {"user_id": "1" * 26},
])
def test_profile_bad_input_is_400(client, params):
    err(client, PROFILE, 400, "bad_request", **params)


@pytest.mark.live
def test_profile_missing_and_private(client):
    err(client, PROFILE, 404, "not_found", handle=MISSING_USER)
    err(client, PROFILE, 404, "not_found", user_id="1")
    body = ok(client, PROFILE, handle=PRIVATE_USER)
    assert body["user"]["privateAccount"] is True and body["user"]["uniqueId"] == PRIVATE_USER


@pytest.mark.live
def test_cache_max_age(tmp_path):
    with app_with({}, tmp_path) as c:
        first = get(c, PROFILE, handle=STOOL, cache_max_age="1")
        assert first.status_code == 200 and first.headers["x-cache"] == "miss" and "cached" not in first.json()
        hit = get(c, PROFILE, handle=STOOL, cache_max_age="1d")
        assert hit.headers["x-cache"] == "hit" and hit.json()["cached"] is True and hit.json()["cached_at"]
        assert hit.json()["user"] == first.json()["user"]
        assert get(c, PROFILE, handle=STOOL, cache_max_age="0").headers["x-cache"] == "miss"
        for _ in range(2):  # errors are never stored
            r = get(c, PROFILE, handle=MISSING_USER, cache_max_age="1")
            assert r.status_code == 404 and "x-cache" not in r.headers
        for bad in ("abc", "-1", "1.5"):
            err(c, PROFILE, 400, "bad_request", handle=STOOL, cache_max_age=bad)


# --- /v3/tiktok/profile/videos -----------------------------------------------------------------


@pytest.fixture(scope="module")
def stool_walk(client) -> list[dict]:
    pages, cursor = [], None
    for _ in range(6):
        params = {"handle": STOOL} if cursor is None else {"handle": STOOL, "max_cursor": cursor}
        pages.append(ok(client, VIDEOS, **params))
        cursor = pages[-1].get("max_cursor")
        if not pages[-1].get("has_more"):
            break
    return pages


@pytest.mark.live
def test_profile_videos_walk(stool_walk):
    assert len(stool_walk) == 6 and all(p["has_more"] is True for p in stool_walk)
    seen: list[str] = []
    last_time, last_cursor = None, None
    for page in stool_walk:
        assert_conforms(page, VIDEOS, min_coverage=0.25)
        items = page["aweme_list"]
        assert 1 <= len(items) <= 20
        times = [a["create_time"] for a in items]
        assert times == sorted(times, reverse=True)
        if last_time is not None:
            assert times[0] <= last_time and times[0] * 1000 <= last_cursor
        assert page["max_cursor"] == min(times) * 1000 - 1
        assert last_cursor is None or page["max_cursor"] < last_cursor
        assert all(a["author"]["unique_id"] == STOOL for a in items)
        seen += [a["aweme_id"] for a in items]
        last_time, last_cursor = times[-1], page["max_cursor"]
    assert len(seen) == len(set(seen))


@pytest.mark.live
def test_profile_videos_walk_matches_ytdlp(stool_walk):
    ours = {a["aweme_id"]: a["create_time"] for p in stool_walk for a in p["aweme_list"]}
    oldest, newest = min(ours.values()), max(ours.values())
    info = ytdlp_info(f"https://www.tiktok.com/@{STOOL}", extract_flat=True, playlistend=len(ours) + 20)
    theirs = {e["id"]: e.get("timestamp") for e in info["entries"]}
    in_range = {i for i, ts in theirs.items() if ts and oldest <= ts <= newest}
    assert set(ours) - set(theirs) == set(), "posts in our walk that yt-dlp's feed lacks"
    assert in_range - set(ours) == set(), "posts yt-dlp lists inside the walked range that we skipped"


@pytest.mark.live
def test_profile_videos_cursor_edges(client, stool_walk):
    first = [a["aweme_id"] for a in stool_walk[0]["aweme_list"]]
    for cursor in ("", "0", "9999999999999", "1" * 25):
        page = ok(client, VIDEOS, handle=STOOL, max_cursor=cursor)
        assert [a["aweme_id"] for a in page["aweme_list"]][:3] == first[:3], cursor
    page2 = ok(client, VIDEOS, handle=STOOL, max_cursor=f" {stool_walk[0]['max_cursor']} ")
    assert page2["aweme_list"][0]["aweme_id"] == stool_walk[1]["aweme_list"][0]["aweme_id"]
    floor = ok(client, VIDEOS, handle=STOOL, max_cursor=str(_FEED_START_MS))
    assert floor["aweme_list"] == [] and floor["has_more"] is False and "max_cursor" not in floor


@pytest.mark.live
def test_profile_videos_photo_post_carries_its_images(client):
    items = ok(client, VIDEOS, handle=WAPO, max_cursor=str((WAPO_PHOTO_TIME + 1) * 1000))["aweme_list"]
    photos = {a["aweme_id"]: a for a in items if "image_post_info" in a}
    assert WAPO_PHOTO_ID in photos, [a["aweme_id"] for a in items]
    images = photos[WAPO_PHOTO_ID]["image_post_info"]["images"]
    assert len(images) == 10 and all(i["display_image"]["url_list"] for i in images)
    assert not any("music" in a for a in items)


@pytest.mark.parametrize("cursor", ["abc", "-5", "1.5e12", "1" * 26, "1746050510", "1000", str(_FEED_START_MS - 1)])
def test_profile_videos_bad_cursor_is_400(client, cursor):
    err(client, VIDEOS, 400, "bad_cursor", handle=STOOL, max_cursor=cursor)


def test_profile_videos_bad_cursor_message(client):
    body = err(client, VIDEOS, 400, "bad_cursor", handle=STOOL, max_cursor="abc")
    assert "TikTok id" not in body["message"] and "max_cursor" in body["message"]


def test_profile_videos_param_validation(client):
    err(client, VIDEOS, 501, "not_supported", handle=STOOL, sort_by="popular")
    for sort_by in ("LATEST", "oldest", "Popular"):
        err(client, VIDEOS, 400, "bad_request", handle=STOOL, sort_by=sort_by)
    for region in ("USA", "12", "u"):
        err(client, VIDEOS, 400, "bad_request", handle=STOOL, region=region)
    err(client, VIDEOS, 400, "bad_request", user_id=STOOL_UID)  # handle is required, as on ScrapeCreators
    err(client, VIDEOS, 400, "bad_request", handle="@")


@pytest.mark.live
def test_profile_videos_accepted_params(client, stool_walk):
    first = [a["aweme_id"] for a in stool_walk[0]["aweme_list"]]
    page = ok(client, VIDEOS, handle=f"@{STOOL.upper()}", sort_by="latest", region="gb", include_pinned="false",
              user_id="1", trim="true")
    assert [a["aweme_id"] for a in page["aweme_list"]][:3] == first[:3]


@pytest.mark.live
def test_profile_videos_small_account_terminates(client):
    pages, cursor = [], None
    for _ in range(5):
        params = {"handle": SMALL_USER} if cursor is None else {"handle": SMALL_USER, "max_cursor": cursor}
        pages.append(ok(client, VIDEOS, **params))
        cursor = pages[-1].get("max_cursor")
        if not pages[-1]["has_more"]:
            break
    assert pages[-1]["has_more"] is False and "max_cursor" not in pages[-1]
    ids = [a["aweme_id"] for p in pages for a in p["aweme_list"]]
    assert 1 <= len(ids) < 75 and len(ids) == len(set(ids))


@pytest.mark.live
def test_profile_videos_private_empty_missing(client):
    err(client, VIDEOS, 403, "forbidden", handle=PRIVATE_USER)
    empty = ok(client, VIDEOS, handle=EMPTY_USER)
    assert empty["aweme_list"] == [] and empty["has_more"] is False and "max_cursor" not in empty
    err(client, VIDEOS, 404, "not_found", handle=MISSING_USER)


# --- /v1/tiktok/profile/region -----------------------------------------------------------------


@pytest.mark.live
def test_region(client):
    body = ok(client, REGION, handle=f"https://www.tiktok.com/@{STOOL}")
    assert_conforms(body, REGION, min_coverage=0.8)
    assert body == {**body, "handle": STOOL, "profile_url": f"https://www.tiktok.com/@{STOOL}", "region": "US"}
    # a non-US account, not the feed's US default: TikTok's app said GB for bbcnews, and AU since 2026-10
    region = ok(client, REGION, handle="bbcnews")["region"]
    assert re.fullmatch(r"[A-Z]{2}", region) and region != "US", region


@pytest.mark.live
def test_region_without_own_sound_is_404(client):
    r = get(client, REGION, handle=NO_OWN_SOUND_USER)
    if r.status_code == 200:
        pytest.skip(f"@{NO_OWN_SOUND_USER} now has a post with its own original sound")
    assert (r.status_code, r.json()["error"]) == (404, "region_unavailable")
    assert "original sound" in r.json()["message"]


@pytest.mark.live
def test_region_errors(client):
    err(client, REGION, 403, "forbidden", handle=PRIVATE_USER)
    err(client, REGION, 404, "region_unavailable", handle=EMPTY_USER)
    err(client, REGION, 404, "not_found", handle=MISSING_USER)
    err(client, REGION, 400, "bad_request", handle="@")
    assert err(client, REGION, 400, "bad_request")["message"] == "handle: Field required"


# --- docs --------------------------------------------------------------------------------------


def test_coverage_notes(client):
    entries = {e["path"]: e for e in client.get("/meta/coverage").json()["endpoints"]}
    for path in (VIDEO, TRANSCRIPT, PROFILE, VIDEOS, REGION):
        assert entries[path]["status"] == "implemented" and entries[path]["notes"]
