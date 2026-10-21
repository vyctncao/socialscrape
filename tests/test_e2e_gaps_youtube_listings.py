"""Gap tests for the YouTube listing slice (/channel, /channel-videos, /channel/shorts, /channel/lives,
/channel/playlists, /channel/community-posts, /playlist, /search, /search/hashtag and the two 501 routes): what
tests/test_e2e_yt_channel.py and tests/test_e2e_yt_search.py don't cover.

Covers the proxy, timeout, cookie and auth settings on every listing route (first page and cursor branch), upstream
HTTP statuses (simulated), the hashtag canonical form, sort=popular without chips, search's is_paid_promotions,
search's lives bucket, community posts of every kind (polls, quizzes, shares), fields against yt-dlp, conflicting
params, a wider region sample and hashtag paging past the cursor's seen-id window.

Anonymous only; only the tests marked live reach YouTube. The tests assert invariants, not volatile values, and skip
on throttling (429/503).
"""

from __future__ import annotations

import hashlib
import io
import unicodedata
from urllib.parse import quote, unquote

import pytest
import yt_dlp

from tests.e2e import LEAKS, app_with, free_port, skip_if_blocked, ytdlp_info

MRBEAST = "UCX6OQ3DkcsbYNE6H8uQQuVA"
NASA = "UCLA_DiR1FfKNvjuUpBHmylQ"
PLAYLIST = "PLsyOSbh5bs16vubvKePAQ1x3PhKavfBIl"  # ~1700 videos, 100 per page
SHARER = "YouTubeViewers"  # its Posts tab shows a share (sharedPostRenderer) of a @youtubetv post
POLLS = "TEDEd"  # Posts tab with poll, quiz, playlist, image and video posts
CHIPLESS = "jawed"  # one video, so YouTube shows no Latest/Popular chips

YT = "/v1/youtube"
FIRST_PAGES = [
    (f"{YT}/channel", {"handle": "MrBeast"}),
    (f"{YT}/channel-videos", {"handle": "MrBeast"}),
    (f"{YT}/channel/shorts", {"handle": "MrBeast"}),
    (f"{YT}/channel/lives", {"channelId": NASA}),
    (f"{YT}/channel/playlists", {"handle": "NASA"}),
    (f"{YT}/channel/community-posts", {"handle": "MrBeast"}),
    (f"{YT}/playlist", {"playlist_id": PLAYLIST}),
    (f"{YT}/search", {"query": "lofi"}),
    (f"{YT}/search", {"query": "lofi", "type": "shorts"}),
    (f"{YT}/search/hashtag", {"hashtag": "funny"}),
]


def _cursor(**state) -> str:
    from socialscrape.cursor import encode_cursor

    return encode_cursor(state)


def _state(token: str) -> dict:
    from socialscrape.cursor import decode_cursor

    return decode_cursor(token)


# Well-formed cursors for each listing's continuation branch. Their native tokens are dummies: these only reach
# code paths that fail before YouTube would look at them (dead proxy, cookies, simulated HTTP statuses).
CURSORS = [
    (f"{YT}/channel-videos", {"continuationToken": _cursor(k="channel-videos", t="4qmFsgI", c=[MRBEAST, "MrBeast",
                                                                                                  "MrBeast"],
                                                           x="mweb")}),
    (f"{YT}/channel/shorts", {"continuationToken": _cursor(k="channel-shorts", t="4qmFsgI")}),
    (f"{YT}/channel/lives", {"continuationToken": _cursor(k="channel-lives", t="4qmFsgI")}),
    (f"{YT}/channel/playlists", {"continuationToken": _cursor(k="channel-playlists", t="4qmFsgI")}),
    (f"{YT}/channel/community-posts", {"continuationToken": _cursor(k="channel-posts", t="4qmFsgI")}),
    (f"{YT}/playlist", {"playlist_id": PLAYLIST, "continuationToken": _cursor(k="playlist", t="4qmFsgI", p=PLAYLIST)}),
    (f"{YT}/search", {"query": "lofi", "continuationToken": _cursor(k="search", t="EpoDEgRsb2Zp")}),
    (f"{YT}/search", {"query": "lofi", "continuationToken": _cursor(k="search", t="EpoDEgRsb2Zp", y="shorts",
                                                                    x="mweb")}),
    (f"{YT}/search/hashtag", {"hashtag": "funny", "continuationToken": _cursor(k="hashtag", t="4qmFsgI",
                                                                               p="funny")}),
]
CURSOR_IDS = [f"{p.split('/v1/youtube/')[1]}:{_state(q['continuationToken']).get('x') or 'web'}" for p, q in CURSORS]


