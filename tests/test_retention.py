"""Stored response bodies and cached responses are pruned, so they can't fill the volume the portal lives on."""

from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from socialscrape import ytdlp
from socialscrape.cache import ResponseCache
from socialscrape.config import get_settings
from socialscrape.portal import PortalStore

DAY = 86400


def _record(store: PortalStore, body: bytes, age_days: float, now: float) -> int:
    store.record_request(None, None, "GET", "/v1/youtube/video", "url=x", 200, 10, False, body)
    request_id = store._conn.execute("SELECT MAX(id) FROM requests").fetchone()[0]
    store._conn.execute("UPDATE requests SET created_at=? WHERE id=?", (now - age_days * DAY, request_id))
    store._conn.commit()
    return request_id


def _bodies(store: PortalStore) -> dict[int, tuple[bool, int]]:
    rows = store._conn.execute("SELECT id, response_body IS NOT NULL, response_pruned FROM requests").fetchall()
    return {row[0]: (bool(row[1]), row[2]) for row in rows}


def test_history_clears_old_bodies_but_keeps_every_request(tmp_path):
    store, now = PortalStore(str(tmp_path / "portal.sqlite3")), time.time()
    old, recent = _record(store, b'{"a":1}', 20, now), _record(store, b'{"b":2}', 1, now)
    assert store.prune_history(now=now) == 1
    assert _bodies(store) == {old: (False, 1), recent: (True, 0)}  # both requests are still in the history
    owner = type("P", (), {"role": "owner", "id": 0})()
    assert store.history_detail(owner, old)["response_pruned"] == 1
    assert store.history_detail(owner, recent)["response"] == '{"b":2}'


def test_history_bodies_fit_in_the_size_limit_oldest_first(tmp_path):
    store, now = PortalStore(str(tmp_path / "portal.sqlite3")), time.time()
    ids = [_record(store, b"x" * 100, 5 - n, now) for n in range(5)]  # oldest first
    assert store.prune_history(now=now, max_bytes=250) == 3
    assert [_bodies(store)[i][0] for i in ids] == [False, False, False, True, True]


def test_history_prunes_itself_as_requests_arrive(tmp_path, monkeypatch):
    store, now = PortalStore(str(tmp_path / "portal.sqlite3")), time.time()
    old = _record(store, b'{"a":1}', 30, now)
    store._pruned_at = 0.0  # the last prune was long ago
    _record(store, b'{"b":2}', 0, now)
    assert _bodies(store)[old] == (False, 1)


def test_cache_drops_stale_responses_and_stays_within_its_size(tmp_path):
    cache, now = ResponseCache(str(tmp_path / "cache.sqlite3")), time.time()
    for key, age in [("stale", 40), ("a", 3), ("b", 2), ("c", 1)]:
        cache.put(key, '{"x":"' + "y" * 100 + '"}', fallback=key == "stale")
        cache._conn.execute("UPDATE responses SET stored_at=? WHERE key=?", (now - age * DAY, key))
    cache._conn.commit()
    assert cache.prune(now=now, max_bytes=250) == 2  # "stale" by age, then "a" to fit two bodies
    assert [row[0] for row in cache._conn.execute("SELECT key FROM responses ORDER BY key")] == ["b", "c"]
    assert cache._conn.execute("SELECT COUNT(*) FROM response_provenance WHERE key='stale'").fetchone()[0] == 0
    assert cache.get("c", 7 * DAY) is not None


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_PATH", str(tmp_path / "portal.sqlite3"))
    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "test-key")
    monkeypatch.setattr(ytdlp, "_executor", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_failed_history_write_still_answers_the_request(app_env, monkeypatch):
    from socialscrape.main import create_app

    calls = []

    def disk_full(*args, **kwargs):
        calls.append(args)
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(PortalStore, "record_request", disk_full)
    response = TestClient(create_app()).get("/v1/youtube/shorts/trending", headers={"x-api-key": "test-key"})
    assert calls and response.status_code == 501 and response.json()["error"] == "not_supported"
