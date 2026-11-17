"""Dashboard email through Resend: invitations, password resets, account passwords and the owner's email settings."""

import io
import json
import sqlite3
import urllib.error

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

OWNER, OWNER_PASSWORD = "owner@example.com", "correct horse battery"
ENV_EMAIL = {"SOCIALSCRAPE_RESEND_API_KEY": "re_env_secret_key", "SOCIALSCRAPE_EMAIL_FROM": "SocialScrape <noreply@mail.example.com>",
             "SOCIALSCRAPE_PUBLIC_URL": "https://scrape.example.com/"}


class FakeResend:
    """Stands in for mailer._post: records each request and answers like Resend, or raises what `fail()` makes."""

    def __init__(self):
        self.requests, self.fail = [], None

    def __call__(self, request):
        self.requests.append(request)
        if self.fail:
            raise self.fail()
        return b'{"id": "4ef9a417-02e9-4d39-ad75-9611e0fcc33c"}'

    @property
    def sent(self):
        return [json.loads(r.data) for r in self.requests]


def rejection(status, message):
    body = json.dumps({"statusCode": status, "message": message, "name": "validation_error"}).encode()
    return urllib.error.HTTPError("https://api.resend.com/emails", status, "Forbidden", {}, io.BytesIO(body))


@pytest.fixture
def resend(monkeypatch):
    from socialscrape import mailer

    fake = FakeResend()
    monkeypatch.setattr(mailer, "_post", fake)
    return fake


@pytest.fixture
def workspace(monkeypatch, tmp_path, resend):
    """`workspace(**env)`: an app factory for a dashboard database in tmp_path, email settings blank unless given."""
    from socialscrape import ytdlp
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    key = Fernet.generate_key().decode()

    def make(**env):
        values = {"SOCIALSCRAPE_PORTAL_PATH": str(tmp_path / "portal.sqlite3"), "SOCIALSCRAPE_API_KEYS": "",
                  "SOCIALSCRAPE_SETUP_TOKEN": "setup", "SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY": key,
                  "SOCIALSCRAPE_RESEND_API_KEY": "", "SOCIALSCRAPE_EMAIL_FROM": "", "SOCIALSCRAPE_PUBLIC_URL": "", **env}
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        return create_app

    monkeypatch.setattr(ytdlp, "_executor", None)
    yield make
    get_settings.cache_clear()


def client(create_app):
    return TestClient(create_app(), base_url="https://testserver", raise_server_exceptions=False)


def signed_in_owner(create_app):
    owner = client(create_app)
    assert owner.post("/setup", data={"email": OWNER, "password": OWNER_PASSWORD, "setup_token": "setup"},
                      follow_redirects=False).status_code == 303
    return owner, {"x-csrf-token": owner.get("/portal/api/me").json()["csrf"]}


def join(create_app, url, password="another strong password"):
    member = client(create_app)
    assert member.post("/invite/" + url.rsplit("/", 1)[-1], data={"password": password},
                       follow_redirects=False).status_code == 303
    return member, {"x-csrf-token": member.get("/portal/api/me").json()["csrf"]}


def link_in(email, path):
    return next(word for word in email["text"].split() if f"{path}/" in word)


