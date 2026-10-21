"""End-to-end checks of the TikTok live, sound and collection endpoints (anonymous).

/v1/tiktok/user/live, /v1/tiktok/live, /v1/tiktok/song, /v1/tiktok/song/videos and
/v1/tiktok/collection/videos: 24/7 live accounts vs offline ones, room_id/user_id ownership, licensed tracks
vs original sounds, cursor walks to the end, collection URL forms, parameter validation, contract
conformance, data accuracy against yt-dlp, cache_max_age, concurrency and error hygiene. Only the tests marked
live reach TikTok.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.contract import assert_conforms
from tests.e2e import LEAKS, app_with, mark_upstream, skip_if_blocked, spaced_get, ytdlp_info

LIVE_HANDLES = {"weathernewslive": "6621496731283095554", "tbsnewsdig": "6844446116312024066"}  # 24/7 streams
ENDED_HANDLES = ("lofigirl", "livenowfox")  # recent rooms that have ended (live page reports status 4)
STOOL = "stoolpresidente"
STOOL_UID = "6659752019493208069"
MISSING_USER = "zzqx_no_such_user_8472619"
ENDED_ROOM, ENDED_ROOM_UID = "7523685855395842871", "6742945285876515845"

LICENSED_SOUND = "6956990112127585029"  # "Build a B*tch" - Bella Poarch, many pages
SMALL_SOUND = "6689804660171082501"  # "original sound - tiffanie.yunque": 10 videos over 3 pages
STOOL_SOUND = "7499229837983222574"  # "original sound - stoolpresidente"

COLLECTION_ID = "7665668414573546258"
COLLECTION = f"https://www.tiktok.com/@kibblemaster808/collection/Want-to-go-{COLLECTION_ID}"  # 5 videos
MISSING_COLLECTION = "https://www.tiktok.com/@kibblemaster808/collection/x-7000000000000000001"

USER_LIVE = "/v1/tiktok/user/live"
LIVE = "/v1/tiktok/live"
SONG = "/v1/tiktok/song"
SONG_VIDEOS = "/v1/tiktok/song/videos"
COLLECTION_VIDEOS = "/v1/tiktok/collection/videos"

_APP_API = {"tiktok": {"app_info": [""]}}
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


# --- /v1/tiktok/user/live ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_now(client) -> tuple[str, str, dict]:
    """(handle, user id, body) of a 24/7 stream that is live right now."""
    for handle, uid in LIVE_HANDLES.items():
        body = ok(client, USER_LIVE, handle=handle)
        if body["is_live"]:
            return handle, uid, body
    pytest.skip("none of the 24/7 TikTok streams is live right now")


@pytest.mark.live
def test_user_live_live_account(live_now):
    handle, uid, body = live_now
    assert_conforms(body, USER_LIVE, min_coverage=0.9)
    user, room = body["liveRoomUserInfo"], body["liveRoom"]
    assert user["uniqueId"] == handle and user["id"] == uid and user["status"] == 2
    assert user["roomId"] and room["status"] == 2 and room["title"]


@pytest.mark.live
@pytest.mark.parametrize("form", ["@{h}", "https://www.tiktok.com/@{h}/live", "www.tiktok.com/@{h}",
                                  "https://m.tiktok.com/@{h}/live?lang=en#x", "  {h}  "])
def test_user_live_handle_forms(client, live_now, form):
    handle, uid, _ = live_now
    body = ok(client, USER_LIVE, handle=form.format(h=handle))
    assert body["is_live"] is True and body["liveRoomUserInfo"]["id"] == uid and body["liveRoom"]["status"] == 2


@pytest.mark.live
@pytest.mark.parametrize("variant", ["upper", "uid"])
def test_user_live_case_and_uid_handles_keep_live_room(client, live_now, variant):
    handle, uid, _ = live_now
    body = ok(client, USER_LIVE, handle=handle.upper() if variant == "upper" else uid)
    assert body["is_live"] is True
    assert body["liveRoomUserInfo"]["uniqueId"] == handle
    assert body["liveRoom"]["status"] == 2  # the live page is case sensitive; the fallback never adds liveRoom


@pytest.mark.live
def test_user_live_not_live(client):
    body = ok(client, USER_LIVE, handle=STOOL)
    assert_conforms(body, USER_LIVE)
    assert body["is_live"] is False and body["liveRoomUserInfo"]["uniqueId"] == STOOL
    assert body["liveRoomUserInfo"]["id"] == STOOL_UID
    assert body.get("liveRoom", {}).get("status") != 2


@pytest.mark.live
def test_user_live_ended_room_keeps_status_4(client):
    """An ended room keeps TikTok's live page data (liveRoom.status 4); is_live is the signal."""
    offline = [b for b in (ok(client, USER_LIVE, handle=h) for h in ENDED_HANDLES) if not b["is_live"]]
    if not offline:
        pytest.skip("every ended-room account is live again")
    for body in offline:
        assert body["liveRoomUserInfo"]["uniqueId"] in ENDED_HANDLES
        assert body.get("liveRoom", {}).get("status", 4) == 4, body["liveRoom"]


