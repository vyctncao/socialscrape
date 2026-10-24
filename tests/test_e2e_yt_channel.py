"""End-to-end checks of the YouTube channel endpoints (anonymous).

/v1/youtube/channel, /channel-videos, /channel/shorts, /channel/lives, /channel/playlists and
/channel/community-posts: identifier forms, validation, deep pagination against yt-dlp's own tab listing,
sort orders, cursors, empty tabs, cache_max_age and error hygiene. Only the tests marked live reach YouTube.
"""

from __future__ import annotations

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from socialscrape.cursor import encode_cursor
from tests.contract import assert_conforms
from tests.e2e import LEAKS, app_with, skip_if_blocked, ytdlp_info

YT = "https://www.youtube.com"
MRBEAST = "UCX6OQ3DkcsbYNE6H8uQQuVA"
NASA = "UCLA_DiR1FfKNvjuUpBHmylQ"
TERMINATED = "UCvsye7V9psc-APX6wV1twLg"  # channel YouTube reports as terminated / not available
TOPIC = "UC9ALqqC4aIeG5iDs7i90Bfw"  # "Royalty Free Music Crew - Topic": auto-generated, no Videos tab
GAANA = "UCqqBB5_Jeag562YhsoDBLJQ"  # canonical handle @गाना (Devanagari with combining vowel signs)
TABS = {  # endpoint -> (list key, first-page params of a channel with several pages)
    "/v1/youtube/channel-videos": ("videos", {"handle": "MrBeast"}),
    "/v1/youtube/channel/shorts": ("shorts", {"handle": "MrBeast"}),
    "/v1/youtube/channel/lives": ("lives", {"channelId": NASA}),
    "/v1/youtube/channel/playlists": ("playlists", {"handle": "NASA"}),
    "/v1/youtube/channel/community-posts": ("posts", {"handle": "MrBeast"}),
}

_SEEN: dict[str, tuple[int, dict]] = {}


def call(client, path: str, **params) -> tuple[int, dict]:
    """GET once per distinct URL per session; skips the test when YouTube throttles this IP."""
    key = json.dumps([path, params], sort_keys=True, ensure_ascii=False)
    if key not in _SEEN:
        r = client.get(path, params=params)
        skip_if_blocked(r)
        _SEEN[key] = (r.status_code, r.json())
    return _SEEN[key]


def ok(client, path: str, **params) -> dict:
    status, body = call(client, path, **params)
    assert status == 200, (path, params, body)
    assert body["success"] is True and body["credits_charged"] == 0
    return body


def err(client, path: str, status: int, code: str | None = None, **params) -> dict:
    got, body = call(client, path, **params)
    assert got == status, (path, params, body)
    assert body["success"] is False and isinstance(body["message"], str) and body["message"]
    assert not LEAKS.search(body["message"]), body
    if code:
        assert body["error"] == code, body
    return body


def walk(client, path: str, n: int, **params) -> list[list[dict]]:
    """Up to n pages; every page but the last must carry a token."""
    key = TABS[path][0]
    out, body = [], ok(client, path, **params)
    while True:
        out.append(body[key])
        token = body["continuationToken"]
        if len(out) == n or not token:
            return out
        assert body[key], "a page with a next token must not be empty"
        body = ok(client, path, continuationToken=token)


def ids_of(pages: list[list[dict]]) -> list[str]:
    return [x["id"] for page in pages for x in page]


def assert_most_viewed_first(views: list[int]) -> None:
    """YouTube orders 'popular' by its own cached counts while viewCountInt is today's exact count, so two nearly
    equal ones can come swapped (live-popular-sort-strict-order-flaky: 341317941 after 341314987). A later item
    may be ahead by 2% at most: any other order (latest first, say) fails."""
    assert all(isinstance(x, int) for x in views)
    for i, (before, after) in enumerate(zip(views, views[1:]), 1):
        assert after <= before * 1.02, f"index {i}: {after} after {before}"


def ytdlp_ids(url: str, n: int) -> list[str]:
    """yt-dlp's own flat listing of a channel tab (anonymous)."""
    return [e["id"] for e in ytdlp_info(url, extract_flat=True, playlistend=n)["entries"]][:n]


