#!/usr/bin/env python3
"""Fallback-disabled public SoundCloud hard test with strict completeness checks.

Keep cohorts and response captures outside the repository. Unknown platform
counters fail completeness rather than becoming zero. An empty native page may
have a real continuation; the runner follows it instead of assuming zero tracks.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse, asyncio, concurrent.futures, json, os, ssl, time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import certifi
from socialscrape.platforms import soundcloud as source

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://127.0.0.1:8794")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--cohort", type=Path)
parser.add_argument("--key-file", type=Path)
args = parser.parse_args()
os.umask(0o077)
args.output.mkdir(parents=True, exist_ok=True)
headers = {"Accept": "application/json", "x-socialscrape-fallback": "off"}
if args.key_file:
    headers["x-api-key"] = args.key_file.read_text().strip()
context = ssl.create_default_context(cafile=certifi.where())


async def discover():
    groups = []
    seen = set()
    for query in ["music", "jazz", "beats"]:
        raw = await source._run(
            lambda ie: ie._call_api(
                source.API + "search/users",
                "cohort",
                query={"q": query, "limit": 100},
                headers={"Accept": "application/json"},
            )
        )
        (args.output / f"discovery-{query}.json").write_text(json.dumps(raw))
        group = []
        for user in raw.get("collection", []):
            if (
                user.get("kind") == "user"
                and user.get("permalink")
                and user.get("track_count", 0) >= 20
                and user["id"] not in seen
            ):
                group.append(
                    {
                        "id": user["id"],
                        "handle": user["permalink"],
                        "previous_followers": user.get("followers_count"),
                        "query": query,
                    }
                )
                seen.add(user["id"])
        groups.append(group)
    out = []
    for index in range(max(map(len, groups))):
        for group in groups:
            if index < len(group):
                out.append(group[index])
    if len(out) < 100:
        raise RuntimeError(
            "Not enough distinct public artists with existing tracks: " + str(len(out))
        )
    return out[:100]


cohort = json.loads(args.cohort.read_text()) if args.cohort else asyncio.run(discover())
(args.output / "cohort.json").write_text(json.dumps(cohort, indent=2))


def ask(path, params, stem):
    start = time.monotonic()
    request = Request(
        args.base_url.rstrip("/") + path + "?" + urlencode(params), headers=headers
    )
    try:
        r = urlopen(request, context=context, timeout=60)
    except HTTPError as exc:
        r = exc
    with r:
        body = json.loads(r.read(8 * 1024 * 1024))
        check = {
            "path": path,
            "status": r.status,
            "seconds": round(time.monotonic() - start, 3),
            "issues": [],
        }
        if r.status != 200 or not isinstance(body, dict) or not body.get("success"):
            check["issues"].append("http_or_success")
        if r.headers.get("x-socialscrape-fallback") == "scrapecreators":
            check["issues"].append("paid_fallback")
        (args.output / f"{stem}.json").write_text(
            json.dumps(
                {
                    "request": {"path": path, "params": params},
                    "response": body,
                    "result": check,
                }
            )
        )
        return check, body


def track_issues(item):
    out = []
    if (
        not item.get("id")
        or item.get("kind") != "track"
        or not item.get("permalink_url")
    ):
        out.append("track.identity")
    try:
        timestamp = datetime.fromisoformat(
            item["created_at"].replace("Z", "+00:00")
        ).timestamp()
        if not 0 < timestamp < time.time() + 86400:
            out.append("track.date")
    except Exception:
        out.append("track.date")
    for key in [
        "playback_count",
        "likes_count",
        "comment_count",
        "reposts_count",
        "duration",
    ]:
        v = item.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            out.append("track." + key)
    return out


def check(row):
    index, artist = row
    stem = f"{index:04d}"
    checks = []
    try:
        result, profile = ask(
            "/v1/soundcloud/artist", {"handle": artist["handle"]}, stem + "-artist"
        )
        checks.append(result)
        if profile.get("id") != artist["id"] or profile.get("kind") != "user":
            result["issues"].append("artist.identity")
        for key in [
            "followers_count",
            "followings_count",
            "track_count",
            "comments_count",
        ]:
            value = profile.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                result["issues"].append("artist." + key)
        if (
            artist.get("previous_followers", 0) > 0
            and profile.get("followers_count") == 0
        ):
            result["issues"].append("artist.positive_count_became_zero")
        result, feed = ask(
            "/v1/soundcloud/artist/tracks",
            {"handle": artist["handle"]},
            stem + "-tracks",
        )
        checks.append(result)
        tracks = feed.get("tracks") or []
        result["items"] = len(tracks)
        if not tracks and not feed.get("cursor"):
            result["issues"].append("feed.unexpected_empty")
        for item in tracks:
            result["issues"].extend(track_issues(item))
        if tracks:
            item = tracks[0]
            result, detail = ask(
                "/v1/soundcloud/track", {"url": item["permalink_url"]}, stem + "-track"
            )
            checks.append(result)
            result["issues"].extend(track_issues(detail))
            if detail.get("id") != item["id"]:
                result["issues"].append("track.requested_identity")
            if (
                isinstance(item.get("playback_count"), int)
                and not isinstance(item.get("playback_count"), bool)
                and item["playback_count"] > 0
                and detail.get("playback_count") == 0
            ):
                result["issues"].append("track.positive_plays_became_zero")
        if (index < 30 or not tracks) and feed.get("cursor"):
            result, older = ask(
                "/v1/soundcloud/artist/tracks",
                {"handle": artist["handle"], "cursor": feed["cursor"]},
                stem + "-tracks-page2",
            )
            checks.append(result)
            items = older.get("tracks") or []
            result["items"] = len(items)
            for item in items:
                result["issues"].extend(track_issues(item))
            if {t["id"] for t in items} & {t["id"] for t in tracks}:
                result["issues"].append("pagination.overlap")
            if older.get("cursor") == feed["cursor"]:
                result["issues"].append("pagination.cursor_cycle")
    except Exception as exc:
        checks.append(
            {
                "path": "transport",
                "status": None,
                "seconds": 0,
                "issues": [type(exc).__name__],
                "message": str(exc).replace(
                    headers.get("x-api-key", "NO_API_KEY"), "[REDACTED]"
                ),
            }
        )
    return {"index": index, "id": artist["id"], "checks": checks}


start = time.monotonic()
rows = []
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    for row in pool.map(check, enumerate(cohort)):
        rows.append(row)
        if len(rows) % 20 == 0:
            print(
                json.dumps(
                    {
                        "artists": len(rows),
                        "failed_checks": sum(
                            bool(c["issues"]) for r in rows for c in r["checks"]
                        ),
                    }
                ),
                flush=True,
            )
checks = [c for r in rows for c in r["checks"]]
times = sorted(c["seconds"] for c in checks)
summary = {
    "artists": len(rows),
    "checks": len(checks),
    "passed": sum(not c["issues"] for c in checks),
    "failed": sum(bool(c["issues"]) for c in checks),
    "items": sum(c.get("items", 0) for c in checks),
    "issues": dict(Counter(i for c in checks for i in set(c["issues"]))),
    "statuses": dict(Counter(str(c["status"]) for c in checks)),
    "seconds": round(time.monotonic() - start, 3),
    "p95_seconds": times[min(len(times) - 1, int(len(times) * 0.95))],
    "fallback": "disabled",
    "cache": "not_requested",
    "scope": "100 public SoundCloud artists from three search topics",
}
(args.output / "results.json").write_text(
    json.dumps({"summary": summary, "artists": rows}, indent=2)
)
print(json.dumps(summary))
raise SystemExit(int(summary["failed"] > 0))
