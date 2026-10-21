"""Live e2e tests for /v1/youtube/search, /v1/youtube/search/hashtag and /v1/youtube/playlist (anonymous).

Assertions are invariants (status, envelope, contract types, bucket exclusivity, no duplicates across pages,
clean termination, error codes), never volatile values. Throttling (429/503) skips instead of failing. Only the
tests marked live reach YouTube.
"""

from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.contract import check
from tests.e2e import LEAKS, app_with, skip_if_blocked

SEARCH, HASHTAG, PLAYLIST = "/v1/youtube/search", "/v1/youtube/search/hashtag", "/v1/youtube/playlist"
BUCKETS = ("videos", "channels", "playlists", "shorts", "shelves", "lives")
TF2 = "PLBB231211A4F62143"  # "Team Fortress 2 [2010 Version]", 29 videos, one page
SCIENCECASTS = "PL8A2171FA17D43A35"  # NASA, ~220 listed videos, 3 pages
MRBEAST_LONG = "UULFX6OQ3DkcsbYNE6H8uQQuVA"  # MrBeast long-form uploads, 800+ videos
MRBEAST_SHORTS = "UUSHX6OQ3DkcsbYNE6H8uQQuVA"
PODCAST = "PLgM0gYv7Sm1M6IwGaKNETyQLC7IO644py"  # 99% Invisible x BBC podcast, ~21 episodes, collab lockups
HIT_LIST = "RDCLAK5uy_kmPRjHDECIcuVwnKsx2Ng7fyNgFKWNJFs"  # YouTube Music's curated "The Hit List", ~50 videos
ALBUM = "MPREb_gTAcphH99wE"  # YouTube Music album "Royalty Free Music Library V2 (50 Songs)"
ALBUM_PLAYLIST = "OLAK5uy_l1m0thk3g31NmIIz_vMIbWtyv7eZixlH0"  # the playlist of its tracks

_memo: dict[tuple, tuple[int, dict]] = {}


def get(client, path: str, **params):
    """GET with memo (page-1 responses are reused across tests); returns (status, body)."""
    key = (path, tuple(sorted(params.items())))
    if key not in _memo:
        r = client.get(path, params=params)
        skip_if_blocked(r)
        _memo[key] = (r.status_code, r.json())
    return _memo[key]


def ok(client, path: str, **params) -> dict:
    status, body = get(client, path, **params)
    assert status == 200, body
    assert body["success"] is True and body["credits_charged"] == 0, body
    report = check(body, path)
    assert not report.mismatches, str(report)
    return body


def err(client, path: str, **params) -> tuple[int, dict]:
    status, body = get(client, path, **params)
    assert body["success"] is False and body["error"] and body["message"], body
    assert not LEAKS.search(body["message"]), body["message"]
    return status, body


def ids(body: dict) -> list[str]:
    out = []
    for k in BUCKETS:
        for item in body.get(k) or []:
            out += [x["id"] for x in item["items"]] if k == "shelves" else [item["id"]]
    return out


def decode(token: str) -> dict:
    raw = token[4:]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def encode(state) -> str:
    return "ss1." + base64.urlsafe_b64encode(json.dumps(state).encode()).decode().rstrip("=")


def walk(client, path: str, first: dict, keep: dict, max_pages: int) -> list[dict]:
    pages, params = [], dict(first)
    for _ in range(max_pages):
        body = ok(client, path, **params)
        pages.append(body)
        if not body.get("continuationToken"):
            break
        params = {**keep, "continuationToken": body["continuationToken"]}
    return pages


# --- search -----------------------------------------------------------------------------------------------

@pytest.mark.live
def test_search_basic(client):
    body = ok(client, SEARCH, query="lofi")
    assert all(isinstance(body[k], list) for k in BUCKETS)
    assert body["videos"] and body["continuationToken"].startswith("ss1.")
    assert all(v["type"] == "video" and v["url"].endswith(v["id"]) for v in body["videos"])


