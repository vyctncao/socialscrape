from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from socialscrape.main import create_app
from tests.contract import assert_conforms

pytestmark = pytest.mark.live


def checked(response):
    if response.status_code == 503 and response.json().get("error") == "upstream_blocked":
        pytest.skip("Google Ads Transparency rate-limited this IP")
    return response


def test_google_advertiser_search_live():
    response = checked(TestClient(create_app()).get(
        "/v1/google/adLibrary/advertisers/search", params={"query": "lululemon", "region": "AU"}))
    assert response.status_code == 200, response.text
    assert_conforms(response.json(), "/v1/google/adLibrary/advertisers/search", min_coverage=.75)


def test_google_company_ads_and_pagination_live():
    client = TestClient(create_app())
    first = checked(client.get("/v1/google/company/ads", params={"advertiser_id": "AR01614014350098432001"}))
    assert first.status_code == 200, first.text
    body = first.json()
    assert_conforms(body, "/v1/google/company/ads", min_coverage=.75)
    if body.get("cursor"):
        second = checked(client.get("/v1/google/company/ads", params={
            "advertiser_id": "AR01614014350098432001", "cursor": body["cursor"]}))
        assert second.status_code == 200, second.text
        assert_conforms(second.json(), "/v1/google/company/ads", min_coverage=.75)


def test_google_ad_live():
    url = "https://adstransparency.google.com/advertiser/AR07541154843046445057/creative/CR17778900238552006657"
    response = checked(TestClient(create_app()).get("/v1/google/ad", params={"url": url}))
    assert response.status_code == 200, response.text
    assert_conforms(response.json(), "/v1/google/ad", min_coverage=.75)


def test_google_not_found_live():
    url = "https://adstransparency.google.com/advertiser/AR00000000000000000000/creative/CR00000000000000000000"
    response = checked(TestClient(create_app()).get("/v1/google/ad", params={"url": url}))
    assert response.status_code == 404, response.text
