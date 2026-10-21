"""Gap tests for the YouTube video slice (/video, /transcript, /comments, /comment/replies, /sponsors,
/community-post): what tests/test_e2e_yt_videos.py doesn't cover.

Covers most_replayed and captionTracks against yt-dlp, collaborators, videos with comments turned off, more URL
forms, long transcripts, age, geo and live transcripts, the cache on every route, the proxy, timeout, cookie and auth
settings, repeated params and the OpenAPI params.

Anonymous only; only the tests marked live reach YouTube. The tests assert invariants, not volatile values, and skip
on throttling (429/503).
"""

from __future__ import annotations

import concurrent.futures as cf
import re
import socket
import unicodedata

import httpx2
import pytest

from tests.e2e import LEAKS, app_with, free_port, skip_if_blocked, ytdlp_info

RICK = "dQw4w9WgXcQ"  # most-replayed heatmap, uploaded en/de-DE/ja/pt-BR/es-419 + en ASR
ZOO = "jNQXAC9IVRw"  # uploaded en + de only
COLLAB = "brhfDfLdDZ8"  # Sauce+ x William Osman 2 collab upload
KIDS = "xBCVrp90wjY"  # CoComelon, made for kids: comments off
PINNED = "v9QtM6qnG50"  # MrBeast, pinned comment by the creator
LONG = "rfscVS0vtbw"  # 4h26m freeCodeCamp course, uploaded en
SHORT = "AwMy11R3au0"  # a Short: fast comments, a few replies
AGE_EMBED_CAPS = "HtVdAasjOgU"  # age-gated; only the web_embedded client plays it (its uploaded 'en' is a blank stub)
AGE_NO_CAPS = "Tq92D6wQ1mg"  # age-gated, no caption tracks
GEO = "sJL6WA-aGkQ"  # JP-only
MEMBERS = "xCvqBqurBlA"  # LTT members-only
PRIVATE = "yZIXLfi8CZQ"
LIVE = "M3HKLzjvKPc"  # NASA ISS 24/7 stream
UPCOMING = "pAKqJxuxDgk"  # NASA scheduled stream (Oct 23 2026)
LTT = "aV81SqB6HQg"  # LTT: '► SPONSORS, AFFILIATES, AND PARTNERS: https://lmg.gg/partners' boilerplate
LTT_AMD = "Ndzz-YWvtkg"  # LTT: 'Thanks to AMD for being a great partner and sponsoring this series! ... https://lmg.gg/..'
TEXT_POST = "Ugkx3w8sfDJf_NDdhUAtu7MTLX3d_Dt1V9UK"  # MrBeast text post
SHARED_POST = "UgkxLWl-HZtRwLZR0KlKXNQ7dOHJx5vhqgsd"  # @YouTubeViewers sharing a @youtubetv post

ROUTES = ["/v1/youtube/video", "/v1/youtube/video/transcript", "/v1/youtube/video/comments",
          "/v1/youtube/video/comment/replies", "/v1/youtube/video/sponsors", "/v1/youtube/community-post"]
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z")


_GETADDRINFO = socket.getaddrinfo


def ipv4_only(*args, **kwargs) -> list:
    return [ai for ai in _GETADDRINFO(*args, **kwargs) if ai[0] == socket.AF_INET]


