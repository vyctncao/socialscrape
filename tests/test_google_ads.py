from __future__ import annotations

import io
import json
from collections import deque
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError

from socialscrape.main import create_app
from socialscrape.platforms import _google_ads_map as google_map
from socialscrape.platforms import google_ads
from tests.contract import assert_conforms

RAW = Path(__file__).parent / "fixtures" / "google_ads"
# Where Google's 302 for a flagged IP leads (recorded 2026-10-24: www.google.com/sorry/index, then HTTP 429).
SORRY = ("https://www.google.com/sorry/index?continue=https://adstransparency.google.com/anji/_/rpc/"
         "SearchService/SearchSuggestions%3Fauthuser%3D&q=EgSsdpOQGL_o8NYG")
AD_URL = "https://adstransparency.google.com/advertiser/AR07541154843046445057/creative/CR17778900238552006657"


@pytest.fixture(autouse=True)
def fresh_cooling(monkeypatch):
    google_ads._cooling.clear()
    monkeypatch.setattr(google_ads, "_DETAIL_GAP", 0)
    yield
    google_ads._cooling.clear()


class FakeIE:
    """Answers RPCs from `responses` (an exception is raised; a str is the raw body) and previews from `previews`."""

    def __init__(self, responses: dict[str, Any], preview: str = ""):
        self.responses = responses
        self.preview = preview
        self.previews: dict[str, Any] = {}
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.preview_requests: list[str] = []
        self.final_url: str | None = None

    def _download_webpage_handle(self, url: str, _id: str, *, data: bytes, headers: dict[str, str]):
        assert headers["Sec-Fetch-Mode"] == "cors" and headers["X-Same-Domain"] == "1"
        payload = json.loads(urllib.parse.parse_qs(data.decode())["f.req"][0])
        method = url.split("/_/rpc/", 1)[1].split("?", 1)[0]
        self.requests.append((method, payload))
        answer = self.responses[method]
        if isinstance(answer, deque):  # one answer per request, in order
            answer = answer.popleft()
        if isinstance(answer, BaseException):
            raise answer
        return (answer if isinstance(answer, str) else json.dumps(answer)), SimpleNamespace(url=self.final_url or url)

    def _download_webpage(self, url: str, _id: str):
        self.preview_requests.append(url)
        answer = self.previews.get(url, self.preview)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeYDL:
    def __init__(self, ie: FakeIE, proxy: str | None = None):
        self.ie = ie
        self.params = {"proxy": proxy}

    def get_info_extractor(self, name: str):
        assert name == "Generic"
        return self.ie


def refusal(status: int = 429, url: str = SORRY, retry_after: str | None = None) -> ExtractorError:
    """What yt-dlp raises when Google's 302 to its unusual-traffic CAPTCHA ends in a 429."""
    headers = {"Retry-After": retry_after} if retry_after else {}
    return ExtractorError("Unable to download webpage: HTTP Error 429: Too Many Requests",
                          cause=HTTPError(Response(io.BytesIO(b"<html>sorry</html>"), url, headers, status=status)))


def fixture(name: str) -> dict[str, Any]:
    return json.loads((RAW / name).read_text())


@pytest.fixture
def fake(monkeypatch):
    ie = FakeIE({
        "SearchService/SearchSuggestions": fixture("fixture-advertiser-search.json"),
        "SearchService/SearchCreatives": fixture("fixture-company-by-id.json"),
        "LookupService/GetCreativeById": fixture("fixture-ad-detail-political.json"),
    })

    async def run(_platform: str, fn, **_opts):
        return fn(FakeYDL(ie))

    monkeypatch.setattr(google_ads, "with_ydl", run)
    return TestClient(create_app()), ie


def test_advertiser_search_filters_region_and_conforms(fake):
    client, ie = fake
    response = client.get("/v1/google/adLibrary/advertisers/search", params={"query": "lululemon", "region": "AU"})
    assert response.status_code == 200
    body = response.json()
    assert body["advertisers"] and all(item["region"] == "AU" for item in body["advertisers"])
    assert body["websites"]
    assert_conforms(body, "/v1/google/adLibrary/advertisers/search", min_coverage=.8)
    assert ie.requests[-1][1] == {"1": "lululemon", "2": 20, "3": 20}


