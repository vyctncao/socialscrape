"""Live gap tests for the core slice (completeness critic): working proxies (authenticated HTTP CONNECT, SOCKS5h),
fake cookie files for every platform, cached_at, .env.example as shipped, the CLI, HOST, logging, signals, the
mlx-whisper model names and the cache path. Anonymous only: no browser cookies, no login; cookie files here are fake.

Servers started by these tests run the real `socialscrape` CLI on an ephemeral localhost port; proxies and the
tarpit are small in-test socket servers. Only the tests marked live reach the platforms (or run the slower
signal checks).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import re
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from importlib.util import find_spec
from pathlib import Path

import httpx2
import pytest

from socialscrape import transcribe as tr
from socialscrape.cache import ResponseCache
from tests.e2e import app_with, free_port, is_envelope, patched_env, skip_if_blocked

API_DIR = Path(__file__).resolve().parents[1]
CLI = Path(sys.executable).with_name("socialscrape")
TT_HANDLE = "stoolpresidente"  # cheap TikTok profile
YT_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
HUGE_AGE = "1" * 5000 + "d"  # digits only, but more than int() converts
HAS_MLX = find_spec("mlx_whisper") is not None


# --- helpers ---------------------------------------------------------------------------------------------


def _unsupported(client) -> str:
    rows = client.get("/meta/coverage").json()["endpoints"]
    return next(r["path"] for r in rows if r["status"] == "not_supported")


def _recv(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("peer closed")
        buf += chunk
    return buf


def _pipe(a: socket.socket, b: socket.socket) -> None:
    socks = [a, b]
    while True:
        readable, _, broken = select.select(socks, [], socks, 60)
        if broken or not readable:
            return
        for s in readable:
            data = s.recv(65536)
            if not data:
                return
            (b if s is a else a).sendall(data)


class Proxy:
    """A tiny threaded HTTP CONNECT or SOCKS5 proxy on 127.0.0.1 that records what it was asked to reach.

    `auth` is "user:password" (Basic for HTTP, RFC 1929 for SOCKS5)."""

    def __init__(self, kind: str = "http", auth: str | None = None):
        self.kind, self.auth = kind, auth
        self.hosts: list[str] = []  # every target host as the client sent it
        self.names: list[str] = []  # SOCKS5 targets sent as domain names (remote DNS)
        self.refused = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def url(self, auth: str | None = None) -> str:
        scheme = "http" if self.kind == "http" else "socks5h"
        creds = f"{auth}@" if auth else ""
        return f"{scheme}://{creds}127.0.0.1:{self.port}"

    def close(self) -> None:
        self.sock.close()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            with conn:
                conn.settimeout(30)
                up = self._socks5(conn) if self.kind == "socks5" else self._connect(conn)
                if up is not None:
                    with up:
                        _pipe(conn, up)
        except OSError:
            pass

    def _connect(self, conn: socket.socket) -> socket.socket | None:
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return None
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, target = lines[0].split(" ")[:2]
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        if self.auth and headers.get("proxy-authorization") != "Basic " + base64.b64encode(self.auth.encode()).decode():
            self.refused += 1
            conn.sendall(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="qa"\r\n'
                         b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            return None
        if method != "CONNECT":
            conn.sendall(b"HTTP/1.1 501 Not Implemented\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return None
        host, _, port = target.rpartition(":")
        host = host.strip("[]")
        self.hosts.append(host)
        up = socket.create_connection((host, int(port)), timeout=20)
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        if rest:
            up.sendall(rest)
        return up

    def _socks5(self, conn: socket.socket) -> socket.socket | None:
        _ver, n = _recv(conn, 2)
        methods = _recv(conn, n)
        if self.auth:
            if 2 not in methods:
                conn.sendall(b"\x05\xff")
                return None
            conn.sendall(b"\x05\x02")
            _v, ulen = _recv(conn, 2)
            user = _recv(conn, ulen)
            (plen,) = _recv(conn, 1)
            password = _recv(conn, plen)
            if f"{user.decode()}:{password.decode()}" != self.auth:
                self.refused += 1
                conn.sendall(b"\x01\x01")
                return None
            conn.sendall(b"\x01\x00")
        else:
            conn.sendall(b"\x05\x00")
        _v, _cmd, _rsv, atyp = _recv(conn, 4)
        if atyp == 3:
            (ln,) = _recv(conn, 1)
            host = _recv(conn, ln).decode()
            self.names.append(host)
        elif atyp == 1:
            host = socket.inet_ntoa(_recv(conn, 4))
        else:
            host = socket.inet_ntop(socket.AF_INET6, _recv(conn, 16))
        port = int.from_bytes(_recv(conn, 2), "big")
        self.hosts.append(host)
        up = socket.create_connection((host, port), timeout=20)
        conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        return up


@contextlib.contextmanager
def proxy(kind: str = "http", auth: str | None = None):
    p = Proxy(kind, auth)
    try:
        yield p
    finally:
        p.close()


@contextlib.contextmanager
def tarpit():
    """A localhost port that accepts TCP connections and never answers (a hung proxy)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    held: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                held.append(sock.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()
        for c in held:
            c.close()


class Server:
    def __init__(self, proc: subprocess.Popen, port: int, log: Path, host: str):
        self.proc, self.port, self.log, self.host = proc, port, log, host
        self.base = f"http://127.0.0.1:{port}"

    def output(self) -> str:
        return self.log.read_text(errors="replace")

    def listening(self) -> bool:
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        try:
            with socket.socket(family) as s:
                s.settimeout(0.5)
                return s.connect_ex((self.host, self.port)) == 0
        except OSError:  # e.g. a HOST that does not resolve
            return False

    def wait(self, timeout: float = 20) -> str:
        """'listening' once the port accepts connections, 'exited' if the process ends first."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return "exited"
            if self.listening():
                return "listening"
            time.sleep(0.1)
        return "timeout"

    def exit_within(self, seconds: float) -> float | None:
        t0 = time.monotonic()
        try:
            self.proc.wait(seconds)
        except subprocess.TimeoutExpired:
            return None
        return time.monotonic() - t0


@contextlib.contextmanager
def serve(tmp_path: Path, env: dict[str, str] | None = None, args=(), *, wait: bool = True):
    """Run the `socialscrape` CLI on a free port with cwd=tmp_path; the cache is off and transcription none
    unless `env` says otherwise. No SOCIALSCRAPE_* variable is inherited from this process."""
    port = free_port()
    full = {k: v for k, v in os.environ.items() if not k.startswith("SOCIALSCRAPE_") and k not in ("HOST", "PORT")}
    full.update({"PORT": str(port), "SOCIALSCRAPE_CACHE_PATH": "", "SOCIALSCRAPE_TRANSCRIBE": "none",
                 "PYTHONUNBUFFERED": "1"})
    full.update(env or {})
    log = tmp_path / f"server-{port}.log"
    with open(log, "wb") as fh:
        proc = subprocess.Popen([str(CLI), *args], cwd=tmp_path, env=full, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True)
    host = full.get("HOST", "127.0.0.1").strip("[] ") or "127.0.0.1"
    srv = Server(proc, port, log, host)
    try:
        if wait:
            state = srv.wait()
            assert state == "listening", f"server did not start ({state}): {srv.output()[-800:]}"
        yield srv
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)


def _tt_profile(client_or_base, **params):
    params = {"handle": TT_HANDLE, **params}
    if isinstance(client_or_base, str):
        return httpx2.get(f"{client_or_base}/v1/tiktok/profile", params=params, timeout=60)
    return client_or_base.get("/v1/tiktok/profile", params=params)


def _fingerprint(path: Path) -> tuple[str, int]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


async def _fake_download(platform, fn, **opts):  # stands in for the audio download in transcribe()
    return "/nonexistent/qa-audio.m4a", {"acodec": "aac"}


# --- proxies that work ---------------------------------------------------------------------------------


@pytest.mark.live
def test_authenticated_http_proxy_carries_the_request(tmp_path):
    with proxy("http", auth="qauser:qapass") as p:
        with app_with({"SOCIALSCRAPE_PROXY": p.url("qauser:qapass")}, tmp_path) as c:
            r = _tt_profile(c)
    skip_if_blocked(r)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["user"]["uniqueId"] == TT_HANDLE
    assert p.refused == 0 and any(h.endswith("tiktok.com") for h in p.hosts), p.hosts


@pytest.mark.live
def test_socks5h_proxy_with_credentials_resolves_remotely(tmp_path):
    with proxy("socks5", auth="qauser:qapass") as p:
        with app_with({"SOCIALSCRAPE_PROXY": p.url("qauser:qapass")}, tmp_path) as c:
            r = _tt_profile(c)
    skip_if_blocked(r)
    assert r.status_code == 200, r.text[:300]
    assert p.refused == 0 and any(n.endswith("tiktok.com") for n in p.names), (p.hosts, p.names)


@pytest.mark.parametrize("kind", ["http", "socks5"])
def test_wrong_proxy_credentials_are_a_proxy_error(tmp_path, kind):
    with proxy(kind, auth="qauser:right-pass") as p:
        with app_with({"SOCIALSCRAPE_PROXY": p.url("qauser:qa-wrong-secret")}, tmp_path, raise_errors=False) as c:
            t0 = time.monotonic()
            r = _tt_profile(c)
            took = time.monotonic() - t0
    assert r.status_code == 502 and is_envelope(r) and r.json()["error"] == "proxy_error", r.text[:300]
    assert "qa-wrong-secret" not in r.text
    assert p.refused >= 1 and not p.hosts  # never tunnelled
    assert took < 20, f"a refused proxy login took {took:.1f}s"


# --- cache: cached_at is the store time ----------------------------------------------------------------


def test_cached_at_is_the_time_the_response_was_stored(tmp_path):
    db = tmp_path / "seed.sqlite3"
    seeded = ResponseCache(str(db))
    seeded.put("/v1/tiktok/profile?handle=qa-seeded", '{"success": true, "credits_charged": 0, "seeded": 1}')
    stored_at = time.time() - 3600 - 30  # an hour and a half minute old
    seeded._conn.execute("UPDATE responses SET stored_at = ?", (stored_at,))
    seeded._conn.commit()
    seeded._conn.close()
    dead = free_port()  # a miss goes to a dead proxy: no upstream traffic
    env = {"SOCIALSCRAPE_CACHE_PATH": str(db), "SOCIALSCRAPE_PROXY": f"http://127.0.0.1:{dead}"}
    with app_with(env, raise_errors=False) as c:
        hit = c.get("/v1/tiktok/profile", params={"handle": "qa-seeded", "cache_max_age": "2h"})
        assert hit.status_code == 200 and hit.headers["x-cache"] == "hit"
        body = hit.json()
        assert body["seeded"] == 1 and body["cached"] is True and ISO_Z.match(body["cached_at"])
        when = datetime.fromisoformat(body["cached_at"].replace("Z", "+00:00"))
        assert when.tzinfo is not None and abs(when.timestamp() - stored_at) < 0.01
        assert datetime.now(timezone.utc).timestamp() - when.timestamp() > 3600
        miss = c.get("/v1/tiktok/profile", params={"handle": "qa-seeded", "cache_max_age": "1h"})
        assert miss.status_code == 502 and miss.json()["error"] == "proxy_error"
        assert "x-cache" not in miss.headers or miss.headers["x-cache"] != "hit"
    con = sqlite3.connect(db)
    try:
        (row,) = con.execute("SELECT stored_at FROM responses").fetchall()
    finally:
        con.close()
    assert row[0] == stored_at, "a failed refresh must not overwrite (or re-date) the cached response"


@pytest.mark.parametrize("cache", ["on", "off"])
def test_cache_max_age_with_too_many_digits_is_400(tmp_path, cache):
    env = {} if cache == "on" else {"SOCIALSCRAPE_CACHE_PATH": ""}
    with app_with(env, tmp_path, raise_errors=False) as c:
        # rejected before any upstream work; a 501 route checks it too, also with the cache off (gap-instagram-5)
        calls = [("/v1/tiktok/profile", {"handle": TT_HANDLE}), (_unsupported(c), {})]
        for path, params in calls:
            r = c.get(path, params={**params, "cache_max_age": HUGE_AGE})
            assert r.status_code == 400 and is_envelope(r), f"{path}: {r.status_code} {r.text[:200]}"
            assert r.json()["error"] == "bad_request" and "cache_max_age" in r.json()["message"]


def test_unusable_cache_path_stops_the_server(tmp_path):
    with serve(tmp_path, {"SOCIALSCRAPE_CACHE_PATH": str(tmp_path)}, wait=False) as srv:  # a directory
        assert srv.wait() == "exited", srv.output()[-500:]
        out = srv.output()
        assert srv.proc.returncode != 0 and "SOCIALSCRAPE_CACHE_PATH" in out and "Traceback" not in out, out[-500:]


# --- cookie files (fake, anonymous) --------------------------------------------------------------------


def _cookie_file(path: Path, domain: str) -> Path:
    path.write_text("# Netscape HTTP Cookie File\n# qa: fake operator-managed file\n"
                    f"{domain}\tTRUE\t/\tTRUE\t2147483647\tQA_FAKE\t1\n")
    return path


@pytest.mark.live
def test_fake_cookie_files_for_every_platform_are_used_read_only(tmp_path):
    files = {"youtube": _cookie_file(tmp_path / "yt.txt", ".youtube.com"),
             "tiktok": _cookie_file(tmp_path / "tt.txt", ".tiktok.com"),
             "instagram": _cookie_file(tmp_path / "ig.txt", ".instagram.com")}
    before = {k: _fingerprint(p) for k, p in files.items()}
    env = {f"SOCIALSCRAPE_COOKIES_{k.upper()}": str(p) for k, p in files.items()}
    with app_with(env, tmp_path) as c:
        health = c.get("/health").json()
        assert health["cookies"] == ["instagram", "tiktok", "youtube"] and not health["cookie_errors"]
        calls = [("/v1/tiktok/profile", {"handle": TT_HANDLE}),
                 ("/v1/instagram/profile", {"handle": "nasa"}),
                 ("/v1/youtube/video", {"url": YT_VIDEO})]
        results = {path: c.get(path, params=params) for path, params in calls}
    assert {k: _fingerprint(p) for k, p in files.items()} == before, "a request rewrote a cookie file"
    usable = {p: r for p, r in results.items() if r.status_code not in (429, 503)}
    if not usable:
        pytest.skip("every platform throttled this IP")
    for path, r in usable.items():
        assert r.status_code == 200 and r.json()["success"] is True, f"{path}: {r.status_code} {r.text[:200]}"


# --- .env.example, CLI, logging, signals ----------------------------------------------------------------


def _env_example() -> dict[str, str]:
    out = {}
    for line in (API_DIR / ".env.example").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def test_env_example_starts_as_shipped(tmp_path):
    env = _env_example()
    assert {"SOCIALSCRAPE_CACHE_PATH", "HOST", "PORT", "SOCIALSCRAPE_LOG_LEVEL"} <= env.keys()
    env.pop("SOCIALSCRAPE_COOKIES_FROM_BROWSER", None)  # never set, not even blank (anonymous-only QA rule)
    env.pop("PORT")  # serve() picks a free one
    with serve(tmp_path, env) as srv:
        h = httpx2.get(f"{srv.base}/health", timeout=10)
        assert h.status_code == 200
        body = h.json()
        assert body["ok"] is True and body["auth"] is False and body["cache"] is True
        assert body["transcription"] is None and body["cookies"] == []
        r = httpx2.get(f"{srv.base}/v1/tiktok/shop/search", params={"query": "x"}, timeout=10)
        assert r.status_code == 501 and is_envelope(r)
    assert (tmp_path / env["SOCIALSCRAPE_CACHE_PATH"]).is_file(), "cache file not created under the working directory"


@pytest.mark.parametrize("arg", ["--help", "--version"])
def test_cli_flags_do_not_start_the_server(tmp_path, arg):
    with serve(tmp_path, args=[arg], wait=False) as srv:
        state = srv.wait(10)
        assert state == "exited", f"`socialscrape {arg}` started the server instead ({state})"
        assert srv.proc.returncode == 0 and srv.output().strip()


def test_bracketed_ipv6_host_listens(tmp_path):
    with serve(tmp_path, {"HOST": "[::1]"}) as srv:
        assert httpx2.get(f"http://[::1]:{srv.port}/health", timeout=10).status_code == 200


@pytest.mark.live
def test_host_that_does_not_resolve_stops_the_server(tmp_path):
    with serve(tmp_path, {"HOST": "bogus.invalid"}, wait=False) as srv:
        assert srv.wait(15) == "exited", srv.output()[-300:]
        out = srv.output()
        assert srv.proc.returncode != 0 and re.search(r"\bHOST\b", out) and "Traceback" not in out, out[-300:]


def test_log_level_warning_silences_info_lines(tmp_path):
    with serve(tmp_path, {"SOCIALSCRAPE_LOG_LEVEL": "WARNING"}) as srv:
        for path in ("/health", "/meta/coverage"):
            assert httpx2.get(srv.base + path, timeout=10).status_code == 200
        srv.proc.send_signal(signal.SIGTERM)
        srv.exit_within(10)
    info = [ln for ln in srv.output().splitlines() if ln.startswith("INFO")]
    assert not info, f"{len(info)} INFO lines at SOCIALSCRAPE_LOG_LEVEL=WARNING, e.g. {info[:3]}"


def _timed_out_request(srv: Server) -> httpx2.Response:
    r = _tt_profile(srv.base)
    assert r.status_code == 500 and r.json()["error"] == "timeout", r.text[:200]
    return r


@pytest.mark.live
def test_sigterm_answers_the_in_flight_request_and_exits(tmp_path):
    with tarpit() as port:
        env = {"SOCIALSCRAPE_PROXY": f"http://127.0.0.1:{port}", "SOCIALSCRAPE_REQUEST_TIMEOUT": "3",
               "SOCIALSCRAPE_SOCKET_TIMEOUT": "30"}
        with serve(tmp_path, env) as srv:
            box: dict[str, object] = {}
            worker = threading.Thread(target=lambda: box.setdefault("r", _tt_profile(srv.base)))
            worker.start()
            time.sleep(1.0)
            srv.proc.send_signal(signal.SIGTERM)
            took = srv.exit_within(15)
            worker.join(20)
    r = box.get("r")
    assert isinstance(r, httpx2.Response) and r.status_code == 500 and r.json()["error"] == "timeout", r
    assert took is not None and took < 10, f"SIGTERM exit took {took}s"


@pytest.mark.live
def test_sigint_exits_promptly_after_a_timed_out_request(tmp_path):
    with tarpit() as port:
        env = {"SOCIALSCRAPE_PROXY": f"http://127.0.0.1:{port}", "SOCIALSCRAPE_REQUEST_TIMEOUT": "1",
               "SOCIALSCRAPE_SOCKET_TIMEOUT": "30"}
        with serve(tmp_path, env) as srv:
            _timed_out_request(srv)  # its worker thread stays blocked on the hung proxy
            srv.proc.send_signal(signal.SIGINT)
            took = srv.exit_within(8)
            assert "Finished server process" in srv.output() or took is not None
            assert took is not None, "Ctrl-C: still running 8 s after uvicorn finished (joining abandoned workers)"


@pytest.mark.live
@pytest.mark.parametrize("second", [0.3, 0.6, 1.0])
def test_second_sigint_during_the_worker_grace_exits_as_fast(tmp_path, second):
    # retest-gaps-core-double-sigint-in-grace: a 2nd Ctrl-C during the 1 s wait for the workers raised
    # KeyboardInterrupt past os._exit, and the process then waited ~28 s for the stuck call (rc -2)
    with tarpit() as port:
        env = {"SOCIALSCRAPE_PROXY": f"http://127.0.0.1:{port}", "SOCIALSCRAPE_REQUEST_TIMEOUT": "1",
               "SOCIALSCRAPE_SOCKET_TIMEOUT": "30"}
        with serve(tmp_path, env) as srv:
            _timed_out_request(srv)  # its worker thread stays blocked on the hung proxy
            srv.proc.send_signal(signal.SIGINT)
            time.sleep(second)
            srv.proc.send_signal(signal.SIGINT)
            took = srv.exit_within(8)
            assert took is not None, f"still running 8 s after a 2nd Ctrl-C {second} s after the 1st"
    out = srv.output()
    assert srv.proc.returncode == 0 and "KeyboardInterrupt" not in out, out[-800:]
    assert "exiting without waiting for 1 yt-dlp/Whisper call(s)" in out


# --- OpenAPI ---------------------------------------------------------------------------------------------


def test_openapi_documents_the_errors_the_server_returns(client):
    spec = client.get("/openapi.json").json()
    implemented = {row["path"] for row in client.get("/meta/coverage").json()["endpoints"]
                   if row["status"] == "implemented"}
    r = client.get("/v1/tiktok/profile")  # missing handle: what a validation error really looks like
    assert r.status_code == 400 and is_envelope(r)
    wrong, missing = [], []
    for path, ops in spec["paths"].items():
        responses = (ops.get("get") or {}).get("responses", {})
        # a 422 in the error envelope is real (post-count's estimate_only); FastAPI's validation one is not
        if "HTTPValidationError" in str(responses.get("422", {})):
            wrong.append(path)
        if path in implemented and "400" not in responses:
            missing.append(path)
    assert not wrong, f"{len(wrong)} operations document a 422 HTTPValidationError the server never sends"
    assert not missing, f"{len(missing)} operations do not document the 400 error envelope"


# --- AI transcription: mlx model names -------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(not HAS_MLX, reason="mlx-whisper not installed")
@pytest.mark.parametrize("model", [
    "small", "large-v3-turbo", "turbo",  # the turbo names: gap-core-mlx-turbo-model-name
])
def test_mlx_model_name_maps_to_an_existing_repo(model):
    import mlx_whisper

    seen: list[str] = []

    def capture(audio_path, path_or_hf_repo=None, language=None, **kwargs):
        seen.append(path_or_hf_repo)
        return {"language": "en", "segments": []}

    env = {"SOCIALSCRAPE_TRANSCRIBE": "mlx-whisper", "SOCIALSCRAPE_WHISPER_MODEL": model,
           "SOCIALSCRAPE_CACHE_PATH": ""}
    with patched_env(env, [(mlx_whisper, "transcribe", capture), (tr, "with_ydl", _fake_download)]):
        asyncio.run(tr.transcribe("https://example.invalid/x", platform="tiktok"))
    assert len(seen) == 1
    repo = seen[0]
    if os.path.isdir(repo):
        return
    try:
        r = httpx2.get(f"https://huggingface.co/api/models/{repo}", timeout=20)
    except httpx2.HTTPError as exc:
        pytest.skip(f"Hugging Face unreachable: {exc}")
    if r.status_code == 429:
        pytest.skip("Hugging Face throttled this IP")
    assert r.status_code == 200, f"WHISPER_MODEL={model!r} loads {repo!r}, which is not on the Hub ({r.status_code})"