@pytest.mark.live
def test_user_live_missing_user(client):
    err(client, USER_LIVE, 404, "not_found", handle=MISSING_USER)


@pytest.mark.parametrize("handle", ["", "   ", "@", "a" * 81, "a/live",
                                    "https://www.youtube.com/@stoolpresidente",
                                    "https://www.instagram.com/stoolpresidente/",
                                    f"https://www.tiktok.com/@{STOOL}/video/7499229683859426602"])
def test_user_live_bad_handles(client, handle):
    err(client, USER_LIVE, 400, "bad_request", handle=handle)


def test_user_live_missing_param(client):
    assert err(client, USER_LIVE, 400, "bad_request")["message"] == "handle: Field required"


# --- /v1/tiktok/live --------------------------------------------------------------------------


@pytest.mark.live
def test_live_room_matches_ytdlp(client, live_now):
    handle, uid, body = live_now
    rid = body["liveRoomUserInfo"]["roomId"]
    room = ok(client, LIVE, room_id=rid, user_id=uid)
    assert_conforms(room, LIVE, min_coverage=0.8)
    assert room["room_id"] == rid and room["status"] == "live" and room["owner"]["id"] == uid
    assert room["owner"]["username"] == handle and isinstance(room["viewer_count"], int)
    info = ytdlp_info(f"https://www.tiktok.com/@{handle}/live")
    mark_upstream()
    assert info["id"] == rid and info["uploader_id"] == uid and info["uploader"] == handle
    assert info["title"].startswith(room["title"])
    # whitespace is stripped from both ids
    assert ok(client, LIVE, room_id=f" {rid} ", user_id=f" {uid} ")["room_id"] == rid


@pytest.mark.live
def test_live_room_owner_mismatch_is_404(client, live_now):
    _, uid, body = live_now
    rid = body["liveRoomUserInfo"]["roomId"]
    assert "does not belong" in err(client, LIVE, 404, "not_found", room_id=rid, user_id=STOOL_UID)["message"]
    err(client, LIVE, 404, "not_found", room_id=uid, user_id=rid)  # swapped


@pytest.mark.live
def test_live_ended_room(client):
    room = ok(client, LIVE, room_id=ENDED_ROOM, user_id=ENDED_ROOM_UID)
    assert room["status"] == "ended" and room["room_id"] == ENDED_ROOM and room["owner"]["id"] == ENDED_ROOM_UID


@pytest.mark.live
@pytest.mark.parametrize("room_id", ["1", "9223372036854775808"])
def test_live_unknown_room_is_404(client, room_id):
    err(client, LIVE, 404, "not_found", room_id=room_id, user_id=STOOL_UID)


@pytest.mark.parametrize("params", [{"user_id": STOOL_UID}, {"room_id": ENDED_ROOM},
                                    {"room_id": "", "user_id": STOOL_UID}, {"room_id": "abc", "user_id": STOOL_UID},
                                    {"room_id": "-5", "user_id": STOOL_UID}, {"room_id": "1" * 26, "user_id": "1"},
                                    {"room_id": ENDED_ROOM, "user_id": "1.0"}, {"room_id": ENDED_ROOM, "user_id": " "}])
def test_live_param_validation(client, params):
    err(client, LIVE, 400, "bad_request", **params)