def test_company_ads_encodes_filters_cursor_and_conforms(fake):
    client, ie = fake
    response = client.get("/v1/google/company/ads", params={
        "advertiser_id": "AR01614014350098432001", "region": "US", "topic": "political",
        "format": "video", "platform": "youtube", "start_date": "2025-08-01",
        "end_date": "2025-08-11", "cursor": "abc%2Bdef%3D",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ads"] and body["cursor"]
    assert set(body["ads"][0]) >= {
        "advertiserId", "creativeId", "format", "adUrl", "advertiserName", "imageUrl", "firstShown", "lastShown",
    }
    assert_conforms(body, "/v1/google/company/ads", min_coverage=.8)
    payload = ie.requests[-1][1]
    assert payload["3"]["13"]["1"] == ["AR01614014350098432001"]
    assert payload["3"]["4"] == 3 and payload["3"]["14"] == [5]
    assert payload["3"]["6"] == 20250801 and payload["4"] == "abc+def="
    assert payload["7"]["1"] == 2 and payload["7"]["3"] == 2840


def test_ad_detail_maps_political_fields_and_conforms(fake):
    client, _ = fake
    url = "https://adstransparency.google.com/advertiser/AR07541154843046445057/creative/CR17778900238552006657"
    response = client.get("/v1/google/ad", params={"url": url})
    assert response.status_code == 200
    body = response.json()
    assert body["overallImpressions"] == {"min": "500000", "max": "600000"}
    assert body["spend"] == {"currency": "USD", "lower": "5000", "upper": "6000"}
    assert body["targeting"]["location"]["included"][0]["criterionId"] == "2840"
    assert body["variations"][0]["imageUrl"].endswith("18242167605449878359")
    assert_conforms(body, "/v1/google/ad", min_coverage=.9)


def test_openapi_documents_google_enums(fake):
    client, _ = fake
    schema = client.get("/openapi.json").json()
    company_params = {
        item["name"]: item for item in schema["paths"]["/v1/google/company/ads"]["get"]["parameters"]
    }
    assert company_params["topic"]["schema"]["enum"] == ["all", "political"]
    assert company_params["platform"]["schema"]["enum"] == sorted(google_map.PLATFORM_IDS)
    assert company_params["format"]["schema"]["enum"] == sorted(google_map.FORMAT_IDS)
    assert company_params["get_ad_details"]["schema"]["enum"] == ["true", "false"]
    search_params = {
        item["name"]: item
        for item in schema["paths"]["/v1/google/adLibrary/advertisers/search"]["get"]["parameters"]
    }
    assert search_params["region"]["schema"]["enum"] == sorted(google_map.REGIONS)
    assert company_params["topic"]["description"]
    assert search_params["region"]["description"]


def test_company_hydrates_details(fake):
    client, ie = fake
    response = client.get("/v1/google/company/ads", params={
        "domain": "lululemon.com", "get_ad_details": "true",
    })
    assert response.status_code == 200
    assert response.json()["ads"][0]["overallImpressions"]["min"] == "500000"
    assert sum(method == "LookupService/GetCreativeById" for method, _ in ie.requests) == 5
    search_payload = next(payload for method, payload in ie.requests if method == "SearchService/SearchCreatives")
    assert search_payload["2"] == 10


@pytest.mark.parametrize(("path", "params", "message"), [
    ("/v1/google/company/ads", {}, "Provide one of"),
    ("/v1/google/company/ads", {"domain": "not a domain"}, "valid domain"),
    ("/v1/google/company/ads", {"domain": "example.com", "topic": "political"}, "region is required"),
    ("/v1/google/company/ads", {"domain": "example.com", "get_ad_details": "yes"}, "true or false"),
    ("/v1/google/ad", {"url": "https://example.com/ad"}, "Transparency creative URL"),
    ("/v1/google/adLibrary/advertisers/search", {"query": "x", "region": "ZZ"}, "country code"),
])
def test_validation(fake, path, params, message):
    client, _ = fake
    response = client.get(path, params=params)
    assert response.status_code == 400
    assert message in response.json()["message"]


def test_unknown_ad_and_advertiser_are_404(fake):
    client, ie = fake
    ie.responses["LookupService/GetCreativeById"] = fixture("fixture-bad-creative.json")
    url = "https://adstransparency.google.com/advertiser/AR00000000000000000000/creative/CR00000000000000000000"
    assert client.get("/v1/google/ad", params={"url": url}).status_code == 404
    ie.responses["SearchService/SearchCreatives"] = fixture("fixture-bad-advertiser.json")
    response = client.get("/v1/google/company/ads", params={"advertiser_id": "AR00000000000000000000"})
    assert response.status_code == 404


def test_preview_url_is_strictly_allowlisted():
    assert google_ads._preview_url("https://displayads-formats.googleusercontent.com/a")
    assert google_ads._preview_url("http://127.0.0.1/x?displayads-formats.googleusercontent.com") is None
    assert google_ads._preview_url("https://displayads-formats.googleusercontent.com.evil.test/a") is None
    assert google_ads._preview_url("https://displayads-formats.googleusercontent.com:444/a") is None


def test_preview_fields_decodes_renderer_markup():
    body = r'<div class=\x22headline\x22><span>Free Shipping &amp; Returns</span></div>' \
           r'<div class=\x22long-description\x22><span>Never Miss A Drop<br>Today</span></div>'
    fields = google_map.preview_fields(body)
    assert fields["headline"] == "Free Shipping & Returns"
    assert fields["description"] == "Never Miss A Drop Today"
    assert fields["allText"] == "Free Shipping & Returns Never Miss A Drop Today"


def test_missing_optional_detail_fields_are_null():
    mapped = google_map.detail({"1": "AR1", "2": "CR1", "7": None, "17": None}, [], "https://example.test")
    assert mapped["overallImpressions"] is None
    assert mapped["spend"] is None
    assert mapped["targeting"] is None
    assert mapped["creativeRegions"] == [] and mapped["regionStats"] == []


@pytest.mark.parametrize("method", [
    "SearchService/SearchSuggestions", "SearchService/SearchCreatives", "LookupService/GetCreativeById",
])
def test_rpc_error_envelopes_are_not_successes(fake, method):
    client, ie = fake
    ie.responses[method] = {"error": {"code": 429, "message": "rate limited"}}
    if method == "SearchService/SearchSuggestions":
        response = client.get("/v1/google/adLibrary/advertisers/search", params={"query": "x"})
    elif method == "SearchService/SearchCreatives":
        response = client.get("/v1/google/company/ads", params={"domain": "example.com"})
    else:
        response = client.get("/v1/google/ad", params={"url":
            "https://adstransparency.google.com/advertiser/AR00000000000000000000/creative/CR00000000000000000000"})
    assert response.status_code == 503
    assert response.json()["error"] == "upstream_blocked"


def test_malformed_nested_fields_do_not_crash(fake):
    client, ie = fake
    ie.responses["SearchService/SearchSuggestions"] = {"1": [{"1": {"1": "x", "2": "AR1", "3": "US", "4": "changed"}}]}
    response = client.get("/v1/google/adLibrary/advertisers/search", params={"query": "x"})
    assert response.status_code == 200 and response.json()["advertisers"][0]["number_of_ads_estimate"] == 0
    ie.responses["SearchService/SearchCreatives"] = {"1": [{"1": "AR1", "2": "CR1", "3": {"1": "changed"}}], "5": "bad"}
    response = client.get("/v1/google/company/ads", params={"domain": "example.com"})
    assert response.status_code == 500 and response.json()["error"] == "upstream_error"


SEARCH = ("/v1/google/adLibrary/advertisers/search", {"query": "lululemon", "region": "AU"})


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(google_ads, "_now", lambda: now[0])
    return now


def test_captcha_refusal_cools_the_egress_and_sets_retry_after(fake, clock):
    """A flagged IP is asked once per window: the burst behind it fails fast instead of re-tripping the check."""
    client, ie = fake
    ie.responses["SearchService/SearchSuggestions"] = refusal()
    first = client.get(*SEARCH[:1], params=SEARCH[1])
    assert first.status_code == 503 and first.json()["error"] == "upstream_blocked"
    assert first.headers["Retry-After"] == "60" and "429" in first.json()["message"]
    assert len(ie.requests) == 1
    burst = [client.get(*SEARCH[:1], params=SEARCH[1]),
             client.get("/v1/google/company/ads", params={"domain": "lululemon.com", "region": "US"}),
             client.get("/v1/google/ad", params={"url": AD_URL})]
    assert [r.status_code for r in burst] == [503, 503, 503]
    assert {r.headers["Retry-After"] for r in burst} == {"60"}
    assert len(ie.requests) == 1  # none of the burst reached Google
    clock[0] += 30
    assert client.get(*SEARCH[:1], params=SEARCH[1]).headers["Retry-After"] == "30"
    clock[0] += 31  # the window is over; Google still refuses, so the next one is twice as long
    again = client.get(*SEARCH[:1], params=SEARCH[1])
    assert again.headers["Retry-After"] == "120" and len(ie.requests) == 2
    clock[0] += 121
    ie.responses["SearchService/SearchSuggestions"] = fixture("fixture-advertiser-search.json")
    assert client.get(*SEARCH[:1], params=SEARCH[1]).status_code == 200
    assert google_ads._cooling == {}  # a clean answer ends the cooling, so a later refusal starts at 60 again
    ie.responses["SearchService/SearchSuggestions"] = refusal(retry_after="300")
    assert client.get(*SEARCH[:1], params=SEARCH[1]).headers["Retry-After"] == "300"  # Google's own wait wins


@pytest.mark.parametrize("answer", [
    "<html>unusual traffic</html>",  # the CAPTCHA page answered 200 at the end of the redirect
    {"error": {"code": 429, "message": "Too Many Requests"}},  # the RPC's own refusal envelope
])
def test_other_refusal_shapes_also_cool(fake, clock, answer):
    client, ie = fake
    ie.responses["SearchService/SearchSuggestions"] = answer
    if isinstance(answer, str):
        ie.final_url = SORRY
    response = client.get(*SEARCH[:1], params=SEARCH[1])
    assert response.status_code == 503 and response.headers["Retry-After"] == "60"
    assert client.get(*SEARCH[:1], params=SEARCH[1]).status_code == 503 and len(ie.requests) == 1


@pytest.mark.parametrize(("body", "status", "error"), [
    ("", 503, "upstream_blocked"),  # an empty 200: throttled, but not a CAPTCHA
    ("<!doctype html><title>Before you continue</title>", 503, "upstream_blocked"),  # consent interstitial
    ('{"1": [', 500, "upstream_error"),  # cut-off JSON
])
def test_unusable_rpc_bodies_are_classified(fake, body, status, error):
    client, ie = fake
    ie.responses["SearchService/SearchSuggestions"] = body
    ie.final_url = "https://consent.google.com/ml?continue=x" if body.startswith("<") else None
    response = client.get(*SEARCH[:1], params=SEARCH[1])
    assert response.status_code == status and response.json()["error"] == error
    assert "Retry-After" not in response.headers and google_ads._cooling == {}
    if body.startswith("<"):
        assert "consent.google.com" in response.json()["message"]


def test_non_refusal_http_errors_do_not_cool():
    ie = FakeIE({"SearchService/SearchSuggestions": refusal(status=500, url=google_ads._RPC + "x")})
    with pytest.raises(ExtractorError):  # left to with_ydl's translate_error, as before
        google_ads._post(FakeYDL(ie), "SearchService/SearchSuggestions", {}, "advertiser suggestions")
    assert google_ads._cooling == {}


def test_refused_proxy_rotates_to_the_next_with_its_own_window(monkeypatch, clock):
    """with_ydl's proxy rotation: a cooling proxy is skipped without a request, the next one still serves."""
    from socialscrape import portal, ytdlp

    proxies = ["http://p1.test:8000", "http://p2.test:8000"]
    ies = {proxy: FakeIE({"SearchService/SearchSuggestions": fixture("fixture-advertiser-search.json")})
           for proxy in proxies}
    ies[proxies[0]].responses["SearchService/SearchSuggestions"] = refusal()
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: (proxies, []))
    monkeypatch.setattr(ytdlp, "_run", lambda platform, fn, options: fn(
        FakeYDL(ies[options["_dashboard_proxy"]], options["_dashboard_proxy"])))
    client = TestClient(create_app())
    for _ in range(2):
        response = client.get(*SEARCH[:1], params=SEARCH[1])
        assert response.status_code == 200 and response.json()["advertisers"]
    assert len(ies[proxies[0]].requests) == 1 and len(ies[proxies[1]].requests) == 2
    ies[proxies[1]].responses["SearchService/SearchSuggestions"] = refusal()
    response = client.get(*SEARCH[:1], params=SEARCH[1])
    assert response.status_code == 503 and response.headers["Retry-After"] == "60"
    assert "p1.test" not in response.text and "p2.test" not in response.text


def _detail_with_previews(ie: FakeIE, urls: list[str]) -> None:
    record = fixture("fixture-ad-detail-political.json")
    record["1"]["5"] = [{"1": {"4": url}} for url in urls]
    ie.responses["LookupService/GetCreativeById"] = record


PREVIEW = "https://displayads-formats.googleusercontent.com/ads/preview/content.js?creativeId="
COPY = r"<div class=\x22headline\x22><span>Free Shipping</span></div>"


def test_previews_are_fetched_once_and_skipped_after_a_refusal(fake):
    client, ie = fake
    _detail_with_previews(ie, [PREVIEW + "1", PREVIEW + "1", PREVIEW + "2", PREVIEW + "3"])
    ie.previews = {PREVIEW + "1": COPY, PREVIEW + "2": refusal(), PREVIEW + "3": COPY}
    response = client.get("/v1/google/ad", params={"url": AD_URL})
    assert response.status_code == 200
    variations = response.json()["variations"]
    assert [v["headline"] for v in variations] == ["Free Shipping", "Free Shipping", None, None]
    assert ie.preview_requests == [PREVIEW + "1", PREVIEW + "2"]  # no repeat, nothing after the refusal


def test_a_missing_preview_only_nulls_its_copy(fake):
    client, ie = fake
    _detail_with_previews(ie, [PREVIEW + "gone", PREVIEW + "1"])
    ie.previews = {PREVIEW + "gone": refusal(status=404, url=PREVIEW + "gone"), PREVIEW + "1": COPY}
    response = client.get("/v1/google/ad", params={"url": AD_URL})
    assert response.status_code == 200
    assert [v["headline"] for v in response.json()["variations"]] == [None, "Free Shipping"]


def test_hydration_refused_midway_is_a_503_not_a_short_page(fake):
    client, ie = fake
    ie.responses["LookupService/GetCreativeById"] = deque(
        [fixture("fixture-ad-detail-political.json"), refusal(), fixture("fixture-ad-detail-political.json")])
    response = client.get("/v1/google/company/ads", params={"domain": "lululemon.com", "get_ad_details": "true"})
    assert response.status_code == 503 and response.headers["Retry-After"] == "60"
    assert sum(method == "LookupService/GetCreativeById" for method, _ in ie.requests) == 2


def test_advertiser_id_wins_over_domain(fake):
    # ScrapeCreators' own example sends both; the advertiser id is the more specific input.
    client, ie = fake
    response = client.get("/v1/google/company/ads", params={
        "domain": "lululemon.com", "advertiser_id": "AR01614014350098432001"})
    assert response.status_code == 200, response.text
    payload = ie.requests[-1][1]
    assert payload["3"]["13"]["1"] == ["AR01614014350098432001"] and "12" not in payload["3"]
