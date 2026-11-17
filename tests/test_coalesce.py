"""Identical requests in flight at the same time share one answer (socialscrape/coalesce.py): a tracking batch that
asks for the same post several times at once scrapes it once, and pays ScrapeCreators at most once."""
import asyncio
import time
from types import SimpleNamespace

import httpx2 as httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from socialscrape.coalesce import CoalesceMiddleware


def small_app():
    """The middleware in front of slow routes that record each time they run; x-user stands for the caller."""
    app, calls = FastAPI(), []

    @app.get("/v1/tiktok/profile")
    async def profile(handle: str):
        calls.append(handle)
        await asyncio.sleep(0.2)
        if handle == "boom" and calls.count("boom") == 1:
            raise RuntimeError("the first one fails")
        if handle == "gone":
            return JSONResponse({"success": False, "error": "not_found"}, status_code=404)
        return {"success": True, "handle": handle, "run": len(calls)}

    @app.get("/v1/account/credit-balance")
    async def balance():
        calls.append("balance")
        await asyncio.sleep(0.2)
        return {"credits_remaining": len(calls)}

    @app.post("/v1/tiktok/profile")
    async def post_profile():
        calls.append("post")
        await asyncio.sleep(0.2)
        return {"success": True}

    class Caller(BaseHTTPMiddleware):  # as ApiKeyMiddleware sets it, before the sharing middleware runs
        async def dispatch(self, request, call_next):
            request.state.principal = SimpleNamespace(id=request.headers.get("x-user", "1"))
            return await call_next(request)

    app.add_middleware(CoalesceMiddleware)
    app.add_middleware(Caller)
    return app, calls


def run_together(app, *requests):
    """(method, url, headers) requests sent at the same time; their responses in order."""
    async def go():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(*(client.request(method, url, headers=headers)
                                          for method, url, headers in requests))
    return asyncio.run(go())


def test_identical_requests_at_the_same_time_share_one_answer():
    app, calls = small_app()
    answers = run_together(app, *[("GET", "/v1/tiktok/profile?handle=a", {})] * 4)
    assert calls == ["a"]
    assert {r.status_code for r in answers} == {200} and len({r.content for r in answers}) == 1
    assert answers[0].json() == {"success": True, "handle": "a", "run": 1}


def test_requests_that_differ_in_what_decides_the_answer_are_not_shared():
    app, calls = small_app()
    base = "/v1/tiktok/profile?handle=a"
    run_together(app, ("GET", base, {}), ("GET", "/v1/tiktok/profile?handle=b", {}),
                 ("GET", base + "&trim=true", {}), ("GET", "/v1/tiktok/profile?trim=true&handle=a", {}),
                 ("GET", base, {"x-user": "2"}), ("GET", base, {"x-socialscrape-fallback": "off"}),
                 ("GET", base, {"via": "1.1 socialscrape"}))
    assert len(calls) == 7  # the query's own order counts: repeated parameters keep theirs for cursors


def test_a_request_made_after_the_answer_is_asked_afresh():
    app, calls = small_app()
    first = run_together(app, ("GET", "/v1/tiktok/profile?handle=a", {}))[0]
    second = run_together(app, ("GET", "/v1/tiktok/profile?handle=a", {}))[0]
    assert calls == ["a", "a"] and (first.json()["run"], second.json()["run"]) == (1, 2)


def test_account_routes_and_posts_are_never_shared():
    app, calls = small_app()
    run_together(app, *[("GET", "/v1/account/credit-balance", {})] * 2, *[("POST", "/v1/tiktok/profile", {})] * 2)
    assert sorted(calls) == ["balance", "balance", "post", "post"]


def test_an_error_answer_is_shared_like_any_other():
    app, calls = small_app()
    answers = run_together(app, *[("GET", "/v1/tiktok/profile?handle=gone", {})] * 3)
    assert calls == ["gone"] and [r.status_code for r in answers] == [404, 404, 404]


def test_when_the_first_request_fails_the_others_ask_again_together():
    app, calls = small_app()
    answers = run_together(app, *[("GET", "/v1/tiktok/profile?handle=boom", {})] * 3)
    assert answers[0].status_code == 500
    assert [r.status_code for r in answers[1:]] == [200, 200] and answers[1].content == answers[2].content
    assert calls == ["boom", "boom"]  # the copies' second ask is shared too


def test_the_real_stack_shares_one_scrape_and_records_every_request(tmp_path, monkeypatch):
    """Through create_app: each copy is authenticated and gets its own history row; the platform is asked once."""
    from socialscrape.config import get_settings
    from socialscrape.main import create_app
    from socialscrape.platforms import tiktok

    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_PATH", str(tmp_path / "portal.sqlite3"))
    monkeypatch.setenv("SOCIALSCRAPE_CACHE_PATH", "")
    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "")
    monkeypatch.setenv("SOCIALSCRAPE_SETUP_TOKEN", "setup")
    for name in ("SOCIALSCRAPE_SCRAPECREATORS_API_KEY", "SOCIALSCRAPE_SCRAPECREATORS_FALLBACK"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    scraped = []

    async def with_ydl(platform, fn, **options):
        scraped.append(platform)
        await asyncio.sleep(0.3)
        return {"user": {"id": "1", "uniqueId": "someone"}, "stats": {"followerCount": 1}}

    monkeypatch.setattr(tiktok, "with_ydl", with_ydl)
    try:
        app = create_app()
        TestClient(app, base_url="https://testserver").post(
            "/setup", data={"email": "owner@example.com", "password": "correct horse battery", "setup_token": "setup"})
        store = app.state.portal
        owner_id = store._conn.execute("SELECT id FROM members WHERE email='owner@example.com'").fetchone()[0]
        _, token = store.create_key(owner_id, "tracking")
        started = time.monotonic()
        answers = run_together(app, *[("GET", "/v1/tiktok/profile?handle=someone", {"x-api-key": token})] * 3,
                               ("GET", "/v1/tiktok/profile?handle=someone", {"x-api-key": "wrong"}))
        assert [r.status_code for r in answers] == [200, 200, 200, 401]
        assert scraped == ["tiktok"] and time.monotonic() - started < 2
        assert len({r.content for r in answers[:3]}) == 1
        rows = store._conn.execute("SELECT path, status FROM requests ORDER BY id").fetchall()
        assert [tuple(row) for row in rows] == [("/v1/tiktok/profile", 200)] * 3
    finally:
        get_settings.cache_clear()
