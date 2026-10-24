"""TikTok Commercial Content Library and Creative Center ad endpoints.

With a TikTok Research token (tiktok_official.research_token) the official Commercial Content API answers searches and
the ads Creative Center doesn't know, in place of the public library, which refuses many server IPs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Query
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError

from ..cursor import decode_cursor, encode_cursor
from ..errors import ApiError, BadRequest, NotFound, UpstreamBlocked, UpstreamError
from ..registry import sc_get
from ..ytdlp import not_json, with_ydl
from . import tiktok_official
from .tiktok import _undecodable

router = APIRouter()
P = "tiktok"
UNSUPPORTED: dict[str, str] = {}

_LIB = "https://library.tiktok.com"
_CC = "https://ads.tiktok.com/creative_radar_api/v1"
_CC_SECRET = "A7B&9z#1G6$2K@8M!3"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140 Safari/537.36"
_ID = re.compile(r"[0-9]{1,25}")
_RETRY_DELAY = 1.5


def _ad_id(value: str | None) -> str:
    ident = (value or "").strip()
    if _ID.fullmatch(ident):
        return ident
    parsed = urlparse(ident)
    if parsed.scheme in ("http", "https") and parsed.hostname:
        host = parsed.hostname.lower()
        if host == "library.tiktok.com" and parsed.path.rstrip("/") == "/ads/detail":
            values = parse_qs(parsed.query).get("ad_id", [])
            if len(values) == 1 and _ID.fullmatch(values[0]):
                return values[0]
        if host == "ads.tiktok.com":
            match = re.fullmatch(r"/business/creativecenter/topads/([0-9]{1,25})(?:/pc/[A-Za-z-]+)?/?", parsed.path)
            if match and not parsed.query:
                return match.group(1)
    raise BadRequest("ad_id must be a numeric TikTok ad id or supported TikTok ad URL")


def _search_params(query: str | None, advertiser_name: str | None, adv_biz_ids: str | None) -> tuple[str, str, str]:
    query = (query or "").strip()
    advertiser_name = (advertiser_name or "").strip()
    adv_biz_ids = (adv_biz_ids or "").strip()
    if bool(query) == bool(advertiser_name):
        raise BadRequest("Provide exactly one of: query, advertiser_name")
    if adv_biz_ids and not advertiser_name:
        raise BadRequest("adv_biz_ids requires advertiser_name")
    if adv_biz_ids and not _ID.fullmatch(adv_biz_ids):
        raise BadRequest("adv_biz_ids must be a numeric TikTok advertiser id")
    return query, advertiser_name, adv_biz_ids


def _lib_headers(token: str | None = None) -> dict[str, str]:
    headers = {"User-Agent": _UA, "Origin": _LIB, "Referer": f"{_LIB}/ads/", "Content-Type": "application/json"}
    if token:
        headers["X-CCL-STR"] = token
    return headers


def _bootstrap(ie: Any) -> tuple[str, list[str]]:
    """(X-CCL-STR token, regions), asked for as library.tiktok.com/ads asks on load: /api/v1/location, then
    /api/v1/support-regions.

    The library only honors a config_str minted after the client's location call: one asked for on its own is
    refused by /api/v1/search with HTTP 421 "system busy", from a browser tab too, while the same request with a
    token minted after /location (or one copied out of a browser tab) answers. Minting another token also voids
    the one before it, so concurrent searches can void each other's (_search asks once more).
    """
    _download_json(ie, f"{_LIB}/api/v1/location", "location", headers=_lib_headers(), impersonate=True)
    response = _download_json(ie, f"{_LIB}/api/v1/support-regions", "regions", query={"lang": "en-US"},
                              headers=_lib_headers(), impersonate=True)
    if not isinstance(response.get("config_str"), str) or not isinstance(response.get("regions"), list):
        raise UpstreamError("TikTok Ad Library returned malformed bootstrap data")
    regions = [str(item["code"]) for item in response["regions"]
               if isinstance(item, dict) and isinstance(item.get("code"), str)]
    if not regions:
        raise UpstreamError("TikTok Ad Library returned no supported regions")
    return response["config_str"], regions


def _http_status(exc: BaseException) -> int | None:
    """The status of the HTTPError in `exc`'s cause chain, if any."""
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, HTTPError):
            return exc.status
        cause = getattr(exc, "cause", None) or exc.__cause__ or exc.__context__
        if not isinstance(cause, BaseException):
            break
        exc = cause
    return None


