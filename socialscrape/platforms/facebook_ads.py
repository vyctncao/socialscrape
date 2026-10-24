"""Facebook Ad Library endpoints backed by Meta's logged-out Relay pages."""

from __future__ import annotations

import datetime
import html as html_lib
from html.parser import HTMLParser
import json
import logging
import re
import threading
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

from fastapi import APIRouter, Query
from yt_dlp.extractor.facebook import FacebookAdsIE
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import ExtractorError

from .. import transcribe
from ..errors import BadRequest, NotFound, UpstreamBlocked, UpstreamError, require_one
from ..registry import sc_get
from ..ytdlp import with_ydl
from . import _facebook_ads_map as M

router = APIRouter()
log = logging.getLogger("socialscrape.facebook_ads")
P = "facebook"
BASE = "https://www.facebook.com"
SEARCH_DOC_ID = "24922295957467452"
TYPEAHEAD_DOC_ID = "9755915494515334"
DETAILS_DOC_ID = "25068828942793558"

_DOC_ID_LOCK = threading.Lock()
_DOC_IDS: dict[str, str] = {}
_OPERATION_NAMES = {
    "AdLibrarySearchPaginationQuery": "AdLibrarySearchPaginationQuery_facebookRelayOperation",
    "useAdLibraryTypeaheadSuggestionDataSourceQuery":
        "useAdLibraryTypeaheadSuggestionDataSourceQuery_facebookRelayOperation",
    "AdLibraryV3AdDetailsQuery": "AdLibraryV3AdDetailsQuery_facebookRelayOperation",
}

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
_ENUMS = {
    "sort_by": {"total_impressions", "relevancy_monthly_grouped"},
    "search_type": {"keyword_unordered", "keyword_exact_phrase"},
    "ad_type": {"all", "political_and_issue_ads"},
    "status": {"ALL", "ACTIVE", "INACTIVE"},
    "media_type": {"ALL", "IMAGE", "VIDEO", "MEME", "IMAGE_AND_MEME", "NONE"},
}
_LANGUAGES = {
    "AA", "AB", "AE", "AF", "AK", "AM", "AN", "AR", "AS", "AV", "AY", "AZ", "BA", "BE", "BG", "BH",
    "BI", "BM", "BN", "BO", "BR", "BS", "CA", "CE", "CH", "CO", "CR", "CS", "CU", "CV", "CY", "DA",
    "DE", "DV", "DZ", "EE", "EL", "EN", "EO", "ES", "ET", "EU", "FA", "FF", "FI", "FJ", "FO", "FR",
    "FY", "GA", "GD", "GL", "GN", "GU", "GV", "HA", "HE", "HI", "HO", "HR", "HT", "HU", "HY", "HZ",
    "IA", "ID", "IE", "IG", "II", "IK", "IO", "IS", "IT", "IU", "JA", "JV", "KA", "KG", "KI", "KJ",
    "KK", "KL", "KM", "KN", "KO", "KR", "KS", "KU", "KV", "KW", "KY", "LA", "LB", "LG", "LI", "LN",
    "LO", "LT", "LU", "LV", "MG", "MH", "MI", "MK", "ML", "MN", "MR", "MS", "MT", "MY", "NA", "NB",
    "ND", "NE", "NG", "NL", "NN", "NO", "NR", "NV", "NY", "OC", "OJ", "OM", "OR", "OS", "PA", "PI",
    "PL", "PS", "PT", "QU", "RM", "RN", "RO", "RU", "RW", "SA", "SC", "SD", "SE", "SG", "SI", "SK",
    "SL", "SM", "SN", "SO", "SQ", "SR", "SS", "ST", "SU", "SV", "SW", "TA", "TE", "TG", "TH", "TI",
    "TK", "TL", "TN", "TO", "TR", "TS", "TT", "TW", "TY", "UG", "UK", "UR", "UZ", "VE", "VI", "VO",
    "WA", "WO", "XH", "YI", "YO", "ZA", "ZH", "ZU",
}
_COUNTRIES = {
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX", "AZ",
    "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ", "BR", "BS",
    "BT", "BV", "BW", "BY", "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN",
    "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ", "DE", "DJ", "DK", "DM", "DO", "DZ", "EC", "EE",
    "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK", "FM", "FO", "FR", "GA", "GB", "GD", "GE", "GF",
    "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS", "GT", "GU", "GW", "GY", "HK", "HM",
    "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN", "IO", "IQ", "IR", "IS", "IT", "JE", "JM",
    "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN", "KP", "KR", "KW", "KY", "KZ", "LA", "LB", "LC",
    "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY", "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK",
    "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA",
    "NC", "NE", "NF", "NG", "NI", "NL", "NO", "NP", "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG",
    "PH", "PK", "PL", "PM", "PN", "PR", "PS", "PT", "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW",
    "SA", "SB", "SC", "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS",
    "ST", "SV", "SX", "SY", "SZ", "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO",
    "TR", "TT", "TV", "TW", "TZ", "UA", "UG", "UM", "US", "UY", "UZ", "VA", "VC", "VE", "VG", "VI",
    "VN", "VU", "WF", "WS", "YE", "YT", "ZA", "ZM", "ZW",
}


