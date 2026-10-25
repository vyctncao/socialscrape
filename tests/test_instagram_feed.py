"""Instagram listings offline: user/posts and user/reels pages (dates, captions, counts, cursors), highlight detail
and the mixed search, against recorded logged-out answers (tests/fixtures/instagram, trimmed) and the failure shapes
Instagram turns anonymous clients away with.

The fixtures were recorded on 2026-10-23 from @jane: her profile page and reels tab (the Relay results they prefetch,
their expectedPreloaders and LSD token), the posts tab's first page and the page after it as served to crawlers, the
reels tab after its first page, the logged-out post query for two reels and a carousel, the /stories/highlights/<id>/
page's prefetched reel, and the answers to a bad cursor, an unknown query id, a throttled API call (401
require_login) and the web page the feed/reels_media API sends anonymous callers.
"""

from __future__ import annotations

import copy
import json
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from yt_dlp.utils import ExtractorError

from socialscrape.errors import BadRequest, UpstreamBlocked, UpstreamError
from socialscrape.platforms import instagram as ig
from socialscrape.platforms import instagram_private
from tests.contract import assert_conforms, check

FIXTURES = Path(__file__).parent / "fixtures" / "instagram"
JANE_PK = "21393171"
HIGHLIGHT_ID = "18382085791177637"
POSTS_Q, REELS_Q = ig._TAB_QUERY[""], ig._TAB_QUERY["reels"]
LOGIN = "https://www.instagram.com/accounts/login/?next=%2Fjane%2F"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _sjs(result: dict[str, Any]) -> str:
    blob = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"require": [
        ["RelayPrefetchedStreamCache", "next", [], ["adp_Q", {"__bbox": {"result": result}}]]]}}]]]}
    return f'<script type="application/json" data-sjs>{json.dumps(blob)}</script>'


def page_html(name: str, page_id: str = "profilePage") -> str:
    """A recorded page put back together: its prefetched Relay results, expectedPreloaders and LSD token."""
    rec = fixture(name)
    preloaders = json.dumps(rec["expectedPreloaders"], separators=(",", ":"))
    return (f'<html><script>{{"pageID":"{page_id}"}}</script>' + "".join(_sjs(r) for r in rec["relay"])
            + f'<script>{{"expectedPreloaders":{preloaders},"initialRouteInfo":{{}}}}</script>'
            + f'<script>["LSD",[],{{"token":"{rec["lsd"]}"}},323]</script></html>')


def highlight_html() -> str:
    return '<html><script>{"pageID":"StoriesPage"}</script>' + _sjs(fixture("highlight_page.json")) + (
        '<script>["LSD",[],{"token":"AdT1MJc5H-X3D9BGBaOayuKK8nI"},323]</script></html>')


def post_answers() -> dict[str, str]:
    """media_id -> the recorded post query answer."""
    out = {}
    for name in ("post_query_reel.json", "post_query_reel2.json", "post_query_carousel.json"):
        data = fixture(name)
        out[data["data"]["xig_polaris_media"]["pk"]] = json.dumps(data)
    return out


Answer = tuple  # (status, body) or (status, body, final URL)


class FakeInstagram:
    """An extractor stand-in: page downloads answer from `pages` ({url: html or (html, final_url)}), /api/graphql
    POSTs from `graphql` ({friendly name: a list of answers used in turn, or a function of the variables}), each a
    (status, body) or (status, body, final URL). Records every request: ("GET", url) or (name, doc_id, variables)."""

    def __init__(self, pages: dict[str, Any], graphql: dict[str, list[Answer] | Callable[[dict], Answer]],
                 logged_in: bool = False) -> None:
        self.pages, self.graphql, self.requests = pages, graphql, []
        self._is_logged_in, self._can_impersonate, self._is_web_app = logged_in, True, True
        self._api_headers = {"X-IG-App-ID": "936619743392459"}
        self._API_BASE_URL = "https://www.instagram.com/api/v1"
        self.json_answer: Callable[[], Any] | None = None

    def _get_cookies(self, url: str) -> dict[str, Any]:
        return {"csrftoken": SimpleNamespace(value="kXucU2WA66bj_l5pnaf1hg")}

    def _download_webpage_handle(self, url: str, item_id: Any = None, note: Any = None, *, data: bytes | None = None,
                                 headers: dict[str, str] | None = None, **kwargs: Any) -> tuple[str, Any]:
        if data is None:
            self.requests.append(("GET", url))
            page = self.pages[url]
            html, final = page if isinstance(page, tuple) else (page, url)
            return html, SimpleNamespace(url=final, status=200)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(data.decode()).items()}
        variables = json.loads(form["variables"])
        assert headers["X-FB-LSD"] == form["lsd"] and headers["X-CSRFToken"] == "kXucU2WA66bj_l5pnaf1hg"
        assert headers["X-FB-Friendly-Name"] == form["fb_api_req_friendly_name"]
        self.requests.append((form["fb_api_req_friendly_name"], form["doc_id"], variables))
        answers = self.graphql[form["fb_api_req_friendly_name"]]
        status, body, *final = answers(variables) if callable(answers) else answers.pop(0)
        return body, SimpleNamespace(url=final[0] if final else ig._GRAPHQL_URL, status=status)

    def _download_json(self, url: str, *args: Any, **kwargs: Any) -> Any:
        self.requests.append(("API", url))
        return self.json_answer()

    def run(self, flow: Callable[[Any], Any]) -> Any:
        return flow(SimpleNamespace(get_info_extractor=lambda name: self, urlopen=lambda req: None))