def _is_blocked(exc: BaseException) -> bool:
    if _http_status(exc) in (421, 429):
        return True
    root = exc
    seen: set[int] = set()
    while id(root) not in seen:
        seen.add(id(root))
        cause = getattr(root, "cause", None) or root.__cause__ or root.__context__
        if not isinstance(cause, BaseException):
            break
        root = cause
    return bool(re.search(r"system busy|challenge|captcha|rate.?limit", str(root), re.I))


def _download_json(ie: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """`ie._download_json`, its blocks a 503: HTTP 421/429, a "system busy"/challenge answer, and any body that isn't
    JSON (an empty one, Akamai's "Access Denied" page, a cut-off answer), which yt-dlp reports as "Failed to parse
    JSON" (a 500 before)."""
    try:
        response = ie._download_json(*args, **kwargs)
    except ExtractorError as exc:
        if _is_blocked(exc):
            raise UpstreamBlocked("TikTok blocked the Ad Library request; retry later or use another proxy") from exc
        if (body := _undecodable(exc)) is not None:
            raise UpstreamBlocked(f"TikTok answered the Ad Library request with {not_json(body)} instead of JSON, as "
                                  "it does for an IP it is blocking; retry later or use another proxy") from exc
        raise
    if not isinstance(response, dict):
        raise UpstreamError("TikTok returned malformed ad data")
    return response


def _search(ydl: Any, query: str, advertiser_name: str, adv_biz_ids: str,
            cursor: str | None) -> dict[str, Any]:
    ie = ydl.get_info_extractor("TikTok")
    token, _ = _bootstrap(ie)  # its regions are what region=all searches: the web app's "All countries"
    matches: list[dict[str, Any]] = []
    resolved: str | None = None
    term, query_type = query or advertiser_name, "1"
    if advertiser_name:
        suggestion = _download_json(
            ie, f"{_LIB}/api/v1/suggestion", "advertiser suggestion",
            data=json.dumps({"query": advertiser_name, "limit": 20, "suggest_type": "1"}).encode(),
            headers=_lib_headers())
        suggestion_data = suggestion.get("data")
        if not isinstance(suggestion_data, dict) or not isinstance(suggestion_data.get("adv_names"), list):
            raise UpstreamError("TikTok Ad Library returned malformed advertiser suggestions")
        matches = [item for item in suggestion_data["adv_names"] if isinstance(item, dict)
                   and isinstance(item.get("name"), str) and isinstance(item.get("ids"), str)]
        selected = next((item for item in matches if item["ids"] == adv_biz_ids), None) if adv_biz_ids else (
            matches[0] if matches else None)
        if adv_biz_ids and not selected:
            raise BadRequest("adv_biz_ids was not found for advertiser_name")
        if selected:
            resolved, adv_biz_ids, query_type = selected["name"], selected["ids"], "2"
            term = resolved

    fingerprint = hashlib.sha256(json.dumps([query, advertiser_name, adv_biz_ids], separators=(",", ":")).encode()).hexdigest()[:16]
    state = decode_cursor(cursor) or {}
    expected_keys = {"operation", "fingerprint", "search_id", "offset"} if cursor else set()
    search_id, offset = state.get("search_id", ""), state.get("offset", 0)
    if (set(state) != expected_keys or (cursor and (state.get("operation") != "tiktok_ad_library_search"
            or state.get("fingerprint") != fingerprint)) or not isinstance(search_id, str)
            or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0):
        raise BadRequest("Invalid cursor/continuation token", code="bad_cursor")
    now = int(time.time())
    body = {"query": term, "query_type": query_type, "adv_biz_ids": adv_biz_ids, "order": "last_shown_date,desc",
            "offset": offset, "search_id": search_id, "limit": 12, "ad_type": 0, "ad_status": 0,
            "ages": ["all"], "ad_reach": ["all"], "gender": "ALL"}
    for attempt in range(2):
        try:
            response = _download_json(
                ie, f"{_LIB}/api/v1/search", "ad search", data=json.dumps(body).encode(),
                query={"region": "all", "type": 1, "start_time": now - 30 * 86_400, "end_time": now},
                headers=_lib_headers(token), impersonate=True)
            break
        except UpstreamBlocked as exc:  # 421: a token another request voided; a fresh one is asked for once
            if attempt or _http_status(exc) != 421:
                raise
            time.sleep(_RETRY_DELAY)
            token, _ = _bootstrap(ie)
    if response.get("code") not in (None, 0):
        raise UpstreamBlocked(f"TikTok Ad Library refused the search (code {response.get('code')})")
    raw_ads = response.get("data")
    if raw_ads is None:  # left out when nothing matched: {"total": 0, "has_more": false, "search_id": .., "code": 0}
        raw_ads = []
    if not isinstance(raw_ads, list):
        raise UpstreamError("TikTok Ad Library returned malformed search results")
    ads = []
    for raw in raw_ads:
        if not isinstance(raw, dict) or not raw.get("id"):
            raise UpstreamError("TikTok Ad Library returned a malformed ad")
        ad = {**raw, "url": f"{_LIB}/ads/detail/?ad_id={raw['id']}"}
        if adv_biz_ids:
            ad["adv_biz_ids"] = adv_biz_ids
        ads.append(ad)
    has_more = bool(response.get("has_more"))
    next_search_id = response.get("search_id")
    next_cursor = encode_cursor({"operation": "tiktok_ad_library_search", "fingerprint": fingerprint,
                                 "search_id": next_search_id, "offset": offset + 1}) \
        if has_more and isinstance(next_search_id, str) and next_search_id else None
    return {"source": "tiktok_public_ads_library", "advertiser_name": advertiser_name or None,
            "resolved_advertiser_name": resolved, "advertiser_matches": matches, "ads": ads,
            "total": response.get("total", 0) if isinstance(response.get("total", 0), int) else 0,
            "has_more": has_more, "cursor": next_cursor}


def _cc_headers() -> dict[str, str]:
    anonymous = str(secrets.randbelow(10**19))
    timestamp = int(time.time())
    digest = hashlib.md5(f"{_CC_SECRET}-{anonymous}-{timestamp}".encode()).hexdigest()
    signature = "".join(format(int(a, 16) ^ int(b, 16), "x") for a, b in zip(digest[:16], digest[16:]))
    return {"User-Agent": _UA, "Referer": "https://ads.tiktok.com/business/creativecenter/",
            "anonymous-user-id": anonymous, "timestamp": str(timestamp), "user-sign": signature, "lang": "en"}


def _creative_ad(ie: Any, ident: str) -> dict[str, Any] | None:
    headers = _cc_headers()
    detail = _download_json(ie, f"{_CC}/top_ads/v2/detail", "ad detail", query={"material_id": ident},
                            headers=headers, impersonate=True)
    if detail.get("code") in (40000, 40004):
        return None
    if detail.get("code") != 0 or not isinstance(detail.get("data"), dict):
        raise UpstreamError(f"TikTok Creative Center returned invalid ad details (code {detail.get('code')})")
    responses = {}
    for name, path in (("analysis", "v2/detail_analysis"), ("recommended", "v2/recommend")):
        try:
            responses[name] = _download_json(
                ie, f"{_CC}/top_ads/{path}", f"ad {name}", query={"material_id": ident},
                headers=headers, impersonate=True)
        except (ExtractorError, ApiError):
            responses[name] = {}
    analysis = responses["analysis"]
    recommended = responses["recommended"]
    analysis_data = analysis.get("data") if analysis.get("code") == 0 and isinstance(analysis.get("data"), dict) else {}
    materials = recommended.get("data", {}).get("materials", []) \
        if recommended.get("code") == 0 and isinstance(recommended.get("data"), dict) else []
    if not isinstance(materials, list):
        materials = []
    return {**detail["data"],
            "creative_center_url": f"https://ads.tiktok.com/business/creativecenter/topads/{ident}/pc/en",
            "detail_analysis": analysis_data.get("detail_analysis") or analysis_data.get("summary"),
            "interactive_time_analysis": analysis_data.get("interactive_time_analysis") or {},
            "recommended_for_you": [item for item in materials if isinstance(item, dict)]}


def _http_body(exc: BaseException) -> str:
    """The start of the body of the HTTPError in `exc`'s cause chain ('' when there is none or it can't be read)."""
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, HTTPError):
            try:
                return exc.response.read(512).decode("utf-8", "replace")
            except Exception:  # closed or already read
                return ""
        cause = getattr(exc, "cause", None) or exc.__cause__ or exc.__context__
        if not isinstance(cause, BaseException):
            break
        exc = cause
    return ""


