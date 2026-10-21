"""Small conversions shared by the platform mappers."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from yt_dlp.utils import ISO639Utils

_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
_COUNT = re.compile(r"([\d.,]+)\s*([kmb])?", re.I)


def parse_count(text: Any) -> int | None:
    """'1.2M views' -> 1200000, '9,221 videos' -> 9221, 42 -> 42, None -> None; None for NaN, infinity or a number
    too long to hold ('9999…K', or past int()'s 4300 digits)."""
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, int):
        return text
    if isinstance(text, float):
        return int(text) if math.isfinite(text) else None
    m = _COUNT.search(str(text))
    if not m:
        return None
    number, suffix = m.group(1), (m.group(2) or "").lower()
    try:
        if suffix:
            return int(round(float(number.replace(",", "")) * _SUFFIX[suffix]))
        digits = re.sub(r"[^\d]", "", number)
        return int(digits) if digits else None
    except (ValueError, OverflowError):
        return None


# ISO 639-3 codes of the standard variety of a macrolanguage, which ISO639Utils (639-1/639-2 only) doesn't know.
# TikTok names Mandarin caption tracks 'cmn-Hans-CN'.
_MACROLANGUAGE_OF = {"cmn": "zh", "arb": "ar", "zsm": "ms", "pes": "fa", "swh": "sw"}
# One language under two codes: withdrawn ISO 639-1 codes still in use (YouTube keys Hebrew captions 'iw', Java
# reports 'iw', 'in' and 'ji'), Bokmål, which YouTube and Whisper call Norwegian ('no'), and Filipino, standard
# Tagalog, which YouTube calls 'fil' and Whisper 'tl'.
_SAME_LANGUAGE = {"iw": "he", "in": "id", "ji": "yi", "jw": "jv", "mo": "ro", "nb": "no", "fil": "tl"}
# ISO 639-2/B codes (the ones MKV and ffmpeg language tags use) of the 20 languages whose 639-2/T code, the one
# ISO639Utils knows, is another. ISO639Utils itself maps 'per' to 'pe', which is no language.
_BIBLIOGRAPHIC = {"alb": "sq", "arm": "hy", "baq": "eu", "bur": "my", "chi": "zh", "cze": "cs", "dut": "nl",
                  "fre": "fr", "geo": "ka", "ger": "de", "gre": "el", "ice": "is", "mac": "mk", "mao": "mi",
                  "may": "ms", "per": "fa", "rum": "ro", "slo": "sk", "tib": "bo", "wel": "cy"}
# Norwegian's two written standards, Nynorsk and Bokmål (which short_lang calls 'no', as YouTube and Whisper do)
_SIBLING = {"nn": "no", "no": "nn"}


def short_lang(code: str | None) -> str:
    """'en' / 'EN' / 'eng' / 'ger' / 'spa-ES' / 'cmn-Hans-CN' / 'iw' -> 'en', 'en', 'en', 'de', 'es', 'zh', 'he':
    the language part as an ISO 639-1 code, the same one for every code of a language. One without such a code
    ('yue', 'haw') is returned lower-cased, None as ''."""
    base = (code or "").strip().split("-")[0].lower()
    base = _MACROLANGUAGE_OF.get(base) or _BIBLIOGRAPHIC.get(base) or base
    base = ISO639Utils.long2short(base) or base
    return _SAME_LANGUAGE.get(base, base)


def sibling_lang(code: str | None) -> str | None:
    """The short_lang() code of the language a track in which is the next best thing when there's none in `code`'s
    ('nn' -> 'no', 'nob' -> 'nn'), None for most."""
    return _SIBLING.get(short_lang(code))


def format_count(n: int | None, unit: str = "") -> str | None:
    """1234567 -> '1,234,567' (+ ' unit')."""
    if n is None:
        return None
    return f"{n:,}" + (f" {unit}" if unit else "")


def iso_utc(ts: float | int | None, *, millis: bool = True) -> str | None:
    """Unix seconds -> '2024-01-02T03:04:05.000Z'; None for None, junk, NaN or a time past the year 9999."""
    if ts is None:
        return None
    try:
        dt = datetime.fromtimestamp(float(ts), timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if millis:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def clock(seconds: float | int | None, *, always_hours: bool = False) -> str | None:
    """75 -> '1:15', 3675 -> '1:01:15' (always_hours: '00:01:15'); None for NaN or ±Infinity."""
    if seconds is None or isinstance(seconds, float) and not math.isfinite(seconds):
        return None
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if always_hours:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def compact(d: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None."""
    return {k: v for k, v in d.items() if v is not None}


# Invisible characters a copied value can start or end with, which str.strip() keeps: a byte order mark, zero-width
# spaces and joiners, bidi marks and isolates (a link copied out of right-to-left text) and the soft hyphen.
_INVISIBLE = frozenset("\u00ad\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"  # soft hyphen, zero-width ones, BOM
                       "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")  # bidi embeddings, isolates


def _strip(value: str) -> str:
    """str.strip() that also takes _INVISIBLE (a scan, not a regex: one would backtrack over long runs of spaces)."""
    start, end = 0, len(value)
    while start < end and (value[start].isspace() or value[start] in _INVISIBLE):
        start += 1
    while end > start and (value[end - 1].isspace() or value[end - 1] in _INVISIBLE):
        end -= 1
    return value[start:end]


_BRACKETED = re.compile(r"<([^<>\s]*)>")


def clean_param(value: str) -> str:
    """A query-string value without the whitespace and invisible characters a copy can carry along at either end,
    or the angle brackets plain text puts around a link ('<https://youtu.be/x>', RFC 3986 appendix C)."""
    value = _strip(value)
    m = _BRACKETED.fullmatch(value)
    return _strip(m[1]) if m and "/" in m[1] and "." in m[1] else value  # a link, not '<b>' or '<br/>'


def truthy(value: str | bool | None) -> bool:
    """Query-string booleans: 'true'/'1'/'yes' -> True."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")
