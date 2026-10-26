#!/usr/bin/env python3
"""Compare the pinned official API with local routes and native availability.

Route registration and an 'implemented' label are inventory facts, not live
qualification. This audit never calls platforms, uses credentials or spends
ScrapeCreators credits. --require-native fails while implementation gaps remain.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from socialscrape.catalog import UPSTREAM_CONTRACT, UPSTREAM_OPERATIONS
from socialscrape.main import create_app
from socialscrape.registry import coverage


def audit() -> dict:
    app = create_app()
    schema = app.openapi()
    rows = {row["path"]: row for row in coverage()}
    operations = []
    for upstream in UPSTREAM_OPERATIONS:
        path, method = upstream["path"], upstream["method"]
        local = schema.get("paths", {}).get(path, {}).get(method.lower())
        account = path.startswith("/v1/account/")
        row = rows.get(path, {})
        status = (
            "missing_route"
            if local is None
            else "local_account"
            if account
            else row.get("status", "uncataloged")
        )
        names = {
            p["name"]
            for p in (local or {}).get("parameters", [])
            if p.get("in") == "query"
        }
        body = (
            (local or {})
            .get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        if "$ref" in body:
            body = schema["components"]["schemas"].get(
                body["$ref"].rsplit("/", 1)[-1], {}
            )
        names |= set(body.get("properties", {}))
        # Account handlers read request.query_params directly; this surfaces their missing schema declarations too.
        missing = [p["name"] for p in upstream["parameters"] if p["name"] not in names]
        operations.append(
            {
                "method": method,
                "path": path,
                "tag": upstream["tag"],
                "status": status,
                "undocumented_parameters": missing,
                "source": row.get("source"),
                "notes": row.get("notes"),
            }
        )
    counts = dict(sorted(Counter(op["status"] for op in operations).items()))
    return {
        "source": UPSTREAM_CONTRACT["source"],
        "retrieved_at": UPSTREAM_CONTRACT["retrieved_at"],
        "source_sha256": UPSTREAM_CONTRACT["source_sha256"],
        "upstream_operations": len(operations),
        "counts": counts,
        "all_routes_registered": counts.get("missing_route", 0) == 0,
        "native_implementation_gaps": sum(
            op["status"] not in ("implemented", "needs_setup", "local_account")
            for op in operations
        ),
        "gated_operations": counts.get("needs_setup", 0),
        "native_readiness_gaps": sum(
            op["status"] not in ("implemented", "local_account") for op in operations
        ),
        "full_native_qualification": False,
        "qualification_note": "This is a structural inventory. Live field completeness, identity, filters, pagination and sustained reliability require separate evidence.",
        "operations": operations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-native", action="store_true")
    args = parser.parse_args()
    report = audit()
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k != "operations"}))
    else:
        print(json.dumps(report, indent=2))
    if (
        not report["all_routes_registered"]
        or args.require_native
        and report["native_readiness_gaps"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
