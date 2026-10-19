"""Live QA of the YouTube video slice: /video, /transcript, /comments, /comment/replies, /sponsors, /community-post.

Anonymous only. Asserts invariants, not volatile values; throttling (429/503) skips. Only the tests marked live
reach YouTube.
"""

from __future__ import annotations

import concurrent.futures as cf
import re

import pytest

from tests.contract import check
from tests.e2e import LEAKS, app_with, skip_if_blocked, ytdlp_info

ZOO = "jNQXAC9IVRw"  # "Me at the zoo": uploaded en captions, ~10.6M comments, auto chapters
SPONSORED = "AVO0ifle-OU"  # paid-promotion badge, BetterHelp, 6 auto chapters, en ASR
CHAPTERS = "rfscVS0vtbw"  # freeCodeCamp Python course: 35 description chapters, uploaded subs only
SHORT = "AwMy11R3au0"  # a Short without captions
SPANISH = "I1CXqUxhBj4"  # Spanish original, uploaded es/fr/pt tracks
DUBBED = "mx7MdPdn_a8"  # HikakinTV: Japanese original, YouTube auto-dubs (en-US-orig and ja-orig ASR tracks)
DUBBED_EN = "RU6gEobXVHA"  # NASA stream: English original auto-dubbed into ~20 languages (ar-orig listed first)
MEGA = "kJQP7kiw5Fk"  # Despacito: its watch-next videos have billions of views
LIVE = "M3HKLzjvKPc"  # NASA ISS 24/7 live stream
MEMBERS = "xCvqBqurBlA"  # LTT members-only
MISSING = "Qq7xZ0nonex"
AGE_GATED = "HtVdAasjOgU"
PRIVATE = "yZIXLfi8CZQ"

POSTS = {  # type -> post id
    "text": "Ugkx3w8sfDJf_NDdhUAtu7MTLX3d_Dt1V9UK",  # MrBeast
    "image": "Ugkx1LonSRBBUqASv-J8j9_FesxwlMAhT3_e",  # MrBeast
    "multi_image": "Ugkx5QH9-Xr0EWbVJ1riARjVrDcXRN1LfJS7",  # MrBeast, 10 images
    "poll": "UgkxMxGZBPcP7px-s1AKSLNO5WfP6MIygiLS",  # MrBeast text poll
    "image_poll": "UgkxHhAuxbqCIMhIlu3WE-HIJAm76kVRh6Lh",  # TED-Ed
    "quiz": "UgkxjrWd9f9ONQ9-FNGgQSoDAT0p8GwXeNZD",  # TED-Ed
    "video": "UgkxCvzxVJBjqrPkk_iT4rRQlWyIU_jKieBY",  # @YouTube, links rz1B4B2yBZY
    "playlist": "UgkxfcOOhlmFIvxIFyxTxk9e2E85h0HQbpSl",  # TED-Ed
}
MISSING_POST = "UgkxAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z")


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


def no_mismatch(body: dict, sc_path: str) -> None:
    report = check(body, sc_path)
    assert not report.mismatches, report.mismatches


# --- /v1/youtube/video ---------------------------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("url", [
    f"https://youtu.be/{SPONSORED}",
    f"https://m.youtube.com/watch?v={SPONSORED}&t=42s&si=x",
    f"https://www.youtube.com/embed/{SPONSORED}",
    f"https://www.youtube.com/live/{SPONSORED}",
    f"youtube.com/watch?v={SPONSORED}",
    f"  {SPONSORED}  ",
])
def test_video_url_forms(client, url):
    assert ok(client, "/v1/youtube/video", url=url)["id"] == SPONSORED


@pytest.mark.parametrize("url", [
    "", " ", "not a url", "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
    "https://www.youtube.com/@MrBeast", "https://www.tiktok.com/@scout2015/video/6718335390845095173",
    "https://www.instagram.com/p/C1/", "https://vimeo.com/76979871", "https://www.youtube.com.evil.com/watch?v=" + ZOO,
    "javascript:alert(1)", "https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=" + "é" * 11,
    f"https://www.youtube.com/post/{POSTS['text']}", "x" * 5000,
])
def test_video_rejects_non_video_urls(client, url):
    err(client, "/v1/youtube/video", 400, "unsupported_url", url=url)