def ok(client, path: str, **params) -> dict:
    r = client.get(path, params=params)
    skip_if_blocked(r)
    assert r.status_code == 200, (path, params, r.status_code, r.text[:300])
    body = r.json()
    assert body["success"] is True and body["credits_charged"] == 0
    return body


def err(client, path: str, status: int, code: str | None = None, *, skip_blocked: bool = True, **params) -> dict:
    r = client.get(path, params=params)
    if skip_blocked:
        skip_if_blocked(r)
    assert r.status_code == status, (path, params, r.status_code, r.text[:300])
    body = r.json()
    assert body["success"] is False
    if code:
        assert body["error"] == code, body
    assert not LEAKS.search(body.get("message") or ""), body
    return body


def _raw_tab(handle: str, tab: str) -> list[tuple[str, dict]]:
    """(renderer key, renderer) items of a channel tab's first page, read straight from the webpage."""
    from socialscrape import ytdlp
    from socialscrape.platforms import _youtube_listings_parse as p
    from socialscrape.platforms import youtube_listings as yl

    def go(ydl):
        ie = ydl.get_info_extractor("YoutubeTab")
        ie.initialize()
        return yl._page(ie, f"https://www.youtube.com/@{handle}/{tab}", f"@{handle}")[0]

    try:
        data = ytdlp._run("youtube", go, {})
    except yt_dlp.utils.YoutubeDLError as exc:
        pytest.skip(f"raw page not reachable: {exc}")
    return p.walk(p.tab_content(data, tab))[0]


def _thread_ids(items: list[tuple[str, dict]]) -> tuple[set[str], set[str]]:
    """(ids of ordinary posts, ids of shares) among a Posts tab's backstagePostThreadRenderer items."""
    plain, shared = set(), set()
    for key, node in items:
        if key != "backstagePostThreadRenderer":
            continue
        post = node.get("post") or {}
        if pid := (post.get("backstagePostRenderer") or {}).get("postId"):
            plain.add(pid)
        if pid := (post.get("sharedPostRenderer") or {}).get("postId"):
            shared.add(pid)
    return plain, shared


# --- settings: proxy, timeout, cookies, auth -------------------------------------------------------------------------

def test_dead_proxy_is_proxy_error_on_every_listing_route():
    """First pages, every cursor branch and an unsupported region: all 502 proxy_error, never bad_cursor,
    unsupported_region or a 500."""
    port = free_port()
    with app_with({"SOCIALSCRAPE_PROXY": f"http://127.0.0.1:{port}"}) as c:
        for path, params in [*FIRST_PAGES, *CURSORS, (f"{YT}/search", {"query": "lofi", "region": "CN"}),
                             (f"{YT}/search", {"query": "lofi", "region": "KP", "type": "shorts"})]:
            body = err(c, path, 502, "proxy_error", skip_blocked=False, **params)
            assert f"127.0.0.1:{port}" not in body["message"]


@pytest.mark.live
def test_request_timeout_is_500_timeout():
    with app_with({"SOCIALSCRAPE_REQUEST_TIMEOUT": "0.01"}) as c:
        for path, params in [FIRST_PAGES[1], FIRST_PAGES[7], CURSORS[8]]:
            err(c, path, 500, "timeout", skip_blocked=False, **params)


@pytest.mark.parametrize("kind", ["missing", "directory", "garbage"])
def test_cookie_file_problems_on_every_listing_route(tmp_path, kind):
    jar = {"missing": tmp_path / "nope.txt", "directory": tmp_path, "garbage": tmp_path / "bad.txt"}[kind]
    (tmp_path / "bad.txt").write_bytes(b"this is not a cookie file\n\x00\x01garbage\n")
    with app_with({"SOCIALSCRAPE_COOKIES_YOUTUBE": str(jar)}) as c:
        health = c.get("/health").json()
        assert "youtube" in health["cookie_errors"] and "youtube" not in health["cookies"]
        for path, params in [*FIRST_PAGES, *CURSORS]:
            body = err(c, path, 500, "cookies_error", skip_blocked=False, **params)
            assert "SOCIALSCRAPE_COOKIES_YOUTUBE" in body["message"]


