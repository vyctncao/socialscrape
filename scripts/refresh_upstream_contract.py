#!/usr/bin/env python3
"""Refresh the documented ScrapeCreators surface; examples are not readiness proof.

Use --input for a previously captured official document or fetch the public
OpenAPI document. The source hash and date make changes reviewable. Never reads
API keys and never calls a paid extraction endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE = "https://docs.scrapecreators.com/openapi.json"
KINDS = {
    dict: "object",
    list: "array",
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
    type(None): "null",
}
META = {
    "success",
    "credits_remaining",
    "credits_charged",
    "cached",
    "cached_at",
    "cursor",
    "has_more",
    "hasMore",
    "max_cursor",
    "min_time",
    "page",
    "next_page",
}


def fields_of(value, path, fields):
    fields[path].add(KINDS[type(value)])
    if isinstance(value, dict):
        for key, item in value.items():
            fields_of(item, f"{path}.{key}" if path else key, fields)
    elif isinstance(value, list):
        for item in value:
            fields_of(item, path + "[]", fields)


def build(raw: bytes, retrieved_at: str) -> dict:
    records = []
    for path, methods in json.loads(raw)["paths"].items():
        for method, operation in methods.items():
            if method not in ("get", "post"):
                continue
            parameters, examples = [], {}
            for parameter in operation.get("parameters", []):
                if parameter.get("in") != "query":
                    continue
                parameters.append(
                    {
                        k: parameter[k]
                        for k in ("name", "in", "required", "schema")
                        if k in parameter
                    }
                )
                if parameter.get("example") is not None:
                    examples[parameter["name"]] = parameter["example"]
            body = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            for name, prop in body.get("properties", {}).items():
                if name not in {p["name"] for p in parameters}:
                    parameters.append(
                        {
                            "name": name,
                            "in": "body",
                            "required": name in body.get("required", []),
                            "schema": {
                                k: v
                                for k, v in prop.items()
                                if k in ("type", "enum", "items", "default", "format")
                            },
                        }
                    )
                if prop.get("example") is not None:
                    examples[name] = prop["example"]
            sample = (
                operation.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("example")
            )
            fields = defaultdict(set)
            if sample is not None:
                fields_of(sample, "", fields)
            fields.pop("", None)
            business = [
                key
                for key, value in (sample.items() if isinstance(sample, dict) else [])
                if key not in META and value not in (None, "", [], {})
            ]
            expect = []
            for key in business[:3]:
                value = sample[key]
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    identity = next(
                        (
                            i
                            for i in (
                                "id",
                                "uri",
                                "username",
                                "handle",
                                "uniqueId",
                                "aweme_id",
                                "url",
                                "title",
                                "name",
                                "text",
                            )
                            if value[0].get(i) not in (None, "")
                        ),
                        None,
                    )
                    expect.append(key + "[]." + identity if identity else key)
                else:
                    expect.append(key)
            if isinstance(sample, list):
                identifiers = [
                    p
                    for p, types in fields.items()
                    if p.startswith("[].")
                    and p.endswith((".id", ".uri", ".url"))
                    and ("string" in types or "integer" in types)
                ]
                expect = sorted(identifiers, key=lambda p: (p.count("."), len(p)))[:2]
            records.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "tag": operation["tags"][0],
                    "summary": operation["summary"],
                    "parameters": parameters,
                    "examples": examples,
                    "response_fields": {
                        p: sorted(types) for p, types in sorted(fields.items())
                    },
                    "preset_expect": expect,
                    "response_kind": KINDS[type(sample)]
                    if sample is not None
                    else None,
                }
            )
    return {
        "source": SOURCE,
        "retrieved_at": retrieved_at,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "upstream_operations": len(records),
        "operations": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="Captured official OpenAPI JSON; otherwise download the public document",
    )
    parser.add_argument(
        "--date", default=date.today().isoformat(), help="Capture date, YYYY-MM-DD"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "socialscrape/upstream_contract.json",
    )
    args = parser.parse_args()
    date.fromisoformat(args.date)
    if args.input:
        raw = args.input.read_bytes()
    else:
        with urlopen(
            Request(SOURCE, headers={"User-Agent": "SocialScrape-contract-audit"}),
            timeout=60,
        ) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("OpenAPI document exceeded 8 MiB")
    contract = build(raw, args.date)
    args.output.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "operations": contract["upstream_operations"],
                "source_sha256": contract["source_sha256"],
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
