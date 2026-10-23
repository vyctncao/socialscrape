import json

from fastapi.testclient import TestClient


def rpc(method, request_id=1, params=None):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def auth_client(monkeypatch):
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "test-key")
    get_settings.cache_clear()
    return TestClient(create_app()), get_settings


def test_auth_initialize_notifications_and_ping(monkeypatch):
    client, clear = auth_client(monkeypatch)
    try:
        denied = client.post("/mcp", json=rpc("ping"))
        assert denied.status_code == 401
        assert denied.json()["error"] == {"code": -32600, "message": "Authorization required"}
        assert denied.headers["www-authenticate"] == \
            'Bearer resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"'
        headers = {"x-api-key": "test-key"}
        initialized = client.post("/mcp", headers=headers,
                                  json=rpc("initialize", params={"protocolVersion": "2025-06-18"}))
        assert initialized.json()["result"] == {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "socialscrape", "version": "0.1.0"},
        }
        assert "mcp-session-id" not in initialized.headers
        old = client.post("/mcp", headers=headers,
                          json=rpc("initialize", params={"protocolVersion": "2025-03-26"}))
        assert old.json()["result"]["protocolVersion"] == "2025-03-26"
        notification = client.post("/mcp", headers=headers,
                                   json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert notification.status_code == 202 and notification.content == b""
        assert client.post("/mcp", headers=headers, json=rpc("ping", 2)).json()["result"] == {}
        assert client.get("/mcp", headers=headers).status_code == 405
    finally:
        clear.cache_clear()


def test_tools_list_and_sse(monkeypatch):
    client, clear = auth_client(monkeypatch)
    try:
        response = client.post("/mcp", headers={"x-api-key": "test-key", "accept": "text/event-stream"},
                               json=rpc("tools/list"))
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.text.startswith("event: message\ndata: ")
        payload = json.loads(response.text.split("data: ", 1)[1])
        tools = payload["result"]["tools"]
        assert len(tools) == 78
        assert [t["name"] for t in tools] == sorted(t["name"] for t in tools)
        assert {"v1_youtube_channel_videos", "v1_account_credit_balance",
                "v1_facebook_adLibrary_search_ads", "v1_tiktok_ad_library_ad",
                "v1_google_adLibrary_advertisers_search"} <= {t["name"] for t in tools}
        representative = next(t for t in tools if t["name"] == "v1_youtube_channel_videos")
        assert representative["inputSchema"]["$schema"].endswith("draft-07/schema#")
        assert representative["inputSchema"]["properties"]["sort"]["enum"] == ["latest", "popular"]
        assert representative["annotations"] == {"readOnlyHint": True, "openWorldHint": False,
                                                   "destructiveHint": False}
        assert representative["execution"] == {"taskSupport": "forbidden"}
    finally:
        clear.cache_clear()


def test_tools_call_success_error_and_unknown(monkeypatch):
    client, clear = auth_client(monkeypatch)
    try:
        headers = {"x-api-key": "test-key"}
        success = client.post("/mcp", headers=headers,
                              json=rpc("tools/call", params={"name": "v1_account_credit_balance", "arguments": {}}))
        text = success.json()["result"]["content"][0]["text"]
        assert json.loads(text)["unlimited"] is True
        error = client.post("/mcp", headers=headers,
                            json=rpc("tools/call", params={"name": "v1_instagram_basic_profile",
                                                           "arguments": {}})).json()
        assert error["result"]["isError"] is True
        assert json.loads(error["result"]["content"][0]["text"])["success"] is False
        unknown = client.post("/mcp", headers=headers,
                              json=rpc("tools/call", params={"name": "no_such_tool", "arguments": {}})).json()
        assert unknown["result"] == {"content": [{"type": "text",
                                                    "text": "MCP error -32602: Tool no_such_tool not found"}],
                                     "isError": True}
    finally:
        clear.cache_clear()
