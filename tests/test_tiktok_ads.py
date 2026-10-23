from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from yt_dlp.utils import ExtractorError

from socialscrape.main import create_app
from socialscrape.cursor import encode_cursor
from socialscrape.platforms import tiktok_ads
from tests.contract import assert_conforms

SEARCH = "/v1/tiktok/ad-library/search"
AD = "/v1/tiktok/ad-library/ad"


class FakeIE:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def _download_json(self, url, *args, **kwargs):
        self.calls.append((url, kwargs))
        for suffix, response in self.responses.items():
            if url.endswith(suffix):
                if isinstance(response, BaseException):
                    raise response
                return response
        raise AssertionError(url)


class FakeYDL:
    def __init__(self, ie):
        self.ie = ie

    def get_info_extractor(self, name):
        assert name == "TikTok"
        return self.ie


@pytest.fixture
def install(monkeypatch):
    def apply(responses):
        ie = FakeIE(responses)

        async def fake_with_ydl(platform, fn, **kwargs):
            assert platform == "tiktok"
            return fn(FakeYDL(ie))

        monkeypatch.setattr(tiktok_ads, "with_ydl", fake_with_ydl)
        return ie
    return apply


@pytest.fixture
def client():
    with TestClient(create_app(), raise_server_exceptions=False) as value:
        yield value


def bootstrap():
    return {"config_str": "token", "regions": [{"code": "DE"}, {"code": "GB"}]}


def search_page(has_more=True):
    return {"code": 0, "total": 7, "has_more": has_more, "search_id": "native-search",
            "data": [{"id": "1879780091329538", "name": "GYMSHARK LTD", "audit_status": "1", "type": "2",
                      "first_shown_date": 1784937600000, "last_shown_date": 1785024000000,
                      "videos": [{"video_url": "https://cdn/video", "cover_img": "https://cdn/cover"}],
                      "estimated_audience": "1K-10K", "spent": "", "impression": 0, "show_mode": 1,
                      "image_urls": [], "rejection_info": None, "sor_audit_status": "1"}]}


def creative_detail():
    return {"code": 0, "msg": "OK", "data": {"ad_title": "A useful ad", "brand_name": "Brand",
            "comment": 1, "cost": 2, "country_code": ["US"], "ctr": 0.2, "favorite": False,
            "has_summary": False, "highlight_text": "", "id": "7679096403545178120", "industry_key": "label",
            "is_search": False, "keyword_list": None, "landing_page": "https://example.com", "like": 10,
            "objective_key": "reach", "objectives": [{"label": "reach", "value": 5}], "pattern_label": [],
            "share": 2, "source": "TikTok Ads Manager", "source_key": 1,
            "video_info": {"vid": "v1", "duration": 12.5, "cover": "https://cdn/cover",
                           "video_url": {"720p": "https://cdn/video"}, "width": 720, "height": 1280},
            "voice_over": False}}