def watch(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def post(pid: str) -> str:
    return f"https://www.youtube.com/post/{pid}"


def ok(client, path: str, **params) -> dict:
    r = client.get(path, params=params)
    skip_if_blocked(r)
    assert r.status_code == 200, (path, params, r.status_code, r.text[:300])
    body = r.json()
    assert body["success"] is True and body["credits_charged"] == 0
    return body


def err(client, path: str, status: int, code: str | None = None, **params) -> dict:
    r = client.get(path, params=params)
    skip_if_blocked(r)
    assert r.status_code == status, (path, params, r.status_code, r.text[:300])
    body = r.json()
    assert body["success"] is False
    if code:
        assert body["error"] == code, body
    assert not LEAKS.search(body.get("message") or ""), body
    return body


@pytest.fixture(scope="module")
def replies_token(client) -> str:
    body = ok(client, "/v1/youtube/video/comments", url=watch(SHORT))
    tokens = [c["repliesContinuationToken"] for c in body["comments"] if c.get("repliesContinuationToken")]
    if not tokens:
        pytest.skip("no comment with replies on the first page")
    return tokens[0]


def route_params(path: str, token: str) -> dict:
    if path.endswith("/comment/replies"):
        return {"continuationToken": token}
    if path.endswith("/community-post"):
        return {"url": post(TEXT_POST)}
    return {"url": watch(SHORT if path.endswith("/comments") else RICK)}


@pytest.fixture(scope="module")
def rick(client) -> dict:
    return ok(client, "/v1/youtube/video", url=watch(RICK))


@pytest.fixture(scope="module")
def rick_ytdlp() -> dict:
    return ytdlp_info(watch(RICK), ignore_no_formats_error=True)


# --- /v1/youtube/video data ----------------------------------------------------------------------------------------


@pytest.mark.live
def test_most_replayed_matches_ytdlp_heatmap(rick, rick_ytdlp):
    heatmap = rick_ytdlp.get("heatmap") or []
    if not heatmap:
        pytest.skip("YouTube served no heatmap this time")
    ours = rick["most_replayed"]
    assert len(ours["markers"]) == len(heatmap)
    for m, h in zip(ours["markers"], heatmap):
        assert m["startMillis"] == round(h["start_time"] * 1000)
        assert m["startMillis"] + m["durationMillis"] == round(h["end_time"] * 1000)
        assert m["intensityScoreNormalized"] == pytest.approx(h["value"])
    assert max(m["intensityScoreNormalized"] for m in ours["markers"]) == pytest.approx(1.0)
    for r in ours["ranges"]:
        assert r["label"] and r["startMillis"] <= r["peakMillis"] <= r["endMillis"]


@pytest.mark.live
def test_caption_tracks_match_ytdlp_languages(rick, rick_ytdlp):
    tracks = rick["captionTracks"]
    uploaded = {t["languageCode"] for t in tracks if t.get("kind") != "asr"}
    assert uploaded == set(rick_ytdlp.get("subtitles") or {})
    assert any(t.get("kind") == "asr" for t in tracks) == bool(rick_ytdlp.get("automatic_captions"))
    for t in tracks:
        assert t["baseUrl"].startswith("https://www.youtube.com/api/timedtext?") and t["name"]["simpleText"]
        assert t["vssId"].endswith(t["languageCode"])


def _fetch(url: str) -> bytes:
    r = httpx2.get(url, timeout=30, follow_redirects=True)
    skip_if_blocked(r)
    assert r.status_code == 200
    return r.content


@pytest.mark.live
def test_ytdlp_caption_url_is_fetchable_control(rick_ytdlp):
    """Control for the test below: the caption URL yt-dlp picks does serve the captions."""
    fmt = next(f for f in rick_ytdlp["subtitles"]["en"] if f["ext"] == "json3")
    assert b'"events"' in _fetch(fmt["url"])


@pytest.mark.live
def test_caption_track_base_url_is_fetchable(rick):
    """gap-youtube_videos-1: the tracks come from a player response whose URLs need no PO token (no exp=xpe)."""
    track = next(t for t in rick["captionTracks"] if t["languageCode"] == "en" and t.get("kind") != "asr")
    assert b'"events"' in _fetch(track["baseUrl"] + "&fmt=json3")
    assert all("exp=xpe" not in t["baseUrl"] and "exp=xpv" not in t["baseUrl"] for t in rick["captionTracks"])


@pytest.mark.live
def test_collaborators(client, rick):
    body = ok(client, "/v1/youtube/video", url=watch(COLLAB))
    collabs = body["collaborators"]
    assert {"sauceplusofficial", "WilliamOsman2"} <= {c["handle"] for c in collabs}
    assert body["channel"]["id"] in {c["id"] for c in collabs}
    for c in collabs:
        assert c["id"].startswith("UC") and c["url"] == f"https://www.youtube.com/@{c['handle']}" and c["title"]
    assert rick["collaborators"] == []


@pytest.mark.live
def test_video_channel_handle_with_combining_marks(client):
    """yt-dlp's uploader_id stops at a handle's first combining mark, so the video route cut such a handle short
    ('TVമലയാളം' -> 'TVമലയ') or, for most Thai, Bengali, Gujarati or Kannada ones, gave none. Gujarati handles have
    a vowel sign in nearly every word; a video of such a channel must name it in full, on the sponsors route too."""
    channels = ok(client, "/v1/youtube/search", query="ગુજરાતી", type="channels")["channels"]
    marked = [ch for ch in channels if any(unicodedata.category(c)[0] == "M" for c in ch["handle"] or "")]
    for ch in marked[:3]:
        videos = ok(client, "/v1/youtube/channel-videos", channelId=ch["id"])["videos"]
        if videos:
            break
    else:
        pytest.skip(f"no video from a channel with a combining mark in its handle: {[c['handle'] for c in channels]}")
    want = {"id": ch["id"], "url": f"https://www.youtube.com/@{ch['handle']}", "handle": ch["handle"]}
    channel = ok(client, "/v1/youtube/video", url=watch(videos[0]["id"]))["channel"]
    assert {k: channel[k] for k in want} == want, videos[0]["id"]
    channel = ok(client, "/v1/youtube/video/sponsors", url=watch(videos[0]["id"]))["video"]["channel"]
    assert {k: channel[k] for k in want} == want, videos[0]["id"]


@pytest.mark.live
def test_comments_off_video(client):
    body = ok(client, "/v1/youtube/video", url=watch(KIDS))
    assert body["commentCountText"] is None and body["commentCountInt"] is None
    assert body["likeCountInt"] is None or body["likeCountInt"] > 0
    comments = ok(client, "/v1/youtube/video/comments", url=watch(KIDS))
    assert comments["comments"] == [] and comments["continuationToken"] is None


@pytest.mark.live
@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={RICK}&list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI&index=1",
    f"https://www.youtube.com/watch?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI&v={RICK}",
    f"https://www.youtube.com/watch?app=desktop&v={RICK}",
])
def test_video_playlist_context_urls(client, url):
    assert ok(client, "/v1/youtube/video", url=url)["id"] == RICK


