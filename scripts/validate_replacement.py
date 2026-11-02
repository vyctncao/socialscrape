#!/usr/bin/env python3
"""Read-only consumer-contract check over an external cohort (native-only by default).

Input is either {"profiles": [...], "videos": [...]} or the same arrays in
[{"kind": "profiles", "rows": [...]}, ...]. Each row names a platform and a
handle (profiles) or url (videos). Keep customer cohorts and response artifacts
outside the repository. No database writes or media downloads are performed.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLATFORMS = ("instagram", "tiktok", "youtube")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from socialscrape.quality import at, number, stamp, url, identity, validate, feed_cursor, feed_ids, pagination_issues


def load_cases(path: Path, feeds: int = 0, reels: bool = False) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, list):
        data = {row["kind"]: row["rows"] for row in data}
    cases = []
    for kind, key in (("profile", "profiles"), ("video", "videos")):
        for fixture in data.get(key, []):
            if fixture["platform"] not in PLATFORMS:
                continue
            value = fixture["handle"] if kind == "profile" else fixture.get("url", fixture.get("post_url"))
            row = {**fixture, "kind": kind}
            row["handle" if kind == "profile" else "url"] = value
            row["case_id"] = hashlib.sha256(f"{kind}:{fixture['platform']}:{value}".encode()).hexdigest()[:20]
            cases.append(row)
    for platform in PLATFORMS:
        for profile in [r for r in cases if r["kind"] == "profile" and r["platform"] == platform][:feeds]:
            cases.append({**profile, "kind": "feed", "case_id": "feed-" + profile["case_id"]})
            if platform == "instagram" and reels:
                cases.append({**profile, "kind": "feed", "variant": "reels",
                              "case_id": "reels-" + profile["case_id"]})
    # Round-robin platforms instead of completing one platform's whole roster first.
    grouped = [[r for r in cases if r["platform"] == platform] for platform in PLATFORMS]
    return [group[index] for index in range(max(map(len, grouped), default=0))
            for group in grouped if index < len(group)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--per-platform", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=100)
    parser.add_argument("--feeds-per-platform", type=int, default=0)
    parser.add_argument("--pages-per-feed", type=int, default=1, help="Follow real cursors, checking for overlaps/cycles")
    parser.add_argument("--instagram-reels", action="store_true", help="Also check the Reels tab for sampled Instagram profiles")
    parser.add_argument("--allow-fallback", action="store_true", help="Qualify normal production routing, including labeled paid fallback")
    parser.add_argument("--consumer-params", action="store_true", help="Use the tracking adapter's trim and sort parameters on feeds")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only-failures", type=Path, help="A previous results.jsonl; rerun its nonpassing case IDs")
    args = parser.parse_args()
    if args.workers < 1 or args.per_platform < 1 or args.pages_per_feed < 1:
        parser.error("worker counts must be positive")
    cases = load_cases(args.cohort, args.feeds_per_platform, args.instagram_reels)
    if args.only_failures:
        ids = {r["case_id"].split("-page")[0] for line in args.only_failures.read_text().splitlines()
               if (r := json.loads(line))["outcome"] != "passed"}
        cases = [r for r in cases if r["case_id"] in ids]
    if args.limit:
        cases = cases[:args.limit]
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = args.output / "responses"
    raw.mkdir(exist_ok=True, mode=0o700)
    key = os.environ.get("SOCIALSCRAPE_API_KEY", "")
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(certifi.where())
    except ImportError:
        pass
    semaphores = {p: threading.Semaphore(args.per_platform) for p in PLATFORMS}
    started_at = datetime.now(timezone.utc).isoformat()

    def run_page(case: dict, cursor: Any = None, page: int = 1) -> tuple[dict, dict]:
        platform, kind = case["platform"], case["kind"]
        if kind == "profile":
            path = {"instagram": "/v1/instagram/profile", "tiktok": "/v1/tiktok/profile",
                    "youtube": "/v1/youtube/channel"}[platform]
            params = {"handle": case["handle"]}
        elif kind == "feed":
            path = {"instagram": "/v2/instagram/user/posts", "tiktok": "/v3/tiktok/profile/videos",
                    "youtube": "/v1/youtube/channel/shorts"}[platform]
            if case.get("variant") == "reels":
                path = "/v1/instagram/user/reels"
            params = {"handle": case["handle"]}
            if platform == "tiktok":
                params["sort_by"] = "latest"
            if args.consumer_params and platform == "instagram" and case.get("variant") != "reels":
                params["trim"] = "true"
            if args.consumer_params and platform == "youtube":
                params["sort"] = "newest"
            if cursor is not None:
                name = {"instagram": "next_max_id", "tiktok": "max_cursor", "youtube": "continuationToken"}[platform]
                params["max_id" if case.get("variant") == "reels" else name] = cursor
        else:
            path = {"instagram": "/v1/instagram/post", "tiktok": "/v2/tiktok/video",
                    "youtube": "/v1/youtube/video"}[platform]
            params = {"url": case["url"]}
        headers = {"Accept": "application/json"}
        if not args.allow_fallback:
            headers["x-socialscrape-fallback"] = "off"
        if key:
            headers["x-api-key"] = key
        is_post = kind == "profile" and platform == "instagram"
        request = urllib.request.Request(args.base_url.rstrip("/") + path
                                         + ("" if is_post else "?" + urllib.parse.urlencode(params)),
                                         data=json.dumps(params).encode() if is_post else None,
                                         headers={**headers, **({"Content-Type": "application/json"} if is_post else {})})
        with semaphores[platform]:
            requested_at = datetime.now(timezone.utc).isoformat()
            start = time.monotonic()
            status, body, response_headers, network_error, network_detail = 0, {}, {}, None, None
            try:
                try:
                    response = urllib.request.urlopen(request, timeout=args.timeout, context=context)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    status, response_headers = response.status, dict(response.headers)
                    text = response.read().decode("utf8", "replace")
                    if key:
                        text = text.replace(key, "[REDACTED]")
                    case_id = case["case_id"] + (f"-page{page}" if page > 1 else "")
                    (raw / (case_id + ".json")).write_text(text)
                    body = json.loads(text)
            except Exception as exc:
                network_error = type(exc).__name__
                network_detail = str(exc)
                if key:
                    network_detail = network_detail.replace(key, "[REDACTED]")
                network_detail = network_detail[:512]
            elapsed = time.monotonic() - start
        if not isinstance(body, dict):
            body = {}
            network_error = "InvalidResponseEnvelope"
        issues = validate(kind, platform, body, case) if status == 200 and body.get("success") is True else []
        fallback = next((v for k, v in response_headers.items() if k.lower() == "x-socialscrape-fallback"), None)
        if fallback and fallback != "off" and not args.allow_fallback:
            issues.append("provider.fallback_used")
        if elapsed > 60:
            issues.append("request.exceeds_consumer_60s_timeout")
        success = status == 200 and body.get("success") is True and not issues and not network_error
        return {"case_id": case["case_id"] + (f"-page{page}" if page > 1 else ""), "kind": kind, "platform": platform,
                "fixture": case, "path": path, "status": status, "seconds": round(elapsed, 3),
                "requested_at": requested_at,
                "outcome": "passed" if success else "failed", "issues": issues,
                "error": body.get("error"), "network_error": network_error,
                "network_error_detail": network_detail, "fallback": fallback,
                "page": page}, body

    def run(case: dict) -> list[dict]:
        pages, seen_ids, seen_cursors = [], set(), set()
        cursor = None
        for page in range(1, (args.pages_per_feed if case["kind"] == "feed" else 1) + 1):
            row, body = run_page(case, cursor, page)
            pages.append(row)
            if row["status"] != 200 or body.get("success") is not True or row["network_error"]:
                break
            if case["kind"] != "feed":
                break
            row["issues"].extend(pagination_issues(case["platform"], body, seen_ids, seen_cursors))
            if row["issues"]:
                row["outcome"] = "failed"
            if "feed.cursor_cycle" in row["issues"]:
                break
            seen_ids.update(feed_ids(case["platform"], body))
            cursor = feed_cursor(case["platform"], body)
            if cursor is None or cursor == "":
                break
            seen_cursors.add(str(cursor))
        return pages

    results = []
    with (args.output / "results.jsonl").open("w") as stream:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(run, case) for case in cases]
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                batch = future.result()
                results.extend(batch)
                completed += 1
                for result in batch:
                    stream.write(json.dumps(result) + "\n")
                stream.flush()
                if completed % 20 == 0 or completed == len(cases):
                    print(json.dumps({"completed": completed, "total": len(cases), "pages_checked": len(results),
                                      "outcomes": dict(Counter(r["outcome"] for r in results)),
                                      "http": dict(Counter(str(r["status"]) for r in results))}), flush=True)
    summary = {"started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
               "base_url": args.base_url, "fallback": "allowed" if args.allow_fallback else "off",
               "fallback_responses": sum(bool(r["fallback"] and r["fallback"] != "off") for r in results),
               "cases": len(results),
               "outcomes": dict(Counter(r["outcome"] for r in results)),
               "issues": dict(Counter(issue for r in results for issue in r["issues"])),
               "platforms": {p: dict(Counter(r["outcome"] for r in results if r["platform"] == p))
                             for p in PLATFORMS},
               "kinds": {kind: dict(Counter(r["outcome"] for r in results if r["kind"] == kind))
                         for kind in ("profile", "video", "feed")}}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    raise SystemExit(0 if results and all(r["outcome"] == "passed" for r in results) else 1)


if __name__ == "__main__":
    main()