def test_video_missing_url(client):
    assert err(client, "/v1/youtube/video", 400, "bad_request")["message"] == "url: Field required"


@pytest.mark.live
def test_video_matches_ytdlp_and_contract(client):
    body = ok(client, "/v1/youtube/video", url=watch(CHAPTERS))
    no_mismatch(body, "/v1/youtube/video")
    info = ytdlp_info(watch(CHAPTERS), ignore_no_formats_error=True)
    assert body["title"] == info["title"] and body["description"] == info["description"]
    assert body["durationMs"] == info["duration"] * 1000
    assert body["channel"]["id"] == info["channel_id"]
    assert body["keywords"] == info["tags"] and body["genre"] == info["categories"][0]
    assert abs(body["viewCountInt"] - info["view_count"]) <= max(1000, info["view_count"] // 1000)
    assert abs(body["likeCountInt"] - info["like_count"]) <= max(100, info["like_count"] // 1000)
    # exact, where yt-dlp's comment_count is the rounded header ('46K')
    assert body["commentCountInt"] >= info["comment_count"] * 0.9
    chapters = [(c["title"], int(c["start_time"] * 1000), int(c["end_time"] * 1000)) for c in info["chapters"]]
    assert [(c["title"], c["startMs"], c["endMs"]) for c in body["chapters"]] == chapters
    assert body["type"] == "video" and body["isPaidPromotion"] is False


@pytest.mark.live
def test_video_kinds(client):
    short = ok(client, "/v1/youtube/video", url=f"https://www.youtube.com/shorts/{SHORT}")
    assert short["type"] == "short" and short["chapters"] == [] and short["durationMs"] > 0
    sponsored = ok(client, "/v1/youtube/video", url=watch(SPONSORED))
    if not sponsored["chapters"]:
        # its chapters are YouTube's auto chapters (none in the description), now and then missing from a watch page
        # (2026-10-19: none once, then 6 on five fetches in a row); yt-dlp's chapters are passed through as they are
        sponsored = ok(client, "/v1/youtube/video", url=watch(SPONSORED))
    assert sponsored["isPaidPromotion"] is True and len(sponsored["chapters"]) >= 2
    starts = [c["startMs"] for c in sponsored["chapters"]]
    assert starts == sorted(starts) and starts[0] == 0
    assert all(a["endMs"] == b["startMs"] for a, b in zip(sponsored["chapters"], sponsored["chapters"][1:]))
    live = ok(client, "/v1/youtube/video", url=f"https://www.youtube.com/live/{LIVE}")
    assert live["id"] == LIVE and live["durationMs"] is None


@pytest.mark.live
@pytest.mark.parametrize(("vid", "status", "code"), [
    (PRIVATE, 403, "forbidden"), (MISSING, 404, "not_found"), (AGE_GATED, 403, "age_restricted"),
])
def test_video_unavailable(client, vid, status, code):
    err(client, "/v1/youtube/video", status, code, url=watch(vid))


@pytest.mark.parametrize("language", ["123", "en_US", "e", "es;q=0.9", "abcdefghijklmnop"])
def test_video_invalid_language(client, language):
    err(client, "/v1/youtube/video", 400, "bad_request", url=watch(ZOO), language=language)


@pytest.mark.live
def test_video_language_localizes(client):
    es = ok(client, "/v1/youtube/video", url=watch(ZOO), language="es")
    assert es["id"] == ZOO and es["commentCountInt"] > 10_000_000
    assert ok(client, "/v1/youtube/video", url=watch(ZOO), language="xx")["id"] == ZOO  # unknown -> English


@pytest.mark.live
def test_video_comment_count_exact_in_korean(client):
    en = ok(client, "/v1/youtube/video", url=watch(ZOO))["commentCountInt"]
    ko = ok(client, "/v1/youtube/video", url=watch(ZOO), language="ko")
    assert abs(ko["commentCountInt"] - en) < 1000, (ko["commentCountText"], ko["commentCountInt"], en)


@pytest.mark.live
def test_watch_next_view_counts_in_japanese(client):
    items = ok(client, "/v1/youtube/video", url=watch(ZOO), language="ja")["watchNextVideos"]
    with_text = [w for w in items if w.get("viewCountText")]
    if not with_text:
        pytest.skip("no watch-next item carried a view count")
    assert all(w["viewCountInt"] is not None for w in with_text), [w["viewCountText"] for w in with_text][:3]


@pytest.mark.live
@pytest.mark.parametrize("language", ["pt", "da", "fr-CA", "ar", "am", "hi", "th"])
def test_watch_next_billion_view_counts_and_dates(client, language):
    items = ok(client, "/v1/youtube/video", url=watch(MEGA), language=language)["watchNextVideos"]
    views = [w for w in items if w.get("viewCountText")]
    if not views:
        pytest.skip("no watch-next item carried a view count")
    assert all(w["viewCountInt"] is not None for w in views), [w["viewCountText"] for w in views
                                                                if w["viewCountInt"] is None][:3]
    dated = [w for w in items if w.get("publishedTimeText")]
    assert all(w["publishedTime"] for w in dated), [w["publishedTimeText"] for w in dated if not w["publishedTime"]]


@pytest.mark.live
@pytest.mark.parametrize("language", ["fr", "ru", "ko", "am", "si"])
def test_video_like_count_exact_in_other_languages(client, language):
    en = ok(client, "/v1/youtube/video", url=watch(ZOO))["likeCountInt"]
    other = ok(client, "/v1/youtube/video", url=watch(ZOO), language=language)
    assert other["likeCountInt"] is not None and abs(other["likeCountInt"] - en) <= max(100, en // 1000), (
        language, other["likeCountInt"], en)


# --- /v1/youtube/video/transcript ----------------------------------------------------------------------------------


@pytest.mark.live
def test_transcript_default_segments(client):
    body = ok(client, "/v1/youtube/video/transcript", url=f"https://youtu.be/{SPONSORED}")
    no_mismatch(body, "/v1/youtube/video/transcript")
    assert body["videoId"] == SPONSORED and body["type"] == "video" and body["language"].startswith("English")
    segs = body["transcript"]
    assert len(segs) > 100
    starts, ends = [int(s["startMs"]) for s in segs], [int(s["endMs"]) for s in segs]
    assert starts == sorted(starts) and all(e >= s for s, e in zip(starts, ends))
    assert all(ends[i] <= starts[i + 1] for i in range(len(segs) - 1))  # endMs clipped to the next start
    assert all(s["text"].strip() and re.fullmatch(r"\d+:\d\d(:\d\d)?", s["startTimeText"]) for s in segs)
    assert body["transcript_only_text"] == " ".join(s["text"] for s in segs)


@pytest.mark.live
def test_transcript_language_and_original_audio(client):
    path, url = "/v1/youtube/video/transcript", watch(SPANISH)
    assert ok(client, path, url=url)["language"].startswith("Spanish")
    for lang in ("es", "ES", "es-419"):
        assert ok(client, path, url=url, language=lang)["language"].startswith("Spanish")
    assert ok(client, path, url=url, language="fr")["language"].startswith("French")
    assert ok(client, path, url=url, language="pt-BR")["language"].startswith("Portuguese")
    # original_audio wins over language
    assert ok(client, path, url=url, language="fr", original_audio="true")["language"].startswith("Spanish")
    assert ok(client, path, url=url, language="fr", original_audio="false")["language"].startswith("French")
    # only a machine translation exists: null (documented)
    nothing = ok(client, path, url=url, language="de")
    assert nothing["transcript"] is None and nothing["language"] is None


@pytest.mark.live
def test_transcript_uploaded_only_and_no_captions(client):
    zoo = ok(client, "/v1/youtube/video/transcript", url=watch(ZOO))
    assert zoo["language"].startswith("English") and zoo["transcript"]
    orig = ok(client, "/v1/youtube/video/transcript", url=watch(ZOO), original_audio="1")
    assert orig["transcript"] is None  # no ASR track in the spoken language (documented)
    short = ok(client, "/v1/youtube/video/transcript", url=f"https://www.youtube.com/shorts/{SHORT}")
    assert short["type"] == "short" and short["transcript"] is None and short["transcript_only_text"] is None


@pytest.mark.parametrize("params", [
    {"language": "123"}, {"language": "en_US"}, {"original_audio": "maybe"}, {"original_audio": "2"},
])
def test_transcript_invalid_params(client, params):
    err(client, "/v1/youtube/video/transcript", 400, "bad_request", url=watch(ZOO), **params)


@pytest.mark.parametrize(("url", "status", "code"), [
    pytest.param(watch(PRIVATE), 403, "forbidden", marks=pytest.mark.live),
    pytest.param(watch(MISSING), 404, "not_found", marks=pytest.mark.live),
    ("https://www.tiktok.com/@a/video/123", 400, "unsupported_url"), ("", 400, "unsupported_url"),  # no request
])
def test_transcript_errors(client, url, status, code):
    err(client, "/v1/youtube/video/transcript", status, code, url=url)


@pytest.mark.live
def test_transcript_dubbed_video_picks_original_audio(client):
    body = ok(client, "/v1/youtube/video/transcript", url=watch(DUBBED), original_audio="true")
    assert (body["language"] or "").startswith("Japanese"), body["language"]
    assert ok(client, "/v1/youtube/video/transcript", url=watch(DUBBED))["language"].startswith("Japanese")


@pytest.mark.live
def test_transcript_dubbed_english_video_default_is_english(client):
    body = ok(client, "/v1/youtube/video/transcript", url=watch(DUBBED_EN))
    assert (body["language"] or "").startswith("English"), body["language"]


@pytest.mark.live
def test_transcript_bare_en_matches_regional_asr(client):
    """yt_videos-3: language=en gets the real en-US track, not YouTube's (throttled) machine translation."""
    regional = ok(client, "/v1/youtube/video/transcript", url=watch(DUBBED), language="en-US")
    bare = ok(client, "/v1/youtube/video/transcript", url=watch(DUBBED), language="en")
    assert regional["transcript"] and bare["transcript"]
    assert bare["language"] == regional["language"] and bare["language"].startswith("English"), bare["language"]


# --- /v1/youtube/video/comments + /comment/replies -----------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("order", ["top", "newest"])
def test_comments_pagination(client, order):
    seen, token = [], None
    for _ in range(3):
        params = {"url": watch(ZOO), "order": order} | ({"continuationToken": token} if token else {})
        body = ok(client, "/v1/youtube/video/comments", **params)
        no_mismatch(body, "/v1/youtube/video/comments")
        assert body["comments"]
        assert all(c["replyLevel"] == 0 and c["author"]["channelId"].startswith("UC") for c in body["comments"])
        assert all(ISO.fullmatch(c["publishedTime"]) for c in body["comments"])
        seen += [c["id"] for c in body["comments"]]
        token = body["continuationToken"]
        assert token
    assert len(seen) == len(set(seen)) >= 40
    if order == "newest":
        times = [c["publishedTime"] for c in body["comments"]]
        assert times == sorted(times, reverse=True)


@pytest.mark.parametrize("order", ["oldest", "popular", "1", "new"])
def test_comments_invalid_order(client, order):
    err(client, "/v1/youtube/video/comments", 400, "bad_request", url=watch(ZOO), order=order)


@pytest.mark.live
def test_comments_order_is_case_insensitive(client):
    assert ok(client, "/v1/youtube/video/comments", url=watch(ZOO), order=" NEWEST ")["comments"]


@pytest.mark.parametrize("token", ["garbage", " ", "A" * 4000, "Eg0SC0FWTzBpZmxlLU9V"])
def test_comments_bad_cursor(client, token):
    err(client, "/v1/youtube/video/comments", 400, "bad_cursor", url=watch(ZOO), continuationToken=token)


@pytest.mark.live
def test_replies_walk_and_cursors(client):
    top = ok(client, "/v1/youtube/video/comments", url=watch(SPONSORED))
    parent = next((c for c in top["comments"] if c.get("repliesContinuationToken")), None)
    if parent is None:
        pytest.skip("no top comment with replies on this page")
    # a comments token is not a replies token, and vice versa
    err(client, "/v1/youtube/video/comment/replies", 400, "bad_cursor", continuationToken=top["continuationToken"])
    err(client, "/v1/youtube/video/comments", 400, "bad_cursor", url=watch(SPONSORED),
        continuationToken=parent["repliesContinuationToken"])
    # a replies token of SPONSORED used against another video
    err(client, "/v1/youtube/video/comments", 400, "bad_cursor", url=watch(ZOO),
        continuationToken=parent["repliesContinuationToken"])
    seen, token = [], parent["repliesContinuationToken"]
    for _ in range(2):
        body = ok(client, "/v1/youtube/video/comment/replies", continuationToken=token)
        no_mismatch(body, "/v1/youtube/video/comment/replies")
        assert body["comments"]
        assert all(r["replyLevel"] == 1 and r["id"].startswith(parent["id"] + ".") for r in body["comments"])
        assert all("repliesContinuationToken" not in r for r in body["comments"])
        seen += [r["id"] for r in body["comments"]]
        token = body["continuationToken"]
        if not token:
            break
    assert len(seen) == len(set(seen))
    if token is None:
        # the last page was reached: YouTube's reply count can include replies it doesn't serve (deleted or held
        # for review; 2026-10-19 on this video: 11 counted/10 served, 25/24, 85/84), so allow a few
        counted = parent["engagement"]["replies"]
        assert len(seen) >= counted - max(2, counted // 10), (counted, len(seen))


def test_replies_missing_and_bad_token(client):
    assert err(client, "/v1/youtube/video/comment/replies", 400, "bad_request")["message"].startswith(
        "continuationToken")
    err(client, "/v1/youtube/video/comment/replies", 400, "bad_cursor", continuationToken="not-a-token")


@pytest.mark.live
@pytest.mark.parametrize(("vid", "status"), [(PRIVATE, 403), (MISSING, 404)])
def test_comments_unavailable(client, vid, status):
    err(client, "/v1/youtube/video/comments", status, url=watch(vid))


@pytest.mark.live
def test_comments_gated_videos_are_empty_pages(client):
    for vid in (MEMBERS,):
        body = ok(client, "/v1/youtube/video/comments", url=watch(vid))
        assert body["comments"] == [] and body["continuationToken"] is None


# --- /v1/youtube/video/sponsors ------------------------------------------------------------------------------------


@pytest.mark.live
def test_sponsors_found_and_not_found(client):
    body = ok(client, "/v1/youtube/video/sponsors", url=f"https://youtu.be/{SPONSORED}")
    no_mismatch(body, "/v1/youtube/video/sponsors")
    assert body["video"]["id"] == SPONSORED and body["video"]["isPaidPromotion"] is True
    names = {s["website"] for s in body["suspectedSponsors"]}
    assert "betterhelp.com" in names
    det = body["sponsorDetection"]
    assert det["status"] == "found" and det["transcriptAvailable"] is True and "paid promotion badge" in det["methods"]
    zoo = ok(client, "/v1/youtube/video/sponsors", url=watch(ZOO), language="es")
    assert zoo["suspectedSponsors"] == [] and zoo["sponsorDetection"]["status"] == "not_found"


@pytest.mark.live
def test_sponsors_errors(client):
    err(client, "/v1/youtube/video/sponsors", 400, "bad_request", url=watch(ZOO), language="123")
    err(client, "/v1/youtube/video/sponsors", 400, "bad_request")
    err(client, "/v1/youtube/video/sponsors", 400, "unsupported_url", url="https://www.tiktok.com/@a/video/1")
    err(client, "/v1/youtube/video/sponsors", 403, "forbidden", url=watch(PRIVATE))
    err(client, "/v1/youtube/video/sponsors", 404, "not_found", url=watch(MISSING))


# --- /v1/youtube/community-post ------------------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("kind", list(POSTS))
def test_community_post_types(client, kind):
    pid = POSTS[kind]
    body = ok(client, "/v1/youtube/community-post", url=post(pid))
    no_mismatch(body, "/v1/youtube/community-post")
    assert body["id"] == pid
    assert body["channel"]["id"].startswith("UC") and body["channel"]["handle"]
    assert body["content"] and isinstance(body["likeCount"], int) and body["likeCount"] >= 0
    assert ISO.fullmatch(body["publishedTime"]) and body["publishedTimeText"]
    images = body["images"]
    assert all(u.startswith("https://") for u in images)
    if kind == "image":
        assert len(images) == 1
    elif kind == "multi_image":
        assert len(images) > 1
    if kind == "video":
        assert body["video"]["id"] and body["video"]["url"].endswith(body["video"]["id"]) and body["video"]["title"]
    else:
        assert body["video"] is None


@pytest.mark.live
@pytest.mark.parametrize("url", [
    f"https://m.youtube.com/post/{POSTS['image']}", f"youtube.com/post/{POSTS['image']}",
    f"https://www.youtube.com/post/{POSTS['image']}/?si=x",
    f"https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA/community?lb={POSTS['image']}",
    f"https://www.youtube.com/@MrBeast/posts?lb={POSTS['image']}",
])
def test_community_post_url_forms(client, url):
    assert ok(client, "/v1/youtube/community-post", url=url)["id"] == POSTS["image"]


@pytest.mark.parametrize("url", [
    "", POSTS["image"], "https://www.youtube.com/post/", "https://www.youtube.com/post/Ugkx", watch(ZOO),
    f"https://evil.com/post/{POSTS['image']}", "https://www.instagram.com/p/abc/",
])
def test_community_post_bad_urls(client, url):
    err(client, "/v1/youtube/community-post", 400, "unsupported_url", url=url)


@pytest.mark.live
def test_community_post_missing(client):
    err(client, "/v1/youtube/community-post", 404, "not_found", url=post(MISSING_POST))
    err(client, "/v1/youtube/community-post", 400, "bad_request")


# --- cache_max_age, concurrency ------------------------------------------------------------------------------------


@pytest.fixture
def cached_client(tmp_path):
    with app_with({}, tmp_path) as c:
        yield c


@pytest.mark.live
def test_cache_max_age(cached_client):
    c = cached_client
    assert c.get("/health").json()["cache"] is True
    strip = lambda b: {k: v for k, v in b.items() if k not in ("cached", "cached_at")}  # noqa: E731
    path, url = "/v1/youtube/video/transcript", watch(SPANISH)
    first = ok(c, path, url=url, cache_max_age="1d")
    assert "cached" not in first
    hit = ok(c, path, url=url, cache_max_age="7d")
    assert hit["cached"] is True and ISO.fullmatch(hit["cached_at"]) and strip(hit) == first
    assert ok(c, path, url=url, cache_max_age="12h")["cached"] is True
    fr = ok(c, path, url=url, cache_max_age="1d", language="fr")  # other params: own entry
    assert "cached" not in fr and fr["language"].startswith("French")
    assert ok(c, path, url=url, cache_max_age="1d", language="fr")["language"].startswith("French")
    assert "cached" not in ok(c, path, url=url, cache_max_age="0")
    for bad in ("-1d", "abc", "1w", "1.5d"):
        err(c, path, 400, "bad_request", url=url, cache_max_age=bad)
    # errors are not cached
    for _ in range(2):
        err(c, "/v1/youtube/community-post", 404, "not_found", url=post(MISSING_POST), cache_max_age="1d")
    # comment pages: the token is part of the key
    p1 = ok(c, "/v1/youtube/video/comments", url=watch(ZOO), cache_max_age="1d")
    p2 = ok(c, "/v1/youtube/video/comments", url=watch(ZOO), cache_max_age="1d",
            continuationToken=p1["continuationToken"])
    assert "cached" not in p2 and {x["id"] for x in p1["comments"]}.isdisjoint(x["id"] for x in p2["comments"])
    newest = ok(c, "/v1/youtube/video/comments", url=watch(ZOO), cache_max_age="1d", order="newest")
    assert "cached" not in newest


@pytest.mark.live
def test_parallel_requests(client):
    calls = [
        ("/v1/youtube/video", {"url": watch(SPONSORED)}),
        ("/v1/youtube/video", {"url": watch(SPONSORED)}),
        ("/v1/youtube/video/transcript", {"url": watch(SPONSORED)}),
        ("/v1/youtube/community-post", {"url": post(POSTS["text"])}),
    ]
    with cf.ThreadPoolExecutor(len(calls)) as pool:
        responses = list(pool.map(lambda c: client.get(c[0], params=c[1]), calls))
    for r in responses:
        skip_if_blocked(r)
        assert r.status_code == 200, r.text[:200]
    a, b = (r.json() for r in responses[:2])
    assert a["title"] == b["title"] and a["chapters"] == b["chapters"] and a["id"] == b["id"] == SPONSORED
