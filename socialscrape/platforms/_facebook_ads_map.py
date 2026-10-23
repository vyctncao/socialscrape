"""Small parsing and response helpers for Facebook Ad Library."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse


def ad_id(value: str | None, url: str | None) -> str | None:
    if value and url:
        return None
    if value:
        return value if value.isascii() and value.isdigit() else None
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in {
        "facebook.com", "www.facebook.com", "m.facebook.com",
    }:
        return None
    values = parse_qs(parsed.query).get("id") or []
    return values[0] if len(values) == 1 and values[0].isascii() and values[0].isdigit() else None


def iso_date(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def decorate_ad(ad: dict[str, Any]) -> dict[str, Any]:
    out = dict(ad)
    ident = str(out.get("ad_archive_id") or "")
    out.setdefault("political_countries", [])
    out["url"] = f"https://www.facebook.com/ads/library?id={ident}"
    out["start_date_string"] = iso_date(out.get("start_date"))
    out["end_date_string"] = iso_date(out.get("end_date"))
    return out


def trim_ad(ad: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "ad_archive_id", "is_active", "page_id", "page_name", "is_aaa_eligible",
        "political_countries", "reach_estimate", "snapshot", "spend", "start_date",
        "end_date", "publisher_platform", "total_active_time", "url",
        "start_date_string", "end_date_string",
    }
    return {key: value for key, value in ad.items() if key in keep}


def detail(ad: dict[str, Any], *, trim: bool = False) -> dict[str, Any]:
    ident = str(ad.get("ad_archive_id") or "")
    snapshot = dict(ad.get("snapshot")) if isinstance(ad.get("snapshot"), dict) else {}
    if isinstance(snapshot.get("body"), dict):
        snapshot["body"] = snapshot["body"].get("text") or ""
    if isinstance(snapshot.get("page_categories"), list):
        snapshot["page_categories"] = {str(index): value for index, value in enumerate(snapshot["page_categories"])}
    if str(snapshot.get("page_id", "")).isascii() and str(snapshot.get("page_id", "")).isdigit():
        snapshot["page_id"] = int(snapshot["page_id"])
    if snapshot.get("event") is None:
        snapshot["event"] = []
    if trim:
        snapshot_keep = {
            "body", "title", "caption", "cta_text", "cta_type", "link_description", "link_url",
            "display_format", "page_id", "page_name", "page_profile_uri", "page_profile_picture_url",
            "images", "videos", "cards",
        }
        snapshot = {key: value for key, value in snapshot.items() if key in snapshot_keep}
    number = lambda value: int(value) if str(value).isascii() and str(value).isdigit() else value
    platforms = ad.get("publisher_platform") or []
    return {
        "adArchiveID": number(ident),
        "endDate": ad.get("end_date"),
        "isAAAEligible": bool(ad.get("is_aaa_eligible")),
        "isActive": bool(ad.get("is_active")),
        "pageID": number(ad.get("page_id")),
        "pageName": ad.get("page_name") or snapshot.get("page_name"),
        "politicalCountries": ad.get("political_countries") or [],
        "reachEstimate": ad.get("reach_estimate"),
        "snapshot": snapshot,
        "totalActiveTime": ad.get("total_active_time"),
        "spend": ad.get("spend"),
        "startDate": ad.get("start_date"),
        "publisherPlatform": [str(item).lower() for item in platforms],
        "url": f"https://www.facebook.com/ads/library?id={ident}",
        "startDateString": iso_date(ad.get("start_date")),
        "endDateString": iso_date(ad.get("end_date")),
        "aaa_info": ad.get("aaa_info"),
    }


def video_url(ad: dict[str, Any]) -> str | None:
    snapshot = ad.get("snapshot") if isinstance(ad.get("snapshot"), dict) else {}
    for group in ("videos", "cards", "extra_videos"):
        for item in snapshot.get(group) or []:
            if not isinstance(item, dict):
                continue
            for key in ("video_hd_url", "video_sd_url", "watermarked_video_hd_url",
                        "watermarked_video_sd_url", "video_hd_handle", "video_sd_handle"):
                if isinstance(item.get(key), str) and item[key]:
                    return item[key]
    return None
