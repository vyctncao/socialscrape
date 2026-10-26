"""A dead or restricted signed-in account never makes a route worse than having no account (sessions.py).

Account checks run against recorded, redacted answers (tests/fixtures/instagram/account_*, tests/fixtures/tiktok/
passport_*); with_ydl's skip/retry/501 logic runs with a stand-in _run and stand-in checks. Nothing here asks a
platform.
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from socialscrape import portal, sessions, ytdlp
from socialscrape.errors import (AccountRejected, LoginRequired, NotFound, SessionInvalid, UpstreamBlocked,
                                 UpstreamError)
from socialscrape.platforms import instagram as ig
from socialscrape.platforms import instagram_authenticated, tiktok
from socialscrape.sessions import Account, Verdict
from tests.e2e import app_with, patched_env

FIXTURES = Path(__file__).parent / "fixtures"
CURRENT_USER = json.loads((FIXTURES / "instagram" / "account_check.json").read_text())["current_user"]
SIGNED_IN_HOME = (FIXTURES / "instagram" / "account_home_signed_in.html").read_text()
LOGGED_OUT_HOME = (FIXTURES / "instagram" / "account_home_logged_out.html").read_text()
PASSPORT_EXPIRED = json.loads((FIXTURES / "tiktok" / "passport_session_expired.json").read_text())
PASSPORT_OK = json.loads((FIXTURES / "tiktok" / "passport_ok.json").read_text())
APP_ANSWERS = json.loads((FIXTURES / "tiktok" / "app_answers_dead_session.json").read_text())
VIEWER = "4242424242"
SECRET = "SECRET-SESSION-VALUE"
IG_COOKIES = ("# Netscape HTTP Cookie File\n"
              f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\t{SECRET}\n"
              f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tds_user_id\t{VIEWER}\n")
TT_COOKIES = ("# Netscape HTTP Cookie File\n"
              f".tiktok.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\t{SECRET}\n"
              f".tiktok.com\tTRUE\t/\tTRUE\t2147483647\tsid_tt\t{SECRET}\n")


# --- the platforms' account checks, on recorded answers ----------------------------------------------------------

class _Cookie(SimpleNamespace):
    pass


class _CheckIE:
    """An extractor stand-in for the account checks: a cookie jar (name -> value) and canned answers by URL. An
    answer can delete cookies, as Instagram's Set-Cookie does for a session it doesn't know."""

    def __init__(self, cookies: dict[str, str], answers: dict[str, dict]):
        self.cookies, self.answers, self.asked = dict(cookies), answers, []
        self._api_headers, self._can_impersonate = {"X-IG-App-ID": "936619743392459"}, True

    @property
    def _is_logged_in(self) -> bool:
        return bool(self.cookies.get("sessionid"))

    def _get_cookies(self, url):
        return {name: _Cookie(value=value) for name, value in self.cookies.items()}

    def _answer(self, url):
        key = next(k for k in self.answers if url.startswith(k))
        self.asked.append(key)
        answer = self.answers[key]
        for name in answer.get("deletes", ()):
            self.cookies.pop(name, None)
        return answer

    def _request_webpage(self, url, video_id, **kwargs):
        answer = self._answer(url)
        body = answer.get("body", "")
        raw = (body if isinstance(body, str) else json.dumps(body)).encode()
        return SimpleNamespace(status=answer["status"], url=answer["final_url"],
                               headers={"Content-Type": answer.get("content_type", "text/html")},
                               read=lambda: raw, close=lambda: None)

    def _download_webpage_handle(self, url, video_id, **kwargs):
        answer = self._answer(url)
        body = answer["body"]
        return (body if isinstance(body, str) else json.dumps(body)), SimpleNamespace(
            status=answer["status"], url=answer.get("final_url", url))


def _ydl(ie):
    return SimpleNamespace(get_info_extractor=lambda name: ie)


ACCOUNT_API = "https://www.instagram.com/api/v1/accounts/current_user/"
HOME = "https://www.instagram.com/"


def _current_user(name, **extra):
    answer = dict(CURRENT_USER[name], **extra)
    if isinstance(answer.get("body"), dict):
        answer["body"] = json.loads(json.dumps(answer["body"]).replace("<ds_user_id>", VIEWER))
    if answer.get("session_cookie_kept") is False:
        answer["deletes"] = ["sessionid"]
    return answer


def _home(html, **extra):
    return {"status": 200, "final_url": HOME, "body": html.replace("<ds_user_id>", VIEWER), **extra}


def test_instagram_flagged_session_is_signed_in_but_restricted():
    """Recorded: the account API sends the flagged session to the home page, whose viewer is the account."""
    ie = _CheckIE({"sessionid": SECRET, "ds_user_id": VIEWER},
                  {ACCOUNT_API: _current_user("flagged"), HOME: _home(SIGNED_IN_HOME)})
    verdict = ig.account_check(_ydl(ie))
    assert (verdict.status, verdict.limited, verdict.account) == ("alive", True, "sample.account")
    assert "home page" in verdict.reason and ie.asked == [ACCOUNT_API, HOME]


def test_instagram_unknown_session_is_rejected_in_one_request():
    """Recorded: a session id Instagram doesn't know gets the 401 and has its session cookie deleted."""
    ie = _CheckIE({"sessionid": SECRET, "ds_user_id": VIEWER}, {ACCOUNT_API: _current_user("unknown_session")})
    verdict = ig.account_check(_ydl(ie))
    assert verdict.status == "rejected" and "deleted the session cookie" in verdict.reason
    assert ie.asked == [ACCOUNT_API]


def test_instagram_logged_out_home_is_rejected_but_a_401_alone_is_not():
    """The 401 is also Instagram's anonymous rate-limit answer: only the logged-out home page settles it."""
    answers = {ACCOUNT_API: _current_user("no_session"), HOME: _home(LOGGED_OUT_HOME)}
    verdict = ig.account_check(_ydl(_CheckIE({"sessionid": SECRET, "ds_user_id": VIEWER}, answers)))
    assert verdict.status == "rejected" and "logged-out home page" in verdict.reason
    answers[HOME] = _home("<html><title>Instagram</title></html>")  # no pageID: a block page, not a verdict
    assert ig.account_check(_ydl(_CheckIE({"sessionid": SECRET}, answers))).status == "unknown"


