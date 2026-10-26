"""Health of the signed-in accounts (cookies.txt sessions) platform calls carry.

A platform can stop honouring a session at any time: it expires, the account is logged out elsewhere, or the
platform flags the account. Nothing in the cookies says so, and the answers a dead session gets are rarely an
error that names it: Instagram serves its logged-out page or clears the session cookie, TikTok's app API answers
"Please upgrade your TikTok app." or an empty list, exactly as it does with no cookies at all. A dead account must
never make a route worse than having no account, so with_ydl (ytdlp.py) asks this module:

  - which accounts a call may carry (accounts(), usable()): dashboard entries in pool order, else the env cookie
    file (SOCIALSCRAPE_COOKIES_<PLATFORM>), else browser cookies; a rejected account is skipped until its cooldown
    ends;
  - whether an account that just failed is dead (verify()): the platform's account check (PROBES), run with the
    account's own cookies and proxy, cached per account;
  - what a route that needs a login answers once no account is usable (login_error()): a 501 setup error naming
    the account and where to replace it.

Platform code reports what it sees: SessionInvalid for an answer that shows the session is not accepted, confirm()
before a login-only call whose failures would not tell (TikTok's app API). _run reports a session cookie the
platform cleared in its answer (lost()).

Health lives here, in memory (it covers the env file and API-only servers too). Dashboard entries' health is also
saved next to the encrypted pool, in the portal database (portal.PortalStore), so it survives restarts and the
dashboard can show it. Cookie values never leave the Account; logs name the platform and the entry id (or the
environment variable), never a value.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import importlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal

from .config import get_settings
from .errors import AccountRejected, ApiError, SessionInvalid

log = logging.getLogger("socialscrape.sessions")

Status = Literal["alive", "rejected", "unknown"]

# The account check of each platform that has one: a function of a YoutubeDL (carrying the account's cookies and
# proxy) returning a Verdict, named "<module>:<function>" under socialscrape.platforms (imported on first use).
PROBES: dict[str, Any] = {"instagram": "instagram:account_check", "tiktok": "tiktok:account_check"}
# The cookies that make a jar signed in, per platform (the URL they are sent to, any one of the names). A platform
# that deletes all of them in an answer has ended the session (_run reports it as lost()).
SESSION_COOKIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "instagram": ("https://www.instagram.com/", ("sessionid",)),
    "tiktok": ("https://www.tiktok.com/", ("sessionid", "sessionid_ss", "sid_tt")),
    "youtube": ("https://www.youtube.com/", ("LOGIN_INFO",)),  # what yt-dlp's own "no longer valid" check reads
    "facebook": ("https://www.facebook.com/", ("c_user",)),
}
ALIVE_SECONDS = 600.0  # a working verdict is reused this long before the account is checked again
UNKNOWN_SECONDS = 60.0  # an inconclusive check (throttled, unreachable) is not repeated sooner than this
COOLDOWNS = (6 * 3600.0, 24 * 3600.0)  # a rejected account is skipped this long: first rejection, then each later one
CHECK_TIMEOUT = 25.0  # seconds one account check may take, in traffic or from the dashboard
NAMES = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube", "facebook": "Facebook",
         "google": "Google"}


@dataclass(frozen=True)
class Account:
    """One signed-in account a call can carry. `key` identifies it for health: the dashboard entry id, or for the
    env file its version (a replaced file starts fresh), or the browser spec."""

    platform: str
    key: str
    source: Literal["dashboard", "env", "browser"]
    value: str | None = field(default=None, repr=False, compare=False)  # dashboard cookies.txt; env/browser: None
    position: int = 1

    @property
    def label(self) -> str:
        """How logs and messages name the account: never by a cookie value."""
        if self.source == "dashboard":
            return f"dashboard entry {self.key}"
        if self.source == "env":
            return f"SOCIALSCRAPE_COOKIES_{self.platform.upper()} (env file)"
        return "SOCIALSCRAPE_COOKIES_FROM_BROWSER"


@dataclass(frozen=True)
class Verdict:
    """What an account check found. `account` is the platform's name for the signed-in user when it said;
    `limited` marks a session that is signed in but that the platform restricts (some signed-in calls refused)."""

    status: Status
    reason: str | None = None
    account: str | None = None
    limited: bool = False


@dataclass
class Health:
    status: Literal["unchecked", "alive", "rejected"] = "unchecked"
    reason: str | None = None
    account: str | None = None
    limited: bool = False
    checked_at: float | None = None  # the last conclusive verdict
    rejected_at: float | None = None
    until: float | None = None  # skipped until then (rejected)
    strikes: int = 0  # rejections in a row
    inconclusive_at: float | None = None
    inconclusive_reason: str | None = None

    def public(self) -> dict[str, Any]:
        """What the dashboard and /health show (no cookie value is ever part of Health)."""
        return {"status": self.status, "reason": self.reason, "account": self.account, "limited": self.limited,
                "checked_at": self.checked_at, "rejected_at": self.rejected_at, "retry_at": self.until,
                "inconclusive_at": self.inconclusive_at, "inconclusive_reason": self.inconclusive_reason}


_health: dict[tuple[str, str], Health] = {}
_lock = threading.Lock()
_check_locks: dict[tuple[str, str], threading.Lock] = {}
# Saves a dashboard entry's health (None: forget it); set by portal.install_portal.
_persist: Callable[[str, str, dict[str, Any] | None], None] | None = None


def reset() -> None:
    """Forget every account's health (tests; a new portal)."""
    with _lock:
        _health.clear()
        _check_locks.clear()


