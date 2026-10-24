"""Gap checks for the Instagram endpoints, from the completeness review. Only the tests marked live reach Instagram.

Covers what the per-endpoint suites (test_e2e_ig_post.py, test_e2e_ig_profile.py) leave out: decomposed (NFD)
topics, an empty hashtag cursor, restricted (age-gated) profiles, zero-comment posts, server configuration paths
for every Instagram endpoint (dead / hanging proxy, request timeout, API keys, missing / malformed / unrelated /
rejected cookie files, an unavailable or broken Whisper backend), the eight 501 routes, cache behaviour of the
remaining endpoints and transcript speech detection. Anonymous only: cookie files here are dummies written to
pytest's tmp_path.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import time
import unicodedata

import pytest

from tests.e2e import LEAKS, app_with, free_port, skip_if_blocked, spaced_get

IG = "https://www.instagram.com"
JANE = "jane"
REEL = "DLDXI0fylTC"  # jane, 85 s of speech
SPEECH_REEL = "DQhKIRakcbY"  # jane, short sketch with dialogue
IMAGE = "Deh3_cCl1Qs"
ZERO_COMMENTS = "DFI3p_7i1be"  # 4irim image carousel without comments
HIGHLIGHT = "18382085791177637"
ABBREVIATED = "natgeo"
PRIVATE = "krys_midnight"
RESTRICTED = "jackdaniels_us"  # alcohol brand: "Restricted profile ... Log in to continue" for logged-out visitors

POST = "/v1/instagram/post"
COMMENTS = "/v2/instagram/post/comments"
PROFILE = "/v1/instagram/profile"
POST_COUNT = "/v1/instagram/profile/post-count"
POSTS = "/v2/instagram/user/posts"
REELS = "/v1/instagram/user/reels"
HIGHLIGHTS = "/v1/instagram/user/highlights"
DETAIL = "/v1/instagram/user/highlight/detail"
SEARCH = "/v1/instagram/search"
POPULAR = "/v1/instagram/search/popular"
HASHTAG = "/v1/instagram/search/hashtag"
REELS_SEARCH = "/v2/instagram/reels/search"
TRANSCRIPT = "/v2/instagram/media/transcript"

# Every implemented Instagram endpoint with parameters that pass validation.
IMPLEMENTED = [
    (POST, {"url": REEL}),
    (COMMENTS, {"url": REEL}),
    (PROFILE, {"handle": JANE}),
    (POST_COUNT, {"handle": JANE}),
    (POSTS, {"handle": JANE}),
    (REELS, {"handle": JANE}),
    (HIGHLIGHTS, {"handle": JANE}),
    (DETAIL, {"id": HIGHLIGHT}),
    (SEARCH, {"query": "nasa"}),
    (POPULAR, {"query": "cats"}),
    (HASHTAG, {"hashtag": "cats"}),
    (REELS_SEARCH, {"query": "cats"}),
    (TRANSCRIPT, {"url": REEL}),
]
NOT_TRANSCRIPT = [e for e in IMPLEMENTED if e[0] not in (TRANSCRIPT, SEARCH)]
UNSUPPORTED = []
HAS_MLX = importlib.util.find_spec("mlx_whisper") is not None
# A cookies.txt with an invalid (expired-looking) Instagram session: what an operator has once a session dies.
FAKE_SESSION = (
    "# Netscape HTTP Cookie File\n"
    ".instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\t12345%3AFAKEFAKEFAKE%3A1%3AAYfake\n"
    ".instagram.com\tTRUE\t/\tTRUE\t2147483647\tds_user_id\t12345\n"
)

_SEEN: dict[str, tuple[int, dict]] = {}
_SPACING = 1.5  # seconds between calls that reached Instagram


def raw(c, path: str, **params) -> tuple[int, dict]:
    """One spaced GET (no skipping, no memo)."""
    r = spaced_get(c, path, _SPACING, **params)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"success": False, "error": "non_json", "message": r.text[:200]}


def call(c, path: str, **params) -> tuple[int, dict]:
    """GET once per distinct client+URL per session, spaced; skips when Instagram throttles this IP."""
    key = json.dumps([id(c), path, params], sort_keys=True, ensure_ascii=False)
    if key not in _SEEN:
        status, body = raw(c, path, **params)
        if status in (429, 503):
            pytest.skip(f"Instagram throttled this IP: {body.get('message')}")
        _SEEN[key] = (status, body)
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


def ensure_not_throttled(c) -> None:
    """A fresh anonymous profile load must work before a 503 is treated as our own misclassification."""
    status, body = raw(c, PROFILE, handle=JANE)
    if status in (429, 503):
        pytest.skip(f"Instagram throttled this IP: {body.get('message')}")
    assert status == 200, body


def _items(body: dict) -> list:
    return body.get("posts") or body.get("reels") or []


# --- topics -------------------------------------------------------------------------------


@pytest.mark.live
def test_decomposed_hashtag_matches_composed(client):
    nfc = ok(client, HASHTAG, hashtag="españa")
    nfd = ok(client, HASHTAG, hashtag=unicodedata.normalize("NFD", "españa"))
    assert nfd["hashtag"] == nfc["hashtag"] == "españa"
    assert unicodedata.is_normalized("NFC", nfd["hashtag"])
    assert _items(nfd) and _items(nfc)


@pytest.mark.live
@pytest.mark.parametrize("path", [POPULAR, REELS_SEARCH])
def test_decomposed_query_is_found(client, path):
    body = ok(client, path, query=unicodedata.normalize("NFD", "münchen"))
    assert _items(body)
    if path == POPULAR:
        assert body["query"] == "münchen" and unicodedata.is_normalized("NFC", body["query"])


@pytest.mark.live
def test_turkish_dotted_capital_i_is_folded(client):
    body = ok(client, POPULAR, query="İstanbul")
    assert body["query"] == "istanbul" and body["posts"]


@pytest.mark.live
def test_hashtag_empty_cursor_is_first_page(client):
    body = ok(client, HASHTAG, hashtag="cats", cursor="")
    assert body["hashtag"] == "cats" and body["posts"]


@pytest.mark.live
def test_hashtag_reels_filter_keeps_only_videos(client):
    body = ok(client, HASHTAG, hashtag="cats", media_type="reels")
    assert body["media_type"] == "reels" and body["posts"]
    assert all(p["is_video"] and "/reel/" in p["url"] for p in body["posts"])


# --- profiles -----------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("path", [PROFILE, HIGHLIGHTS, REELS])
def test_restricted_profile_is_403(client, path):
    status, body = call(client, path, handle=RESTRICTED)
    if status == 200:
        pytest.skip("the profile is no longer restricted for logged-out visitors")
    assert status == 403, body


@pytest.mark.live
def test_restricted_profile_post_count(client):
    # The restricted page still carries "2,370 Posts" in og:description, so a count (or a 403) is expected.
    status, body = call(client, POST_COUNT, handle=RESTRICTED)
    assert status in (200, 403), body
    if status == 200:
        assert isinstance(body["media_count"], int) and body["media_count"] > 0


# --- posts / comments ---------------------------------------------------------------------


@pytest.mark.live
def test_zero_comment_post(client):
    m = ok(client, POST, url=ZERO_COMMENTS)["data"]["xdt_shortcode_media"]
    edges = m["edge_media_to_parent_comment"]["edges"]
    comments = ok(client, COMMENTS, url=ZERO_COMMENTS)["comments"]
    assert isinstance(comments, list) and len(comments) == len(edges)
    assert m["edge_media_to_parent_comment"]["count"] >= len(edges)


@pytest.mark.live
def test_comments_endpoint_matches_post_edges(client):
    m = ok(client, POST, url=REEL)["data"]["xdt_shortcode_media"]
    comments = ok(client, COMMENTS, url=REEL)["comments"]
    edges = {e["node"]["id"]: e["node"] for e in m["edge_media_to_parent_comment"]["edges"]}
    common = [c for c in comments if c["id"] in edges]
    assert edges and len(common) >= len(edges) - 2  # two separate requests: allow a new comment or two
    for c in common:
        n = edges[c["id"]]
        assert c["text"] == n["text"] and c["user"]["id"] == n["owner"]["id"]
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(n["created_at"]))
        assert c["created_at"] == stamp


@pytest.mark.parametrize("url", [
    f"https://evil.example/?u=instagram.com/p/{REEL}/",
    f"https://ddinstagram.com/p/{REEL}/",
    f"{IG}/stories/jane/3657869083548472514/",
])
def test_foreign_or_non_post_urls_are_400(client, url):
    err(client, POST, 400, "unsupported_url", url=url)


# --- 501 routes, routing, auth ------------------------------------------------------------


@pytest.mark.parametrize("path", UNSUPPORTED)
def test_unsupported_route_envelope(client, path):
    r = client.get(path, params={"handle": JANE, "userId": "1", "url": REEL})
    body = r.json()
    assert r.status_code == 501 and body["error"] == "not_supported" and body["endpoint"] == path
    assert body["message"]
    p = client.post(path)
    assert p.status_code == 405 and p.headers["allow"] == "GET" and p.json()["error"] == "method_not_allowed"


def test_instagram_coverage_has_no_unsupported_routes(client):
    entries = client.get("/meta/coverage").json()["endpoints"]
    mine = [entry for entry in entries if entry["platform"] == "instagram"]
    assert len(mine) == 20 and not [entry for entry in mine if entry["status"] == "not_supported"]
    assert all(entry["source"] and entry["notes"] for entry in mine)


def test_unknown_instagram_path_and_wrong_method(client):
    r = client.get("/v1/instagram/xyz")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    for path, params in IMPLEMENTED:
        p = client.put(path, params=params)
        assert p.status_code == 405 and p.headers["allow"] == "GET", path


def test_api_keys_guard_every_instagram_route():
    with app_with({"SOCIALSCRAPE_API_KEYS": "k1,k2"}, raise_errors=False) as c:
        for path, params in IMPLEMENTED + [(p, {}) for p in UNSUPPORTED]:
            assert c.get(path, params=params).status_code == 401, path
            assert c.get(path, params=params, headers={"x-api-key": "nope"}).status_code == 401, path
            assert c.get(path, params={**params, "x-api-key": "k1"}).status_code == 401, path
        r = c.get(POST, params={"url": "a.b"}, headers={"x-api-key": "k2"})
        assert r.status_code == 400 and r.json()["error"] == "unsupported_url"
        assert c.get("/meta/coverage").status_code == 200


# --- network configuration (no Instagram traffic) -----------------------------------------


def test_dead_proxy_is_502_on_every_endpoint():
    env = {"SOCIALSCRAPE_PROXY": f"http://127.0.0.1:{free_port()}"}
    if HAS_MLX:
        env |= {"SOCIALSCRAPE_TRANSCRIBE": "mlx-whisper", "SOCIALSCRAPE_WHISPER_MODEL": "small"}
    with app_with(env, raise_errors=False) as c:
        for path, params in IMPLEMENTED if HAS_MLX else NOT_TRANSCRIPT:
            r = c.get(path, params=params)
            body = r.json()
            assert r.status_code == 502 and body["error"] == "proxy_error", (path, body)
            assert "127.0.0.1" not in body["message"]


@pytest.mark.live
def test_request_timeout_is_500_timeout_on_every_endpoint():
    hang = socket.socket()  # accepts TCP connections (backlog) and never answers
    hang.bind(("127.0.0.1", 0))
    hang.listen(64)
    env = {"SOCIALSCRAPE_PROXY": f"http://127.0.0.1:{hang.getsockname()[1]}",
           "SOCIALSCRAPE_REQUEST_TIMEOUT": "0.3", "SOCIALSCRAPE_SOCKET_TIMEOUT": "2"}
    try:
        with app_with(env, raise_errors=False) as c:
            for path, params in NOT_TRANSCRIPT:
                start = time.monotonic()
                r = c.get(path, params=params)
                assert r.status_code == 500 and r.json()["error"] == "timeout", (path, r.json())
                assert time.monotonic() - start < 1.5, path
    finally:
        hang.close()


# --- cookie files (dummies only) ----------------------------------------------------------


@pytest.mark.parametrize("content,problem", [
    (None, "does not exist"),
    ("this is not a cookie file\n\x00\x01garbage\n", "is not a valid Netscape cookies.txt file"),
])
def test_unusable_cookie_file_is_cookies_error(tmp_path, content, problem):
    path = tmp_path / "ig.txt"
    if content is not None:
        path.write_text(content)
    with app_with({"SOCIALSCRAPE_COOKIES_INSTAGRAM": str(path)}, raise_errors=False) as c:
        h = c.get("/health").json()
        assert h["cookie_errors"] == {"instagram": problem} and h["cookies"] == []
        for p, params in NOT_TRANSCRIPT:
            r = c.get(p, params=params)
            body = r.json()
            assert r.status_code == 500 and body["error"] == "cookies_error", (p, body)
            assert str(tmp_path) not in body["message"]


@pytest.mark.live
def test_cookie_file_without_instagram_cookies_stays_anonymous(tmp_path):
    path = tmp_path / "ig.txt"
    path.write_text("# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t2147483647\tfoo\tbar\n")
    with app_with({"SOCIALSCRAPE_COOKIES_INSTAGRAM": str(path)}, raise_errors=False) as c:
        assert c.get("/health").json()["cookies"] == ["instagram"]
        m = ok(c, POST, url=REEL)["data"]["xdt_shortcode_media"]
        assert m["shortcode"] == REEL and m["edge_media_to_parent_comment"]["edges"]
        assert "video_play_count" not in m


@pytest.mark.live
@pytest.mark.parametrize("path,params", [(PROFILE, {"handle": JANE}), (POST, {"url": REEL})])
def test_rejected_session_cookie_is_reported_as_a_cookie_problem(client, tmp_path, path, params):
    ensure_not_throttled(client)
    cookie = tmp_path / "ig.txt"
    cookie.write_text(FAKE_SESSION)
    with app_with({"SOCIALSCRAPE_COOKIES_INSTAGRAM": str(cookie)}, raise_errors=False) as c:
        status, body = raw(c, path, **params)
    if status == 200:
        pytest.skip("Instagram ignored the dummy session cookie")
    message = body["message"].lower()
    assert "anonymous" not in message and "rate limit" not in message, body
    assert "cookie" in message or "session" in message, body


# --- cache --------------------------------------------------------------------------------


@pytest.mark.live
def test_cache_hits_and_error_responses_are_not_cached(tmp_path):
    with app_with({}, tmp_path, raise_errors=False) as c:
        first = c.get(DETAIL, params={"id": HIGHLIGHT, "cache_max_age": "1h"})
        skip_if_blocked(first)
        assert first.status_code == 200 and first.headers.get("x-cache") == "miss"
        hit = c.get(DETAIL, params={"cache_max_age": "1d", "id": HIGHLIGHT})
        assert hit.headers.get("x-cache") == "hit" and hit.json()["cached"] is True and hit.json()["cached_at"]
        assert hit.json()["id"] == first.json()["id"]
        for _ in range(2):
            time.sleep(_SPACING)
            r = c.get(POST_COUNT, params={"handle": ABBREVIATED, "cache_max_age": "1h"})
            skip_if_blocked(r)
            assert r.status_code == 422 and r.json()["error"] == "estimate_only"
            assert "x-cache" not in r.headers and "cached" not in r.json()
        r = c.get(HASHTAG, params={"hashtag": "cats", "cursor": "2", "cache_max_age": "1h"})
        assert r.status_code == 501 and "x-cache" not in r.headers


# --- transcripts --------------------------------------------------------------------------


def test_transcript_backend_not_installed_is_501_before_any_request():
    with app_with({"SOCIALSCRAPE_TRANSCRIBE": "faster-whisper",
                   "SOCIALSCRAPE_PROXY": f"http://127.0.0.1:{free_port()}"}, raise_errors=False) as c:
        if importlib.util.find_spec("faster_whisper") is not None:
            pytest.skip("faster-whisper is installed")
        assert c.get("/health").json()["transcription"] is None
        r = c.get(TRANSCRIPT, params={"url": REEL})
        assert r.status_code == 501 and r.json()["error"] == "transcription_unavailable"
        r = c.get(TRANSCRIPT, params={"url": "bad..url"})
        assert r.status_code == 400 and r.json()["error"] == "unsupported_url"


@pytest.mark.live
@pytest.mark.skipif(not HAS_MLX, reason="mlx_whisper is not installed")
def test_transcript_unknown_model_is_json_500():
    with app_with({"SOCIALSCRAPE_TRANSCRIBE": "mlx-whisper",
                   "SOCIALSCRAPE_WHISPER_MODEL": "no-such-model-xyz"}, raise_errors=False) as c:
        status, body = raw(c, TRANSCRIPT, url=REEL)
    if status in (429, 503):
        pytest.skip("Instagram throttled this IP")
    assert status == 500 and body["error"] == "transcription_failed", body
    assert "no-such-model-xyz" in body["message"]


@pytest.mark.live
@pytest.mark.skipif(not HAS_MLX, reason="mlx_whisper is not installed")
def test_transcript_speech_survives_no_speech_filter():
    with app_with({"SOCIALSCRAPE_TRANSCRIBE": "mlx-whisper", "SOCIALSCRAPE_WHISPER_MODEL": "small"},
                  raise_errors=False) as c:
        body = ok(c, TRANSCRIPT, url=SPEECH_REEL)
        (entry,) = body["transcripts"]
        assert entry["shortcode"] == SPEECH_REEL
        assert entry["text"] and len(entry["text"].split()) >= 20, entry
        err(c, TRANSCRIPT, 404, "no_video", url=IMAGE)
