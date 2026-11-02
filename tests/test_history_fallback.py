"""Request history can be narrowed to requests ScrapeCreators served (or the rest), with all-time counts."""
import time
from pathlib import Path

from fastapi.testclient import TestClient

from socialscrape.portal import Principal, PortalStore


def seeded(tmp_path):
    store = PortalStore(str(tmp_path / "portal.sqlite3"))
    owner = store.create_member("owner@example.com", "correct horse battery", "owner")
    member = store.create_member("member@example.com", "correct horse battery")
    now = time.time()
    rows = [(owner, now - 50, "/v1/tiktok/profile", 1), (owner, now - 40, "/v1/instagram/post", 0),
            (member, now - 30, "/v2/tiktok/video", 1), (member, now - 20, "/v1/youtube/channel", 0),
            (member, now - 10, "/v1/youtube/video", 0)]
    store._conn.executemany(
        "INSERT INTO requests(member_id,created_at,method,path,query,status,duration_ms,fallback) VALUES(?,?,?,?,?,?,?,?)",
        [(member_id, at, "GET", path, "", 200, 10, fallback) for member_id, at, path, fallback in rows])
    store._conn.commit()
    return store, Principal(owner, "owner@example.com", "owner"), Principal(member, "member@example.com", "member")


def test_history_filters_by_fallback_and_counts_every_request(tmp_path):
    store, owner, member = seeded(tmp_path)
    assert [row["path"] for row in store.history(owner, 200, True)] == ["/v2/tiktok/video", "/v1/tiktok/profile"]
    assert [row["path"] for row in store.history(owner, 200, False)] == [
        "/v1/youtube/video", "/v1/youtube/channel", "/v1/instagram/post"]
    assert len(store.history(owner, 200)) == 5 and len(store.history(owner, 1, False)) == 1
    assert store.history_counts(owner) == {"all": 5, "fallback": 2, "direct": 3}
    assert [row["path"] for row in store.history(member, 200, True)] == ["/v2/tiktok/video"]  # members see their own
    assert store.history_counts(member) == {"all": 3, "fallback": 1, "direct": 2}


def test_history_route_takes_the_fallback_filter(tmp_path, monkeypatch):
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_SETUP_TOKEN", "setup")
    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_PATH", str(tmp_path / "portal.sqlite3"))
    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "")
    get_settings.cache_clear()
    try:
        app = create_app()
        client = TestClient(app, base_url="https://testserver")
        client.post("/setup", data={"email": "owner@example.com", "password": "correct horse battery",
                                    "setup_token": "setup"})
        store = app.state.portal
        store._conn.executemany(
            "INSERT INTO requests(member_id,created_at,method,path,query,status,duration_ms,fallback) VALUES(1,?,?,?,?,?,?,?)",
            [(time.time() - 5, "GET", "/v1/tiktok/profile", "", 200, 10, 1),
             (time.time() - 4, "GET", "/v1/instagram/post", "", 200, 10, 0)])
        store._conn.commit()
        only = client.get("/portal/api/history", params={"limit": 200, "fallback": "true"}).json()
        assert [row["path"] for row in only["requests"]] == ["/v1/tiktok/profile"] and only["requests"][0]["fallback"] == 1
        assert only["counts"] == {"all": 2, "fallback": 1, "direct": 1}
        direct = client.get("/portal/api/history", params={"fallback": "false"}).json()["requests"]
        assert [row["path"] for row in direct] == ["/v1/instagram/post"]
        assert len(client.get("/portal/api/history").json()["requests"]) == 2
    finally:
        get_settings.cache_clear()


def test_history_table_marks_fallback_rows_and_offers_the_filter():
    script = (Path(__file__).resolve().parents[1] / "socialscrape" / "static" / "portal.js").read_text()
    assert '<th scope="col" class="history-fallback">Fallback</th>' in script
    assert "Yes, served by ScrapeCreators" in script and '<span class="sr-only">No</span>' in script
    assert 'name="history-fallback"' in script and "url.searchParams.set('fallback', filter)" in script
    assert "&fallback=${filter === 'yes'}" in script
    assert '<span class="badge">via ScrapeCreators</span>' not in script  # the column replaces the old status badge