def attach(rows: Iterable[tuple[str, str, dict[str, Any]]], persist: Callable[[str, str, dict[str, Any] | None],
                                                                                None] | None) -> None:
    """Load the saved health of dashboard entries and save later changes with `persist`."""
    global _persist
    names = {f.name for f in dataclasses.fields(Health)}
    with _lock:
        for platform, entry_id, data in rows:
            _health[(platform, entry_id)] = Health(**{k: v for k, v in data.items() if k in names})
        _persist = persist


def detach() -> None:
    global _persist
    _persist = None


# --- which accounts a call carries -----------------------------------------------------------------------------

def accounts(platform: str, entries: list[tuple[str, str]]) -> list[Account]:
    """The accounts configured for `platform`, in the order with_ydl tries them: the dashboard pool's entries
    ((id, cookies.txt) pairs) replace the env file, which replaces browser cookies (base_options' precedence)."""
    if entries:
        return [Account(platform, entry_id, "dashboard", value, position)
                for position, (entry_id, value) in enumerate(entries, 1)]
    settings = get_settings()
    if path := settings.cookie_file(platform):
        try:
            st = os.stat(path)
            version = f"{st.st_mtime_ns}-{st.st_size}"
        except OSError:  # base_options reports why it can't be used (500 cookies_error)
            version = "missing"
        return [Account(platform, f"env@{version}", "env")]
    if settings.cookies_from_browser:
        return [Account(platform, f"browser:{settings.cookies_from_browser}", "browser")]
    return []


def health(account: Account) -> Health:
    with _lock:
        return dataclasses.replace(_health.get((account.platform, account.key)) or Health())


def entry_health(platform: str, entry_id: str) -> dict[str, Any]:
    with _lock:
        return (_health.get((platform, entry_id)) or Health()).public()


def usable(account: Account, now: float | None = None) -> bool:
    """False while a rejected account cools down."""
    h = health(account)
    return not (h.status == "rejected" and h.until is not None and (now or time.time()) < h.until)


def has_session(platform: str, jar: Any) -> bool:
    """Whether a cookie jar holds `platform`'s session cookie."""
    if platform not in SESSION_COOKIES:
        return False
    url, names = SESSION_COOKIES[platform]
    try:
        return any(cookie.name in names and cookie.value for cookie in jar.get_cookies_for_url(url))
    except Exception:  # a stand-in jar (tests) or one yt-dlp can't read: no evidence either way
        return False


# --- verdicts --------------------------------------------------------------------------------------------------

def _fresh(h: Health, now: float) -> Verdict | None:
    if h.status == "rejected" and h.until is not None and now < h.until:
        return Verdict("rejected", h.reason, h.account)
    if h.status == "alive" and h.checked_at is not None and now - h.checked_at < ALIVE_SECONDS:
        return Verdict("alive", h.reason, h.account, h.limited)
    if h.inconclusive_at is not None and now - h.inconclusive_at < UNKNOWN_SECONDS:
        return Verdict("unknown", h.inconclusive_reason)
    return None


def cached(account: Account) -> Verdict | None:
    with _lock:
        return _fresh(_health.get((account.platform, account.key)) or Health(), time.time())