def posts_answers(variables: dict[str, Any]) -> Answer:
    """The recorded post query answers; the throttled answer for a post without one."""
    answer = post_answers().get(variables["media_id"])
    return (200, answer) if answer else (401, json.dumps(fixture("api_require_login.json")))


@pytest.fixture
def net(monkeypatch):
    """Routes run their flows against a FakeInstagram (set net.ig)."""
    state = SimpleNamespace(ig=None)

    async def with_ydl(platform, fn, **opts):
        return state.ig.run(fn)

    monkeypatch.setattr(ig, "with_ydl", with_ydl)
    monkeypatch.setattr(ig, "_RETRY_DELAY", 0.0)
    return state


def jane(graphql: dict[str, Any], **pages: Any) -> FakeInstagram:
    return FakeInstagram({f"{ig.WEB}/jane/": page_html("profile_page.json"),
                          f"{ig.WEB}/jane/reels/": page_html("reels_page.json"), **pages}, graphql)


# --- /v2/instagram/user/posts ----------------------------------------------------------------------------------


def test_posts_first_page_has_dates_captions_counts_and_a_cursor(client, net):
    """Blocker 2: the 12-item feed had no taken_at and no next_max_id."""
    net.ig = jane({POSTS_Q: [(200, json.dumps(fixture("posts_tab_first.json")))], ig._POST_QUERY: posts_answers})
    body = client.get("/v2/instagram/user/posts", params={"handle": "jane", "trim": "true"}).json()
    assert body["success"] is True and body["num_results"] == 3 and body["more_available"] is True
    assert body["next_max_id"] == fixture("posts_tab_first.json")["data"]["xig_user_by_username"][
        "polaris_ordered_timeline_connection"]["page_info"]["end_cursor"]
    reel, reel2, carousel = body["items"]
    # what Stratton's getInstagramPostsPage reads: code, caption.text, taken_at, play/like/comment counts
    assert (reel["code"], reel["taken_at"], reel["created_at"]) == (
        "DQhKIRakcbY", 1762009243, "2025-11-01T15:00:43.000Z")
    assert reel["caption"]["text"].startswith("CHICKENLEIGH") and reel["caption"]["pk"] == "18060605873449050"
    assert reel["caption"]["media_id"] == "3756328113124001496" and reel["caption"]["user_id"] == JANE_PK
    assert (reel["play_count"], reel["ig_play_count"], reel["like_count"], reel["comment_count"]) == (
        30888401, 30888401, 1744330, 18730)
    assert reel["has_audio"] is True and reel["video_duration"] == pytest.approx(66.933334)
    assert reel["image_versions2"]["candidates"][0]["url"].startswith("https://")
    assert reel["video_versions"][0]["url"].startswith("https://") and reel["url"].endswith("/p/DQhKIRakcbY/")
    assert reel2["taken_at"] == 1792694492 and reel2["caption"]["text"].startswith("Never change")
    assert carousel["media_type"] == 8 and carousel["taken_at"] == 1791313113 and carousel["comment_count"] == 157
    assert "play_count" not in carousel and "video_versions" not in carousel and "has_audio" not in carousel
    assert all(i["user"]["username"] == "jane" and i["id"].endswith(f"_{JANE_PK}") for i in body["items"])
    assert_conforms(body, "/v2/instagram/user/posts", min_coverage=0.3)
    # one page, its first page again as served to crawlers (the page's own query id), one post query per item
    assert net.ig.requests[0] == ("GET", f"{ig.WEB}/jane/")
    assert net.ig.requests[1] == (POSTS_Q, "29483846714553007", {
        "first": 12, "username": "jane", ig._CRAWLER: True})
    assert [r[2] for r in net.ig.requests[2:]] == [
        {"media_id": "3756328113124001496"}, {"media_id": "4013734517356337091"}, {"media_id": "4002146922441727065"}]


