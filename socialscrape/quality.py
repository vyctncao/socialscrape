"""Tracking contracts shared by production failover and the read-only qualification runner.

Unknown counters are not measured zeros. These checks cover the profile, post
and feed responses consumed by tracking; other catalog routes pass through.
"""
from __future__ import annotations

import math
import re
import time
import urllib.parse
from datetime import datetime
from typing import Any

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
    if platform == "instagram" and re.fullmatch(r"[A-Za-z0-9_-]{5,64}", value):
        return value
    if platform == "instagram" and parsed.path.startswith("/share/"):
        return None  # a share-link token is not the resolved post's shortcode
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
                for field in ("digg_count", "comment_count", "share_count", "collect_count"):
                    count(at(item, "statistics." + field), f"feed[{index}].{field}")
            elif platform == "instagram":
                ident, dated = item.get("code"), item.get("taken_at")
                views = next((item[key] for key in ("play_count", "ig_play_count", "view_count")
                              if number(item.get(key))), None)
                is_video = item.get("media_type") == 2
                caption_known = "caption" in item
                count(item.get("like_count", item.get("likes")), f"feed[{index}].likes")
                count(item.get("comment_count", item.get("comments")), f"feed[{index}].comments")
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
        require(bool(media.get("shortcode")) and (expected is None or media.get("shortcode") == expected), "post.identity")
        require(isinstance(media.get("is_video"), bool), "post.media_type")
        views = next((media[key] for key in ("video_play_count", "video_view_count")
                      if number(media.get(key))), None)
        if media.get("is_video"):
            count(views, "post.views")
        count(at(media, "edge_media_preview_like.count"), "post.likes")
        count(at(media, "edge_media_to_parent_comment.count"), "post.comments")
        require(stamp(media.get("taken_at_timestamp")), "post.date")
        require(url(media.get("display_url") or media.get("thumbnail_src")), "post.thumbnail")
    elif platform == "tiktok":
        media = body.get("aweme_detail") or {}
        require(bool(media.get("aweme_id")) and (expected is None or str(media.get("aweme_id")) == expected), "post.identity")
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


OPERATIONS = {
    "/v1/instagram/profile": ("profile", "instagram"),
    "/v1/tiktok/profile": ("profile", "tiktok"),
    "/v1/youtube/channel": ("profile", "youtube"),
    "/v1/instagram/post": ("video", "instagram"),
    "/v2/tiktok/video": ("video", "tiktok"),
    "/v1/youtube/video": ("video", "youtube"),
    "/v2/instagram/user/posts": ("feed", "instagram"),
    "/v1/instagram/user/reels": ("feed", "instagram"),
    "/v3/tiktok/profile/videos": ("feed", "tiktok"),
    "/v1/youtube/channel/shorts": ("feed", "youtube"),
}


def prepare(path: str, answer: dict) -> dict:
    """Add numeric aliases only when the source actually supplies a count; never fill missing values with zero."""
    if path == "/v1/youtube/channel" and not number(answer.get("videoCount")):
        from .util import parse_count
        count = parse_count(answer.get("videoCountText"))
        if count is not None and count >= 0:
            return {**answer, "videoCount": count}
    return answer


_SHORT_COUNT = re.compile(r"feed\[(\d+)\]\.(likes|comments)")


def unfillable(path: str, answer: dict, missing: list[str]) -> bool:
    """Whether every missing field is a count YouTube shows nobody, which ScrapeCreators, reading the same pages,
    answers with the same null: a channel whose about panel loaded (it has a joined date) without a subscriber count,
    or a short read in full (its date and views) without its like or comment count. From Oct 26 to Nov 3 every one
    of the 54 YouTube channel and shorts fallbacks ended incomplete too, a credit each."""
    if not missing:
        return False
    if path == "/v1/youtube/channel":
        return (missing == ["profile.followers"] and bool(answer.get("joinedDateText"))
                and not answer.get("subscriberCountText"))
    if path == "/v1/youtube/channel/shorts":
        shorts = answer.get("shorts")
        for field in missing:
            match = _SHORT_COUNT.fullmatch(field)
            index = int(match.group(1)) if match else -1
            item = shorts[index] if isinstance(shorts, list) and 0 <= index < len(shorts) else None
            if not (isinstance(item, dict) and stamp(item.get("publishDate")) and number(item.get("viewCountInt"))):
                return False
        return True
    return False


def issues(path: str, params: dict, answer: dict) -> list[str]:
    operation = OPERATIONS.get(path)
    if operation is None:
        return []
    # Media-download callers consume URLs, not tracking counters. Hidden engagement must not block archiving.
    if str(params.get("download_media", "")).lower() in ("true", "1"):
        return []
    if answer.get("success") is False:
        return ["response.upstream_failed"]
    kind, platform = operation
    try:
        return validate(kind, platform, answer, params)
    except (TypeError, ValueError, AttributeError, KeyError):
        return ["response.invalid_shape"]
