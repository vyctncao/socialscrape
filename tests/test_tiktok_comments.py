"""TikTok comments and replies come from the web app's anonymous comment lists: no account, no fallback."""
import pytest

from socialscrape.platforms import tiktok

VIDEO = "https://www.tiktok.com/@stoolpresidente/video/7499229683859426602"
PAGE = {"alias_comment_deleted": False, "comments": [{"cid": "7499260293689639723", "text": "Omg you're here",
        "digg_count": 808, "reply_comment_total": 1, "user": {"unique_id": "veryvera88"}}], "cursor": 20,
        "extra": {"now": 1}, "has_filtered_comments": 0, "has_more": 1, "log_pb": {"impr_id": "x"},
        "reply_style": 2, "status_code": 0, "status_msg": "", "top_gifts": None, "total": 327}


class FakeYdl:
    def get_info_extractor(self, name):
        return name


@pytest.fixture
def tiktok_web(monkeypatch):
    calls = {"api": [], "opts": [], "answer": PAGE}

    async def with_ydl(platform, fn, **opts):
        calls["opts"].append((platform, opts))
        return fn(FakeYdl())

    def web_api(ie, url, display, note, query, what):
        calls["api"].append((url, dict(query), what))
        return calls["answer"]

    monkeypatch.setattr(tiktok, "with_ydl", with_ydl)
    monkeypatch.setattr(tiktok, "_web_api", web_api)
    return calls


def test_comments_pass_tiktoks_page_through_from_a_minimal_anonymous_query(client, tiktok_web):
    body = client.get("/v1/tiktok/video/comments", params={"url": VIDEO, "cursor": "40"}).json()
    assert body == {"success": True, "credits_charged": 0, **PAGE}  # ScrapeCreators' shape is TikTok's own body
    [(url, query, what)] = tiktok_web["api"]
    # The web app's full parameter set (device_id, browser and region fields) gets an empty body unless signed.
    assert (url, what) == (tiktok._COMMENTS, "comments")
    assert query == {"aid": "1988", "aweme_id": "7499229683859426602", "count": "20", "cursor": "40"}
    assert tiktok_web["opts"] == [("tiktok", {"use_cookies": False})]  # a dead session can't cost the answer


def test_turned_off_or_missing_comments_are_an_empty_page(client, tiktok_web):
    tiktok_web["answer"] = {"status_code": 0, "status_msg": "", "comments": None, "cursor": None, "has_more": None,
                            "total": None}
    body = client.get("/v1/tiktok/video/comments", params={"url": VIDEO, "cursor": "20"}).json()
    assert (body["comments"], body["cursor"], body["has_more"], body["total"]) == ([], 20, 0, 0)


def test_a_refusal_is_an_upstream_error_not_an_empty_page(client, tiktok_web):
    tiktok_web["answer"] = {"status_code": 5, "status_msg": "", "comments": None}
    response = client.get("/v1/tiktok/video/comments", params={"url": VIDEO})
    assert response.status_code == 500 and response.json()["error"] == "upstream_error"


def test_replies_ask_for_the_comment_within_its_post(client, tiktok_web):
    body = client.get("/v1/tiktok/video/comment/replies",
                      params={"url": VIDEO, "comment_id": "7499260293689639723", "cursor": "3"}).json()
    assert body["success"] is True and body["comments"][0]["cid"] == "7499260293689639723"
    [(url, query, what)] = tiktok_web["api"]
    assert (url, what) == (tiktok._REPLIES, "comment replies")
    assert query == {"aid": "1988", "item_id": "7499229683859426602", "aweme_id": "7499229683859426602",
                     "comment_id": "7499260293689639723", "count": "20", "cursor": "3"}


def test_short_links_resolve_and_bad_input_is_a_400(client, tiktok_web, monkeypatch):
    monkeypatch.setattr(tiktok, "_resolve_short_link", lambda ydl, url: VIDEO + "?is_from_webapp=1")
    assert client.get("/v1/tiktok/video/comments", params={"url": "https://vm.tiktok.com/ZMabc123/"}).status_code == 200
    assert tiktok_web["api"][-1][1]["aweme_id"] == "7499229683859426602"
    monkeypatch.setattr(tiktok, "_resolve_short_link", lambda ydl, url: "https://www.tiktok.com/?_r=1")
    lost = client.get("/v1/tiktok/video/comments", params={"url": "https://vm.tiktok.com/ZMnothing/"})
    assert (lost.status_code, lost.json()["error"]) == (404, "not_found")
    for path, params in (("/v1/tiktok/video/comments", {"url": "https://example.com/v/1"}),
                         ("/v1/tiktok/video/comments", {"url": VIDEO, "cursor": "next"}),
                         ("/v1/tiktok/video/comment/replies", {"url": VIDEO, "comment_id": "abc"}),
                         ("/v1/tiktok/video/comment/replies", {"url": VIDEO, "comment_id": "1", "cursor": "-1"})):
        response = client.get(path, params=params)
        assert (response.status_code, response.json()["error"]) == (400, "bad_request"), params


def test_coverage_lists_comments_as_implemented(client):
    rows = {row["path"]: row for row in client.get("/meta/coverage").json()["endpoints"]}
    for path in ("/v1/tiktok/video/comments", "/v1/tiktok/video/comment/replies"):
        assert rows[path]["status"] == "implemented" and "anonymous" in rows[path]["source"], path


@pytest.mark.live
def test_live_comments_and_replies_answer_without_an_account(client):
    first = client.get("/v1/tiktok/video/comments", params={"url": VIDEO}).json()
    assert first["comments"] and first["comments"][0]["cid"] and first["total"] > 0 and first["has_more"] == 1
    second = client.get("/v1/tiktok/video/comments", params={"url": VIDEO, "cursor": str(first["cursor"])}).json()
    assert not {c["cid"] for c in first["comments"]} & {c["cid"] for c in second["comments"]}
    parent = next(c for c in first["comments"] + second["comments"] if c.get("reply_comment_total"))
    replies = client.get("/v1/tiktok/video/comment/replies", params={"url": VIDEO, "comment_id": parent["cid"]}).json()
    assert replies["comments"] and replies["status_code"] == 0