def test_clip_url_is_not_a_video_url(client):
    # Clip pages aren't listed as accepted input (by SC or the README); they're rejected cleanly
    err(client, "/v1/youtube/video", 400, "unsupported_url", url="https://www.youtube.com/clip/UgkxU2HSeGL_NvmDJ-nQJrlLwllwMDBdGZFs")


# --- /v1/youtube/video/comments ------------------------------------------------------------------------------------


@pytest.mark.live
def test_comments_url_forms(client):
    first = ok(client, "/v1/youtube/video/comments", url=watch(SHORT))["comments"][0]["id"]
    for url in (f"https://youtube.com/shorts/{SHORT}", f"https://youtu.be/{SHORT}", SHORT, f"m.youtube.com/watch?v={SHORT}"):
        assert ok(client, "/v1/youtube/video/comments", url=url)["comments"][0]["id"] == first, url


@pytest.mark.live
def test_comment_creator_flags(client):
    channel_id = ok(client, "/v1/youtube/video", url=watch(PINNED))["channel"]["id"]
    comments = ok(client, "/v1/youtube/video/comments", url=watch(PINNED))["comments"]
    creators = [c for c in comments if c["author"]["isCreator"]]
    if not creators:
        pytest.skip("the creator's pinned comment isn't on the first page any more")
    for c in creators:
        assert c["author"]["channelId"] == channel_id and c["author"]["isVerified"] is True
    assert all(c["author"]["channelUrl"].startswith("https://www.youtube.com/") for c in comments)


# --- /v1/youtube/video/transcript ----------------------------------------------------------------------------------


def _time_text(ms: str) -> str:
    s = int(ms) // 1000
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