@pytest.mark.live
def test_dummy_cookie_file_keeps_listings_working_and_untouched(tmp_path):
    """A harmless cookies.txt (a PREF asking for French, a cookie for another site) changes nothing: English
    text is still parsed and the file is never written back."""
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n"
                   ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tPREF\thl=fr&gl=FR&f6=40000000\n"
                   ".example.com\tTRUE\t/\tFALSE\t2147483647\tdummy\t1\n")
    before = hashlib.sha256(jar.read_bytes()).hexdigest()
    with app_with({"SOCIALSCRAPE_COOKIES_YOUTUBE": str(jar)}) as c:
        assert c.get("/health").json()["cookies"] == ["youtube"]
        ch = ok(c, f"{YT}/channel", channelId=MRBEAST)
        assert ch["subscriberCountText"].endswith("subscribers") and ch["subscriberCount"] > 100_000_000
        page = ok(c, f"{YT}/channel-videos", channelId=MRBEAST)
        texts = [v["viewCountText"] or "" for v in page["videos"]]
        assert any(t.endswith(" views") for t in texts) and not any("vues" in t for t in texts), texts
        more = ok(c, f"{YT}/channel-videos", continuationToken=page["continuationToken"])
        assert more["videos"] and not {v["id"] for v in more["videos"]} & {v["id"] for v in page["videos"]}
    assert hashlib.sha256(jar.read_bytes()).hexdigest() == before


@pytest.mark.live
def test_auth_on_listing_cursor_and_501_routes(tmp_path):
    with app_with({"SOCIALSCRAPE_API_KEYS": "k1, k2"}, tmp_path) as c:
        for path, params in [*FIRST_PAGES, *CURSORS, (f"{YT}/shorts/trending", {}),
                             (f"{YT}/search/typeahead", {"query": "x"}),
                             (f"{YT}/shorts/trending", {"cache_max_age": "abc"}),
                             (f"{YT}/channel-videos", {"sort": "bogus"})]:
            for headers in ({}, {"x-api-key": "bad"}):
                r = c.get(path, params=params, headers=headers)
                assert r.status_code == 401 and r.json()["error"] == "unauthorized", (path, params, r.text[:200])
        for path in (f"{YT}/shorts/trending", f"{YT}/search/typeahead"):
            r = c.get(path, params={"query": "x"}, headers={"x-api-key": "k2"})
            assert r.status_code == 501 and r.json()["error"] == "not_supported"
        r = c.get(f"{YT}/channel", params={"channelId": NASA, "cache_max_age": "1d"}, headers={"x-api-key": "k1"})
        skip_if_blocked(r)
        assert r.status_code == 200
        hit = c.get(f"{YT}/channel", params={"channelId": NASA, "cache_max_age": "1d"}, headers={"X-API-KEY": "k2"})
        assert hit.status_code == 200 and hit.json()["cached"] is True
        leak = c.get(f"{YT}/channel", params={"channelId": NASA, "cache_max_age": "1d"})
        assert leak.status_code == 401 and "cached" not in leak.json()
        assert c.get("/meta/coverage").status_code == 200 and c.get("/health").json()["auth"] is True


def test_501_routes_with_cache_max_age(client, tmp_path):
    """A bad cache_max_age is a 400 on the 501 routes too, with the cache off (the conftest default) or on."""
    for path in (f"{YT}/shorts/trending", f"{YT}/search/typeahead"):
        err(client, path, 400, "bad_request", skip_blocked=False, cache_max_age="abc")
        assert client.get(path, params={"cache_max_age": "7d"}).json()["error"] == "not_supported"
    with app_with({}, tmp_path) as c:
        for path in (f"{YT}/shorts/trending", f"{YT}/search/typeahead"):
            assert c.get(path, params={"cache_max_age": "7d"}).status_code == 501
            err(c, path, 400, "bad_request", skip_blocked=False, cache_max_age="abc")


# --- upstream HTTP statuses (simulated: no request leaves the machine) ------------------------------------------------

@pytest.fixture
def upstream_status(monkeypatch):
    """Make every yt-dlp request answer with the HTTP status put in the returned list."""
    from yt_dlp.networking.common import Response
    from yt_dlp.networking.exceptions import HTTPError

    status = [429]

    def urlopen(self, req):
        url = req if isinstance(req, str) else req.url
        raise HTTPError(Response(io.BytesIO(b"{}"), url, {}, status=status[0], reason="simulated"))

    monkeypatch.setattr(yt_dlp.YoutubeDL, "urlopen", urlopen)
    return status


