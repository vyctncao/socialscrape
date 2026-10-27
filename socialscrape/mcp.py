"""Small stateless MCP Streamable HTTP transport.

The MCP SDK is intentionally not used: this facade supports four synchronous
methods, no sessions, server requests, resources, prompts, or resumability. A
hand-written handler keeps the deployed service small while making its exact
ScrapeCreators-compatible JSON and SSE framing explicit.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .catalog import CATALOG, UPSTREAM_OPERATIONS

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26")
ACCOUNT_PATHS = {
    "v1_account_credit_balance": "/v1/account/credit-balance",
    "v1_account_get_api_usage": "/v1/account/get-api-usage",
    "v1_account_get_daily_usage_count": "/v1/account/get-daily-usage-count",
    "v1_account_get_most_used_routes": "/v1/account/get-most-used-routes",
}


PLATFORM_SCHEMA = {"type": "string", "enum": ["youtube", "tiktok", "instagram", "facebook", "google"]}
ID_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}
KIND_SCHEMA = {"type": "string", "enum": ["proxies", "accounts"]}
ADMIN_TOOLS = [
    ("admin_list_platform_settings", "List proxies and accounts", "GET", "/v1/admin/platforms", [],
     {}, [], True, False, "List saved proxies and signed-in accounts, with account health. Never returns their values."),
    ("admin_add_proxy", "Add a proxy", "POST", "/v1/admin/platforms/{platform}/proxies", ["proxy", "test"],
     {"platform": PLATFORM_SCHEMA, "proxy": {"type": "string", "description": "Proxy URL; it is encrypted and never returned"},
      "test": {"type": "boolean", "description": "Test the platform through it after saving (default true)"}},
     ["platform", "proxy"], False, False, "Save a proxy to the end of a platform's ordered pool (owner API key only)."),
    ("admin_test_proxy", "Test a proxy", "POST", "/v1/admin/platforms/{platform}/proxies/{id}/test", [],
     {"platform": PLATFORM_SCHEMA, "id": ID_SCHEMA}, ["platform", "id"], False, False,
     "Check whether a saved proxy connects and can reach the platform."),
    ("admin_add_account", "Add a signed-in account", "POST", "/v1/admin/platforms/{platform}/accounts", ["cookies"],
     {"platform": PLATFORM_SCHEMA, "cookies": {"type": "string", "description":
      "Netscape cookies.txt, a browser JSON cookie export (or base64 of it), or a Cookie header; never returned"}},
     ["platform", "cookies"], False, False, "Save a signed-in account's cookies and check its session (owner API key only)."),
    ("admin_check_account", "Check a signed-in account", "POST", "/v1/admin/platforms/{platform}/accounts/{id}/check", [],
     {"platform": PLATFORM_SCHEMA, "id": ID_SCHEMA}, ["platform", "id"], False, False,
     "Ask the platform whether a saved account session still works."),
    ("admin_move_platform_setting", "Reorder a proxy or account", "POST",
     "/v1/admin/platforms/{platform}/{kind}/{id}/move", ["direction"],
     {"platform": PLATFORM_SCHEMA, "kind": KIND_SCHEMA, "id": ID_SCHEMA,
      "direction": {"type": "string", "enum": ["up", "down"]}}, ["platform", "kind", "id", "direction"], False, False,
     "Move a saved proxy or account one position earlier or later in its pool."),
    ("admin_remove_platform_setting", "Remove a proxy or account", "DELETE", "/v1/admin/platforms/{platform}/{kind}/{id}", [],
     {"platform": PLATFORM_SCHEMA, "kind": KIND_SCHEMA, "id": ID_SCHEMA}, ["platform", "kind", "id"], False, True,
     "Permanently remove a saved proxy or signed-in account."),
]


def _admin_tools() -> tuple[list[dict[str, Any]], dict[str, tuple[str, str, list[str]]]]:
    tools, routes = [], {}
    for name, title, method, path, fields, properties, required, read_only, destructive, description in ADMIN_TOOLS:
        tools.append({"name": name, "title": "SocialScrape admin - " + title, "description": description,
                      "inputSchema": {"type": "object", "properties": properties, "required": required},
                      "annotations": {"readOnlyHint": read_only, "openWorldHint": False,
                                      "destructiveHint": destructive},
                      "execution": {"taskSupport": "forbidden"}})
        routes[name] = (method, path, fields)
    return tools, routes


def _can_administer(request: Request) -> bool:
    principal = getattr(request.state, "principal", None)
    return getattr(principal, "role", None) == "owner" and getattr(request.state, "auth_kind", None) == "api_key"


def tool_name(path: str) -> str:
    return path.lstrip("/").replace("/", "_").replace("-", "_")


def _catalog() -> list[dict[str, Any]]:
    tools = json.loads(files("socialscrape.data").joinpath("mcp_tools.json").read_text())
    by_name = {tool["name"]: tool for tool in tools}
    documented = {(op["method"], op["path"]): op for op in UPSTREAM_OPERATIONS}
    for entry in CATALOG:
        name = tool_name(entry["path"])
        operation = documented.get(("GET", entry["path"]), {})
        parameters = {p["name"]: p for p in operation.get("parameters", [])}
        tool = by_name.setdefault(name, {
            "name": name, "title": "SocialScrape - " + entry["summary"],
            "description": (entry["summary"] + ". Check /meta/coverage for native availability; "
                            "a documented operation is not proof of an implemented extractor."),
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True, "openWorldHint": True, "destructiveHint": False},
            "execution": {"taskSupport": "forbidden"},
        })
        props = tool["inputSchema"].setdefault("properties", {})
        for param in entry["params"]:
            props.setdefault(param, parameters.get(param, {}).get("schema", {"type": "string"}))
        if entry["required"]:
            tool["inputSchema"]["required"] = entry["required"]
    return sorted(by_name.values(), key=lambda tool: tool["name"])


def _paths() -> dict[str, str]:
    return {tool_name(row["path"]): row["path"] for row in CATALOG} | ACCOUNT_PATHS


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": request_id}


def _validate(schema: dict[str, Any], arguments: Any) -> str | None:
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    properties = schema.get("properties", {})
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        return f"unknown argument: {unknown[0]}"
    missing = [name for name in schema.get("required", []) if name not in arguments]
    if missing:
        return f"missing required argument: {missing[0]}"
    expected = {"string": str, "boolean": bool, "integer": int, "number": (int, float), "array": list,
                "object": dict}
    for name, value in arguments.items():
        prop = properties[name]
        kinds = prop.get("type")
        if kinds is None and "anyOf" in prop:
            kinds = [item.get("type") for item in prop["anyOf"]]
        kinds = [kinds] if isinstance(kinds, str) else kinds or []
        if value is None and "null" in kinds:
            continue
        if kinds and not any(kind in expected and isinstance(value, expected[kind]) and
                             not (kind in ("integer", "number") and isinstance(value, bool)) for kind in kinds):
            return f"argument {name} has the wrong type"
        if "enum" in prop and value not in prop["enum"]:
            return f"argument {name} must be one of {prop['enum']}"
    return None


async def _asgi_get(app: FastAPI, outer: Request, path: str, arguments: dict[str, Any], method: str = "GET",
                    body: dict[str, Any] | None = None) -> tuple[int, Any]:
    query = b"" if method != "GET" else urlencode(
        [(key, str(value).lower() if isinstance(value, bool) else value)
         for key, value in arguments.items() if value is not None], doseq=True).encode()
    payload = json.dumps(body).encode() if body is not None else b""
    forwarded = [(b"content-type", b"application/json")] if body is not None else []
    for header in ("x-api-key", "authorization", "x-socialscrape-fallback"):
        if value := outer.headers.get(header):
            forwarded.append((header.encode(), value.encode("latin-1")))
    forwarded.append((b"accept", b"application/json"))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": outer.url.scheme, "path": path, "raw_path": path.encode(), "query_string": query,
        "root_path": "", "headers": forwarded, "client": outer.scope.get("client"),
        "server": outer.scope.get("server"), "app": app,
    }
    messages: list[dict[str, Any]] = []
    received = False

    async def receive() -> dict[str, Any]:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": payload, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {"success": False, "error": "invalid_response", "message": body.decode("utf-8", "replace")}
    return start["status"], parsed


def _response(request: Request, payload: dict[str, Any]) -> Response:
    accepts = request.headers.get("accept", "")
    if "text/event-stream" in accepts:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        return Response(f"event: message\ndata: {data}\n\n", media_type="text/event-stream")
    return JSONResponse(payload)


def install_mcp(app: FastAPI) -> None:
    tools = _catalog()
    tools_by_name = {tool["name"]: tool for tool in tools}
    admin_tools, admin_routes = _admin_tools()
    admin_by_name = {tool["name"]: tool for tool in admin_tools}
    paths = _paths()
    if set(tools_by_name) != set(paths):
        raise RuntimeError("MCP tool metadata does not match the endpoint catalog")

    @app.api_route("/mcp", methods=["POST", "GET", "HEAD", "DELETE"], include_in_schema=False)
    async def mcp(request: Request):
        if request.method != "POST":
            return Response(status_code=405, headers={"allow": "POST"})
        version = request.headers.get("mcp-protocol-version")
        if version is not None and version not in SUPPORTED_VERSIONS:
            return JSONResponse(_rpc_error(None, -32600, f"Unsupported MCP protocol version: {version}"),
                                status_code=400)
        try:
            message = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _response(request, _rpc_error(None, -32700, "Parse error"))
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _response(request, _rpc_error(message.get("id") if isinstance(message, dict) else None,
                                                  -32600, "Invalid Request"))
        method, request_id = message["method"], message.get("id")
        if method.startswith("notifications/") or "id" not in message:
            return Response(status_code=202)
        params = message.get("params", {})
        if not isinstance(params, dict):
            return _response(request, _rpc_error(request_id, -32602, "Invalid params"))
        if method == "initialize":
            requested = params.get("protocolVersion")
            negotiated = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            result = {"protocolVersion": negotiated, "capabilities": {"tools": {"listChanged": True}},
                      "serverInfo": {"name": "socialscrape", "version": app.version}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": sorted(tools + admin_tools, key=lambda tool: tool["name"])
                      if _can_administer(request) else tools}
        elif method == "tools/call":
            name = params.get("name")
            if name in admin_by_name:
                arguments = params.get("arguments", {})
                if not _can_administer(request):
                    result = {"content": [{"type": "text", "text":
                              "Admin tools need a workspace owner's API key in the x-api-key header."}], "isError": True}
                elif problem := _validate(admin_by_name[name]["inputSchema"], arguments):
                    result = {"content": [{"type": "text", "text": f"MCP error -32602: {problem}"}],
                              "isError": True}
                elif any(key in arguments and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(arguments[key]))
                         for key in ("id",)):
                    result = {"content": [{"type": "text", "text": "MCP error -32602: invalid id"}], "isError": True}
                else:
                    method, template, fields = admin_routes[name]
                    path = template.format(**{key: arguments[key] for key in ("platform", "kind", "id") if key in arguments})
                    payload = {key: arguments[key] for key in fields if key in arguments} if method == "POST" else None
                    status, body = await _asgi_get(app, request, path, {}, method, payload)
                    result = {"content": [{"type": "text", "text": json.dumps(body, indent=2, ensure_ascii=False)}]}
                    if status >= 400:
                        result["isError"] = True
            elif name not in tools_by_name:
                result = {"content": [{"type": "text", "text": f"MCP error -32602: Tool {name} not found"}],
                          "isError": True}
            else:
                arguments = params.get("arguments", {})
                if problem := _validate(tools_by_name[name]["inputSchema"], arguments):
                    result = {"content": [{"type": "text", "text": f"MCP error -32602: {problem}"}],
                              "isError": True}
                else:
                    status, body = await _asgi_get(app, request, paths[name], arguments)
                    result = {"content": [{"type": "text", "text": json.dumps(body, indent=2, ensure_ascii=False)}]}
                    if status >= 400:
                        result["isError"] = True
        else:
            return _response(request, _rpc_error(request_id, -32601, "Method not found"))
        return _response(request, {"jsonrpc": "2.0", "id": request_id, "result": result})
