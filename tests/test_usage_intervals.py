"""Usage analytics buckets: calendar periods in the viewer's timezone for every range and interval."""
import datetime as dt
from zoneinfo import ZoneInfo

from socialscrape.portal import Principal, PortalStore

LA = ZoneInfo("America/Los_Angeles")
OWNER = Principal(1, "owner@example.com", "owner")


def local(*parts):
    return dt.datetime(*parts, tzinfo=LA).timestamp()


def store_with(tmp_path, requests):
    store = PortalStore(str(tmp_path / "portal.sqlite3"))
    store.create_member("owner@example.com", "correct horse battery", "owner")
    store.create_member("member@example.com", "correct horse battery")
    store._conn.executemany(
        "INSERT INTO requests(member_id,created_at,method,path,query,status,duration_ms,fallback) VALUES(?,?,?,?,?,?,?,?)",
        [(member, at, "GET", path, "", 200, 5, fallback) for at, path, member, fallback in requests])
    store._conn.commit()
    return store


def test_daily_buckets_follow_local_midnights_across_daylight_saving(tmp_path):
    now = local(2025, 11, 3, 10, 30)
    store = store_with(tmp_path, [
        (local(2025, 11, 2, 0, 15), "/v1/tiktok/profile", 1, 0),   # just after the Nov 2 local midnight
        (local(2025, 11, 2, 23, 45), "/v1/tiktok/profile", 1, 1),  # the 25-hour fall-back day still holds this
        (local(2025, 11, 1, 23, 59), "/v1/instagram/profile", 1, 0),
        (local(2025, 10, 27, 23, 0), "/v1/youtube/channel", 1, 0),  # before the 7-day window
    ])
    usage = store.usage(OWNER, "7d", "day", "America/Los_Angeles", now=now)
    assert (usage["interval"], usage["timezone"], len(usage["buckets"])) == ("day", "America/Los_Angeles", 7)
    nov1, nov2, nov3 = usage["buckets"][-3:]
    assert (nov1["instagram"], nov2["tiktok"], nov2["fallback"], nov3["total"]) == (1, 2, 1, 0)
    assert nov2["end"] - nov2["start"] == 25 * 3600 and nov3["start"] == local(2025, 11, 3)
    assert usage["range_total"] == 3 and usage["fallback_count"] == 1 and usage["total"] == 4


def test_hour_week_and_month_intervals_cover_their_ranges(tmp_path):
    now = local(2026, 11, 1, 15, 20)
    store = store_with(tmp_path, [
        (local(2026, 11, 1, 15, 5), "/v1/facebook/adLibrary/ad", 1, 0),
        (local(2026, 11, 1, 14, 59), "/v1/google/ad", 1, 0),
        (local(2026, 10, 27, 9, 0), "/v1/tiktok/profile", 1, 0),   # Tuesday of the week before Nov 1 (a Sunday)
        (local(2026, 1, 15, 9, 0), "/v1/account/credit-balance", 1, 0),
    ])
    hourly = store.usage(OWNER, "24h", "hour", "America/Los_Angeles", now=now)
    assert len(hourly["buckets"]) == 24 and hourly["buckets"][-1]["start"] == local(2026, 11, 1, 15)
    assert (hourly["buckets"][-1]["facebook"], hourly["buckets"][-2]["google"], hourly["range_total"]) == (1, 1, 2)
    weekly = store.usage(OWNER, "30d", "week", "America/Los_Angeles", now=now)
    assert len(weekly["buckets"]) == 5 and weekly["buckets"][-1]["start"] == local(2026, 10, 26)  # weeks start Monday
    assert weekly["buckets"][-1]["total"] == 3
    monthly = store.usage(OWNER, "12m", "month", "America/Los_Angeles", now=now)
    assert [dt.datetime.fromtimestamp(bucket["start"], LA).month for bucket in monthly["buckets"]][:3] == [12, 1, 2]
    assert (monthly["buckets"][1]["other"], monthly["buckets"][-2]["tiktok"], monthly["buckets"][-1]["total"]) == (1, 1, 2)
    assert [route["path"] for route in monthly["routes"]] == [
        "/v1/account/credit-balance", "/v1/facebook/adLibrary/ad", "/v1/google/ad", "/v1/tiktok/profile"]


def test_unknown_choices_fall_back_and_members_see_only_their_requests(tmp_path):
    now = local(2026, 11, 1, 12)
    store = store_with(tmp_path, [
        (local(2026, 11, 1, 9), "/v1/tiktok/profile", 1, 0),
        (local(2026, 11, 1, 10), "/v1/tiktok/profile", 2, 0),
        (local(2026, 9, 1, 10), "/v1/tiktok/profile", 2, 0),  # outside 30 days: counted only in the all-time total
    ])
    usage = store.usage(OWNER, "5y", "decade", "../../etc/passwd", now=now)
    assert (usage["range"], usage["interval"], usage["timezone"], len(usage["buckets"])) == ("30d", "day", "UTC", 30)
    assert usage["intervals"] == ["day", "week"] and usage["range_total"] == 2 and usage["total"] == 3
    hourly_month = store.usage(OWNER, "30d", "hour", "UTC", now=now)
    assert hourly_month["interval"] == "day"  # hourly bars would be unreadable over 30 days
    member = store.usage(Principal(2, "member@example.com", "member"), "90d", "", "UTC", now=now)
    assert (member["interval"], member["range_total"], member["total"]) == ("week", 2, 2)
    assert member["routes"] == [{"path": "/v1/tiktok/profile", "count": 2}]


def test_dashboard_offers_exactly_the_ranges_and_intervals_the_server_draws():
    import json
    import re
    from pathlib import Path

    from socialscrape.portal import USAGE_RANGES

    script = (Path(__file__).resolve().parents[1] / "socialscrape" / "static" / "portal.js").read_text()
    table = re.search(r"const USAGE_RANGES = (\{.*?\});\n", script)[1]
    ranges = {key: intervals for key, intervals in re.findall(r"'(\w+)':\{label:'[^']+', intervals:(\[[^\]]*\])\}", table)}
    assert {key: json.loads(value.replace("'", '"')) for key, value in ranges.items()} == {
        key: list(intervals) for key, intervals in USAGE_RANGES.items()}  # same keys, same default-first order
    assert "tz:usageZone()" in script and 'role="slider"' in script


def test_usage_route_reads_range_interval_and_timezone(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_SETUP_TOKEN", "setup")
    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_PATH", str(tmp_path / "portal.sqlite3"))
    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "")
    get_settings.cache_clear()
    client = TestClient(create_app(), base_url="https://testserver")
    client.post("/setup", data={"email": "owner@example.com", "password": "correct horse battery", "setup_token": "setup"})
    client.get("/v1/tiktok/profile/region")
    usage = client.get("/portal/api/usage", params={"range": "24h", "interval": "hour", "tz": "Asia/Kathmandu"}).json()
    assert (usage["range"], usage["interval"], usage["timezone"], len(usage["buckets"])) == ("24h", "hour", "Asia/Kathmandu", 24)
    assert usage["buckets"][-1]["tiktok"] == 1 and usage["start"] % 3600 == 900  # Kathmandu hours start at :15 UTC
    get_settings.cache_clear()
