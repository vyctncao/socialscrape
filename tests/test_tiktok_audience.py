"""/v1/tiktok/user/audience: the account regions of a sample of followers, counted as ScrapeCreators counts them
(audienceLocations with country names and percentage strings), read off the followers' own posts."""
import pytest

from socialscrape.errors import UpstreamBlocked
from socialscrape.platforms import tiktok
from tests.contract import assert_conforms


def follower(i, posts=3):
    return {"uid": str(1000 + i), "unique_id": f"fan{i}", "sec_uid": f"MS4wLjABAAAA{i}", "nickname": f"Fan {i}",
            "aweme_count": posts}


@pytest.fixture
def sample(monkeypatch):
    """Two pages of followers and, per follower handle, the region their posts give (None: not placed; an exception:
    the lookup failed)."""
    state = {"pages": [], "regions": {}, "asked_pages": [], "looked_up": [], "options": []}

    async def with_ydl(platform, fn, **options):
        state["options"].append(options)
        return fn(None)

    def graph_page(ydl, ident, kind, min_time):
        state["asked_pages"].append((ident, kind, min_time))
        return state["pages"][len(state["asked_pages"]) - 1]

    def follower_region(ydl, user):
        state["looked_up"].append(user["unique_id"])
        answer = state["regions"].get(user["unique_id"])
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(tiktok, "with_ydl", with_ydl)
    monkeypatch.setattr(tiktok, "_graph_page", graph_page)
    monkeypatch.setattr(tiktok, "_follower_region", follower_region)
    return state


def page(users, more=False, min_time=0):
    return {"followers": users, "has_more": more, "min_time": min_time, "total": 99}


def test_regions_of_placed_followers_are_counted_like_scrapecreators(client, sample):
    first, second = [follower(i) for i in range(30)], [follower(i) for i in range(30, 36)]
    sample["pages"] = [page(first, more=True, min_time=1700000000), page(second)]
    sample["regions"] = {f"fan{i}": ("US" if i < 9 else "ng" if i < 11 else "GB" if i < 12 else None) for i in range(36)}
    body = client.get("/v1/tiktok/user/audience", params={"handle": "@someone"}).json()
    assert sample["asked_pages"] == [("someone", "followers", None), ("someone", "followers", "1700000000")]
    assert body["audienceLocations"] == [
        {"country": "United States", "countryCode": "US", "count": 9, "percentage": "75.00%"},
        {"country": "Nigeria", "countryCode": "NG", "count": 2, "percentage": "16.67%"},
        {"country": "United Kingdom", "countryCode": "GB", "count": 1, "percentage": "8.33%"}]
    assert (body["sampledFollowers"], body["resolvedFollowers"]) == (36, 12)
    assert all(options.get("use_cookies") is False for options in sample["options"])
    assert all(options.get("extractor_args") == tiktok._APP_API for options in sample["options"][2:])
    assert_conforms(body, "/v1/tiktok/user/audience")


def test_followers_who_never_posted_are_not_looked_up(client, sample):
    users = [follower(i, posts=0 if i % 2 else 4) for i in range(30)]
    sample["pages"] = [page(users)]
    sample["regions"] = {f"fan{i}": "US" for i in range(30)}
    body = client.get("/v1/tiktok/user/audience", params={"handle": "someone"}).json()
    assert sorted(sample["looked_up"]) == sorted(f"fan{i}" for i in range(0, 30, 2))
    assert (body["sampledFollowers"], body["resolvedFollowers"]) == (30, 15)


def test_a_thin_sample_is_a_501_the_fallback_answers(client, sample):
    # emma.collins920: two of thirteen followers placed read 100% NG, where ScrapeCreators counted 83% US
    sample["pages"] = [page([follower(i, posts=1 if i < 6 else 0) for i in range(13)])]
    r = client.get("/v1/tiktok/user/audience", params={"handle": "someone"})
    assert (r.status_code, r.json()["error"]) == (501, "not_supported")
    assert sample["looked_up"] == []  # six posting followers can't reach the minimum: nothing is asked
    assert (r.json()["sampled_followers"], r.json()["resolved_followers"]) == (13, 0)
    sample["asked_pages"].clear()
    sample["pages"] = [page([follower(i) for i in range(20)])]
    sample["regions"] = {f"fan{i}": "US" for i in range(8)}  # 8 placed: still too few
    r = client.get("/v1/tiktok/user/audience", params={"handle": "someone"})
    assert r.status_code == 501 and r.json()["resolved_followers"] == 8


def test_an_account_without_followers_has_an_empty_audience(client, sample):
    sample["pages"] = [page([])]
    body = client.get("/v1/tiktok/user/audience", params={"handle": "someone"}).json()
    assert body["audienceLocations"] == [] and (body["sampledFollowers"], body["resolvedFollowers"]) == (0, 0)


def test_failed_lookups_are_left_out_and_placing_stops_at_the_maximum(client, sample, monkeypatch):
    monkeypatch.setattr(tiktok, "_AUDIENCE_PARALLEL", 1)  # one at a time, so the stop is exact
    sample["pages"] = [page([follower(i) for i in range(30)], more=True, min_time=5), page([follower(i) for i in range(30, 60)])]
    sample["regions"] = {f"fan{i}": (UpstreamBlocked("blocked") if i < 5 else "US") for i in range(60)}
    body = client.get("/v1/tiktok/user/audience", params={"handle": "someone"}).json()
    assert body["resolvedFollowers"] == tiktok._AUDIENCE_MAX and body["audienceLocations"][0]["count"] == 30
    assert len(sample["looked_up"]) == 5 + tiktok._AUDIENCE_MAX  # nobody asked once 30 were placed


def test_unknown_region_codes_keep_their_code_as_the_name(client, sample):
    sample["pages"] = [page([follower(i) for i in range(10)])]
    sample["regions"] = {f"fan{i}": "QQ" for i in range(10)}
    body = client.get("/v1/tiktok/user/audience", params={"handle": "someone"}).json()
    assert body["audienceLocations"] == [{"country": "QQ", "countryCode": "QQ", "count": 10, "percentage": "100.00%"}]


def test_follower_region_reads_the_newest_own_sound_post(monkeypatch):
    user = follower(1)
    own = {"id": "9", "music": {"id": "555", "original": True, "authorName": "Fan 1"}}
    borrowed = {"id": "8", "music": {"id": "556", "original": False, "authorName": "Someone"}}
    asked = {}
    monkeypatch.setattr(tiktok, "_feed_page", lambda ie, sec_uid, display, cursor, region: (
        asked.update(sec_uid=sec_uid, display=display) or ([borrowed, own], None)))

    def account_region(ydl, prof, items, tries):
        asked.update(prof=prof, items=[item["id"] for item in items], tries=tries)
        return "CA"

    monkeypatch.setattr(tiktok, "_account_region", account_region)

    class Ydl:
        def get_info_extractor(self, name):
            return name

    assert tiktok._follower_region(Ydl(), user) == "CA"
    assert asked == {"sec_uid": "MS4wLjABAAAA1", "display": "fan1", "items": ["9"], "tries": 1,
                     "prof": {"id": "1001", "secUid": "MS4wLjABAAAA1", "nickname": "Fan 1"}}
    monkeypatch.setattr(tiktok, "_feed_page", lambda *args: ([borrowed], None))
    assert tiktok._follower_region(Ydl(), user) is None  # no post with their own sound: not placed


def test_audience_is_listed_as_native(client):
    coverage = {row["path"]: row for row in client.get("/meta/coverage").json()["endpoints"]}
    assert coverage["/v1/tiktok/user/audience"]["status"] == "implemented"