def test_posts_next_page_follows_the_cursor(client, net):
    """Blocker 2: next_max_id was 501 pagination_unavailable. A refused post query leaves the rest without details,
    with no further requests."""
    net.ig = jane({POSTS_Q: [(200, json.dumps(fixture("posts_tab_after.json")))],
                   ig._POST_QUERY: [(401, json.dumps(fixture("api_require_login.json")))]})
    body = client.get("/v2/instagram/user/posts", params={"handle": "jane", "next_max_id": "AQHTn0DQ"}).json()
    assert body["success"] is True and [i["code"] for i in body["items"]] == ["DeHz08LvwwW", "Dde6ceOEsd9"]
    assert body["next_max_id"].startswith("AQHTWDgrqlbB") and body["more_available"] is True
    first = body["items"][0]
    assert (first["like_count"], first["play_count"]) == (12464, 663958)  # the crawler fields
    assert first["caption"] == {"text": "The brain rot is real. Happy birthday Dad! #67🤷‍♀️\n\n#siblings "
                                        "#brother #sister #family"}
    assert not any("taken_at" in i or "comment_count" in i for i in body["items"])  # unknown, not 0
    assert net.ig.requests[1] == (POSTS_Q, "29483846714553007", {
        "first": 12, "username": "jane", ig._CRAWLER: True, "after": "AQHTn0DQ"})
    assert len(net.ig.requests) == 3  # the page, the tab query, one refused post query


def test_posts_first_page_without_the_tab_query_uses_the_page(client, net):
    """Page 1 survives a refused tab query with the page's own nodes; being throttled, it skips the post queries."""
    net.ig = jane({POSTS_Q: [(401, json.dumps(fixture("api_require_login.json")))],
                   ig._POST_QUERY: posts_answers})
    body = client.get("/v2/instagram/user/posts", params={"handle": "jane"}).json()
    assert body["success"] is True and [i["code"] for i in body["items"]] == [
        "DQhKIRakcbY", "Dezpkchv5fD", "DeKe2vzD9xZ"]
    assert body["items"][0]["caption"]["text"].startswith("CHICKENLEIGH") and body["next_max_id"].startswith("AQHTn0DQ")
    assert not any(k in i for i in body["items"] for k in ("like_count", "play_count", "taken_at"))
    assert [r[0] for r in net.ig.requests] == ["GET", POSTS_Q]

    # an error that isn't a block (a query id Instagram retired) still lets the post queries run
    net.ig = jane({POSTS_Q: [(200, json.dumps(fixture("graphql_unknown_doc.json")))], ig._POST_QUERY: posts_answers})
    body = client.get("/v2/instagram/user/posts", params={"handle": "jane"}).json()
    assert body["items"][0]["like_count"] == 1744330 and "play_count" not in body["items"][0]
    assert body["items"][0]["taken_at"] == 1762009243


@pytest.mark.parametrize("answers,status,code", [
    ([(200, json.dumps(fixture("graphql_bad_cursor.json")))], 400, "bad_cursor"),
    ([(200, json.dumps(fixture("graphql_unknown_doc.json")))], 500, "query_unavailable"),
    ([(401, json.dumps(fixture("api_require_login.json")))], 503, "upstream_blocked"),
    ([(200, ""), (200, (FIXTURES / "api_error_page.html").read_text())], 503, "upstream_blocked"),
    ([(200, "")] * 2, 503, "upstream_blocked"),
    ([(502, "<html>Bad gateway</html>")], 500, "upstream_error"),
    ([(200, "", LOGIN)], 503, "upstream_blocked"),
])
def test_posts_next_page_failures(client, net, answers, status, code):
    """The shapes production saw as 500 "Failed to parse JSON" are blocks (503, retried through the next proxy)."""
    net.ig = jane({POSTS_Q: list(answers)})
    r = client.get("/v2/instagram/user/posts", params={"handle": "jane", "next_max_id": "garbage"})
    assert r.status_code == status and r.json()["error"] == code, r.json()


def test_empty_answer_is_retried_once(client, net):
    net.ig = jane({POSTS_Q: [(200, ""), (200, json.dumps(fixture("posts_tab_after.json")))],
                   ig._POST_QUERY: posts_answers})
    body = client.get("/v2/instagram/user/posts", params={"handle": "jane", "next_max_id": "AQHTn0DQ"}).json()
    assert body["success"] is True and len(body["items"]) == 2
    assert [r[0] for r in net.ig.requests[:3]] == ["GET", POSTS_Q, POSTS_Q]


