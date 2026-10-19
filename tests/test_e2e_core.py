"""QA of the core slice: auth, envelope/errors, cache_max_age, the 501 routes, /health, /meta/coverage,
/openapi.json, settings/env handling and resource hygiene. Only the tests marked live reach the platforms.
Anonymous only (no browser cookies, no login).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time

import httpx2
import pytest
import uvicorn
import yt_dlp

from socialscrape import main, ytdlp
from socialscrape.cache import ResponseCache
from socialscrape.catalog import CATALOG
from socialscrape.config import get_settings
from socialscrape.errors import UpstreamError
from socialscrape.main import create_app
from tests.e2e import app_with, free_port, is_envelope, patched_env, skip_if_blocked

YT_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
CATALOG_BY_PATH = {e["path"]: e for e in CATALOG}
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")


# --- helpers -------------------------------------------------------------------


def _coverage(client) -> dict[str, dict]:
    return {row["path"]: row for row in client.get("/meta/coverage").json()["endpoints"]}


def _unsupported(client) -> list[str]:
    return [p for p, row in _coverage(client).items() if row["status"] == "not_supported"]


# --- 501 routes, coverage, openapi, docs, health ---------------------------------


def test_unsupported_routes_are_501_with_reason(client):
    cov = _coverage(client)
    paths = _unsupported(client)
    assert paths, "expected some not_supported endpoints"
    for path in paths:
        dummy = {p: "x" for p in CATALOG_BY_PATH[path]["params"] if p != "cache_max_age"}
        for params in ({}, dummy, {**dummy, "unknown_param": "1"}):
            r = client.get(path, params=params)
            assert r.status_code == 501, (path, params, r.text)
            body = r.json()
            assert body == {"success": False, "error": "not_supported", "message": cov[path]["notes"],
                            "endpoint": path}, path
            assert body["message"]


def test_unsupported_routes_ignore_cache_and_need_key(tmp_path):
    with app_with({"SOCIALSCRAPE_API_KEYS": "qa-key"}, tmp_path) as c:
        for path in _unsupported(c):
            assert c.get(path).status_code == 401, path
            for _ in range(2):
                r = c.get(path, params={"cache_max_age": "1d"}, headers={"x-api-key": "qa-key"})
                assert r.status_code == 501 and r.headers.get("x-cache") != "hit", path
            bad = c.get(path, params={"cache_max_age": "1w"}, headers={"x-api-key": "qa-key"})
            assert bad.status_code == 400 and is_envelope(bad)
    rows = sqlite3.connect(tmp_path / "cache.sqlite3").execute("SELECT count(*) FROM responses").fetchone()[0]
    assert rows == 0


def test_meta_coverage_is_consistent(client):
    r = client.get("/meta/coverage")
    assert r.status_code == 200
    body = r.json()
    rows = body["endpoints"]
    assert body["success"] is True
    assert body["total"] == len(CATALOG) == len(rows)
    assert [row["path"] for row in rows] == [e["path"] for e in CATALOG]
    assert body["implemented"] == sum(row["status"] == "implemented" for row in rows)
    assert 0 < body["implemented"] < body["total"]
    for row in rows:
        assert row["platform"] == row["path"].split("/")[2] and row["platform"] in ("youtube", "tiktok", "instagram")
        assert row["summary"] == CATALOG_BY_PATH[row["path"]]["summary"]
        assert row["notes"], row["path"]
        if row["status"] == "implemented":
            assert row["source"], row["path"]
        else:
            assert row["status"] == "not_supported" and row["source"] is None


def test_openapi_covers_catalog(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    cov = _coverage(client)
    for e in CATALOG:
        assert "get" in spec["paths"].get(e["path"], {}), e["path"]
        if cov[e["path"]]["status"] != "implemented":
            continue
        op = spec["paths"][e["path"]]["get"]
        names = {p["name"] for p in op.get("parameters", [])}
        required = {p["name"] for p in op.get("parameters", []) if p.get("required")}
        assert names - set(e["params"]) <= {"cache_max_age"}, e["path"]  # no invented params (cache: every GET)
        assert set(e["params"]) <= names, e["path"]
        assert required == set(e["required"]), e["path"]


def test_docs_and_redoc(client):
    for path in ("/docs", "/redoc"):
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"], path
        assert "/openapi.json" in r.text


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    h = r.json()
    assert h["ok"] is True
    assert h["yt_dlp"] == yt_dlp.version.__version__
    assert h["auth"] is bool(get_settings().api_keys)
    assert h["implemented_endpoints"] >= client.get("/meta/coverage").json()["implemented"]
    assert isinstance(h["cookies"], list)


def test_health_reflects_settings(tmp_path):
    cookie = tmp_path / "tt.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n")
    env = {"SOCIALSCRAPE_API_KEYS": "k", "SOCIALSCRAPE_COOKIES_TIKTOK": str(cookie),
           "SOCIALSCRAPE_TRANSCRIBE": "none"}
    with app_with(env, tmp_path) as c:
        h = c.get("/health").json()
    assert h["auth"] is True and h["cache"] is True and h["cookies"] == ["tiktok"] and h["transcription"] is None


# --- auth -----------------------------------------------------------------------


@pytest.mark.parametrize("keys,good", [("solo", ["solo"]), (" k1 , k2 ,,", ["k1", "k2"])])
def test_api_key_auth(tmp_path, keys, good):
    with app_with({"SOCIALSCRAPE_API_KEYS": keys}, tmp_path) as c:
        path = _unsupported(c)[0]
        for headers in ({}, {"x-api-key": ""}, {"x-api-key": "wrong"}, {"x-api-key": good[0].upper()},
                        {"x-api-key": ",".join(good) + ",x"}, {"Authorization": f"Bearer {good[0]}"}):
            r = c.get(path, headers=headers)
            assert r.status_code == 401, headers
            assert r.json() == {"success": False, "error": "unauthorized",
                                "message": "Missing or invalid x-api-key header"}
        assert c.get(path, params={"x-api-key": good[0]}).status_code == 401  # header only, like ScrapeCreators
        for key in good:
            assert c.get(path, headers={"x-api-key": key}).status_code == 501
            assert c.get(path, headers={"X-API-KEY": key}).status_code == 501
        # implemented routes are guarded before any upstream work
        assert c.get("/v1/tiktok/profile", params={"handle": "tiktok"}).status_code == 401
        assert c.get("/v1/tiktok/profile").status_code == 401
        for public in ("/health", "/docs", "/redoc", "/openapi.json", "/meta/coverage"):
            assert c.get(public).status_code == 200, public
        assert c.get("/health/", follow_redirects=False).status_code == 401  # exact-match public paths only


def test_auth_runs_before_cache(tmp_path):
    db = tmp_path / "seed.sqlite3"
    ResponseCache(str(db)).put("/v1/tiktok/profile?handle=qa-seeded", '{"success": true, "seeded": 1}')
    env = {"SOCIALSCRAPE_API_KEYS": "k1", "SOCIALSCRAPE_CACHE_PATH": str(db)}
    with app_with(env) as c:
        params = {"handle": "qa-seeded", "cache_max_age": "1d"}
        assert c.get("/v1/tiktok/profile", params=params).status_code == 401
        hit = c.get("/v1/tiktok/profile", params=params, headers={"x-api-key": "k1"})
        assert hit.status_code == 200 and hit.headers["x-cache"] == "hit"
        assert hit.json()["seeded"] == 1 and hit.json()["cached"] is True


# --- cache_max_age ----------------------------------------------------------------


@pytest.mark.live
def test_cache_max_age_live(tmp_path):
    with app_with({}, tmp_path) as c:
        base = {"handle": "stoolpresidente"}
        r = c.get("/v1/tiktok/profile", params=base)
        skip_if_blocked(r)
        assert r.status_code == 200 and r.headers["x-cache"] == "miss", r.text
        live = r.json()
        assert live["user"]["uniqueId"] == "stoolpresidente" and "cached" not in live

        for age in ("1d", "3d", "7d", "14d", "30d", "12h", "1h", "1D", "24H", " 1d", "1 d", "5", "999d"):
            h = c.get("/v1/tiktok/profile", params={**base, "cache_max_age": age})
            assert h.status_code == 200 and h.headers["x-cache"] == "hit", age
            body = h.json()
            assert body.pop("cached") is True and ISO_Z.match(body.pop("cached_at")), age
            assert body == live, age
        # parameter order and the position of cache_max_age do not matter
        assert c.get("/v1/tiktok/profile?cache_max_age=7d&handle=stoolpresidente").headers["x-cache"] == "hit"
        # a different request is never served from another one's entry
        other = c.get("/v1/tiktok/profile", params={"handle": "stoolpresidente", "user_id": "", "cache_max_age": "1d"})
        assert other.headers.get("x-cache") != "hit"

        for bad in ("abc", "-1", "-1d", "1w", "1.5d", "1d1h", "d"):
            e = c.get("/v1/tiktok/profile", params={**base, "cache_max_age": bad})
            assert e.status_code == 400 and is_envelope(e), bad
            assert "cache_max_age" in e.json()["message"]

        # expiry: age the stored row by two days
        con = sqlite3.connect(tmp_path / "cache.sqlite3")
        con.execute("UPDATE responses SET stored_at = stored_at - 2 * 86400")
        con.commit()
        assert c.get("/v1/tiktok/profile", params={**base, "cache_max_age": "3d"}).headers["x-cache"] == "hit"
        assert c.get("/v1/tiktok/profile", params={**base, "cache_max_age": "47h"}).headers.get("x-cache") != "hit"
        fresh = c.get("/v1/tiktok/profile", params={**base, "cache_max_age": "0"})  # 0 = always live
        skip_if_blocked(fresh)
        assert fresh.status_code == 200 and fresh.headers["x-cache"] == "miss" and "cached" not in fresh.json()
        assert c.get("/v1/tiktok/profile", params={**base, "cache_max_age": "1h"}).headers["x-cache"] == "hit"


def test_errors_are_not_cached(tmp_path):
    with app_with({}, tmp_path) as c:
        for _ in range(2):
            r = c.get("/v1/tiktok/profile", params={"cache_max_age": "1d"})  # 400 missing handle
            assert r.status_code == 400 and r.headers.get("x-cache") != "hit"
            r = c.get("/v1/youtube/video", params={"cache_max_age": "1d"})
            assert r.status_code == 400 and is_envelope(r)
    rows = sqlite3.connect(tmp_path / "cache.sqlite3").execute("SELECT count(*) FROM responses").fetchone()[0]
    assert rows == 0


# --- errors / envelope / params ---------------------------------------------------


def test_validation_errors_are_400_envelopes(client):
    cases = [
        ("/v1/tiktok/profile", {}, "handle"),
        ("/v1/youtube/video", {}, "url"),
        ("/v1/tiktok/profile", {"handle": "a" * 10_000}, None),
        ("/v1/tiktok/profile", {"handle": "\U0001f600"}, None),
        ("/v1/tiktok/profile", {"handle": "a\x00b"}, None),
        ("/v1/tiktok/profile", {"handle": "a\r\nb"}, None),
    ]
    for path, params, name in cases:
        r = client.get(path, params=params)
        assert r.status_code == 400 and is_envelope(r), (path, params, r.text[:200])
        if name:
            assert name in r.json()["message"], r.json()


def test_trailing_slash_redirects(client):
    r = client.get("/v1/tiktok/profile/", params={"handle": "x"}, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].endswith("/v1/tiktok/profile?handle=x")


def test_malformed_cursor_is_400(client):
    for token in ("garbage", "ss1.", "ss1.!!!!", "ss1." + base64.urlsafe_b64encode(b"[1]").decode(),
                  "ss1." + base64.urlsafe_b64encode(b"\xff\xfe").decode()):
        r = client.get("/v1/youtube/channel-videos", params={"continuationToken": token})
        assert r.status_code == 400 and r.json()["error"] == "bad_cursor", token


# --- openapi accuracy -------------------------------------------------------------


def test_openapi_lists_no_params_on_501_routes(client):
    # by design (registry.add_unsupported_routes): a 501 route ignores every param, catalog ones included (see
    # test_unsupported_routes_are_501_with_reason), so it documents none and is labelled as unsupported instead
    spec = client.get("/openapi.json").json()["paths"]
    for path in _unsupported(client):
        op = spec[path]["get"]
        assert "parameters" not in op, path
        assert op["tags"] == ["not supported"] and op["summary"].endswith("(not supported)"), path


# --- settings / env ----------------------------------------------------------------


@pytest.mark.live
def test_max_workers_one_serialises_without_blocking_health(tmp_path):
    handles = ["stoolpresidente", "tiktok", "khaby.lame"]

    async def go(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://t", timeout=120) as c:
            tasks = [asyncio.create_task(c.get("/v1/tiktok/profile", params={"handle": h})) for h in handles]
            await asyncio.sleep(0.3)
            t0 = time.monotonic()
            health = await c.get("/health")
            health_s = time.monotonic() - t0
            return await asyncio.gather(*tasks), health, health_s

    with patched_env({"SOCIALSCRAPE_MAX_WORKERS": "1", "SOCIALSCRAPE_CACHE_PATH": str(tmp_path / "c.sqlite3")}):
        responses, health, health_s = asyncio.run(go(create_app()))
        assert ytdlp._executor is not None and ytdlp._executor._max_workers == 1
    assert health.status_code == 200 and health_s < 1.0
    for h, r in zip(handles, responses):
        skip_if_blocked(r)
        assert r.status_code == 200, r.text
        assert r.json()["user"]["uniqueId"].lower() == h


def test_request_timeout_is_enforced(tmp_path):
    slow = lambda platform, fn, opts: time.sleep(1.5)  # noqa: E731
    with app_with({"SOCIALSCRAPE_REQUEST_TIMEOUT": "0.3"}, tmp_path, patches=[(ytdlp, "_run", slow)]) as c:
        t0 = time.monotonic()
        r = c.get("/v1/tiktok/profile", params={"handle": "tiktok"})
        elapsed = time.monotonic() - t0
    # a 500 with its own code, as the README documents (ScrapeCreators defines no timeout status, so no 504)
    assert elapsed < 1.2 and r.status_code == 500
    assert r.json() == {"success": False, "error": "timeout", "message": "Timed out waiting for the platform"}


def test_call_queued_behind_a_timed_out_one_times_out_too():
    # README caveat: a timed-out yt-dlp call keeps its worker until it ends, so with one worker the next call times
    # out waiting for it. That call is dropped, not run late, and the worker serves again once the stuck one ends.
    stuck_done = threading.Event()
    ran: list[str] = []

    def stuck(_):
        time.sleep(2.5)
        stuck_done.set()

    async def go():
        for fn in (stuck, lambda _: ran.append("queued")):
            with pytest.raises(UpstreamError) as exc:
                await ytdlp.with_ydl("tiktok", fn)
            assert exc.value.code == "timeout"
        await asyncio.to_thread(stuck_done.wait, 5)
        return await ytdlp.with_ydl("tiktok", lambda _: "ok")

    env = {"SOCIALSCRAPE_MAX_WORKERS": "1", "SOCIALSCRAPE_REQUEST_TIMEOUT": "1"}
    with patched_env(env, [(ytdlp, "_run", lambda platform, fn, opts: fn(None))]):
        assert asyncio.run(go()) == "ok"
    assert not ran


def test_dead_proxy_is_classified(tmp_path):
    with app_with({"SOCIALSCRAPE_PROXY": f"http://127.0.0.1:{free_port()}"}, tmp_path) as c:
        r = c.get("/v1/tiktok/profile", params={"handle": "tiktok"})
    assert is_envelope(r)
    assert r.json()["error"] != "upstream_error" and "curl:" not in r.json()["message"], r.json()


@pytest.mark.parametrize("name,value", [("SOCIALSCRAPE_LOG_LEVEL", "debug"), ("SOCIALSCRAPE_LOG_LEVEL", "info"),
                                        ("PORT", "")])
def test_run_tolerates_env(monkeypatch, name, value):
    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append(k))
    monkeypatch.setenv(name, value)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers.clear()  # basicConfig only applies (and validates) the level when root has no handlers
    try:
        main.run([])  # no CLI args (pytest's are in sys.argv)
    finally:
        for h in root.handlers:
            if h not in saved_handlers:
                h.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
    assert calls and isinstance(calls[0]["port"], int)


def test_empty_host_keeps_loopback_default(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append(k))
    monkeypatch.setenv("HOST", "")
    monkeypatch.setattr(logging, "basicConfig", lambda **k: None)
    main.run([])
    assert calls[0]["host"] == "127.0.0.1"


def test_run_defaults(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append(k))
    monkeypatch.setattr(logging, "basicConfig", lambda **k: None)
    for name in ("HOST", "PORT", "SOCIALSCRAPE_LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    main.run([])
    assert calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == 8000 and calls[0]["log_level"] == logging.INFO


# --- resource hygiene -------------------------------------------------------------------


@pytest.mark.live
def test_subtitle_temp_dirs_are_removed(tmp_path, monkeypatch):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    created: list[str] = []
    real = tempfile.TemporaryDirectory

    class Recording(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            created.append(self.name)

    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setattr(tempfile, "TemporaryDirectory", Recording)
    with app_with({}, tmp_path) as c:
        r = c.get("/v1/youtube/video/transcript", params={"url": YT_VIDEO})
    skip_if_blocked(r)
    assert r.status_code == 200, r.text[:300]
    ours = [p for p in created if os.path.basename(p).startswith("socialscrape-")]
    assert ours, "expected the subtitle download to use a socialscrape-subs-* temp dir"
    assert not [p for p in ours if os.path.exists(p)]
    assert not [n for n in os.listdir(scratch) if n.startswith("socialscrape-")]


def test_no_thread_growth_on_cheap_routes(client):
    paths = _unsupported(client)[:5] + ["/health", "/meta/coverage"]
    for p in paths:
        client.get(p)
    before = threading.active_count()
    for _ in range(10):
        for p in paths:
            client.get(p)
    assert threading.active_count() <= before + 1
