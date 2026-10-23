"""Mapping helpers for Google Ads Transparency protobuf-JSON responses."""

from __future__ import annotations

import datetime as dt
import html
import re
from typing import Any

REGIONS: dict[str, tuple[int, str]] = {'AD': (2020, 'Andorra'),
 'AE': (2784, 'United Arab Emirates'),
 'AF': (2004, 'Afghanistan'),
 'AG': (2028, 'Antigua and Barbuda'),
 'AL': (2008, 'Albania'),
 'AM': (2051, 'Armenia'),
 'AO': (2024, 'Angola'),
 'AQ': (2010, 'Antarctica'),
 'AR': (2032, 'Argentina'),
 'AS': (2016, 'American Samoa'),
 'AT': (2040, 'Austria'),
 'AU': (2036, 'Australia'),
 'AZ': (2031, 'Azerbaijan'),
 'BA': (2070, 'Bosnia and Herzegovina'),
 'BB': (2052, 'Barbados'),
 'BD': (2050, 'Bangladesh'),
 'BE': (2056, 'Belgium'),
 'BF': (2854, 'Burkina Faso'),
 'BG': (2100, 'Bulgaria'),
 'BH': (2048, 'Bahrain'),
 'BI': (2108, 'Burundi'),
 'BJ': (2204, 'Benin'),
 'BM': (2060, 'Bermuda'),
 'BN': (2096, 'Brunei'),
 'BO': (2068, 'Bolivia'),
 'BQ': (2535, 'Caribbean Netherlands'),
 'BR': (2076, 'Brazil'),
 'BS': (2044, 'The Bahamas'),
 'BT': (2064, 'Bhutan'),
 'BW': (2072, 'Botswana'),
 'BY': (2112, 'Belarus'),
 'BZ': (2084, 'Belize'),
 'CA': (2124, 'Canada'),
 'CC': (2166, 'Cocos (Keeling) Islands'),
 'CD': (2180, 'Democratic Republic of the Congo'),
 'CF': (2140, 'Central African Republic'),
 'CG': (2178, 'Republic of the Congo'),
 'CH': (2756, 'Switzerland'),
 'CI': (2384, "Cote d'Ivoire"),
 'CK': (2184, 'Cook Islands'),
 'CL': (2152, 'Chile'),
 'CM': (2120, 'Cameroon'),
 'CN': (2156, 'China'),
 'CO': (2170, 'Colombia'),
 'CR': (2188, 'Costa Rica'),
 'CV': (2132, 'Cabo Verde'),
 'CW': (2531, 'Curacao'),
 'CX': (2162, 'Christmas Island'),
 'CY': (2196, 'Cyprus'),
 'CZ': (2203, 'Czechia'),
 'DE': (2276, 'Germany'),
 'DJ': (2262, 'Djibouti'),
 'DK': (2208, 'Denmark'),
 'DM': (2212, 'Dominica'),
 'DO': (2214, 'Dominican Republic'),
 'DZ': (2012, 'Algeria'),
 'EC': (2218, 'Ecuador'),
 'EE': (2233, 'Estonia'),
 'EG': (2818, 'Egypt'),
 'ER': (2232, 'Eritrea'),
 'ES': (2724, 'Spain'),
 'ET': (2231, 'Ethiopia'),
 'FI': (2246, 'Finland'),
 'FJ': (2242, 'Fiji'),
 'FM': (2583, 'Micronesia'),
 'FR': (2250, 'France'),
 'GA': (2266, 'Gabon'),
 'GB': (2826, 'United Kingdom'),
 'GD': (2308, 'Grenada'),
 'GE': (2268, 'Georgia'),
 'GG': (2831, 'Guernsey'),
 'GH': (2288, 'Ghana'),
 'GM': (2270, 'The Gambia'),
 'GN': (2324, 'Guinea'),
 'GQ': (2226, 'Equatorial Guinea'),
 'GR': (2300, 'Greece'),
 'GS': (2239, 'South Georgia and the South Sandwich Islands'),
 'GT': (2320, 'Guatemala'),
 'GU': (2316, 'Guam'),
 'GW': (2624, 'Guinea-Bissau'),
 'GY': (2328, 'Guyana'),
 'HM': (2334, 'Heard Island and McDonald Islands'),
 'HN': (2340, 'Honduras'),
 'HR': (2191, 'Croatia'),
 'HT': (2332, 'Haiti'),
 'HU': (2348, 'Hungary'),
 'ID': (2360, 'Indonesia'),
 'IE': (2372, 'Ireland'),
 'IL': (2376, 'Israel'),
 'IN': (2356, 'India'),
 'IQ': (2368, 'Iraq'),
 'IS': (2352, 'Iceland'),
 'IT': (2380, 'Italy'),
 'JE': (2832, 'Jersey'),
 'JM': (2388, 'Jamaica'),
 'JO': (2400, 'Jordan'),
 'JP': (2392, 'Japan'),
 'KE': (2404, 'Kenya'),
 'KG': (2417, 'Kyrgyzstan'),
 'KH': (2116, 'Cambodia'),
 'KI': (2296, 'Kiribati'),
 'KM': (2174, 'Comoros'),
 'KN': (2659, 'Saint Kitts and Nevis'),
 'KR': (2410, 'South Korea'),
 'KZ': (2398, 'Kazakhstan'),
 'LA': (2418, 'Laos'),
 'LB': (2422, 'Lebanon'),
 'LC': (2662, 'Saint Lucia'),
 'LI': (2438, 'Liechtenstein'),
 'LK': (2144, 'Sri Lanka'),
 'LR': (2430, 'Liberia'),
 'LS': (2426, 'Lesotho'),
 'LT': (2440, 'Lithuania'),
 'LU': (2442, 'Luxembourg'),
 'LV': (2428, 'Latvia'),
 'LY': (2434, 'Libya'),
 'MA': (2504, 'Morocco'),
 'MC': (2492, 'Monaco'),
 'MD': (2498, 'Moldova'),
 'ME': (2499, 'Montenegro'),
 'MG': (2450, 'Madagascar'),
 'MH': (2584, 'Marshall Islands'),
 'MK': (2807, 'North Macedonia'),
 'ML': (2466, 'Mali'),
 'MM': (2104, 'Myanmar (Burma)'),
 'MN': (2496, 'Mongolia'),
 'MO': (2446, 'Macao'),
 'MP': (2580, 'Northern Mariana Islands'),
 'MR': (2478, 'Mauritania'),
 'MT': (2470, 'Malta'),
 'MU': (2480, 'Mauritius'),
 'MV': (2462, 'Maldives'),
 'MW': (2454, 'Malawi'),
 'MX': (2484, 'Mexico'),
 'MY': (2458, 'Malaysia'),
 'MZ': (2508, 'Mozambique'),
 'NA': (2516, 'Namibia'),
 'NC': (2540, 'New Caledonia'),
 'NE': (2562, 'Niger'),
 'NF': (2574, 'Norfolk Island'),
 'NG': (2566, 'Nigeria'),
 'NI': (2558, 'Nicaragua'),
 'NL': (2528, 'Netherlands'),
 'NO': (2578, 'Norway'),
 'NP': (2524, 'Nepal'),
 'NR': (2520, 'Nauru'),
 'NU': (2570, 'Niue'),
 'NZ': (2554, 'New Zealand'),
 'OM': (2512, 'Oman'),
 'PA': (2591, 'Panama'),
 'PE': (2604, 'Peru'),
 'PF': (2258, 'French Polynesia'),
 'PG': (2598, 'Papua New Guinea'),
 'PH': (2608, 'Philippines'),
 'PK': (2586, 'Pakistan'),
 'PL': (2616, 'Poland'),
 'PM': (2666, 'Saint Pierre and Miquelon'),
 'PN': (2612, 'Pitcairn Islands'),
 'PT': (2620, 'Portugal'),
 'PW': (2585, 'Palau'),
 'PY': (2600, 'Paraguay'),
 'QA': (2634, 'Qatar'),
 'RO': (2642, 'Romania'),
 'RS': (2688, 'Serbia'),
 'RW': (2646, 'Rwanda'),
 'SA': (2682, 'Saudi Arabia'),
 'SB': (2090, 'Solomon Islands'),
 'SC': (2690, 'Seychelles'),
 'SD': (2736, 'Sudan'),
 'SE': (2752, 'Sweden'),
 'SG': (2702, 'Singapore'),
 'SH': (2654, 'Saint Helena, Ascension and Tristan da Cunha'),
 'SI': (2705, 'Slovenia'),
 'SJ': (2744, 'Svalbard and Jan Mayen'),
 'SK': (2703, 'Slovakia'),
 'SL': (2694, 'Sierra Leone'),
 'SM': (2674, 'San Marino'),
 'SN': (2686, 'Senegal'),
 'SO': (2706, 'Somalia'),
 'SR': (2740, 'Suriname'),
 'SS': (2728, 'South Sudan'),
 'ST': (2678, 'Sao Tome and Principe'),
 'SV': (2222, 'El Salvador'),
 'SX': (2534, 'Sint Maarten'),
 'SZ': (2748, 'Eswatini'),
 'TD': (2148, 'Chad'),
 'TF': (2260, 'French Southern and Antarctic Lands'),
 'TG': (2768, 'Togo'),
 'TH': (2764, 'Thailand'),
 'TJ': (2762, 'Tajikistan'),
 'TK': (2772, 'Tokelau'),
 'TL': (2626, 'Timor-Leste'),
 'TM': (2795, 'Turkmenistan'),
 'TN': (2788, 'Tunisia'),
 'TO': (2776, 'Tonga'),
 'TR': (2792, 'Turkiye'),
 'TT': (2780, 'Trinidad and Tobago'),
 'TV': (2798, 'Tuvalu'),
 'TZ': (2834, 'Tanzania'),
 'UA': (2804, 'Ukraine'),
 'UG': (2800, 'Uganda'),
 'US': (2840, 'United States'),
 'UY': (2858, 'Uruguay'),
 'UZ': (2860, 'Uzbekistan'),
 'VA': (2336, 'Vatican City'),
 'VC': (2670, 'Saint Vincent and the Grenadines'),
 'VE': (2862, 'Venezuela'),
 'VI': (2850, 'U.S. Virgin Islands'),
 'VN': (2704, 'Vietnam'),
 'VU': (2548, 'Vanuatu'),
 'WS': (2882, 'Samoa'),
 'XK': (2900, 'Kosovo'),
 'YE': (2887, 'Yemen'),
 'ZA': (2710, 'South Africa'),
 'ZM': (2894, 'Zambia'),
 'ZW': (2716, 'Zimbabwe')}