def unb64(token: str) -> dict:
    body = token[4:]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --- /v1/youtube/channel -----------------------------------------------------

@pytest.mark.live
@pytest.mark.parametrize("params", [
    {"channelId": MRBEAST}, {"handle": "MrBeast"}, {"handle": "@MrBeast"}, {"handle": "mrbeast"},
    {"handle": "MRBEAST"}, {"url": f"{YT}/@MrBeast"}, {"url": f"{YT}/@MrBeast/videos?view=0#x"},
    {"url": f"{YT}/channel/{MRBEAST}"}, {"url": f"{YT}/user/MrBeast6000"}, {"url": f"{YT}/MrBeast6000"},
    {"url": "m.youtube.com/@MrBeast"}, {"url": "@MrBeast"}, {"url": MRBEAST}, {"channelId": "@MrBeast"},
    {"handle": f"{YT}/@MrBeast/about"}, {"url": f"https://music.youtube.com/browse/{MRBEAST}"},
])
def test_channel_identifier_forms(client, params):
    body = ok(client, "/v1/youtube/channel", **params)
    assert body["channelId"] == MRBEAST and body["name"] == "MrBeast"
    assert body["channel"].endswith("/@MrBeast")


@pytest.mark.live
def test_channel_contract_and_fields(client):
    body = ok(client, "/v1/youtube/channel", handle="NASA")
    report = assert_conforms(body, "/v1/youtube/channel", min_coverage=0.9)
    assert not report.mismatches
    assert body["channelId"] == NASA and body["name"] == "NASA"
    assert isinstance(body["subscriberCount"], int) and body["subscriberCount"] > 1_000_000
    assert body["subscriberCountText"].endswith("subscribers")
    assert re.fullmatch(r"[\d,]+ videos?", body["videoCountText"])
    assert re.fullmatch(r"[\d,]+ views?", body["viewCountText"])
    assert body["joinedDateText"].startswith("Joined") and body["email"] is None
    assert all(isinstance(x, str) for x in body["links"])


@pytest.mark.parametrize("params,message", [
    ({}, "Provide one of"),
    ({"handle": "bad handle!"}, "Invalid YouTube handle"),
    ({"channelId": "UCX6OQ3"}, "channelId must look like"),
    ({"channelId": "notachannel"}, "channelId must look like"),
    ({"url": "https://www.tiktok.com/@mrbeast"}, "Not a YouTube URL"),
    ({"url": f"{YT}/watch?v=dQw4w9WgXcQ"}, "Not a YouTube channel URL"),
    ({"handle": "x" * 101}, "Invalid YouTube handle"),
])
def test_channel_rejects_bad_input(client, params, message):
    body = err(client, "/v1/youtube/channel", 400, **params)
    assert message in body["message"]


@pytest.mark.live
@pytest.mark.parametrize("params", [
    {"handle": "thishandledoesnotexist12345xyz"}, {"channelId": "UCxxxxxxxxxxxxxxxxxxxxxx"},
    {"channelId": TERMINATED},
])
def test_channel_missing_or_terminated_is_404(client, params):
    err(client, "/v1/youtube/channel", 404, "not_found", **params)


@pytest.mark.live
@pytest.mark.parametrize("handle", ["日本料理かんさい", "@日本料理かんさい", "한식푸우v10"])
def test_channel_cjk_handles(client, handle):
    body = ok(client, "/v1/youtube/channel", handle=handle)
    assert body["channelId"].startswith("UC") and "%" in body["channel"]  # canonical URL is percent-encoded


@pytest.mark.live
def test_indic_channel_by_id(client):
    body = ok(client, "/v1/youtube/channel", channelId=GAANA)
    assert body["channelId"] == GAANA  # the id form works for the channel the handle tests below use