def test_invitations_are_emailed_with_links_to_the_public_url_never_the_host(workspace, resend):
    create_app = workspace(**ENV_EMAIL)
    owner, headers = signed_in_owner(create_app)
    status = owner.get("/portal/api/settings/email")
    assert status.json() == {"configured": True, "source": "environment",
                             "sender": "SocialScrape <noreply@mail.example.com>",
                             "public_url": "https://scrape.example.com", "updated_at": None}
    assert "re_env_secret_key" not in status.text

    invited = owner.post("/portal/api/invites", headers={**headers, "host": "attacker.example"},
                         json={"email": " Friend@Example.com "}).json()
    assert invited["url"].startswith("https://scrape.example.com/invite/")
    assert (invited["email"], invited["emailed"], invited["email_error"]) == ("friend@example.com", True, None)
    request, = resend.requests
    assert request.full_url == "https://api.resend.com/emails" and request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer re_env_secret_key"
    assert request.get_header("User-agent").startswith("socialscrape")  # Resend refuses requests without one
    email, = resend.sent
    assert email["from"] == "SocialScrape <noreply@mail.example.com>" and email["to"] == ["friend@example.com"]
    assert email["subject"] == "You’re invited to SocialScrape" and OWNER in email["text"]
    assert link_in(email, "/invite") == invited["url"] and invited["url"] in email["html"]

    page = client(create_app).get(invited["url"].replace("https://scrape.example.com", ""))
    assert "friend@example.com" in page.text
    assert page.headers["referrer-policy"] == "no-referrer" and page.headers["cache-control"] == "no-store"

    # Resending replaces the pending invitation: one per address, and only the newest link works.
    pending = owner.get("/portal/api/invites").json()["invites"]
    assert [(i["email"], i["invited_by"], i["expired"]) for i in pending] == [("friend@example.com", OWNER, False)]
    again = owner.post("/portal/api/invites", headers=headers, json={"email": "friend@example.com"}).json()
    assert again["url"] != invited["url"] and len(resend.sent) == 2
    assert len(owner.get("/portal/api/invites").json()["invites"]) == 1
    assert "Invitation unavailable" in client(create_app).get(invited["url"][len("https://scrape.example.com"):]).text

    marked = owner.post("/portal/api/invites", headers=headers, json={"email": "<b>bold</b>@example.com"}).json()
    assert marked["emailed"] and "<b>bold</b>" not in resend.sent[-1]["html"]
    assert "&lt;b&gt;bold&lt;/b&gt;@example.com" in resend.sent[-1]["html"]

    assert owner.delete("/portal/api/invites", params={"email": "friend@example.com"},
                        headers=headers).json() == {"revoked": True}
    assert owner.delete("/portal/api/invites", params={"email": "friend@example.com"},
                        headers=headers).json() == {"revoked": False}
    assert "Invitation unavailable" in client(create_app).get(again["url"][len("https://scrape.example.com"):]).text

    for bad, says in ((OWNER, "already a workspace member"), ("not-an-email", "valid email")):
        response = owner.post("/portal/api/invites", headers=headers, json={"email": bad})
        assert response.status_code == 400 and says in response.json()["message"]

    member, member_headers = join(create_app, marked["url"])
    assert member.post("/portal/api/invites", headers=member_headers, json={"email": "x@example.com"}).status_code == 403
    assert member.get("/portal/api/invites").status_code == 403
    assert member.get("/portal/api/settings/email").status_code == 403