# --- /v1/tiktok/song --------------------------------------------------------------------------


@pytest.mark.live
def test_song_licensed_track(client):
    body = ok(client, SONG, clipId=LICENSED_SOUND)
    assert_conforms(body, SONG, min_coverage=0.6)
    m = body["music_info"]
    assert m["id_str"] == LICENSED_SOUND and m["title"] == "Build a B*tch" and m["author"] == "Bella Poarch"
    assert m["is_original_sound"] is False and m["duration"] > 0
    assert "user_count" not in m or m["user_count"] > 0


@pytest.mark.live
def test_song_original_sound(client):
    m = ok(client, SONG, clipId=f" {STOOL_SOUND} ")["music_info"]
    assert m["id_str"] == STOOL_SOUND and m["is_original_sound"] is True
    assert m["owner_handle"] == STOOL and m["title"] == f"original sound - {STOOL}"


@pytest.mark.live
def test_song_from_its_page_url(client):
    # the sound page's address (TikTokSoundIE's own test URL), and the m. host without a title or scheme
    m = ok(client, SONG, clipId=f"https://www.tiktok.com/music/Build-a-Btch-{LICENSED_SOUND}?lang=en")["music_info"]
    assert m["id_str"] == LICENSED_SOUND and m["title"] == "Build a B*tch"
    page = ok(client, SONG_VIDEOS, clipId=f"m.tiktok.com/music/{SMALL_SOUND}/")
    assert page["aweme_list"] and {a["music"]["id_str"] for a in page["aweme_list"]} == {SMALL_SOUND}


@pytest.mark.parametrize("clip", ["", "  ", "abc", "-1", "1.0", "1" * 26, "６９５６",
                                  "https://www.tiktok.com/music/Build-a-Btch", f"https://www.tiktok.com/@{STOOL}",
                                  f"https://www.youtube.com/music/x-{LICENSED_SOUND}"])
def test_song_bad_clip_id(client, clip):
    err(client, SONG, 400, "bad_request", clipId=clip)


@pytest.mark.live
def test_song_missing_param_and_unknown_ids(client):
    assert err(client, SONG, 400, "bad_request")["message"] == "clipId: Field required"
    for clip in ("0", "1"):
        err(client, SONG, 404, "not_found", clipId=clip)


# --- /v1/tiktok/song/videos -------------------------------------------------------------------


def _walk_song(c, clip: str, max_pages: int, cursor: str | None = None) -> list[dict]:
    pages = []
    for _ in range(max_pages):
        page = ok(c, SONG_VIDEOS, clipId=clip, **({"cursor": str(cursor)} if cursor is not None else {}))
        pages.append(page)
        if not page["has_more"]:
            break
        cursor = page["cursor"]
    return pages


@pytest.mark.live
def test_song_videos_walk_to_end_matches_ytdlp(client):
    pages = _walk_song(client, SMALL_SOUND, 6)
    assert not pages[-1]["has_more"] and "cursor" not in pages[-1], "walk did not terminate"
    assert all("cursor" in p for p in pages[:-1]) and len(pages) >= 2
    ids = [a["aweme_id"] for p in pages for a in p["aweme_list"]]
    assert len(ids) == len(set(ids)), "duplicate videos across pages"
    assert {a["music"]["id_str"] for p in pages for a in p["aweme_list"]} == {SMALL_SOUND}
    info = ytdlp_info(f"https://www.tiktok.com/music/x-{SMALL_SOUND}", extract_flat=True, extractor_args=_APP_API)
    mark_upstream()
    assert [e["id"] for e in info["entries"]] == ids


@pytest.mark.live
def test_song_videos_licensed_pages(client):
    pages = _walk_song(client, LICENSED_SOUND, 2, cursor="0")  # cursor=0 is the first page
    for p in pages:
        assert_conforms(p, SONG_VIDEOS, min_coverage=0.7)
    ids = [a["aweme_id"] for p in pages for a in p["aweme_list"]]
    assert len(pages) == 2 and len(ids) == len(set(ids)) >= 20
    assert all(p["has_more"] and int(p["cursor"]) > 0 for p in pages)
    assert int(pages[1]["cursor"]) > int(pages[0]["cursor"])
    assert {a["music"]["id_str"] for p in pages for a in p["aweme_list"]} == {LICENSED_SOUND}