@pytest.mark.live
@pytest.mark.parametrize("path,params", [
    ("/v1/youtube/channel", {"handle": "गाना"}),
    ("/v1/youtube/channel", {"handle": "@गाना"}),
    ("/v1/youtube/channel", {"url": f"{YT}/@गाना"}),
    ("/v1/youtube/channel-videos", {"handle": "@गाना"}),
])
def test_indic_handle_with_combining_marks(client, path, params):
    status, body = call(client, path, **params)
    assert status == 200, body
    if path == "/v1/youtube/channel":
        assert body["channelId"] == GAANA
    else:
        assert body["videos"]


@pytest.mark.live
def test_store_link_hosted_like_a_store(client):
    body = ok(client, "/v1/youtube/channel", handle="LinusTechTips")
    shop = [u for u in body["links"] if "lttstore.com" in u]
    if not shop:
        pytest.skip("LinusTechTips no longer links lttstore.com")
    assert body["store"] == shop[0]


@pytest.mark.live
def test_links_are_absolute_urls(client):
    body = ok(client, "/v1/youtube/channel", handle="LinusTechTips")
    if not any("tiktok.com" in u for u in body["links"]):
        pytest.skip("LinusTechTips no longer links TikTok")
    assert all(re.match(r"https?://", u) for u in body["links"]), body["links"]


# --- channel-videos ------------------------------------------------------------

@pytest.mark.live
def test_channel_videos_deep_pagination_matches_ytdlp(client):
    pages = walk(client, "/v1/youtube/channel-videos", 5, handle="MrBeast")
    assert len(pages) == 5 and all(len(p) == 30 for p in pages)
    ids = ids_of(pages)
    assert len(set(ids)) == len(ids), "duplicates across pages"
    assert ids == ytdlp_ids(f"{YT}/@MrBeast/videos", len(ids)), "gap or reorder vs yt-dlp's tab listing"
    now = datetime.now(timezone.utc)
    for v in (x for p in pages for x in p):
        assert v["type"] == "video" and v["url"] == f"{YT}/watch?v={v['id']}"
        assert v["channel"]["title"] == "MrBeast"
        if v["viewCountText"] and v["viewCountText"] != "No views":  # exact counts on the mweb listing
            assert re.fullmatch(r"[\d,]+ views?", v["viewCountText"]), v["viewCountText"]
            assert v["viewCountInt"] == int(re.sub(r"\D", "", v["viewCountText"]))
        if v["publishedTime"]:
            assert iso(v["publishedTime"]) <= now + timedelta(days=1)
    assert_conforms(ok(client, "/v1/youtube/channel-videos", handle="MrBeast"), "/v1/youtube/channel-videos",
                    min_coverage=0.9)


@pytest.mark.live
def test_channel_videos_popular_is_sorted_by_views(client):
    pages = walk(client, "/v1/youtube/channel-videos", 3, handle="MrBeast", sort="popular")
    views = [v["viewCountInt"] for p in pages for v in p]
    assert len(views) == 90
    assert_most_viewed_first(views)
    latest = ok(client, "/v1/youtube/channel-videos", handle="MrBeast", sort="latest")
    assert ids_of([latest["videos"]]) == ids_of([ok(client, "/v1/youtube/channel-videos", handle="MrBeast")["videos"]])


@pytest.mark.live
def test_channel_videos_popular_behind_the_sort_drop_down(client):
    """A Videos tab with members-only videos (LTT's) has Latest/Popular/Oldest in a drop-down chip, not as chips of
    their own; sort=popular used to answer the latest order there."""
    popular = ok(client, "/v1/youtube/channel-videos", handle="LinusTechTips", sort="popular")["videos"]
    assert len(popular) == 30
    assert_most_viewed_first([v["viewCountInt"] for v in popular])
    latest = ok(client, "/v1/youtube/channel-videos", handle="LinusTechTips")["videos"]
    assert ids_of([popular]) != ids_of([latest])


@pytest.mark.parametrize("sort", ["Latest", "POPULAR", "oldest", "newest"])
def test_channel_videos_bad_sort(client, sort):
    err(client, "/v1/youtube/channel-videos", 400, handle="MrBeast", sort=sort)


@pytest.mark.parametrize("value", ["true", "1", "TRUE"])
def test_channel_videos_paid_promotions_not_supported(client, value):
    err(client, "/v1/youtube/channel-videos", 501, "not_supported", handle="MrBeast", is_paid_promotions=value)