def test_owners_copy_links_when_email_is_off_or_resend_refuses(workspace, resend, tmp_path):
    create_app = workspace()
    owner, headers = signed_in_owner(create_app)
    assert owner.get("/portal/api/settings/email").json()["configured"] is False
    off = owner.post("/portal/api/invites", headers=headers, json={"email": "a@example.com"}).json()
    assert off["url"].startswith("https://testserver/invite/") and (off["emailed"], off["email_error"]) == (False, None)
    assert resend.requests == []
    assert owner.post("/portal/api/settings/email/test", headers=headers).status_code == 400

    saved = owner.put("/portal/api/settings/email", headers=headers, json={
        "api_key": "re_dashboard_secret", "sender": "Team <team@mail.example.com>", "public_url": "https://dash.example.com"})
    assert saved.json() == {"configured": True}
    status = owner.get("/portal/api/settings/email").json()
    assert (status["source"], status["sender"], status["public_url"]) == (
        "dashboard", "Team <team@mail.example.com>", "https://dash.example.com")
    assert status["updated_at"] and b"re_dashboard_secret" not in (tmp_path / "portal.sqlite3").read_bytes()

    resend.fail = lambda: rejection(403, "The mail.example.com domain is not verified. Please, add and verify your domain.")
    refused = owner.post("/portal/api/invites", headers=headers, json={"email": "b@example.com"})
    body = refused.json()
    assert refused.status_code == 200 and body["emailed"] is False and body["url"].startswith("https://dash.example.com/")
    assert "domain is not verified" in body["email_error"] and "re_dashboard_secret" not in refused.text
    test = owner.post("/portal/api/settings/email/test", headers=headers)
    assert test.status_code == 502 and "domain is not verified" in test.json()["message"]
    resend.fail = lambda: urllib.error.URLError("timed out")
    unreachable = owner.post("/portal/api/invites", headers=headers, json={"email": "b@example.com"}).json()
    assert unreachable["email_error"].startswith("Could not reach Resend")
    resend.fail = lambda: rejection(429, "Too many requests")
    assert "rate limiting" in owner.post("/portal/api/settings/email/test", headers=headers).json()["message"]

    resend.fail = None
    assert owner.post("/portal/api/settings/email/test", headers=headers).json() == {"sent": True, "to": OWNER}
    assert resend.requests[-1].get_header("Authorization") == "Bearer re_dashboard_secret"

    # A blank key keeps the saved one while the sender or address changes.
    owner.put("/portal/api/settings/email", headers=headers,
              json={"api_key": "", "sender": "ops@mail.example.com", "public_url": "https://dash.example.com/"})
    owner.post("/portal/api/settings/email/test", headers=headers)
    assert resend.requests[-1].get_header("Authorization") == "Bearer re_dashboard_secret"
    assert resend.sent[-1]["from"] == "ops@mail.example.com"
    for bad, says in (({"sender": "nobody", "public_url": "https://dash.example.com"}, "from address"),
                      ({"sender": "ops@mail.example.com", "public_url": "dash.example.com"}, "dashboard URL"),
                      ({"sender": "ops@mail.example.com", "public_url": "https://u:p@dash.example.com"}, "dashboard URL"),
                      ({"api_key": "re_ has spaces", "sender": "a@b.co", "public_url": "https://d.example"}, "API key")):
        response = owner.put("/portal/api/settings/email", headers=headers, json={"api_key": "", **bad})
        assert response.status_code == 400 and says in response.json()["message"]
    assert owner.put("/portal/api/settings/email", json={"api_key": "re_x", "sender": "a@b.co",
                                                          "public_url": "https://d.example"}).status_code == 403

    assert owner.delete("/portal/api/settings/email", headers=headers).json() == {"cleared": True}
    assert owner.get("/portal/api/settings/email").json()["configured"] is False
    response = owner.put("/portal/api/settings/email", headers=headers,
                         json={"api_key": "", "sender": "a@b.co", "public_url": "https://d.example"})
    assert response.status_code == 400 and "Resend API key" in response.json()["message"]


def test_saved_email_settings_another_key_cant_read_turn_email_off(workspace, resend, monkeypatch):
    create_app = workspace()
    owner, headers = signed_in_owner(create_app)
    owner.put("/portal/api/settings/email", headers=headers,
              json={"api_key": "re_dashboard_secret", "sender": "a@mail.example.com", "public_url": "https://d.example"})
    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    status = owner.get("/portal/api/settings/email").json()
    assert status["configured"] is False and "cannot decrypt" in status["error"]
    invited = owner.post("/portal/api/invites", headers=headers, json={"email": "a@example.com"}).json()
    assert invited["emailed"] is False and "cannot decrypt" in invited["email_error"]
    assert invited["url"].startswith("https://testserver/invite/")
    assert "Ask your workspace owner" in client(create_app).get("/forgot").text
    assert resend.requests == []