def test_post_queries_stop_at_the_time_budget(client, net, monkeypatch):
    monkeypatch.setattr(ig, "_DETAILS_BUDGET", -1.0)
    net.ig = jane({POSTS_Q: [(200, json.dumps(fixture("posts_tab_first.json")))], ig._POST_QUERY: posts_answers})
    body = client.get("/v2/instagram/user/posts", params={"handle": "jane"}).json()
    assert len(body["items"]) == 3 and not any("taken_at" in i for i in body["items"])
    assert [r[0] for r in net.ig.requests] == ["GET", POSTS_Q]


def test_restricted_or_missing_profile_stops_before_any_query(client, net):
    net.ig = FakeInstagram({f"{ig.WEB}/jane/": ('{"pageID":"httpErrorPage"}', f"{ig.WEB}/jane/"),
                            f"{ig.WEB}/walled/": ("<html>", LOGIN)}, {})
    assert client.get("/v2/instagram/user/posts", params={"handle": "jane"}).status_code == 404
    r = client.get("/v2/instagram/user/posts", params={"handle": "walled", "next_max_id": "x"})
    assert r.status_code == 503 and r.json()["error"] == "upstream_blocked"
    assert [r[0] for r in net.ig.requests] == ["GET", "GET"]


# --- /v1/instagram/user/reels -------------------------------------------------------------------------------


def test_reels_carry_captions_and_dates(client, net):
    """Blocker 2: Stratton's posts+reels merge keeps the reel item for a shortcode, and reels had no caption."""
    net.ig = jane({ig._POST_QUERY: posts_answers})
    body = client.get("/v1/instagram/user/reels", params={"handle": "jane"}).json()
    first, second = body["items"]
    assert first["code"] == "DQhKIRakcbY" and first["url"].endswith("/reel/DQhKIRakcbY/")
    assert first["caption"]["text"].startswith("CHICKENLEIGH") and first["taken_at"] == 1762009243
    assert (first["play_count"], first["like_count"], first["comment_count"]) == (30888364, 1744330, 18730)
    assert second["caption"]["text"].startswith("Never change") and second["created_at"] == "2026-10-22T18:41:32.000Z"
    assert body["max_id"].startswith("AQHTwomlEWyRX")
    assert_conforms(body, "/v1/instagram/user/reels", min_coverage=0.25)
    assert [r[0] for r in net.ig.requests] == ["GET", ig._POST_QUERY, ig._POST_QUERY]  # no tab query on page 1


def test_posts_and_reels_share_their_post_queries(client, net):
    """verify-consumer: Stratton lists an account's reels and its posts (merged by shortcode), so most items were asked
    for twice (24 post queries for 13 jane posts). The second listing reuses the first one's answers."""
    net.ig = jane({ig._POST_QUERY: posts_answers})
    reels = client.get("/v1/instagram/user/reels", params={"handle": "jane"}).json()["items"]
    assert [r[0] for r in net.ig.requests].count(ig._POST_QUERY) == 2
    net.ig = jane({POSTS_Q: [(200, json.dumps(fixture("posts_tab_first.json")))], ig._POST_QUERY: posts_answers})
    posts = client.get("/v2/instagram/user/posts", params={"handle": "jane"}).json()["items"]
    assert [r[2] for r in net.ig.requests if r[0] == ig._POST_QUERY] == [{"media_id": "4002146922441727065"}]
    assert [(i["code"], i["taken_at"], i["comment_count"]) for i in posts] == [
        ("DQhKIRakcbY", 1762009243, 18730), ("Dezpkchv5fD", 1792694492, reels[1]["comment_count"]),
        ("DeKe2vzD9xZ", 1791313113, 157)]
    assert posts[0]["caption"] == reels[0]["caption"] and posts[0]["play_count"] == 30888401  # the posts tab's own


def test_reels_keep_what_the_posts_listing_got_when_post_queries_are_refused(client, net):
    """verify-consumer: with the post query refused (production throttling), reels had no caption and Stratton's
    posts+reels merge, which keeps the reel for a shortcode, lost the caption the posts listing carried."""
    refused = (401, json.dumps(fixture("api_require_login.json")))
    net.ig = jane({POSTS_Q: [(200, json.dumps(fixture("posts_tab_first.json")))], ig._POST_QUERY: lambda v: refused})
    posts = client.get("/v2/instagram/user/posts", params={"handle": "jane"}).json()["items"]
    assert all(i["caption"]["text"] for i in posts) and not any("taken_at" in i for i in posts)
    net.ig = jane({ig._POST_QUERY: lambda v: refused})
    reels = client.get("/v1/instagram/user/reels", params={"handle": "jane"}).json()["items"]
    by_code = {i["code"]: i for i in posts}
    assert [r["caption"]["text"] for r in reels] == [by_code[r["code"]]["caption"]["text"] for r in reels]
    assert not any("taken_at" in r for r in reels)  # still unknown, not guessed
    assert [r[0] for r in net.ig.requests] == ["GET", ig._POST_QUERY]  # the one refused query, then no more

    # and the dates once a listing got them: the posts listing's tab refused, its items still dated
    net.ig = jane({ig._POST_QUERY: posts_answers})
    client.get("/v1/instagram/user/reels", params={"handle": "jane"})
    net.ig = jane({POSTS_Q: [refused], ig._POST_QUERY: posts_answers})
    posts = client.get("/v2/instagram/user/posts", params={"handle": "jane"}).json()["items"]
    assert [i.get("taken_at") for i in posts] == [1762009243, 1792694492, None]
    assert [r[0] for r in net.ig.requests] == ["GET", POSTS_Q]  # throttled: nothing more asked


