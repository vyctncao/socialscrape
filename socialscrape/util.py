"""Small conversions shared by the platform mappers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from yt_dlp.utils import ISO639Utils

_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
_COUNT = re.compile(r"([\d.,]+)\s*([kmb])?", re.I)


def parse_count(text: Any) -> int | None:
    """'1.2M views' -> 1200000, '9,221 videos' -> 9221, 42 -> 42, None -> None."""
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return int(text)
    m = _COUNT.search(str(text))
    if not m:
        return None
    number, suffix = m.group(1), (m.group(2) or "").lower()
    if suffix:
        try:
            return int(round(float(number.replace(",", "")) * _SUFFIX[suffix]))
        except ValueError:
            return None
    digits = re.sub(r"[^\d]", "", number)
    return int(digits) if digits else None


# ISO 639-3 codes of the standard variety of a macrolanguage, which ISO639Utils (639-1/639-2 only) doesn't know.
# TikTok names Mandarin caption tracks 'cmn-Hans-CN'.
_MACROLANGUAGE_OF = {"cmn": "zh", "arb": "ar", "zsm": "ms", "pes": "fa", "swh": "sw"}
# One language under two codes: withdrawn ISO 639-1 codes still in use (YouTube keys Hebrew captions 'iw', Java
# reports 'iw', 'in' and 'ji'), Bokmål, which YouTube and Whisper call Norwegian ('no'), and Filipino, standard
# Tagalog, which YouTube calls 'fil' and Whisper 'tl'.
_SAME_LANGUAGE = {"iw": "he", "in": "id", "ji": "yi", "jw": "jv", "mo": "ro", "nb": "no", "fil": "tl"}


def short_lang(code: str | None) -> str:
    """'en' / 'EN' / 'eng' / 'spa-ES' / 'cmn-Hans-CN' / 'iw' -> 'en', 'en', 'en', 'es', 'zh', 'he': the language
    part as an ISO 639-1 code, the same one for every code of a language. One without such a code ('yue', 'haw')
    is returned lower-cased, None as ''."""
    base = (code or "").strip().split("-")[0].lower()
    base = _MACROLANGUAGE_OF.get(base, base)
    base = ISO639Utils.long2short(base) or base
    return _SAME_LANGUAGE.get(base, base)


def format_count(n: int | None, unit: str = "") -> str | None:
    """1234567 -> '1,234,567' (+ ' unit')."""
    if n is None:
        return None
    return f"{n:,}" + (f" {unit}" if unit else "")


def iso_utc(ts: float | int | None, *, millis: bool = True) -> str | None:
    """Unix seconds -> '2024-01-02T03:04:05.000Z'."""
    if ts is None:
        return None
    dt = datetime.fromtimestamp(float(ts), timezone.utc)
    if millis:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def clock(seconds: float | int | None, *, always_hours: bool = False) -> str | None:
    """75 -> '1:15', 3675 -> '1:01:15' (always_hours: '00:01:15')."""
    if seconds is None:
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


def truthy(value: str | bool | None) -> bool:
    """Query-string booleans: 'true'/'1'/'yes' -> True."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")
