#!/usr/bin/env python3
"""Read-only, fallback-disabled consumer-contract check over an external cohort.

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
import math
import os
import re
import ssl
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


def at(value: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def number(value: Any) -> bool:
    if value is None or isinstance(value, bool) or value == "":
        return False
    try:
        return math.isfinite(float(value)) and float(value) >= 0
    except (ValueError, TypeError, OverflowError):
        return False


def stamp(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        instant = (float(value) / (1000 if float(value) > 1e12 else 1)
                   if isinstance(value, (int, float)) else
                   datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
        return 0 < instant < time.time() + 86400
    except (ValueError, TypeError, OverflowError, OSError):
        return False


def url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urllib.parse.urlsplit(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def identity(platform: str, value: str) -> str | None:
    parsed = urllib.parse.urlsplit(value)
    if platform == "youtube":
        return (urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
                or (parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else None))
    matched = re.search(r"/(?:p|reel|reels|tv|video|photo)/([^/?#]+)", parsed.path)
    return matched.group(1) if matched else None


def validate(kind: str, platform: str, body: Any, fixture: dict) -> list[str]:
    """Validate fields the current Stratton adapter would otherwise zero-fill."""
    if not isinstance(body, dict):
        return ["response.object_required"]
    issues: list[str] = []

    def require(ok: bool, field: str) -> None:
        if not ok:
            issues.append(field)

    def count(value: Any, field: str) -> None:
        require(number(value), field)

    if kind == "profile":
        if platform == "instagram":
            user = at(body, "data.user") or {}
            require(bool(user.get("username")), "profile.username")
            require(url(user.get("profile_pic_url_hd") or user.get("profile_pic_url")), "profile.avatar")
            count(at(user, "edge_followed_by.count"), "profile.followers")
            count(at(user, "edge_follow.count"), "profile.following")
            count(at(user, "edge_owner_to_timeline_media.count"), "profile.videos")
            require(isinstance(user.get("is_verified"), bool), "profile.verified")
        elif platform == "tiktok":
            user = body.get("user") or {}
            stats = body.get("stats") or body.get("statsV2") or {}
            require(bool(user.get("uniqueId")), "profile.username")
            require(url(user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb")),
                    "profile.avatar")
            for field in ("followerCount", "followingCount", "videoCount", "heartCount"):
                count(stats.get(field), "profile." + field)
            require(isinstance(user.get("verified"), bool), "profile.verified")
        else:
            require(bool(body.get("channelId")) and bool(body.get("name")), "profile.identity")
            count(body.get("subscriberCount"), "profile.followers")
            count(body.get("videoCount"), "profile.videos")
            require(isinstance(body.get("isVerified"), bool), "profile.verified")
        return issues

    if kind == "feed":
        items = body.get("aweme_list" if platform == "tiktok" else "shorts" if platform == "youtube" else "items")
        require(isinstance(items, list), "feed.items")
        if not isinstance(items, list):
            return issues
        ids = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                issues.append(f"feed[{index}].object_required")
                continue
            item = item.get("media", item)
            if not isinstance(item, dict):
                issues.append(f"feed[{index}].object_required")
                continue
            if platform == "tiktok":
                ident = item.get("aweme_id")
                dated = item.get("create_time")
                views = at(item, "statistics.play_count")
                is_video = not bool(item.get("image_post_info"))
                caption_known = "desc" in item
            elif platform == "instagram":
                ident, dated = item.get("code"), item.get("taken_at")
                views = item.get("play_count", item.get("ig_play_count"))
                is_video = item.get("media_type") == 2
                caption_known = "caption" in item
            else:
                ident, dated, views = item.get("id"), item.get("publishDate"), item.get("viewCountInt")
                is_video, caption_known = True, "title" in item
                count(item.get("likeCountInt"), f"feed[{index}].likes")
                count(item.get("commentCountInt"), f"feed[{index}].comments")
            require(bool(ident), f"feed[{index}].identity")
            require(stamp(dated), f"feed[{index}].date")
            if is_video:
                count(views, f"feed[{index}].views")
            require(caption_known, f"feed[{index}].caption")
            if ident:
                ids.append(str(ident))
        require(len(ids) == len(set(ids)), "feed.duplicate_ids")
        if body.get("has_more") is True:
            require(body.get("max_cursor") is not None, "feed.next_cursor")
        if platform == "instagram" and body.get("more_available") is True:
            require(bool(feed_cursor(platform, body)), "feed.next_cursor")
        return issues

    expected = identity(platform, fixture["url"])
    if platform == "instagram":
        media = at(body, "data.xdt_shortcode_media") or {}
        require(media.get("shortcode") == expected, "post.identity")
        require(isinstance(media.get("is_video"), bool), "post.media_type")
        views = media.get("video_play_count", media.get("video_view_count"))
        if media.get("is_video"):
            count(views, "post.views")
        count(at(media, "edge_media_preview_like.count"), "post.likes")
        count(at(media, "edge_media_to_parent_comment.count"), "post.comments")
        require(stamp(media.get("taken_at_timestamp")), "post.date")
        require(url(media.get("display_url") or media.get("thumbnail_src")), "post.thumbnail")
    elif platform == "tiktok":
        media = body.get("aweme_detail") or {}
        require(str(media.get("aweme_id")) == expected, "post.identity")
        require(bool(at(media, "author.unique_id")), "post.author")
        stats = media.get("statistics") or {}
        for field in ("play_count", "digg_count", "comment_count", "share_count", "collect_count"):
            count(stats.get(field), "post." + field)
        views = stats.get("play_count")
        require(stamp(media.get("create_time")), "post.date")
    else:
        require(body.get("id") == expected, "post.identity")
        for field in ("viewCountInt", "likeCountInt", "commentCountInt"):
            count(body.get(field), "post." + field)
        views = body.get("viewCountInt")
        require(stamp(body.get("publishDate")), "post.date")
    if number(views) and float(views) == 0 and number(fixture.get("previous_views")):
        require(float(fixture["previous_views"]) == 0, "post.positive_counter_became_zero")
    return issues


def feed_cursor(platform: str, body: dict) -> Any:
    if platform == "youtube":
        return body.get("continuationToken")
    if platform == "instagram":
        return body.get("next_max_id") or body.get("nextMaxId") or body.get("max_id")
    return body.get("max_cursor") if body.get("has_more") else None


def feed_ids(platform: str, body: dict) -> set[str]:
    items = body.get({"instagram": "items", "tiktok": "aweme_list", "youtube": "shorts"}[platform], [])
    field = {"instagram": "code", "tiktok": "aweme_id", "youtube": "id"}[platform]
    return {str(item.get("media", item)[field]) for item in items
            if isinstance(item, dict) and isinstance(item.get("media", item), dict)
            and item.get("media", item).get(field)} if isinstance(items, list) else set()


def pagination_issues(platform: str, body: dict, seen_ids: set[str], seen_cursors: set[str]) -> list[str]:
    issues = []
    if feed_ids(platform, body) & seen_ids:
        issues.append("feed.repeated_ids_across_pages")
    cursor = feed_cursor(platform, body)
    if cursor is not None and str(cursor) in seen_cursors:
        issues.append("feed.cursor_cycle")
    return issues


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
            if cursor is not None:
                name = {"instagram": "next_max_id", "tiktok": "max_cursor", "youtube": "continuationToken"}[platform]
                params["max_id" if case.get("variant") == "reels" else name] = cursor
        else:
            path = {"instagram": "/v1/instagram/post", "tiktok": "/v2/tiktok/video",
                    "youtube": "/v1/youtube/video"}[platform]
            params = {"url": case["url"]}
        headers = {"x-socialscrape-fallback": "off", "Accept": "application/json"}
        if key:
            headers["x-api-key"] = key
        is_post = kind == "profile" and platform == "instagram"
        request = urllib.request.Request(args.base_url.rstrip("/") + path
                                         + ("" if is_post else "?" + urllib.parse.urlencode(params)),
                                         data=json.dumps(params).encode() if is_post else None,
                                         headers={**headers, **({"Content-Type": "application/json"} if is_post else {})})
        with semaphores[platform]:
            start = time.monotonic()
            status, body, response_headers, network_error = 0, {}, {}, None
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
            elapsed = time.monotonic() - start
        if not isinstance(body, dict):
            body = {}
            network_error = "InvalidResponseEnvelope"
        issues = validate(kind, platform, body, case) if status == 200 and body.get("success") is True else []
        fallback = next((v for k, v in response_headers.items() if k.lower() == "x-socialscrape-fallback"), None)
        if fallback and fallback != "off":
            issues.append("provider.fallback_used")
        if elapsed > 60:
            issues.append("request.exceeds_consumer_60s_timeout")
        success = status == 200 and body.get("success") is True and not issues and not network_error
        return {"case_id": case["case_id"] + (f"-page{page}" if page > 1 else ""), "kind": kind, "platform": platform,
                "fixture": case, "path": path, "status": status, "seconds": round(elapsed, 3),
                "outcome": "passed" if success else "failed", "issues": issues,
                "error": body.get("error"), "network_error": network_error, "fallback": fallback,
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
               "base_url": args.base_url, "fallback": "off", "cases": len(results),
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