def _ie(ydl: Any) -> FacebookAdsIE:
    ie = FacebookAdsIE(ydl)
    ie.initialize()
    return ie


def _page(ie: FacebookAdsIE, url: str, ident: str) -> str:
    try:
        return ie._download_webpage(url, ident, headers=_HEADERS)
    except Exception as exc:
        cause = getattr(exc, "cause", None)
        if getattr(cause, "status", None) != 403:
            raise
        response = cause.response
        body = ie._webpage_read_content(response, url, ident)
        match = re.search(r"fetch\s*\(\s*['\"](/__rd_verify[^'\"]+)", body)
        if not match:
            raise UpstreamBlocked("Facebook blocked the Ad Library request") from exc
        ie._request_webpage(urljoin(url, match.group(1)), ident, data=b"", headers={
            "Origin": BASE, "Referer": url, "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
        })
        try:
            return ie._download_webpage(url, ident, headers=_HEADERS)
        except Exception as retry_exc:
            raise UpstreamBlocked("Facebook's Ad Library challenge could not be completed") from retry_exc


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


_PRELOADER = re.compile(
    r'AdLibraryFoundationRootQueryRelayPreloader_[^"]+","queryID":"[0-9]+","variables":(\{.*?\}),"queryName"')


def _page_variables(html: str) -> dict[str, Any] | None:
    """The variables of the Ad Library app's root query on this page, or None when the app didn't render."""
    match = _PRELOADER.search(html)
    try:
        variables = json.loads(match.group(1)) if match else None
    except ValueError:
        return None
    return variables if isinstance(variables, dict) else None


def _archive(html: str, ident: str) -> dict[str, Any]:
    candidates = []
    for raw in re.findall(r"<script[^>]*data-sjs[^>]*>({.*?ScheduledServerJS.*?)</script>", html):
        try:
            root = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in _walk(root):
            nested = obj.get("deeplink_ad_archive")
            for item in (obj, nested):
                if isinstance(item, dict) and str(item.get("ad_archive_id")) == ident:
                    candidates.append(item)
    if not candidates:
        # Meta answers an id it has no ad for with the Ad Library's own landing page (deeplinkAdID null), whose
        # bundled config names arkose_captcha and google_recaptcha: that is a missing ad, not a challenge.
        variables = _page_variables(html)
        if variables is not None and str(variables.get("deeplinkAdID")) != ident:
            raise NotFound("Facebook ad was not found", code="ad_not_found")
        if re.search(r"captcha|checkpoint|challenge|log in(?: to Facebook)?", html, re.I):
            raise UpstreamBlocked("Facebook blocked the Ad Library request")
        raise NotFound("Facebook ad was not found", code="ad_not_found")
    return max(candidates, key=lambda item: len(json.dumps(item, default=str)))


def _lsd(html: str) -> str:
    match = re.search(r'"LSD",\[\],\{"token":"([^"]+)', html)
    if not match:
        raise UpstreamError("Facebook did not return its Ad Library request token")
    return match.group(1)


def _is_bundle_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.hostname == "static.xx.fbcdn.net"
            and parsed.path.startswith("/rsrc.php/") and parsed.path.endswith(".js"))


class _ScriptResources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        src = dict(attrs).get("src")
        if src and _is_bundle_url(src):
            self.urls.append(src)


def _bundle_urls(page_html: str) -> list[str]:
    normalized = html_lib.unescape(page_html.replace(r"\/", "/").replace(r"\u0026", "&"))
    parser = _ScriptResources()
    parser.feed(normalized)
    return list(dict.fromkeys(parser.urls))


