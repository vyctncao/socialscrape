"""/v1/tiktok/search/keyword and /hashtag from TikTok's public keyword pages (tiktok_search), offline: answers recorded
from /api/seo/kap/video_list/ (tests/fixtures/tiktok_search, trimmed to three posts) and the failure shapes TikTok
throttles with (an empty 200 body, a challenge page, HTTP 429)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError

from socialscrape.errors import UpstreamBlocked, UpstreamError
from socialscrape.platforms import tiktok_private, tiktok_search as S
from tests.contract import assert_conforms

FIXTURES = Path(__file__).parent / "fixtures" / "tiktok_search"
KEYWORD, HASHTAG = "/v1/tiktok/search/keyword", "/v1/tiktok/search/hashtag"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


PAGE = fixture("kap-video-list-travel.json")  # 3 posts, hasMore true; only the second carries #travel
NO_PAGE = fixture("kap-no-page.json")  # status_code 4: what "fitness" gets
INVALID = fixture("kap-invalid-parameters.json")  # status_code 5
CHALLENGE = "<!DOCTYPE html><html><head><title>Please wait...</title></head></html>"


def page(**changes) -> str:
    body = {**json.loads(PAGE), **changes}
    return json.dumps(body)


def http_error(status: int) -> ExtractorError:
    response = Response(io.BytesIO(b""), S._KAP, {}, status=status)
    return ExtractorError(f"HTTP Error {status}", cause=HTTPError(response))


class FakeIE:
    """Answers _download_webpage with the scripted bodies (an exception is raised), recording each query."""

    def __init__(self, *answers):
        self.answers, self.queries = list(answers), []

    def _download_webpage(self, url, video_id, note, query, impersonate, headers):
        assert url == S._KAP and impersonate and headers["Referer"].endswith(f"/discover/{query['keyword']}")
        self.queries.append(query)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeYdl:
    def __init__(self, ie):
        self.ie = ie

    def get_info_extractor(self, name):
        assert name == "TikTok"
        return self.ie


@pytest.fixture
def answer(monkeypatch):
    """answer(*bodies) -> the FakeIE the routes' with_ydl will run on."""
    monkeypatch.setattr(S, "_RETRY_DELAY", 0)
    S._sessions.clear()
    current = {}

    async def with_ydl(platform, fn, **opts):
        assert platform == "tiktok"
        return fn(FakeYdl(current["ie"]))

    monkeypatch.setattr(S, "with_ydl", with_ydl)

    def install(*answers):
        current["ie"] = FakeIE(*answers)
        return current["ie"]

    yield install
    S._sessions.clear()