@pytest.mark.live
@pytest.mark.parametrize("params", [
    {"uploadDate": "today"}, {"uploadDate": "this_week"}, {"uploadDate": "this_year"}, {"sortBy": "relevance"},
    {"region": "GB"}, {"region": " jp "},
    {"uploadDate": "this_month", "sortBy": "popular", "type": "videos", "duration": "over_20_min"},
    {"type": "shorts", "sortBy": "popular"},
    {"includeExtras": "true"},
])
def test_search_filters_return_results(client, params):
    body = ok(client, SEARCH, query="lofi", **params)
    assert ids(body), params


@pytest.mark.live
@pytest.mark.parametrize("kind,bucket", [("videos", "videos"), ("channels", "channels"),
                                         ("playlists", "playlists"), ("shorts", "shorts")])
def test_search_type_buckets_are_exclusive(client, kind, bucket):
    body = ok(client, SEARCH, query="lofi", type=kind)
    assert body[bucket]
    others = {"videos": ("channels", "playlists", "shorts"), "channels": ("videos", "playlists", "shorts"),
              "playlists": ("videos", "channels", "shorts"), "shorts": ("videos", "channels", "playlists")}[kind]
    assert all(body[k] == [] for k in others), {k: len(body[k]) for k in others}


@pytest.mark.live
@pytest.mark.parametrize("duration,lo,hi", [("under_3_min", 0, 4 * 60), ("between_3_and_20_min", 3 * 60 - 30, 20 * 60 + 30),
                                             ("over_20_min", 20 * 60, 10**9)])
def test_search_duration_filter_is_applied(client, duration, lo, hi):
    # 'lofi' is dominated by 24/7 streams that YouTube returns without a length; use a query of plain uploads.
    lengths = [v["lengthSeconds"] for v in ok(client, SEARCH, query="piano tutorial", duration=duration)["videos"]
               if v["lengthSeconds"] is not None]
    assert lengths and sum(lo <= n <= hi for n in lengths) >= 0.8 * len(lengths), lengths


@pytest.mark.live
def test_search_popular_is_sorted_by_views(client):
    views = [v["viewCountInt"] for v in ok(client, SEARCH, query="lofi", sortBy="popular")["videos"]
             if v["viewCountInt"] is not None]
    pairs = list(zip(views, views[1:]))
    assert pairs and sum(a >= b for a, b in pairs) >= 0.8 * len(pairs)


@pytest.mark.live
@pytest.mark.parametrize("query", ["日本語 音楽", "café música", "a&b=c?d#e", "x" * 1500], ids=["ja", "es", "punct", "long"])
def test_search_odd_queries(client, query):
    ok(client, SEARCH, query=query)


@pytest.mark.parametrize("params,needle", [
    ({"query": ""}, "query must not be empty"),
    ({"query": "   "}, "query must not be empty"),
    ({"query": "lofi", "uploadDate": "yesterday"}, "uploadDate"),
    ({"query": "lofi", "uploadDate": ""}, "uploadDate"),
    ({"query": "lofi", "sortBy": "views"}, "sortBy"),
    ({"query": "lofi", "type": "video"}, "type"),
    ({"query": "lofi", "type": "all"}, "type"),
    ({"query": "lofi", "duration": "long"}, "duration"),
    ({"query": "lofi", "region": "ZZ"}, "region"),
    ({"query": "lofi", "region": "USA"}, "region"),
    ({"query": "lofi", "region": "EU"}, "region"),
    ({"query": "lofi", "region": "UK"}, "region"),
    ({"query": "lofi", "cache_max_age": "abc"}, "cache_max_age"),
])
def test_search_bad_params_are_400(client, params, needle):
    status, body = err(client, SEARCH, **params)
    assert status == 400 and body["error"] == "bad_request" and needle in body["message"]


