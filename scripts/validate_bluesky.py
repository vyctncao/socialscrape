#!/usr/bin/env python3
"""Sustained public Bluesky qualification against a running SocialScrape server.

Discovers a varied public cohort through AppView actor search, freezes it, then
checks native profile, feed, post and pagination behavior. Requests always opt
out of paid fallback and caching. Saves captures outside the repository. Public
discovery is topic-biased and is not a claim about every creator or platform.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from socialscrape.platforms import bluesky
from socialscrape.ytdlp import with_ydl

POST = re.compile(r"at://(did:[^/]+)/app\.bsky\.feed\.post/([^/]+)$")


def validate_post(post: dict) -> list[str]:
    errors = []
    match = POST.fullmatch(str(post.get("uri", "")))
    if not match or (post.get("author") or {}).get("did") != match[1]:
        errors.append("post.identity")
    record = post.get("record") or {}
    try:
        stamp = datetime.fromisoformat(
            record["createdAt"].replace("Z", "+00:00")
        ).timestamp()
        if not 0 < stamp < time.time() + 86400:
            errors.append("post.createdAt")
    except (KeyError, ValueError, AttributeError):
        errors.append("post.createdAt")
    if not isinstance(record.get("text"), str):
        errors.append("post.text")
    for key in ("likeCount", "replyCount", "repostCount", "quoteCount"):
        value = post.get(key)
        if (key != "quoteCount" or value is not None) and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            errors.append("post." + key)
    return errors


async def discover(count: int) -> list[dict]:
    actors = {}
    # Keep the original discovery order; the saved cohort makes reruns comparable.
    for query in ("science", "news", "art", "gaming", "music", "sports"):

        def go(ydl):
            return bluesky._json(
                ydl, "app.bsky.actor.searchActors", {"q": query, "limit": 100}
            )

        data = await with_ydl("bluesky", go, cookiefile=None, cookiesfrombrowser=None)
        for actor in data.get("actors", []):
            if actor.get("did") and actor.get("handle"):
                actors.setdefault(
                    actor["did"],
                    {
                        "did": actor["did"],
                        "handle": actor["handle"],
                        "discovery_query": query,
                    },
                )
        # Use several topics even if the first search already fills the cohort.
        if len(actors) >= count and query not in ("science", "news"):
            break
    values = list(actors.values())
    # Round-robin topics instead of taking the first search's entire page.
    groups = [
        [a for a in values if a["discovery_query"] == q]
        for q in dict.fromkeys(a["discovery_query"] for a in values)
    ]
    selected = []
    for index in range(max(map(len, groups), default=0)):
        for group in groups:
            if index < len(group):
                selected.append(group[index])
    if len(selected) < count:
        raise RuntimeError(
            f"Discovery yielded only {len(selected)} distinct public profiles"
        )
    return selected[:count]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8794")
    parser.add_argument("--profiles", type=int, default=150)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--pagination-profiles", type=int, default=20)
    parser.add_argument(
        "--cohort",
        type=Path,
        help="Reuse a frozen public cohort instead of discovering another",
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        help="Service API key file; never printed or saved in captures",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        not 1 <= args.profiles <= 1000
        or not 1 <= args.workers <= 8
        or args.pagination_profiles < 0
    ):
        parser.error(
            "profiles must be 1..1000, workers 1..8, pagination-profiles nonnegative"
        )
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    cohort = (
        json.loads(args.cohort.read_text())
        if args.cohort
        else asyncio.run(discover(args.profiles))
    )
    cohort = cohort[: args.profiles]
    (args.output / "cohort.json").write_text(json.dumps(cohort, indent=2) + "\n")
    headers = {"Accept": "application/json", "x-socialscrape-fallback": "off"}
    if args.key_file:
        headers["x-api-key"] = args.key_file.read_text().strip()

    def ask(path, params, stem):
        start = time.monotonic()
        request = Request(
            args.base_url.rstrip("/") + path + "?" + urlencode(params), headers=headers
        )
        try:
            response = urlopen(request, timeout=60)
        except HTTPError as exc:
            response = exc
        with response:
            data = json.loads(response.read(8 * 1024 * 1024))
            result = {
                "path": path,
                "status": response.status,
                "seconds": round(time.monotonic() - start, 3),
                "issues": [],
            }
            if response.headers.get("x-socialscrape-fallback") == "scrapecreators":
                result["issues"].append("paid_fallback")
            if (
                response.status != 200
                or not isinstance(data, dict)
                or data.get("success") is not True
            ):
                result["issues"].append("http_or_success")
            (args.output / (stem + ".json")).write_text(
                json.dumps(
                    {
                        "request": {"path": path, "params": params},
                        "response": data,
                        "result": result,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            return result, data

    def check(index_actor):
        index, actor = index_actor
        stem = f"{index:04d}"
        checks = []
        try:
            result, profile = ask(
                "/v1/bluesky/profile", {"handle": actor["did"]}, stem + "-profile"
            )
            if profile.get("did") != actor["did"] or not profile.get("handle"):
                result["issues"].append("profile.identity")
            for key in ("followersCount", "followsCount", "postsCount"):
                value = profile.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    result["issues"].append("profile." + key)
            checks.append(result)
            result, feed = ask(
                "/v1/bluesky/user/posts", {"user_id": actor["did"]}, stem + "-feed"
            )
            posts = feed.get("feed")
            if not isinstance(posts, list):
                result["issues"].append("feed.list")
                posts = []
            for post in posts:
                result["issues"].extend(validate_post(post))
            result["items"] = len(posts)
            result["known_empty"] = not posts and profile.get("postsCount") == 0
            if not posts and profile.get("postsCount", 0) > 0:
                result["issues"].append("feed.unexpected_empty")
            checks.append(result)
            if posts:
                match = POST.fullmatch(posts[0].get("uri", ""))
                if match:
                    result, detail = ask(
                        "/v1/bluesky/post",
                        {"url": f"https://bsky.app/profile/{match[1]}/post/{match[2]}"},
                        stem + "-post",
                    )
                    post = detail.get("post") or {}
                    result["issues"].extend(validate_post(post))
                    if post.get("uri") != posts[0]["uri"] or post.get(
                        "record"
                    ) != posts[0].get("record"):
                        result["issues"].append("post.requested_identity_or_content")
                    if not isinstance(detail.get("replies"), list):
                        result["issues"].append("post.replies")
                    checks.append(result)
            if index < args.pagination_profiles and feed.get("cursor"):
                result, older = ask(
                    "/v1/bluesky/user/posts",
                    {"user_id": actor["did"], "cursor": feed["cursor"]},
                    stem + "-feed-page2",
                )
                items = older.get("feed") or []
                for post in items:
                    result["issues"].extend(validate_post(post))
                if {p.get("uri") for p in posts} & {p.get("uri") for p in items}:
                    result["issues"].append("pagination.overlap")
                if older.get("cursor") == feed["cursor"]:
                    result["issues"].append("pagination.cursor_cycle")
                result["items"] = len(items)
                checks.append(result)
        except Exception as exc:
            checks.append(
                {
                    "path": "transport",
                    "status": None,
                    "seconds": 0,
                    "issues": [type(exc).__name__],
                    "message": str(exc),
                }
            )
        return {"index": index, "did": actor["did"], "checks": checks}

    started = time.monotonic()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(check, enumerate(cohort)):
            rows.append(row)
            if len(rows) % 25 == 0:
                print(
                    json.dumps(
                        {
                            "completed_profiles": len(rows),
                            "failures": sum(
                                bool(c["issues"]) for r in rows for c in r["checks"]
                            ),
                        }
                    ),
                    flush=True,
                )
    checks = [check for row in rows for check in row["checks"]]
    times = sorted(check["seconds"] for check in checks)
    summary = {
        "requested_profiles": args.profiles,
        "profiles": len(rows),
        "checks": len(checks),
        "passed": sum(not check["issues"] for check in checks),
        "failed": sum(bool(check["issues"]) for check in checks),
        "items": sum(check.get("items", 0) for check in checks),
        "known_empty_feeds": sum(check.get("known_empty", False) for check in checks),
        "issues": dict(
            Counter(issue for check in checks for issue in set(check["issues"]))
        ),
        "statuses": dict(Counter(str(check["status"]) for check in checks)),
        "seconds": round(time.monotonic() - started, 3),
        "p95_seconds": times[min(int(len(times) * 0.95), len(times) - 1)]
        if times
        else None,
        "fallback": "disabled",
        "cache": "not_requested",
        "scope": "Public Bluesky topic-discovered cohort",
    }
    (args.output / "results.json").write_text(
        json.dumps({"summary": summary, "profiles": rows}, indent=2) + "\n"
    )
    print(json.dumps(summary))
    return int(summary["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