def test_instagram_working_session_and_inconclusive_answers():
    working = _CheckIE({"sessionid": SECRET, "ds_user_id": VIEWER}, {ACCOUNT_API: _current_user("working")})
    verdict = ig.account_check(_ydl(working))
    assert (verdict.status, verdict.limited, verdict.account) == ("alive", False, "sample.account")
    assert working.asked == [ACCOUNT_API]
    for status in (429, 500, 502):
        ie = _CheckIE({"sessionid": SECRET}, {ACCOUNT_API: {"status": status, "final_url": ACCOUNT_API}})
        assert ig.account_check(_ydl(ie)).status == "unknown" and ie.asked == [ACCOUNT_API]
    for path, says in (("/accounts/login/?next=%2F", "login page"), ("/challenge/action/", "checkpoint"),
                       ("/accounts/suspended/", "suspended")):
        ie = _CheckIE({"sessionid": SECRET}, {ACCOUNT_API: {"status": 200, "final_url": HOME.rstrip("/") + path}})
        verdict = ig.account_check(_ydl(ie))
        assert verdict.status == "rejected" and says in verdict.reason
    ie = _CheckIE({"csrftoken": "x"}, {})
    assert ig.account_check(_ydl(ie)).status == "rejected" and ie.asked == []  # no session cookie: no request


PASSPORT = "https://www.tiktok.com/passport/web/account/info/"


def test_tiktok_passport_check():
    expired = _CheckIE({"sessionid": SECRET, "sid_tt": SECRET}, {PASSPORT: PASSPORT_EXPIRED})
    verdict = tiktok.account_check(_ydl(expired))
    assert verdict.status == "rejected" and "session expired, please sign in again" in verdict.reason
    verdict = tiktok.account_check(_ydl(_CheckIE({"sid_tt": SECRET}, {PASSPORT: PASSPORT_OK})))
    assert (verdict.status, verdict.account) == ("alive", "sample.account")
    # the same session_expired body answers no cookies at all: without a session cookie it is never asked
    nothing = _CheckIE({"tt_csrf_token": "x"}, {PASSPORT: PASSPORT_EXPIRED})
    assert tiktok.account_check(_ydl(nothing)).status == "rejected" and nothing.asked == []
    for answer in ({"status": 429, "body": ""}, {"status": 200, "body": "<html><title>Please wait...</title>"},
                   {"status": 200, "body": {"message": "error", "data": {"name": "other", "error_code": 7}}}):
        assert tiktok.account_check(_ydl(_CheckIE({"sid_tt": SECRET}, {PASSPORT: answer}))).status == "unknown"


# --- with_ydl: skip, retry without cookies, 501 for login-only routes ---------------------------------------------

class _Call:
    """A stand-in _run: records each route call's connection and runs `fn` on a ydl stand-in that says which
    account (if any) it carries; account checks (sessions.verify) go through it too and are counted apart."""

    def __init__(self, monkeypatch, proxies=(), entries=()):
        self.calls, self.checks = [], []
        monkeypatch.setattr(portal, "active_platform_pool", lambda platform: (list(proxies), list(entries)))
        monkeypatch.setattr(ytdlp, "_run", self.run)

    def run(self, platform, fn, opts):
        account = opts.get("_account")
        ydl = SimpleNamespace(_socialscrape_account=account, cookies=opts.get("_dashboard_cookies"),
                              proxy=opts.get("_dashboard_proxy"))
        if getattr(fn, "__name__", "") == "<lambda>" and fn.__qualname__.startswith("verify"):
            self.checks.append((account.key if account else None, ydl.proxy))
        else:
            self.calls.append((account.key if account else None, ydl.cookies, ydl.proxy))
        return fn(ydl)


def _probe(platform, verdicts):
    """sessions.PROBES[platform] answering `verdicts` (key -> Verdict) for the account the ydl carries."""
    return {platform: lambda ydl: verdicts[ydl._socialscrape_account.key]}


def _logged_out_route(ydl):
    """A route that works without cookies; with an account it gets what a dead session gets."""
    if ydl._socialscrape_account is not None:
        raise SessionInvalid("instagram", "Instagram served its logged-out page to the signed-in session")
    return "public data"


def test_rejected_account_is_skipped_and_the_call_made_without_cookies(monkeypatch, caplog):
    call = _Call(monkeypatch, ["http://proxy-a:1"], [("entry1", IG_COOKIES)])
    monkeypatch.setattr(sessions, "PROBES", _probe("instagram", {"entry1": Verdict("rejected", "dead session")}))
    with caplog.at_level(logging.WARNING, logger="socialscrape.sessions"):
        assert asyncio.run(ytdlp.with_ydl("instagram", _logged_out_route)) == "public data"
    # the account's call, then the same proxy without account cookies ('' keeps env/browser cookies out too)
    assert call.calls == [("entry1", IG_COOKIES, "http://proxy-a:1"), (None, ytdlp.NO_COOKIES, "http://proxy-a:1")]
    assert call.checks == [("entry1", "http://proxy-a:1")]
    warning = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning) == 1 and "instagram" in warning[0].getMessage() and "entry1" in warning[0].getMessage()
    assert SECRET not in caplog.text

    call.calls.clear()  # the next call skips it: one call, no account, no check
    assert asyncio.run(ytdlp.with_ydl("instagram", _logged_out_route)) == "public data"
    assert call.calls == [(None, ytdlp.NO_COOKIES, "http://proxy-a:1")] and len(call.checks) == 1

    call.calls.clear()  # once the cooldown is over the account is used (and checked) again
    later = time.time() + sessions.COOLDOWNS[0] + 1
    monkeypatch.setattr(sessions.time, "time", lambda: later)
    assert asyncio.run(ytdlp.with_ydl("instagram", _logged_out_route)) == "public data"
    assert call.calls[0][0] == "entry1" and len(call.checks) == 2
    health = sessions.entry_health("instagram", "entry1")
    assert health["status"] == "rejected" and health["retry_at"] == pytest.approx(later + sessions.COOLDOWNS[1])


def test_second_account_is_used_when_the_first_is_rejected(monkeypatch):
    call = _Call(monkeypatch, [], [("one", IG_COOKIES), ("two", IG_COOKIES.replace(VIEWER, "77"))])
    monkeypatch.setattr(sessions, "PROBES", _probe("instagram", {"one": Verdict("rejected", "dead"),
                                                                 "two": Verdict("alive")}))

    def route(ydl):
        if ydl._socialscrape_account.key == "one":
            raise UpstreamError("Instagram returned no signed-in profile")
        return "signed-in data"

    assert asyncio.run(ytdlp.with_ydl("instagram", route)) == "signed-in data"
    assert [key for key, _, _ in call.calls] == ["one", "two"]  # no call without cookies


def _login_route(platform):
    def route(ydl):
        if ydl._socialscrape_account is None:  # the route's pre-flight: no session cookie, no request
            raise LoginRequired(f"Add {platform} account cookies in Platform settings to use this endpoint",
                                platform=platform)
        sessions.confirm(ydl, platform)  # checked before the call (cached): its answer wouldn't tell
        raise UpstreamError("TikTok API failed: Please upgrade your TikTok app.")  # what a dead session gets
    return route