FORMAT_IDS = {"text": 1, "image": 2, "video": 3}
FORMAT_NAMES = {value: key for key, value in FORMAT_IDS.items()}
PLATFORM_IDS = {"google_play": 1, "google_maps": 2, "google_search": 3, "google_shopping": 4, "youtube": 5}
BY_CRITERION = {str(value[0]): (code, value[1]) for code, value in REGIONS.items()}


def timestamp(value: Any) -> str | None:
    if not isinstance(value, dict) or not value.get("1"):
        return None
    try:
        moment = dt.datetime.fromtimestamp(int(value["1"]), dt.UTC)
    except (TypeError, ValueError, OverflowError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def compact_date(value: Any) -> str | None:
    text = str(value or "")
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:]}T00:00:00.000Z"


def suggestions(data: dict[str, Any], region: str) -> dict[str, Any]:
    advertisers: list[dict[str, Any]] = []
    websites: list[dict[str, str]] = []
    for item in data.get("1", []):
        if not isinstance(item, dict):
            continue
        advertiser = item.get("1")
        if isinstance(advertiser, dict) and advertiser.get("3") == region:
            estimate_container = advertiser.get("4")
            inner = estimate_container.get("2") if isinstance(estimate_container, dict) else None
            estimate = inner.get("2") if isinstance(inner, dict) else None
            try:
                estimate = int(estimate or 0)
            except (TypeError, ValueError):
                estimate = 0
            advertisers.append({
                "name": advertiser.get("1"),
                "advertiser_id": advertiser.get("2"),
                "region": advertiser.get("3"),
                "number_of_ads_estimate": estimate,
            })
        website = item.get("2")
        if isinstance(website, dict) and website.get("1"):
            websites.append({"domain": website["1"]})
    return {"advertisers": advertisers, "websites": websites}


