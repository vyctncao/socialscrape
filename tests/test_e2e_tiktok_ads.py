"""Live contract checks for TikTok's Commercial Content Library and Creative Center."""

from __future__ import annotations

import pytest

from tests.contract import assert_conforms
from tests.e2e import skip_if_blocked, spaced_get

SEARCH = "/v1/tiktok/ad-library/search"
AD = "/v1/tiktok/ad-library/ad"
CURRENT_TOP_AD = "7679096403545178120"
MISSING_AD = "1000000000000000000"


def get(client, path, **params):
    response = spaced_get(client, path, 1.5, **params)
    skip_if_blocked(response)
    return response


@pytest.mark.live
@pytest.mark.parametrize("params", [
    {"query": "running shoes"},
    {"query": "coffee"},
    {"advertiser_name": "Gymshark"},
])
def test_ad_library_search(client, params):
    response = get(client, SEARCH, **params)
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert_conforms(body, SEARCH, min_coverage=0.75)
    assert isinstance(body["ads"], list) and isinstance(body["has_more"], bool)


@pytest.mark.live
def test_ad_library_search_second_page(client):
    first = get(client, SEARCH, query="fashion")
    assert first.status_code == 200, first.text[:500]
    cursor = first.json().get("cursor")
    if not cursor:
        pytest.skip("TikTok returned no second search page")
    second = get(client, SEARCH, query="fashion", cursor=cursor)
    assert second.status_code == 200, second.text[:500]
    assert_conforms(second.json(), SEARCH, min_coverage=0.75)


@pytest.mark.live
@pytest.mark.parametrize("ad_id, coverage", [
    (CURRENT_TOP_AD, 0.4),
    # Library-only ads (no Creative Center detail, so fewer fields) age out of the library: refresh from a search
    ("1879780091329538", 0.38),
])
def test_ad_library_ad(client, ad_id, coverage):
    response = get(client, AD, ad_id=ad_id)
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert_conforms(body, AD, min_coverage=coverage)
    assert body["id"] == ad_id


@pytest.mark.live
def test_ad_library_ad_not_found(client):
    response = get(client, AD, ad_id=MISSING_AD)
    assert response.status_code == 404, response.text[:500]
    assert response.json()["error"] == "not_found"