def test_login_route_answers_501_naming_the_rejected_dashboard_account(monkeypatch):
    call = _Call(monkeypatch, [], [("entry1", TT_COOKIES)])
    monkeypatch.setattr(sessions, "PROBES", _probe("tiktok", {"entry1": Verdict("rejected", "TikTok says the "
                                                                                 "session expired")}))
    with pytest.raises(AccountRejected) as exc:
        asyncio.run(ytdlp.with_ydl("tiktok", _login_route("tiktok")))
    err = exc.value
    assert (err.status, err.code, err.details["reason"]) == (501, "platform_auth_required", "account_rejected")
    assert "The configured TikTok account (Primary) was rejected: TikTok says the session expired" in err.message
    assert "Settings → Proxies & accounts" in err.message and SECRET not in err.message
    assert [cookies for _, cookies, _ in call.calls] == [TT_COOKIES, ytdlp.NO_COOKIES]
    # while it cools down, the route answers the same 501 at once (the pre-flight makes no request)
    call.calls.clear()
    with pytest.raises(AccountRejected):
        asyncio.run(ytdlp.with_ydl("tiktok", _login_route("tiktok")))
    assert call.calls == [(None, ytdlp.NO_COOKIES, None)]


def test_login_route_keeps_a_working_accounts_own_error(monkeypatch):
    _Call(monkeypatch, [], [("entry1", TT_COOKIES)])
    monkeypatch.setattr(sessions, "PROBES", _probe("tiktok", {"entry1": Verdict("alive")}))
    with pytest.raises(UpstreamError, match="upgrade your TikTok app"):  # the account is fine: the route isn't
        asyncio.run(ytdlp.with_ydl("tiktok", _login_route("tiktok")))


def test_login_route_with_one_working_account_left_keeps_its_error(monkeypatch):
    call = _Call(monkeypatch, [], [("dead", TT_COOKIES), ("live", TT_COOKIES)])
    monkeypatch.setattr(sessions, "PROBES", _probe("tiktok", {"dead": Verdict("rejected", "expired"),
                                                              "live": Verdict("alive")}))
    with pytest.raises(UpstreamError, match="upgrade your TikTok app"):
        asyncio.run(ytdlp.with_ydl("tiktok", _login_route("tiktok")))
    assert [key for key, _, _ in call.calls] == ["dead", "live", None]


def test_restricted_account_answers_501_only_for_session_evidence_on_login_routes(monkeypatch):
    """A restricted account's failure on a login-only route is answered as "replace this account" only when it is
    about the session (Instagram sent the signed-in call to its home page: SessionRejected). A 404, a throttle or a
    field error keeps its own status and code, with details.account_health; logged-out routes retry without
    cookies."""
    call = _Call(monkeypatch, [], [("entry1", IG_COOKIES)])
    restricted = Verdict("alive", "its account API sends the session to the home page", "sample", limited=True)
    monkeypatch.setattr(sessions, "PROBES", _probe("instagram", {"entry1": restricted}))

    def login_route(failure):
        def route(ydl):
            if ydl._socialscrape_account is None:
                raise LoginRequired("Add Instagram account cookies", platform="instagram")
            raise failure()
        return route

    with pytest.raises(AccountRejected) as exc:
        asyncio.run(ytdlp.with_ydl("instagram", login_route(ig.SessionRejected)))
    assert exc.value.details["reason"] == "account_limited"
    assert "Instagram restricts this account" in exc.value.message and "login page" in exc.value.message
    assert sessions.usable(Account("instagram", "entry1", "dashboard"))  # restricted, not skipped

    for failure, status, code in (
            (lambda: NotFound("Unable to download JSON metadata: HTTP Error 404: Not Found"), 404, "not_found"),
            (lambda: UpstreamBlocked("Instagram refused the anonymous request: A server error field_exception "
                                     "occured"), 503, "upstream_blocked"),
            (lambda: UpstreamError("Instagram returned an incomplete tagged-post listing"), 500, "upstream_error")):
        call.calls.clear()
        with pytest.raises(Exception) as exc:
            asyncio.run(ytdlp.with_ydl("instagram", login_route(failure)))
        assert not isinstance(exc.value, AccountRejected)
        assert (exc.value.status, exc.value.code, exc.value.details.get("account_health")) == (status, code,
                                                                                               "limited")
        # the account's call, then the pre-flight without cookies (no request) that shows the route needs one
        assert [cookies for _, cookies, _ in call.calls] == [IG_COOKIES, ytdlp.NO_COOKIES]

    def profile(ydl):
        if ydl._socialscrape_account is not None:
            raise NotFound("Instagram profile not found")
        return "logged-out profile"

    call.calls.clear()
    assert asyncio.run(ytdlp.with_ydl("instagram", profile)) == "logged-out profile"
    assert [cookies for _, cookies, _ in call.calls] == [IG_COOKIES, ytdlp.NO_COOKIES]


@pytest.mark.parametrize("verdict", [Verdict("alive", "restricted", "sample", limited=True),
                                     Verdict("unknown", "Instagram answered the account check with HTTP 429")])
def test_restricted_or_unchecked_account_falls_back_through_every_proxy(monkeypatch, verdict):
    """The call without cookies after such an account fails starts at the same proxy and goes on through the
    remaining ones, as a call with no account would: never on the last proxy only."""
    working = {"p1"}

    def profile(ydl):
        if ydl._socialscrape_account is not None:
            raise UpstreamError("Instagram served a partial profile page", code="no_profile_data")
        if ydl.proxy not in working:  # only these egresses are not blocked logged out
            raise UpstreamBlocked("Instagram sent the logged-out request to its login page")
        return "public profile"

    call = _Call(monkeypatch, ["p1", "p2", "p3"], [("acct", IG_COOKIES)])
    monkeypatch.setattr(sessions, "PROBES", _probe("instagram", {"acct": verdict}))
    for _ in range(2):  # every request: one try with the account, then no cookies from the same proxy
        call.calls.clear()
        assert asyncio.run(ytdlp.with_ydl("instagram", profile)) == "public profile"
        assert [(key, proxy) for key, _, proxy in call.calls] == [("acct", "p1"), (None, "p1")]
    working.clear()
    working.add("p2")
    call.calls.clear()
    assert asyncio.run(ytdlp.with_ydl("instagram", profile)) == "public profile"
    assert [(key, proxy) for key, _, proxy in call.calls] == [("acct", "p1"), (None, "p1"), (None, "p2")]
    assert sessions.usable(Account("instagram", "acct", "dashboard"))