def list_ad(item: dict[str, Any]) -> dict[str, Any]:
    advertiser_id, creative_id = item.get("1"), item.get("2")
    content = item.get("3") if isinstance(item.get("3"), dict) else {}
    first, second, third = content.get("1"), content.get("2"), content.get("3")
    preview = ((first.get("4") if isinstance(first, dict) else None)
               or (second.get("4") if isinstance(second, dict) else None))
    markup = third.get("2", "") if isinstance(third, dict) else ""
    image = re.search(r'<img[^>]+src=["\']([^"\']+)', markup) if isinstance(markup, str) else None
    result = {
        "advertiserId": advertiser_id,
        "creativeId": creative_id,
        "format": FORMAT_NAMES.get(item.get("4"), "unknown"),
        "adUrl": f"https://adstransparency.google.com/advertiser/{advertiser_id}/creative/{creative_id}",
        "advertiserName": item.get("12") or "",
        "imageUrl": html.unescape(image.group(1)) if image else None,
        "firstShown": timestamp(item.get("6")),
        "lastShown": timestamp(item.get("7")),
    }
    if item.get("14"):
        result["domain"] = item["14"]
    if preview:
        result["previewUrl"] = preview
    return result


def preview_fields(body: str) -> dict[str, Any]:
    decoded = re.sub(r"\\+x([0-9a-fA-F]{2})", lambda match: chr(int(match.group(1), 16)), body)
    result: dict[str, Any] = {}
    image = re.search(r"(?:archive/)?simgad/(\d+)", decoded)
    if image:
        result["imageUrl"] = "https://tpc.googlesyndication.com/archive/simgad/" + image.group(1)
    for key, value in re.findall(r"['\"]([a-z_]+)['\"]:\s*['\"]([^'\"]*)['\"]", decoded):
        if key in {"headline", "description", "body", "video_id"} and value:
            result["description" if key == "body" else key] = html.unescape(value)
    copy = re.search(r'data-p="%\.@\.\[1,\[&quot;(.*?)&quot;,&quot;(.*?)&quot;,&quot;(.*?)&quot;', decoded)
    if copy:
        result.update(zip(("headline", "description", "destinationUrl"), map(html.unescape, copy.groups())))
    headline = re.search(r'class="[^"]*\bheadline\b[^"]*"[^>]*>.*?<span[^>]*>(.*?)</span>', decoded, re.S)
    description = re.search(r'class="[^"]*\blong-description\b[^"]*"[^>]*>.*?<span[^>]*>(.*?)</span>', decoded, re.S)
    def visible(match: re.Match[str] | None) -> str | None:
        if not match:
            return None
        return html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).strip()
    result.setdefault("headline", visible(headline))
    result.setdefault("description", visible(description))
    result = {key: value for key, value in result.items() if value is not None}
    if result.get("headline") or result.get("description"):
        result["allText"] = " ".join(x for x in (result.get("headline"), result.get("description")) if x)
    return result


