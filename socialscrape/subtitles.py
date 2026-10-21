"""Caption/subtitle fetching via yt-dlp's own subtitle pipeline.

yt-dlp knows how to download each platform's caption tracks (impersonation,
HLS-wrapped tracks, PO tokens...), so instead of fetching track URLs ourselves
we pick a track, let yt-dlp write it to a temp dir, and parse the file.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp

FORMAT_PREFERENCE = "json3/vtt/srt/srv3/best"


@dataclass
class Segment:
    start: float  # seconds
    end: float
    text: str


@dataclass
class Transcript:
    language: str  # track key as yt-dlp reports it, e.g. "en", "en-US", "de-orig"
    name: str | None  # human name, e.g. "English (auto-generated)"
    is_auto: bool
    ext: str
    raw: str  # file contents as downloaded (e.g. WEBVTT text)
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments if s.text).strip()


def _base(lang: str) -> str:
    return lang.split("-")[0].lower()


# The script a Chinese region writes in, for tags that leave it out: YouTube lists zh-CN and zh-TW as often as
# zh-Hans and zh-Hant.
_ZH_SCRIPTS = {"tw": "hant", "hk": "hant", "mo": "hant", "cn": "hans", "sg": "hans", "my": "hans"}


def _script(lang: str) -> str | None:
    """The script a tag names or implies ('sr-Latn' -> 'latn', 'zh-TW' -> 'hant'), None when it doesn't say."""
    base, *subtags = lang.lower().split("-")
    if not subtags:
        return None
    if len(subtags[0]) == 4 and subtags[0].isalpha() and subtags[0] != "orig":
        return subtags[0]
    return _ZH_SCRIPTS.get(subtags[0]) if base == "zh" else None


def pick_track(
    subtitles: dict[str, Any],
    automatic: dict[str, Any],
    *,
    language: str | None = None,
    original: bool = False,
    video_language: str | None = None,
    translated: Collection[str] = (),
) -> tuple[str, bool] | None:
    """Choose (track_key, is_auto).

    language        explicit request ('en', 'en-US'); exact match beats prefix match,
                    uploaded tracks beat auto-generated ones, and a real track of the
                    language beats a machine translation ('en-US' over a translated 'en').
                    Short of an exact match, a track in another script comes last (zh-HK
                    takes zh-TW or zh-Hant over zh-CN, sr-Cyrl takes sr over sr-Latn).
    original        prefer the track in the spoken language (yt-dlp marks the
                    untranslated ASR track with an '-orig' suffix on YouTube).
    video_language  the original audio language, if known. An auto-dubbed video has
                    one '-orig' track per audio track: this picks among them, and
                    without it none of them counts as the spoken language.
    translated      keys of `automatic` that are machine translations (YouTube's tlang= tracks).
                    Uploaded tracks are never translations, even under the same key.
    default         uploaded English -> spoken-language track -> any uploaded ->
                    auto English -> any auto.
    """
    subtitles = {k: v for k, v in (subtitles or {}).items() if v and k != "live_chat"}
    automatic = {k: v for k, v in (automatic or {}).items() if v}
    translated = frozenset(translated)

    def match(tracks: dict[str, Any], want: str, avoid: frozenset[str] = frozenset()) -> str | None:
        want_l, script = want.lower(), _script(want)

        def other_script(key: str) -> int:  # 0 same script, 1 unknown (either tag doesn't say), 2 another one
            return 1 if not (script and (key_script := _script(key))) else 0 if key_script == script else 2

        ranked = [(key in avoid, key.lower() != want_l, other_script(key), key.endswith("-orig"), i, key)
                  for i, key in enumerate(tracks) if _base(key) == _base(want_l)]
        return min(ranked)[-1] if ranked else None

    real = {k: v for k, v in automatic.items() if k not in translated}
    orig = {k: v for k, v in real.items() if k.endswith("-orig")}
    if video_language:
        spoken = match(orig, video_language) or match(real, video_language) or match(subtitles, video_language)
    else:
        spoken = next(iter(orig)) if len(orig) == 1 else None

    if original:
        if video_language and (key := match(subtitles, video_language)):
            return key, False
        if spoken:
            return spoken, spoken in automatic and spoken not in subtitles
        # Fall through to default choice.

    if language and not original:
        if key := match(subtitles, language):
            return key, False
        if key := match(automatic, language, translated):
            return key, True
        return None

    if key := match(subtitles, "en"):
        return key, False
    if spoken:
        return spoken, spoken not in subtitles
    if subtitles:
        return next(iter(subtitles)), False
    if key := match(automatic, "en", translated):
        return key, True
    if automatic:
        return next(iter(real or automatic)), True
    return None