@pytest.mark.live
def test_search_pagination_no_duplicates(client):
    pages = walk(client, SEARCH, {"query": "lofi"}, {"query": "lofi"}, 3)
    assert len(pages) == 3
    all_ids = [i for p in pages for i in ids(p)]
    assert len(all_ids) == len(set(all_ids))


@pytest.mark.live
def test_search_shorts_pagination_keeps_type(client):
    pages = walk(client, SEARCH, {"query": "running", "type": "shorts"}, {"query": "running"}, 2)
    assert len(pages) == 2
    assert all(p["shorts"] and p["videos"] == [] and p["channels"] == [] for p in pages)
    all_ids = [i for p in pages for i in ids(p)]
    assert len(all_ids) == len(set(all_ids))


@pytest.mark.live
def test_search_terminates(client):
    q = 'intitle:"Foursquare Swarm"'
    pages = walk(client, SEARCH, {"query": q}, {"query": q}, 8)
    assert pages[-1]["continuationToken"] is None


def _search_cursor(client) -> str:
    return ok(client, SEARCH, query="lofi")["continuationToken"]


@pytest.mark.live
@pytest.mark.parametrize("mutate", [
    lambda t, s: "garbage",
    lambda t, s: "4qmFsgKrCBIYVUNkRkpXVWE0M3NtUm00SXBIQnB",  # a native ScrapeCreators token
    lambda t, s: t[:-10],
    lambda t, s: encode({**s, "t": s["t"][:-12] + "AAAAAAAAAAAA"}),  # YouTube answers HTTP 400
    # yt_channel-4: YouTube answers HTTP 200 with a reloadContinuationItemsCommand of page 1, not an append
    lambda t, s: encode({**s, "t": s["t"][:len(s["t"]) // 2] + "AAAA" + s["t"][len(s["t"]) // 2 + 4:]}),
    lambda t, s: encode({**s, "t": ""}),
    lambda t, s: encode({**s, "k": "playlist"}),
    lambda t, s: encode({**s, "s": 7}),
    lambda t, s: encode({**s, "g": "ZZ"}),
    lambda t, s: encode({**s, "y": "movies"}),
    lambda t, s: encode({**s, "x": "ios"}),
    lambda t, s: encode({**s, "n": -1}),
    lambda t, s: encode([1, 2]),
    lambda t, s: "ss1." + "A" * 20000,
], ids=["garbage", "sc_native", "truncated", "t_tampered", "t_mid_tampered", "t_empty", "k_other", "s_int",
        "g_bad", "y_bad", "x_bad", "n_negative", "list", "huge"])
def test_search_tampered_cursor_is_bad_cursor(client, mutate):
    token = _search_cursor(client)
    r = client.get(SEARCH, params={"query": "lofi", "continuationToken": mutate(token, decode(token))})
    skip_if_blocked(r)
    assert r.status_code == 400 and r.json()["error"] == "bad_cursor", r.json()


@pytest.mark.live
def test_foreign_cursors_are_rejected(client):
    tokens = {
        SEARCH: (_search_cursor(client), {"query": "lofi"}),
        HASHTAG: (ok(client, HASHTAG, hashtag="funny")["continuationToken"], {"hashtag": "funny"}),
        PLAYLIST: (ok(client, PLAYLIST, playlist_id=SCIENCECASTS)["continuationToken"], {"playlist_id": SCIENCECASTS}),
    }
    for target, (_, base) in tokens.items():
        for source, (token, _) in tokens.items():
            if source == target:
                continue
            r = client.get(target, params={**base, "continuationToken": token})
            skip_if_blocked(r)
            assert r.status_code == 400 and r.json()["error"] == "bad_cursor", (source, target, r.json())


@pytest.mark.live
@pytest.mark.parametrize("params", [{"region": "CN"}, {"region": "KP", "type": "shorts"}], ids=["web", "mweb"])
def test_unsupported_region_is_not_an_empty_success(client, params):
    # CN / KP pass the ISO 3166 check, but YouTube answers the web client with a 'Something went wrong' promo
    # (once a 200 with every bucket empty and no cursor, cached as a success) and mweb with HTTP 400 (once a 500).
    status, body = get(client, SEARCH, query="lofi", **params)
    if status != 200 or not any(body[k] for k in BUCKETS):
        assert status == 400 and body["error"] == "unsupported_region", body


# --- hashtag ----------------------------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.parametrize("tag", ["funny", " #FUNNY ", "youtube.com/hashtag/funny/", "#日本"])
def test_hashtag_input_forms(client, tag):
    body = ok(client, HASHTAG, hashtag=tag)
    assert body["videos"] and body["continuationToken"]
    assert all(v["type"] in ("video", "short") for v in body["videos"])


@pytest.mark.live
@pytest.mark.parametrize("tag", ["தமிழ்", "#हिंदी", "россия", "#هشتگ\u200cها", "https://www.youtube.com/hashtag/മലയാളം"],
                         ids=["tamil", "hindi", "russian", "persian_zwnj", "url"])
def test_hashtag_non_latin_tags(client, tag):
    # retest-youtube_listings-1: these tags' feed params once carried '+' / '/' (YouTube: HTTP 400 -> our 500),
    # and a ZWNJ kept in the tag gave an empty feed; YouTube's own pages use URL-safe base64 and drop ZWNJ/ZWJ.
    body = ok(client, HASHTAG, hashtag=tag)
    assert body["videos"] and body["continuationToken"], body
    page2 = ok(client, HASHTAG, hashtag=tag, continuationToken=body["continuationToken"])
    assert page2["videos"], page2


@pytest.mark.live
def test_hashtag_zwnj_is_the_same_tag(client):
    token = ok(client, HASHTAG, hashtag="#\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645")["continuationToken"]
    assert ok(client, HASHTAG, hashtag="#\u0645\u06cc\u062e\u0648\u0627\u0647\u0645", continuationToken=token)["videos"]


@pytest.mark.live
@pytest.mark.parametrize("params", [{"hashtag": "funny", "type": "shorts"},
                                    {"hashtag": "https://m.youtube.com/hashtag/funny/shorts"}])
def test_hashtag_shorts_tab(client, params):
    body = ok(client, HASHTAG, **params)
    assert body["videos"] and all(v["type"] == "short" for v in body["videos"])


@pytest.mark.live
def test_hashtag_type_all_overrides_shorts_url(client):
    body = ok(client, HASHTAG, hashtag="https://www.youtube.com/hashtag/funny/shorts", type="all")
    # the 'All' tab: long-form videos with a channel, unlike the Shorts tab (id/title/thumbnail/views only)
    assert any(v["type"] == "video" and v.get("channel") for v in body["videos"])


@pytest.mark.parametrize("params,needle", [
    ({"hashtag": ""}, "Invalid hashtag"), ({"hashtag": " "}, "Invalid hashtag"), ({"hashtag": "#"}, "Invalid hashtag"),
    ({"hashtag": "#fun ny"}, "Invalid hashtag"), ({"hashtag": "fun-ny"}, "Invalid hashtag"),
    ({"hashtag": "https://www.tiktok.com/tag/funny"}, "Not a YouTube URL"),
    ({"hashtag": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, "Not a YouTube hashtag URL"),
    ({"hashtag": "funny", "type": "videos"}, "type"), ({"hashtag": "funny", "type": ""}, "type"),
    ({}, "hashtag"),
])
def test_hashtag_bad_params_are_400(client, params, needle):
    status, body = err(client, HASHTAG, **params)
    assert status == 400 and needle in body["message"]


@pytest.mark.live
def test_hashtag_nonexistent_is_empty(client):
    body = ok(client, HASHTAG, hashtag="qaq9zzxxnonexist77")
    assert body["videos"] == [] and body["continuationToken"] is None


@pytest.mark.live
@pytest.mark.parametrize("params,pages", [({"hashtag": "funny"}, 3), ({"hashtag": "funny", "type": "shorts"}, 3)])
def test_hashtag_pagination_no_duplicates(client, params, pages):
    walked = walk(client, HASHTAG, params, {"hashtag": "funny"}, pages)
    assert len(walked) == pages and all(p["videos"] for p in walked)
    all_ids = [v["id"] for p in walked for v in p["videos"]]
    assert len(all_ids) == len(set(all_ids))


@pytest.mark.live
def test_hashtag_tampered_cursor_is_bad_cursor(client):
    state = decode(ok(client, HASHTAG, hashtag="funny")["continuationToken"])
    half = len(state["t"]) // 2
    for token in ("garbage", encode({**state, "x": "ios"}), encode({**state, "k": "search"}),
                  encode({**state, "t": state["t"][:half] + "AAAA" + state["t"][half + 4:]})):
        r = client.get(HASHTAG, params={"hashtag": "funny", "continuationToken": token})
        skip_if_blocked(r)
        assert r.status_code == 400 and r.json()["error"] == "bad_cursor", r.json()


@pytest.mark.live
def test_hashtag_cursor_is_bound_to_its_tag(client):
    token = ok(client, HASHTAG, hashtag="funny")["continuationToken"]
    r = client.get(HASHTAG, params={"hashtag": "cooking", "continuationToken": token})
    skip_if_blocked(r)
    assert r.status_code == 400 and r.json()["error"] == "bad_cursor"


# --- playlist ---------------------------------------------------------------------------------------------

@pytest.mark.live
def test_playlist_single_page(client):
    body = ok(client, PLAYLIST, playlist_id=TF2)
    assert body["title"] == "Team Fortress 2 [2010 Version]" and body["owner"]["id"].startswith("UC")
    assert body["totalVideos"] == len(body["videos"]) == 29 and body["continuationToken"] is None
    assert check(body, PLAYLIST).coverage == 1.0
    assert all(v["url"].endswith(v["id"]) and v["lengthSeconds"] and v["channel"]["title"] for v in body["videos"])


@pytest.mark.live
@pytest.mark.parametrize("value", [
    f"https://www.youtube.com/watch?v=dQw4w9WgXcQ&list={TF2}", f"https://youtu.be/dQw4w9WgXcQ?list={TF2}",
    f"music.youtube.com/playlist?list={TF2}&index=3&si=abc", f"https://www.youtube.com/embed/videoseries?list={TF2}",
    f"  {TF2}  ", f"https://www.youtube.com/embed/videoseries?si=x&amp;list={TF2}",
    f"https://music.youtube.com/browse/VL{TF2}",
], ids=["watch", "youtu.be", "music_noscheme_extra", "embed", "padded", "embed_src_escaped", "music_browse"])
def test_playlist_url_forms(client, value):
    assert ok(client, PLAYLIST, playlist_id=value)["title"] == "Team Fortress 2 [2010 Version]"


@pytest.mark.parametrize("value,needle", [
    ("", "playlist_id must be"), (" ", "playlist_id must be"), ("LL", "playlist_id must be"),
    ("RDMM", "playlist_id must be"), ("PL!!!!!!!!!!!!", "playlist_id must be"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "playlist_id must be"),
    ("https://www.youtube.com/playlist", "playlist_id must be"),
    (f"https://www.tiktok.com/playlist?list={TF2}", "Not a YouTube URL"),
])
def test_playlist_bad_ids_are_400(client, value, needle):
    status, body = err(client, PLAYLIST, playlist_id=value)
    assert status == 400 and body["error"] == "bad_request" and needle in body["message"]


def test_playlist_missing_param(client):
    status, body = err(client, PLAYLIST)
    assert status == 400 and "playlist_id" in body["message"]


@pytest.mark.live
@pytest.mark.parametrize("value", ["PLzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "PLtPgu7CB4gbY9oDN3drwC3cMbJggS7dKl"])
def test_playlist_missing_or_private_is_404(client, value):
    status, body = err(client, PLAYLIST, playlist_id=value)
    assert status == 404 and body["error"] == "not_found"


@pytest.mark.live
@pytest.mark.parametrize("value", ["RDdQw4w9WgXcQ", "RDAMVMdQw4w9WgXcQ"])
def test_playlist_mix_is_unviewable(client, value):
    status, body = err(client, PLAYLIST, playlist_id=value)
    assert status == 400 and body["error"] == "unviewable_playlist"


@pytest.mark.live
def test_playlist_music_curated_lists(client):
    """YouTube Music's curated RDCLAK5uy_… playlists start with 'RD' but are no mixes: they list (the route notes
    said every RD… playlist was a 400)."""
    body = ok(client, PLAYLIST, playlist_id=HIT_LIST)
    assert body["title"] and body["videos"] and body["totalVideos"] >= len(body["videos"])


@pytest.mark.live
def test_playlist_music_album(client):
    """A YouTube Music album page (/browse/MPREb_…; it was a 400) lists the playlist of the album's tracks."""
    body = ok(client, PLAYLIST, playlist_id=f"https://music.youtube.com/browse/{ALBUM}")
    assert "Royalty Free Music Library V2" in body["title"] and len(body["videos"]) >= 40
    tracks = ok(client, PLAYLIST, playlist_id=ALBUM_PLAYLIST)["videos"]
    assert [v["id"] for v in body["videos"]] == [v["id"] for v in tracks]
    status, body = err(client, PLAYLIST, playlist_id="MPREb_zzzzzzzzzzz")
    assert status == 404 and body["error"] == "not_found"


@pytest.mark.live
def test_playlist_walk_terminates_at_total(client):
    pages = walk(client, PLAYLIST, {"playlist_id": SCIENCECASTS}, {"playlist_id": SCIENCECASTS}, 6)
    assert pages[-1]["continuationToken"] is None and 2 <= len(pages) <= 4
    assert all(len(p["videos"]) <= 100 for p in pages) and len(pages[0]["videos"]) >= 90
    served = sum(len(p["videos"]) for p in pages)
    assert served <= pages[0]["totalVideos"] and served >= 0.9 * pages[0]["totalVideos"]


@pytest.mark.live
def test_playlist_large_walk(client):
    pages = walk(client, PLAYLIST, {"playlist_id": MRBEAST_LONG}, {"playlist_id": MRBEAST_LONG}, 15)
    total = pages[0]["totalVideos"]
    all_ids = [v["id"] for p in pages for v in p["videos"]]
    assert total > 300 and pages[-1]["continuationToken"] is None
    assert len(all_ids) == len(set(all_ids)) and 0.95 * total <= len(all_ids) <= total
    assert {v["channel"]["title"] for p in pages for v in p["videos"]} == {"MrBeast"}


@pytest.mark.live
def test_playlist_shorts_is_one_page(client):
    body = ok(client, PLAYLIST, playlist_id=MRBEAST_SHORTS)
    assert 0 < len(body["videos"]) <= 100 and body["continuationToken"] is None
    assert all(v["lengthSeconds"] is None and v["channel"]["title"] == "MrBeast" for v in body["videos"])


@pytest.mark.live
def test_playlist_cursor_checks(client):
    token = ok(client, PLAYLIST, playlist_id=SCIENCECASTS)["continuationToken"]
    state = decode(token)
    for params in ({"playlist_id": TF2, "continuationToken": token},
                   {"playlist_id": SCIENCECASTS, "continuationToken": encode({**state, "t": state["t"][:-8] + "AAAAAAAA"})},
                   {"playlist_id": SCIENCECASTS, "continuationToken": encode({**state, "n": True})},
                   {"playlist_id": SCIENCECASTS, "continuationToken": encode({**state, "m": [1]})}):
        r = client.get(PLAYLIST, params=params)
        skip_if_blocked(r)
        assert r.status_code == 400 and r.json()["error"] == "bad_cursor", r.json()
    url = f"https://www.youtube.com/playlist?list={SCIENCECASTS}"
    page2 = ok(client, PLAYLIST, playlist_id=url, continuationToken=token)
    assert page2["videos"] and page2["totalVideos"] == state["m"]["totalVideos"]


@pytest.mark.live
def test_playlist_collab_videos_keep_their_channel(client):
    # Collaboration lockups (avatarStackViewModel) lose the channel; yt-dlp reports '99% Invisible'.
    assert all(v["channel"] for v in ok(client, PLAYLIST, playlist_id=PODCAST)["videos"])


@pytest.mark.live
def test_podcast_playlist_has_total_and_terminates(client):
    body = ok(client, PLAYLIST, playlist_id=PODCAST)
    assert isinstance(body["totalVideos"], int)
    assert body["continuationToken"] is None or len(body["videos"]) == 100


@pytest.mark.live
def test_playlist_forged_cursor_meta_is_rejected(client):
    state = decode(ok(client, PLAYLIST, playlist_id=SCIENCECASTS)["continuationToken"])
    forged = encode({**state, "m": {"title": "forged", "totalVideos": "abc", "owner": "str"}})
    r = client.get(PLAYLIST, params={"playlist_id": SCIENCECASTS, "continuationToken": forged})
    skip_if_blocked(r)
    assert r.status_code == 400 and r.json()["error"] == "bad_cursor"


# --- cross-cutting ----------------------------------------------------------------------------------------

@pytest.mark.live
def test_cache_hits_and_isolation(tmp_path):
    with app_with({}, tmp_path) as c:
        for path, params, other in [(SEARCH, {"query": "lofi"}, {"query": "lofi", "type": "channels"}),
                                    (HASHTAG, {"hashtag": "funny"}, {"hashtag": "funny", "type": "shorts"}),
                                    (PLAYLIST, {"playlist_id": TF2}, {"playlist_id": SCIENCECASTS})]:
            first = c.get(path, params={**params, "cache_max_age": "1h"})
            skip_if_blocked(first)
            second = c.get(path, params={**params, "cache_max_age": "1h"})
            assert first.status_code == second.status_code == 200
            assert second.json().get("cached") is True and second.json()["cached_at"]
            assert second.headers.get("x-cache") == "hit"
            a, b = first.json(), second.json()
            for k in ("cached", "cached_at"):
                a.pop(k, None), b.pop(k, None)
            assert a == b
            diff = c.get(path, params={**other, "cache_max_age": "1h"})
            skip_if_blocked(diff)
            assert diff.status_code == 200 and not diff.json().get("cached")
        for _ in range(2):  # errors are never cached
            r = c.get(PLAYLIST, params={"playlist_id": "RDdQw4w9WgXcQ", "cache_max_age": "1h"})
            assert r.status_code == 400 and not r.json().get("cached")
        assert c.get(SEARCH, params={"query": "lofi", "cache_max_age": "-1"}).status_code == 400


@pytest.mark.live
def test_parallel_requests(client):
    calls = [(SEARCH, {"query": "piano"}), (SEARCH, {"query": "chess", "type": "channels"}),
             (HASHTAG, {"hashtag": "dance"}), (PLAYLIST, {"playlist_id": TF2})]
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda p: client.get(p[0], params=p[1]), calls))
    for r in results:
        skip_if_blocked(r)
    assert [r.status_code for r in results] == [200] * len(results)
    assert all(r.json()["success"] is True and r.json()["credits_charged"] == 0 for r in results)
    assert results[3].json()["title"] == "Team Fortress 2 [2010 Version]"


def test_coverage_notes(client):
    routes = {e["path"]: e for e in client.get("/meta/coverage").json()["endpoints"]}
    for path in (SEARCH, HASHTAG, PLAYLIST):
        assert routes[path]["status"] == "implemented"