def test_login_route_resumes_the_account_on_the_next_proxy(monkeypatch):
    """An inconclusive check (the first proxy is blocked) sends the call on without cookies; the login-only route's
    pre-flight then shows it needs the account, which takes over again on the next proxy."""
    call = _Call(monkeypatch, ["p1", "p2"], [("acct", IG_COOKIES)])
    monkeypatch.setattr(sessions, "PROBES", _probe("instagram", {"acct": Verdict("unknown", "HTTP 429")}))

    def search(ydl):
        if ydl._socialscrape_account is None:
            raise LoginRequired("Add Instagram account cookies", platform="instagram")
        if ydl.proxy == "p1":
            raise UpstreamBlocked("Instagram rate-limited the request (HTTP 429); try again later")
        return "signed-in results"

    assert asyncio.run(ytdlp.with_ydl("instagram", search)) == "signed-in results"
    assert [(key, proxy) for key, _, proxy in call.calls] == [("acct", "p1"), (None, "p1"), ("acct", "p2")]


def test_working_account_that_is_throttled_is_not_retried_without_cookies(monkeypatch):
    """A throttle answered to a working account throttles the egress, which a logged-out call shares."""
    call = _Call(monkeypatch, [], [("acct", IG_COOKIES)])
    monkeypatch.setattr(sessions, "PROBES", _probe("instagram", {"acct": Verdict("alive", account="x")}))

    def throttled(ydl):
        raise UpstreamBlocked("Instagram rate-limited the request: Please wait a few minutes")

    for _ in range(2):
        call.calls.clear()
        with pytest.raises(UpstreamBlocked):
            asyncio.run(ytdlp.with_ydl("instagram", throttled))
        assert [key for key, _, _ in call.calls] == ["acct"]
    assert len(call.checks) == 1  # the working verdict is cached

    def partial(ydl):  # an incomplete answer to the session is still retried logged out, once
        if ydl._socialscrape_account is not None:
            raise UpstreamError("Instagram served a partial profile page", code="no_profile_data")
        return "public profile"

    call.calls.clear()
    assert asyncio.run(ytdlp.with_ydl("instagram", partial)) == "public profile"
    assert [key for key, _, _ in call.calls] == ["acct", None]


def test_login_route_with_a_working_account_keeps_its_session_error(monkeypatch):
    """A SessionInvalid from an account whose check just passed is the account's own error, not a 501 asking to
    replace it (the dashboard would show Working)."""
    call = _Call(monkeypatch, [], [("acct", IG_COOKIES)])
    monkeypatch.setattr(sessions, "PROBES", _probe("instagram", {"acct": Verdict("alive", account="x")}))

    def login_route(ydl):
        if ydl._socialscrape_account is None:
            raise LoginRequired("Add Instagram account cookies", platform="instagram")
        raise SessionInvalid("instagram", "Instagram served its logged-out page to the signed-in session")

    with pytest.raises(SessionInvalid) as exc:
        asyncio.run(ytdlp.with_ydl("instagram", login_route))
    assert not isinstance(exc.value, AccountRejected) and exc.value.code == "session_invalid"
    assert sessions.entry_health("instagram", "acct")["status"] == "alive"
    assert [key for key, _, _ in call.calls] == ["acct", None]

    sessions.reset()  # with no conclusive check, the same answer does name the account
    monkeypatch.setattr(sessions, "PROBES", _probe("instagram", {"acct": Verdict("unknown", "HTTP 429")}))
    with pytest.raises(AccountRejected) as exc:
        asyncio.run(ytdlp.with_ydl("instagram", login_route))
    assert exc.value.details["reason"] == "account_rejected" and "was not accepted" in exc.value.message


def test_missing_content_with_a_working_account_stays_one_request(monkeypatch):
    call = _Call(monkeypatch, ["p1", "p2"], [("entry1", IG_COOKIES)])
    monkeypatch.setattr(sessions, "PROBES", _probe("instagram", {"entry1": Verdict("alive")}))

    def missing(ydl):
        raise NotFound("Post not found")

    with pytest.raises(NotFound):
        asyncio.run(ytdlp.with_ydl("instagram", missing))
    assert call.calls == [("entry1", IG_COOKIES, "p1")]


def test_last_failure_is_retried_without_cookies_for_platforms_without_a_check(monkeypatch):
    call = _Call(monkeypatch, [], [("entry1", "# Netscape HTTP Cookie File\n")])

    def route(ydl):
        if ydl._socialscrape_account is not None:
            raise UpstreamBlocked("Meta refused the request")
        return "ad library"

    assert asyncio.run(ytdlp.with_ydl("facebook", route)) == "ad library"
    assert [cookies for _, cookies, _ in call.calls] == ["# Netscape HTTP Cookie File\n", ytdlp.NO_COOKIES]
    assert call.checks == [] and sessions.entry_health("facebook", "entry1")["status"] == "unchecked"


def test_account_without_a_session_cookie_is_rejected_by_the_pre_flight(monkeypatch):
    _Call(monkeypatch, [], [("entry1", "# Netscape HTTP Cookie File\n.tiktok.com\tTRUE\t/\tTRUE\t0\tttwid\tx\n")])

    def route(ydl):
        raise LoginRequired("Add TikTok account cookies in Platform settings", platform="tiktok")

    with pytest.raises(AccountRejected, match="hold no TikTok session cookie"):
        asyncio.run(ytdlp.with_ydl("tiktok", route))


def test_env_cookie_file_gets_the_same_protection(monkeypatch, tmp_path):
    path = tmp_path / "tiktok.txt"
    path.write_text(TT_COOKIES)
    calls = []

    def run(platform, fn, opts):
        account = opts.get("_account")
        calls.append((account.source if account else None, opts.get("_dashboard_cookies")))
        return fn(SimpleNamespace(_socialscrape_account=account))

    with patched_env({"SOCIALSCRAPE_COOKIES_TIKTOK": str(path)}, [(ytdlp, "_run", run)]):
        monkeypatch.setattr(sessions, "PROBES", {"tiktok": lambda ydl: Verdict("rejected", "TikTok says the "
                                                                                           "session expired")})
        with pytest.raises(AccountRejected) as exc:
            asyncio.run(ytdlp.with_ydl("tiktok", _login_route("tiktok")))
        assert "SOCIALSCRAPE_COOKIES_TIKTOK" in exc.value.message and "fresh export" in exc.value.message
        assert str(path) not in exc.value.message
        # the env account's call (base_options adds the file), then one with no cookies at all ('' skips the file)
        assert calls == [("env", None), (None, ytdlp.NO_COOKIES)]
        account = sessions.accounts("tiktok", [])[0]
        assert not sessions.usable(account) and account.label.startswith("SOCIALSCRAPE_COOKIES_TIKTOK")
        # a replaced file is a new account version: it is used again at once
        path.write_text(TT_COOKIES + "# refreshed\n")
        os.utime(path, ns=(time.time_ns(), time.time_ns() + 10**9))
        assert sessions.usable(sessions.accounts("tiktok", [])[0])