def test_query_search_maps_results_and_cursor(client, install):
    ie = install({"support-regions": bootstrap(), "/search": search_page()})
    response = client.get(SEARCH, params={"query": "running shoes"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert_conforms(body, SEARCH, min_coverage=0.8)
    assert body["ads"][0]["url"].endswith("ad_id=1879780091329538")
    assert body["cursor"].startswith("ss1.")
    request = next(call for call in ie.calls if "/search" in call[0])
    sent = json.loads(request[1]["data"])
    assert sent["query_type"] == "1" and sent["offset"] == 0
    assert request[1]["query"]["region"] == "DE,GB"


def test_advertiser_search_resolves_and_paginates(client, install):
    ie = install({"support-regions": bootstrap(), "/suggestion": {"code": 0, "data": {"adv_names": [
        {"name": "GYMSHARK LTD", "ids": "7647240890083328016"}]}}, "/search": search_page()})
    first = client.get(SEARCH, params={"advertiser_name": "Gymshark"}).json()
    second = client.get(SEARCH, params={"advertiser_name": "Gymshark", "cursor": first["cursor"]})
    assert second.status_code == 200, second.text
    assert first["resolved_advertiser_name"] == "GYMSHARK LTD"
    assert first["ads"][0]["adv_biz_ids"] == "7647240890083328016"
    searches = [json.loads(kwargs["data"]) for url, kwargs in ie.calls if "/search" in url]
    assert searches[0]["query_type"] == "2"
    assert searches[1]["search_id"] == "native-search" and searches[1]["offset"] == 1


def test_pinned_advertiser_must_resolve(client, install):
    ie = install({"support-regions": bootstrap(), "/suggestion": {"code": 0, "data": {"adv_names": [
        {"name": "OTHER", "ids": "999"}]}}, "/search": search_page()})
    response = client.get(SEARCH, params={"advertiser_name": "Gymshark", "adv_biz_ids": "7078923208527618049"})
    assert response.status_code == 400
    assert "was not found" in response.json()["message"]
    assert not any("/search" in url for url, _ in ie.calls)


def test_cursor_is_bound_to_search(client, install):
    install({"support-regions": bootstrap(), "/search": search_page()})
    cursor = encode_cursor({"search_id": "attacker", "offset": 1})
    response = client.get(SEARCH, params={"query": "x", "cursor": cursor})
    assert response.status_code == 400 and response.json()["error"] == "bad_cursor"


@pytest.mark.parametrize("params,message", [
    ({}, "Provide exactly one"),
    ({"query": "x", "advertiser_name": "y"}, "Provide exactly one"),
    ({"query": "x", "adv_biz_ids": "12"}, "requires advertiser_name"),
    ({"advertiser_name": "x", "adv_biz_ids": "abc"}, "must be a numeric"),
    ({"query": "x", "cursor": "bad"}, "Invalid cursor"),
])
def test_search_validation(client, install, params, message):
    install({"support-regions": bootstrap(), "/search": search_page(), "/suggestion": {"data": {"adv_names": []}}})
    response = client.get(SEARCH, params=params)
    assert response.status_code == 400
    assert message in response.json()["message"]


@pytest.mark.parametrize("ad_id", [
    "https://ads.tiktok.com/business/creativecenter/topads/7679096403545178120/pc/en",
    "https://library.tiktok.com/ads/detail/?ad_id=7679096403545178120",
])
def test_ad_accepts_documented_urls(client, install, ad_id):
    install({"v2/detail": creative_detail(), "v2/detail_analysis": {"code": 0, "data": {}},
             "v2/recommend": {"code": 0, "data": {"materials": []}}})
    response = client.get(AD, params={"ad_id": ad_id})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "7679096403545178120"


def test_creative_center_ad_maps_all_sources(client, install):
    install({"v2/detail": creative_detail(), "v2/detail_analysis": {"code": 0, "data": {
                "detail_analysis": {"summary": "works"}, "interactive_time_analysis": {"ctr": {
                    "metric": "retain_ctr", "analysis": [{"second": 0, "value": 0.1}], "duration": 12,
                    "highlight": [1], "percentile": 0.9}}}},
             "v2/recommend": {"code": 0, "data": {"materials": []}}})
    response = client.get(AD, params={"ad_id": "7679096403545178120"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert_conforms(body, AD, min_coverage=0.45)
    assert body["id"] == "7679096403545178120"
    assert body["detail_analysis"] == {"summary": "works"}
    assert body["interactive_time_analysis"]["ctr"]["metric"] == "retain_ctr"


def test_non_top_ad_falls_back_to_library(client, install):
    library = {"code": 0, "data": {"ad": {"id": "1879780091329538", "title": "Library ad",
                "advertising_objective": "TRAFFIC", "videos": [{"video_url": "https://cdn/video",
                "cover_img": "https://cdn/cover"}]}, "advertiser": {"name": "Advertiser"},
                "targeting": {"location": {"data": [{"region": "DE"}]}}}}
    install({"v2/detail": {"code": 40004}, "support-regions": bootstrap(), "/details": library})
    response = client.get(AD, params={"ad_id": "1879780091329538"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert_conforms(body, AD, min_coverage=0.38)
    assert body["ad_title"] == "Library ad" and body["country_code"] == ["DE"]
    assert body["recommended_for_you"] == []


@pytest.mark.parametrize("ad_id", ["", "abc", "-1", "1.2", "1" * 26])
def test_ad_id_validation(client, ad_id):
    response = client.get(AD, params={"ad_id": ad_id})
    assert response.status_code == 400
    assert "numeric TikTok ad id" in response.json()["message"]


def test_optional_creative_calls_do_not_hide_detail(client, install):
    install({"v2/detail": creative_detail(), "v2/detail_analysis": ExtractorError("temporary"),
             "v2/recommend": ExtractorError("temporary")})
    response = client.get(AD, params={"ad_id": "7679096403545178120"})
    assert response.status_code == 200, response.text
    assert response.json()["detail_analysis"] is None
    assert response.json()["recommended_for_you"] == []


def test_library_missing_ad_is_404(client, install):
    install({"v2/detail": {"code": 40004}, "support-regions": bootstrap(),
             "/details": {"code": 404, "data": None}})
    response = client.get(AD, params={"ad_id": "1000000000000000000"})
    assert response.status_code == 404 and response.json()["error"] == "not_found"


@pytest.mark.parametrize("responses,params", [
    ({"support-regions": {"config_str": "token", "regions": None}}, {"query": "x"}),
    ({"support-regions": bootstrap(), "/suggestion": {"code": 0, "data": {"adv_names": None}}},
     {"advertiser_name": "x"}),
])
def test_nullable_upstream_collections_are_classified(client, install, responses, params):
    install(responses)
    response = client.get(SEARCH, params=params)
    assert response.status_code == 500 and response.json()["error"] == "upstream_error"


def test_bootstrap_block_is_classified(client, install):
    error = ExtractorError("system busy")
    install({"support-regions": error})
    response = client.get(SEARCH, params={"query": "x"})
    assert response.status_code == 503 and response.json()["error"] == "upstream_blocked"


def test_malformed_upstream_is_classified(client, install):
    install({"support-regions": bootstrap(), "/search": {"code": 0, "data": {}}})
    response = client.get(SEARCH, params={"query": "x"})
    assert response.status_code == 500 and response.json()["error"] == "upstream_error"


def test_openapi_documents_ad_library_parameters(client):
    paths = client.get("/openapi.json").json()["paths"]
    search = {item["name"]: item for item in paths[SEARCH]["get"]["parameters"]}
    assert "either query or advertiser_name" in search["query"]["description"]
    assert "typeahead" in search["advertiser_name"]["description"]
    assert "previous response" in search["cursor"]["description"]
    ad = {item["name"]: item for item in paths[AD]["get"]["parameters"]}
    assert "Top Ads material ID or URL" in ad["ad_id"]["description"]


def test_research_token_routes_through_the_official_api(client, install, monkeypatch):
    from socialscrape.platforms import tiktok_official
    sent = []

    def official(url, body):
        sent.append((url.split("?")[0], body))
        if url.split("?")[0].endswith("/ad/query/"):
            return {"data": {"ads": [{"id": 1879780091329538}], "search_id": "official-next", "has_more": True}}
        return {"data": {"ad": {"id": 1879780091329538, "title": "Official"}}}

    monkeypatch.setattr(tiktok_official, "research_token", lambda: "research-token")
    monkeypatch.setattr(tiktok_official, "_post", official)
    body = client.get(SEARCH, params={"query": "gymshark"}).json()
    assert body["ads"] == [{"id": 1879780091329538}] and body["cursor"] == "official-next"
    assert sent[0][0].endswith("/research/adlib/ad/query/") and sent[0][1]["search_term"] == "gymshark"
    # Creative Center still answers first for a Top Ad; the official API takes the public library's place after it.
    ie = install({"v2/detail": {"code": 40004}})
    response = client.get(AD, params={"ad_id": "1879780091329538"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Official" and body["id"] == "1879780091329538"
    assert sent[1][0].endswith("/research/adlib/ad/detail/") and sent[1][1] == {"ad_id": 1879780091329538}
    assert not any("library.tiktok.com" in url for url, _ in ie.calls)
