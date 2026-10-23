#!/usr/bin/env python3
"""Generate SocialScrape MCP metadata from a ScrapeCreators tools/list capture.

The input is research data and is parsed only as JSON. The resulting catalog is
limited to paths in socialscrape.catalog plus the four account operations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ACCOUNT = {
    "v1_account_credit_balance": (
        "SocialScrape - Get credit balance",
        "Returns SocialScrape's unlimited request status. SocialScrape does not charge API credits.",
    ),
    "v1_account_get_api_usage": (
        "SocialScrape - Get request history",
        "Returns a paginated list of API requests visible to the caller, with optional endpoint and status filters.",
    ),
    "v1_account_get_daily_usage_count": (
        "SocialScrape - Get daily usage",
        "Returns request counts for each of the last 30 days. SocialScrape does not charge API credits.",
    ),
    "v1_account_get_most_used_routes": (
        "SocialScrape - Get most used routes",
        "Returns the top 20 API routes by request count. Defaults to the last 24 hours and supports ranges up to one year.",
    ),
}


def name(path: str) -> str:
    return path.lstrip("/").replace("/", "_").replace("-", "_")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    # catalog.py is intentionally read as text rather than imported, so generation
    # has no application startup side effects.
    import ast
    module = ast.parse(args.catalog.read_text())
    assignment = next(n for n in module.body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "CATALOG")
    rows = ast.literal_eval(assignment.value)
    wanted = {name(row["path"]) for row in rows} | set(ACCOUNT)
    captured = json.loads(args.capture.read_text())["tools"]["tools"]
    by_name = {tool["name"]: tool for tool in captured}
    missing = wanted - by_name.keys()
    if missing:
        raise SystemExit(f"capture is missing tools: {sorted(missing)}")

    output = []
    for tool_name in sorted(wanted):
        tool = by_name[tool_name]
        item = {key: tool[key] for key in ("name", "title", "description", "inputSchema", "annotations", "execution")}
        if tool_name in ACCOUNT:
            item["title"], item["description"] = ACCOUNT[tool_name]
            for prop in item["inputSchema"].get("properties", {}).values():
                if "description" in prop:
                    prop["description"] = prop["description"].replace("credits", "requests")
        output.append(item)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