@pytest.mark.live
def test_channel_videos_extra_params(client):
    base = ids_of([ok(client, "/v1/youtube/channel-videos", handle="MrBeast")["videos"]])
    for extra in ({"includeExtras": "true"}, {"is_paid_promotions": "false"}):
        assert ids_of([ok(client, "/v1/youtube/channel-videos", handle="MrBeast", **extra)["videos"]]) == base
    videos = ok(client, "/v1/youtube/channel-videos", handle="MrBeast", includeExtras="true")["videos"]
    assert sum(bool(isinstance(v["likeCountInt"], int) and isinstance(v["commentCountInt"], int)
                    and v["publishDate"] and v["description"]) for v in videos) >= len(videos) - 2


# --- shorts / lives / playlists / community-posts -----------------------------

@pytest.mark.live
def test_shorts_pagination_and_sort(client):
    pages = walk(client, "/v1/youtube/channel/shorts", 3, handle="MrBeast")
    ids = ids_of(pages)
    assert len(ids) == 144 and len(set(ids)) == len(ids)
    assert ids == ytdlp_ids(f"{YT}/@MrBeast/shorts", len(ids))
    first = ok(client, "/v1/youtube/channel/shorts", handle="MrBeast")
    assert_conforms(first, "/v1/youtube/channel/shorts")
    assert all(first[k] == [] for k in ("videos", "channels", "playlists", "shelves", "lives"))
    assert all(isinstance(s["viewCountInt"], int) for s in first["shorts"])
    newest = ok(client, "/v1/youtube/channel/shorts", handle="MrBeast", sort="newest")
    assert ids_of([newest["shorts"]]) == ids_of([first["shorts"]])
    popular = walk(client, "/v1/youtube/channel/shorts", 2, handle="MrBeast", sort="popular")
    views = [s["viewCountInt"] for p in popular for s in p]
    assert_most_viewed_first(views)
    assert len(set(ids_of(popular))) == len(views)
    err(client, "/v1/youtube/channel/shorts", 400, handle="MrBeast", sort="latest")


@pytest.mark.live
def test_lives_pagination_matches_ytdlp(client):
    pages = walk(client, "/v1/youtube/channel/lives", 3, channelId=NASA)
    ids = ids_of(pages)
    assert len(ids) == 90 and len(set(ids)) == len(ids)
    assert ids == ytdlp_ids(f"{YT}/channel/{NASA}/streams", len(ids))
    assert_conforms(ok(client, "/v1/youtube/channel/lives", channelId=NASA), "/v1/youtube/channel/lives",
                    min_coverage=0.9)
    for v in (x for p in pages for x in p):
        assert v["type"] == "live" and v["channel"]["id"] == NASA and v["channel"]["thumbnail"] is None
        if re.search(r"watching|waiting", v["viewCountText"] or ""):
            assert v["viewCountInt"] is None
        elif v["viewCountText"]:
            assert isinstance(v["viewCountInt"], int)


@pytest.mark.live
def test_playlists_walk_to_end_matches_ytdlp(client):
    pages = walk(client, "/v1/youtube/channel/playlists", 8, handle="NASA")
    ids = ids_of(pages)
    assert len(pages) < 8 and len(set(ids)) == len(ids)
    assert ids == ytdlp_ids(f"{YT}/@NASA/playlists", 500)
    assert_conforms(ok(client, "/v1/youtube/channel/playlists", handle="NASA"), "/v1/youtube/channel/playlists",
                    min_coverage=0.9)
    for pl in (x for p in pages for x in p):
        assert pl["playlistUrl"] == f"{YT}/playlist?list={pl['id']}"
        assert pl["channel"]["id"] == NASA and pl["channel"]["channelUrl"] == f"{YT}/@NASA"
        assert pl["videoCount"] is None or pl["videoCount"] >= 0


