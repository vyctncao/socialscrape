from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from socialscrape.main import create_app
from socialscrape.platforms import _google_ads_map as google_map
from socialscrape.platforms import google_ads
from tests.contract import assert_conforms

RAW = Path(__file__).parent / "fixtures" / "google_ads"


class FakeIE:
    def __init__(self, responses: dict[str, Any], preview: str = ""):
        self.responses = responses
        self.preview = preview
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def _download_json(self, url: str, _id: str, *, data: bytes, headers: dict[str, str]):
        payload = json.loads(urllib.parse.parse_qs(data.decode())["f.req"][0])
        method = url.split("/_/rpc/", 1)[1].split("?", 1)[0]
        self.requests.append((method, payload))
        return self.responses[method]

    def _download_webpage(self, url: str, _id: str):
        return self.preview


class FakeYDL:
    def __init__(self, ie: FakeIE):
        self.ie = ie

    def get_info_extractor(self, name: str):
        assert name == "Generic"
        return self.ie


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
