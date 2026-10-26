from __future__ import annotations

import json
from pathlib import Path
import pytest
from yt_dlp.utils import ExtractorError

from socialscrape.errors import BadRequest, NotFound, UpstreamBlocked, UpstreamError
from socialscrape.platforms import _facebook_ads_map as M
from socialscrape.platforms import facebook_ads as F
from tests.contract import assert_conforms

FIXTURES = Path(__file__).parent / "fixtures" / "facebook_ads"
DETAIL_FIXTURE = FIXTURES / "ad-detail.json"
DETAILS_FIXTURE = FIXTURES / "ad-details.json"  # AdLibraryV3AdDetailsQuery answers: EU-delivered and political
MISSING_FIXTURE = FIXTURES / "missing-ad-page.html"  # the deep-link page Meta serves for an unknown ad id
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
    with pytest.raises(NotFound):  # the app's landing page (deeplinkAdID null) is Meta's answer for an unknown id
        F._archive(html + MISSING_FIXTURE.read_text(), "999")
    with pytest.raises(UpstreamBlocked):
        F._archive("<title>Log in to Facebook</title>", "999")


@pytest.mark.parametrize("page", ["", "  \n", "<html><head><title>Facebook</title></head><body>Sorry, something went "
                                  "wrong.</body></html>"])
def test_a_page_without_the_ad_library_app_is_a_block(page):
    """verify-load fb-all-empty / facebook-all-html: an empty 200 or a block page without the words captcha/checkpoint/
    log in was a 404 ad_not_found (Stratton reads that as a missing ad), and a 500 on the search routes."""
    for read in (lambda: F._archive(page, "1046730950862281"), lambda: F._search_connection(page),
                 lambda: F._lsd(page)):
        with pytest.raises(UpstreamBlocked, match="without its app data"):
            read()
    # with the app rendered, a page without its token stays a 500 (the recorded landing page carries none)
    app = MISSING_FIXTURE.read_text()
    assert '"LSD"' not in app and F._PRELOADER.search(app)
    with pytest.raises(UpstreamError):
        F._lsd(app)


def test_missing_ad_landing_page_is_404_not_a_block():
    """Recorded 2026-10-24 for id 999999999999999999: Meta renders the Ad Library landing page (deeplinkAdID null),
    whose consent config lists arkose_captcha and google_recaptcha. That used to read as a 503 bot check."""
    page = MISSING_FIXTURE.read_text()
    assert "arkose_captcha" in page and '"deeplinkAdID":null' in page
    with pytest.raises(NotFound) as exc:
        F._archive(page, "999999999999999999")
    assert exc.value.code == "ad_not_found"
    shell_for_this_ad = page.replace('"deeplinkAdID":null', '"deeplinkAdID":999999999999999999')
    with pytest.raises(UpstreamBlocked):  # the app was asked for this ad yet sent none: not provably missing
        F._archive(shell_for_this_ad, "999999999999999999")


def test_detail_image_crops_follow_the_scrapecreators_object_shape():
    archive = {"ad_archive_id": "1", "snapshot": {"images": [
        {"image_crops": [], "original_image_url": "https://x/a.jpg"},
        {"image_crops": [{"x": 0, "y": 0}], "original_image_url": "https://x/b.jpg"}, "unexpected"]}}
    images = M.detail(archive)["snapshot"]["images"]
    assert images[0]["image_crops"] == {} and images[1]["image_crops"] == {"0": {"x": 0, "y": 0}}
    assert images[2] == "unexpected" and archive["snapshot"]["images"][0]["image_crops"] == []
    assert M.detail(archive, trim=True)["snapshot"]["images"][0]["image_crops"] == {}


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
    eu = json.loads(DETAILS_FIXTURE.read_text())["eu"]["response"]["data"]["ad_library_main"]["ad_details"]
    asked = []

    async def detail(_, *, details=False):
        asked.append(details)
        return archive, eu if details else None

    monkeypatch.setattr(F, "_detail_archive", detail)
    result = client.get("/v1/facebook/adLibrary/ad", params={"id": archive["ad_archive_id"]})
    assert result.status_code == 200
    body = result.json()
    assert body["adArchiveID"] == int(archive["ad_archive_id"])
    assert body["aaa_info"]["eu_total_reach"] == 20138
    assert_conforms(body, "/v1/facebook/adLibrary/ad", min_coverage=0.6)

    async def image_detail(_, *, details=False):
        asked.append(details)
        return image, None

    monkeypatch.setattr(F, "_detail_archive", image_detail)
    result = client.get("/v1/facebook/adLibrary/ad/transcript", params={"id": archive["ad_archive_id"]})
    assert result.status_code == 200
    assert result.json()["data"]["transcript_available"] is False
    assert asked == [True, False]  # the transcript route never pays for the details query


