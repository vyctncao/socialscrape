"""Helpers shared by the tests/test_e2e_*.py suites."""

from __future__ import annotations

import contextlib
import re
import socket
import time

import pytest
import yt_dlp
from fastapi.testclient import TestClient

from socialscrape import ytdlp
from socialscrape.config import get_settings
from socialscrape.main import create_app

# What an error message must never show: tracebacks, file paths, module names.
LEAKS = re.compile(r"Traceback|File \"|/Users/|/tmp/|/private/|site-packages|\.py\b|socialscrape\.")

_last_upstream = [0.0]


def skip_if_blocked(r) -> None:
    if r.status_code in (429, 503):
        pytest.skip(f"platform throttled this IP: {r.text[:200]}")


def is_envelope(r) -> bool:
    if "application/json" not in r.headers.get("content-type", ""):
        return False
    body = r.json()
    return body.get("success") is False and bool(body.get("error")) and bool(body.get("message"))


def free_port() -> int:
    """A localhost port nothing listens on (as a dead proxy, it keeps requests off the network)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def mark_upstream() -> None:
    """Record that a platform was just reached some other way (yt-dlp directly, a burst of parallel calls)."""
    _last_upstream[0] = time.monotonic()


def spaced_get(c, path: str, spacing: float, **params):
    """c.get(), started at least `spacing` s after the last call that reached a platform (one that took > 0.25 s)."""
    wait = _last_upstream[0] + spacing - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    start = time.monotonic()
    r = c.get(path, params=params)
    if time.monotonic() - start > 0.25:
        mark_upstream()
    return r


def ytdlp_info(url: str, **opts) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, **opts}) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False))


@contextlib.contextmanager
def patched_env(env: dict[str, str], patches=()):
    """Env vars/attribute patches with a fresh settings cache and a fresh yt-dlp pool; restored afterwards."""
    mp = pytest.MonkeyPatch()
    try:
        for k, v in env.items():
            mp.setenv(k, v)
        mp.setattr(ytdlp, "_executor", None)
        for target, name, value in patches:
            mp.setattr(target, name, value)
        get_settings.cache_clear()
        yield mp
    finally:
        pool = ytdlp._executor
        mp.undo()
        if pool is not None and pool is not ytdlp._executor:
            pool.shutdown(wait=False, cancel_futures=True)
        get_settings.cache_clear()


@contextlib.contextmanager
def app_with(env: dict[str, str], tmp_path=None, *, raise_errors: bool = True, patches=()):
    """A TestClient over create_app() under `env` (see patched_env). The cache goes to tmp_path unless env sets it."""
    env = dict(env)
    if tmp_path is not None:
        env.setdefault("SOCIALSCRAPE_CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    with patched_env(env, patches):
        with TestClient(create_app(), raise_server_exceptions=raise_errors) as c:
            yield c