def test_forgot_password_sends_one_time_links_without_revealing_who_has_an_account(workspace, resend, tmp_path):
    create_app = workspace(**ENV_EMAIL)
    owner, headers = signed_in_owner(create_app)
    invited = owner.post("/portal/api/invites", headers=headers, json={"email": "friend@example.com"}).json()
    friend, _ = join(create_app, invited["url"])
    resend.requests.clear()

    anonymous = client(create_app)
    login = anonymous.get("/login").text
    assert 'href="/forgot"' in login
    assert "Reset your password" in anonymous.get("/forgot").text
    unknown = anonymous.post("/forgot", data={"email": "nobody@example.com"}, follow_redirects=False)
    known = anonymous.post("/forgot", data={"email": "FRIEND@example.com"}, headers={"host": "attacker.example"},
                           follow_redirects=False)
    assert unknown.status_code == known.status_code == 303
    assert unknown.headers["location"] == known.headers["location"] == "/forgot?sent=1"
    assert "Check your email" in anonymous.get("/forgot?sent=1").text
    email, = resend.sent
    assert email["to"] == ["friend@example.com"] and email["subject"] == "Reset your SocialScrape password"
    link = link_in(email, "/reset")
    assert link.startswith("https://scrape.example.com/reset/") and "expires in 1 hour" in email["text"]
    path = link[len("https://scrape.example.com"):]

    for _ in range(4):  # three links an hour, then the same answer without an email
        assert anonymous.post("/forgot", data={"email": "friend@example.com"},
                              follow_redirects=False).headers["location"] == "/forgot?sent=1"
    assert len(resend.sent) == 3
    later = link_in(resend.sent[1], "/reset")[len("https://scrape.example.com"):]

    page = anonymous.get(path)
    assert "Choose a new password" in page.text and "friend@example.com" in page.text
    assert page.headers["referrer-policy"] == "no-referrer" and page.headers["cache-control"] == "no-store"
    short = anonymous.post(path, data={"password": "too short"}, follow_redirects=False)
    assert short.status_code == 200 and "at least 12 characters" in short.text
    done = anonymous.post(path, data={"password": "a brand new passphrase"}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/app"
    assert anonymous.get("/portal/api/me").json()["email"] == "friend@example.com"
    assert friend.get("/portal/api/me").status_code == 401  # signed out everywhere else
    notice = resend.sent[-1]
    assert notice["subject"] == "Your SocialScrape password was changed" and notice["to"] == ["friend@example.com"]

    # Every outstanding link ends with the reset, not just the one used.
    for used in (path, later):
        assert "Reset link unavailable" in anonymous.get(used).text
        assert "Reset link unavailable" in anonymous.post(used, data={"password": "yet another passphrase"}).text
    assert client(create_app).post("/login", data={"email": "friend@example.com", "password": "another strong password"},
                                   follow_redirects=False).headers["location"].startswith("/login?error=")
    assert client(create_app).post("/login", data={"email": "friend@example.com", "password": "a brand new passphrase"},
                                   follow_redirects=False).headers["location"] == "/app"

    assert anonymous.get("/forgot", follow_redirects=False).headers["location"] == "/app/account"
    resend.requests.clear()
    anonymous_again = client(create_app)
    anonymous_again.post("/forgot", data={"email": OWNER})
    with sqlite3.connect(tmp_path / "portal.sqlite3") as db:
        db.execute("UPDATE password_resets SET expires_at=0 WHERE used_at IS NULL")
    expired = link_in(resend.sent[0], "/reset")[len("https://scrape.example.com"):]
    assert "Reset link unavailable" in anonymous_again.get(expired).text


def test_forgot_password_without_email_points_members_to_their_owner(workspace, resend):
    create_app = workspace()
    signed_in_owner(create_app)
    anonymous = client(create_app)
    assert "Ask your workspace owner" in anonymous.get("/forgot").text
    assert anonymous.post("/forgot", data={"email": OWNER}, follow_redirects=False).headers["location"] == "/forgot"
    assert resend.requests == []
    assert "Reset link unavailable" in anonymous.get("/reset/not-a-token").text


def test_owners_send_members_reset_links(workspace, resend):
    create_app = workspace()
    owner, headers = signed_in_owner(create_app)
    invited = owner.post("/portal/api/invites", headers=headers, json={"email": "friend@example.com"}).json()
    friend, friend_headers = join(create_app, invited["url"])
    member = next(m for m in owner.get("/portal/api/team").json()["members"] if m["role"] == "member")
    me = next(m for m in owner.get("/portal/api/team").json()["members"] if m["role"] == "owner")

    copied = owner.post(f"/portal/api/team/{member['id']}/password-reset", headers=headers).json()
    assert copied["url"].startswith("https://testserver/reset/") and copied["email"] == "friend@example.com"
    assert (copied["emailed"], copied["email_error"]) == (False, None) and resend.requests == []
    assert owner.post(f"/portal/api/team/{me['id']}/password-reset", headers=headers).status_code == 400
    assert owner.post(f"/portal/api/team/{member['id']}/password-reset").status_code == 403  # no CSRF token
    assert friend.post(f"/portal/api/team/{me['id']}/password-reset", headers=friend_headers).status_code == 403
    assert friend.get("/portal/api/me").status_code == 200  # the current password keeps working until it's reset

    stranger = client(create_app)
    assert stranger.post(copied["url"][len("https://testserver"):], data={"password": "chosen by the member"},
                         follow_redirects=False).headers["location"] == "/app"
    assert friend.get("/portal/api/me").status_code == 401

    owner.put("/portal/api/settings/email", headers=headers,
              json={"api_key": "re_dashboard_secret", "sender": "a@mail.example.com", "public_url": "https://d.example"})
    emailed = owner.post(f"/portal/api/team/{member['id']}/password-reset", headers=headers).json()
    assert emailed["emailed"] is True and emailed["url"].startswith("https://d.example/reset/")
    email, = resend.sent
    assert OWNER in email["text"] and "24 hours" in email["text"] and link_in(email, "/reset") == emailed["url"]


def test_members_change_their_password_and_sign_out_their_other_sessions(workspace, resend):
    create_app = workspace(**ENV_EMAIL)
    owner, headers = signed_in_owner(create_app)
    laptop = client(create_app)
    laptop.post("/login", data={"email": OWNER, "password": OWNER_PASSWORD})
    assert laptop.get("/portal/api/me").status_code == 200

    def change(current, new, with_csrf=True):
        return owner.post("/portal/api/account/password", headers=headers if with_csrf else {},
                          json={"current_password": current, "new_password": new})

    assert change(OWNER_PASSWORD, "a new owner passphrase", with_csrf=False).status_code == 403
    wrong = change("not the password", "a new owner passphrase")
    assert wrong.status_code == 400 and wrong.json()["message"] == "Current password is incorrect"
    short = change(OWNER_PASSWORD, "short")
    assert short.status_code == 400 and "12 characters" in short.json()["message"]
    assert resend.requests == []
    assert change(OWNER_PASSWORD, "a new owner passphrase").json() == {"changed": True, "signed_out_sessions": 1}
    assert owner.get("/portal/api/me").status_code == 200 and laptop.get("/portal/api/me").status_code == 401
    assert resend.sent[-1]["subject"] == "Your SocialScrape password was changed"
    assert client(create_app).post("/login", data={"email": OWNER, "password": "a new owner passphrase"},
                                   follow_redirects=False).headers["location"] == "/app"


@pytest.mark.parametrize("env,says", [
    ({"SOCIALSCRAPE_RESEND_API_KEY": "re_x"}, "needs SOCIALSCRAPE_EMAIL_FROM and SOCIALSCRAPE_PUBLIC_URL"),
    ({**ENV_EMAIL, "SOCIALSCRAPE_PUBLIC_URL": ""}, "needs SOCIALSCRAPE_EMAIL_FROM and SOCIALSCRAPE_PUBLIC_URL"),
    ({**ENV_EMAIL, "SOCIALSCRAPE_PUBLIC_URL": "scrape.example.com"}, "SOCIALSCRAPE_PUBLIC_URL must be"),
    ({**ENV_EMAIL, "SOCIALSCRAPE_PUBLIC_URL": "https://scrape.example.com/?next=1"}, "SOCIALSCRAPE_PUBLIC_URL must be"),
    ({**ENV_EMAIL, "SOCIALSCRAPE_PUBLIC_URL": "https://scrape.example.com:0"}, "SOCIALSCRAPE_PUBLIC_URL must be"),
    ({**ENV_EMAIL, "SOCIALSCRAPE_PUBLIC_URL": "https://example.com/socialscrape"}, "without a path"),
    ({**ENV_EMAIL, "SOCIALSCRAPE_EMAIL_FROM": "SocialScrape"}, "SOCIALSCRAPE_EMAIL_FROM must be"),
    ({**ENV_EMAIL, "SOCIALSCRAPE_EMAIL_FROM": "SocialScrape <noreply@example.com"}, "SOCIALSCRAPE_EMAIL_FROM must be"),
    ({**ENV_EMAIL, "SOCIALSCRAPE_EMAIL_FROM": "noreply@exa mple.com"}, "SOCIALSCRAPE_EMAIL_FROM must be"),
    ({**ENV_EMAIL, "SOCIALSCRAPE_EMAIL_FROM": "noreply@localhost"}, "SOCIALSCRAPE_EMAIL_FROM must be"),
    ({**ENV_EMAIL, "SOCIALSCRAPE_RESEND_API_KEY": "re_key with space"}, "SOCIALSCRAPE_RESEND_API_KEY must be"),
])
def test_unusable_email_settings_stop_startup(workspace, env, says):
    from socialscrape.config import ConfigError

    create_app = workspace(**env)
    with pytest.raises(ConfigError, match=says):
        create_app()


def test_settings_keep_the_resend_key_out_of_their_repr(workspace):
    from socialscrape.config import get_settings

    workspace(**ENV_EMAIL)
    settings = get_settings()
    assert settings.resend_api_key == "re_env_secret_key" and "re_env_secret_key" not in repr(settings)
    assert settings.public_url == "https://scrape.example.com"


def test_a_password_change_does_not_overwrite_one_that_landed_while_it_hashed(workspace, resend, monkeypatch):
    from socialscrape.portal import PortalStore

    create_app = workspace()
    owner, headers = signed_in_owner(create_app)
    store, original = owner.app.state.portal, PortalStore._password_hash

    def hashing_while_a_reset_lands(password):
        if password == "first new passphrase":
            with store._lock:
                store._conn.execute("UPDATE members SET password_hash=? WHERE email=?",
                                    (original("set from a reset link"), OWNER))
                store._conn.commit()
        return original(password)

    monkeypatch.setattr(PortalStore, "_password_hash", staticmethod(hashing_while_a_reset_lands))
    response = owner.post("/portal/api/account/password", headers=headers,
                          json={"current_password": OWNER_PASSWORD, "new_password": "first new passphrase"})
    assert response.status_code == 400 and "just changed" in response.json()["message"]
    assert client(create_app).post("/login", data={"email": OWNER, "password": "set from a reset link"},
                                   follow_redirects=False).headers["location"] == "/app"


def test_older_duplicate_invitations_list_once_and_end_together(workspace, resend, tmp_path):
    create_app = workspace()
    owner, headers = signed_in_owner(create_app)
    invited = owner.post("/portal/api/invites", headers=headers, json={"email": "a@example.com"}).json()
    with sqlite3.connect(tmp_path / "portal.sqlite3") as db:  # what the dashboard allowed before one-per-address
        db.execute("INSERT INTO invites(token_hash,email,created_by,expires_at) "
                   "SELECT 'older', email, created_by, expires_at - 60 FROM invites")
    pending = owner.get("/portal/api/invites").json()["invites"]
    assert [(i["email"], i["expires_at"]) for i in pending] == [("a@example.com", invited["expires_at"])]
    assert owner.delete("/portal/api/invites", params={"email": "a@example.com"}, headers=headers).json()["revoked"]
    with sqlite3.connect(tmp_path / "portal.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM invites").fetchone()[0] == 0

    # Joining with one of them ends the others.
    invited = owner.post("/portal/api/invites", headers=headers, json={"email": "b@example.com"}).json()
    with sqlite3.connect(tmp_path / "portal.sqlite3") as db:
        db.execute("INSERT INTO invites(token_hash,email,created_by,expires_at) "
                   "SELECT 'older', email, created_by, expires_at - 60 FROM invites WHERE email='b@example.com'")
    join(create_app, invited["url"])
    assert owner.get("/portal/api/invites").json()["invites"] == []
    with sqlite3.connect(tmp_path / "portal.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM invites WHERE accepted_at IS NULL").fetchone()[0] == 0


def test_the_access_log_keeps_no_invitation_or_reset_tokens(workspace, resend, caplog):
    import logging

    create_app = workspace()
    owner, headers = signed_in_owner(create_app)
    token = owner.post("/portal/api/invites", headers=headers, json={"email": "a@example.com"}).json()["url"]
    token = token.rsplit("/", 1)[-1]
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        for path in (f"/invite/{token}", f"/reset/{token}?x=1", "/reset/abc/def", "/app/team"):  # as uvicorn logs
            logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", path, "1.1", 200)
    assert token not in caplog.text and "abc" not in caplog.text and "/app/team" in caplog.text
    assert "/invite/[redacted]" in caplog.text and "/reset/[redacted]?x=1" in caplog.text
