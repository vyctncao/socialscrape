"""Web Interface Guidelines fixes: auth errors that are safe and sit on their field, and dashboard markup rules."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "socialscrape" / "static"


@pytest.fixture
def make_app(monkeypatch, tmp_path):
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    def build(setup_token=None):
        monkeypatch.setenv("SOCIALSCRAPE_PORTAL_PATH", str(tmp_path / "portal.sqlite3"))
        monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "")
        if setup_token:
            monkeypatch.setenv("SOCIALSCRAPE_SETUP_TOKEN", setup_token)
        else:
            monkeypatch.delenv("SOCIALSCRAPE_SETUP_TOKEN", raising=False)
        get_settings.cache_clear()
        return create_app()

    yield build
    get_settings.cache_clear()


def field(page, name):
    return re.search(rf'<input name="{name}"[^>]*>', page)[0]


def test_sign_in_errors_travel_as_codes_so_the_url_cannot_put_words_on_the_page(make_app):
    app = make_app()
    app.state.portal.create_member("owner@example.com", "correct horse battery", "owner")
    client = TestClient(app, base_url="https://testserver")
    failed = client.post("/login", data={"email": "owner@example.com", "password": "not the password"},
                         follow_redirects=False)
    assert failed.status_code == 303 and failed.headers["location"].startswith("/login?error=credentials")
    page = client.get(failed.headers["location"]).text
    assert 'role="alert">Email or password is incorrect. Check both and try again.</div>' in page
    assert 'spellcheck="false"' in field(page, "email") and "aria-invalid" not in page
    spoofed = client.get("/login", params={"error": "Your account is locked. Call 555-0100."}).text
    assert "555-0100" not in spoofed and 'class="form-error"' not in spoofed


def test_setup_errors_sit_on_their_field_and_take_focus(make_app):
    client = TestClient(make_app(setup_token="one-time-code"), base_url="https://testserver")
    bad_code = client.post("/setup", data={"email": "owner@example.com", "password": "correct horse battery",
                                           "setup_token": "wrong"}, follow_redirects=False)
    assert bad_code.headers["location"] == "/login?error=setup_code"
    page = client.get(bad_code.headers["location"]).text
    setup = field(page, "setup_token")
    assert 'aria-invalid="true"' in setup and "autofocus" in setup and 'aria-describedby="setup_token-error"' in setup
    assert '<p class="field-error" id="setup_token-error">This setup code is not valid.' in page
    assert 'class="form-error"' not in page and "autofocus" not in field(page, "email")
    short = client.post("/setup", data={"email": "owner@example.com", "password": "short",
                                        "setup_token": "one-time-code"}, follow_redirects=False)
    assert short.headers["location"] == "/login?error=password"
    password = field(client.get(short.headers["location"]).text, "password")
    assert 'aria-invalid="true"' in password and 'aria-describedby="password-error password-hint"' in password


def test_invite_failures_keep_the_form_or_name_the_next_step_without_internals(make_app):
    from socialscrape.portal import Principal

    app = make_app()
    store = app.state.portal
    owner = Principal(store.create_member("owner@example.com", "correct horse battery", "owner"),
                      "owner@example.com", "owner")
    client = TestClient(app, base_url="https://testserver")
    token = store.create_invite(owner, "friend@example.com")
    short = client.post(f"/invite/{token}", data={"password": "short"}, follow_redirects=False)
    assert short.status_code == 400 and 'action' not in short.text.split("<form", 1)[1].split(">", 1)[0]
    assert 'aria-invalid="true"' in field(short.text, "password") and "autofocus" in field(short.text, "password")
    assert '<p class="field-error" id="password-error">Use at least 12 characters for the password.</p>' in short.text
    assert '<h1>Join <span translate="no">Social Scrape</span></h1>' in short.text
    taken = store.create_invite(owner, "owner@example.com")
    duplicate = client.post(f"/invite/{taken}", data={"password": "another strong password"})
    assert "An account with this email already exists. Sign in with it instead." in duplicate.text
    assert 'href="/login"' in duplicate.text and "UNIQUE" not in duplicate.text
    gone = client.post("/invite/not-a-real-token", data={"password": "another strong password"})
    assert "Invitation unavailable" in gone.text and 'href="/login"' in gone.text


def test_auth_pages_follow_the_theme_and_keep_the_brand_untranslated(make_app):
    page = TestClient(make_app(), base_url="https://testserver").get("/login").text
    assert '<meta name="theme-color" content="#212121">' in page
    assert re.search(r'<a class="brand"[^>]*translate="no"', page)
    assert '<span translate="no">Social Scrape</span>' in page
    appearance = (STATIC / "appearance.js").read_text()
    assert "function updateThemeColor()" in appearance and "updateButtons(); updateThemeColor();" in appearance
    assert "'/oauth/authorize' ? (deny ? 'Denying…' : 'Approving…')" in appearance
    assert "event.target.getAttribute('aria-busy') === 'true'" in appearance


def test_dashboard_markup_meets_the_interface_guidelines():
    scripts = {name: (STATIC / name).read_text() for name in ("portal.js", "preview.js", "selftest.js", "fallback.js")}
    for name, text in scripts.items():
        assert not re.findall(r'<(?:code|pre)(?=[ >])(?![^>]*translate="no")', text), name
    portal = scripts["portal.js"]
    placeholders = re.findall(r'placeholder="([^"$]+)"', portal)
    assert placeholders and all(text.endswith("…") for text in placeholders), placeholders
    assert 'autocomplete="email"' not in portal and 'name="param-${esc(param.name)}" autocomplete="off"' in portal
    assert 'id="doc-search" name="q" autocomplete="off" spellcheck="false"' in portal
    assert "url.searchParams.set('q', query)" in portal
    assert "const HISTORY_PAGE = 50;" in portal and "data.requests.slice(0, HISTORY_PAGE)" in portal
    assert "I saved this key" not in portal and "will not be shown again" not in portal
    assert not re.search(r"\} ms\b|\d MiB|~25 s", portal + scripts["selftest.js"])
    css = (STATIC / "portal.css").read_text()
    assert "scroll-padding-top:calc(var(--topbar-h) + var(--space-3))" in css and "-webkit-tap-highlight-color" in css
    assert "text-wrap:balance" in css and "@media(pointer:fine){html{overscroll-behavior-y:none}}" in css
    selftest_css = (STATIC / "selftest.css").read_text()
    assert "transition:width" not in selftest_css and "::-webkit-progress-value" not in selftest_css
    assert 'role="progressbar"' in scripts["selftest.js"] and "fill.animate(" in scripts["selftest.js"]