def _operation_id(bundle: str, operation: str) -> str | None:
    name = re.escape(_OPERATION_NAMES[operation])
    match = re.search(name + r'.{0,180}?exports\s*=\s*["\']([0-9]+)["\']', bundle, re.S)
    return match.group(1) if match else None


def _discover_doc_id(ie: FacebookAdsIE, shell_url: str, page_html: str, ident: str,
                     operation: str) -> str:
    for index, bundle_url in enumerate(_bundle_urls(page_html)):
        try:
            bundle, response = ie._download_webpage_handle(
                bundle_url, f"{ident}-bundle-{index}", headers={"Referer": shell_url})
        except Exception:
            continue
        if not _is_bundle_url(response.url):
            continue
        if doc_id := _operation_id(bundle, operation):
            return doc_id
    raise UpstreamError(f"Facebook did not expose its {operation} document ID")


def _stale_document(errors: Any) -> bool:
    if not isinstance(errors, list) or not errors:
        return False
    pattern = (r"GraphQL document with ID .* was not found|PersistedQueryNotFound|"
               r"invalid (?:or expired )?(?:document|doc(?:ument)?_?id)")
    return all(bool(re.search(pattern, json.dumps(error), re.I)) for error in errors)


def _blocked_errors(errors: Any) -> bool:
    return bool(re.search(r"rate|captcha|checkpoint|login|auth", json.dumps(errors), re.I))


def _graphql_request(ie: FacebookAdsIE, shell_url: str, ident: str, friendly: str,
                     doc_id: str, variables: dict[str, Any], lsd: str) -> dict[str, Any]:
    form = {
        "av": "0", "__user": "0", "__a": "1", "__req": "1", "__comet_req": "15", "lsd": lsd,
        "jazoest": "2" + "".join(str(ord(char)) for char in lsd),
        "fb_api_caller_class": "RelayModern", "fb_api_req_friendly_name": friendly,
        "variables": json.dumps(variables, separators=(",", ":")), "server_timestamps": "true", "doc_id": doc_id,
    }
    try:
        result = ie._download_json(BASE + "/api/graphql/", ident, data=urlencode(form).encode(), headers={
            "Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded", "Origin": BASE,
            "Referer": shell_url, "X-FB-LSD": lsd, "X-ASBD-ID": "129477", "X-FB-Friendly-Name": friendly,
        })
    except Exception as exc:
        if isinstance(getattr(exc, "cause", None), HTTPError) and getattr(exc.cause, "status", None) in (401, 403, 429):
            raise UpstreamBlocked("Facebook blocked the Ad Library request") from exc
        raise
    if not isinstance(result, dict):
        raise UpstreamError("Facebook returned malformed Ad Library data")
    return result


def _graphql(ie: FacebookAdsIE, shell_url: str, page_html: str, ident: str, friendly: str,
             doc_id: str, variables: dict[str, Any], *, pagination: bool = False) -> dict[str, Any]:
    lsd = _lsd(page_html)
    attempted = _DOC_IDS.get(friendly, doc_id)
    result = _graphql_request(ie, shell_url, ident, friendly, attempted, variables, lsd)
    if result.get("errors") and _blocked_errors(result["errors"]):
        raise UpstreamBlocked("Facebook blocked the Ad Library request")
    if result.get("errors") and _stale_document(result["errors"]):
        with _DOC_ID_LOCK:
            refreshed = _DOC_IDS.get(friendly)
            if not refreshed or refreshed == attempted:
                refreshed = _discover_doc_id(ie, shell_url, page_html, ident, friendly)
                _DOC_IDS[friendly] = refreshed
        result = _graphql_request(ie, shell_url, ident, friendly, refreshed, variables, lsd)
    if result.get("errors"):
        text = json.dumps(result["errors"])
        if _blocked_errors(result["errors"]):
            raise UpstreamBlocked("Facebook blocked the Ad Library request")
        if pagination and re.search(r"cursor|continuation", text, re.I):
            raise BadRequest("Invalid cursor/continuation token", code="bad_cursor")
        raise UpstreamError("Facebook's Ad Library query failed")
    return result


