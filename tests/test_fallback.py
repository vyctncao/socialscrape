"""The ScrapeCreators fallback (socialscrape/fallback.py), offline: a fake ScrapeCreators on localhost records what
it is sent and answers as scripted, and local failures come from a patched /v1/tiktok/profile or a 501 route."""

from __future__ import annotations

import json
import logging
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from socialscrape import fallback
from socialscrape.config import ConfigError, get_settings
from socialscrape.errors import (BadRequest, Forbidden, NotFound, NotSupported, UpstreamBlocked, UpstreamError,
                                 UpstreamUnreachable)
from socialscrape.main import create_app
from socialscrape.platforms import facebook_ads, tiktok, tiktok_ads
from socialscrape.portal import PortalStore

CLIENT_KEY = "client-key"
SC_KEY = "sc-secret-key-123"
UNSUPPORTED = "/v1/tiktok/video/comments"  # a 501 route
PROFILE = "/v1/tiktok/profile"  # implemented: its with_ydl is patched
SC_OK = {"success": True, "credits_remaining": 99, "comments": [{"text": "hi"}]}


class Upstream:
    """A fake ScrapeCreators: each request is recorded (path, query pairs, lower-cased headers), and answered with
    the next scripted reply, else SC_OK."""

    def __init__(self):
        self.requests: list[dict] = []
        self.replies: list[tuple[int, bytes, dict, float]] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._answer()

            def do_POST(self):
                self._answer()

            def _answer(self):
                parts = urlsplit(self.path)
                length = int(self.headers.get("content-length", "0"))
                upstream.requests.append({"method": self.command, "path": parts.path,
                                          "query": parse_qsl(parts.query, keep_blank_values=True),
                                          "headers": {k.lower(): v for k, v in self.headers.items()},
                                          "body": self.rfile.read(length).decode("utf-8")})
                status, body, headers, delay = (upstream.replies.pop(0) if upstream.replies
                                                else (200, json.dumps(SC_OK).encode(), {}, 0))
                time.sleep(delay)
                try:
                    self.send_response(status)
                    for name, value in {"content-type": "application/json", **headers}.items():
                        self.send_header(name, value)
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:  # the client gave up first (the timeout tests)
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reply(self, status: int, body: dict | list | bytes = b"", *, headers: dict | None = None, delay: float = 0):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.replies.append((status, raw, headers or {}, delay))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def upstream():
    server = Upstream()
    yield server
    server.close()