def record(account: Account, verdict: Verdict, now: float | None = None) -> Health:
    """Keep `verdict` as the account's health: alive clears a cooldown, rejected starts (or extends) one, unknown
    keeps the previous verdict. Changes are logged, a rejection at WARNING (platform and entry id only)."""
    now = time.time() if now is None else now
    name = NAMES.get(account.platform, account.platform)
    with _lock:
        h = _health.setdefault((account.platform, account.key), Health())
        before = h.status
        if verdict.status == "alive":
            h.status, h.reason, h.limited = "alive", verdict.reason, verdict.limited
            h.account = verdict.account or h.account
            h.checked_at, h.until, h.strikes = now, None, 0
            h.inconclusive_at = h.inconclusive_reason = None
        elif verdict.status == "rejected":
            cooling = h.status == "rejected" and h.until is not None and now < h.until
            if not cooling:  # a new rejection (not the same one reported again by a parallel call)
                h.strikes += 1
                h.until = now + COOLDOWNS[min(h.strikes, len(COOLDOWNS)) - 1]
                h.rejected_at = now
                log.warning("%s account %s rejected: %s; it is skipped for %d h", account.platform, account.label,
                            verdict.reason, round((h.until - now) / 3600))
            h.status, h.reason, h.limited = "rejected", verdict.reason, False
            h.account = verdict.account or h.account
            h.checked_at = now
            h.inconclusive_at = h.inconclusive_reason = None
        else:
            h.inconclusive_at, h.inconclusive_reason = now, verdict.reason
        snapshot = dataclasses.replace(h)
    if before == "rejected" and snapshot.status == "alive":
        log.warning("%s account %s works again", account.platform, account.label)
    elif before != snapshot.status and snapshot.status == "alive":
        log.info("%s account %s checked: %s%s", account.platform, account.label, "signed in",
                 f" ({name} restricts it: {snapshot.reason})" if snapshot.limited else "")
    if account.source == "dashboard" and _persist is not None:
        try:
            _persist(account.platform, account.key, dataclasses.asdict(snapshot))
        except Exception:  # the portal database is unavailable: the verdict still holds in memory
            log.exception("could not save the health of %s account %s", account.platform, account.label)
    return snapshot


def forget(platform: str, entry_id: str) -> None:
    """A removed dashboard entry's health."""
    with _lock:
        _health.pop((platform, entry_id), None)
    if _persist is not None:
        _persist(platform, entry_id, None)


def lost(account: Account) -> None:
    """The platform deleted the session cookie in one of its answers: it ended the session."""
    record(account, Verdict("rejected", f"{NAMES.get(account.platform, account.platform)} deleted the session "
                                        "cookie in its answer: the session expired or was logged out"))


def _probe_for(platform: str) -> Callable[[Any], Verdict] | None:
    probe = PROBES.get(platform)
    if probe is None or callable(probe):
        return probe
    module, _, name = probe.partition(":")
    return getattr(importlib.import_module(f".platforms.{module}", __package__), name)


def check_now(ydl: Any, account: Account, *, force: bool = False) -> Verdict:
    """The account's verdict, checked with `ydl` (its cookies and proxy) unless a fresh one is kept. One check at a
    time per account: a call arriving meanwhile waits and reuses its verdict."""
    probe = _probe_for(account.platform)
    if probe is None:
        return Verdict("unknown", f"No account check is available for {NAMES.get(account.platform, account.platform)}")
    with _lock:
        guard = _check_locks.setdefault((account.platform, account.key), threading.Lock())
    with guard:
        if not force and (kept := cached(account)) is not None:
            return kept
        try:
            verdict = probe(ydl)
        except Exception as exc:  # an unreachable platform or proxy: says nothing about the account
            log.info("%s account %s check failed: %s", account.platform, account.label, type(exc).__name__)
            verdict = Verdict("unknown", f"The account check could not reach "
                                         f"{NAMES.get(account.platform, account.platform)} ({type(exc).__name__})")
        record(account, verdict)
        return verdict


def confirm(ydl: Any, platform: str) -> None:
    """For a call that needs a signed-in account and whose failures wouldn't show a dead session: SessionInvalid
    unless the account the call carries (bound by _run) passes its check (cached ALIVE_SECONDS)."""
    account = getattr(ydl, "_socialscrape_account", None)
    if not isinstance(account, Account) or _probe_for(platform) is None:
        return
    verdict = check_now(ydl, account)
    if verdict.status == "rejected":
        raise SessionInvalid(platform, verdict.reason or "the account check rejected the session")


async def verify(platform: str, account: Account, proxy: str | None, *, force: bool = False) -> Verdict | None:
    """The account's verdict after a call carrying it failed (None: the platform has no account check). The check
    runs with the account's cookies through `proxy`, as the call did."""
    from . import ytdlp

    if _probe_for(platform) is None:
        return None
    if not force and (kept := cached(account)) is not None:
        return kept
    options: dict[str, Any] = {"_account": account}
    if proxy is not None:
        options["_dashboard_proxy"] = proxy
    if account.value is not None:
        options["_dashboard_cookies"] = account.value
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(
            ytdlp._pool(), ytdlp._run, platform, lambda ydl: check_now(ydl, account, force=force), options),
            timeout=CHECK_TIMEOUT)
    except Exception as exc:  # a check never fails the call it was made for
        reason = ("The account check timed out" if isinstance(exc, asyncio.TimeoutError)
                  else getattr(exc, "message", None) or type(exc).__name__)
        verdict = Verdict("unknown", reason)
        record(account, verdict)
        return verdict


