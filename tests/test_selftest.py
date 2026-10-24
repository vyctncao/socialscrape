"""Endpoint tests (socialscrape/selftest.py): every catalog endpoint has a preset the dashboard can send, the presets
agree with the catalog and the ScrapeCreators contracts, and signed-in members can read them. The runs themselves are
live; the dashboard (/app/tests) makes them."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from socialscrape import fallback, selftest
from socialscrape.catalog import CATALOG
from socialscrape.config import get_settings
from socialscrape.main import create_app
from tests.contract import load_contract
from tests.test_fallback import Upstream

ENTRIES = {entry["path"]: entry for entry in CATALOG}


def test_every_catalog_endpoint_has_a_preset():
    assert set(selftest.PRESETS) == set(ENTRIES)


@pytest.mark.parametrize("path", sorted(ENTRIES))
def test_a_preset_sends_catalog_params_including_every_required_one(path):
    entry, preset = ENTRIES[path], selftest.PRESETS[path]
    sent = set(preset.params) | set(preset.derive)
    assert sent <= set(entry["params"]) and set(entry["required"]) <= sent
    assert not set(preset.params) & set(preset.derive)
    assert all(isinstance(value, str) and value.strip() == value != "" for value in preset.params.values())
    assert "cache_max_age" not in sent  # a test is always answered live


@pytest.mark.parametrize("path", sorted(ENTRIES))
def test_a_passing_answer_must_hold_contract_fields(path):
    preset = selftest.PRESETS[path]
    assert preset.expect, "a 200 without data must not pass"
    assert set(preset.expect) <= set(load_contract(path))


def test_a_derived_param_comes_from_a_static_preset_on_the_same_platform():
    create_app()  # registers the platform routes
    implemented = {row["path"] for row in selftest.coverage() if row["status"] == "implemented"}
    derived = {path: preset for path, preset in selftest.PRESETS.items() if preset.derive}
    assert derived
    for path, preset in derived.items():
        for source, pick in preset.derive.values():
            assert source != path and source.split("/")[2] == path.split("/")[2]
            # The dashboard runs a lane's tests in order, after their sources: no chains, nothing unanswerable.
            assert source in implemented and not selftest.PRESETS[source].derive
            assert pick in load_contract(source)


@pytest.fixture
def signed_in(tmp_path, monkeypatch):
    """signed_in(**env) -> a client signed in as the owner of a fresh portal."""
    for name in ("SOCIALSCRAPE_SCRAPECREATORS_API_KEY", "SOCIALSCRAPE_SCRAPECREATORS_URL",
                 "SOCIALSCRAPE_SCRAPECREATORS_FALLBACK", "SOCIALSCRAPE_SCRAPECREATORS_PLATFORMS",
                 "SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                 "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)

    def build(**env: str) -> TestClient:
        for name, value in {"SOCIALSCRAPE_PORTAL_PATH": str(tmp_path / "portal.sqlite3"),
                            "SOCIALSCRAPE_SETUP_TOKEN": "setup", **env}.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        c = TestClient(create_app(), base_url="https://testserver", raise_server_exceptions=False)
        c.post("/setup", data={"email": "owner@example.com", "password": "correct horse battery",
                               "setup_token": "setup"})
        return c

    yield build
    fallback.shutdown_pool()
    get_settings.cache_clear()


def test_members_read_every_endpoint_with_its_preset(signed_in):
    c = signed_in()
    data = c.get("/portal/api/endpoint-tests").json()
    assert data["fallback"] is None
    assert [test["path"] for test in data["tests"]] == list(ENTRIES)
    by_path = {test["path"]: test for test in data["tests"]}
    video = by_path["/v1/youtube/video"]
    assert video["implemented"] is True and video["reason"] is None and video["platform"] == "youtube"
    assert video["params"] == selftest.PRESETS["/v1/youtube/video"].params and video["expect"]
    trending = by_path["/v1/youtube/shorts/trending"]
    assert trending["implemented"] is True and trending["reason"] is None
    gated = by_path["/v1/tiktok/video/comments"]
    assert gated["implemented"] is False and gated["needs_setup"] is True
    [chained] = [test for test in data["tests"] if test["path"] == "/v1/youtube/video/comment/replies"]
    assert chained["derive"] == [{"param": "continuationToken", "source": "/v1/youtube/video/comments",
                                  "pick": selftest.PRESETS[chained["path"]].derive["continuationToken"][1]}]

    assert TestClient(c.app, base_url="https://testserver").get("/portal/api/endpoint-tests").status_code == 401
    page = c.get("/app/tests").text
    version = re.search(r'/assets/selftest\.js\?v=(\w+)"', page)[1]
    assert f"/assets/selftest.css?v={version}" in page and 'href="/app/tests"' in page
    initial = re.search(r'<main[^>]*id="view"[^>]*>(.*?)</main>', page, re.S)[1]
    assert "<h1>Playground</h1>" in initial and 'class="skeleton ' in initial
    assert '<a href="/app/tests" aria-current="page">Endpoint tests</a>' in initial  # a Playground tab
    assert 'class="card test-run"' in initial and 'class="test-run-actions"' in initial
    assert 'class="test-run-options"' in initial
    assert set(re.findall(r'data-loading-test-group="(instagram|tiktok|youtube)"', initial)) == {"instagram", "tiktok", "youtube"}
    assert initial.count('class="table-wrap skeleton-table"') == 3
    assert "window.EndpointTests" in c.get(f"/assets/selftest.js?v={version}").text
    gated = c.get("/v1/tiktok/video/comments", params={"url": "x"}, headers={fallback.HEADER: "off"})
    assert gated.status_code == 501 and gated.json()["error"] == "platform_auth_required"


def test_the_page_learns_what_falls_back(signed_in):
    c = signed_in(SOCIALSCRAPE_SCRAPECREATORS_API_KEY="sc-key", SOCIALSCRAPE_SCRAPECREATORS_URL="http://127.0.0.1:9")
    falls_back = c.get("/portal/api/endpoint-tests").json()["fallback"]
    assert falls_back["provider"] == "scrapecreators" and falls_back["triggers"] == list(fallback.DEFAULT_TRIGGERS)
    assert sorted(falls_back["platforms"]) == ["instagram", "tiktok", "youtube"]
    # The page's requests skip it unless the member allows it.
    r = c.get("/v1/tiktok/video/comments", params={"url": "x"}, headers={fallback.HEADER: "OFF"})
    assert r.status_code == 501 and r.json()["error"] == "platform_auth_required"
    assert fallback.HEADER not in r.headers and "fallback" not in r.json()


def test_an_opted_out_request_is_not_answered_with_a_cached_fallback(signed_in, tmp_path):
    upstream = Upstream()
    try:
        c = signed_in(SOCIALSCRAPE_SCRAPECREATORS_API_KEY="sc-key", SOCIALSCRAPE_SCRAPECREATORS_URL=upstream.url,
                      SOCIALSCRAPE_CACHE_PATH=str(tmp_path / "cache.sqlite3"))
        path, params = "/v1/tiktok/video/comments", {"url": "x", "cache_max_age": "1d"}
        assert c.get(path, params=params).headers[fallback.HEADER] == fallback.PROVIDER  # answered there, cached
        assert c.get(path, params=params).headers["x-cache"] == "hit"
        own = c.get(path, params=params, headers={fallback.HEADER: "off"})
        assert own.status_code == 501 and fallback.HEADER not in own.headers
        assert len(upstream.requests) == 1
    finally:
        upstream.close()


def test_endpoint_test_assets_encode_accessible_run_state_and_mobile_rows():
    root = __import__("pathlib").Path(__file__).parents[1]
    script = (root / "socialscrape/static/selftest.js").read_text()
    styles = (root / "socialscrape/static/selftest.css").read_text()
    assert "socialscrape.endpoint-tests.v2" in script and "lastRun" in script
    assert "platform_auth_required" in script and "Needs setup" in script
    assert 'aria-live="polite" aria-atomic="true"' in script
    assert 'data-label="Endpoint"' in script and 'data-label="Preset parameters"' in script
    assert 'data-label="Result"' in script and 'data-label="Actions"' in script
    assert "Retry ${plural(retry.length" in script
    assert ".test-group thead th{position:sticky" in styles
    assert "@media(max-width:760px)" in styles and ".test-group td::before{content:attr(data-label)" in styles


def test_platform_setup_errors_render_as_needs_setup():
    script = (__import__('pathlib').Path(__file__).parents[1] / 'socialscrape' / 'static' / 'selftest.js').read_text()
    for code in ("platform_auth_required", "platform_oauth_required", "platform_shop_auth_required",
                 "platform_shop_credentials_incomplete", "platform_scope_unavailable"):
        assert code in script