def test_reels_first_take_captions_from_the_posts_tab_when_post_queries_are_refused(client, net):
    """Stratton's fetchFeed asks posts and reels at once (and its incremental sweep reels first): reels refused their
    post queries ask the posts tab's first page once for its captions, as Instagram answers that query when it turns
    the post queries away."""
    refused = (401, json.dumps(fixture("api_require_login.json")))
    net.ig = jane({ig._POST_QUERY: lambda v: refused, POSTS_Q: [(200, json.dumps(fixture("posts_tab_first.json")))]})
    reels = client.get("/v1/instagram/user/reels", params={"handle": "jane"}).json()["items"]
    assert [r["caption"]["text"][:12] for r in reels] == ["CHICKENLEIGH", "Never change"]
    assert not any("taken_at" in r for r in reels)
    assert net.ig.requests[1:] == [(ig._POST_QUERY, ig._POST_DOC_ID, {"media_id": "3756328113124001496"}),
                                   (POSTS_Q, "29483846714553007", {"first": 12, "username": "jane", ig._CRAWLER: True})]
    # the posts tab refused too: the reels are still served, without captions
    ig.CAPTIONS.clear()
    net.ig = jane({ig._POST_QUERY: lambda v: refused, POSTS_Q: [refused]})
    body = client.get("/v1/instagram/user/reels", params={"handle": "jane"}).json()
    assert body["success"] is True and not any("caption" in r for r in body["items"])


def test_reels_next_page(client, net):
    net.ig = jane({REELS_Q: [(200, json.dumps(fixture("reels_tab_after.json")))],
                   ig._POST_QUERY: lambda v: (200, json.dumps({"data": {"xig_polaris_media": None}}))})
    body = client.get("/v1/instagram/user/reels", params={"handle": "jane", "max_id": "AQHTwoml"}).json()
    assert body["success"] is True and len(body["items"]) == 2 and body["max_id"].startswith("AQHT1XEUSx")
    assert all(isinstance(i["play_count"], int) and "caption" not in i for i in body["items"])
    # the reels tab's query takes no crawler provider
    assert net.ig.requests[1] == (REELS_Q, "27838951732404191", {"first": 12, "username": "jane", "after": "AQHTwoml"})


# --- mapping details -------------------------------------------------------------------------------------------


def test_captionless_post_is_distinct_from_an_unanswered_detail():
    node = {"pk": "1", "code": "abc", "media_type": 2}
    profile = {"pk": JANE_PK, "username": "jane"}
    assert "caption" not in ig.map_feed_item(node, profile)
    assert "caption" not in ig.map_feed_item(node, profile, {"caption": {"pk": "123"}})
    assert ig.map_feed_item(node, profile, {"caption": None})["caption"] is None
    assert ig.map_feed_item({**node, "caption": None}, profile)["caption"] is None
    assert ig.map_feed_item(node, profile, {"caption": {"text": ""}})["caption"] == {"text": ""}
    assert ig.map_feed_item(node, profile, {"caption": {"text": "hello"}})["caption"]["text"] == "hello"


def test_hidden_counts_are_unknown_not_zero():
    profile = {"pk": JANE_PK, "username": "jane"}
    node = {"pk": "1", "code": "abc", "media_type": 2, "like_and_view_counts_disabled": True, "like_count": 0,
            "play_count": 0, "user": {"pk": JANE_PK}}
    item = ig.map_feed_item(node, profile, {"pk": "1", "comment_count": 0, "like_count": 0, "taken_at": 5})
    assert "like_count" not in item and "play_count" not in item and "ig_play_count" not in item
    assert item["comment_count"] == 0 and item["taken_at"] == 5
    item = ig.map_feed_item({**node, "like_and_view_counts_disabled": False}, profile)
    assert (item["like_count"], item["play_count"]) == (0, 0) and "taken_at" not in item and "caption" not in item