@pytest.mark.parametrize("status,code,http", [(429, "upstream_blocked", 503), (403, "forbidden", 403),
                                              (401, "forbidden", 403), (404, "not_found", 404)])
def test_upstream_status_mapping(client, upstream_status, status, code, http):
    upstream_status[0] = status
    for path, params in [*FIRST_PAGES, *CURSORS]:
        err(client, path, http, code, skip_blocked=False, **params)


@pytest.mark.parametrize("path,params", CURSORS, ids=CURSOR_IDS)
def test_upstream_400_on_a_cursor_is_bad_cursor(client, upstream_status, path, params):
    upstream_status[0] = 400
    err(client, path, 400, "bad_cursor", skip_blocked=False, **params)


def test_upstream_400_on_a_first_page_is_not_a_success(client, upstream_status):
    upstream_status[0] = 400
    for path, params in FIRST_PAGES:
        r = client.get(path, params=params)
        assert r.status_code >= 400 and r.json()["success"] is False
        assert r.json()["error"] != "bad_cursor"


def test_upstream_5xx_is_upstream_error(client, upstream_status):
    for status in (500, 503):
        upstream_status[0] = status
        for path, params in [FIRST_PAGES[1], FIRST_PAGES[7], CURSORS[0], CURSORS[6]]:
            err(client, path, 500, "upstream_error", skip_blocked=False, **params)


# --- hashtag canonical form ---------------------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.parametrize("given,canonical", [
    ("κόσμος", "κόσμοσ"),  # final sigma
    ("ﬁre", "fire"),  # NFKC ligature
    ("İstanbul", "istanbul"),  # dotted capital I -> plain i
    (unicodedata.normalize("NFD", "türkiye"), "türkiye"),  # decomposed -> composed
    ("KADIKÖY", "kadiköy"),  # capital I is i, not the dotless ı
    ("#Cooking", "cooking"),
    ("https://www.youtube.com/hashtag/Cooking", "cooking"),
    ("coo‍king", "cooking"),  # ZWJ dropped
    ("ทำอาหาร", "ทำอาหาร"),  # Thai SARA AM stays (NFKC splits it into NIKHAHIT + SARA AA)
    ("ㅋㅋㅋ", "ㅋㅋㅋ"),  # Hangul compatibility jamo stay (NFKC makes them conjoining jamo)
    ("＃ゲーム", "ゲーム"),  # the full-width '#' of Japanese and Chinese keyboards
])
def test_hashtag_canonical_form(client, given, canonical):
    body = ok(client, f"{YT}/search/hashtag", hashtag=given)
    assert body["videos"], given
    assert _state(body["continuationToken"])["p"] == canonical


@pytest.mark.live
@pytest.mark.parametrize("given", ["ทำอาหาร", "ㅋㅋㅋ", "ﾻﾻ", "CO₂", "Ⅳ", "a½", "ລຳວົງ", "Երևան", "＃ゲーム"])
def test_hashtag_canonical_form_is_youtube_pages(given):
    """YouTube's own /hashtag/<given> page holds the feed params of our canonical tag, both where YouTube keeps
    what NFKC would rewrite (SARA AM, compatibility jamo, subscripts, Roman numerals, fractions) and where it
    rewrites as NFKC does (the Lao AM, Armenian 'և', the full-width '＃' it then drops)."""
    from socialscrape.platforms import _youtube_listings_parse as p
    from socialscrape.platforms.youtube_listings import hashtag_of

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        webpage = ydl.get_info_extractor("YoutubeTab")._download_webpage(
            f"https://www.youtube.com/hashtag/{quote(given)}", given)
    tag = hashtag_of(given)[0]
    assert p.hashtag_params(tag, False) in unquote(webpage), (given, tag)  # the page has '=' as '%3D'


def test_pre_binding_hashtag_cursor_is_bad_cursor(client):
    """Hashtag cursors minted before they carried their tag can't be checked: 400, no upstream call."""
    err(client, f"{YT}/search/hashtag", 400, "bad_cursor", hashtag="funny",
        continuationToken=_cursor(k="hashtag", t="4qmFsgI", x="mweb"))


