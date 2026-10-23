"""Live logged-out checks for the Facebook Ad Library routes."""

from __future__ import annotations

import pytest

from tests.contract import assert_conforms
from tests.e2e import skip_if_blocked, spaced_get

AD = "/v1/facebook/adLibrary/ad"
TRANSCRIPT = "/v1/facebook/adLibrary/ad/transcript"
SEARCH = "/v1/facebook/adLibrary/search/ads"
COMPANY = "/v1/facebook/adLibrary/company/ads"
COMPANIES = "/v1/facebook/adLibrary/search/companies"
VIDEO_AD = "1847168433318898"
IMAGE_AD = "914467831604972"
MISSING_AD = "999999999999999999"
NIKE_PAGE = "15087023444"


def get(client, path, **params):
    response = spaced_get(client, path, 1.0, **params)
    skip_if_blocked(response)
    return response


def ok(client, path, **params):
    response = get(client, path, **params)
    assert response.status_code == 200, (path, params, response.text[:500])
    body = response.json()
    assert body["success"] is True and body["credits_charged"] == 0
    return body


@pytest.mark.live
@pytest.mark.parametrize("ident", [VIDEO_AD, IMAGE_AD])
def test_ad_details(client, ident):
    body = ok(client, AD, id=ident)
    assert_conforms(body, AD, min_coverage=0.6)
    assert str(body["adArchiveID"]) == ident


@pytest.mark.live
def test_ad_detail_url_and_not_found(client):
    body = ok(client, AD, url=f"https://www.facebook.com/ads/library/?country=US&id={VIDEO_AD}", trim="true")
    assert str(body["adArchiveID"]) == VIDEO_AD
    response = get(client, AD, id=MISSING_AD)
    assert response.status_code == 404 and response.json()["error"] == "ad_not_found"


@pytest.mark.live
@pytest.mark.parametrize("query", ["nike", "adidas", "coffee"])
def test_search_first_page(client, query):
    body = ok(client, SEARCH, query=query, country="US")
    assert_conforms(body, SEARCH, min_coverage=0.72)
    assert isinstance(body["searchResultsCount"], int)


@pytest.mark.live
def test_search_sort_and_date_filters(client):
    common = {"query": "nike", "country": "US", "status": "ACTIVE"}
    impressions = ok(client, SEARCH, **common, sort_by="total_impressions")
    relevancy = ok(client, SEARCH, **common, sort_by="relevancy_monthly_grouped")
    ids = lambda body: [ad.get("ad_archive_id") for ad in body["searchResults"]]
    assert ids(impressions) != ids(relevancy)
    dated = ok(client, SEARCH, **common, start_date="2020-01-01", end_date="2020-01-31")
    assert all("2020-01-01" <= ad["start_date_string"][:10] <= "2020-01-31"
               for ad in dated["searchResults"])


@pytest.mark.live
def test_search_pagination_and_post(client):
    first = ok(client, SEARCH, query="nike", country="US")
    assert first["cursor"]
    second = ok(client, SEARCH, query="nike", country="US", cursor=first["cursor"])
    assert second["searchResults"]
    response = client.post(SEARCH, json={"query": "nike", "country": "US", "cursor": first["cursor"]})
    skip_if_blocked(response)
    assert response.status_code == 200
    assert_conforms(response.json(), SEARCH, min_coverage=0.72)


@pytest.mark.live
@pytest.mark.parametrize("params", [{"pageId": NIKE_PAGE}, {"companyName": "Nike"},
                                      {"pageId": NIKE_PAGE, "media_type": "VIDEO"}])
def test_company_ads(client, params):
    body = ok(client, COMPANY, **params)
    assert_conforms(body, COMPANY, min_coverage=0.72)


@pytest.mark.live
def test_company_ads_pagination_and_post(client):
    first = ok(client, COMPANY, pageId=NIKE_PAGE)
    assert first["cursor"]
    second = ok(client, COMPANY, pageId=NIKE_PAGE, cursor=first["cursor"])
    assert second["results"]
    response = client.post(COMPANY, json={"pageId": NIKE_PAGE, "cursor": first["cursor"]})
    skip_if_blocked(response)
    assert response.status_code == 200
    assert_conforms(response.json(), COMPANY, min_coverage=0.72)


@pytest.mark.live
@pytest.mark.parametrize("query", ["nike", "adidas", "coca cola"])
def test_company_search(client, query):
    body = ok(client, COMPANIES, query=query)
    assert_conforms(body, COMPANIES, min_coverage=0.85)
    assert body["searchResults"]


@pytest.mark.live
def test_transcript_route_shape(client):
    response = get(client, TRANSCRIPT, id=VIDEO_AD)
    if response.status_code == 501:
        assert response.json()["error"] in {"transcription_disabled", "transcription_unavailable"}
        pytest.skip("local transcription backend is not enabled")
    assert response.status_code == 200, response.text[:500]
    assert_conforms(response.json(), TRANSCRIPT, min_coverage=0.9)


@pytest.mark.live
def test_transcription_with_available_backend(client):
    from socialscrape import transcribe

    if not transcribe.enabled():
        pytest.skip("local transcription backend is not enabled")
    dedicated = ok(client, TRANSCRIPT, id=VIDEO_AD)["data"]
    inline = ok(client, AD, id=VIDEO_AD, get_transcript="true")
    assert dedicated["transcript_available"] is True
    assert dedicated["transcript"]
    assert inline["transcript"] == dedicated["transcript"]

    image = ok(client, TRANSCRIPT, id=IMAGE_AD)["data"]
    assert image["transcript"] is None
    assert image["transcript_available"] is False
