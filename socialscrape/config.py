"""Runtime settings, read once from environment variables.

A blank variable means its default, except SOCIALSCRAPE_CACHE_PATH, where blank disables the cache.
A value that can't be used raises ConfigError naming the variable; create_app() reads the settings
eagerly, so a bad value stops startup instead of failing every request.
"""

from __future__ import annotations

import math
import os
import re
import socket
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable
from urllib.parse import urlsplit

PLATFORMS = ("youtube", "tiktok", "instagram")
TRANSCRIBE_BACKENDS = ("none", "faster-whisper", "mlx-whisper")
# What yt-dlp's curl_cffi and requests handlers accept.
PROXY_SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
DEFAULT_CACHE_PATH = ".cache/socialscrape.sqlite3"


class ConfigError(ValueError):
    """An environment variable has a value the server can't run with."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _path(value: str) -> str:
    """'~/c.txt', '$HOME/c.txt' expanded as yt-dlp expands its own path options (yt_dlp.utils.expand_path)."""
    return os.path.expandvars(os.path.expanduser(value))


def _number(name: str, default: str, cast: Callable[[str], float], *, minimum: float, inclusive: bool = True):
    raw = _env(name, default) or default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{name} must be {'an integer' if cast is int else 'a number'}, got {raw!r}") from None
    if not math.isfinite(value):
        raise ConfigError(f"{name} must be a finite number, got {raw!r}")
    if value < minimum or (value == minimum and not inclusive):
        raise ConfigError(f"{name} must be {'at least' if inclusive else 'greater than'} {minimum:g}, got {raw!r}")
    return value


def _socket_timeout() -> float:
    """yt-dlp gives it to each socket it opens, so one longer than a socket can wait (about 292 years on Linux and
    macOS) would start, then fail on every request in yt-dlp's requests and urllib handlers, and in curl_cffi's
    too past about 1e16 seconds."""
    name = "SOCIALSCRAPE_SOCKET_TIMEOUT"
    value = _number(name, "20", float, minimum=0, inclusive=False)
    try:
        with socket.socket() as probe:
            probe.settimeout(value)
    except OverflowError:
        raise ConfigError(f"{name} is longer than a socket can wait, got {_env(name)!r}") from None
    except OSError:  # no IPv4 socket to ask (IPv4 turned off): the value stands
        pass
    return value


def validate_proxy(proxy: str | None, *, name: str = "SOCIALSCRAPE_PROXY") -> str | None:
    proxy = proxy.strip() if proxy and proxy.strip() else None
    if not proxy:
        return None
    # Only the scheme in the messages, never the URL: it may carry credentials.
    if (m := re.match(r"([A-Za-z][A-Za-z0-9+.-]*)://", proxy)) and m.group(1).lower() not in PROXY_SCHEMES:
        raise ConfigError(f"{name} has unsupported scheme {m.group(1)!r}; use one of {', '.join(PROXY_SCHEMES)}")
    # yt-dlp reads a scheme-less proxy as http. Its handlers only parse the URL per request, so an unusable host or
    # port would otherwise fail every request, with the URL (and its password) in the error text.
    try:
        url = urlsplit(proxy if m else "http://" + proxy.removeprefix("//"))
        host, port = url.hostname, url.port
    except ValueError:  # 'Invalid IPv6 URL', a port that isn't a number or is out of range
        host, port = None, 0
    if not host or any(c.isspace() or not c.isprintable() for c in host) or port == 0:
        raise ConfigError(f"{name} must be a proxy URL such as http://user:pass@host:port with a valid host and, "
                          "if given, a port from 1 to 65535")
    return proxy


def _proxy() -> str | None:
    return validate_proxy(_env("SOCIALSCRAPE_PROXY"))


def validate_public_url(value: str | None, *, name: str = "SOCIALSCRAPE_PUBLIC_URL") -> str | None:
    """'https://scrape.example.com/' -> 'https://scrape.example.com': the origin every emailed link points to."""
    value = value.strip().rstrip("/") if value and value.strip() else None
    if not value:
        return None
    try:
        url = urlsplit(value)
        # Only an origin: the dashboard's routes (/invite/..., /reset/...) are at the root, so a path would make
        # every emailed link a 404.
        unusable = (url.scheme.lower() not in ("http", "https") or not url.hostname or url.port == 0
                    or url.username is not None or url.path or url.query or url.fragment)
    except ValueError:  # 'Invalid IPv6 URL', a port that isn't a number or is out of range
        unusable = True
    if unusable or any(c.isspace() or not c.isprintable() for c in value):
        raise ConfigError(f"{name} must be the dashboard's public address without a path, such as "
                          "https://scrape.example.com")
    return value


# 'noreply@example.com' or 'Display Name <noreply@example.com>', the two forms Resend's `from` takes.
_SENDER = re.compile(r"(?:[^<>\r\n]*<(?P<bracketed>[^<>\s]+)>|(?P<bare>[^<>\s]+))")
_DOMAIN = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")


def validate_sender(value: str | None, *, name: str = "SOCIALSCRAPE_EMAIL_FROM") -> str | None:
    value = value.strip() if value and value.strip() else None
    if not value:
        return None
    m = _SENDER.fullmatch(value)
    local, _, domain = (m["bracketed"] or m["bare"]).rpartition("@") if m else ("", "", "")
    if not local or "@" in local or not _DOMAIN.fullmatch(domain) or len(value) > 320 or not value.isprintable():
        raise ConfigError(f"{name} must be an email address such as SocialScrape <noreply@example.com>")
    return value


def validate_resend_key(value: str | None, *, name: str = "SOCIALSCRAPE_RESEND_API_KEY") -> str | None:
    value = value.strip() if value and value.strip() else None
    # Sent as an HTTP header: anything else would fail every email, possibly with the key in the error text.
    if value and (not value.isascii() or any(c.isspace() or not c.isprintable() for c in value)):
        raise ConfigError(f"{name} must be a Resend API key (re_…) without spaces")
    return value


@dataclass(frozen=True)
class Settings:
    # Accepted values for the `x-api-key` header (ASCII). Empty means auth is disabled.
    api_keys: frozenset[str]
    # SQLite file backing `cache_max_age`. Empty string (the variable set but blank) disables caching.
    cache_path: str
    # Threads available for blocking yt-dlp calls (>= 1).
    max_workers: int
    # Wall-clock limit for a single upstream fetch, in seconds (> 0).
    request_timeout: float
    # yt-dlp's per-connection network timeout, in seconds (> 0).
    socket_timeout: float
    # http(s)/socks4(a)/socks5(h) URL; scheme-less means http.
    proxy: str | None
    # platform -> Netscape cookies.txt path. Read-only: never written back.
    cookie_files: dict[str, str]
    # Invite-only dashboard/session database. Empty disables the dashboard.
    portal_path: str
    # yt-dlp `cookiesfrombrowser` spec, e.g. "chrome" or "firefox:default-release"
    cookies_from_browser: str | None
    # none | faster-whisper | mlx-whisper
    transcribe_backend: str
    whisper_model: str
    # Resend API key for invitation and password emails; None turns email off (owners share links by hand).
    resend_api_key: str | None = field(repr=False)
    # Resend `from`, e.g. "SocialScrape <noreply@example.com>". Set whenever resend_api_key is.
    email_from: str | None
    # The dashboard's public origin, without a trailing slash. Emailed links use it, never the request's Host.
    public_url: str | None

    def cookie_file(self, platform: str) -> str | None:
        return self.cookie_files.get(platform)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    keys = _env("SOCIALSCRAPE_API_KEYS", "") or ""
    if not keys.isascii():  # HTTP clients don't agree on how to send other characters
        raise ConfigError("SOCIALSCRAPE_API_KEYS must be ASCII")
    raw_cache = os.environ.get("SOCIALSCRAPE_CACHE_PATH")  # not _env: blank must disable, not mean the default
    backend = (_env("SOCIALSCRAPE_TRANSCRIBE", "none") or "none").lower()
    if backend not in TRANSCRIBE_BACKENDS:
        raise ConfigError(f"SOCIALSCRAPE_TRANSCRIBE must be one of {', '.join(TRANSCRIBE_BACKENDS)}, got {backend!r}")
    resend_api_key = validate_resend_key(_env("SOCIALSCRAPE_RESEND_API_KEY"))
    email_from = validate_sender(_env("SOCIALSCRAPE_EMAIL_FROM"))
    public_url = validate_public_url(_env("SOCIALSCRAPE_PUBLIC_URL"))
    if resend_api_key and not (email_from and public_url):
        raise ConfigError("SOCIALSCRAPE_RESEND_API_KEY needs SOCIALSCRAPE_EMAIL_FROM and SOCIALSCRAPE_PUBLIC_URL")
    return Settings(
        api_keys=frozenset(k.strip() for k in keys.split(",") if k.strip()),
        cache_path=DEFAULT_CACHE_PATH if raw_cache is None else _path(raw_cache.strip()),
        max_workers=int(_number("SOCIALSCRAPE_MAX_WORKERS", "8", int, minimum=1)),
        request_timeout=_number("SOCIALSCRAPE_REQUEST_TIMEOUT", "180", float, minimum=0, inclusive=False),
        socket_timeout=_socket_timeout(),
        proxy=_proxy(),
        portal_path=_path(os.environ.get("SOCIALSCRAPE_PORTAL_PATH", "").strip()),
        cookie_files={
            p: _path(path) for p in PLATFORMS if (path := _env(f"SOCIALSCRAPE_COOKIES_{p.upper()}"))
        },
        cookies_from_browser=_env("SOCIALSCRAPE_COOKIES_FROM_BROWSER"),
        transcribe_backend=backend,
        whisper_model=_env("SOCIALSCRAPE_WHISPER_MODEL", "small") or "small",
        resend_api_key=resend_api_key,
        email_from=email_from,
        public_url=public_url,
    )
