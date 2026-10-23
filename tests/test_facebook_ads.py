from __future__ import annotations

import json
from pathlib import Path
import pytest

from socialscrape.errors import BadRequest, NotFound, UpstreamBlocked, UpstreamError
from socialscrape.platforms import _facebook_ads_map as M
from socialscrape.platforms import facebook_ads as F
from tests.contract import assert_conforms

FIXTURES = Path(__file__).parent / "fixtures" / "facebook_ads"
DETAIL_FIXTURE = FIXTURES / "ad-detail.json"
SEARCH_FIXTURE = FIXTURES / "search-page1.json"
COMPANY_FIXTURE = FIXTURES / "company-search.json"


def test_id_url_parsing():
    assert M.ad_id("123", None) == "123"
    assert M.ad_id(None, "https://www.facebook.com/ads/library/?country=US&id=123") == "123"
    assert M.ad_id(None, "facebook.com/ads/library?id=123") == "123"
    assert M.ad_id("123", "https://facebook.com/ads/library?id=123") is None
    assert M.ad_id(None, "https://example.com/ads/library?id=123") is None
    assert M.ad_id("１２３", None) is None


def test_detail_mapping_and_video_selection():
    archive = json.loads(DETAIL_FIXTURE.read_text())["archive"]
    result = M.detail(archive)
    assert result["adArchiveID"] == 1046730950862281
    assert result["pageID"] == 146705838515566
    assert result["publisherPlatform"] == ["instagram"]
    assert result["startDateString"].endswith(".000Z")
    assert M.video_url(archive).startswith("https://video-")
    trimmed = M.detail(archive, trim=True)
    assert "videos" in trimmed["snapshot"] and "extra_videos" not in trimmed["snapshot"]


def test_search_mapping_and_trim():
    fixture = json.loads(SEARCH_FIXTURE.read_text())
    ad = M.decorate_ad(fixture["ads"][0])
    assert ad["political_countries"] == []
    assert ad["url"].endswith(ad["ad_archive_id"])
    assert ad["start_date_string"].endswith("Z")
    assert "menu_items" not in M.trim_ad(ad)


def test_archive_parser_and_not_found():
    archive = json.loads(DETAIL_FIXTURE.read_text())["archive"]
    html = '<script data-sjs>' + json.dumps({"ScheduledServerJS": {"deeplink_ad_archive": archive}}) + "</script>"
    assert F._archive(html, archive["ad_archive_id"])["page_name"] == "Sukeban World"
    with pytest.raises(NotFound):
        F._archive(html, "999")
    with pytest.raises(UpstreamBlocked):
        F._archive("<title>Log in to Facebook</title>", "999")


@pytest.mark.parametrize("kwargs", [
    {"query": "x", "country": "ZZ"},
    {"query": "x", "status": "PAUSED"},
    {"query": "x", "media_type": "AUDIO"},
    {"query": "x", "language": "english"},
    {"query": "x", "language": "ZZ"},
    {"query": "x", "start_date": "01-01-2025"},
    {"query": "x", "start_date": "2025-02-29"},
    {"query": "x", "end_date": "2025-13-01"},
    {"query": "x", "start_date": "2025-02-01", "end_date": "2025-01-01"},
])
def test_search_validation(kwargs):
    values = {"page_id": None, "country": None, "status": None, "media_type": None,
              "language": None, "sort_by": None, **kwargs}
    with pytest.raises(BadRequest):
        F._params(values.pop("query"), **values)


def test_filter_defaults():
    params = F._params("nike", page_id=None, country=None, status=None, media_type=None,
                       language=None, sort_by=None)
    assert params["active_status"] == "active"
    assert params["country"] == "ALL"
    assert params["media_type"] == "all"
    assert params["sort_data[mode]"] == "total_impressions"
    assert params["sort_data[direction]"] == "desc"


def test_filter_object_query_serialization():
    params = F._params("nike", page_id=None, country=None, status=None, media_type=None,
                       language=None, sort_by="relevancy_monthly_grouped",
                       start_date="2025-01-01", end_date="2025-01-31")
    assert params["sort_data[mode]"] == "relevancy_monthly_grouped"
    assert params["start_date[min]"] == "2025-01-01"
    assert params["start_date[max]"] == "2025-01-31"
    assert "sort_data" not in params and "start_date" not in params


async def _fake_with_ydl(platform, fn, **opts):
    return fn(None)


def test_malformed_edge_is_upstream_error():
    with pytest.raises(UpstreamError):
        F._ads({"edges": [{"node": "unexpected"}]})