@pytest.mark.live
def test_community_posts_deep_pagination(client):
    pages = walk(client, "/v1/youtube/channel/community-posts", 5, handle="MrBeast")
    posts = [x for p in pages for x in p]
    assert len(pages) == 5 and len(posts) >= 40 and len({x["id"] for x in posts}) == len(posts)
    assert_conforms(ok(client, "/v1/youtube/channel/community-posts", handle="MrBeast"),
                    "/v1/youtube/channel/community-posts")
    times = [iso(x["publishedTime"]) for x in posts[1:] if x["publishedTime"]]  # a pinned post may lead
    assert times == sorted(times, reverse=True) and len(times) >= len(posts) - 2
    for x in posts:
        assert x["url"] == f"{YT}/post/{x['id']}" and x["channel"]["id"] == MRBEAST
        assert x["likeCount"] is None or isinstance(x["likeCount"], int)
        assert isinstance(x["images"], list) and x["image"] == (x["images"][0] if x["images"] else None)


# --- cross-cutting -------------------------------------------------------------

@pytest.mark.live
@pytest.mark.parametrize("path", list(TABS))
@pytest.mark.parametrize("params", [
    {"handle": f"{YT}/@MrBeast/featured"}, {"channelId": f"{YT}/channel/{MRBEAST}"}, {"handle": "@mrbeast"},
])
def test_tabs_accept_identifier_forms(client, path, params):
    body = ok(client, path, **params)
    assert isinstance(body[TABS[path][0]], list)


@pytest.mark.parametrize("path", list(TABS))
@pytest.mark.parametrize("params", [
    {}, {"handle": ""}, {"handle": "bad handle!"}, {"channelId": "UCX6OQ3"},
    {"handle": "https://www.tiktok.com/@x"},
])
def test_tabs_reject_bad_input(client, path, params):
    err(client, path, 400, **params)


@pytest.mark.live
@pytest.mark.parametrize("path", list(TABS))
def test_tabs_missing_channel_is_404(client, path):
    err(client, path, 404, "not_found", handle="thishandledoesnotexist12345xyz")


@pytest.mark.live
@pytest.mark.parametrize("path", ["/v1/youtube/channel/shorts", "/v1/youtube/channel/lives",
                                  "/v1/youtube/channel/community-posts"])
def test_empty_tab_is_empty_200(client, path):
    body = ok(client, path, handle="jawed")
    assert body[TABS[path][0]] == [] and body["continuationToken"] is None


@pytest.mark.live
@pytest.mark.parametrize("path", list(TABS))
def test_auto_generated_topic_channel_does_not_error(client, path):
    body = ok(client, path, channelId=TOPIC)
    assert isinstance(body[TABS[path][0]], list)


def _first_token(client, path: str) -> str:
    token = ok(client, path, **TABS[path][1])["continuationToken"]
    assert token and token.startswith("ss1.")
    return token