def test_vanished_session_cookie_is_a_reason_to_check_not_a_verdict(monkeypatch, caplog):
    """_run: a session cookie that vanished during a call (the platform deleted it, or yt-dlp cleared it itself,
    as InstagramIE does after an HTTP error on the home page a restricted session is sent to) is reported to
    with_ydl on a platform with an account check. Only without one (YouTube) is it the verdict."""
    monkeypatch.setattr(sessions, "PROBES", {"instagram": lambda ydl: pytest.fail("_run runs no check")})
    account = Account("instagram", "entry1", "dashboard", IG_COOKIES)

    def answer_clears_session(ydl):
        assert sessions.has_session("instagram", ydl.cookiejar)
        ydl.cookiejar.clear(".instagram.com", "/", "sessionid")
        return "logged-out answer"

    report: dict = {}
    assert ytdlp._run("instagram", answer_clears_session, {"_dashboard_cookies": IG_COOKIES, "_account": account,
                                                            "_report": report}) == "logged-out answer"
    assert report == {"session_lost": True}
    assert sessions.usable(account) and sessions.health(account).status == "unchecked"

    yt_cookies = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\tx\n"
    youtube = Account("youtube", "entry2", "dashboard", yt_cookies)

    def youtube_clears_session(ydl):
        ydl.cookiejar.clear(".youtube.com", "/", "LOGIN_INFO")
        return "logged-out answer"

    with caplog.at_level(logging.WARNING, logger="socialscrape.sessions"):
        ytdlp._run("youtube", youtube_clears_session, {"_dashboard_cookies": yt_cookies, "_account": youtube,
                                                        "_report": {}})
    assert not sessions.usable(youtube) and "deleted the session cookie" in sessions.health(youtube).reason
    assert SECRET not in caplog.text


def test_with_ydl_checks_an_account_whose_session_cookie_vanished(monkeypatch):
    """The check decides: a restricted session (yt-dlp cleared its cookie after Instagram's 429 on the home page)
    stays usable and the call goes on without cookies; a working verdict older than a minute is checked again."""
    calls, probes = [], []
    verdicts = {"acct": Verdict("alive", "restricted", "sample", limited=True)}
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: ([], [("acct", IG_COOKIES)]))
    monkeypatch.setattr(sessions, "PROBES", {"instagram": lambda ydl: probes.append(1) or verdicts["acct"]})

    def run(platform, fn, opts):
        account = opts.get("_account")
        if getattr(fn, "__qualname__", "").startswith("verify"):
            return fn(SimpleNamespace(_socialscrape_account=account))
        calls.append(account.key if account else None)
        if account is not None:
            opts["_report"]["session_lost"] = True  # what _run reports
            raise UpstreamBlocked("Instagram rate-limited the request (HTTP 429); try again later")
        return "public post"

    monkeypatch.setattr(ytdlp, "_run", run)
    assert asyncio.run(ytdlp.with_ydl("instagram", lambda ydl: None)) == "public post"
    assert calls == ["acct", None] and len(probes) == 1
    assert sessions.usable(sessions.accounts("instagram", [("acct", IG_COOKIES)])[0])

    verdicts["acct"] = Verdict("alive", account="sample")
    account = sessions.accounts("instagram", [("acct", IG_COOKIES)])[0]
    sessions.record(account, verdicts["acct"], now=time.time() - sessions.UNKNOWN_SECONDS - 1)
    calls.clear()
    verdicts["acct"] = Verdict("rejected", "Instagram ended the session (its answer deleted the session cookie)")
    assert asyncio.run(ytdlp.with_ydl("instagram", lambda ydl: None)) == "public post"
    assert calls == ["acct", None] and len(probes) == 2 and not sessions.usable(account)


def test_a_call_that_succeeds_with_a_vanished_session_cookie_checks_the_account_in_the_background(monkeypatch):
    probes = []
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: ([], [("acct", IG_COOKIES)]))
    monkeypatch.setattr(sessions, "PROBES", {"instagram": lambda ydl: probes.append(1) or Verdict(
        "rejected", "Instagram ended the session (its answer deleted the session cookie)")})

    def run(platform, fn, opts):
        if getattr(fn, "__qualname__", "").startswith("verify"):
            return fn(SimpleNamespace(_socialscrape_account=opts.get("_account")))
        opts["_report"]["session_lost"] = True
        return "logged-out answer"

    monkeypatch.setattr(ytdlp, "_run", run)

    async def call_then_wait():
        result = await ytdlp.with_ydl("instagram", lambda ydl: None)
        assert probes == []  # the call isn't delayed by the check
        await asyncio.gather(*sessions._background)
        return result

    assert asyncio.run(call_then_wait()) == "logged-out answer"
    assert probes == [1] and not sessions.usable(sessions.accounts("instagram", [("acct", IG_COOKIES)])[0])


def test_saved_health_follows_the_latest_verdict_and_forget(monkeypatch):
    """Saving is serialized and writes the entry's state at that moment: a slow save of an older verdict can't
    land after a newer one, and a verdict finishing after the entry was removed brings back no row."""
    import threading

    saved: dict = {}

    def persist(platform, entry_id, data):
        if data is not None and data["status"] == "rejected":
            time.sleep(0.2)  # this writer is slow (the portal database is busy)
        if data is None:
            saved.pop((platform, entry_id), None)
        else:
            saved[(platform, entry_id)] = data["status"]

    sessions.attach([], persist)
    try:
        account = Account("tiktok", "entry1", "dashboard", "x")
        slow = threading.Thread(target=sessions.record, args=(account, Verdict("rejected", "expired")))
        slow.start()
        time.sleep(0.05)
        sessions.record(account, Verdict("alive"))  # the owner's Check, a moment later
        slow.join()
        assert sessions.health(account).status == "alive" and saved[("tiktok", "entry1")] == "alive"

        other = Account("tiktok", "entry2", "dashboard", "x")
        late = threading.Thread(target=sessions.record, args=(other, Verdict("rejected", "expired")))
        late.start()
        time.sleep(0.05)
        sessions.forget("tiktok", "entry2")  # removed while its check was finishing
        late.join()
        sessions.record(other, Verdict("rejected", "expired"))  # a check that ends even later
        assert ("tiktok", "entry2") not in saved
        assert sessions.entry_health("tiktok", "entry2")["status"] == "unchecked"
    finally:
        sessions.detach()