def test_collab_author_comes_from_the_post_query():
    media = fixture("post_query_carousel.json")["data"]["xig_polaris_media"]["if_not_gated_logged_out"]
    collab = {**copy.deepcopy(media), "user": {"pk": "777", "id": "17841400000000777", "username": "bob",
                                               "full_name": "Bob", "polaris_ordered_timeline_connection": {}}}
    node = {"pk": media["pk"], "code": media["code"], "user": {"pk": "777", "id": "17841400000000777"}}
    item = ig.map_feed_item(node, {"pk": JANE_PK, "username": "jane"}, collab)
    assert item["user"] == {"pk": "777", "pk_id": "777", "id": "777", "username": "bob", "full_name": "Bob",
                            "fbid_v2": "17841400000000777"}
    assert item["id"] == f"{media['pk']}_777" and item["caption"]["user_id"] == "777"


@pytest.mark.parametrize("status,body,final,error", [
    (200, "", ig._GRAPHQL_URL, UpstreamBlocked),
    (200, " \n", ig._GRAPHQL_URL, UpstreamBlocked),
    (200, (FIXTURES / "api_error_page.html").read_text(), ig._GRAPHQL_URL, UpstreamBlocked),
    (401, json.dumps(fixture("api_require_login.json")), ig._GRAPHQL_URL, UpstreamBlocked),
    (429, "", ig._GRAPHQL_URL, UpstreamBlocked),
    (200, "{}", LOGIN, UpstreamBlocked),
    (502, "<html>Bad gateway</html>", ig._GRAPHQL_URL, UpstreamError),
    # what i.instagram.com answers an anonymous feed/reels_media call
    (500, '{"message":"We\'re sorry, but something went wrong. Please try again.","status_code":"500",'
          '"status":"fail"}', ig._GRAPHQL_URL, UpstreamError),
    (200, '{"status":"fail","message":"x"}', ig._GRAPHQL_URL, UpstreamError),
    # verify-merged: what tab_connection already read as refusals was data (and then a 500) here
    (200, json.dumps({"errors": [{"message": "execution error", "severity": "CRITICAL", "code": 1675004}]}),
     ig._GRAPHQL_URL, UpstreamBlocked),
    (200, json.dumps({"data": None, "errors": [{"message": "execution error"}]}), ig._GRAPHQL_URL, UpstreamBlocked),
    (200, '{"message":"Please wait a few minutes before you try again.","status":"fail"}', ig._GRAPHQL_URL,
     UpstreamBlocked),
    (400, '{"message":"Please wait a few minutes before you try again.","status":"fail"}', ig._GRAPHQL_URL,
     UpstreamBlocked),
])
def test_api_answer_failure_shapes(status, body, final, error):
    with pytest.raises(error) as exc:
        ig.api_answer(status, body, final)
    assert exc.value.status == (503 if error is UpstreamBlocked else 500)
    if status == 401:
        assert "Please wait a few minutes" in exc.value.message
    assert ig.api_answer(200, '{"data": {}}', ig._GRAPHQL_URL) == {"data": {}}
    # a retired query id is left for _graphql_failure (query_unavailable), errors next to data for the caller
    assert ig.api_answer(200, json.dumps(fixture("graphql_unknown_doc.json")), ig._GRAPHQL_URL)["errors"]
    assert ig.api_answer(200, json.dumps(fixture("graphql_bad_cursor.json")), ig._GRAPHQL_URL)["data"]


_EXECUTION_ERROR = (200, json.dumps({"errors": [{"message": "execution error", "severity": "CRITICAL",
                                                 "code": 1675004}]}))


def test_graphql_refusals_are_blocks_and_stop_the_post_queries(client, net):
    """verify-merged: the next page answering GraphQL's errors list was a 500 "no profile listing: execution error",
    and every post query of a page kept being sent into that refusal (3 of 3 here, 12 on a real page)."""
    net.ig = jane({POSTS_Q: [_EXECUTION_ERROR]})
    r = client.get("/v2/instagram/user/posts", params={"handle": "jane", "next_max_id": "QVFDabc"})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked") and "execution error" in r.json()["message"]
    net.ig = jane({REELS_Q: [_EXECUTION_ERROR]})
    r = client.get("/v1/instagram/user/reels", params={"handle": "jane", "max_id": "QVFDabc"})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked")

    net.ig = jane({POSTS_Q: [(200, json.dumps(fixture("posts_tab_first.json")))],
                   ig._POST_QUERY: lambda v: _EXECUTION_ERROR})
    body = client.get("/v2/instagram/user/posts", params={"handle": "jane"}).json()
    assert body["success"] is True and len(body["items"]) == 3 and not any("taken_at" in i for i in body["items"])
    assert [r[0] for r in net.ig.requests] == ["GET", POSTS_Q, ig._POST_QUERY]  # stopped at the first refusal