def test_graphql_invalid_cursor_is_bad_request():
    class IE:
        def _download_json(self, *args, **kwargs):
            return {"errors": [{"message": "Invalid cursor supplied"}]}

    with pytest.raises(BadRequest) as exc:
        F._graphql(IE(), "url", '\"LSD\",[],{\"token\":\"token\"}', "ads", "query", "1", {},
                   pagination=True)
    assert exc.value.code == "bad_cursor"


def test_repeated_first_page_is_bad_cursor(monkeypatch):
    fixture = json.loads(SEARCH_FIXTURE.read_text())
    ad = fixture["ads"][0]
    connection = {"edges": [{"node": {"collated_results": [ad]}}],
                  "page_info": {"end_cursor": "same"}}
    html = "shell"
    monkeypatch.setattr(F, "_ie", lambda ydl: object())
    monkeypatch.setattr(F, "_page", lambda *args: html)
    monkeypatch.setattr(F, "_search_connection", lambda value: ({}, connection))
    monkeypatch.setattr(F, "_graphql", lambda *args, **kwargs: {
        "data": {"ad_library_main": {"search_results_connection": connection}}})
    with pytest.raises(BadRequest) as exc:
        F._fetch_search(None, {"q": "nike"}, "tampered")
    assert exc.value.code == "bad_cursor"


def test_search_get_and_post(client, monkeypatch):
    fixture = json.loads(SEARCH_FIXTURE.read_text())
    connection = {"count": fixture["count"], "page_info": fixture["page_info"]}
    monkeypatch.setattr(F, "with_ydl", _fake_with_ydl)
    monkeypatch.setattr(F, "_fetch_search", lambda ydl, params, cursor: (connection, [M.decorate_ad(fixture["ads"][0])]))
    for method in ("get", "post"):
        response = (client.get("/v1/facebook/adLibrary/search/ads", params={"query": "nike"})
                    if method == "get" else
                    client.post("/v1/facebook/adLibrary/search/ads", json={"query": "nike"}))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["searchResultsCount"] == fixture["count"]
        assert_conforms(body, "/v1/facebook/adLibrary/search/ads", min_coverage=0.75)


def test_company_get_and_post(client, monkeypatch):
    fixture = json.loads(SEARCH_FIXTURE.read_text())
    connection = {"page_info": fixture["page_info"]}
    monkeypatch.setattr(F, "with_ydl", _fake_with_ydl)
    monkeypatch.setattr(F, "_fetch_search", lambda ydl, params, cursor: (connection, [M.decorate_ad(fixture["ads"][0])]))
    for method in ("get", "post"):
        response = (client.get("/v1/facebook/adLibrary/company/ads", params={"pageId": "15087023444"})
                    if method == "get" else
                    client.post("/v1/facebook/adLibrary/company/ads", json={"pageId": "15087023444"}))
        assert response.status_code == 200, response.text
        assert response.json()["results"]