def test_embed_page_is_read_without_account_cookies(monkeypatch):
    """Instagram renders the visitor's csrftoken (and ds_user_id, ig_did, mid) into the embed page, which the route
    returns verbatim: the server account's cookies must never be sent for it."""
    import dataclasses

    from yt_dlp.extractor.instagram import InstagramUserIE

    from socialscrape.config import get_settings

    csrf = "SECRET-CSRF-VALUE"
    jar = IG_COOKIES + f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tcsrftoken\t{csrf}\n"
    settings = dataclasses.replace(get_settings(), cookie_files={"instagram": "/file-that-must-not-be-opened"},
                                   cookies_from_browser="chrome")
    monkeypatch.setattr(ytdlp, "get_settings", lambda: settings)
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: (["http://proxy.example:8080"],
                                                                          [("entry1", jar)]))
    seen = []

    def embed_page(self, url, video_id, **kwargs):
        cookies = self._get_cookies(ig.WEB)
        seen.append((url, sorted(cookies), self._downloader.params.get("cookiefile"),
                     self._downloader.params.get("cookiesfrombrowser"), self._downloader.params.get("proxy")))
        token = cookies["csrftoken"].value if "csrftoken" in cookies else "anonymous-token"
        html = ('<div class="Embed" data-media-type="Profile"></div>"require":[["PolarisEmbedSimple","init",[],'
                f'[{{"isProfileEmbed":true}}]]]{{"csrf_token":"{token}"}}')
        return html, SimpleNamespace(url=url, status=200)

    monkeypatch.setattr(InstagramUserIE, "_download_webpage_handle", embed_page)
    result = asyncio.run(ig.instagram_user_embed("nasa"))
    assert csrf not in result["html"] and SECRET not in result["html"]
    assert seen == [(f"{ig.WEB}/nasa/embed/", [], None, None, "http://proxy.example:8080")]


def test_cookieless_calls_carry_no_account_but_report_an_unusable_cookie_file(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: ([], [("entry1", IG_COOKIES)]))
    monkeypatch.setattr(ytdlp, "_run", lambda platform, fn, opts: calls.append(dict(opts)) or "page")
    assert asyncio.run(ytdlp.with_ydl("instagram", lambda ydl: None, use_cookies=False)) == "page"
    assert calls == [{"_dashboard_cookies": ytdlp.NO_COOKIES, "cookiefile": None, "cookiesfrombrowser": None}]
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: ([], []))
    with patched_env({"SOCIALSCRAPE_COOKIES_INSTAGRAM": str(tmp_path / "missing.txt")}):
        with pytest.raises(Exception) as exc:
            asyncio.run(ytdlp.with_ydl("instagram", lambda ydl: None, use_cookies=False))
        assert getattr(exc.value, "code", None) == "cookies_error"


def test_topic_pages_are_read_without_account_cookies(monkeypatch):
    """A signed-in session asking /explore/tags/<tag>/ is sent to the signed-in keyword search, which carries none
    of the topic's data (recorded with a flagged session; a working one is served the same client)."""
    seen = []

    async def fetch_page(ie_name, url, item_id, what, *, use_cookies=True):
        seen.append((url, use_cookies))
        return '{"pageID":"popularSearchPage"}', [{"data": {"xig_logged_out_popular_search_media_info": {}}}]

    monkeypatch.setattr(ig, "fetch_page", fetch_page)
    monkeypatch.setattr(ig, "_TOPIC_KEYS", ("xig_logged_out_popular_search_media_info",))
    asyncio.run(ig.fetch_topic("cats"))
    assert seen == [(f"{ig.WEB}/explore/tags/cats/", False)]


def test_logged_out_page_served_to_a_session_is_a_session_signal():
    profile = json.loads((FIXTURES / "instagram" / "profile_page.json").read_text())
    blob = json.dumps({"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"require": [
        ["RelayPrefetchedStreamCache", "next", [], ["adp", {"__bbox": {"result": profile["relay"][0]}}]]]}}]]]})
    html = (f'<script type="application/json">{{"pageID":"profilePage"}}</script><script>["LSD",[],'
            f'{{"token":"AVq"}},1]</script><script type="application/json" data-sjs>{blob}</script>')
    assert ig.served_logged_out(html) and not ig.served_logged_out(SIGNED_IN_HOME)
    with pytest.raises(SessionInvalid, match="logged-out page"):
        instagram_authenticated.Session(SimpleNamespace(), html, f"{ig.WEB}/jane/")
    with pytest.raises(UpstreamBlocked, match="request tokens"):  # a shell without tokens: not a session verdict
        instagram_authenticated.Session(SimpleNamespace(), '{"pageID":"feedPage"}["LSD",[],{"token":"A"}', ig.WEB)


def test_app_api_answers_of_a_dead_session_are_not_verdicts():
    """Recorded: each is what no cookies get too. They stay the route's own errors; only the passport check (made
    before the call, tiktok_private._signed_in) decides an account's health."""
    from socialscrape.platforms import tiktok_private

    for endpoint, answer in APP_ANSWERS.items():
        if endpoint.startswith("_") or not answer.get("status_code"):
            continue
        ie = SimpleNamespace(_get_cookies=lambda url: {"sessionid": _Cookie(value=SECRET)},
                             _call_api=lambda *args, answer=answer, **kwargs: answer)
        ydl = SimpleNamespace(get_info_extractor=lambda name, ie=ie: ie, _socialscrape_account=None)
        with pytest.raises(UpstreamError) as exc:
            tiktok_private._app(endpoint, "1", {})(ydl)
        assert not isinstance(exc.value, SessionInvalid)
        assert exc.value.message.endswith(answer.get("status_msg") or answer.get("message") or str(
            answer["status_code"]))


# --- the dashboard: health next to each entry, Check, the check after adding ------------------------------------

def _owner_client(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_SETUP_TOKEN", "setup")
    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_PATH", str(tmp_path / "portal.sqlite3"))
    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "")
    from socialscrape.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(ytdlp, "_executor", None)
    owner = TestClient(create_app(), base_url="https://testserver", raise_server_exceptions=False)
    owner.post("/setup", data={"email": "owner@example.com", "password": "correct horse battery",
                                "setup_token": "setup"})
    return owner, {"x-csrf-token": owner.get("/portal/api/me").json()["csrf"]}