def machine_translated(automatic: dict[str, Any]) -> set[str]:
    """Keys of `automatic` that are machine translations: YouTube's tlang= tracks (a key also listed for the
    real track of that language counts too, since yt-dlp may download either)."""
    return {key for key, tracks in (automatic or {}).items()
            if any("tlang=" in (t.get("url") or "") for t in tracks or [])}


# --- parsing -----------------------------------------------------------------

_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAG = re.compile(r"<[^>]+>")


def _ts(value: str) -> float:
    m = _TS.search(value)
    if not m:
        return 0.0
    h, mi, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_json3(raw: str) -> list[Segment]:
    data = json.loads(raw)
    out = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if not text:
            continue
        start = (ev.get("tStartMs") or 0) / 1000
        out.append(Segment(start, start + (ev.get("dDurationMs") or 0) / 1000, text))
    return out


def parse_cues(raw: str) -> list[Segment]:
    """WebVTT or SRT. Collapses YouTube's rolling auto-caption duplicates."""
    out: list[Segment] = []
    last_line = None
    for block in re.split(r"\r?\n\r?\n", raw):
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        start_s, _, end_s = lines[idx].partition("-->")
        text_lines = []
        for ln in lines[idx + 1 :]:
            clean = html.unescape(_TAG.sub("", ln)).strip()
            if clean and clean != last_line:
                text_lines.append(clean)
                last_line = clean
        if text_lines:
            out.append(Segment(_ts(start_s), _ts(end_s.split()[0] if end_s.split() else end_s), " ".join(text_lines)))
    return out


def parse(raw: str, ext: str) -> list[Segment]:
    if ext == "json3":
        try:
            return parse_json3(raw)
        except (ValueError, TypeError):
            return []
    if ext in ("vtt", "srt"):
        return parse_cues(raw)
    if ext.startswith("srv") or ext == "ttml":
        segs = []
        for m in re.finditer(r'<(?:text|p)[^>]*?(?:start|begin|t)="([^"]+)"[^>]*?(?:dur|d)?="?([^"\s>]*)"?[^>]*>(.*?)</(?:text|p)>', raw, re.S):
            start_raw, dur_raw, body = m.groups()
            start = float(start_raw) / (1000 if ext == "srv3" else 1) if re.fullmatch(r"[\d.]+", start_raw) else _ts(start_raw)
            dur = float(dur_raw) / (1000 if ext == "srv3" else 1) if re.fullmatch(r"[\d.]+", dur_raw or "") else 0.0
            text = html.unescape(_TAG.sub("", body)).strip()
            if text:
                segs.append(Segment(start, start + dur, text))
        return segs
    return []


# --- fetching ----------------------------------------------------------------


def _download_track(ydl: yt_dlp.YoutubeDL, ie_info: dict[str, Any], key: str, is_auto: bool, tmp: str) -> Transcript | None:
    ydl.params.update(
        writesubtitles=not is_auto,
        writeautomaticsub=is_auto,
        subtitleslangs=[re.escape(key)],
        subtitlesformat=FORMAT_PREFERENCE,
        skip_download=True,
        outtmpl={"default": str(Path(tmp) / "%(id)s.%(ext)s")},
        paths={"home": tmp, "temp": tmp},
    )
    info = ydl.process_ie_result(ie_info, download=True)
    track = (info.get("requested_subtitles") or {}).get(key)
    if not track or not track.get("filepath") or not Path(track["filepath"]).exists():
        return None
    raw = Path(track["filepath"]).read_text(encoding="utf-8", errors="replace")
    source = (ie_info.get("automatic_captions") if is_auto else ie_info.get("subtitles")) or {}
    name = next((t.get("name") for t in source.get(key) or [] if t.get("name")), None)
    return Transcript(key, name, is_auto, track.get("ext") or "", raw, parse(raw, track.get("ext") or ""))
