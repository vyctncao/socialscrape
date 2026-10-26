"""ScrapeCreators' documented examples (socialscrape/examples.py): GET /meta/examples serves one operation's params and
responses, publicly and outside the cache, every catalog operation has them, and the served OpenAPI carries the param
examples but no response examples."""

from __future__ import annotations

import time

from socialscrape import examples
from socialscrape.catalog import CATALOG
from tests.e2e import app_with

COMPANY_ADS = "/v1/facebook/adLibrary/company/ads"
OPERATIONS = [f"{method} {entry['path']}" for entry in CATALOG for method in entry.get("methods", ["GET"])]


def test_the_examples_file_loads_in_under_a_second():
    examples.operations.cache_clear()
    started = time.perf_counter()
    assert len(examples.operations()) == len(OPERATIONS)
    assert time.perf_counter() - started < 1.0


def test_meta_examples_serves_one_operation(client):
    r = client.get("/meta/examples", params={"method": "POST", "path": COMPANY_ADS})
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=300"
    body = r.json()
    assert list(body) == ["success", "method", "path", "params", "responses"]
    assert body["success"] is True and (body["method"], body["path"]) == ("POST", COMPANY_ADS)
    assert body["params"]["pageId"] == "367152833370567" and body["params"]["trim"] is False  # JSON types kept
    assert list(body["responses"]) == ["200", "200 (trimmed)", "404", "403", "500"]
    ok = body["responses"]["200"]
    assert ok["success"] is True and ok["credits_charged"] == 0 and "credits_remaining" not in ok
    assert ok["results"][0]["ad_archive_id"]
    assert body["responses"]["404"] == {"success": False, "error": "company_not_found",
                                        "message": "Facebook company was not found"}  # the route's own 404
    assert body["responses"]["500"] == {"success": False, "error": "server_error", "message": "Internal server error"}
    get = client.get("/meta/examples", params={"method": "GET", "path": COMPANY_ADS}).json()
    assert get["method"] == "GET" and get["params"] == body["params"]  # the POST body is the GET query


def test_meta_examples_errors(client):
    r = client.get("/meta/examples", params={"method": "POST", "path": "/v1/tiktok/profile"})  # GET only
    assert r.status_code == 404 and r.json() == {
        "success": False, "error": "not_found",
        "message": "No examples for POST /v1/tiktok/profile; GET /meta/coverage lists the endpoints"}
    assert client.get("/meta/examples", params={"method": "GET", "path": "/v1/tiktok/nothing"}).status_code == 404
    for params, message in (({"path": COMPANY_ADS}, "method: Field required"),
                            ({"method": "PUT", "path": COMPANY_ADS}, "method: Input should be 'GET' or 'POST'"),
                            ({"method": "GET"}, "path: Field required"),
                            ({"method": "GET", "path": "tiktok/profile"}, "path must be an API path"),
                            ({"method": "GET", "path": "/v1/tiktok/profile?handle=x"}, "path must be an API path")):
        r = client.get("/meta/examples", params=params)
        assert r.status_code == 400 and "cache-control" not in r.headers, params
        assert r.json()["error"] == "bad_request" and r.json()["message"].startswith(message), params


def test_meta_examples_is_public_and_not_cached(tmp_path):
    with app_with({"SOCIALSCRAPE_API_KEYS": "k1"}, tmp_path) as c:  # the response cache is on too
        assert c.get("/v1/youtube/shorts/trending").status_code == 401
        r = c.get("/meta/examples", params={"method": "GET", "path": "/v1/tiktok/profile", "cache_max_age": "7d"})
        assert r.status_code == 200 and r.json()["params"]["handle"] == "stoolpresidente"
        assert "x-cache" not in r.headers
        assert c.get("/meta/examples/", follow_redirects=False).status_code == 307  # not a 401


def test_every_catalog_operation_has_examples():
    operations = examples.operations()
    assert list(operations) == OPERATIONS
    params = {entry["path"]: set(entry["params"]) for entry in CATALOG}
    for key, entry in operations.items():
        assert set(entry["params"]) <= params[key.split(" ", 1)[1]], key
        assert set(entry["responses"]) - {"200 (trimmed)"} == {"200", "404", "403", "500"}, key
        ok = entry["responses"]["200"]
        # A few routes this server doesn't implement document a bare array; those are shown as they are.
        assert isinstance(ok, list) or ok["success"] is True, key
        for code in ("404", "403", "500"):
            error = entry["responses"][code]
            assert set(error) == {"success", "error", "message"} and error["success"] is False, (key, code)


def test_openapi_carries_param_examples_but_no_response_examples(client):
    spec = client.get("/openapi.json").json()
    get = {param["name"]: param for param in spec["paths"][COMPANY_ADS]["get"]["parameters"]}
    assert get["pageId"]["example"] == "367152833370567"
    assert get["trim"]["example"] == "false"  # as query text: the GET params are strings
    assert "example" not in get["cache_max_age"]  # ScrapeCreators gives none here
    body = spec["paths"][COMPANY_ADS]["post"]["requestBody"]["content"]["application/json"]
    assert body["example"] == examples.operations()[f"POST {COMPANY_ADS}"]["params"]
    properties = spec["components"]["schemas"]["FacebookCompanyAdsBody"]["properties"]
    assert properties["companyName"]["example"] == "Lululemon" and properties["trim"]["example"] is False
    video = {param["name"]: param for param in spec["paths"]["/v1/youtube/video"]["get"]["parameters"]}
    assert video["url"]["example"].startswith("https://") and video["cache_max_age"]["example"] == "7d"
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            for code, response in operation["responses"].items():
                media = response.get("content", {}).get("application/json", {})
                assert "example" not in media and "examples" not in media, (method, path, code)
    assert client.app.openapi() is client.app.openapi()


def test_a_declared_example_is_kept():
    spec = {"paths": {"/v1/tiktok/profile": {"get": {"parameters": [
        {"name": "handle", "in": "query", "schema": {"type": "string", "examples": ["mine"]}},
        {"name": "user_id", "in": "query", "example": "42", "schema": {"type": "string"}},
    ]}}}}
    params = examples.add_param_examples(spec)["paths"]["/v1/tiktok/profile"]["get"]["parameters"]
    assert "example" not in params[0] and params[1]["example"] == "42"