def test_dashboard_checks_accounts_and_keeps_their_health(tmp_path, monkeypatch):
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    owner, headers = _owner_client(tmp_path, monkeypatch)
    verdicts = {"tiktok": Verdict("rejected", "TikTok says the session expired (session expired, please sign in "
                                              "again)")}
    checked = []

    def probe(ydl):
        checked.append(sessions.has_session("tiktok", ydl.cookiejar))  # the entry's own cookies, loaded by _run
        return verdicts["tiktok"]

    monkeypatch.setattr(sessions, "PROBES", {"tiktok": probe})
    added = owner.post("/portal/api/settings/platforms/tiktok/cookies", headers=headers, json={"value": TT_COOKIES})
    assert added.status_code == 200, added.text
    body = added.json()
    entry = body["id"]
    assert body["check"]["status"] == "rejected" and body["check"]["ok"] is False and checked == [True]
    assert SECRET not in added.text
    status = owner.get("/portal/api/settings/platforms").json()["tiktok"]["cookies"]
    assert len(status) == 1 and status[0]["id"] == entry  # kept, whatever the check said
    assert status[0]["health"]["status"] == "rejected" and "session expired" in status[0]["health"]["reason"]

    verdicts["tiktok"] = Verdict("alive", account="sample.account")
    url = f"/portal/api/settings/platforms/tiktok/cookies/{entry}/check"
    assert owner.post(url).status_code == 403  # CSRF
    checked_now = owner.post(url, headers=headers).json()
    assert {k: checked_now[k] for k in ("ok", "status", "account", "supported")} == {
        "ok": True, "status": "alive", "account": "sample.account", "supported": True}
    assert checked_now["health"]["retry_at"] is None  # a passing check ends the cooldown
    assert owner.post("/portal/api/settings/platforms/tiktok/cookies/nope/check", headers=headers).status_code == 404
    monkeypatch.setattr(sessions, "PROBES", {"tiktok": probe, "instagram": lambda ydl: Verdict(
        "alive", "its account API sends the session to the home page", "sample.account", limited=True)})
    flagged = owner.post("/portal/api/settings/platforms/instagram/cookies", headers=headers,
                         json={"value": IG_COOKIES}).json()
    assert {k: flagged["check"][k] for k in ("ok", "status", "limited")} == {"ok": None, "status": "limited",
                                                                           "limited": True}
    assert flagged["check"]["health"]["status"] == "alive" and flagged["check"]["health"]["limited"] is True
    again_flagged = owner.post(f"/portal/api/settings/platforms/instagram/cookies/{flagged['id']}/check",
                               headers=headers).json()
    assert (again_flagged["ok"], again_flagged["status"]) == (None, "limited")
    unsupported = owner.post("/portal/api/settings/platforms/youtube/cookies", headers=headers,
                             json={"value": TT_COOKIES.replace("tiktok.com", "youtube.com")}).json()
    assert unsupported["check"]["supported"] is False and unsupported["check"]["status"] == "unknown"

    # the health is saved next to the encrypted pool (no secrets) and read back by a new app
    raw = (tmp_path / "portal.sqlite3").read_bytes()
    assert SECRET.encode() not in raw
    sessions.reset()
    get_settings.cache_clear()
    again = TestClient(create_app(), base_url="https://testserver")
    again.post("/login", data={"email": "owner@example.com", "password": "correct horse battery"})
    health = again.get("/portal/api/settings/platforms").json()["tiktok"]["cookies"][0]["health"]
    assert (health["status"], health["account"]) == ("alive", "sample.account")
    csrf = {"x-csrf-token": again.get("/portal/api/me").json()["csrf"]}
    assert again.delete(f"/portal/api/settings/platforms/tiktok/cookies/{entry}", headers=csrf).json() == {
        "removed": True}
    assert sessions.entry_health("tiktok", entry)["status"] == "unchecked"
    import sqlite3
    rows = sqlite3.connect(tmp_path / "portal.sqlite3").execute(
        "SELECT entry_id FROM platform_account_health WHERE platform='tiktok'").fetchall()
    assert rows == []

    script = again.get("/assets/portal.js").text
    assert "data-check-account" in script and "Account rejected" in script and "accountCheckToast" in script



def _account_views(cases: dict) -> dict:
    """Run the dashboard's account-health helpers (portal.js) in Node on `cases` -> their output, by case name."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    source = (Path(__file__).parents[1] / "socialscrape" / "static" / "portal.js").read_text()
    helpers = [line for line in source.splitlines()
               if line.startswith(("const esc =", "const dateFormatter =", "const fmt =", "function platformName("))]
    block = source[source.index("function poolPosition("):source.index("function poolList(")]
    script = f"""const vm=require('vm'), context=vm.createContext({{}});
