"""Google Ads Transparency Center endpoints.

Every call is one RPC to adstransparency.google.com (plus one renderer fetch per distinct ad preview). Google answers
an IP it has flagged with a 302 to www.google.com/sorry, its "unusual traffic" CAPTCHA served as HTTP 429, on every
RPC for minutes to hours, and each further request keeps the flag fresh. So after a refusal this egress (the direct
connection, or one proxy) is left alone for a cooling window that doubles while Google keeps refusing: calls inside
it fail fast with 503 upstream_blocked and Retry-After instead of re-tripping the check, and with_ydl moves on to the
platform's next configured proxy, which has its own window.
"""

from __future__ import annotations

import html
import json
import logging
import math
import re
import threading
import time
import urllib.parse
from typing import Any

from fastapi import APIRouter, Query
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError

from ..errors import BadRequest, NotFound, UpstreamBlocked, UpstreamError, require_one
from ..registry import sc_get
from ..ytdlp import with_ydl
from . import _google_ads_map as M

router = APIRouter()
log = logging.getLogger("socialscrape.google_ads")

_BASE = "https://adstransparency.google.com"
_RPC = _BASE + "/anji/_/rpc/"
_HEADERS = {  # what the Transparency Center's own XHRs send, not yt-dlp's page-navigation defaults
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "Origin": _BASE,
    "Referer": _BASE + "/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Same-Domain": "1",
}
_AD_URL = re.compile(r"^https://adstransparency\.google\.com/advertiser/(AR\d+)/creative/(CR\d+)/?$")
_ADVERTISER = re.compile(r"^AR\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")
_SORRY = re.compile(r"^https?://(?:www\.|ipv4\.|ipv6\.)?google\.[a-z.]+/sorry/", re.I)
_COOL_FIRST, _COOL_MAX = 60.0, 900.0  # seconds an egress is left alone after a refusal, doubling up to the cap
_DETAIL_GAP = .5  # seconds between hydration lookups: a browser opens one creative at a time

_cool_lock = threading.Lock()
_cooling: dict[str, tuple[float, float]] = {}  # egress -> (_now() at the end of its window, window length)
_now = time.monotonic


def _egress(ydl: Any) -> str:
    """The cooling key of ydl's connection: its proxy URL, or "" for direct. Kept in memory only."""
    params = getattr(ydl, "params", None)
    return str(params.get("proxy") or "") if isinstance(params, dict) else ""


def _check_cooling(ydl: Any) -> None:
    with _cool_lock:
        until = _cooling.get(_egress(ydl), (0.0, 0.0))[0]
    left = until - _now()
    if left > 0:
        raise UpstreamBlocked("Google Ads Transparency is refusing this server's IP (unusual-traffic CAPTCHA); "
                              "not asking again until it cools down", retry_after=math.ceil(left))


def _refused(ydl: Any, retry_after: float = 0.0) -> UpstreamBlocked:
    """Start (or, if Google is still refusing within one window of the last, double) this egress's window."""
    key, now = _egress(ydl), _now()
    with _cool_lock:
        until, window = _cooling.get(key, (0.0, 0.0))
        window = min(window * 2, _COOL_MAX) if window and now < until + window else _COOL_FIRST
        window = max(window, min(retry_after, _COOL_MAX))
        _cooling[key] = (now + window, window)
    log.warning("Google Ads Transparency refused %s; cooling for %ds", "a proxy" if key else "the direct IP", window)
    return UpstreamBlocked("Google Ads Transparency is rate-limiting this server's IP (HTTP 429 / unusual-traffic "
                           "CAPTCHA); retry later or configure a Google proxy", retry_after=math.ceil(window))


def _refusal(exc: ExtractorError) -> float | None:
    """Google's Retry-After (0 without one) when exc is Google refusing the request (HTTP 429, or its /sorry
    CAPTCHA page under any status), else None."""
    cause = exc.cause
    if not isinstance(cause, HTTPError):
        return None
    if cause.status != 429 and not _SORRY.match(cause.response.url or ""):
        return None
    wait = (cause.response.headers.get("Retry-After") or "").strip()
    return float(wait) if wait.isdigit() else 0.0