def test_company_search_mapping(client, monkeypatch):
    response = json.loads(COMPANY_FIXTURE.read_text())["response"]

    class IE:
        def _download_json(self, *args, **kwargs):
            return response

    monkeypatch.setattr(F, "_ie", lambda ydl: IE())
    monkeypatch.setattr(F, "_page", lambda *args: '"LSD",[],{"token":"token"}')
    monkeypatch.setattr(F, "with_ydl", _fake_with_ydl)
    result = client.get("/v1/facebook/adLibrary/search/companies", params={"query": "nike"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["searchResults"][0]["page_id"] == "15087023444"
    assert_conforms(body, "/v1/facebook/adLibrary/search/companies", min_coverage=0.9)


def test_detail_and_image_transcript(client, monkeypatch):
    archive = json.loads(DETAIL_FIXTURE.read_text())["archive"]
    image = dict(archive, snapshot={"images": [{}], "page_name": archive["page_name"]})

    async def detail(_):
        return archive

    monkeypatch.setattr(F, "_detail_archive", detail)
    result = client.get("/v1/facebook/adLibrary/ad", params={"id": archive["ad_archive_id"]})
    assert result.status_code == 200
    assert result.json()["adArchiveID"] == int(archive["ad_archive_id"])

    async def image_detail(_):
        return image

    monkeypatch.setattr(F, "_detail_archive", image_detail)
    result = client.get("/v1/facebook/adLibrary/ad/transcript", params={"id": archive["ad_archive_id"]})
    assert result.status_code == 200
    assert result.json()["data"]["transcript_available"] is False


def test_bundle_discovery_only_accepts_declared_meta_scripts():
    allowed = "https://static.xx.fbcdn.net/rsrc.php/v4/test.js?hash=1&amp;lang=en"
    html = (f'<script src="{allowed}"></script>'
            '<script src="https://evil.example/fbcdn.net/rsrc.php/x.js"></script>'
            '<img src="https://static.xx.fbcdn.net/rsrc.php/v4/image.js">'
            'https://static.xx.fbcdn.net/rsrc.php/v4/reflected.js')
    assert F._bundle_urls(html) == [allowed.replace("&amp;", "&")]


def test_bundle_discovery_rejects_redirect_off_meta():
    bundle_url = "https://static.xx.fbcdn.net/rsrc.php/v4/test.js"
    operation = "AdLibrarySearchPaginationQuery"

    class IE:
        def _download_webpage_handle(self, *args, **kwargs):
            bundle = 'AdLibrarySearchPaginationQuery_facebookRelayOperation exports="987654321"'
            return bundle, type("Response", (), {"url": "https://evil.example/bundle.js"})()

    with pytest.raises(UpstreamError):
        F._discover_doc_id(IE(), "shell", f'<script src="{bundle_url}"></script>', "ad", operation)


def test_relay_operation_parser_and_stale_document_retry(monkeypatch):
    operation = "AdLibrarySearchPaginationQuery"
    bundle_url = "https://static.xx.fbcdn.net/rsrc.php/v4/test.js"
    html = ('"LSD",[],{"token":"token"}'
            f'<script src="{bundle_url.replace("&", "&amp;")}"></script>')
    bundle = ('__d("AdLibrarySearchPaginationQuery_facebookRelayOperation",[], '
              '(function(t,n,r,o,a,i){a.exports="987654321"}),null);')
    assert F._operation_id(bundle, operation) == "987654321"

    class IE:
        def __init__(self):
            self.ids = []

        def _download_json(self, *args, **kwargs):
            from urllib.parse import parse_qs
            doc_id = parse_qs(kwargs["data"].decode())["doc_id"][0]
            self.ids.append(doc_id)
            if doc_id == "stale":
                return {"errors": [{"message": "The GraphQL document with ID stale was not found."}]}
            return {"data": {"ok": True}}

        def _download_webpage_handle(self, url, *args, **kwargs):
            assert url == bundle_url
            return bundle, type("Response", (), {"url": url})()

    F._DOC_IDS.clear()
    ie = IE()
    assert F._graphql(ie, "url", html, "ads", operation, "stale", {})["data"]["ok"] is True
    assert ie.ids == ["stale", "987654321"]
    assert F._DOC_IDS[operation] == "987654321"


@pytest.mark.parametrize("errors", [
    [{"message": "Rate limit exceeded"}],
    [{"message": "Rate limit exceeded; GraphQL document with ID 1 was not found."}],
    [{"message": "GraphQL document with ID 1 was not found."},
     {"message": "Authentication required"}],
])
def test_blocked_graphql_errors_do_not_refresh(monkeypatch, errors):
    class IE:
        def _download_json(self, *args, **kwargs):
            return {"errors": errors}

    monkeypatch.setattr(F, "_discover_doc_id", lambda *args: pytest.fail("must not refresh"))
    F._DOC_IDS.clear()
    with pytest.raises(UpstreamBlocked):
        F._graphql(IE(), "url", '"LSD",[],{"token":"token"}', "ads",
                   "AdLibrarySearchPaginationQuery", "1", {})


def test_mixed_stale_graphql_errors_do_not_refresh(monkeypatch):
    errors = [{"message": "GraphQL document with ID 1 was not found."},
              {"message": "Another upstream failure"}]

    class IE:
        def _download_json(self, *args, **kwargs):
            return {"errors": errors}

    monkeypatch.setattr(F, "_discover_doc_id", lambda *args: pytest.fail("must not refresh"))
    F._DOC_IDS.clear()
    with pytest.raises(UpstreamError):
        F._graphql(IE(), "url", '"LSD",[],{"token":"token"}', "ads",
                   "AdLibrarySearchPaginationQuery", "1", {})


def test_facebook_get_openapi_documents_enums(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/v1/facebook/adLibrary/search/ads", "/v1/facebook/adLibrary/company/ads"):
        params = {item["name"]: item for item in paths[path]["get"]["parameters"]}
        assert params["sort_by"]["schema"]["enum"] == ["total_impressions", "relevancy_monthly_grouped"]
        assert params["status"]["schema"]["enum"] == ["ALL", "ACTIVE", "INACTIVE"]
        assert params["media_type"]["description"]
    search = {item["name"]: item for item in paths["/v1/facebook/adLibrary/search/ads"]["get"]["parameters"]}
    assert search["search_type"]["schema"]["enum"] == ["keyword_unordered", "keyword_exact_phrase"]
    assert search["ad_type"]["schema"]["enum"] == ["all", "political_and_issue_ads"]