def details_case(name: str) -> tuple[dict, dict]:
    case = json.loads(DETAILS_FIXTURE.read_text())[name]
    return case["archive"], case["response"]


def test_aaa_info_merges_payer_data_with_eu_transparency():
    _, response = details_case("eu")
    info = M.aaa_info(response["data"]["ad_library_main"]["ad_details"])
    assert info["payer_beneficiary_data"] == [{"payer": "FigureO POD", "beneficiary": "FigureO POD"}]
    assert info["targets_eu"] is True and info["is_ad_taken_down"] is False
    assert info["eu_total_reach"] == 20138
    assert info["age_audience"] == {"min": 18, "max": 65} and info["gender_audience"] == "All"
    assert [row["name"] for row in info["location_audience"]] == ["Africa", "Europe", "South America"]
    first = info["age_country_gender_reach_breakdown"][0]
    assert first["country"] == "YT"
    assert {"age_range": "45-54", "male": 125, "female": 82, "unknown": None} in first["age_gender_breakdowns"]


def test_aaa_info_normalizes_political_delivery_shares_without_inventing():
    _, response = details_case("political")
    info = M.aaa_info(response["data"]["ad_library_main"]["ad_details"])
    assert info["location_audience"][-1] == {"name": "California", "reach": 0.99}
    assert all(set(row) == {"name", "reach"} for row in info["location_audience"])
    assert info["age_country_gender_reach_breakdown"] == [{"country": "US", "age_gender_breakdowns": [
        {"age_range": "18-24", "male": 0.13, "female": 0.13, "unknown": 0},
        {"age_range": "25-34", "male": 0.22, "female": 0.19, "unknown": 0},
        {"age_range": "35-44", "male": 0.16, "female": 0.16, "unknown": 0}]}]
    assert "eu_total_reach" not in info and "payer_beneficiary_data" not in info


def test_aaa_info_prefers_eu_then_uk_and_keeps_absolute_total_reach():
    uk = {"location_audience": [{"name": "United Kingdom", "num_obfuscated": 0, "type": "countries",
                                 "excluded": False}], "total_reach": 5120, "gender_audience": "All"}
    info = M.aaa_info({"aaa_info": None, "transparency_by_location": {
        "br_transparency": {"total_reach": 1}, "eu_transparency": None, "uk_transparency": uk}})
    assert info == uk
    assert M.aaa_info(None) is None
    assert M.aaa_info({"aaa_info": None, "insights": None,
                       "transparency_by_location": {"eu_transparency": None}}) is None
    assert M.detail({"ad_archive_id": "1"}, details=None)["aaa_info"] is None


class DetailsIE:
    def __init__(self, answer):
        self.answer, self.forms = answer, []

    def _download_json(self, url, ident, *, data, headers):
        from urllib.parse import parse_qs
        self.forms.append({key: values[0] for key, values in parse_qs(data.decode()).items()})
        assert headers["X-FB-Friendly-Name"] == "AdLibraryV3AdDetailsQuery"
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