def test_api_answer_login_redirect_with_a_session_is_a_cookie_problem(monkeypatch):
    monkeypatch.setattr(ig, "get_settings", lambda: SimpleNamespace(cookie_file=lambda platform: "/x/ig.txt"))
    with pytest.raises(ig.SessionRejected):
        ig.api_answer(200, "<html>", LOGIN, logged_in=True)


def test_web_session_reads_the_page():
    session = ig.WebSession(None, page_html("profile_page.json"), f"{ig.WEB}/jane/")
    assert session.lsd == fixture("profile_page.json")["lsd"]
    assert session.preloaded[POSTS_Q] == ("29483846714553007", {"first": 12, "username": "jane", ig._CRAWLER: False})
    bare = ig.WebSession(None, "<html></html>", f"{ig.WEB}/jane/")
    with pytest.raises(UpstreamError) as exc:
        bare.query(POSTS_Q, "1", {})
    assert exc.value.code == "no_session_token"


def test_tab_page_without_a_preloader_uses_the_known_ids():
    fake = FakeInstagram({}, {REELS_Q: [(200, json.dumps(fixture("reels_tab_after.json")))]})
    session = ig.WebSession(fake, '["LSD",[],{"token":"t"},1]', f"{ig.WEB}/jane/reels/")
    assert len(session.tab_page("reels", "jane", "c")["edges"]) == 2
    assert fake.requests == [(REELS_Q, "27838951732404191", {"first": 12, "username": "jane", "after": "c"})]
    with pytest.raises(BadRequest):
        fake.graphql[REELS_Q] = [(200, json.dumps(fixture("graphql_bad_cursor.json")))]
        session.tab_page("reels", "jane", "c")


# --- /v1/instagram/user/highlight/detail -------------------------------------------------------------------


def test_highlight_detail_reads_the_highlight_page(client, net):
    """It answered 500 "Failed to parse JSON": the feed/reels_media API answers anonymous callers with a web page,
    while the /stories/highlights/<id>/ page carries the same answer prefetched."""
    url = f"{ig.WEB}/stories/highlights/{HIGHLIGHT_ID}/"
    net.ig = FakeInstagram({url: highlight_html()}, {})
    body = client.get("/v1/instagram/user/highlight/detail", params={"id": HIGHLIGHT_ID}).json()
    assert body["success"] is True and body["id"] == f"highlight:{HIGHLIGHT_ID}" and body["title"] == "Mems"
    assert body["media_count"] == 2 and body["user"]["username"] == "jane"
    assert body["media_ids"] == ["3438202805570461074", "3729609534873796542"]
    video, photo = body["items"]
    assert video["id"] == f"3438202805570461074_{JANE_PK}" and video["code"] == "C-28zw7x12S"
    # the items name their author by pk only; the reel's user fills them in, as InstagramStoryIE does
    assert video["user"] == {"id": JANE_PK, "username": "jane"} and video["owner"]["username"] == "jane"
    assert video["owner"]["pk"] == JANE_PK and photo["owner"] == video["owner"]
    assert video["video_versions"][0]["url"].startswith("https://") and video["taken_at"] == 1724085716
    assert photo["media_type"] == 1 and photo["image_versions2"]["candidates"][0]["url"].startswith("https://")
    report = check(body, "/v1/instagram/user/highlight/detail")
    assert not report.mismatches and not report.extras, report
    assert net.ig.requests == [("GET", url)]