vm.runInContext({json.dumps(chr(10).join(helpers) + chr(10) + block)}, context);
context.cases={json.dumps(cases)};
console.log(vm.runInContext(`JSON.stringify(Object.fromEntries(Object.entries(cases).map(([name, c]) => [name,
  c.toast ? accountCheckToast(c.platform, c.position, c.result, c.saved)
          : {{...accountHealth(c.platform, c.health),
              summary: accountSummary(c.platform, {{position: 1, updated_at: 1761440000, health: c.health}})}}])))`,
  context));"""
    return json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout)


def test_dashboard_names_each_account_health_in_words():
    """The manager dialog's badge and lines say only what the server recorded, in words (not colour alone)."""
    now, later, earlier = time.time(), time.time() + 6 * 3600, time.time() - 3600
    expired = "TikTok says the session expired (session expired, please sign in again)"
    rejected = {"status": "rejected", "reason": expired, "checked_at": now, "rejected_at": now, "retry_at": later}
    views = _account_views({
        "unchecked": {"platform": "tiktok", "health": {"status": "unchecked"}},
        "no_probe": {"platform": "youtube", "health": {"status": "unchecked"}},
        "working": {"platform": "tiktok", "health": {"status": "alive", "account": "sample.account",
                                                     "checked_at": now}},
        "restricted": {"platform": "instagram", "health": {"status": "alive", "limited": True, "checked_at": now,
                                                           "reason": "its account API sends the session home"}},
        "rejected": {"platform": "tiktok", "health": rejected},
        "no_session": {"platform": "instagram", "health": {
            "status": "rejected", "reason": "its cookies hold no Instagram session cookie (sessionid)",
            "checked_at": now, "rejected_at": now, "retry_at": later}},
        "cooled": {"platform": "tiktok", "health": {**rejected, "rejected_at": earlier - 7 * 3600,
                                                    "retry_at": earlier}},
        "inconclusive": {"platform": "instagram", "health": {
            "status": "unchecked", "inconclusive_at": now,
            "inconclusive_reason": "The account check could not reach Instagram (ProxyError)"}},
        "rejected_then_inconclusive": {"platform": "tiktok", "health": {
            **rejected, "inconclusive_at": now, "inconclusive_reason": "TikTok answered with HTTP 503"}},
        "escaped": {"platform": "instagram", "health": {"status": "rejected", "reason": "Instagram said <b>no</b>",
                                                        "checked_at": now, "rejected_at": now, "retry_at": later}},
        "toast_saved_rejected": {"toast": True, "platform": "tiktok", "position": 1, "saved": True,
                                 "result": {"status": "rejected", "ok": False, "reason": expired,
                                            "supported": True, "health": rejected}},
        "toast_checked_no_session": {"toast": True, "platform": "instagram", "position": 2, "result": {
            "status": "rejected", "ok": False, "reason": "its cookies hold no Instagram session cookie (sessionid)",
            "supported": True, "health": {}}},
        "toast_working": {"toast": True, "platform": "tiktok", "position": 1, "result": {
            "status": "alive", "ok": True, "account": "sample.account", "supported": True, "health": {}}},
        "toast_inconclusive": {"toast": True, "platform": "instagram", "position": 1, "result": {
            "status": "unknown", "ok": None, "reason": "The account check timed out", "supported": True}},
        "toast_saved_no_probe": {"toast": True, "platform": "youtube", "position": 1, "saved": True, "result": {
            "status": "unknown", "ok": None, "supported": False}},
        "toast_saved_unreachable": {"toast": True, "platform": "instagram", "position": 1, "saved": True, "result": {
            "status": "unknown", "ok": None, "supported": True,
            "reason": "The account check could not reach Instagram (ExtractorError)"}},
        "toast_limited": {"toast": True, "platform": "instagram", "position": 1, "result": {
            "status": "limited", "ok": None, "limited": True, "supported": True,
            "reason": "its account API sends the session home"}},
    })
    assert (views["unchecked"]["badge"], views["unchecked"]["tone"], views["unchecked"]["checked"]) == (
        "Not checked yet", "", None)
    assert "Last checked" not in views["unchecked"]["summary"]
    assert views["no_probe"]["badge"] == "No account check" and "cannot check YouTube" in views["no_probe"]["lines"][0]
    assert (views["working"]["badge"], views["working"]["tone"]) == ("Working", "good")
    assert views["working"]["lines"] == ["Signed in as @sample.account."]
    assert "Last checked" in views["working"]["summary"]
    assert (views["restricted"]["badge"], views["restricted"]["tone"]) == ("Restricted by Instagram", "warn")
    assert views["restricted"]["lines"] == ["Instagram restricts this account: its account API sends the session home."]

    bad = views["rejected"]
    assert bad["tone"] == "bad" and bad["badge"].startswith("Rejected by TikTok · <time datetime=")
    assert bad["lines"][0] == f"{expired}." and bad["lines"][1].startswith("Requests skip this account until ")
    assert 'data-account-badge>Rejected by TikTok · <time' in bad["summary"]
    # SocialScrape's own finding (no session cookie in the file) is not put in the platform's mouth
    assert views["no_session"]["badge"].startswith("Rejected · <time")
    assert views["no_session"]["lines"][0] == "Its cookies hold no Instagram session cookie (sessionid)."
    assert views["cooled"]["lines"][1].startswith("Its pause has ended")
    assert views["inconclusive"]["badge"] == "Check inconclusive" and views["inconclusive"]["tone"] == ""
    assert views["inconclusive"]["lines"][0] == "The account check could not reach Instagram (ProxyError)."
    both = views["rejected_then_inconclusive"]
    assert both["badge"].startswith("Rejected by TikTok")
    assert both["lines"][-1].endswith("was inconclusive. TikTok answered with HTTP 503. The verdict above still stands.")
    assert "<b>" not in views["escaped"]["summary"] and "&lt;b&gt;no&lt;/b&gt;" in views["escaped"]["summary"]

    message, kind, persistent = views["toast_saved_rejected"]
    assert message.startswith(f"Saved, but TikTok rejected the Primary account: {expired}. Requests skip it until ")
    assert (kind, persistent) == ("warning", True) and message.endswith("Replace it with a fresh cookies.txt export.")
    message, kind, _ = views["toast_checked_no_session"]
    assert message.startswith("The Instagram Backup 1 account was rejected: its cookies hold no") and kind == "warning"
    assert views["toast_working"] == ["The TikTok Primary account works · signed in as @sample.account.", "success",
                                      False]
    assert views["toast_inconclusive"] == [
        "The check of the Instagram Primary account was inconclusive. The account check timed out.", "warning", False]
    assert views["toast_saved_unreachable"][0] == (
        "Signed-in account saved for Instagram. The check of the Instagram Primary account was inconclusive. "
        "The account check could not reach Instagram (ExtractorError).")
    assert views["toast_limited"] == [
        "Instagram restricts the Primary account: its account API sends the session home.", "warning", False]
    assert views["toast_saved_no_probe"] == [
        "Signed-in account saved for YouTube. SocialScrape cannot check YouTube sessions.", "success", False]

def test_health_reports_the_env_account_verdict_without_its_path(tmp_path):
    path = tmp_path / "ig.txt"
    path.write_text(IG_COOKIES)
    with app_with({"SOCIALSCRAPE_COOKIES_INSTAGRAM": str(path)}) as c:
        assert c.get("/health").json()["cookie_health"] == {}  # /health never checks an account itself
        sessions.record(sessions.accounts("instagram", [])[0], Verdict("rejected", "dead session"))
        h = c.get("/health").json()["cookie_health"]
        assert h["instagram"]["status"] == "rejected" and h["instagram"]["source"] == "env"
        assert str(path) not in json.dumps(h) and SECRET not in json.dumps(h)


def test_tiktok_routes_name_the_rejected_env_account(tmp_path, monkeypatch):
    """The real _run and route code: the env file's session fails TikTok's check (stand-in) before the app call."""
    path = tmp_path / "tt.txt"
    path.write_text(TT_COOKIES)
    monkeypatch.setattr(sessions, "PROBES", {"tiktok": lambda ydl: Verdict(
        "rejected", "TikTok says the session expired (session expired, please sign in again)")})
    with app_with({"SOCIALSCRAPE_COOKIES_TIKTOK": str(path)}) as c:
        for route, params in (("/v1/tiktok/video/comments",
                               {"url": "https://www.tiktok.com/@a/video/7499229683859426602"}),
                              ("/v1/tiktok/search/suggestions", {"query": "travel"}),
                              ("/v1/tiktok/user/following", {"handle": "a"})):
            r = c.get(route, params=params, headers={"x-socialscrape-fallback": "off"})
            body = r.json()
            assert (r.status_code, body["error"], body["reason"]) == (501, "platform_auth_required",
                                                                     "account_rejected"), (route, body)
            assert "SOCIALSCRAPE_COOKIES_TIKTOK" in body["message"] and "session expired" in body["message"]