@pytest.mark.live
@pytest.mark.parametrize("path", list(TABS))
def test_tabs_reject_bad_cursors(client, path):
    state = unb64(_first_token(client, path))
    other = next(p for p in TABS if p != path)
    cases = {
        "garbage": "garbage",
        "native": state["t"],
        "not_json": "ss1." + base64.urlsafe_b64encode(b"{nope").decode(),
        "list": "ss1." + base64.urlsafe_b64encode(b"[1,2]").decode(),
        "truncated_b64": _first_token(client, path)[:40],
        "t_not_str": encode_cursor({**state, "t": 5}),
        "bad_client": encode_cursor({**state, "x": "ios"}),
        "bad_ctx": encode_cursor({**state, "c": ["a"]}),
        "no_kind": encode_cursor({k: v for k, v in state.items() if k != "k"}),
        "upstream_rejects": encode_cursor({**state, "t": "garbage"}),
        "half_token": encode_cursor({**state, "t": state["t"][: len(state["t"]) // 2]}),
        "foreign": _first_token(client, other),
    }
    for name, token in cases.items():
        status, body = call(client, path, continuationToken=token)
        assert status == 400 and body["error"] == "bad_cursor", (name, status, body)


@pytest.mark.live
@pytest.mark.parametrize("path", ["/v1/youtube/channel-videos", "/v1/youtube/channel/shorts"])
def test_corrupted_native_token_is_bad_cursor(client, path):
    state = unb64(_first_token(client, path))
    forged = encode_cursor({**state, "t": state["t"][:-8] + "AAAAAAAA"})
    status, body = call(client, path, continuationToken=forged)
    assert status == 400 and body["error"] == "bad_cursor", (status, body)


def _overwritten(token: str, at: float) -> str:
    i = int(len(token) * at)
    return token[:i] + "AAAA" + token[i + 4:]


@pytest.mark.live
@pytest.mark.parametrize("path", ["/v1/youtube/channel/community-posts", "/v1/youtube/channel/playlists"])
def test_token_corrupted_mid_way_is_bad_cursor(client, path):
    # yt_channel-4: YouTube answers such a token with an HTTP 200 that reloads the tab's start (home shelves
    # plus a reloadContinuationItemsCommand) rather than appending; it was read as an empty last page.
    state = unb64(_first_token(client, path))
    status, body = call(client, path, continuationToken=encode_cursor({**state, "t": _overwritten(state["t"], 0.5)}))
    assert status == 400 and body["error"] == "bad_cursor", (status, body)


@pytest.mark.live
@pytest.mark.parametrize("path", list(TABS))
def test_corrupted_token_is_never_an_empty_last_page(client, path):
    # YouTube tolerates some overwritten bytes (a real next page) and rejects others (bad_cursor), but a
    # corrupted token must never pass for the end of the tab.
    key, state = TABS[path][0], unb64(_first_token(client, path))
    for name, t in {"end": state["t"][:-8] + "AAAAAAAA", "third": _overwritten(state["t"], 1 / 3),
                    "half": _overwritten(state["t"], 0.5)}.items():
        status, body = call(client, path, continuationToken=encode_cursor({**state, "t": t}))
        assert (status == 400 and body["error"] == "bad_cursor") or (status == 200 and body[key]), (name, status, body)


@pytest.mark.live
def test_cursor_keeps_its_channel(client):
    token = _first_token(client, "/v1/youtube/channel-videos")
    page2 = ok(client, "/v1/youtube/channel-videos", continuationToken=token)
    assert page2["videos"] and all(v["channel"]["title"] == "MrBeast" for v in page2["videos"])


@pytest.mark.live
def test_cache_max_age(tmp_path):
    with app_with({}, tmp_path) as c:
        path, params = "/v1/youtube/channel/lives", {"channelId": NASA}
        first = c.get(path, params=params)
        skip_if_blocked(first)
        token = first.json()["continuationToken"]
        for p in (params, {"continuationToken": token}):
            miss = c.get(path, params={**p, "cache_max_age": "1d"})
            skip_if_blocked(miss)
            hit = c.get(path, params={**p, "cache_max_age": "1d"})
            assert hit.headers.get("x-cache") == "hit" and hit.json()["cached"] is True
            assert hit.json()["lives"] == miss.json()["lives"]
            fresh = c.get(path, params={**p, "cache_max_age": "0"})
            assert fresh.headers.get("x-cache") != "hit" and "cached" not in fresh.json()
        for bad in ("-1d", "abc", "1w"):
            assert c.get(path, params={**params, "cache_max_age": bad}).status_code == 400
        gone = {"handle": "thishandledoesnotexist12345xyz", "cache_max_age": "1d"}
        assert c.get(path, params=gone).status_code == 404
        assert c.get(path, params=gone).headers.get("x-cache") != "hit"  # errors are not cached


@pytest.mark.live
def test_parallel_requests(client):
    reqs = [("/v1/youtube/channel", {"handle": "veritasium"}), ("/v1/youtube/channel-videos", {"handle": "mkbhd"}),
            ("/v1/youtube/channel/playlists", {"handle": "mkbhd"})] * 2
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(lambda a: client.get(a[0], params=a[1]), reqs))
    if any(r.status_code in (429, 503) for r in res):
        pytest.skip("YouTube throttled this IP")
    assert all(r.status_code == 200 and r.json()["success"] for r in res), [r.text[:200] for r in res]
    assert res[0].json()["channelId"] == res[3].json()["channelId"]
    assert [v["id"] for v in res[1].json()["videos"]] == [v["id"] for v in res[4].json()["videos"]]