def _post(ydl: Any, method: str, payload: dict[str, Any], context: str) -> Any:
    """One RPC's decoded JSON. A refusal starts the egress's cooling window; a clean answer ends it."""
    _check_cooling(ydl)
    body = urllib.parse.urlencode({"f.req": json.dumps(payload, separators=(",", ":"))}).encode()
    try:
        text, handle = ydl.get_info_extractor("Generic")._download_webpage_handle(
            _RPC + method + "?authuser=", "google-ads", data=body, headers=_HEADERS)
    except ExtractorError as exc:
        if (wait := _refusal(exc)) is not None:
            raise _refused(ydl, wait) from exc
        raise
    if _SORRY.match(getattr(handle, "url", "") or ""):
        raise _refused(ydl)
    if not text.strip():
        raise UpstreamBlocked(f"Google returned an empty response for {context}; try again later")
    try:
        data = json.loads(text)
    except ValueError:
        if text.lstrip().startswith("<"):  # a consent or interstitial page instead of the RPC answer
            host = urllib.parse.urlsplit(getattr(handle, "url", "") or "").hostname or "an unknown host"
            raise UpstreamBlocked(f"Google answered the {context} request with a web page from {host} "
                                  "instead of data") from None
        raise UpstreamError(f"Google returned malformed {context}") from None
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("code") == 429:
        raise _refused(ydl)
    with _cool_lock:
        _cooling.pop(_egress(ydl), None)
    return data


def _enum(name: str, value: str | None, choices: set[str], default: str | None = None) -> str | None:
    value = value or default
    if value not in choices:
        raise BadRequest(f"{name} must be one of: {', '.join(sorted(choices))}")
    return value


def _region(value: str | None, *, default: str | None = None) -> str | None:
    code = (value or default or "").upper()
    if code and code not in M.REGIONS:
        raise BadRequest("region must be a supported 2-letter country code")
    return code or None


def _date(name: str, value: str | None) -> int | None:
    if value is None:
        return None
    if not _DATE.fullmatch(value):
        raise BadRequest(f"{name} must be a date in YYYY-MM-DD format")
    try:
        import datetime as dt
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise BadRequest(f"{name} must be a valid date in YYYY-MM-DD format") from exc
    return int(value.replace("-", ""))


def _boolean(name: str, value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    if value.casefold() not in {"true", "false"}:
        raise BadRequest(f"{name} must be true or false")
    return value.casefold() == "true"


def _search_payload(*, domain: str | None, advertiser_id: str | None, topic: str, region: str | None,
                    start_date: int | None, end_date: int | None, platform: str | None,
                    ad_format: str | None, cursor: str | None) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if advertiser_id:
        filters["13"] = {"1": [advertiser_id]}
    if domain:
        filters["12"] = {"1": domain, "2": True}
    if ad_format:
        filters["4"] = M.FORMAT_IDS[ad_format]
    if region:
        filters["8"] = [M.REGIONS[region][0]]
    if start_date:
        filters["6"] = start_date
    if end_date:
        filters["7"] = end_date
    if platform:
        filters["14"] = [M.PLATFORM_IDS[platform]]
    viewer: dict[str, Any] = {"1": 2 if topic == "political" else 1, "2": 0}
    if region:
        viewer["3"] = M.REGIONS[region][0]
    payload: dict[str, Any] = {"2": 40, "3": filters, "7": viewer}
    if cursor:
        payload["4"] = urllib.parse.unquote(cursor)
    return payload


def _rpc_error(data: Any, context: str) -> None:
    if not isinstance(data, dict) or "error" not in data:
        return
    error = data["error"]
    code = error.get("code") if isinstance(error, dict) else None
    message = error.get("message", "") if isinstance(error, dict) else str(error)
    if code == 429 or any(word in message.casefold() for word in ("rate limit", "blocked", "challenge")):
        raise UpstreamBlocked(f"Google blocked the {context} request")
    raise UpstreamError(f"Google returned an error for {context}")


def _preview_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.hostname != "displayads-formats.googleusercontent.com"
            or parsed.username or parsed.password or port not in (None, 443)):
        return None
    return value


class _Previews:
    """Preview renderer bodies for one call: each distinct URL is fetched once (variations and hydrated ads share
    them), and none after Google refuses one. A preview only adds copy (headline, description, destination) to a
    variation, so one that can't be fetched leaves those null instead of failing the ad."""

    def __init__(self, ydl: Any):
        self.ydl, self.refused = ydl, False
        self.bodies: dict[str, str | None] = {}

    def fields(self, url: str) -> dict[str, Any]:
        if url not in self.bodies:
            if self.refused:
                return {}
            try:
                self.bodies[url] = self.ydl.get_info_extractor("Generic")._download_webpage(url, "google-ad-preview")
            except ExtractorError as exc:
                self.refused = _refusal(exc) is not None
                log.warning("Google ad preview unavailable (%s)",
                            "refused" if self.refused else type(exc.cause or exc).__name__)
                self.bodies[url] = None
        body = self.bodies[url]
        return M.preview_fields(body) if body is not None else {}