@pytest.mark.live
def test_long_transcript_time_text(client):
    body = ok(client, "/v1/youtube/video/transcript", url=watch(LONG))
    segs = body["transcript"]
    assert body["language"].startswith("English") and len(segs) > 1000
    assert all(s["startTimeText"] == _time_text(s["startMs"]) for s in segs)
    assert int(segs[-1]["startMs"]) > 4 * 3600 * 1000 and segs[-1]["startTimeText"].count(":") == 2
    assert all(int(a["endMs"]) <= int(b["startMs"]) for a, b in zip(segs, segs[1:]))
    assert body["transcript_only_text"] == " ".join(s["text"] for s in segs)


@pytest.mark.live
@pytest.mark.parametrize("language", ["en-US", "en-GB", "EN"])
def test_transcript_region_tag_on_uploaded_en(client, language):
    default = ok(client, "/v1/youtube/video/transcript", url=watch(ZOO))
    body = ok(client, "/v1/youtube/video/transcript", url=watch(ZOO), language=language)
    assert body["language"] == default["language"] == "English"
    assert body["transcript"] == default["transcript"]


@pytest.mark.live
def test_age_restricted_without_captions_is_403(client):
    err(client, "/v1/youtube/video", 403, "age_restricted", url=watch(AGE_NO_CAPS))
    err(client, "/v1/youtube/video/transcript", 403, "age_restricted", url=watch(AGE_NO_CAPS))


@pytest.mark.live
def test_age_restricted_transcript_is_403_or_real(client):
    """gap-youtube_videos-2: only the embedded player plays this age-gated video, and its uploaded 'en' track is a
    blank stub; the transcript falls back to the English auto-generated track instead of null."""
    err(client, "/v1/youtube/video", 403, "age_restricted", url=watch(AGE_EMBED_CAPS))
    r = client.get("/v1/youtube/video/transcript", params={"url": watch(AGE_EMBED_CAPS)})
    skip_if_blocked(r)
    assert r.status_code == 403 or r.json()["transcript"], r.text[:300]


@pytest.mark.live
@pytest.mark.parametrize(("vid", "code"), [(GEO, "geo_restricted"), (MEMBERS, "forbidden"), (PRIVATE, "forbidden")])
def test_unplayable_transcript_is_403(client, vid, code):
    err(client, "/v1/youtube/video/transcript", 403, code, url=watch(vid))


@pytest.mark.live
@pytest.mark.parametrize("params", [{}, {"original_audio": "true"}, {"language": "en"}])
def test_live_transcript_is_null(client, params):
    body = ok(client, "/v1/youtube/video/transcript", url=watch(LIVE), **params)
    assert body["transcript"] is None and body["transcript_only_text"] is None and body["language"] is None


@pytest.mark.live
def test_live_url_form_transcript_is_null(client):
    body = ok(client, "/v1/youtube/video/transcript", url=f"https://www.youtube.com/live/{LIVE}")
    assert body["videoId"] == LIVE and body["transcript"] is None


@pytest.mark.live
def test_upcoming_transcript_is_null(client):
    video = ok(client, "/v1/youtube/video", url=watch(UPCOMING))
    if not (video.get("publishDateText") or "").startswith("Scheduled"):
        pytest.skip("the stream isn't upcoming any more")
    assert ok(client, "/v1/youtube/video/transcript", url=watch(UPCOMING))["transcript"] is None


# --- /v1/youtube/video/sponsors ------------------------------------------------------------------------------------


@pytest.mark.live
def test_sponsors_skip_the_channels_own_partner_page(client):
    """gap-youtube_videos-3: LTT's boilerplate link to its own partner page (https://lmg.gg/partners) isn't a sponsor."""
    body = ok(client, "/v1/youtube/video/sponsors", url=watch(LTT))
    assert "lmg.gg" not in {s.get("website") for s in body["suspectedSponsors"]}
    sponsors = ok(client, "/v1/youtube/video/sponsors", url=watch(LTT_AMD))["suspectedSponsors"]
    assert "AMD" in {s["name"] for s in sponsors} and "lmg.gg" not in {s.get("website") for s in sponsors}


