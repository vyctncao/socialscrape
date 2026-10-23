import time

from fastapi.testclient import TestClient


def portal_client(monkeypatch, tmp_path):
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_PATH", str(tmp_path / "portal.sqlite3"))
    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "legacy")
    get_settings.cache_clear()
    app = create_app()
    store = app.state.portal
    owner = store.create_member("owner@example.com", "owner-password", "owner")
    member = store.create_member("member@example.com", "member-password")
    _, owner_key = store.create_key(owner, "owner")
    _, member_key = store.create_key(member, "member")
    return TestClient(app), store, owner_key, member_key, get_settings


def test_credit_balance_and_legacy_scope(monkeypatch, tmp_path):
    client, _, _, _, clear = portal_client(monkeypatch, tmp_path)
    try:
        balance = client.get("/v1/account/credit-balance", headers={"x-api-key": "legacy"})
        assert balance.json() == {"success": True, "creditCount": None, "unlimited": True,
                                  "message": "SocialScrape doesn't charge credits; requests are unlimited."}
        denied = client.get("/v1/account/get-api-usage", headers={"x-api-key": "legacy"})
        assert denied.status_code == 403
        assert denied.json()["error"] == "member_identity_required"
    finally:
        clear.cache_clear()


def test_account_usage_scopes_and_aggregates(monkeypatch, tmp_path):
    client, store, owner_key, member_key, clear = portal_client(monkeypatch, tmp_path)
    try:
        owner = store.authenticate_key(owner_key)[0]
        member = store.authenticate_key(member_key)[0]
        store.record_request(owner, None, "GET", "/v1/youtube/video", "url=x", 200, 10, False)
        store.record_request(member, None, "GET", "/v1/tiktok/profile", "handle=x", 404, 12, False)
        member_history = client.get("/v1/account/get-api-usage", headers={"x-api-key": member_key}).json()
        assert len(member_history) == 1 and member_history[0]["endpoint"].startswith("/v1/tiktok/profile")
        owner_history = client.get("/v1/account/get-api-usage", headers={"x-api-key": owner_key}).json()
        # This request itself is recorded after its response; both seeded rows are visible here.
        assert {row["endpoint"].split("?", 1)[0] for row in owner_history} >= {
            "/v1/youtube/video", "/v1/tiktok/profile"}
        filtered = client.get("/v1/account/get-api-usage", params={"statusCode": "404"},
                              headers={"x-api-key": owner_key}).json()
        assert filtered and all(row["status_code"] == 404 for row in filtered)
        daily = client.get("/v1/account/get-daily-usage-count", headers={"x-api-key": member_key}).json()
        assert len(daily) == 30 and int(daily[0]["request_count"]) >= 1
        routes = client.get("/v1/account/get-most-used-routes",
                            params={"start_time": "2020-01-01T00:00:00Z",
                                    "end_time": "2030-01-01T00:00:00Z"},
                            headers={"x-api-key": owner_key})
        assert routes.status_code == 400  # range longer than one year
        routes = client.get("/v1/account/get-most-used-routes",
                            params={"start_time": "2026-01-01T00:00:00Z",
                                    "end_time": "2026-12-31T00:00:00Z"},
                            headers={"x-api-key": owner_key})
        assert routes.status_code == 200
    finally:
        clear.cache_clear()