@pytest.fixture
def make(monkeypatch, upstream):
    """make(ENV=value, ...): a client of an app with an API key, a ScrapeCreators key and the fake upstream (None
    unsets a variable). Its requests send the client key."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
                 "SOCIALSCRAPE_SCRAPECREATORS_FALLBACK", "SOCIALSCRAPE_SCRAPECREATORS_PLATFORMS",
                 "SOCIALSCRAPE_PORTAL_PATH", "SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY", "SOCIALSCRAPE_SETUP_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(fallback, "_executor", None)

    def build(**env: str | None) -> TestClient:
        values = {"SOCIALSCRAPE_API_KEYS": CLIENT_KEY, "SOCIALSCRAPE_SCRAPECREATORS_API_KEY": SC_KEY,
                  "SOCIALSCRAPE_SCRAPECREATORS_URL": upstream.url, "SOCIALSCRAPE_CACHE_PATH": "", **env}
        for name, value in values.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        return TestClient(create_app(), base_url="https://testserver", headers={"x-api-key": CLIENT_KEY},
                          raise_server_exceptions=False)

    yield build
    fallback.shutdown_pool()
    get_settings.cache_clear()


def fail_profile(monkeypatch, exc: BaseException | None = None) -> None:
    """/v1/tiktok/profile raises `exc` instead of scraping (or, with None, succeeds)."""
    async def with_ydl(*args, **kwargs):
        if exc is not None:
            raise exc
        return {"user": {"id": "1", "uniqueId": "someone"}, "stats": {"followerCount": 1}}

    monkeypatch.setattr(tiktok, "with_ydl", with_ydl)


def test_an_unsupported_route_is_served_by_scrapecreators_with_only_its_key(make, upstream):
    c = make()
    r = c.get(UNSUPPORTED, params=[("url", "https://www.tiktok.com/@a/video/1?x=a b"), ("cursor", "20"),
                                   ("tag", "a"), ("tag", "b"), ("cache_max_age", "1d")],
              headers={"cookie": "session=private", "x-portal-key-id": "2", "authorization": "Bearer private"})
    assert r.status_code == 200 and r.json() == SC_OK
    assert r.headers[fallback.HEADER] == fallback.PROVIDER
    [sent] = upstream.requests
    assert sent["path"] == UNSUPPORTED
    # Verbatim but for cache_max_age (repeats and encoding kept).
    assert sent["query"] == [("url", "https://www.tiktok.com/@a/video/1?x=a b"), ("cursor", "20"), ("tag", "a"),
                             ("tag", "b")]
    assert sent["headers"]["x-api-key"] == SC_KEY
    assert sent["headers"]["via"] == "1.1 socialscrape"
    assert not {"cookie", "x-portal-key-id", "authorization"} & set(sent["headers"])
    assert CLIENT_KEY not in json.dumps(sent)


def test_blocked_tiktok_ad_library_search_uses_fallback(make, upstream, monkeypatch):
    async def blocked(*args, **kwargs):
        raise UpstreamBlocked("TikTok blocked the Ad Library request")

    monkeypatch.setattr(tiktok_ads, "with_ydl", blocked)
    response = make().get("/v1/tiktok/ad-library/search", params={"query": "running shoes"})
    assert response.status_code == 200 and response.json() == SC_OK
    assert response.headers[fallback.HEADER] == fallback.PROVIDER
    [sent] = upstream.requests
    assert sent["path"] == "/v1/tiktok/ad-library/search"
    assert sent["query"] == [("query", "running shoes")]


def test_catalog_post_fallback_forwards_the_json_body(make, upstream, monkeypatch):
    path = "/v1/facebook/adLibrary/search/ads"
    body = {"query": "running shoes", "country": "US", "trim": True, "cursor": "upstream-token"}

    async def failed(*args, **kwargs):
        raise UpstreamError("Meta failed")

    monkeypatch.setattr(facebook_ads, "with_ydl", failed)
    c = make(SOCIALSCRAPE_SCRAPECREATORS_PLATFORMS="facebook")
    response = c.post(path, json=body)
    assert response.status_code == 200 and response.json() == SC_OK
    [sent] = upstream.requests
    assert sent["method"] == "POST" and sent["path"] == path and sent["query"] == []
    assert json.loads(sent["body"]) == body
    assert sent["headers"]["content-type"] == "application/json"
    assert sent["headers"]["x-api-key"] == SC_KEY and sent["headers"]["via"] == "1.1 socialscrape"

    own_cursor = {**body, "cursor": "ss1.e30"}
    assert c.post(path, json=own_cursor).status_code == 500
    assert len(upstream.requests) == 1

    # JSON that isn't an object gets the route's own 400, as it would without the fallback.
    for not_an_object in ([1, 2], "nike", 3):
        r = c.post(path, json=not_an_object)
        assert r.status_code == 400 and r.json()["error"] == "bad_request", r.json()
    assert len(upstream.requests) == 1


def test_a_local_answer_never_asks_scrapecreators(make, upstream, monkeypatch):
    fail_profile(monkeypatch, None)
    c = make(SOCIALSCRAPE_SCRAPECREATORS_FALLBACK="all")
    r = c.get(PROFILE, params={"handle": "someone"})
    assert r.status_code == 200 and fallback.HEADER not in r.headers
    assert c.get(PROFILE).status_code == 400  # handle missing: the caller's mistake
    assert c.get(PROFILE, headers={"x-api-key": "wrong"}).status_code == 401
    assert c.post(UNSUPPORTED).status_code == 405
    assert c.get("/v1/twitter/profile").status_code == 404  # outside the catalog
    assert upstream.requests == []


@pytest.mark.parametrize("exc,trigger", [
    (NotSupported("sort_by=popular is not supported"), "unsupported"),
    (BadRequest("Invalid cursor/continuation token", code="bad_cursor"), "unsupported"),
    (UpstreamError("Unclassified"), "errors"),
    (UpstreamError("Timed out waiting for the platform", code="timeout"), "errors"),
    (UpstreamUnreachable("No answer"), "errors"),
    (UpstreamBlocked("Rate limited"), "errors"),
    (Forbidden("Private account"), "restricted"),
    (NotFound("No such user"), "not_found"),
])
def test_each_local_failure_falls_back_only_under_its_trigger(make, upstream, monkeypatch, exc, trigger):
    fail_profile(monkeypatch, exc)
    others = ",".join(t for t in fallback.TRIGGERS if t != trigger)
    r = make(SOCIALSCRAPE_SCRAPECREATORS_FALLBACK=others).get(PROFILE, params={"handle": "someone"})
    assert r.status_code == exc.status and r.json()["error"] == exc.code and "fallback" not in r.json()
    assert upstream.requests == []
    r = make(SOCIALSCRAPE_SCRAPECREATORS_FALLBACK=trigger).get(PROFILE, params={"handle": "someone"})
    assert r.status_code == 200 and r.json() == SC_OK and r.headers[fallback.HEADER] == fallback.PROVIDER
    assert len(upstream.requests) == 1


def test_switches_that_keep_requests_local(make, upstream, monkeypatch, caplog):
    for env in ({"SOCIALSCRAPE_SCRAPECREATORS_FALLBACK": "none"},
                {"SOCIALSCRAPE_SCRAPECREATORS_PLATFORMS": "youtube,instagram"},
                {"SOCIALSCRAPE_SCRAPECREATORS_API_KEY": None}):
        assert make(**env).get(UNSUPPORTED, params={"url": "x"}).status_code == 501
    c = make()
    # Our own cursors mean nothing to ScrapeCreators; a request that came through a fallback isn't sent again.
    assert c.get(UNSUPPORTED, params={"url": "x", "cursor": "ss1.e30"}).status_code == 501
    assert c.get(UNSUPPORTED, params={"url": "x"}, headers={"via": "1.1 proxy, 1.1 socialscrape"}).status_code == 501
    # Without authentication anyone could spend the credits: the environment key is ignored.
    with caplog.at_level(logging.WARNING, "socialscrape"):
        open_app = make(SOCIALSCRAPE_API_KEYS="")
    assert "SOCIALSCRAPE_SCRAPECREATORS_API_KEY is ignored" in caplog.text
    assert open_app.get(UNSUPPORTED, params={"url": "x"}).status_code == 501
    assert open_app.get("/health").json()["fallback"] is None
    assert upstream.requests == []


def test_unsupported_route_never_leaks_501_when_fallback_is_active(make, upstream):
    upstream.reply(500, {"success": False, "error": "upstream_error", "message": "provider failed"})
    response = make().get(UNSUPPORTED, params={"url": "x"})
    assert response.status_code == 500 and response.json()["message"] == "provider failed"
    assert response.headers[fallback.HEADER] == fallback.PROVIDER

    upstream.reply(200, b"not json")
    response = make().get(UNSUPPORTED, params={"url": "x"})
    assert response.status_code == 502 and response.json()["error"] == "fallback_unavailable"
    assert response.json()["fallback"]["error"] == "bad_response"


def test_scrapecreators_verdict_stands_only_where_we_had_no_answer(make, upstream, monkeypatch):
    c = make(SOCIALSCRAPE_SCRAPECREATORS_FALLBACK="all")
    for status in (400, 403, 404, 422):
        upstream.reply(status, {"success": False, "message": f"ScrapeCreators says {status}"})
        r = c.get(UNSUPPORTED, params={"url": "x"})
        assert r.status_code == status and r.json()["message"] == f"ScrapeCreators says {status}"
        assert r.headers[fallback.HEADER] == fallback.PROVIDER
    fail_profile(monkeypatch, UpstreamBlocked("Rate limited"))
    upstream.reply(404, {"success": False, "message": "not found there"})
    r = c.get(PROFILE, params={"handle": "someone"})
    assert r.status_code == 503 and r.json()["error"] == "upstream_blocked"
    assert r.json()["fallback"] == {"provider": "scrapecreators", "status": 404, "error": "upstream_error",
                                    "message": "not found there"}
    assert fallback.HEADER not in r.headers


@pytest.mark.parametrize("status,body,headers,error,message", [
    (401, {"success": False, "message": f"Invalid API key {SC_KEY}"}, {}, "unauthorized", "Invalid API key [key]"),
    (402, {"success": False, "error": "no_credits"}, {}, "no_credits", "no_credits"),
    (429, b"slow down", {"content-type": "text/plain"}, "rate_limited", "ScrapeCreators rate-limited the request"),
    (500, b"<html>oops</html>", {"content-type": "text/html"}, "upstream_error", "ScrapeCreators answered HTTP 500"),
    (200, b"not json", {}, "bad_response", "ScrapeCreators' answer wasn't a JSON object"),
    (200, b"[1, 2]", {}, "bad_response", "ScrapeCreators' answer wasn't a JSON object"),
    (200, {"success": True, "message": "x" * 1000}, {}, None, None),  # served: only failures are cut short
])
def test_a_failed_fallback_keeps_the_local_error_with_a_scrubbed_detail(make, upstream, monkeypatch, status, body,
                                                                        headers, error, message):
    fail_profile(monkeypatch, UpstreamBlocked("Rate limited"))
    upstream.reply(status, body, headers=headers)
    r = make().get(PROFILE, params={"handle": "someone"})
    if error is None:
        assert r.status_code == 200 and len(r.json()["message"]) == 1000
        return
    assert r.status_code == 503 and r.json()["error"] == "upstream_blocked" and r.json()["message"] == "Rate limited"
    assert r.json()["fallback"] == {"provider": "scrapecreators", "status": status, "error": error,
                                    "message": message}
    assert SC_KEY not in r.text and fallback.HEADER not in r.headers


def test_timeouts_unreachable_and_oversized_answers(make, upstream, monkeypatch):
    fail_profile(monkeypatch, UpstreamBlocked("Rate limited"))
    c = make(SOCIALSCRAPE_REQUEST_TIMEOUT="0.5")
    upstream.reply(200, SC_OK, delay=3)
    started = time.monotonic()
    r = c.get(PROFILE, params={"handle": "someone"})
    assert time.monotonic() - started < 2
    assert r.status_code == 503 and r.json()["fallback"]["error"] == "timeout"

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]  # nothing listens once it's closed
    r = make(SOCIALSCRAPE_SCRAPECREATORS_URL=f"http://127.0.0.1:{dead}").get(PROFILE, params={"handle": "someone"})
    assert r.status_code == 503 and r.json()["fallback"] == {
        "provider": "scrapecreators", "status": None, "error": "network_error",
        "message": "ScrapeCreators couldn't be reached"}

    monkeypatch.setattr(fallback, "MAX_BODY_BYTES", 64)
    upstream.reply(200, {"success": True, "items": ["x" * 200]})
    r = make().get(PROFILE, params={"handle": "someone"})
    assert r.status_code == 503 and r.json()["fallback"]["error"] == "bad_response"


def test_a_trickled_answer_frees_its_thread_at_the_deadline():
    class Trickle(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-length", "1000")
            self.end_headers()
            try:
                for _ in range(100):  # a byte every 0.1 s: never quiet for as long as a socket read may wait
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        started = time.monotonic()
        status, body, problem = fallback._fetch(f"http://127.0.0.1:{server.server_port}/v1/x", SC_KEY, 0.5)
        assert (status, body, problem) == (200, b"", "timeout")
        assert time.monotonic() - started < 1  # the thread itself, not only the request waiting on it
    finally:
        server.shutdown()
        server.server_close()


def test_a_chunked_answer_is_read_whole():
    class Chunked(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            for part in (b'{"success": ', b'true, "items": ', b"[1, 2, 3]}"):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
            self.wfile.write(b"0\r\n\r\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Chunked)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, body, problem = fallback._fetch(f"http://127.0.0.1:{server.server_port}/v1/x", SC_KEY, 5)
        assert (status, json.loads(body), problem) == (200, {"success": True, "items": [1, 2, 3]}, None)
    finally:
        server.shutdown()
        server.server_close()


def test_ss1_counts_as_our_cursor_only_in_a_pagination_param(make, upstream):
    c = make()
    assert c.get("/v1/tiktok/search/keyword", params={"query": "ss1.not-a-cursor"}).status_code == 200
    assert c.get("/v1/tiktok/search/keyword", params={"query": "x", "cursor": "ss1.e30"}).status_code == 501
    assert len(upstream.requests) == 1


def test_a_short_key_is_scrubbed_from_scrapecreators_text_only(make, upstream, monkeypatch):
    fail_profile(monkeypatch, UpstreamBlocked("Rate limited"))
    upstream.reply(401, {"success": False, "message": "Bad key: s"})
    r = make(SOCIALSCRAPE_SCRAPECREATORS_API_KEY="s").get(PROFILE, params={"handle": "someone"})
    assert r.json()["fallback"]["message"] == "Bad key: [key]"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    r = make(SOCIALSCRAPE_SCRAPECREATORS_API_KEY="s",
             SOCIALSCRAPE_SCRAPECREATORS_URL=f"http://127.0.0.1:{dead}").get(PROFILE, params={"handle": "someone"})
    assert r.json()["fallback"]["message"] == "ScrapeCreators couldn't be reached"


def test_a_relay_url_keeps_its_path(make, upstream):
    r = make(SOCIALSCRAPE_SCRAPECREATORS_URL=upstream.url + "/relay/").get(UNSUPPORTED, params={"url": "x"})
    assert r.status_code == 200 and upstream.requests[0]["path"] == "/relay" + UNSUPPORTED


def test_a_redirect_is_not_followed_with_the_key(make, upstream):
    elsewhere = Upstream()
    try:
        upstream.reply(302, b"", headers={"location": elsewhere.url + "/collect"})
        r = make().get(UNSUPPORTED, params={"url": "x"})
        assert r.status_code == 502 and r.json()["error"] == "fallback_unavailable"
        assert r.json()["fallback"]["status"] == 302
        assert elsewhere.requests == [] and SC_KEY not in r.text
    finally:
        elsewhere.close()


def test_an_internal_error_falls_back_under_errors_and_is_still_logged(make, upstream, monkeypatch, caplog):
    fail_profile(monkeypatch, RuntimeError("boom"))
    with caplog.at_level(logging.ERROR, "socialscrape.fallback"):
        r = make().get(PROFILE, params={"handle": "someone"})
    assert r.status_code == 200 and r.json() == SC_OK
    assert any(rec.exc_info and "boom" in str(rec.exc_info[1]) for rec in caplog.records)
    upstream.reply(503, {"success": False, "message": "down"})
    r = make().get(PROFILE, params={"handle": "someone"})
    assert r.status_code == 500 and r.json()["error"] == "server_error" and r.json()["fallback"]["status"] == 503
    r = make(SOCIALSCRAPE_SCRAPECREATORS_FALLBACK="unsupported").get(PROFILE, params={"handle": "someone"})
    assert r.status_code == 500 and r.json()["error"] == "server_error" and "fallback" not in r.json()
    assert len(upstream.requests) == 2


def test_an_answer_is_cached_fetched_live_and_still_marked_on_a_hit(make, upstream, tmp_path):
    c = make(SOCIALSCRAPE_CACHE_PATH=str(tmp_path / "cache.sqlite3"))
    first = c.get(UNSUPPORTED, params={"url": "x", "cache_max_age": "1d"})
    second = c.get(UNSUPPORTED, params={"url": "x", "cache_max_age": "1d"})
    assert first.headers["x-cache"] == "miss" and second.headers["x-cache"] == "hit"
    assert first.headers[fallback.HEADER] == second.headers[fallback.HEADER] == fallback.PROVIDER
    assert second.json()["cached"] is True
    # ScrapeCreators is asked live (no cache_max_age), once.
    assert [r["query"] for r in upstream.requests] == [[("url", "x")]]


@pytest.mark.parametrize("name,value", [
    ("SOCIALSCRAPE_SCRAPECREATORS_FALLBACK", "errors,sometimes"),
    ("SOCIALSCRAPE_SCRAPECREATORS_PLATFORMS", "myspace"),
    ("SOCIALSCRAPE_SCRAPECREATORS_API_KEY", "clé"),
    ("SOCIALSCRAPE_SCRAPECREATORS_API_KEY", "two words"),
    ("SOCIALSCRAPE_SCRAPECREATORS_URL", "ftp://example.com"),
    ("SOCIALSCRAPE_SCRAPECREATORS_URL", "https://example.com:0"),
    ("SOCIALSCRAPE_SCRAPECREATORS_URL", "https://example.com/?a=1"),
    ("SOCIALSCRAPE_SCRAPECREATORS_URL", "example.com"),
])
def test_an_unusable_variable_stops_startup_naming_it(make, name, value):
    with pytest.raises(ConfigError, match=name) as info:
        make(**{name: value})
    assert "clé" not in str(info.value) and "two words" not in str(info.value)


def test_health_and_trigger_aliases(make):
    health = make(SOCIALSCRAPE_SCRAPECREATORS_FALLBACK="ALL", SOCIALSCRAPE_SCRAPECREATORS_PLATFORMS=" TikTok ,youtube")
    response = health.get("/health")
    assert response.json()["fallback"] == {"provider": "scrapecreators", "triggers": list(fallback.TRIGGERS),
                                           "platforms": ["youtube", "tiktok"]}
    assert SC_KEY not in response.text
    assert make(SOCIALSCRAPE_SCRAPECREATORS_FALLBACK="none").get("/health").json()["fallback"] is None


# --- dashboard ----------------------------------------------------------------


@pytest.fixture
def portal(make, tmp_path, monkeypatch):
    """portal(encryption_key=None, **env) -> (owner client, its CSRF headers, app)."""
    path = tmp_path / "portal.sqlite3"
    default_key = Fernet.generate_key().decode()

    def build(encryption_key: str | None = None, **env):
        monkeypatch.setenv("SOCIALSCRAPE_SETUP_TOKEN", "setup")
        monkeypatch.setenv("SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY", encryption_key or default_key)
        c = make(SOCIALSCRAPE_PORTAL_PATH=str(path), SOCIALSCRAPE_API_KEYS="", **env)
        c.headers.pop("x-api-key")
        if c.post("/login", data={"email": "owner@example.com", "password": "correct horse battery"},
                  follow_redirects=False).headers.get("location") != "/app":
            c.post("/setup", data={"email": "owner@example.com", "password": "correct horse battery",
                                   "setup_token": "setup"})
        return c, {"x-csrf-token": c.get("/portal/api/me").json()["csrf"]}, c.app

    build.path = path
    return build


def test_owner_settings_are_encrypted_never_returned_and_apply_at_once(portal, upstream):
    owner, csrf, _ = portal(SOCIALSCRAPE_SCRAPECREATORS_API_KEY="env-key-1")
    settings = owner.get("/portal/api/settings/fallback").json()
    assert settings == {"provider": "scrapecreators", "active": True, "enabled": True,
                        "triggers": ["unsupported", "errors"], "platforms": ["youtube", "tiktok", "instagram", "facebook", "google"],
                        "options_source": "environment", "options_updated_at": None,
                        "api_key": {"configured": True, "source": "environment", "updated_at": None}}

    assert owner.put("/portal/api/settings/fallback/api-key", headers=csrf,
                     json={"value": "  dash-key-2  "}).json() == {"configured": True}
    assert owner.get(UNSUPPORTED, params={"url": "x"}).status_code == 200  # a session spends credits too
    assert upstream.requests[-1]["headers"]["x-api-key"] == "dash-key-2"
    key = owner.get("/portal/api/settings/fallback").json()["api_key"]
    assert key["source"] == "dashboard" and key["updated_at"]
    raw = b"".join(p.read_bytes() for p in portal.path.parent.glob("portal.sqlite3*"))
    assert b"dash-key-2" not in raw

    options = {"enabled": True, "triggers": ["errors"], "platforms": ["instagram"]}
    saved = owner.put("/portal/api/settings/fallback", headers=csrf, json=options).json()
    assert saved["options_source"] == "dashboard" and saved["triggers"] == ["errors"] and saved["active"]
    assert owner.get(UNSUPPORTED, params={"url": "x"}).status_code == 501  # unsupported and tiktok are off now
    options = {"enabled": False, "triggers": ["unsupported"], "platforms": ["tiktok"]}
    assert owner.put("/portal/api/settings/fallback", headers=csrf, json=options).json()["active"] is False
    assert owner.get(UNSUPPORTED, params={"url": "x"}).status_code == 501
    assert owner.get("/health").json()["fallback"] is None
    assert len(upstream.requests) == 1

    assert owner.delete("/portal/api/settings/fallback/api-key", headers=csrf).json() == {"cleared": True}
    assert owner.get("/portal/api/settings/fallback").json()["api_key"]["source"] == "environment"
    assert "dash-key-2" not in owner.get("/portal/api/settings/fallback").text


def test_history_and_usage_mark_answers_scrapecreators_served(portal):
    owner, _, _ = portal()
    owner.get(UNSUPPORTED, params={"url": "x"})
    owner.get("/v1/youtube/shorts/trending")  # direct local route
    history = owner.get("/portal/api/history").json()["requests"]
    assert [bool(row["fallback"]) for row in history] == [False, True]
    assert owner.get("/portal/api/usage").json()["fallback_count"] == 1


@pytest.mark.parametrize("body", [
    {"enabled": True, "triggers": ["sometimes"], "platforms": []},
    {"enabled": True, "triggers": ["errors", "errors"], "platforms": []},
    {"enabled": "yes", "triggers": [], "platforms": []},
    {"enabled": True, "triggers": [], "platforms": ["myspace"]},
    {"enabled": True, "triggers": [], "platforms": [], "url": "https://evil.example"},
    {"enabled": True, "triggers": "errors", "platforms": []},
    [],
])
def test_invalid_options_are_a_400(portal, body):
    owner, csrf, _ = portal()
    r = owner.put("/portal/api/settings/fallback", headers=csrf, json=body)
    assert r.status_code == 400
    assert owner.get("/portal/api/settings/fallback").json()["options_source"] == "environment"


@pytest.mark.parametrize("value", ["", "   ", "two words", "clé", "x" * 513, None, 12])
def test_an_invalid_key_is_a_400_that_never_echoes_it(portal, value):
    owner, csrf, _ = portal()
    r = owner.put("/portal/api/settings/fallback/api-key", headers=csrf, json={"value": value})
    assert r.status_code == 400 and "two words" not in r.text and "clé" not in r.text
    assert owner.get("/portal/api/settings/fallback").json()["api_key"]["source"] == "environment"


def test_only_the_owner_with_its_csrf_token_reads_or_changes_settings(portal):
    owner, _, app = portal()
    app.state.portal.create_member("member@example.com", "another strong password")
    member = TestClient(app, base_url="https://testserver")
    member.post("/login", data={"email": "member@example.com", "password": "another strong password"})
    member_csrf = {"x-csrf-token": member.get("/portal/api/me").json()["csrf"]}
    assert member.get("/portal/api/settings/fallback").status_code == 403
    assert member.put("/portal/api/settings/fallback/api-key", headers=member_csrf,
                      json={"value": "member-key"}).status_code == 403
    assert member.delete("/portal/api/settings/fallback/api-key", headers=member_csrf).status_code == 403
    assert owner.put("/portal/api/settings/fallback/api-key", json={"value": "no-csrf"}).status_code == 403
    assert owner.put("/portal/api/settings/fallback", headers={"x-csrf-token": "wrong"},
                     json={"enabled": False, "triggers": [], "platforms": []}).status_code == 403
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get("/portal/api/settings/fallback").status_code == 401
    assert owner.get("/portal/api/settings/fallback").json()["api_key"]["source"] == "environment"


def test_unreadable_saved_settings_fall_back_to_the_environment(portal, upstream, caplog):
    owner, csrf, _ = portal(SOCIALSCRAPE_SCRAPECREATORS_API_KEY="env-key-1")
    owner.put("/portal/api/settings/fallback/api-key", headers=csrf, json={"value": "dash-key-2"})
    owner.put("/portal/api/settings/fallback", headers=csrf,
              json={"enabled": False, "triggers": [], "platforms": []})
    # A new SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY can't decrypt them: the environment's key and options apply.
    owner, _, _ = portal(Fernet.generate_key().decode(), SOCIALSCRAPE_SCRAPECREATORS_API_KEY="env-key-1")
    with caplog.at_level(logging.WARNING, "socialscrape.fallback"):
        assert owner.get(UNSUPPORTED, params={"url": "x"}).status_code == 200
    assert upstream.requests[-1]["headers"]["x-api-key"] == "env-key-1"
    assert "can't be read" in caplog.text


def test_saving_without_an_encryption_key_says_so(portal, monkeypatch):
    owner, csrf, _ = portal()
    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY", "")
    r = owner.put("/portal/api/settings/fallback/api-key", headers=csrf, json={"value": "dash-key-2"})
    assert r.status_code == 400 and "SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY" in r.json()["message"]
    assert "dash-key-2" not in r.text


def test_an_existing_portal_database_gains_the_fallback_column(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:  # the requests table as it was before the fallback
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, member_id INTEGER, api_key_id INTEGER, "
                     "created_at REAL NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL, query TEXT NOT NULL, "
                     "status INTEGER NOT NULL, duration_ms INTEGER NOT NULL, cached INTEGER NOT NULL DEFAULT 0)")
        conn.execute("INSERT INTO requests (created_at, method, path, query, status, duration_ms) "
                     "VALUES (1, 'GET', '/v1/youtube/video', '', 200, 5)")
    store = PortalStore(str(path))
    columns = {row[1] for row in store._conn.execute("PRAGMA table_info(requests)")}
    assert "fallback" in columns
    assert [tuple(row) for row in store._conn.execute("SELECT fallback FROM requests")] == [(0,)]
