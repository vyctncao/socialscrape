"""Proxy testing must test the selected proxy, not the availability of an IP-check service."""
from types import SimpleNamespace

import pytest
from curl_cffi import CurlOpt, requests
from curl_cffi.requests.exceptions import RequestException

from socialscrape.portal import _test_proxy


def answer(status=200, body="public page", location="", ip="203.0.113.7"):
    return SimpleNamespace(status_code=status, text=body, content=body.encode(),
                           headers={"location": location}, json=lambda: {"ip": ip})


def transport(monkeypatch, target, ip=None):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        result = ip if "ipify" in url else target
        if isinstance(result, Exception):
            raise result
        return result or answer()

    monkeypatch.setattr(requests, "get", get)
    return calls


@pytest.mark.parametrize("proxy,expected", [
    ("host:1234", "http://host:1234"),
    ("//host:1234", "http://host:1234"),
    ("http://user:secret@host:1234", "http://user:secret@host:1234"),
    ("socks5://host:1234", "socks5h://host:1234"),
    ("socks4a://host:1234", "socks4a://host:1234"),
])
def test_probe_normalizes_proxy_and_never_uses_direct_transport(monkeypatch, proxy, expected):
    calls = transport(monkeypatch, answer())
    result = _test_proxy("instagram", proxy)
    assert result["ok"] and result["connected"]
    assert calls[0][0] == "https://www.instagram.com/instagram/"
    assert all(options["proxy"] == expected for _, options in calls)
    assert all(options["curl_options"][CurlOpt.NOPROXY] == "" for _, options in calls)
    assert sum(options["timeout"] for _, options in calls) <= 20
    assert "secret" not in str(result)


def test_ip_lookup_failure_does_not_fail_working_proxy(monkeypatch):
    transport(monkeypatch, answer(), RequestException("IP service blocked"))
    result = _test_proxy("youtube", "host:1234")
    assert result["ok"] and result["connected"] and result["exit_ip"] is None


@pytest.mark.parametrize("status,body,location,blocked", [
    (403, "denied", "", True), (429, "slow down", "", True),
    (302, "redirect", "/accounts/login/", True),
    (200, "Please verify you are human", "", True),
    (404, "missing", "", False), (500, "error", "", False),
    (302, "redirect", "https://example.org", False), (200, "", "", False),
])
def test_platform_failure_is_not_a_broken_proxy(monkeypatch, status, body, location, blocked):
    transport(monkeypatch, answer(status, body, location))
    result = _test_proxy("tiktok", "host:1234")
    assert not result["ok"] and result["connected"]
    assert result["blocked"] is blocked and result["status"] == status


def test_auth_failure_is_actionable_and_does_not_disclose_credentials(monkeypatch):
    calls = transport(monkeypatch, answer(407))
    result = _test_proxy("google", "http://user:secret@host:1234")
    assert not result["ok"] and not result["connected"]
    assert result["error"] == "proxy_auth" and len(calls) == 1
    assert "secret" not in str(result) and "password" in result["message"]


def test_timeout_does_not_try_ip_lookup_or_leak_exception(monkeypatch):
    calls = transport(monkeypatch, RequestException("http://user:secret@host", code=28))
    result = _test_proxy("facebook", "http://user:secret@host:1234")
    assert not result["connected"] and result["error"] == "timeout"
    assert len(calls) == 1 and "secret" not in str(result)


def test_invalid_exit_ip_is_not_rendered(monkeypatch):
    transport(monkeypatch, answer(), answer(ip="<script>secret</script>"))
    assert _test_proxy("instagram", "host:1234")["exit_ip"] is None