@pytest.mark.live
def test_song_videos_past_the_end_is_empty(client):
    body = ok(client, SONG_VIDEOS, clipId=SMALL_SOUND, cursor="999999999")
    assert body["aweme_list"] == [] and not body["has_more"] and "cursor" not in body


@pytest.mark.parametrize("cursor", ["abc", "-1", "36.0", "1" * 10, " 36 ", "²", "ss1.abc"])
def test_song_videos_bad_cursor_is_400(client, cursor):
    err(client, SONG_VIDEOS, 400, "bad_cursor", clipId=SMALL_SOUND, cursor=cursor)


@pytest.mark.live
def test_song_videos_param_validation(client):
    for params in ({}, {"clipId": ""}):
        assert err(client, SONG_VIDEOS, 400, "bad_request", **params)["message"] == "Provide one of: clipId"
    for clip in ("abc", "-1", f"https://www.tiktok.com/tag/x-{SMALL_SOUND}"):
        err(client, SONG_VIDEOS, 400, "bad_request", clipId=clip)
    err(client, SONG_VIDEOS, 404, "not_found", clipId="1")


# --- /v1/tiktok/collection/videos -------------------------------------------------------------


@pytest.fixture(scope="module")
def collection(client) -> dict:
    return ok(client, COLLECTION_VIDEOS, url=COLLECTION)


@pytest.mark.live
def test_collection_matches_ytdlp(collection):
    assert_conforms(collection, COLLECTION_VIDEOS, min_coverage=0.9)
    assert collection["collection_id"] == COLLECTION_ID and collection["status_code"] == 0
    assert collection["has_more"] is False and collection["max_cursor"] == str(len(collection["videos"]))
    ids = [v["id"] for v in collection["videos"]]
    assert ids and len(ids) == len(set(ids)) and all(v["author"]["uniqueId"] for v in collection["videos"])
    info = ytdlp_info(COLLECTION, extract_flat=True)
    mark_upstream()
    assert info["id"] == COLLECTION_ID and [e["id"] for e in info["entries"]] == ids


@pytest.mark.live
@pytest.mark.parametrize("url", [
    f"https://tiktok.com/@kibblemaster808/collection/Want-to-go-{COLLECTION_ID}/",
    f"m.tiktok.com/@kibblemaster808/collection/{COLLECTION_ID}?lang=en#top",
    f"  HTTPS://WWW.TIKTOK.COM/@someoneelse/collection/%F0%9F%90%88-{COLLECTION_ID}  ",
])
def test_collection_url_forms(client, collection, url):
    body = ok(client, COLLECTION_VIDEOS, url=url)
    assert body["collection_id"] == COLLECTION_ID
    assert [v["id"] for v in body["videos"]] == [v["id"] for v in collection["videos"]]


@pytest.mark.live
def test_collection_cursor_offsets(client, collection):
    ids = [v["id"] for v in collection["videos"]]
    page = ok(client, COLLECTION_VIDEOS, url=COLLECTION, cursor="2")
    assert [v["id"] for v in page["videos"]] == ids[2:] and page["has_more"] is False
    end = ok(client, COLLECTION_VIDEOS, url=COLLECTION, cursor=collection["max_cursor"])
    assert not end.get("videos") and end["has_more"] is False


@pytest.mark.parametrize("cursor", ["abc", "-1", "2.0", " 2 ", "1" * 10])
def test_collection_bad_cursor_is_400(client, cursor):
    err(client, COLLECTION_VIDEOS, 400, "bad_cursor", url=COLLECTION, cursor=cursor)


@pytest.mark.parametrize("path,params", [(SONG_VIDEOS, {"clipId": SMALL_SOUND}),
                                         (COLLECTION_VIDEOS, {"url": COLLECTION})])
def test_tampered_cursor_is_bad_cursor(client, path, params):
    err(client, path, 400, "bad_cursor", cursor="abc", **params)