def ok(client, path, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text[:300]
    return r.json()


# --- params -----------------------------------------------------------------------

def test_slug_spells_keywords_as_keyword_pages_do():
    assert S.slug("Skincare routine for oily skin") == "skincare-routine-for-oily-skin"
    assert S.slug("What's in my bag") == S.slug("What’s in my bag") == "whats-in-my-bag"
    assert S.slug("  #Travel  ") == "travel" and S.slug("how_to  make--pasta") == "how-to-make-pasta"
    assert S.slug("旅行") == "旅行" and S.slug("!!!") == ""


@pytest.mark.parametrize("path, params, code", [
    (KEYWORD, {"query": "travel", "region": "USA"}, "bad_request"),
    (KEYWORD, {"query": "travel", "date_posted": "never"}, "bad_request"),
    (KEYWORD, {"query": "travel", "sort_by": "views"}, "bad_request"),
    (KEYWORD, {"query": "travel", "cursor": "next"}, "bad_cursor"),
    (KEYWORD, {"query": "!!!"}, "bad_request"),
    (HASHTAG, {"hashtag": "#"}, "bad_request"),
    (HASHTAG, {"hashtag": "travel", "cursor": "-30"}, "bad_cursor"),
])
def test_bad_params_are_400_before_asking_tiktok(client, answer, path, params, code):
    ie = answer()
    r = client.get(path, params=params)
    assert (r.status_code, r.json()["error"]) == (400, code), r.text[:200]
    assert ie.queries == []


# --- keyword ------------------------------------------------------------------------

def test_keyword_search_maps_a_recorded_page(client, answer):
    ie = answer(PAGE)
    body = ok(client, KEYWORD, query="Travel", region="gb")
    assert ie.queries == [{"pageType": 7, "appId": 1233, "aid": 1988, "trafficType": 0, "keyword": "travel",
                           "count": 30, "offset": 0, "region": "GB"}]
    assert_conforms(body, KEYWORD, min_coverage=0.2)
    assert body["cursor"] == 30 and len(body["search_item_list"]) == 3
    first = body["search_item_list"][0]
    assert "aweme_info" not in first  # the aweme itself, as ScrapeCreators' example has it
    assert first["aweme_id"] == "7690818986950380821"
    assert first["url"] == "https://www.tiktok.com/@vietnam_expresstour/video/7690818986950380821"
    assert first["create_time"] == 1790658343 and first["desc"].startswith("If you’re planning 14 days in Vietnam")
    # TikTok's own (web-rounded) counts, as recorded; nothing is invented for what the page lacks
    assert first["statistics"] == {"aweme_id": "7690818986950380821", "collect_count": 34159, "comment_count": 525,
                                   "digg_count": 75500, "play_count": 1600000, "share_count": 28300}
    assert first["author"]["unique_id"] == "vietnam_expresstour" and first["author"]["follower_count"] == 55300
    assert first["video"]["duration"] == 25000 and first["video"]["cover"]["url_list"][0].startswith("https://")
    assert first["music"]["id_str"] == "7690819041476446996" and first["music"]["play_url"]["url_list"]
    for key in ("is_ad", "region", "desc_language"):  # not on keyword-page items
        assert key not in first
    assert "custom_verify" not in first["author"] and "verification_type" not in first["author"]


def test_the_last_page_has_no_cursor_and_repeats_are_dropped(client, answer):
    items = json.loads(PAGE)["videoList"]
    answer(page(hasMore=False, videoList=[items[0], items[0], {"id": "not-an-id"}, items[1]]))
    body = ok(client, KEYWORD, query="travel", cursor="60")
    assert "cursor" not in body
    assert [p["aweme_id"] for p in body["search_item_list"]] == ["7690818986950380821", "7690107966522985750"]
    answer(page(videoList=[]))  # an empty page ends the results even when TikTok says hasMore
    assert ok(client, KEYWORD, query="travel") == {"success": True, "credits_charged": 0, "search_item_list": []}


def test_later_pages_reuse_the_first_pages_session(client, answer):
    ie = answer(PAGE, page(cacheSessionId="other"), page(), page())
    ok(client, KEYWORD, query="travel")
    ok(client, KEYWORD, query="travel", cursor="30")
    ok(client, KEYWORD, query="travel", region="GB", cursor="30")  # another market: another ranking
    ok(client, KEYWORD, query="travel", cursor="0")  # a new search starts a new session
    session = json.loads(PAGE)["cacheSessionId"]
    assert [q.get("cacheSessionId") for q in ie.queries] == [None, session, None, None]
    assert [q["offset"] for q in ie.queries] == [0, 30, 30, 0]


def test_a_keyword_without_a_page_needs_the_signed_in_search(client, answer):
    ie = answer(NO_PAGE)
    r = client.get(KEYWORD, params={"query": "fitness"})
    assert (r.status_code, r.json()["error"]) == (501, "platform_auth_required")
    assert "no public keyword page for 'fitness'" in r.json()["message"] and "cookies" in r.json()["message"]
    assert len(ie.queries) == 1  # status 4 is TikTok's answer, not throttling: not asked again


@pytest.mark.parametrize("params", [{"sort_by": "most-liked"}, {"date_posted": "this-week"}])
def test_filters_need_the_signed_in_search(client, answer, params):
    ie = answer()
    r = client.get(KEYWORD, params={"query": "travel", **params})
    assert (r.status_code, r.json()["error"]) == (501, "platform_auth_required")
    assert "date_posted" in r.json()["message"] and ie.queries == []
    for default in ({"sort_by": "relevance", "date_posted": "all-time"}, {"sort_by": "", "date_posted": ""}):
        answer(PAGE)
        assert len(ok(client, KEYWORD, query="travel", **default)["search_item_list"]) == 3


def test_with_cookies_the_rest_goes_to_the_app_search(client, answer, monkeypatch):
    sent = []

    async def signed_in():
        pass

    async def app_search(path, query, cursor, region, **extra):
        sent.append((path, query, cursor, region, extra))
        return {"data": [{"aweme_info": {"aweme_id": "1"}}], "cursor": 20, "has_more": 1}

    monkeypatch.setattr(tiktok_private, "_require_account", signed_in)
    monkeypatch.setattr(tiktok_private, "_search", app_search)
    answer(NO_PAGE, NO_PAGE)
    body = ok(client, KEYWORD, query="Fitness tips", region="US")
    assert body["search_item_list"] == [{"aweme_info": {"aweme_id": "1"}}] and body["has_more"] is True
    body = ok(client, KEYWORD, query="travel", sort_by="most-liked", date_posted="this-month")
    ok(client, HASHTAG, hashtag="#Fitness")
    assert sent == [("search/item/full", "Fitness tips", None, "US", {"publish_time": 0, "sort_type": 0}),
                    ("search/item/full", "travel", None, None, {"publish_time": 30, "sort_type": 1}),
                    ("challenge/search", "fitness", None, None, {})]


# --- hashtag ------------------------------------------------------------------------

def test_hashtag_search_keeps_only_tagged_posts(client, answer):
    ie = answer(PAGE)
    body = ok(client, HASHTAG, hashtag="#Travel")
    assert ie.queries[0]["keyword"] == "travel"
    assert_conforms(body, HASHTAG, min_coverage=0.18)
    assert [p["aweme_id"] for p in body["aweme_list"]] == ["7690107966522985750"] and body["cursor"] == 30
    assert S.hashtags(json.loads(PAGE)["videoList"][1]) >= {"travel", "travellife", "traveltheworld"}


def test_hashtag_search_reads_up_to_three_pages_for_a_tagged_post(client, answer):
    untagged = page(videoList=[json.loads(PAGE)["videoList"][0]])
    ie = answer(untagged, untagged, PAGE)
    body = ok(client, HASHTAG, hashtag="travel")
    assert [q["offset"] for q in ie.queries] == [0, 30, 60]
    assert len(body["aweme_list"]) == 1 and body["cursor"] == 90
    ie = answer(untagged, untagged, untagged)
    body = ok(client, HASHTAG, hashtag="travel")
    assert body["aweme_list"] == [] and body["cursor"] == 90 and len(ie.queries) == 3
    answer(page(hasMore=False, videoList=json.loads(untagged)["videoList"]))
    assert ok(client, HASHTAG, hashtag="travel")["aweme_list"] == []


def test_a_hashtag_without_a_page_needs_the_signed_in_search(client, answer):
    answer(NO_PAGE)
    r = client.get(HASHTAG, params={"hashtag": "fitness"})
    assert (r.status_code, r.json()["error"]) == (501, "platform_auth_required")


# --- failure shapes ----------------------------------------------------------------

@pytest.mark.parametrize("blocked", ["", "   ", CHALLENGE])
def test_an_empty_body_or_a_challenge_page_is_asked_once_more(client, answer, blocked):
    ie = answer(blocked, PAGE)
    assert len(ok(client, KEYWORD, query="travel")["search_item_list"]) == 3 and len(ie.queries) == 2
    ie = answer(blocked, blocked)
    r = client.get(KEYWORD, params={"query": "travel"})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked") and len(ie.queries) == 2
    assert "retry later" in r.json()["message"]


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_http_refusals_are_asked_once_more_then_are_503(client, answer, status):
    ie = answer(http_error(status), PAGE)
    assert len(ok(client, KEYWORD, query="travel")["search_item_list"]) == 3
    ie = answer(http_error(status), http_error(status))
    r = client.get(KEYWORD, params={"query": "travel"})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked") and len(ie.queries) == 2
    assert f"HTTP {status}" in r.json()["message"]


def test_other_failures_keep_their_meaning():
    for answer_, error in ((INVALID, UpstreamError), ('{"statusCode": 10000}', UpstreamBlocked),
                           ('{"statusCode": 0, "videoList": {}}', UpstreamError), ("[]", UpstreamError),
                           ('{"statusCode": 0, "videoList": [', UpstreamError), (NO_PAGE, S.NoKeywordPage)):
        ie = FakeIE(answer_)
        with pytest.raises(error):
            S.keyword_posts(FakeYdl(ie), "travel", "US", 0)
        assert len(ie.queries) == 1, answer_
    with pytest.raises(ExtractorError):  # 404, network, proxy: with_ydl's usual translation
        S.keyword_posts(FakeYdl(FakeIE(http_error(404))), "travel", "US", 0)