def _search_connection(html: str) -> tuple[dict[str, Any], dict[str, Any]]:
    query = _PRELOADER.search(html)
    marker = '"search_results_connection":'
    position = html.find(marker)
    if not query or position < 0:
        if re.search(r"captcha|checkpoint|log in", html, re.I):
            raise UpstreamBlocked("Facebook blocked the Ad Library request")
        raise UpstreamError("Facebook returned malformed Ad Library search data")
    try:
        variables = json.loads(query.group(1))
        connection, _ = json.JSONDecoder().raw_decode(html[position + len(marker):])
    except (ValueError, TypeError) as exc:
        raise UpstreamError("Facebook returned malformed Ad Library search data") from exc
    return variables, connection


def _ads(connection: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(connection, dict):
        raise UpstreamError("Facebook returned malformed Ad Library search data")
    edges = connection.get("edges")
    if edges is None:
        return []
    if not isinstance(edges, list):
        raise UpstreamError("Facebook returned malformed Ad Library search data")
    ads = []
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("node"), dict):
            raise UpstreamError("Facebook returned malformed Ad Library search data")
        collated = edge["node"].get("collated_results")
        if collated is None:
            continue
        if not isinstance(collated, list) or not all(isinstance(ad, dict) for ad in collated):
            raise UpstreamError("Facebook returned malformed Ad Library search data")
        ads.extend(M.decorate_ad(ad) for ad in collated)
    return ads


def _value(name: str, value: str | None, default: str) -> str:
    value = value or default
    if value not in _ENUMS[name]:
        raise BadRequest(f"{name} must be one of: {', '.join(sorted(_ENUMS[name]))}")
    return value


def _date(name: str, value: str | None) -> str | None:
    if value:
        try:
            parsed = datetime.date.fromisoformat(value)
        except ValueError:
            raise BadRequest(f"{name} must use a valid YYYY-MM-DD date") from None
        if parsed.isoformat() != value:
            raise BadRequest(f"{name} must use a valid YYYY-MM-DD date")
    return value


def _country(value: str | None) -> str:
    value = (value or "ALL").upper()
    if value != "ALL" and value not in _COUNTRIES:
        raise BadRequest("country must be ALL or a valid 2-letter country code")
    return value


def _language(value: str | None) -> str | None:
    value = value.upper() if value else None
    if value and value not in _LANGUAGES:
        raise BadRequest("language must be a valid ISO 639-1 language code")
    return value