def test_highlight_aliases_and_highlight_id_reach_the_route(client, net):
    """Stratton's getInstagramHighlightDetails asks /v1/instagram/highlights/details (then highlight/details) with
    highlight_id."""
    net.ig = FakeInstagram({f"{ig.WEB}/stories/highlights/{HIGHLIGHT_ID}/": highlight_html()}, {})
    for path in ("/v1/instagram/highlights/details", "/v1/instagram/highlight/details"):
        r = client.get(path, params={"highlight_id": HIGHLIGHT_ID})
        assert r.status_code == 200 and r.json()["id"] == f"highlight:{HIGHLIGHT_ID}", (path, r.text[:200])
        r = client.get(path, params={"highlight_id": HIGHLIGHT_ID}, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (307, f"/v1/instagram/user/highlight/detail?id={HIGHLIGHT_ID}")
    # the canonical route keeps ScrapeCreators' one param
    assert client.get("/v1/instagram/user/highlight/detail", params={"highlight_id": HIGHLIGHT_ID}).status_code == 400


def test_highlight_detail_failures(client, net, monkeypatch):
    url = f"{ig.WEB}/stories/highlights/{HIGHLIGHT_ID}/"
    shell = '<html><script>{"pageID":"StoriesPage"}</script></html>'
    for page, status, code in (('{"pageID":"httpErrorPage"}', 404, "not_found"),
                               (("<html>", LOGIN), 503, "upstream_blocked"),
                               (shell, 503, "upstream_blocked")):  # no prefetched reel: throttled
        net.ig = FakeInstagram({url: page}, {})
        r = client.get("/v1/instagram/user/highlight/detail", params={"id": HIGHLIGHT_ID})
        assert (r.status_code, r.json()["error"]) == (status, code), r.json()
        assert net.ig.requests == [("GET", url)]  # never the API call anonymously

    # with a session, a page without the reel falls back to the API InstagramStoryIE calls
    net.ig = FakeInstagram({url: shell}, {}, logged_in=True)
    net.ig.json_answer = lambda: {"reels": {f"highlight:{HIGHLIGHT_ID}": fixture("highlight_page.json")["data"][
        "xdt_api__v1__feed__reels_media__connection"]["edges"][0]["node"]}}
    assert client.get("/v1/instagram/user/highlight/detail", params={"id": HIGHLIGHT_ID}).json()["media_count"] == 2
    assert [r[0] for r in net.ig.requests] == ["GET", "API"]

    def web_page():
        html = (FIXTURES / "api_error_page.html").read_text()
        raise ExtractorError("Failed to parse JSON", cause=json.JSONDecodeError("Expecting value", html, 0))

    net.ig.json_answer = web_page
    r = client.get("/v1/instagram/user/highlight/detail", params={"id": HIGHLIGHT_ID})
    assert (r.status_code, r.json()["error"]) == (503, "upstream_blocked")


# --- /v1/instagram/search ---------------------------------------------------------------------------------------

# The blended top-search answer's shape (users/hashtags/places entries with a position), as Instagram's web search
# returns it to signed-in sessions; these values are made up.
TOPSEARCH = {
    "users": [{"position": 0, "user": {"pk": "528817151", "pk_id": "528817151", "username": "nasa",
                                       "full_name": "NASA", "is_verified": True, "profile_pic_url": "https://img/n"}},
              {"position": 3, "user": {"pk": "1", "username": ""}}],
    "hashtags": [{"position": 1, "hashtag": {"name": "nasa", "id": 17843830945036129, "media_count": 6182315,
                                             "search_result_subtitle": "6M posts"}}],
    "places": [{"position": 2, "place": {"title": "NASA Johnson Space Center", "subtitle": "Houston, Texas",
                                         "location": {"pk": "213012122", "facebook_places_id": 113196295394011,
                                                      "name": "NASA Johnson Space Center"}}}],
    "rank_token": "1792816190738|abc", "status": "ok",
}


def test_search_needs_an_instagram_account(client):
    """It answered 500 "Instagram profile search is unavailable": logged-out Instagram has no search, so without a
    saved account this is the setup gap it is (and what the ScrapeCreators fallback takes)."""
    r = client.get("/v1/instagram/search", params={"query": "cats"})
    assert r.status_code == 501 and r.json()["error"] == "platform_auth_required"
    rows = {row["path"]: row for row in client.get("/meta/coverage").json()["endpoints"]}
    assert rows["/v1/instagram/search"]["status"] == "needs_setup"


def test_search_maps_the_blended_answer(client, monkeypatch):
    seen = []

    async def topsearch(query, context):
        seen.append((query, context))
        return copy.deepcopy(TOPSEARCH)

    monkeypatch.setattr(instagram_private, "topsearch", topsearch)
    body = client.get("/v1/instagram/search", params={"query": "nasa"}).json()
    assert seen == [("nasa", "blended")]
    assert body["data"]["users"] == [{"full_name": "NASA", "id": "528817151", "is_verified": True, "position": 0,
                                      "profile_pic_url": "https://img/n", "username": "nasa"}]
    assert body["data"]["hashtags"] == [{"id": "17843830945036129", "media_count": 6182315, "name": "nasa",
                                         "position": 1, "search_result_subtitle": "6M posts"}]
    assert body["data"]["places"][0] == {"facebook_places_id": "113196295394011", "id": "213012122",
                                         "name": "NASA Johnson Space Center", "position": 2,
                                         "subtitle": "Houston, Texas", "title": "NASA Johnson Space Center"}
    assert body["data"]["query"] == "nasa" and body["data"]["rank_token"] == "1792816190738|abc"
    report = check(body, "/v1/instagram/search")
    assert not report.mismatches and not report.extras and report.coverage >= 0.9, report
