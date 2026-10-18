"""End-to-end checks of the Instagram profile endpoints (anonymous). Only the tests marked live reach Instagram.

/v1/instagram/profile, /v1/instagram/profile/post-count, /v2/instagram/user/posts, /v1/instagram/user/reels,
/v1/instagram/user/highlights and /v1/instagram/user/highlight/detail: handle and id forms, parameter validation,
private / empty / abbreviated-count accounts, contract conformance, data accuracy against yt-dlp, cache and
error hygiene.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from yt_dlp.utils import DownloadError

from tests.contract import assert_conforms
from tests.e2e import LEAKS, app_with, skip_if_blocked, spaced_get, ytdlp_info

IG = "https://www.instagram.com"
JANE = "jane"
JANE_PK = "21393171"
HIGHLIGHT = "18382085791177637"  # jane's "Mems" highlight (photos and videos)
MISSING = "zzqx_no_such_user_8811"
PRIVATE = "krys_midnight"  # private account with posts
NO_POSTS = "lilaltomtas"  # 0 posts
ABBREVIATED = "natgeo"  # og:description shows "32K Posts"

PROFILE = "/v1/instagram/profile"
POST_COUNT = "/v1/instagram/profile/post-count"
POSTS = "/v2/instagram/user/posts"
REELS = "/v1/instagram/user/reels"
HIGHLIGHTS = "/v1/instagram/user/highlights"
DETAIL = "/v1/instagram/user/highlight/detail"
HANDLE_PATHS = [PROFILE, POST_COUNT, POSTS, REELS, HIGHLIGHTS]

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


def user(c, handle: str = JANE, **params) -> dict:
    return ok(c, PROFILE, handle=handle, **params)["data"]["user"]


# --- /v1/instagram/profile ---------------------------------------------------------------


@pytest.mark.live
def test_profile_conforms_and_is_consistent(client):
    body = ok(client, PROFILE, handle=JANE)
    assert_conforms(body, PROFILE, min_coverage=0.25)
    u = body["data"]["user"]
    assert u["id"] == JANE_PK and u["username"] == JANE and u["is_private"] is False and u["is_verified"] is True
    assert u["edge_followed_by"]["count"] > 1_000_000 and u["edge_follow"]["count"] >= 0
    media = u["edge_owner_to_timeline_media"]
    assert media["count"] > 1000  # jane shows an exact "1,8xx Posts"
    nodes = [e["node"] for e in media["edges"]]
    assert 1 <= len(nodes) <= 12 and len({n["id"] for n in nodes}) == len(nodes)
    assert all(n["shortcode"] and n["display_url"].startswith("https://") for n in nodes)
    assert media["page_info"]["has_next_page"] is True and media["page_info"]["end_cursor"]


@pytest.mark.live
@pytest.mark.parametrize("handle", [
    "@jane",
    "JANE",
    "m.instagram.com/jane/reels/?igsh=abc123#top",
])
def test_profile_handle_forms(client, handle):
    assert user(client, handle)["id"] == JANE_PK


@pytest.mark.live
def test_profile_dotted_and_underscored_handles(client):
    u = user(client, "the.whimsical.introvert")
    assert u["username"] == "the.whimsical.introvert" and u["id"].isdigit()
    count = ok(client, POST_COUNT, handle="__i_s_l_a___")
    assert count["handle"] == "__i_s_l_a___" and count["media_count"] >= 0 and count["is_estimated"] is False


@pytest.mark.live
def test_profile_trim_is_ignored(client):
    assert user(client, f"  {IG}/jane/ ", trim="true")["id"] == JANE_PK


@pytest.mark.live
def test_profile_abbreviated_count_is_omitted(client):
    u = user(client, ABBREVIATED)
    assert "count" not in u["edge_owner_to_timeline_media"]
    assert u["edge_followed_by"]["count"] > 100_000_000


@pytest.mark.live
def test_private_profile(client):
    u = user(client, PRIVATE)
    assert u["is_private"] is True and u["username"] == PRIVATE
    assert not u["edge_owner_to_timeline_media"].get("edges")


BAD_HANDLES = [
    "", " ", "@", "@jane@", "jane.", ".jane", "ja..ne", "jäne", "a" * 31, "jane?", "%40jane", "about", "explore",
    "www.instagram.com/@jane", f"{IG}/jane/p/xyz/", f"{IG}/p/DLDXI0fylTC/", f"{IG}/stories/jane/",
    "https://instagram.com.evil.com/jane/", "https://www.youtube.com/@jane", "javascript:alert(1)",
]


@pytest.mark.parametrize("handle", BAD_HANDLES)
def test_profile_rejects_bad_handles(client, handle):
    err(client, PROFILE, 400, "bad_handle", handle=handle)


@pytest.mark.parametrize("path", [POST_COUNT, POSTS, REELS, HIGHLIGHTS])
def test_other_endpoints_reject_bad_handles(client, path):
    for handle in (" ", "jane.", f"{IG}/p/DLDXI0fylTC/", "x" * 10_000):
        err(client, path, 400, "bad_handle", handle=handle)


@pytest.mark.parametrize("path", [PROFILE, POST_COUNT, POSTS])
def test_missing_handle(client, path):
    r = client.get(path)
    assert r.status_code == 400 and r.json()["success"] is False and r.json()["message"].startswith("handle")


@pytest.mark.live
@pytest.mark.parametrize("path", HANDLE_PATHS)
def test_nonexistent_handle_is_404(client, path):
    err(client, path, 404, "not_found", handle=MISSING)


@pytest.mark.live
def test_reserved_name_redirecting_off_instagram_is_404(client):
    err(client, PROFILE, 404, "not_found", handle="developer")


@pytest.mark.live
def test_profile_page_with_hashed_relay_module(client, monkeypatch):
    """Some profile pages (1 in 36 of jane's) name the relay module RelayPrefetchedStreamCache@<hash>; those used to
    parse to nothing and answer 500 no_profile_data (finding ig_profile-3). jane's real page, renamed that way."""
    from socialscrape.platforms import instagram as ig

    real, renamed = ig.with_ydl, []

    async def hashed_module(*args, **kwargs):
        html, final_url = await real(*args, **kwargs)
        renamed.append(html.count('"RelayPrefetchedStreamCache"'))
        hashed = '"RelayPrefetchedStreamCache\\u00402d59f5a78e6f242b0cf731dd819b49d5"'
        return html.replace('"RelayPrefetchedStreamCache"', hashed), final_url

    monkeypatch.setattr(ig, "with_ydl", hashed_module)
    status, body = raw(client, PROFILE, handle=JANE)
    if status in (429, 503):
        pytest.skip(f"Instagram throttled this IP: {body.get('message')}")
    assert renamed and renamed[0] > 0
    assert status == 200 and body["data"]["user"]["id"] == JANE_PK, body


# --- /v1/instagram/profile/post-count --------------------------------------------------------


@pytest.mark.live
def test_post_count_exact_matches_profile(client):
    body = ok(client, POST_COUNT, handle=f"{IG}/jane/")
    assert_conforms(body, POST_COUNT)
    assert body["handle"] == JANE and body["is_estimated"] is False
    assert abs(body["media_count"] - user(client)["edge_owner_to_timeline_media"]["count"]) <= 2


@pytest.mark.live
def test_post_count_abbreviated_needs_allow_estimated(client):
    body = err(client, POST_COUNT, 422, "estimate_only", handle=ABBREVIATED)
    estimate = body["estimated_media_count"]
    assert isinstance(estimate, int) and estimate >= 1000 and estimate % 1000 == 0
    assert "allow_estimated" in body["message"]
    got = ok(client, POST_COUNT, handle=ABBREVIATED, allow_estimated="1")
    assert got["is_estimated"] is True and got["media_count"] == estimate
    err(client, POST_COUNT, 422, "estimate_only", handle=ABBREVIATED, allow_estimated="false")


@pytest.mark.live
def test_post_count_private_and_empty_accounts(client):
    private = ok(client, POST_COUNT, handle=PRIVATE)
    assert private["media_count"] > 0 and private["is_estimated"] is False
    empty = ok(client, POST_COUNT, handle=NO_POSTS)
    assert empty["media_count"] == 0 and empty["is_estimated"] is False


# --- /v2/instagram/user/posts -------------------------------------------------------------


@pytest.mark.live
def test_user_posts_conforms_and_ids(client):
    body = ok(client, POSTS, handle=JANE)
    assert_conforms(body, POSTS, min_coverage=0.2)
    items = body["items"]
    assert 1 <= len(items) <= 12 and body["num_results"] == len(items)
    assert len({i["id"] for i in items}) == len(items)
    for item in items:
        assert item["id"].endswith(f"_{item['user']['pk']}") and item["owner"] == item["user"]
        assert item["url"] == f"{IG}/p/{item['code']}/" and item["media_type"] in (1, 2, 8)
    assert body["user"]["pk"] == JANE_PK and body["user"]["username"] == JANE
    assert any(i["user"]["pk"] == JANE_PK for i in items)
    for item in items:
        if "like_count" in item:
            assert item["product_type"] == "clips" and item["comment_count"] >= 0


@pytest.mark.live
def test_user_posts_counts_match_ytdlp(client):
    items = ok(client, POSTS, handle=JANE)["items"]
    item = next((i for i in items if i.get("like_count") and i.get("comment_count")), None)
    if item is None:
        pytest.skip("no reel with counts among jane's first 12 posts")
    time.sleep(_SPACING)
    try:
        info = ytdlp_info(item["url"])
    except DownloadError as exc:
        pytest.skip(f"yt-dlp could not load the post: {exc}")
    assert info["id"] == item["code"]
    assert item["like_count"] == pytest.approx(info["like_count"], rel=0.05)
    assert item["comment_count"] == pytest.approx(info["comment_count"], rel=0.05, abs=5)


def test_user_posts_pagination_is_501(client):
    for value in ("QVFE", "AQHTAKFzp1SVWlXrj2CA"):
        err(client, POSTS, 501, "pagination_unavailable", handle=JANE, next_max_id=value)


@pytest.mark.live
def test_user_posts_private_account(client):
    body = ok(client, POSTS, handle=PRIVATE)
    assert body["user"]["is_private"] is True and body["items"] == [] and body["num_results"] == 0


def test_user_posts_user_id_is_not_a_handle(client):
    r = client.get(POSTS, params={"user_id": JANE_PK})
    assert r.status_code == 400 and r.json()["success"] is False


# --- /v1/instagram/user/reels --------------------------------------------------------------


@pytest.mark.live
def test_reels_conform_and_belong_to_tab(client):
    body = ok(client, REELS, handle=JANE)
    assert_conforms(body, REELS, min_coverage=0.15)
    items = body["items"]
    assert 1 <= len(items) <= 12 and len({i["id"] for i in items}) == len(items)
    for item in items:
        assert item["url"] == f"{IG}/reel/{item['code']}/" and item["play_count"] == item["ig_play_count"]
        assert item["like_count"] >= 0 and item["comment_count"] >= 0
        assert item["id"].endswith(f"_{item['user']['pk']}")


@pytest.mark.live
def test_reels_handle_wins_over_user_id_and_empty_max_id(client):
    ids = [i["id"] for i in ok(client, REELS, handle=JANE)["items"]]
    assert [i["id"] for i in ok(client, REELS, handle=JANE, user_id="1", max_id="")["items"]] == ids


def test_reels_unsupported_params(client):
    err(client, REELS, 501, "pagination_unavailable", handle=JANE, max_id="QVFE")
    err(client, REELS, 501, "not_supported", user_id=JANE_PK)
    assert "user_id" in err(client, REELS, 400, user_id="", handle="")["message"]


@pytest.mark.live
@pytest.mark.parametrize("path", [REELS, HIGHLIGHTS])
def test_private_account_lists_say_private(client, path):
    err(client, path, 403, "forbidden", handle=PRIVATE)


# --- /v1/instagram/user/highlights ---------------------------------------------------------


@pytest.mark.live
def test_highlights_conform(client):
    body = ok(client, HIGHLIGHTS, handle=JANE)
    assert_conforms(body, HIGHLIGHTS, min_coverage=0.8)
    hl = body["highlights"]
    assert 1 <= len(hl) <= 10 and len({h["id"] for h in hl}) == len(hl)
    assert HIGHLIGHT in {h["id"] for h in hl}
    assert all(h["owner"]["id"] == JANE_PK and h["owner"]["username"] == JANE for h in hl)
    assert all(h["id"].isdigit() and h["cover_media_cropped_thumbnail"]["url"].startswith("https://") for h in hl)


def test_highlights_user_id(client):
    err(client, HIGHLIGHTS, 501, "not_supported", user_id=JANE_PK)
    r = client.get(HIGHLIGHTS)
    assert r.status_code == 400 and "handle" in r.json()["message"]


# --- /v1/instagram/user/highlight/detail ----------------------------------------------------


@pytest.mark.live
def test_highlight_detail_conforms_and_matches_ytdlp(client):
    body = ok(client, DETAIL, id=HIGHLIGHT)
    assert_conforms(body, DETAIL, min_coverage=0.6)
    assert body["id"] == f"highlight:{HIGHLIGHT}" and body["user"]["pk"] == JANE_PK
    items = body["items"]
    assert body["media_count"] == len(items) and len({i["pk"] for i in items}) == len(items)
    videos = [i for i in items if i["media_type"] == 2]
    assert all(v["video_versions"][0]["url"].startswith("https://") for v in videos)

    time.sleep(_SPACING)
    try:
        info = ytdlp_info(f"{IG}/stories/highlights/{HIGHLIGHT}/", extract_flat="in_playlist")
    except DownloadError as exc:
        pytest.skip(f"yt-dlp could not load the highlight: {exc}")
    entries = list(info["entries"])
    assert len(entries) == len(videos)
    assert {e["timestamp"] for e in entries} == {v["taken_at"] for v in videos}


@pytest.mark.live
@pytest.mark.parametrize("value", [
    f"highlight:{HIGHLIGHT}",
    f"  https://instagram.com/stories/highlights/{HIGHLIGHT}?igsh=abc  ",
])
def test_highlight_detail_id_forms(client, value):
    assert ok(client, DETAIL, id=value)["id"] == f"highlight:{HIGHLIGHT}"


@pytest.mark.parametrize("value", [
    " ", "abc", "-1", "1234", "1" * 20, "highlight:", "highlight:abc", f"HIGHLIGHT:{HIGHLIGHT}", f"{HIGHLIGHT}.0",
    "1.8382085791177637e16", "https://www.tiktok.com/@scout2015/video/6718335390845095173",
    f"{IG}/s/aGlnaGxpZ2h0OjE4MzgyMDg1NzkxMTc3NjM3/",
])
def test_highlight_detail_bad_ids(client, value):
    err(client, DETAIL, 400, "bad_id", id=value)


@pytest.mark.live
def test_highlight_detail_other_errors(client):
    err(client, DETAIL, 400, "unsupported_url", id=f"{IG}/stories/jane/{JANE_PK}/")
    assert "id" in err(client, DETAIL, 400, id="")["message"]
    r = client.get(DETAIL)
    assert r.status_code == 400 and "id" in r.json()["message"]
    err(client, DETAIL, 404, "not_found", id="17999999999999999")


def test_highlight_detail_non_ascii_digits_are_rejected(client):
    status, body = call(client, DETAIL, id="１８３８２０８５７９１１７７６３７")
    assert status == 400 and body["error"] == "bad_id", body


# --- cache, concurrency, docs --------------------------------------------------------------


@pytest.mark.live
def test_profile_cache_hit(tmp_path):
    with app_with({}, tmp_path) as c:
        params = {"handle": JANE, "cache_max_age": "1d"}
        first = spaced_get(c, PROFILE, _SPACING, **params)
        skip_if_blocked(first)
        assert first.status_code == 200 and first.headers.get("x-cache") != "hit"
        second = c.get(PROFILE, params={**params, "cache_max_age": "12h"})
        assert second.status_code == 200 and second.headers.get("x-cache") == "hit"
        b = second.json()
        assert b.pop("cached") is True and b.pop("cached_at").endswith("Z")
        assert b == first.json()
        for bad in ("abc", "-1d", "1w"):
            assert c.get(PROFILE, params={**params, "cache_max_age": bad}).status_code == 400
        time.sleep(_SPACING)
        missing = [c.get(PROFILE, params={"handle": MISSING, "cache_max_age": "1d"}) for _ in range(2)]
        if any(r.status_code in (429, 503) for r in missing):
            pytest.skip("Instagram throttled this IP")
        assert [r.status_code for r in missing] == [404, 404]
        assert all(r.headers.get("x-cache") != "hit" for r in missing)


@pytest.mark.live
def test_parallel_requests(client):
    calls = [(PROFILE, {"handle": JANE}), (REELS, {"handle": JANE}), (HIGHLIGHTS, {"handle": JANE}),
             (PROFILE, {"handle": "@jane"})]
    time.sleep(_SPACING)
    start = time.monotonic()
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda p: client.get(p[0], params=p[1]), calls))
    if any(r.status_code in (429, 503) for r in results):
        pytest.skip("Instagram throttled this IP")
    assert [r.status_code for r in results] == [200] * len(results), [r.json() for r in results]
    assert time.monotonic() - start < 60
    assert all(r.json()["success"] is True and r.json()["credits_charged"] == 0 for r in results)
    assert results[0].json()["data"]["user"]["id"] == results[3].json()["data"]["user"]["id"] == JANE_PK