def _library_ad(ie: Any, ident: str) -> dict[str, Any]:
    token, _ = _bootstrap(ie)
    try:
        response = _download_json(ie, f"{_LIB}/api/v1/items/{ident}/details", "ad detail",
                                  query={"lang": "en-US"}, headers=_lib_headers(token), impersonate=True)
    except ExtractorError as exc:
        # An id the library doesn't have (never was, or aged out) is answered HTTP 425 with the body "not found"
        if _http_status(exc) == 425 and "not found" in _http_body(exc).lower():
            raise NotFound("TikTok ad not found") from exc
        raise
    if response.get("code") in (40000, 40004, 404) or response.get("data") is None:
        raise NotFound("TikTok ad not found")
    data = response.get("data")
    if response.get("code") not in (None, 0) or not isinstance(data, dict):
        raise UpstreamError("TikTok Ad Library returned malformed ad details")
    ad, advertiser, targeting = data.get("ad"), data.get("advertiser"), data.get("targeting")
    if not isinstance(ad, dict) or not isinstance(advertiser, dict) or not isinstance(targeting, dict):
        raise UpstreamError("TikTok Ad Library returned malformed ad details")
    videos = ad.get("videos") if isinstance(ad.get("videos"), list) else []
    video = videos[0] if videos and isinstance(videos[0], dict) else {}
    location = targeting.get("location")
    location_data = location.get("data") if isinstance(location, dict) else None
    countries = [item["region"] for item in location_data
                 if isinstance(item, dict) and isinstance(item.get("region"), str)] \
        if isinstance(location_data, list) else []
    if not countries and isinstance(targeting.get("countries"), list):
        countries = [item for item in targeting["countries"] if isinstance(item, str)]
    # The library has no engagement, cost or CTR figures and no video size: those stay null, not 0.
    return {"ad_title": str(ad.get("title") or ""), "brand_name": str(advertiser.get("name") or ""),
            "comment": None, "cost": None, "country_code": countries, "ctr": None,
            "favorite": False, "has_summary": False, "highlight_text": "", "id": str(ad.get("id") or ident),
            "industry_key": "", "is_search": False, "keyword_list": None, "landing_page": "", "like": None,
            "objective_key": str(ad.get("advertising_objective") or ""), "objectives": [], "pattern_label": [],
            "share": None, "source": "TikTok Commercial Content Library", "source_key": None,
            "video_info": {"vid": "", "duration": None, "cover": str(video.get("cover_img") or ""),
                           "video_url": {"720p": str(video.get("video_url") or "")}, "width": None, "height": None},
            "voice_over": False,
            "creative_center_url": f"https://ads.tiktok.com/business/creativecenter/topads/{ident}/pc/en",
            "detail_analysis": None, "interactive_time_analysis": {}, "recommended_for_you": []}