@pytest.mark.parametrize("name", ["eu", "political"])
def test_ad_details_asks_like_the_pages_own_modal(name):
    archive, response = details_case(name)
    page = json.loads(DETAILS_FIXTURE.read_text())["page"]
    ie = DetailsIE(response)
    F._DOC_IDS.clear()
    details = F._ad_details(ie, "https://www.facebook.com/ads/library/?id=1", page, archive)
    assert details is response["data"]["ad_library_main"]["ad_details"]
    form = ie.forms[0]
    assert form["doc_id"] == F.DETAILS_DOC_ID and form["fb_api_req_friendly_name"] == "AdLibraryV3AdDetailsQuery"
    variables = json.loads(form["variables"])
    assert variables == {"adArchiveID": archive["ad_archive_id"], "pageID": archive["page_id"], "country": "US",
                         "sessionID": "70135ce7-6a00-4508-bb92-8a6912fe22da", "source": None,
                         "isAdNonPolitical": name != "political", "isAdNotAAAEligible": name != "eu"}


def test_ad_details_skips_unregulated_ads_without_a_request():
    ie = DetailsIE(pytest.fail)
    archive = {"ad_archive_id": "1", "page_id": "2", "is_aaa_eligible": False, "categories": ["UNKNOWN"]}
    assert F._ad_details(ie, "url", "", archive) is None and ie.forms == []


@pytest.mark.parametrize("answer", [
    {"errors": [{"message": "Rate limit exceeded"}]},
    {"errors": [{"message": "Something else broke"}]},
    ExtractorError("Unable to download JSON metadata: HTTP Error 500"),
])
def test_ad_details_failure_leaves_aaa_info_unknown(monkeypatch, answer):
    """aaa_info is supplementary: a refused or broken details query is null (unknown), never a failed ad."""
    archive, _ = details_case("eu")
    page = json.loads(DETAILS_FIXTURE.read_text())["page"]
    monkeypatch.setattr(F, "_discover_doc_id", lambda *args: pytest.fail("must not refresh"))
    F._DOC_IDS.clear()
    assert F._ad_details(DetailsIE(answer), "url", page, archive) is None


def test_details_operation_id_parses_the_recorded_bundle():
    bundle = ('__d("AdLibraryV3AdDetailsQuery_facebookRelayOperation",[],(function(t,n,r,o,a,i)'
              '{a.exports="25068828942793558"}),null);')
    assert F._operation_id(bundle, "AdLibraryV3AdDetailsQuery") == F.DETAILS_DOC_ID


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


def test_both_inputs_of_a_pair_take_the_specific_one(client, monkeypatch):
    # ScrapeCreators' own examples send pageId with companyName and id with url, so a copied request must work.
    fixture = json.loads(SEARCH_FIXTURE.read_text())
    seen = []
    monkeypatch.setattr(F, "with_ydl", _fake_with_ydl)
    monkeypatch.setattr(F, "_fetch_search", lambda ydl, params, cursor: (
        seen.append(params) or ({"page_info": fixture["page_info"]}, [M.decorate_ad(fixture["ads"][0])])))

    async def resolve(name):
        raise AssertionError("companyName must not be resolved next to a pageId")

    monkeypatch.setattr(F, "_resolve_company", resolve)
    body = {"pageId": "15087023444", "companyName": "Lululemon"}
    assert client.get("/v1/facebook/adLibrary/company/ads", params=body).status_code == 200
    assert client.post("/v1/facebook/adLibrary/company/ads", json=body).status_code == 200
    assert all("15087023444" in json.dumps(params) for params in seen)

    archive = json.loads(DETAIL_FIXTURE.read_text())["archive"]
    asked = []

    async def detail(ident, *, details=False):
        asked.append(ident)
        return archive, None

    monkeypatch.setattr(F, "_detail_archive", detail)
    other = "https://www.facebook.com/ads/library/?id=1111111111111111"
    response = client.get("/v1/facebook/adLibrary/ad", params={"id": archive["ad_archive_id"], "url": other})
    assert response.status_code == 200 and asked == [archive["ad_archive_id"]]