@pytest.mark.live
def test_hashtag_paging_past_the_seen_window_repeats_only_outside_it(client):
    """The cursor remembers the last _Seen.keep ids served: walking past that, an id the feed re-sends is served
    again only once it has dropped out of the window (whether the feed re-sends one is up to YouTube)."""
    from socialscrape.platforms.youtube_listings import _Seen

    ids, token = [], None
    for _ in range(16):
        params = {"hashtag": "funny", **({"continuationToken": token} if token else {})}
        body = ok(client, f"{YT}/search/hashtag", **params)
        ids += [v["id"] for v in body["videos"]]
        token = body["continuationToken"]
        if not token or len(ids) > _Seen.keep + 40:
            break
    last: dict[str, int] = {}
    for at, vid in enumerate(ids):
        assert vid not in last or at - last[vid] > _Seen.keep, (vid, last[vid], at)
        last[vid] = at
    if len(ids) <= _Seen.keep:
        pytest.skip(f"the feed ended after {len(ids)} items, inside the seen window")


# --- channel tabs -------------------------------------------------------------------------------------------------

@pytest.mark.live
def test_sort_popular_without_chips_falls_back_to_latest(client):
    latest = ok(client, f"{YT}/channel-videos", handle=CHIPLESS)
    popular = ok(client, f"{YT}/channel-videos", handle=CHIPLESS, sort="popular")
    assert latest["videos"] and [v["id"] for v in popular["videos"]] == [v["id"] for v in latest["videos"]]
    assert popular["continuationToken"] is None
    for sort in ("newest", "popular"):
        assert ok(client, f"{YT}/channel/shorts", handle=CHIPLESS, sort=sort)["shorts"] == []


@pytest.mark.live
def test_community_posts_keep_polls_quizzes_and_attachments(client):
    """Every ordinary post on the first page of the Posts tab (poll, quiz, playlist, image, video) is listed."""
    for handle in (POLLS, SHARER):
        plain, _ = _thread_ids(_raw_tab(handle, "posts"))
        assert plain, handle
        got = {x["id"] for x in ok(client, f"{YT}/channel/community-posts", handle=handle)["posts"]}
        assert plain <= got, (handle, plain - got)


@pytest.mark.live
def test_community_posts_keep_shared_posts(client):
    """gap-youtube_listings-2 (fixed): shares were dropped. Now each is listed by the sharing channel, with the
    post it shares as originalPost."""
    _, shared = _thread_ids(_raw_tab(SHARER, "posts"))
    if not shared:
        pytest.skip(f"@{SHARER}'s first Posts page no longer shows a share")
    posts = {x["id"]: x for x in ok(client, f"{YT}/channel/community-posts", handle=SHARER)["posts"]}
    assert shared <= set(posts), shared - set(posts)
    for pid in shared:
        share = posts[pid]
        assert share["url"].endswith(f"/post/{pid}") and share["channel"]["handle"] == SHARER, share
        assert share["publishedTime"] and share["content"], share
        original = share.get("originalPost")
        if original:
            assert original["id"] != pid and original["channel"]["id"], original


@pytest.mark.live
def test_tab_fields_match_ytdlp(client):
    """Titles and lengths of the Videos tab, a playlist page and the Live tab equal yt-dlp's flat extraction."""
    cases = [(f"{YT}/channel-videos", {"channelId": MRBEAST}, "videos", f"https://www.youtube.com/channel/{MRBEAST}/videos", 30),
             (f"{YT}/playlist", {"playlist_id": PLAYLIST}, "videos", f"https://www.youtube.com/playlist?list={PLAYLIST}", 100),
             (f"{YT}/channel/lives", {"channelId": NASA}, "lives", f"https://www.youtube.com/channel/{NASA}/streams", 30)]
    for path, params, key, url, n in cases:
        ours = ok(client, path, **params)[key]
        ref = {e["id"]: e for e in ytdlp_info(url, extract_flat="in_playlist", playlistend=n)["entries"]}
        common = [v for v in ours if v["id"] in ref]
        assert len(common) >= 0.8 * min(len(ours), len(ref)), (path, len(common), len(ours), len(ref))
        for v in common:
            if ref[v["id"]].get("title"):
                assert v["title"] == ref[v["id"]]["title"], (path, v["id"])
            if key == "videos":
                assert v["lengthSeconds"] == ref[v["id"]].get("duration"), (path, v["id"])


# --- search -------------------------------------------------------------------------------------------------------