def locations(ids: Any) -> list[dict[str, Any]]:
    output = []
    for criterion_id in ids or []:
        code, name = BY_CRITERION.get(str(criterion_id), (None, str(criterion_id)))
        output.append({"criterionId": str(criterion_id), "name": name, "fullName": name,
                       "countryCodes": [code] if code else []})
    return output


def detail(record: dict[str, Any], variations: list[dict[str, Any]], url: str) -> dict[str, Any]:
    region_values = record.get("7")
    creative_regions = [{"regionCode": code, "regionName": REGIONS.get(code, (0, code))[1]}
                        for code in region_values if isinstance(code, str)] if isinstance(region_values, list) else []
    impressions = None
    if "9" in record or "10" in record:
        impressions = {"min": str(record["9"]) if record.get("9") is not None else None,
                       "max": str(record["10"]) if record.get("10") is not None else None}
    result: dict[str, Any] = {
        "advertiserId": record.get("1"), "creativeId": record.get("2"),
        "firstShown": timestamp(record.get("3")), "url": url,
        "lastShown": timestamp(record.get("4")), "format": FORMAT_NAMES.get(record.get("8"), "unknown"),
        "overallImpressions": impressions, "spend": None, "targeting": None,
    }
    lower_container, upper_container = record.get("11"), record.get("12")
    lower_rows = lower_container.get("1") if isinstance(lower_container, dict) else None
    upper_rows = upper_container.get("1") if isinstance(upper_container, dict) else None
    lower = lower_rows[0] if isinstance(lower_rows, list) and lower_rows and isinstance(lower_rows[0], dict) else {}
    upper = upper_rows[0] if isinstance(upper_rows, list) and upper_rows and isinstance(upper_rows[0], dict) else {}
    if lower or upper:
        result["spend"] = {"currency": lower.get("1") or upper.get("1") or "",
                           "lower": str(lower.get("2", "0")), "upper": str(upper.get("2", "0"))}
    targeting = record.get("13")
    if isinstance(targeting, dict):
        ages, genders, location = targeting.get("1", {}), targeting.get("2", {}), targeting.get("3", {})
        ages = ages if isinstance(ages, dict) else {}
        genders = genders if isinstance(genders, dict) else {}
        location = location if isinstance(location, dict) else {}
        age_names = {1: "18-24", 2: "25-34", 3: "35-44", 4: "45-54", 5: "55-64", 6: "65+"}
        gender_names = {1: "male", 2: "female", 3: "unknown"}
        def values(rows: Any, names: dict[int, str]) -> list[str]:
            return [names[value["1"]] for value in rows or []
                    if isinstance(value, dict) and value.get("1") in names]
        result["targeting"] = {
            "age": {"included": values(ages.get("1"), age_names), "excluded": values(ages.get("2"), age_names)},
            "gender": {"included": values(genders.get("1"), gender_names),
                       "excluded": values(genders.get("2"), gender_names)},
            "location": {"included": locations(location.get("1")), "excluded": locations(location.get("2"))},
        }
    result["creativeRegions"] = creative_regions
    stats = []
    stat_rows = record.get("17")
    for row in stat_rows if isinstance(stat_rows, list) else []:
        if not isinstance(row, dict):
            continue
        code, name = BY_CRITERION.get(str(row.get("1")), ("", str(row.get("1"))))
        stats.append({"regionCode": code, "regionName": name, "firstShown": compact_date(row.get("4")),
                      "lastShown": compact_date(row.get("5")),
                      "impressions": {"lower": str(row.get("2", "0")), "upper": str(row.get("3", "0"))},
                      "platformImpressions": []})
    result["regionStats"] = stats
    result["variations"] = variations
    return result
