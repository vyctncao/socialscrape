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


def test_a_thin_sample_is_answered_with_its_counts(client, sample):
    """It never goes to the fallback: a small sample says how small it is. (emma.collins920: 2 of 13 followers placed
    read 100% NG, where ScrapeCreators counted 83% US, because her US followers never post.)"""
    sample["pages"] = [page([follower(i, posts=1 if i < 2 else 0) for i in range(13)])]
    sample["regions"] = {"fan0": "NG", "fan1": "NG"}
    body = client.get("/v1/tiktok/user/audience", params={"handle": "someone"}).json()
    assert body["audienceLocations"] == [{"country": "Nigeria", "countryCode": "NG", "count": 2, "percentage": "100.00%"}]
    assert (body["sampledFollowers"], body["resolvedFollowers"]) == (13, 2) and sample["looked_up"] == ["fan0", "fan1"]
    sample["asked_pages"].clear()
    sample["regions"] = {}  # none placed: an empty list, with the sample it came from
    body = client.get("/v1/tiktok/user/audience", params={"handle": "someone"}).json()
    assert body["audienceLocations"] == [] and (body["sampledFollowers"], body["resolvedFollowers"]) == (13, 0)


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


def test_follower_region_reads_their_newest_post_through_pages_only(monkeypatch):
    user = follower(1)
    posts = {"10": {"id": "10", "locationCreated": "mx"},
             "11": {"id": "11", "music": {"id": "555", "original": True, "authorName": "Fan 1"}},
             "12": {"id": "12", "music": {"id": "556", "original": False, "authorName": "Someone"}}}
    embed, read, feeds = ["10"], [], []
    monkeypatch.setattr(tiktok, "_embed_ids", lambda ydl, name: list(embed))
    monkeypatch.setattr(tiktok, "_embed_post", lambda ydl, name, aweme_id: read.append(aweme_id) or posts.get(aweme_id))
    monkeypatch.setattr(tiktok, "_feed_page", lambda *args: pytest.fail("item_list is never asked"))

    def account_region(ydl, prof, items, tries):
        feeds.append((prof, [item["id"] for item in items], tries))
        return "CA"

    monkeypatch.setattr(tiktok, "_account_region", account_region)
    assert tiktok._follower_region(None, user) == "MX" and read == ["10"] and feeds == []  # where the post was made
    embed[:] = ["11"]  # no location, but their own sound: the app feed's region
    assert tiktok._follower_region(None, user) == "CA"
    assert feeds == [({"id": "1001", "secUid": "MS4wLjABAAAA1", "nickname": "Fan 1"}, ["11"], 1)]
    embed[:], read[:] = ["99", "12", "10"], []  # a removed post, then a borrowed sound: the first two only
    assert tiktok._follower_region(None, user) is None and read == ["99", "12"]
    assert tiktok._follower_region(None, {**user, "unique_id": None}) is None  # no handle: no embed page


def test_audience_is_listed_as_native(client):
    coverage = {row["path"]: row for row in client.get("/meta/coverage").json()["endpoints"]}
    assert coverage["/v1/tiktok/user/audience"]["status"] == "implemented"


def test_post_location_reads_the_newest_post_pages_and_passes_over_failures(monkeypatch):
    pages = {"1": UpstreamBlocked("blocked"), "2": {"locationCreated": "de"}, "3": {"locationCreated": "Earth"}}
    loaded = []

    def load_post(ydl, url):
        aweme_id = url.rsplit("/", 1)[1]
        loaded.append(aweme_id)
        if isinstance(pages[aweme_id], Exception):
            raise pages[aweme_id]
        return None, pages[aweme_id]

    monkeypatch.setattr(tiktok, "_load_post", load_post)
    assert tiktok._post_location(None, "someone", [{"id": "1"}, {"id": "2"}, {"id": "4"}]) == "DE"
    assert loaded == ["1", "2"]  # the newest two at most
    loaded.clear()
    assert tiktok._post_location(None, "someone", [{"id": "9", "locationCreated": "US"}]) == "US" and loaded == []
    assert tiktok._post_location(None, "someone", [{"id": "3"}]) is None  # not a country code
    assert tiktok._post_location(None, "someone", [{"id": "1"}, {"id": "2"}], tries=1) is None



def test_private_tiktok_content_is_flagged_so_it_is_never_sent_to_the_fallback():
    for status, message in ((10222, "This account is private"), (10216, "This post is private")):
        assert tiktok._status_error(status).body() == {"success": False, "error": "forbidden", "message": message,
                                                       "private": True}


def test_lookups_stop_starting_steps_the_native_budget_cant_finish(monkeypatch, client, sample):
    """With error fallback on, a slow native answer becomes a paid one at the native budget: a lookup answers with
    what it has instead of starting a step it can't finish."""
    import time
    from socialscrape import budget

    asked = []
    monkeypatch.setattr(tiktok, "_music_page", lambda ydl, clip, cursor: asked.append(clip) or {"aweme_list": []})
    monkeypatch.setattr(tiktok, "_load_post", lambda ydl, url: asked.append(url) or (None, {"locationCreated": "US"}))
    prof = {"id": "42", "nickname": "Creator"}
    posts = [{"id": str(i), "music": {"id": str(500 + i), "original": True, "authorName": "Creator"}} for i in range(3)]
    token = budget.deadline.set(time.monotonic() + 5.0)  # under _SOUND_RESERVE, over _PAGE_RESERVE
    try:
        assert tiktok._account_region(None, prof, posts) is None and asked == ["500"]  # one answered: stop there
        asked.clear()
        assert tiktok._post_location(None, "creator", posts) == "US" and len(asked) == 1
        budget.deadline.set(time.monotonic() + 1.0)  # under _PAGE_RESERVE: no post page at all
        asked.clear()
        assert tiktok._post_location(None, "creator", posts) is None and asked == []
        assert tiktok._post_location(None, "creator", [{"id": "9", "locationCreated": "GB"}]) == "GB"  # no page needed
    finally:
        budget.deadline.reset(token)
    asked.clear()
    assert tiktok._account_region(None, prof, posts) is None and asked == ["500", "501", "502"]  # no budget: all asked


def test_an_audience_with_no_budget_left_answers_with_the_followers_placed(client, sample, monkeypatch):
    sample["pages"] = [page([follower(i) for i in range(12)])]
    sample["regions"] = {f"fan{i}": "US" for i in range(12)}
    monkeypatch.setattr(tiktok.budget, "remaining", lambda default: 1.0)  # under _LOOKUP_RESERVE from the start
    body = client.get("/v1/tiktok/user/audience", params={"handle": "someone"}).json()
    assert sample["looked_up"] == [] and body["audienceLocations"] == []
    assert (body["sampledFollowers"], body["resolvedFollowers"]) == (12, 0)
