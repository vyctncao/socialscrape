"""ScrapeCreators' documented examples for every operation: its params and its 200, 404, 403 and 500 responses.

data/doc_examples.json is built from ScrapeCreators' docs, adapted to this server's envelopes (its "_source" says
how). GET /meta/examples serves one operation's examples to the dashboard's docs pages, and the served OpenAPI gets
each param's example. The response examples (about 1 MB) stay out of /openapi.json, which every docs page loads.
"""

from __future__ import annotations

import functools
import json
import re
from importlib.resources import files
from typing import Any, Literal

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from .errors import BadRequest, ErrorResponse, NotFound, error_responses
from .registry import _body_value

_API_PATH = re.compile(r"/v[0-9]+(/[\w.-]+)+")
# The docs pages fetch one operation per page they open; the file changes only with a new release.
CACHE_CONTROL = "public, max-age=300"


@functools.cache
def operations() -> dict[str, dict[str, Any]]:
    """{"GET /v1/...": {"params": {...}, "responses": {"200": {...}, ...}}}, read on first use."""
    text = files("socialscrape.data").joinpath("doc_examples.json").read_text(encoding="utf-8")
    return json.loads(text)["operations"]


def _declares_example(*objects: dict[str, Any]) -> bool:
    return any("example" in item or "examples" in item for item in objects)


def add_param_examples(schema: dict[str, Any]) -> dict[str, Any]:
    """The OpenAPI `schema` with each documented param's example, unless its route declares one: `example` on a
    GET's query params (as query text, the type they have here: false is 'false') and on a POST body's properties,
    and the whole body as its requestBody's example. In place, and running it again changes nothing."""
    components = schema.get("components", {}).get("schemas", {})
    for key, entry in operations().items():
        method, path = key.split(" ", 1)
        operation = schema["paths"].get(path, {}).get(method.lower())
        params = entry["params"]
        if not operation or not params:
            continue
        for param in operation.get("parameters", []):
            if (param["in"] == "query" and param["name"] in params
                    and not _declares_example(param, param.get("schema", {}))):
                param["example"] = _body_value(params[param["name"]])
        content = operation.get("requestBody", {}).get("content", {}).get("application/json")
        if content is None:
            continue
        body = content["schema"]
        if ref := body.get("$ref"):  # sc_get's body model, under components
            body = components.get(ref.rsplit("/", 1)[-1], {})
        properties = body.get("properties", {})
        for name, value in params.items():
            if name in properties and not _declares_example(properties[name]):
                properties[name]["example"] = value
        if not _declares_example(content):
            content["example"] = {name: value for name, value in params.items() if name in properties}
    return schema


def install_examples(app: FastAPI) -> None:
    """GET /meta/examples, public like /meta/coverage, and the param examples in app.openapi()."""

    @app.get("/meta/examples", tags=["meta"], responses={
        **error_responses("default"),
        400: {"model": ErrorResponse, "description": "A missing or invalid method or path"},
        404: {"model": ErrorResponse, "description": "An operation this server doesn't serve"},
    })
    async def meta_examples(
        method: Literal["GET", "POST"] = Query(..., description="The operation's method"),
        path: str = Query(..., description="The operation's path, e.g. /v1/tiktok/profile"),
    ) -> JSONResponse:
        """ScrapeCreators' documented params and 200, 404, 403 and 500 responses for one operation, in this
        server's envelopes."""
        if not _API_PATH.fullmatch(path):
            raise BadRequest("path must be an API path, e.g. /v1/tiktok/profile")
        entry = operations().get(f"{method} {path}")
        if entry is None:
            raise NotFound(f"No examples for {method} {path}; GET /meta/coverage lists the endpoints")
        return JSONResponse({"success": True, "method": method, "path": path, "params": entry["params"],
                             "responses": entry["responses"]}, headers={"cache-control": CACHE_CONTROL})

    # The examples go on the generated schema rather than into the routes at registration (sc_get): there they would
    # have to be copied into each handler's Query() objects, which also parse requests, cache_max_age is one Query
    # shared by every route, and the file would be read at import instead of on the first /openapi.json. FastAPI
    # keeps the schema it generates until the routes change; annotating a schema again is a no-op.
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        return add_param_examples(generate())

    app.openapi = openapi  # type: ignore[method-assign]