def _variation(previews: _Previews, value: dict[str, Any]) -> dict[str, Any]:
    content_value = value.get("3")
    content = content_value.get("2", "") if isinstance(content_value, dict) else ""
    image = re.search(r'<img[^>]+src=["\']([^"\']+)', content) if isinstance(content, str) else None
    first, second = value.get("1"), value.get("2")
    preview = ((first.get("4") if isinstance(first, dict) else None)
               or (second.get("4") if isinstance(second, dict) else None) or value.get("4"))
    result: dict[str, Any] = {
        "destinationUrl": None,
        "headline": None,
        "description": None,
        "allText": None,
        "imageUrl": html.unescape(image.group(1)) if image else None,
    }
    preview = _preview_url(preview)
    if preview:
        result.update(previews.fields(preview))
    return result


def _detail_with_ydl(ydl: Any, advertiser_id: str, creative_id: str, region: str, url: str,
                     previews: _Previews | None = None) -> dict[str, Any]:
    data = _post(ydl, "LookupService/GetCreativeById",
                 {"1": advertiser_id, "2": creative_id, "5": {"1": 1, "2": M.REGIONS[region][0]}}, "ad details")
    if not isinstance(data, dict):
        raise UpstreamError("Google returned malformed ad details")
    _rpc_error(data, "ad details")
    record = data.get("1")
    if not isinstance(record, dict):
        raise NotFound("Google ad not found")
    raw_variations = record.get("5", [])
    if not isinstance(raw_variations, list):
        raise UpstreamError("Google returned malformed ad variations")
    previews = previews or _Previews(ydl)
    variations = [_variation(previews, value) for value in raw_variations if isinstance(value, dict)]
    return M.detail(record, variations, url)


@sc_get(router, "/v1/google/company/ads", source="Google Ads Transparency Center SearchCreatives RPC",
        notes="Returns Google's mapped list rows by default; detail hydration makes one sequential lookup per ad.")