@pytest.mark.parametrize("url", ["", "   ", COLLECTION_ID, "https://www.tiktok.com/@kibblemaster808",
                                 f"https://www.tiktok.com/@{STOOL}/video/7499229683859426602",
                                 f"https://www.tiktok.com/@kibblemaster808/collection/Want-to-go-{COLLECTION_ID}/x",
                                 f"https://www.tiktok.com/@{STOOL}/playlist/x-7090570072548461358",
                                 f"https://www.tiktok.com.evil.com/@a/collection/x-{COLLECTION_ID}",
                                 "https://www.youtube.com/playlist?list=PL0", "https://vm.tiktok.com/ZMkyTWLpV/"])
def test_collection_unsupported_urls(client, url):
    err(client, COLLECTION_VIDEOS, 400, "unsupported_url", url=url)


@pytest.mark.live
def test_collection_missing(client):
    assert err(client, COLLECTION_VIDEOS, 400, "bad_request")["message"] == "url: Field required"
    assert "100001" in err(client, COLLECTION_VIDEOS, 404, "not_found", url=MISSING_COLLECTION)["message"]


@pytest.mark.parametrize("cid", ["0", "00", "9223372036854775808", "9" * 30])
def test_collection_out_of_range_id_is_not_500(client, cid):
    err(client, COLLECTION_VIDEOS, 404, "not_found", url=f"https://www.tiktok.com/@a/collection/x-{cid}")


@pytest.mark.live
def test_collection_leading_zero_id_is_canonical(client, collection):
    body = ok(client, COLLECTION_VIDEOS, url=f"https://www.tiktok.com/@a/collection/x-00{COLLECTION_ID}")
    assert body["collection_id"] == COLLECTION_ID
    assert [v["id"] for v in body["videos"]] == [v["id"] for v in collection["videos"]]


# --- cache_max_age and concurrency ------------------------------------------------------------


@pytest.mark.live
def test_cache_max_age(tmp_path):
    with app_with({}, tmp_path) as c:
        for path, params in ((COLLECTION_VIDEOS, {"url": COLLECTION}),
                             (LIVE, {"room_id": ENDED_ROOM, "user_id": ENDED_ROOM_UID})):
            first = get(c, path, cache_max_age="1", **params)
            assert first.status_code == 200 and first.headers["x-cache"] == "miss", (path, first.text[:200])
            hit = get(c, path, cache_max_age="1d", **params)
            assert hit.headers["x-cache"] == "hit" and hit.json()["cached"] is True and hit.json()["cached_at"]
            assert {k: v for k, v in hit.json().items() if k not in ("cached", "cached_at")} == first.json()
        assert get(c, COLLECTION_VIDEOS, url=COLLECTION, cache_max_age="0").headers["x-cache"] == "miss"
        for _ in range(2):  # errors are never stored
            r = get(c, USER_LIVE, handle=MISSING_USER, cache_max_age="1")
            assert r.status_code == 404 and "x-cache" not in r.headers
        for bad in ("abc", "-1"):
            err(c, SONG, 400, "bad_request", clipId=STOOL_SOUND, cache_max_age=bad)


@pytest.mark.live
def test_concurrent_requests(client, live_now):
    handle, uid, body = live_now
    time.sleep(_SPACING)
    calls = [(USER_LIVE, {"handle": handle}), (LIVE, {"room_id": body["liveRoomUserInfo"]["roomId"], "user_id": uid}),
             (SONG, {"clipId": SMALL_SOUND}), (SONG_VIDEOS, {"clipId": LICENSED_SOUND, "cursor": "18"}),
             (COLLECTION_VIDEOS, {"url": COLLECTION, "cursor": "1"})]
    with ThreadPoolExecutor(5) as ex:
        results = list(ex.map(lambda p: client.get(p[0], params=p[1]), calls))
    mark_upstream()
    if any(r.status_code in (429, 503) for r in results):
        pytest.skip("TikTok throttled this IP")
    assert [r.status_code for r in results] == [200] * len(results), [r.text[:200] for r in results]
    assert results[0].json()["is_live"] is True and results[1].json()["status"] == "live"
    assert results[2].json()["music_info"]["id_str"] == SMALL_SOUND
    assert len(results[3].json()["aweme_list"]) > 0 and len(results[4].json()["videos"]) == 4
