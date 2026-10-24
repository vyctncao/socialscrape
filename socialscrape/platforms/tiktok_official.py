"""Official TikTok Research, Commercial Content and Shop API routes."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from fastapi import APIRouter, Query

from ..config import get_settings
from ..errors import NotSupported, UpstreamError
from ..registry import sc_get
from ..ytdlp import base_options

router = APIRouter()
_FIELDS = ("ad.id,ad.first_shown_date,ad.last_shown_date,ad.status,ad.status_statement,ad.videos,"
           "ad.image_urls,ad.reach,ad.title,ad.external_url,ad.download_url,ad.call_to_action,"
           "ad.advertising_objective,advertiser.country_code,advertiser.business_id,advertiser.business_name,"
           "advertiser.paid_for_by,advertiser.follower_count,advertiser.avatar_url,advertiser.profile_url,"
           "ad_group.targeting_info,ad.rejection_info")


def _official_token() -> str:
    token = get_settings().tiktok_research_token
    if not token:
        raise NotSupported("Configure SOCIALSCRAPE_TIKTOK_RESEARCH_ACCESS_TOKEN with TikTok Research API scopes",
                           code="platform_oauth_required", platform="tiktok",
                           scopes=["research.data.basic", "research.adlib.basic"])
    return token


def _post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    token = _official_token()
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json", "Accept": "application/json"})
    proxy = base_options("tiktok").get("proxy")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=get_settings().socket_timeout) as response:
            data = json.load(response)
    except Exception as exc:
        raise UpstreamError("TikTok official API request failed") from exc
    if not isinstance(data, dict):
        raise UpstreamError("TikTok official API returned invalid data")
    error = data.get("error")
    if isinstance(error, dict) and error.get("code") not in (None, "ok"):
        raise UpstreamError(f"TikTok official API failed: {error.get('message') or error.get('code')}")
    return data


@sc_get(router, "/v1/tiktok/ad-library/ad", source="official TikTok Commercial Content API ad detail",
        status="needs_setup", notes="Requires SOCIALSCRAPE_TIKTOK_RESEARCH_ACCESS_TOKEN with research.adlib.basic.")
async def ad_detail(ad_id: str = Query(...)):
    url = "https://open.tiktokapis.com/v2/research/adlib/ad/detail/?" + urllib.parse.urlencode({"fields": _FIELDS})
    data = _post(url, {"ad_id": int(ad_id)})
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    ad = payload.get("ad") if isinstance(payload.get("ad"), dict) else payload
    return {**ad, "id": str(ad.get("id") or ad_id)}


@sc_get(router, "/v1/tiktok/ad-library/search", source="official TikTok Commercial Content API ad query",
        status="needs_setup", notes="Requires SOCIALSCRAPE_TIKTOK_RESEARCH_ACCESS_TOKEN with research.adlib.basic.")
async def ad_search(query: str = Query(...), cursor: str | None = None, adv_biz_ids: str | None = None,
                    advertiser_name: str | None = None):
    url = "https://open.tiktokapis.com/v2/research/adlib/ad/query/?" + urllib.parse.urlencode({"fields": _FIELDS})
    term = advertiser_name or query
    body: dict[str, Any] = {"filters": {"country_code_list": ["US"]}, "search_term": term,
                            "search_type": "fuzzy_phrase", "max_count": 10}
    if adv_biz_ids:
        body["filters"]["advertiser_business_id_list"] = [value.strip() for value in adv_biz_ids.split(",") if value.strip()]
    if cursor:
        body["search_id"] = cursor
    payload = _post(url, body).get("data") or {}
    return {"ads": payload.get("ads") or [], "cursor": payload.get("search_id"),
            "has_more": bool(payload.get("has_more"))}


@sc_get(router, "/v1/tiktok/user/audience", source="TikTok authorized creator analytics",
        status="needs_setup", notes="Audience demographics are not exposed by TikTok's public Research User Info API. Requires a future "
              "authorized Creator Marketplace analytics integration rather than fabricated public estimates.")
async def audience(handle: str = Query(...)):
    _official_token()
    raise NotSupported("TikTok's official Research User Info API does not expose audience demographics; configure a "
                       "Creator Marketplace analytics integration", code="platform_scope_unavailable",
                       platform="tiktok", required_capability="creator_audience_analytics")
