"""TikTok followers and following come from the web app's anonymous user lists; deactivated accounts end at home."""
import pytest

from socialscrape.platforms import tiktok
from tests.contract import assert_conforms

PROFILE = {"id": "42", "secUid": "MS4wLjABAAAA" + "x" * 40, "nickname": "Some One", "private": False, "videoCount": 3}
LIST = {"statusCode": 0, "status_code": 0, "hasMore": True, "minCursor": 1793746907, "total": 4788833,
        "extra": {"now": 1}, "log_pb": {"impr_id": "x"},
        "userList": [{"user": {"id": "7", "uniqueId": "jackgreen_60", "nickname": "Jack Green", "secUid": "s7",
                               "verified": False, "signature": "hi", "createTime": 1600000000, "secret": False,
                               "avatarMedium": "https://p16-sign-va.tiktokcdn.com/tos-maliva-avt-0068/abc~tplv.jpeg"},
                      "stats": {"followerCount": 105, "followingCount": 3, "heartCount": 9, "videoCount": 2}},
                     {"user": {}, "stats": {}}]}


class FakeYdl:
    def get_info_extractor(self, name):
        return name


@pytest.fixture
def web(monkeypatch):
    calls = {"api": [], "opts": [], "answer": LIST, "profile": PROFILE}

    async def with_ydl(platform, fn, **opts):
        calls["opts"].append(opts)
        return fn(FakeYdl())

    def web_api(ie, url, display, note, query, what):
        calls["api"].append((url, dict(query), what))
        return calls["answer"]

    monkeypatch.setattr(tiktok, "with_ydl", with_ydl)
    monkeypatch.setattr(tiktok, "_web_api", web_api)
    monkeypatch.setattr(tiktok, "_profile", lambda ydl, name: calls["profile"])
    return calls


def test_followers_are_app_shaped_users_from_a_minimal_anonymous_query(client, web):
    body = client.get("/v1/tiktok/user/followers", params={"handle": "stoolpresidente", "min_time": "1793746999"}).json()
    [(url, query, what)] = web["api"]
    # Without maxCursor and minCursor TikTok answers 10201 "err input"; 30 is the largest page it takes.
    assert (url, what) == (tiktok._USER_LIST, "followers")
    assert query == {"aid": "1988", "secUid": PROFILE["secUid"], "scene": "67", "count": "30", "maxCursor": "0",
                     "minCursor": "1793746999"}
    assert web["opts"] == [{"confirm_not_found": True, "use_cookies": False}]
    assert (body["has_more"], body["min_time"], body["total"], body["status_code"]) == (True, 1793746907, 4788833, 0)
    [user] = body["followers"]  # the entry without a user is dropped
    assert {k: user[k] for k in ("uid", "unique_id", "nickname", "follower_count", "aweme_count")} == {
        "uid": "7", "unique_id": "jackgreen_60", "nickname": "Jack Green", "follower_count": 105, "aweme_count": 2}
    assert user["avatar_medium"]["url_list"] == [LIST["userList"][0]["user"]["avatarMedium"]]
    assert_conforms(body, "/v1/tiktok/user/followers")


def test_following_uses_its_scene_and_key_and_the_short_aliases_still_work(client, web):
    body = client.get("/v1/tiktok/following", params={"handle": "someone", "count": "24", "limit": "24"},
                      follow_redirects=True).json()
    assert web["api"][-1][1]["scene"] == "21" and body["followings"][0]["unique_id"] == "jackgreen_60"
    assert_conforms(body, "/v1/tiktok/user/following")
    web["answer"] = {"statusCode": 0, "userList": [], "hasMore": False, "minCursor": -1, "total": 0}
    empty = client.get("/v1/tiktok/user/followers", params={"handle": "nobody"}).json()
    assert (empty["followers"], empty["has_more"], empty["min_time"], empty["total"]) == ([], False, 0, 0)


def test_private_accounts_refusals_and_bad_cursors(client, web):
    web["profile"] = {**PROFILE, "private": True}
    assert client.get("/v1/tiktok/user/followers", params={"handle": "locked"}).status_code == 403
    web["profile"] = PROFILE
    web["answer"] = {"statusCode": 10222, "userList": []}
    assert client.get("/v1/tiktok/user/following", params={"handle": "locked"}).status_code == 403
    web["answer"] = {"statusCode": 10201, "statusMsg": "err input"}
    refused = client.get("/v1/tiktok/user/followers", params={"handle": "x"})
    assert (refused.status_code, refused.json()["error"]) == (500, "upstream_error")
    assert client.get("/v1/tiktok/user/followers", params={"handle": "x", "min_time": "soon"}).status_code == 400
    assert client.get("/v1/tiktok/user/followers").status_code == 400


def test_a_deactivated_account_answers_like_scrapecreators_and_never_falls_back(client, monkeypatch):
    """ScrapeCreators answers a TikTok handle that doesn't exist with account_deactivated and "Account doesn't
    exist" (a credit each time, for 19 handles in one week of production history), which the fallback then keeps as
    a 404. The profile route now says it itself, and the fallback leaves that 404 alone."""
    from socialscrape.fallback import _trigger

    status = {"code": 10221}

    class Ie:
        _UPLOADER_URL_FORMAT = "https://www.tiktok.com/@%s"

        def _generate_blockbuster_headers(self):
            return {}

        def _get_universal_data(self, page, ident):
            return {"webapp.user-detail": {"statusCode": status["code"], "userInfo": {}}}

    class Ydl:
        def get_info_extractor(self, name):
            return Ie()

    async def with_ydl(platform, fn, **opts):
        return fn(Ydl())

    monkeypatch.setattr(tiktok, "with_ydl", with_ydl)
    monkeypatch.setattr(tiktok, "_web_page", lambda *args, **kwargs: "<html>")
    gone = client.get("/v1/tiktok/profile", params={"handle": "sionkori"})
    assert (gone.status_code, gone.json()) == (404, {"success": False, "error": "not_found",
                                                     "message": "Account doesn't exist", "handle": "sionkori",
                                                     "account_deactivated": True})
    by_id = client.get("/v1/tiktok/profile", params={"user_id": "6659752019493208069"}).json()
    assert by_id["user_id"] == "6659752019493208069" and "handle" not in by_id and by_id["account_deactivated"]
    # The same page read for another route stays an ordinary 404: ScrapeCreators' ranked user search can still
    # answer it, and no other route's provider answer was seen to be account_deactivated.
    search = client.get("/v1/tiktok/search/users", params={"query": "sionkori"})
    assert search.status_code == 404 and "account_deactivated" not in search.json()
    status["code"] = 10101
    other = client.get("/v1/tiktok/profile", params={"handle": "sionkori"})
    assert other.status_code == 404 and "account_deactivated" not in other.json()
    status["code"] = [10202]  # not a code TikTok sends, but not a crash either
    assert client.get("/v1/tiktok/profile", params={"handle": "sionkori"}).status_code == 404
    assert _trigger(404, "not_found", final=True) is None and _trigger(404, "not_found") == "not_found"