@pytest.mark.live
def test_sponsors_url_forms(client):
    for url in (f"https://youtu.be/{SHORT}", SHORT):
        assert ok(client, "/v1/youtube/video/sponsors", url=url)["video"]["id"] == SHORT


# --- /v1/youtube/community-post ------------------------------------------------------------------------------------


@pytest.mark.live
def test_shared_post_returns_the_share(client):
    body = ok(client, "/v1/youtube/community-post", url=post(SHARED_POST))
    assert body["id"] == SHARED_POST and body["channel"]["handle"] == "YouTubeViewers"
    assert body["content"] and STAMP.fullmatch(body["publishedTime"])
    assert body["publishedTime"].startswith("2026-09-09T22:41:25")  # exact (microformat), not estimated
    orig = body["originalPost"]
    assert orig["id"] != SHARED_POST and orig["channel"]["handle"] == "youtubetv"
    assert "https://goo.gle/" in orig["content"]  # link runs keep their full URL text
    assert orig["video"]["id"] and orig["video"]["url"].endswith(orig["video"]["id"])


# --- cache ---------------------------------------------------------------------------------------------------------


@pytest.mark.live
def test_cache_on_replies_sponsors_and_posts(tmp_path, client, replies_token):
    with app_with({}, tmp_path) as c:
        for path, params in [("/v1/youtube/video/comment/replies", {"continuationToken": replies_token}),
                             ("/v1/youtube/video/sponsors", {"url": watch(SHORT)}),
                             ("/v1/youtube/community-post", {"url": post(TEXT_POST)})]:
            first = c.get(path, params={**params, "cache_max_age": "1d"})
            skip_if_blocked(first)
            assert first.status_code == 200 and first.headers["x-cache"] == "miss", (path, first.text[:200])
            again = c.get(path, params={**params, "cache_max_age": "1d"})
            assert again.headers["x-cache"] == "hit", path
            hit = again.json()
            assert hit.pop("cached") is True and STAMP.fullmatch(hit.pop("cached_at"))
            assert hit == first.json() and "cached" not in first.json()


@pytest.mark.live
def test_cache_hit_still_needs_an_api_key(tmp_path):
    with app_with({"SOCIALSCRAPE_API_KEYS": "k1"}, tmp_path) as c:
        params = {"url": watch(SHORT), "cache_max_age": "1d"}
        first = c.get("/v1/youtube/video/comments", params=params, headers={"x-api-key": "k1"})
        skip_if_blocked(first)
        assert first.status_code == 200
        assert c.get("/v1/youtube/video/comments", params=params).status_code == 401
        hit = c.get("/v1/youtube/video/comments", params=params, headers={"x-api-key": "k1"})
        assert hit.status_code == 200 and hit.headers["x-cache"] == "hit"


# --- settings: proxy, timeouts, cookies, auth (all six routes) -------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize(("env", "status", "code"), [
    ({"SOCIALSCRAPE_PROXY": "http://127.0.0.1:{port}"}, 502, "proxy_error"),
    ({"SOCIALSCRAPE_SOCKET_TIMEOUT": "0.001"}, 500, "timeout"),
    ({"SOCIALSCRAPE_COOKIES_YOUTUBE": "{tmp}/missing.txt"}, 500, "cookies_error"),
    ({"SOCIALSCRAPE_COOKIES_YOUTUBE": "{tmp}/bad.txt"}, 500, "cookies_error"),
], ids=["dead_proxy", "socket_timeout", "missing_cookies", "bad_cookies"])
def test_setting_errors_on_every_route(tmp_path, replies_token, env, status, code):
    (tmp_path / "bad.txt").write_bytes(b"garbage, not a cookies.txt\n\x00\x01")
    env = {k: v.format(tmp=tmp_path, port=free_port()) for k, v in env.items()}
    # IPv4 only: on a host that resolves YouTube's IPv6 addresses but has no route to them (a VPN's tunnels can do
    # that), yt-dlp's connect loop raises the last address's error, an instant 'No route to host' (502), after
    # every IPv4 attempt timed out
    with app_with(env, patches=[(socket, "getaddrinfo", ipv4_only)]) as c:
        for path in ROUTES:
            err(c, path, status, code, **route_params(path, replies_token))
        if code == "cookies_error":
            assert "youtube" in c.get("/health").json()["cookie_errors"]


