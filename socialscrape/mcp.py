"""Small stateless MCP Streamable HTTP transport.

The MCP SDK is intentionally not used: this facade supports four synchronous
methods, no sessions, server requests, resources, prompts, or resumability. A
hand-written handler keeps the deployed service small while making its exact
ScrapeCreators-compatible JSON and SSE framing explicit.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .catalog import CATALOG

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26")
ACCOUNT_PATHS = {
    "v1_account_credit_balance": "/v1/account/credit-balance",
    "v1_account_get_api_usage": "/v1/account/get-api-usage",
    "v1_account_get_daily_usage_count": "/v1/account/get-daily-usage-count",
    "v1_account_get_most_used_routes": "/v1/account/get-most-used-routes",
}


def tool_name(path: str) -> str:
    return path.lstrip("/").replace("/", "_").replace("-", "_")


def _catalog() -> list[dict[str, Any]]:
    return json.loads(files("socialscrape.data").joinpath("mcp_tools.json").read_text())


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


async def _asgi_get(app: FastAPI, outer: Request, path: str, arguments: dict[str, Any]) -> tuple[int, Any]:
    query = urlencode([(key, value) for key, value in arguments.items() if value is not None], doseq=True).encode()
    forwarded = []
    for header in ("x-api-key", "authorization"):
        if value := outer.headers.get(header):
            forwarded.append((header.encode(), value.encode("latin-1")))
    forwarded.append((b"accept", b"application/json"))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
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
            return {"type": "http.request", "body": b"", "more_body": False}
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
            result = {"tools": tools}
        elif method == "tools/call":
            name = params.get("name")
            if name not in tools_by_name:
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