@pytest.mark.live
def test_search_paid_promotions_is_not_silently_ignored(client):
    """gap-youtube_listings-1 (fixed): ScrapeCreators' search supports is_paid_promotions=true and channel-videos
    answers it with 501, but search answered 200 with ordinary results, as if the flag weren't there."""
    err(client, f"{YT}/search", 501, "not_supported", query="iphone review", is_paid_promotions="true")
    page = ok(client, f"{YT}/search", query="iphone review")
    if page["continuationToken"]:
        err(client, f"{YT}/search", 501, "not_supported", query="iphone review", is_paid_promotions="true",
            continuationToken=page["continuationToken"])


@pytest.mark.live
def test_search_lives_bucket(client):
    for query in ("live news", "live now"):
        body = ok(client, f"{YT}/search", query=query)
        if body["lives"]:
            break
    else:
        pytest.skip("no live-now results right now")
    other = {v["id"] for v in body["videos"]} | {v["id"] for v in body["shorts"]}
    for v in body["lives"]:
        assert v["type"] == "live" and v["id"] not in other
        assert v["viewCountInt"] is None and v["lengthSeconds"] is None
        assert "watching" in (v["viewCountText"] or "") or "LIVE" in (v.get("badges") or [])


@pytest.mark.live
def test_search_results_agree_with_detail_routes(client):
    channels = ok(client, f"{YT}/search", query="nasa", type="channels")["channels"]
    assert channels
    for ch in channels[:2]:
        about = ok(client, f"{YT}/channel", channelId=ch["id"])
        assert about["channelId"] == ch["id"] and about["subscriberCount"] == ch["subscriberCount"]
        if ch["handle"]:
            assert about["channel"].lower().endswith("/@" + ch["handle"].lower())
    playlists = ok(client, f"{YT}/search", query="lofi", type="playlists")["playlists"]
    assert playlists
    for pl in [x for x in playlists if x["videoCount"]][:2]:
        # two YouTube counts, which can drift apart on a playlist with unavailable videos: search's badge said 10
        # where the playlist page said "11 videos" and "4 unavailable videos are hidden"
        total = ok(client, f"{YT}/playlist", playlist_id=pl["id"])["totalVideos"]
        assert total == pytest.approx(pl["videoCount"], rel=0.1, abs=1), (pl["id"], total, pl["videoCount"])


@pytest.mark.parametrize("region,status,code", [
    *[pytest.param(*case, marks=pytest.mark.live) for case in [
        ("GB", 200, None), ("us", 200, None), ("BY", 200, None), ("HK", 200, None),
        ("IR", 400, "unsupported_region"), ("MO", 400, "unsupported_region"), ("AQ", 400, "unsupported_region")]],
    ("XK", 400, "bad_request"), ("UK", 400, "bad_request"),  # not a region code: refused before any request
])
def test_search_region_sample(client, region, status, code):
    if status == 200:
        body = ok(client, f"{YT}/search", query="music", region=region)
        assert body["videos"] or body["shelves"]
        if body["continuationToken"]:
            assert _state(body["continuationToken"])["g"] == region.upper()
    else:
        err(client, f"{YT}/search", status, code, query="music", region=region)


@pytest.mark.live
def test_search_shorts_unsupported_region(client):
    err(client, f"{YT}/search", 400, "unsupported_region", query="music", region="IR", type="shorts")


# --- params -------------------------------------------------------------------------------------------------------

@pytest.mark.live
def test_conflicting_and_repeated_params(client):
    assert ok(client, f"{YT}/channel", channelId=NASA, handle="MrBeast")["channelId"] == NASA
    r = client.get(f"{YT}/channel", params=[("handle", "MrBeast"), ("handle", "NASA")])
    skip_if_blocked(r)
    assert r.status_code == 200 and r.json()["channelId"] == NASA  # the last one wins
    page = ok(client, f"{YT}/channel-videos", channelId=MRBEAST)
    other = ok(client, f"{YT}/channel-videos", handle="NASA", continuationToken=page["continuationToken"])
    assert other["videos"] and {v["channel"]["title"] for v in other["videos"]} == {"MrBeast"}  # the cursor wins
    assert ok(client, f"{YT}/channel-videos", channelId="", handle="NASA")["videos"]
    err(client, f"{YT}/channel-videos", 400, "bad_request", channelId="  ")
    err(client, f"{YT}/channel-videos", 400, "bad_request", continuationToken="")
    err(client, f"{YT}/channel-videos", 400, "bad_request", continuationToken=" ")  # blank: left out, as ""
    err(client, f"{YT}/channel", 400, "bad_request", url="https://youtu.be/MrBeast")
