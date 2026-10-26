"""Bounded, anonymous first-party public requests with restricted redirects."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException, Timeout

from ..config import get_settings
from ..errors import (
    BadRequest,
    Forbidden,
    NotFound,
    UpstreamBlocked,
    UpstreamError,
    UpstreamUnreachable,
)
from ..ytdlp import with_ydl

MAX_BYTES = 8 * 1024 * 1024


@dataclass
class PublicResponse:
    body: bytes
    url: str
    headers: dict

    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise UpstreamError("The public platform returned invalid JSON") from exc


def _allowed(url: str, hosts: set[str]) -> bool:
    try:
        value = urlsplit(url)
        return (
            value.scheme == "https"
            and value.hostname in hosts
            and not value.username
            and not value.password
            and value.port in (None, 443)
        )
    except ValueError:
        return False


def fetch(
    ydl, url: str, hosts: set[str], *, headers: dict | None = None
) -> PublicResponse:
    """No ambient credentials; never follow profile-controlled external redirects."""
    if not _allowed(url, hosts):
        raise BadRequest("The URL must identify a supported public platform")
    settings = get_settings()
    deadline = time.monotonic() + settings.request_timeout
    proxy = ydl.params.get("proxy") or None
    # A new session per fetch has no browser cookies or authentication context.
    with requests.Session(trust_env=False) as session:
        for _ in range(5):
            raw = bytearray()
            oversized = False

            def collect(chunk):
                nonlocal oversized
                if len(raw) + len(chunk) > MAX_BYTES:
                    oversized = True
                    return 0
                raw.extend(chunk)
                return len(chunk)

            left = deadline - time.monotonic()
            if left <= 0:
                raise UpstreamError(
                    "Timed out waiting for the public platform", code="timeout"
                )
            try:
                response = session.get(
                    url,
                    headers={"Cookie": "", **(headers or {})},
                    impersonate="chrome",
                    proxy=proxy,
                    allow_redirects=False,
                    timeout=min(settings.socket_timeout, left),
                    content_callback=collect,
                )
            except Timeout:
                raise UpstreamError(
                    "Timed out waiting for the public platform", code="timeout"
                ) from None
            except RequestException:
                if oversized:
                    raise UpstreamError(
                        "The public platform response exceeded the size limit"
                    ) from None
                raise UpstreamUnreachable(
                    "The public platform could not be reached",
                    code="proxy_error" if proxy else "network_error",
                ) from None
            if oversized:
                raise UpstreamError(
                    "The public platform response exceeded the size limit"
                )
            if response.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, response.headers.get("location", ""))
                if not response.headers.get("location") or not _allowed(target, hosts):
                    raise Forbidden(
                        "This public profile redirects outside its supported platform"
                    )
                url = target
                continue
            if response.status_code == 404:
                raise NotFound("Public resource not found")
            if response.status_code in (403, 429):
                retry = response.headers.get("retry-after", "")
                raise UpstreamBlocked(
                    "The public platform blocked or rate-limited this connection",
                    retry_after=min(int(retry), 86400)
                    if retry.isascii() and retry.isdigit()
                    else None,
                )
            if response.status_code == 401:
                raise Forbidden(
                    "The platform requires authentication for this resource"
                )
            if response.status_code in (400, 422):
                raise BadRequest(
                    "The public platform rejected the requested resource or page"
                )
            if not 200 <= response.status_code < 300:
                raise UpstreamError(
                    "The public platform returned an unsuccessful response",
                    upstream_status=response.status_code,
                )
            return PublicResponse(bytes(raw), url, dict(response.headers))
    raise UpstreamError("The public platform returned too many redirects")


async def run(platform: str, fn):
    return await with_ydl(platform, fn, use_cookies=False)