async def company_ads(
    domain: str | None = Query(None, description=(
        "Company domain; use this or advertiser_id (advertiser_id wins when both are sent).")),
    advertiser_id: str | None = Query(None, description=(
        "Google advertiser id; use this or domain (it wins when both are sent).")),
    topic: str | None = Query(None, description=(
        "Topic to search. Political searches also require region."),
        json_schema_extra={"enum": ["all", "political"]}),
    region: str | None = Query(None, description="Two-letter country code; defaults to anywhere.",
                               json_schema_extra={"enum": sorted(M.REGIONS)}),
    start_date: str | None = Query(None, description="Start date in YYYY-MM-DD format."),
    end_date: str | None = Query(None, description="End date in YYYY-MM-DD format."),
    platform: str | None = Query(None, description="Google platform to search.",
                                 json_schema_extra={"enum": sorted(M.PLATFORM_IDS)}),
    format: str | None = Query(None, description="Ad format to search.",
                               json_schema_extra={"enum": sorted(M.FORMAT_IDS)}),
    get_ad_details: str | None = Query(None, description=(
        "Set true to hydrate every list row with the detailed ad response."),
        json_schema_extra={"enum": ["true", "false"]}),
    cursor: str | None = Query(None, description="Cursor from the previous page."),
) -> dict[str, Any]:
    require_one(domain=domain, advertiser_id=advertiser_id)
    if domain and advertiser_id:  # ScrapeCreators' own examples send both: the advertiser id wins
        domain = None
    if domain and not _DOMAIN.fullmatch(domain):
        raise BadRequest("domain must be a valid domain name")
    if advertiser_id and not _ADVERTISER.fullmatch(advertiser_id):
        raise BadRequest("advertiser_id must be a Google advertiser id beginning with AR")
    topic_value = _enum("topic", topic, {"all", "political"}, "all") or "all"
    region_value = _region(region)
    if topic_value == "political" and not region_value:
        raise BadRequest("region is required when topic is political")
    platform_value = _enum("platform", platform, set(M.PLATFORM_IDS)) if platform else None
    format_value = _enum("format", format, set(M.FORMAT_IDS)) if format else None
    start, end = _date("start_date", start_date), _date("end_date", end_date)
    if start and end and start > end:
        raise BadRequest("start_date must not be after end_date")
    hydrate = _boolean("get_ad_details", get_ad_details)
    payload = _search_payload(domain=domain, advertiser_id=advertiser_id, topic=topic_value,
                              region=region_value, start_date=start, end_date=end, platform=platform_value,
                              ad_format=format_value, cursor=cursor)
    if hydrate:
        payload["2"] = 10

    def fetch(ydl: Any) -> dict[str, Any]:
        data = _post(ydl, "SearchService/SearchCreatives", payload, "company ads")
        if not isinstance(data, dict):
            raise UpstreamError("Google returned malformed company ads")
        _rpc_error(data, "company ads")
        if advertiser_id and not data and not cursor:
            raise NotFound("Google advertiser not found")
        rows = data.get("1", [])
        if not isinstance(rows, list):
            raise UpstreamError("Google returned malformed company ads")
        mapped = [M.list_ad(row) for row in rows if isinstance(row, dict)]
        if hydrate:
            detail_region, previews = region_value or "US", _Previews(ydl)
            ads = []
            for index, ad in enumerate(mapped):
                if index:
                    time.sleep(_DETAIL_GAP)
                try:
                    ads.append(_detail_with_ydl(ydl, ad["advertiserId"], ad["creativeId"], detail_region,
                                                ad["adUrl"], previews))
                except NotFound:
                    continue
                except UpstreamBlocked:
                    raise  # a page cut short here would hand out a cursor past the ads it dropped
                except UpstreamError:
                    if not ads:
                        raise
                    break
        else:
            ads = mapped
        next_cursor = data.get("2")
        estimate = data.get("5") or data.get("4") or 0
        if isinstance(estimate, dict):
            estimate = estimate.get("1") or estimate.get("2") or 0
        try:
            estimate = int(estimate)
        except (TypeError, ValueError) as exc:
            raise UpstreamError("Google returned a malformed ad estimate") from exc
        return {"ads": ads, "cursor": urllib.parse.quote(next_cursor, safe="") if isinstance(next_cursor, str) else None,
                "number_of_ads_estimate": estimate}

    return await with_ydl("google", fetch)


@sc_get(router, "/v1/google/ad", source="Google Ads Transparency Center GetCreativeById RPC",
        notes="Preview copy is decoded from Google's renderer asset when available; an unavailable preview leaves "
              "that variation's copy null. After a 429 the egress cools down (503 with Retry-After).")
async def ad(url: str = Query(..., description="Google Ads Transparency creative URL.")) -> dict[str, Any]:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise BadRequest("url must be a Google Ads Transparency creative URL") from exc
    match = _AD_URL.fullmatch(urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))
    if not match:
        raise BadRequest("url must be a Google Ads Transparency creative URL", code="unsupported_url")
    query = urllib.parse.parse_qs(parsed.query)
    region = _region(query.get("region", [None])[0], default="US") or "US"
    return await with_ydl("google", lambda ydl: _detail_with_ydl(ydl, *match.groups(), region, url))


@sc_get(router, "/v1/google/adLibrary/advertisers/search",
        source="Google Ads Transparency Center SearchSuggestions RPC",
        notes="Google suggestions are global; advertiser records are filtered locally by registered country.")
async def advertiser_search(
    query: str = Query(..., description="Advertiser name or website to search for."),
    region: str | None = Query(None, description="Two-letter country code; defaults to US.",
                               json_schema_extra={"enum": sorted(M.REGIONS)}),
) -> dict[str, Any]:
    if not query.strip():
        raise BadRequest("query is required")
    region_value = _region(region, default="US") or "US"
    data = await with_ydl("google", lambda ydl: _post(ydl, "SearchService/SearchSuggestions",
                                                      {"1": query, "2": 20, "3": 20}, "advertiser suggestions"))
    if not isinstance(data, dict):
        raise UpstreamError("Google returned malformed advertiser suggestions")
    _rpc_error(data, "advertiser suggestions")
    if not isinstance(data.get("1", []), list):
        raise UpstreamError("Google returned malformed advertiser suggestions")
    return M.suggestions(data, region_value)
