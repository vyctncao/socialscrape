"""An HTTP 200 login wall must reach the actual connection-profile retry loop."""
import asyncio
from types import SimpleNamespace

import pytest

from socialscrape import portal, ytdlp
from socialscrape.errors import NotFound
from socialscrape.platforms import instagram as ig
from tests.test_instagram import _sjs

URL = "https://www.instagram.com/sample/"
GOOD = _sjs({"data": {"xig_user_by_username": {"pk": "123", "username": "sample"}}})


class Connection(str):
    def get_info_extractor(self, name):
        return SimpleNamespace(_is_logged_in=False)


@pytest.mark.parametrize("bad,final", [
    ("<html>Login</html>", "https://www.instagram.com/accounts/login/?next=%2Fsample%2F"),
    ("", URL),
    ("<html>Access denied</html>", URL),
    ('<script>{"pageID":"profilePage"}</script>', URL),
])
def test_profile_refusal_reaches_the_working_backup(monkeypatch, bad, final):
    attempts = []
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: (["primary", "backup"], []))

    def run(platform, fn, opts):
        proxy = opts["_dashboard_proxy"]
        attempts.append(proxy)
        return fn(Connection(proxy))

    monkeypatch.setattr(ytdlp, "_run", run)
    monkeypatch.setattr(ig, "_page_flow", lambda *args: lambda proxy: (bad, final)
                        if proxy == "primary" else (GOOD, URL))
    user, _ = asyncio.run(ig.fetch_profile("sample"))
    assert user["pk"] == "123" and attempts == ["primary", "backup"]


def test_checked_page_refusal_reaches_the_working_backup(monkeypatch):
    attempts = []
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: (["primary", "backup"], []))

    def run(platform, fn, opts):
        proxy = opts["_dashboard_proxy"]
        attempts.append(proxy)
        return fn(Connection(proxy))

    monkeypatch.setattr(ytdlp, "_run", run)
    monkeypatch.setattr(ig, "_page_flow", lambda *args: lambda proxy: ("", URL)
                        if proxy == "primary" else (GOOD, URL))
    html, results = asyncio.run(ig.fetch_page("InstagramUser", URL, "sample", "Profile"))
    assert html == GOOD and results and attempts == ["primary", "backup"]


def test_missing_profile_is_confirmed_with_all_configured_connections(monkeypatch):
    attempts = []
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: (["primary", "backup"], []))

    def run(platform, fn, opts):
        attempts.append(opts["_dashboard_proxy"])
        return fn(None)

    monkeypatch.setattr(ytdlp, "_run", run)
    monkeypatch.setattr(ig, "_page_flow", lambda *args: lambda ydl: ('{"pageID":"httpErrorPage"}', URL))
    with pytest.raises(NotFound):
        asyncio.run(ig.fetch_profile("sample"))
    assert attempts == ["primary", "backup"]


@pytest.mark.parametrize("listing", [False, True])
def test_missing_profile_page_can_reach_a_working_backup(monkeypatch, listing):
    attempts = []
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: (["primary", "backup"], []))

    def run(platform, fn, opts):
        proxy = opts["_dashboard_proxy"]
        attempts.append(proxy)
        return fn(Connection(proxy))

    monkeypatch.setattr(ytdlp, "_run", run)
    missing = '{"pageID":"httpErrorPage"}'
    monkeypatch.setattr(ig, "_page_flow", lambda *args: lambda proxy:
                        (missing if proxy == "primary" else GOOD, URL))
    if listing:
        # Exercise fetch_feed's real retry policy without unrelated GraphQL calls.
        monkeypatch.setattr(ig, "_feed_flow", lambda *args: lambda proxy:
                            ig._profile_user(*_checked_profile(proxy, missing)))
        assert asyncio.run(ig.fetch_feed("sample"))["pk"] == "123"
    else:
        user, _ = asyncio.run(ig.fetch_profile("sample"))
        assert user["pk"] == "123"
    assert attempts == ["primary", "backup"]


def _checked_profile(proxy, missing):
    html = missing if proxy == "primary" else GOOD
    return html, ig._check_page(html, URL, "Profile")


def test_missing_nonprofile_page_remains_final(monkeypatch):
    attempts = []
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: (["primary", "backup"], []))

    def run(platform, fn, opts):
        attempts.append(opts["_dashboard_proxy"])
        return fn(None)

    monkeypatch.setattr(ytdlp, "_run", run)
    monkeypatch.setattr(ig, "_page_flow", lambda *args: lambda ydl:
                        ('{"pageID":"httpErrorPage"}', URL))
    with pytest.raises(NotFound):
        asyncio.run(ig.fetch_page("Instagram", URL, "sample", "Post"))
    assert attempts == ["primary"]
