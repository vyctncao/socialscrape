"""Checks for the TikTok gaps found by the completeness review. Only the tests marked live reach TikTok.

Covers what the other TikTok suites leave untested or only test shallowly:
- the 19 TikTok 501 routes and the OpenAPI surface of the 10 implemented ones
- 405 and redirect behaviour
- auth with the cache on
- cache_max_age keys on the transcript route
- caption language matching for bilingual and Chinese posts
- AI transcription with a broken model and with a backend that isn't installed
- cookie-file, dead-proxy, request-timeout and max-workers handling
- /v3, song/videos and collection cursor edges
- the /v3 profile cache
- /v2 URL forms
- exact large-account stats
- the account-region lookup
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec

import pytest

from socialscrape.errors import UpstreamBlocked, UpstreamUnreachable
from socialscrape.platforms import tiktok
from tests.e2e import LEAKS, app_with, free_port, skip_if_blocked, spaced_get

STOOL = "stoolpresidente"
STOOL_ID = "7499229683859426602"
SCOUT_VIDEO = "https://www.tiktok.com/@scout2015/video/6718335390845095173"  # no captions, no speech
LONG_TT = "https://www.tiktok.com/@stoolpresidente/video/7691685734738119950"  # 124 s, English captions only
KHABY_ES = "https://www.tiktok.com/@khaby.lame/video/7683944847840922911"  # spa-ES (original) + eng-US tracks
CGTN_ZH = "https://www.tiktok.com/@cgtnofficial/video/7697245129492630815"  # eng-US + cmn-Hans-CN (original)
SMALL_SOUND = "6689804660171082501"  # 10 videos over 3 pages
COLLECTION = "https://www.tiktok.com/@kibblemaster808/collection/Want-to-go-7665668414573546258"
ENDED_ROOM, ENDED_ROOM_UID = "7523685855395842871", "6742945285876515845"
LARGE = "khaby.lame"  # like count above 2**31

CJK = re.compile(r"[一-鿿]")
FEED_START_MS = 1472706000000

# Parameter names per implemented route (ScrapeCreators' plus cache_max_age).
ROUTE_PARAMS = {
    "/v2/tiktok/video": {"url", "get_transcript", "region", "trim", "download_media"},
    "/v1/tiktok/video/transcript": {"url", "language", "use_ai_as_fallback", "allow_long_transcription"},
    "/v1/tiktok/profile": {"handle", "user_id"},
    "/v3/tiktok/profile/videos": {"handle", "user_id", "sort_by", "max_cursor", "region", "include_pinned", "trim"},
    "/v1/tiktok/profile/region": {"handle"},
    "/v1/tiktok/user/live": {"handle"},
    "/v1/tiktok/live": {"room_id", "user_id"},
    "/v1/tiktok/song": {"clipId"},
    "/v1/tiktok/song/videos": {"clipId", "cursor"},
    "/v1/tiktok/collection/videos": {"url", "cursor"},
    "/v1/tiktok/creators/popular": {"page", "sortBy", "followerCount", "creatorCountry", "audienceCountry"},
    "/v1/tiktok/search/users": {"query", "cursor", "trim"},
}

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


def check_err(r, status: int, code: str) -> dict:
    assert r.headers["content-type"].startswith("application/json"), r.text[:300]
    body = r.json()
    assert (r.status_code, body.get("success"), body.get("error")) == (status, False, code), body
    assert not LEAKS.search(body["message"]), body["message"]
    return body


def err(c, path: str, status: int, code: str, **params) -> dict:
    return check_err(get(c, path, **params), status, code)


def _tiktok_rows(c) -> dict[str, str]:
    rows = c.get("/meta/coverage").json()["endpoints"]
    return {r["path"]: r["status"] for r in rows if r["platform"] == "tiktok" or "/tiktok/" in r["path"]}


# --- server surface (no TikTok traffic) ----------------------------------------------------------


def test_tiktok_501_routes_answer_501_405_and_expose_no_params(client):
    rows = _tiktok_rows(client)
    unsupported = sorted(p for p, s in rows.items() if s == "not_supported")
    assert len(unsupported) >= 17, unsupported
    spec = client.get("/openapi.json").json()["paths"]
    for path in unsupported:
        check_err(client.get(path, params={"handle": STOOL, "url": SCOUT_VIDEO}), 501, "not_supported")
        r = client.post(path)
        check_err(r, 405, "method_not_allowed")
        assert r.headers.get("allow") == "GET", (path, r.headers)
        params = {p["name"] for p in spec[path]["get"].get("parameters", [])}
        assert params <= {"cache_max_age"}, (path, params)


def test_implemented_routes_openapi_params_and_405(client):
    rows = _tiktok_rows(client)
    implemented = {p for p, s in rows.items() if s != "not_supported"}
    assert implemented == set(ROUTE_PARAMS), implemented ^ set(ROUTE_PARAMS)
    spec = client.get("/openapi.json").json()["paths"]
    for path, names in ROUTE_PARAMS.items():
        params = {p["name"] for p in spec[path]["get"].get("parameters", [])}
        assert params == names | {"cache_max_age"}, (path, params ^ (names | {"cache_max_age"}))
        for method in ("post", "put", "delete"):
            r = getattr(client, method)(path)
            check_err(r, 405, "method_not_allowed")
            assert r.headers.get("allow") == "GET", (path, method, r.headers)


def test_trailing_slash_redirects_and_wrong_case_is_404(client):
    r = client.get("/v1/tiktok/profile/", params={"handle": STOOL}, follow_redirects=False)
    assert r.status_code == 307, r.text
    assert r.headers["location"].split("?")[0].endswith("/v1/tiktok/profile"), r.headers
    check_err(client.get("/v1/TikTok/profile", params={"handle": STOOL}), 404, "not_found")


# --- auth -------------------------------------------------------------------------------------


@pytest.mark.live
def test_auth_keys_with_cache_on(tmp_path):
    with app_with({"SOCIALSCRAPE_API_KEYS": "qa-key-1, qa-key-2"}, tmp_path, raise_errors=False) as c:
        for public in ("/health", "/openapi.json", "/meta/coverage", "/docs"):
            assert c.get(public).status_code == 200, public
        assert c.get("/health").json()["auth"] is True
        route = "/v1/tiktok/search/users"
        check_err(c.get(route), 401, "unauthorized")
        check_err(c.get(route, headers={"x-api-key": "qa-key-3"}), 401, "unauthorized")
        check_err(c.get(route, params={"api_key": "qa-key-1"}), 401, "unauthorized")
        check_err(c.get(route, headers={"authorization": "Bearer qa-key-1"}), 401, "unauthorized")
        check_err(c.get(route, headers={"x-api-key": "qa-key-1"}), 501, "not_supported")
        check_err(c.get(route, headers={"x-api-key": "qa-key-2"}), 501, "not_supported")  # env value is stripped
        # auth runs before validation, routing and method checks
        check_err(c.get("/v1/tiktok/song", params={"clipId": "abc"}), 401, "unauthorized")
        check_err(c.get("/v1/tiktok/song", params={"clipId": "abc"}, headers={"x-api-key": "qa-key-1"}),
                  400, "bad_request")
        check_err(c.post("/v1/tiktok/profile"), 401, "unauthorized")
        check_err(c.get("/v1/tiktok/nope"), 401, "unauthorized")
        # a cached body is never served without a key
        key = {"x-api-key": "qa-key-1"}
        r1 = c.get("/v1/tiktok/profile", params={"handle": STOOL}, headers=key)
        skip_if_blocked(r1)
        assert r1.status_code == 200 and r1.headers["x-cache"] == "miss", r1.text[:300]
        r2 = c.get("/v1/tiktok/profile", params={"handle": STOOL, "cache_max_age": "1d"}, headers=key)
        assert r2.headers["x-cache"] == "hit" and r2.json()["cached"] is True
        r3 = c.get("/v1/tiktok/profile", params={"handle": STOOL, "cache_max_age": "1d"})
        check_err(r3, 401, "unauthorized")
        assert "x-cache" not in r3.headers


@pytest.mark.parametrize("header", ["qa-key-1 ", " qa-key-1", "qa-key-1\t"])
def test_api_key_header_surrounding_whitespace_is_ignored(header):
    with app_with({"SOCIALSCRAPE_API_KEYS": "qa-key-1, qa-key-2"}, raise_errors=False) as c:
        response = c.get("/v1/tiktok/search/users", params={"query": "not_a_real_user_12345"},
                         headers={"x-api-key": header})
        assert response.status_code != 401


# --- captions and the cache key ---------------------------------------------------------------


@pytest.mark.live
def test_bilingual_caption_language_matching_and_cache_key(tmp_path):
    with app_with({}, tmp_path, raise_errors=False) as c:
        default = ok(c, "/v1/tiktok/video/transcript", url=KHABY_ES)["transcript"]
        if not (default and default.startswith("WEBVTT")):
            pytest.skip("KHABY_ES lost its native captions")
        r = get(c, "/v1/tiktok/video/transcript", url=KHABY_ES, language="es")
        assert r.status_code == 200 and r.headers["x-cache"] == "miss"
        es = r.json()["transcript"]
        assert es == default and "aqu" in es.lower()  # original track is the Spanish one
        assert ok(c, "/v1/tiktok/video/transcript", url=KHABY_ES, language="spa")["transcript"] == es
        en = ok(c, "/v1/tiktok/video/transcript", url=KHABY_ES, language="eng")["transcript"]
        assert en.startswith("WEBVTT") and en != es and re.search(r"\b(look|here)\b", en.lower()), en[:300]
        assert ok(c, "/v1/tiktok/video/transcript", url=KHABY_ES, language="fr")["transcript"] is None
        hit = c.get("/v1/tiktok/video/transcript", params={"url": KHABY_ES, "language": "es", "cache_max_age": "3d"})
        assert hit.headers["x-cache"] == "hit" and hit.json()["cached"] is True and hit.json()["cached_at"]
        assert hit.json()["transcript"] == es
        fresh = get(c, "/v1/tiktok/video/transcript", url=KHABY_ES, language="es", cache_max_age="0")
        assert fresh.status_code == 200 and fresh.headers["x-cache"] == "miss"
        check_err(c.get("/v1/tiktok/video/transcript", params={"url": KHABY_ES, "cache_max_age": "abc"}),
                  400, "bad_request")
        check_err(c.get("/v1/tiktok/search/users", params={"cache_max_age": "abc"}), 400, "bad_request")


_CGTN: dict[str, str | None] = {}


def _cgtn_default(c) -> str:
    if "default" not in _CGTN:
        _CGTN["default"] = ok(c, "/v1/tiktok/video/transcript", url=CGTN_ZH)["transcript"]
    vtt = _CGTN["default"]
    if not (vtt and vtt.startswith("WEBVTT") and CJK.search(vtt)):
        pytest.skip("CGTN_ZH no longer has its Chinese original-language captions")
    return vtt


@pytest.mark.live
def test_chinese_post_cmn_and_english_tracks(client):
    zh = _cgtn_default(client)
    assert ok(client, "/v1/tiktok/video/transcript", url=CGTN_ZH, language="cmn")["transcript"] == zh
    en = ok(client, "/v1/tiktok/video/transcript", url=CGTN_ZH, language="en")["transcript"]
    assert en and en.startswith("WEBVTT") and not CJK.search(en), (en or "")[:300]


@pytest.mark.live
def test_chinese_post_language_zh_returns_chinese_track(client):
    zh = _cgtn_default(client)
    for lang in ("zh", "zho", "ZH"):  # gap-tiktok-2: 'zh' is ScrapeCreators' own example
        assert ok(client, "/v1/tiktok/video/transcript", url=CGTN_ZH, language=lang)["transcript"] == zh, lang


# --- AI transcription: a broken model, a backend that isn't installed --------------------------


def _need_mlx():
    if find_spec("mlx_whisper") is None:
        pytest.skip("mlx-whisper is not installed")


@pytest.mark.live
def test_ai_broken_model_is_transcription_failed():
    _need_mlx()
    with app_with({"SOCIALSCRAPE_TRANSCRIBE": "mlx-whisper",
                   "SOCIALSCRAPE_WHISPER_MODEL": "qa-nonexistent-model-xyz"}, raise_errors=False) as c:
        err(c, "/v1/tiktok/video/transcript", 500, "transcription_failed", url=SCOUT_VIDEO,
            use_ai_as_fallback="true")


@pytest.mark.live
def test_ai_backend_not_installed_is_transcription_unavailable():
    if find_spec("faster_whisper") is not None:
        pytest.skip("faster-whisper is installed here")
    with app_with({"SOCIALSCRAPE_TRANSCRIBE": "faster-whisper"}, raise_errors=False) as c:
        assert c.get("/health").json()["transcription"] is None
        err(c, "/v1/tiktok/video/transcript", 501, "transcription_unavailable", url=SCOUT_VIDEO,
            use_ai_as_fallback="true")
        # native captions don't need the backend
        vtt = ok(c, "/v1/tiktok/video/transcript", url=LONG_TT, language="en", use_ai_as_fallback="true")
        assert (vtt["transcript"] or "").startswith("WEBVTT")


# --- server settings: cookies, proxy, timeout, workers ------------------------------------------


@pytest.mark.parametrize("kind", ["missing", "malformed", "directory"])
def test_unusable_cookie_file_is_cookies_error(tmp_path, kind):
    path = tmp_path / "tt_cookies.txt"
    if kind == "malformed":
        path.write_text("this is not a cookies file\n")
    elif kind == "directory":
        path.mkdir()
    with app_with({"SOCIALSCRAPE_COOKIES_TIKTOK": str(path)}, tmp_path, raise_errors=False) as c:
        health = c.get("/health").json()
        assert health["ok"] is True and health["cookie_errors"].get("tiktok"), health
        assert "tiktok" not in health["cookies"]
        for path_, params in (("/v1/tiktok/profile", {"handle": STOOL}),
                              ("/v1/tiktok/song", {"clipId": SMALL_SOUND}),
                              ("/v1/tiktok/video/transcript", {"url": SCOUT_VIDEO})):
            body = check_err(c.get(path_, params=params), 500, "cookies_error")
            assert str(tmp_path) not in body["message"]
        check_err(c.get("/v1/tiktok/search/users", params={"query": STOOL}), 500, "cookies_error")
        check_err(c.get("/v1/tiktok/song", params={"clipId": "abc"}), 400, "bad_request")


@pytest.mark.live
def test_valid_dummy_cookie_file_is_used_and_left_unchanged(tmp_path):
    path = tmp_path / "tt_cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n.tiktok.com\tTRUE\t/\tFALSE\t2000000000\tqa_dummy\tdummyvalue\n")
    before = path.read_bytes()
    with app_with({"SOCIALSCRAPE_COOKIES_TIKTOK": str(path)}, tmp_path, raise_errors=False) as c:
        health = c.get("/health").json()
        assert "tiktok" in health["cookies"] and health["cookie_errors"] == {}, health
        body = ok(c, "/v1/tiktok/profile", handle=STOOL)
        assert body["user"]["uniqueId"].lower() == STOOL
    assert path.read_bytes() == before


def test_dead_proxy_with_credentials_is_proxy_error_without_secret():
    proxy = f"http://qauser:qasecret@127.0.0.1:{free_port()}"
    routes = [
        ("/v1/tiktok/profile", {"handle": STOOL}),
        ("/v2/tiktok/video", {"url": f"https://www.tiktok.com/@{STOOL}/video/{STOOL_ID}"}),
        ("/v2/tiktok/video", {"url": "https://vm.tiktok.com/ZMqa1234/"}),
        ("/v1/tiktok/song", {"clipId": SMALL_SOUND}),
        ("/v1/tiktok/live", {"room_id": ENDED_ROOM, "user_id": ENDED_ROOM_UID}),
        ("/v1/tiktok/collection/videos", {"url": COLLECTION}),
    ]
    with app_with({"SOCIALSCRAPE_PROXY": proxy}, raise_errors=False) as c:
        for path, params in routes:
            start = time.monotonic()
            r = c.get(path, params=params)
            body = check_err(r, 502, "proxy_error")
            assert "qasecret" not in r.text and "qauser" not in body["message"]
            assert time.monotonic() - start < 15, path


@pytest.mark.live
def test_request_timeout_is_500_timeout():
    def slow_user_page(ydl, ident):
        time.sleep(2)
        raise AssertionError("the request should have timed out")

    patches = [(tiktok, "_user_page", slow_user_page)]
    with app_with({"SOCIALSCRAPE_REQUEST_TIMEOUT": "0.3"}, raise_errors=False, patches=patches) as c:
        for path, params in (("/v1/tiktok/profile", {"handle": STOOL}),
                             ("/v2/tiktok/video", {"url": f"https://www.tiktok.com/@{STOOL}/video/{STOOL_ID}"})):
            start = time.monotonic()
            check_err(c.get(path, params=params), 500, "timeout")
            assert time.monotonic() - start < 2.0, path


@pytest.mark.live
def test_max_workers_1_serialises_scrapes_but_not_cheap_routes():
    spans: list[tuple[float, float]] = []
    orig = tiktok._user_page

    def slow_user_page(ydl, ident):
        t0 = time.monotonic()
        time.sleep(1.5)
        try:
            return orig(ydl, ident)
        finally:
            spans.append((t0, time.monotonic()))

    patches = [(tiktok, "_user_page", slow_user_page)]
    with app_with({"SOCIALSCRAPE_MAX_WORKERS": "1"}, raise_errors=False, patches=patches) as c:
        with ThreadPoolExecutor(2) as ex:
            futs = [ex.submit(c.get, "/v1/tiktok/profile", params={"handle": h}) for h in (STOOL, LARGE)]
            time.sleep(0.4)
            for path, params, status in (("/health", {}, 200), ("/v1/tiktok/search/users", {}, 501),
                                         ("/v1/tiktok/song", {"clipId": "abc"}, 400)):
                t0 = time.monotonic()
                assert c.get(path, params=params).status_code == status, path
                assert time.monotonic() - t0 < 1.0, path  # not queued behind the busy pool
            results = [f.result(timeout=120) for f in futs]
    for r in results:
        skip_if_blocked(r)
        assert r.status_code == 200, r.text[:300]
    (a0, a1), (b0, b1) = sorted(spans)
    assert a1 <= b0 + 0.05, spans  # the second scrape started only after the first finished


# --- cursors and paging -----------------------------------------------------------------------


@pytest.mark.live
def test_v3_profile_cache_and_max_cursor_edges(client, monkeypatch):
    calls: list[str] = []
    orig = tiktok._user_page

    def counting(ydl, ident):
        calls.append(ident)
        return orig(ydl, ident)

    monkeypatch.setattr(tiktok, "_profiles", {})
    monkeypatch.setattr(tiktok, "_user_page", counting)
    p1 = ok(client, "/v3/tiktok/profile/videos", handle=STOOL)
    assert len(calls) == 1 and p1["aweme_list"] and p1["has_more"] is True, (calls, p1.keys())
    cur = p1["max_cursor"]
    assert isinstance(cur, int) and cur >= FEED_START_MS
    assert cur == min(a["create_time"] for a in p1["aweme_list"]) * 1000 - 1
    p2 = ok(client, "/v3/tiktok/profile/videos", handle="StoolPresidente", max_cursor=str(cur))
    assert len(calls) == 1, calls  # secUid reused across pages and handle case
    ids1 = {a["aweme_id"] for a in p1["aweme_list"]}
    assert p2["aweme_list"] and not ids1 & {a["aweme_id"] for a in p2["aweme_list"]}
    assert all(a["create_time"] * 1000 <= cur for a in p2["aweme_list"])
    end = ok(client, "/v3/tiktok/profile/videos", handle=STOOL, max_cursor=str(FEED_START_MS))
    assert end["aweme_list"] == [] and end["has_more"] is False and "max_cursor" not in end
    for bad in (str(FEED_START_MS - 1), f"{cur}.0", "abc", "-5"):
        err(client, "/v3/tiktok/profile/videos", 400, "bad_cursor", handle=STOOL, max_cursor=bad)
    assert len(calls) == 1, calls


@pytest.mark.live
def test_song_videos_cursor_edges(client):
    first = ok(client, "/v1/tiktok/song/videos", clipId=SMALL_SOUND, cursor="20")
    padded = ok(client, "/v1/tiktok/song/videos", clipId=SMALL_SOUND, cursor="020")
    assert [a["aweme_id"] for a in padded["aweme_list"]] == [a["aweme_id"] for a in first["aweme_list"]]
    beyond = ok(client, "/v1/tiktok/song/videos", clipId=SMALL_SOUND, cursor="999999999")
    assert beyond["aweme_list"] == [] and not beyond.get("has_more") and "cursor" not in beyond
    for bad in ("1000000000", "-1", "1.5", "2 0", "abc"):
        err(client, "/v1/tiktok/song/videos", 400, "bad_cursor", clipId=SMALL_SOUND, cursor=bad)


@pytest.mark.live
def test_collection_cursor_past_the_end(client):
    p1 = ok(client, "/v1/tiktok/collection/videos", url=COLLECTION)
    n = len(p1["videos"])
    if p1["has_more"] or not n:
        pytest.skip("collection changed size")
    assert isinstance(p1["max_cursor"], str) and p1["max_cursor"].isdigit()
    assert n <= 30 and p1["collection_id"] == COLLECTION.rsplit("-", 1)[1]
    past = ok(client, "/v1/tiktok/collection/videos", url=COLLECTION, cursor=str(n))
    assert past["videos"] == [] and past["has_more"] is False
    for bad in ("-1", "1.5", "abc"):
        err(client, "/v1/tiktok/collection/videos", 400, "bad_cursor", url=COLLECTION, cursor=bad)


# --- /v2 URL forms, stats, region -------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("url", [
    f"https://m.tiktok.com/@{STOOL}/video/{STOOL_ID}",
    f"https://www.tiktok.com/@{STOOL}/video/{STOOL_ID}?is_from_webapp=1&sender_device=pc#comments",
    f"http://www.tiktok.com/@{STOOL}/video/{STOOL_ID}/",
    f"https://www.tiktok.com/@someone.else/video/{STOOL_ID}",
    f"https://www.tiktokv.com/share/video/{STOOL_ID}/",
])
def test_v2_url_forms_resolve_to_the_post(client, url):
    d = ok(client, "/v2/tiktok/video", url=url)["aweme_detail"]
    assert str(d["aweme_id"]) == STOOL_ID
    assert d["author"]["unique_id"].lower() == STOOL
    assert d["url"] == f"https://www.tiktok.com/@{d['author']['unique_id']}/video/{STOOL_ID}"


@pytest.mark.live
def test_large_account_stats_are_exact(client):
    body = ok(client, "/v1/tiktok/profile", handle=LARGE)
    stats = body["stats"]
    assert stats["heartCount"] >= 2**31 and stats["heart"] >= 2**31, stats  # int32 overflow would be negative
    for k, v in stats.items():
        if isinstance(v, int) and not isinstance(v, bool):
            assert v >= 0, (k, v)


@pytest.mark.live
def test_profile_region_reads_the_app_region(client):
    r = get(client, "/v1/tiktok/profile/region", handle=STOOL)
    assert r.status_code == 200, r.text[:300]  # a failed app-API lookup is a 502/503 now, not 404 (gap-tiktok-1)
    body = r.json()
    assert re.fullmatch(r"[A-Z]{2}", body["region"]) and body["handle"] == STOOL
    assert body["profile_url"] == f"https://www.tiktok.com/@{STOOL}"


@pytest.mark.live
@pytest.mark.parametrize("exc,status,code", [
    (UpstreamBlocked("The platform returned an empty response; try again later"), 503, "upstream_blocked"),
    (UpstreamUnreachable("Could not reach the platform through the configured proxy", code="proxy_error"),
     502, "proxy_error"),
])
def test_profile_region_app_api_failure_is_not_404(client, monkeypatch, exc, status, code):
    calls: list[str] = []

    def failing_music_page(ydl, clip_id, cursor):
        calls.append(clip_id)
        raise exc

    monkeypatch.setattr(tiktok, "_music_page", failing_music_page)
    r = spaced_get(client, "/v1/tiktok/profile/region", _SPACING, handle=STOOL)  # no skip: a 503 is expected
    if not calls:
        pytest.skip(f"region lookup never reached the app API ({r.status_code}): {r.text[:200]}")
    check_err(r, status, code)