@pytest.mark.live
def test_request_timeout_setting():
    with app_with({"SOCIALSCRAPE_REQUEST_TIMEOUT": "0.05"}) as c:
        err(c, "/v1/youtube/video", 500, "timeout", url=watch(RICK))


@pytest.mark.live
def test_dummy_cookie_file_still_works(tmp_path, replies_token):
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tDUMMY_TEST\tabc123\n")
    before = jar.read_bytes()
    with app_with({"SOCIALSCRAPE_COOKIES_YOUTUBE": str(jar)}) as c:
        health = c.get("/health").json()
        assert health["cookies"] == ["youtube"] and health["cookie_errors"] == {}
        for path in ROUTES:
            ok(c, path, **route_params(path, replies_token))
    assert jar.read_bytes() == before  # read-only


@pytest.mark.live
def test_auth_on_every_route(replies_token):
    with app_with({"SOCIALSCRAPE_API_KEYS": "k1,k2"}) as c:
        for path in ROUTES:
            params = route_params(path, replies_token)
            for headers in ({}, {"x-api-key": "nope"}, {"x-api-key": "K1"}):
                r = c.get(path, params=params, headers=headers)
                assert r.status_code == 401 and r.json()["error"] == "unauthorized", (path, headers)
            assert c.get(path, params={"api_key": "k1", **params}).status_code == 401
        r = c.get("/v1/youtube/video/comments", params={"url": watch(SHORT)}, headers={"X-API-KEY": "k2"})
        skip_if_blocked(r)
        assert r.status_code == 200
        assert c.get("/v1/youtube/video").status_code == 401  # auth before validation


# --- repeated params, OpenAPI --------------------------------------------------------------------------------------


@pytest.mark.live
def test_repeated_params_last_wins(client):
    r = client.get("/v1/youtube/video", params=[("url", watch(ZOO)), ("url", watch(SHORT))])
    skip_if_blocked(r)
    assert r.status_code == 200 and r.json()["id"] == SHORT
    bad = client.get("/v1/youtube/video/comments", params=[("url", SHORT), ("order", "top"), ("order", "bogus")])
    assert bad.status_code == 400 and bad.json()["error"] == "bad_request"
    bad = client.get("/v1/youtube/video", params=[("url", ZOO), ("cache_max_age", "1d"), ("cache_max_age", "bogus")])
    assert bad.status_code == 400 and "cache_max_age" in bad.json()["message"]
    bad = client.get("/v1/youtube/video/transcript",
                     params=[("url", ZOO), ("original_audio", "true"), ("original_audio", "maybe")])
    assert bad.status_code == 400


def test_openapi_params_match_scrapecreators(client):
    expected = {  # ScrapeCreators' documented params, plus our cache_max_age on every route
        "/v1/youtube/video": {"url", "language"},
        "/v1/youtube/video/transcript": {"url", "language", "original_audio"},
        "/v1/youtube/video/comments": {"url", "continuationToken", "order"},
        "/v1/youtube/video/comment/replies": {"continuationToken"},
        "/v1/youtube/video/sponsors": {"url", "language"},
        "/v1/youtube/community-post": {"url"},
    }
    spec = client.get("/openapi.json").json()
    for path, names in expected.items():
        params = {p["name"]: p for p in spec["paths"][path]["get"]["parameters"]}
        assert set(params) == names | {"cache_max_age"}, path
        required = {n for n, p in params.items() if p.get("required")}
        assert required == ({"continuationToken"} if path.endswith("/replies") else {"url"}), path


@pytest.mark.live
def test_parallel_routes_in_one_app(client, replies_token):
    with cf.ThreadPoolExecutor(3) as ex:
        results = list(ex.map(lambda p: client.get(p, params=route_params(p, replies_token)), ROUTES[2:5]))
    for r in results:
        skip_if_blocked(r)
        assert r.status_code == 200 and r.json()["success"] is True