async def check(platform: str, account: Account, proxy: str | None) -> dict[str, Any]:
    """The dashboard's Check: a fresh verdict for one entry -> {ok, status, reason, checked_at, account, limited,
    latency_ms, health}. ok is null when the check was inconclusive or the platform has none."""
    started = time.monotonic()
    verdict = await verify(platform, account, proxy, force=True)
    if verdict is None:
        verdict = Verdict("unknown", f"No account check is available for {NAMES.get(platform, platform)}")
    return {"ok": {"alive": True, "rejected": False}.get(verdict.status), "status": verdict.status,
            "reason": verdict.reason, "account": verdict.account, "limited": verdict.limited,
            "checked_at": time.time(), "latency_ms": round((time.monotonic() - started) * 1000),
            "supported": _probe_for(platform) is not None, "health": health(account).public()}


# --- what a login-only route answers -----------------------------------------------------------------------------

def _when(timestamp: float | None) -> str:
    if not timestamp:
        return ""
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime(" since %Y-%m-%d %H:%M UTC")


def _replace_hint(account: Account) -> str:
    if account.source == "dashboard":
        return "Replace it in Settings → Proxies & accounts"
    if account.source == "env":
        return (f"Replace the cookies.txt file SOCIALSCRAPE_COOKIES_{account.platform.upper()} names with a fresh "
                "export from a signed-in browser")
    return "Sign in again in the browser SOCIALSCRAPE_COOKIES_FROM_BROWSER names"


def _named(account: Account) -> str:
    name = NAMES.get(account.platform, account.platform)
    if account.source == "dashboard":
        position = "Primary" if account.position == 1 else f"Backup {account.position - 1}"
        return f"The configured {name} account ({position})"
    if account.source == "env":
        return f"The {name} account in SOCIALSCRAPE_COOKIES_{account.platform.upper()}"
    return f"The {name} account of SOCIALSCRAPE_COOKIES_FROM_BROWSER"


_RETRY_LATER = re.compile(r"[;,.]\s*(?:try again|retry) later.*$", re.I)


def login_error(platform: str, accounts: list[Account], limited: Account | None,
                failure: ApiError | None) -> AccountRejected | None:
    """The 501 for a route that needs a signed-in account, once the call without account cookies asked for one,
    or None when there is no evidence against the accounts (the caller re-raises the account's own failure)."""
    rejected = [account for account in accounts if health(account).status == "rejected"]
    if rejected and len(rejected) == len(accounts):  # else a working account's own failure says more
        account, h = rejected[0], health(rejected[0])
        more = f" (and {len(rejected) - 1} more)" if len(rejected) > 1 else ""
        return AccountRejected(
            f"{_named(account)}{more} was rejected: {h.reason}{_when(h.rejected_at)}. {_replace_hint(account)}.",
            platform=platform, reason="account_rejected", account_source=account.source)
    if limited is not None:
        h = health(limited)
        name = NAMES.get(platform, platform)
        answered = (f" ({name} answered: {_RETRY_LATER.split(failure.message)[0].rstrip('. ')})"
                    if failure is not None and failure.message else "")
        return AccountRejected(
            f"{_named(limited)} could not use this endpoint{answered}. {name} restricts this account: "
            f"{h.reason or 'some signed-in requests are refused'}. {_replace_hint(limited)}.",
            platform=platform, reason="account_limited", account_source=limited.source)
    if isinstance(failure, SessionInvalid) and accounts:
        return AccountRejected(f"{_named(accounts[0])} was not accepted: {failure.reason}. "
                               f"{_replace_hint(accounts[0])}.", platform=platform, reason="account_rejected",
                               account_source=accounts[0].source)
    return None


def summary() -> dict[str, dict[str, Any]]:
    """/health's view of the env-file and browser accounts it has verdicts for (no paths, no values)."""
    with _lock:
        items = [(platform, key, dataclasses.replace(h)) for (platform, key), h in _health.items()
                 if key.startswith(("env@", "browser:"))]
    out: dict[str, dict[str, Any]] = {}
    for platform, key, h in items:
        current = accounts(platform, [])
        if current and current[0].key == key:  # not an older version of the env file
            out[platform] = {"source": current[0].source, **{k: v for k, v in h.public().items()
                                                             if k != "account"}}
    return out