def _params(query: str, *, page_id: str | None, country: str | None, status: str | None,
            media_type: str | None, language: str | None, sort_by: str | None,
            search_type: str | None = None, ad_type: str | None = None,
            start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    country = _country(country)
    status = _value("status", status, "ACTIVE")
    media_type = _value("media_type", media_type, "ALL")
    sort_by = _value("sort_by", sort_by, "total_impressions")
    search_type = _value("search_type", search_type, "keyword_unordered")
    ad_type = _value("ad_type", ad_type, "all")
    language = _language(language)
    start_date, end_date = _date("start_date", start_date), _date("end_date", end_date)
    if start_date and end_date and start_date > end_date:
        raise BadRequest("start_date must not be after end_date")
    params: dict[str, Any] = {
        "active_status": status.lower(), "ad_type": ad_type, "country": country, "q": query,
        "search_type": "page" if page_id else search_type, "media_type": media_type.lower(),
        "sort_data[mode]": sort_by, "sort_data[direction]": "desc",
    }
    if page_id:
        params.update(q="", view_all_page_id=page_id)
    if language:
        params["content_languages[0]"] = language
    if start_date:
        params["start_date[min]"] = start_date
    if end_date:
        params["start_date[max]"] = end_date
    return params


def _pagination_variables(variables: dict[str, Any], cursor: str) -> dict[str, Any]:
    names = ("activeStatus", "adType", "bylines", "collationToken", "contentLanguages", "countries",
             "isTargetedCountry", "location", "mediaType", "multiCountryFilterMode", "pageIDs",
             "potentialReachInput", "publisherPlatforms", "queryString", "regions", "searchType", "sessionID",
             "sortData", "source", "startDate", "v", "viewAllPageID")
    out = {name: variables.get(name) for name in names}
    out.update(cursor=cursor, excludedIDs=[], first=10)
    return out


def _fetch_search(ydl: Any, params: dict[str, Any], cursor: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ie = _ie(ydl)
    url = BASE + "/ads/library/?" + urlencode(params)
    html = _page(ie, url, params.get("q") or params.get("view_all_page_id") or "ads")
    variables, first = _search_connection(html)
    if not cursor:
        return first, _ads(first)
    result = _graphql(ie, url, html, "ads", "AdLibrarySearchPaginationQuery", SEARCH_DOC_ID,
                      _pagination_variables(variables, cursor), pagination=True)
    connection = (((result.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection"))
    if not isinstance(connection, dict):
        raise BadRequest("Invalid cursor/continuation token", code="bad_cursor")
    ads = _ads(connection)
    first_ads = _ads(first)
    first_ids = [str(ad.get("ad_archive_id")) for ad in first_ads]
    ids = [str(ad.get("ad_archive_id")) for ad in ads]
    first_end = (first.get("page_info") or {}).get("end_cursor")
    end = (connection.get("page_info") or {}).get("end_cursor")
    if (ids and ids == first_ids) or (end and first_end and end == first_end):
        raise BadRequest("Invalid cursor/continuation token", code="bad_cursor")
    return connection, ads


def _ad_details(ie: FacebookAdsIE, url: str, html: str, archive: dict[str, Any]) -> dict[str, Any] | None:
    """The ad's AdLibraryV3AdDetailsQuery `ad_details` (what "See ad details" shows), or None when there is none.

    Meta keeps regulated-delivery data out of the deep-link page: the payer/beneficiary and EU/UK/Brazil audience
    and reach of an ad delivered under those rules (is_aaa_eligible), and the delivery shares of a political or
    issue ad, come only from this query, asked as the page's own modal asks it. Other ads have none of it, so
    they cost no second request. It only fills aaa_info, so a refusal leaves that null (unknown) and is logged
    rather than failing the ad itself."""
    political = "POLITICAL" in (archive.get("categories") or [])
    eligible = bool(archive.get("is_aaa_eligible"))
    if not (political or eligible):
        return None
    page = _page_variables(html) or {}
    ident = str(archive.get("ad_archive_id") or "")
    variables = {"adArchiveID": ident, "pageID": str(archive.get("page_id") or ""),
                 "country": page.get("country") or "ALL", "sessionID": page.get("sessionID"), "source": None,
                 "isAdNonPolitical": not political, "isAdNotAAAEligible": not eligible}
    try:
        result = _graphql(ie, url, html, ident, "AdLibraryV3AdDetailsQuery", DETAILS_DOC_ID, variables)
    except (UpstreamBlocked, UpstreamError, ExtractorError) as exc:
        log.warning("Facebook ad %s: details query failed, aaa_info left null (%s)", ident,
                    getattr(exc, "message", None) or type(exc).__name__)
        return None
    details = ((result.get("data") or {}).get("ad_library_main") or {}).get("ad_details")
    return details if isinstance(details, dict) else None


async def _detail_archive(ident: str, *, details: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(the ad's deeplink_ad_archive, its ad_details when asked for and it has any)."""
    def fetch(ydl: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
        ie = _ie(ydl)
        url = BASE + "/ads/library/?id=" + ident
        html = _page(ie, url, ident)
        archive = _archive(html, ident)
        return archive, _ad_details(ie, url, html, archive) if details else None
    return await with_ydl(P, fetch, impersonate=ImpersonateTarget(client="chrome"), http_headers=_HEADERS)


def _input_id(id: str | None, url: str | None) -> str:
    require_one(id=id, url=url)
    if id and url:
        raise BadRequest("Provide only one of: id, url")
    ident = M.ad_id(id, url)
    if not ident:
        raise BadRequest("id must be numeric, or url must be a Facebook Ad Library URL containing a numeric id")
    return ident


_LONG = "true transcribes videos longer than 120 seconds too; without it they answer 400 transcription_too_long."


@sc_get(router, "/v1/facebook/adLibrary/ad",
        source="Meta Ad Library SSR deeplink_ad_archive + AdLibraryV3AdDetailsQuery via yt-dlp FacebookAdsIE",
        notes="Logged-out Meta archive data; trim is a conservative creative projection. aaa_info comes from the "
              "ad-details query, asked only for EU/UK/Brazil-regulated or political ads, and is null otherwise "
              "or if Meta refuses it. Optional transcript uses local Whisper.")
async def facebook_ad(
    id: str | None = Query(None, description="Ad Library ad id (ad_archive_id); use this or url."),
    url: str | None = Query(None, description="Ad Library URL such as https://www.facebook.com/ads/library/?id=123."),
    allow_long_transcription: str | None = Query(None, description=_LONG),
    get_transcript: str | None = Query(None, description="true adds the ad video's transcript (local Whisper).",
                                       json_schema_extra={"enum": ["true", "false"]}),
    trim: str | None = Query(None, description="true returns a trimmed-down response.",
                             json_schema_extra={"enum": ["true", "false"]}),
) -> dict[str, Any]:
    ident = _input_id(id, url)
    archive, details = await _detail_archive(ident, details=True)
    result = M.detail(archive, trim=trim == "true", details=details)
    if get_transcript == "true":
        media = M.video_url(archive)
        if not media:
            result["transcript"] = None
        else:
            text = await transcribe.transcribe(result["url"], platform=P,
                                                allow_long=allow_long_transcription == "true")
            result["transcript"] = text.text or None
    return result


@sc_get(router, "/v1/facebook/adLibrary/ad/transcript",
        source="Meta Ad Library SSR media via yt-dlp FacebookAdsIE + local Whisper",
        notes="Uses the first playable ad video; image-only ads return transcript_available=false.")
async def facebook_ad_transcript(
    id: str | None = Query(None, description="Ad Library ad id (ad_archive_id); use this or url."),
    url: str | None = Query(None, description="Ad Library URL such as https://www.facebook.com/ads/library/?id=123."),
    allow_long_transcription: str | None = Query(None, description=_LONG),
) -> dict[str, Any]:
    ident = _input_id(id, url)
    archive, _ = await _detail_archive(ident)
    canonical = f"{BASE}/ads/library?id={ident}"
    if not M.video_url(archive):
        return {"data": {"ad_id": ident, "url": canonical, "transcript": None, "transcript_available": False}}
    result = await transcribe.transcribe(canonical, platform=P, allow_long=allow_long_transcription == "true")
    text = result.text or None
    return {"data": {"ad_id": ident, "url": canonical, "transcript": text, "transcript_available": bool(text)}}


@sc_get(router, "/v1/facebook/adLibrary/search/ads", post=True,
        source="Meta Ad Library SSR + AdLibrarySearchPaginationQuery via yt-dlp networking",
        notes="Raw Meta ads with ScrapeCreators wrappers; trim removes operational/reporting fields approximately.")
async def facebook_search_ads(
    query: str = Query(..., description="Keyword to search for."),
    sort_by: str | None = Query(None, description="Sort by impressions or most recent.",
                                json_schema_extra={"enum": ["total_impressions", "relevancy_monthly_grouped"]}),
    search_type: str | None = Query(None, description="Search by unordered keywords or an exact phrase.",
                                    json_schema_extra={"enum": ["keyword_unordered", "keyword_exact_phrase"]}),
    ad_type: str | None = Query(None, description="Search all ads or only political and issue ads.",
                                json_schema_extra={"enum": ["all", "political_and_issue_ads"]}),
    country: str | None = Query(None, description="Two-letter country code, or ALL."),
    language: str | None = Query(None, description="Two-letter language code, such as EN, ES, or FR."),
    status: str | None = Query(None, description="Ad status; defaults to ACTIVE.",
                               json_schema_extra={"enum": ["ALL", "ACTIVE", "INACTIVE"]}),
    media_type: str | None = Query(None, description="Ad media type; defaults to ALL.",
                                   json_schema_extra={"enum": ["ALL", "IMAGE", "VIDEO", "MEME",
                                                               "IMAGE_AND_MEME", "NONE"]}),
    start_date: str | None = Query(None, description="Impressions start date in YYYY-MM-DD format."),
    end_date: str | None = Query(None, description="Impressions end date in YYYY-MM-DD format."),
    trim: str | None = Query(None, description="Set to true for a trimmed response."),
    cursor: str | None = Query(None, description="Cursor to paginate through results."),
) -> dict[str, Any]:
    if not query:
        raise BadRequest("query is required")
    params = _params(query, page_id=None, country=country, status=status, media_type=media_type,
                     language=language, sort_by=sort_by, search_type=search_type, ad_type=ad_type,
                     start_date=start_date, end_date=end_date)
    connection, ads = await with_ydl(P, lambda ydl: _fetch_search(ydl, params, cursor),
                                     impersonate=ImpersonateTarget(client="chrome"), http_headers=_HEADERS)
    if trim == "true":
        ads = [M.trim_ad(ad) for ad in ads]
    page = connection.get("page_info") or {}
    return {"searchResults": ads, "searchResultsCount": int(connection.get("count") or len(ads)),
            "cursor": page.get("end_cursor") or ""}


async def _resolve_company(name: str) -> str:
    results = await facebook_search_companies(name)
    pages = results["searchResults"]
    exact = next((page for page in pages if str(page.get("name", "")).casefold() == name.casefold()), None)
    if not exact and pages:
        exact = pages[0]
    if not exact or not str(exact.get("page_id", "")).isdigit():
        raise NotFound("Facebook company was not found", code="company_not_found")
    return str(exact["page_id"])


@sc_get(router, "/v1/facebook/adLibrary/company/ads", post=True,
        source="Meta Ad Library page search + AdLibrarySearchPaginationQuery via yt-dlp networking",
        notes="companyName resolves through Meta typeahead before page search; trim is an approximate projection.")
async def facebook_company_ads(
    pageId: str | None = Query(None, description="Ad Library page ID; use this or companyName."),
    companyName: str | None = Query(None, description="Company name; use this or pageId."),
    country: str | None = Query(None, description="Two-letter country code, or ALL."),
    status: str | None = Query(None, description="Ad status; defaults to ACTIVE.",
                               json_schema_extra={"enum": ["ALL", "ACTIVE", "INACTIVE"]}),
    media_type: str | None = Query(None, description="Ad media type; defaults to ALL.",
                                   json_schema_extra={"enum": ["ALL", "IMAGE", "VIDEO", "MEME",
                                                               "IMAGE_AND_MEME", "NONE"]}),
    language: str | None = Query(None, description="Two-letter language code, such as EN, ES, or FR."),
    sort_by: str | None = Query(None, description="Sort by impressions or most recent.",
                                json_schema_extra={"enum": ["total_impressions", "relevancy_monthly_grouped"]}),
    start_date: str | None = Query(None, description="Start date in YYYY-MM-DD format."),
    end_date: str | None = Query(None, description="End date in YYYY-MM-DD format."),
    trim: str | None = Query(None, description="Set to true for a trimmed response."),
    cursor: str | None = Query(None, description="Cursor to paginate through results."),
) -> dict[str, Any]:
    require_one(pageId=pageId, companyName=companyName)
    if pageId and companyName:
        raise BadRequest("Provide only one of: pageId, companyName")
    if pageId and (not pageId.isascii() or not pageId.isdigit()):
        raise BadRequest("pageId must be a numeric Facebook page id")
    page_id = pageId or await _resolve_company(companyName or "")
    params = _params("", page_id=page_id, country=country, status=status, media_type=media_type,
                     language=language, sort_by=sort_by, start_date=start_date, end_date=end_date)
    connection, ads = await with_ydl(P, lambda ydl: _fetch_search(ydl, params, cursor),
                                     impersonate=ImpersonateTarget(client="chrome"), http_headers=_HEADERS)
    if trim == "true":
        ads = [M.trim_ad(ad) for ad in ads]
    return {"results": ads, "cursor": (connection.get("page_info") or {}).get("end_cursor") or ""}


@sc_get(router, "/v1/facebook/adLibrary/search/companies",
        source="Meta useAdLibraryTypeaheadSuggestionDataSourceQuery via yt-dlp networking",
        notes="Returns Meta page_results unchanged; Relay document IDs may rotate.")
async def facebook_search_companies(query: str = Query(..., description="Company name to search for.")) -> dict[str, Any]:
    if not query:
        raise BadRequest("query is required")

    def fetch(ydl: Any) -> dict[str, Any]:
        ie = _ie(ydl)
        params = {"active_status": "all", "ad_type": "all", "country": "US", "q": query,
                  "search_type": "keyword_unordered", "media_type": "all"}
        url = BASE + "/ads/library/?" + urlencode(params)
        html = _page(ie, url, query)
        result = _graphql(ie, url, html, query, "useAdLibraryTypeaheadSuggestionDataSourceQuery",
                          TYPEAHEAD_DOC_ID, {"queryString": query, "isMobile": False,
                                             "country": "US", "adType": "ALL"})
        suggestions = (((result.get("data") or {}).get("ad_library_main") or {}).get("typeahead_suggestions"))
        if not isinstance(suggestions, dict) or not isinstance(suggestions.get("page_results"), list):
            raise UpstreamError("Facebook returned malformed company search data")
        return {"searchResults": suggestions["page_results"]}

    return await with_ydl(P, fetch, impersonate=ImpersonateTarget(client="chrome"), http_headers=_HEADERS)


UNSUPPORTED: dict[str, str] = {}