def _ad(ydl: Any, ident: str) -> dict[str, Any]:
    ie = ydl.get_info_extractor("TikTok")
    if found := _creative_ad(ie, ident):
        return found
    return tiktok_official.ad_detail(ident) if tiktok_official.research_token() else _library_ad(ie, ident)


@sc_get(router, "/v1/tiktok/ad-library/search",
        source="TikTok Commercial Content Library API, or the official Commercial Content API with a research token",
        notes=("With SOCIALSCRAPE_TIKTOK_RESEARCH_ACCESS_TOKEN (research.adlib.basic) the official API answers. "
               "Without it, the public library answers as its web app asks it: /api/v1/location, then "
               "/api/v1/support-regions for the X-CCL-STR token, then /api/v1/search over the last 30 days of all "
               "regions (12 ads a page; no match is an empty ads list). A token minted without the location call "
               "first is refused with HTTP 421 system busy; a 421 is asked once more with a fresh token, then (like "
               "429) is a 503 upstream_blocked."))
async def tiktok_ad_library_search(
    query: str | None = Query(None, description=(
        "General ad search. Provide either query or advertiser_name, not both.")),
    advertiser_name: str | None = Query(None, description=(
        "Advertiser name resolved through TikTok's typeahead. Provide either advertiser_name or query, not both.")),
    adv_biz_ids: str | None = Query(None, description=(
        "TikTok advertiser business ID used with advertiser_name to pin the exact advertiser.")),
    cursor: str | None = Query(None, description="Opaque cursor returned from the previous response."),
) -> dict[str, Any]:
    query, advertiser_name, adv_biz_ids = _search_params(query, advertiser_name, adv_biz_ids)
    if tiktok_official.research_token():
        return await asyncio.to_thread(tiktok_official.ad_search, query or None, cursor, adv_biz_ids or None,
                                       advertiser_name or None)
    return await with_ydl(P, lambda ydl: _search(ydl, query, advertiser_name, adv_biz_ids, cursor))


@sc_get(router, "/v1/tiktok/ad-library/ad", source="TikTok Creative Center and Commercial Content Library APIs",
        notes=("Creative Center details fall back to the official Commercial Content API with a research token, "
               "else to the public Commercial Content Library (bootstrapped as the search is), for non-Top Ads."))
async def tiktok_ad_library_ad(
    ad_id: str = Query(..., description=(
        "Creative Center Top Ads material ID or URL, or a public Ads Library ad ID or detail URL.")),
) -> dict[str, Any]:
    ident = _ad_id(ad_id)
    return await with_ydl(P, lambda ydl: _ad(ydl, ident))
