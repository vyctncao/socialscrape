"""Optional local speech-to-text, standing in for ScrapeCreators' "AI transcription".

Disabled unless SOCIALSCRAPE_TRANSCRIBE is set:
  faster-whisper   pip install 'socialscrape[whisper]'   (CPU/CUDA)
  mlx-whisper      pip install 'socialscrape[mlx]'       (Apple Silicon)
A backend whose package isn't installed answers 501 transcription_unavailable;
one failing at run time (bad model name, download error) 500
transcription_failed, with the details in the server log.

Audio is downloaded with yt-dlp (same cookies/proxy as everything else), then
transcribed in a thread of its own, one file at a time. Like ScrapeCreators,
media longer than 120 s is refused unless the caller passes
allow_long_transcription=true. mlx-whisper has no VAD: _run_backend goes by
Whisper's own no-speech and decoding stats instead (best effort, see the
README's "AI transcription").
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any

import yt_dlp

from .config import ConfigError, get_settings
from .errors import ApiError, BadRequest, NotFound, NotSupported
from .subtitles import Segment, Transcript
from .util import short_lang
from .ytdlp import stop_executor, with_ydl

log = logging.getLogger("socialscrape.transcribe")

SHORT_LIMIT_SECONDS = 120
# One transcription at a time (models are memory hungry), in a thread of its own: a queued transcription used to
# hold a yt-dlp worker while it waited, so a few of them starved every platform request.
_executor: ThreadPoolExecutor | None = None
# backend -> (module it imports, pyproject extra that installs it)
_MODULES = {"faster-whisper": ("faster_whisper", "whisper"), "mlx-whisper": ("mlx_whisper", "mlx")}
# mlx-whisper loads a Hugging Face repo. The mlx-community conversions of most model names (faster-whisper's) are
# whisper-{name}-mlx; these are named otherwise, and faster-whisper's _NO_MLX_REPO have none (checked at startup).
_MLX_REPOS = {
    "turbo": "mlx-community/whisper-turbo",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "distil-large-v3": "mlx-community/distil-whisper-large-v3",
    "distil-medium.en": "mlx-community/distil-whisper-medium.en",
}
_NO_MLX_REPO = frozenset({"distil-large-v2", "distil-small.en", "distil-large-v3.5"})

# Whisper's tokenizer languages (the same list in mlx-whisper and faster-whisper). Every multilingual model has
# the first 99; yue (Cantonese) is the 100th and only large-v3-based models have it.
WHISPER_LANGUAGES = frozenset(
    "en zh de es ru ko fr ja pt tr pl ca nl ar sv it id hi fi vi he uk el ms cs ro da hu ta no th ur hr bg lt la mi "
    "ml cy sk te fa lv bn sr az sl kn et mk br eu is hy ne mn bs kk sq sw gl mr pa si km sn yo so af oc ka be tg sd "
    "gu am yi lo uz fo ht ps tk nn mt sa lb my bo tl mg as tt haw ln ha ba jw su yue".split())
_V3_ONLY = frozenset({"yue"})
_ALIASES = {"jv": "jw", "nb": "no"}  # ISO 639-1 codes Whisper spells differently
# Multilingual model names both backends resolve to a pre-large-v3 model (99 languages, no yue). 'large' is left
# out: faster-whisper maps it to large-v3.
_PRE_V3_MODELS = frozenset("tiny base small medium large-v1 large-v2 distil-large-v2".split())
# English-only models (tiny.en, distil-small.en, mlx-community/whisper-small.en-mlx): both backends ignore
# `language` and transcribe as English.
_ENGLISH_ONLY = re.compile(r"\.en(?![a-z0-9])")
# Whisper's own no_speech, logprob and compression_ratio thresholds (beyond either of the last two, decoding failed)
NO_SPEECH_THRESHOLD = 0.6
LOGPROB_THRESHOLD = -1.0
COMPRESSION_RATIO_THRESHOLD = 2.4
# Between this and NO_SPEECH_THRESHOLD Whisper is unsure; a forced `language` makes it hallucinate there
DOUBTFUL_NO_SPEECH = 0.4
# Subtitle credits and outro lines Whisper learned from subtitled videos (all seen on no-speech clips).
_CREDITS = re.compile(r"amara\.org|字幕|sous-titr|untertitel|субтитр|sottotitol|altyaz[ıi]|tekstet|ondertitel|napisy|"
                      r"suscr[ií]b|subscri|구독|订阅|訂閱|ご視聴|thanks? (?:you )?for watching|terima kasih "
                      r"(?:kerana|karena|telah|sudah) menonton|grazie per la visione|obrigad[oa] por assistir", re.I)


@lru_cache(maxsize=None)
def installed(backend: str) -> bool:
    """Whether the backend's package is importable (checked without importing it: both are slow to load)."""
    return backend in _MODULES and importlib.util.find_spec(_MODULES[backend][0]) is not None


def enabled() -> bool:
    backend = get_settings().transcribe_backend
    return backend in _MODULES and installed(backend)


def mlx_repo(name: str) -> str:
    """The repo id (or local directory) mlx-whisper loads for SOCIALSCRAPE_WHISPER_MODEL=name."""
    if "/" in name:
        return name
    return _MLX_REPOS.get(name.lower(), f"mlx-community/whisper-{name}-mlx")


def check_model() -> None:
    """ConfigError for a model name the configured backend has no model for; create_app() calls it, so that is a
    startup error instead of a 500 transcription_failed on every request."""
    s = get_settings()
    if s.transcribe_backend == "mlx-whisper" and s.whisper_model.lower() in _NO_MLX_REPO:
        raise ConfigError(f"SOCIALSCRAPE_WHISPER_MODEL {s.whisper_model!r} has no mlx-whisper conversion; use "
                          f"another model name or a Hugging Face repo id")


def require_enabled() -> str:
    """The configured backend, or 501: transcription_disabled (not configured) / transcription_unavailable
    (configured, package missing). Call it before any download."""
    backend = get_settings().transcribe_backend
    if enabled():
        return backend
    if backend not in _MODULES:
        raise NotSupported(
            "AI transcription is disabled on this server. Set SOCIALSCRAPE_TRANSCRIBE=faster-whisper "
            "or mlx-whisper (and install the matching extra) to enable it.",
            code="transcription_disabled",
        )
    raise NotSupported(
        f"AI transcription is configured ({backend}) but its package is not installed on this server; "
        f"install the '{_MODULES[backend][1]}' extra (uv sync --extra {_MODULES[backend][1]}).",
        code="transcription_unavailable",
    )


def whisper_language(language: str | None) -> str | None:
    """'en' / 'EN' / 'eng' / 'spa-ES' / 'cmn' -> the code Whisper takes ('en', 'es', 'zh'); None if Whisper has no
    such language.

    The backends raise ValueError on anything else, ISO 639-2 codes included.
    """
    code = short_lang(language)
    code = _ALIASES.get(code, code)
    return code if code in WHISPER_LANGUAGES else None


def check_language(language: str | None, model: str | None = None) -> str | None:
    """`language` as the Whisper code for the configured model (None: auto-detect).

    400 for a language Whisper doesn't know, for yue on a known pre-large-v3 model, and for anything but English
    on an English-only (*.en) model. Other model names (large-v3, turbo, repo ids, paths) can't be judged without
    loading the model: transcribe() turns the model's own refusal into the same 400 after the download.
    """
    if not language:
        return None
    code = whisper_language(language)
    if not code:
        raise BadRequest(f"language {language} is not supported for AI transcription")
    name = (get_settings().whisper_model if model is None else model).strip().lower()
    if code in _V3_ONLY and name in _PRE_V3_MODELS:
        raise BadRequest(f"language {language} is not supported by this server's Whisper model "
                         "(it needs a large-v3 model)")
    if code != "en" and _ENGLISH_ONLY.search(name):
        raise BadRequest(f"language {language} is not supported by this server's Whisper model "
                         "(it is English-only)")
    return code


def _speech(segments: list[Segment]) -> list[Segment]:
    """Drop segments without a single letter or digit ('!', '♪', '...'): Whisper's output for music and noise."""
    return [s for s in segments if any(c.isalnum() for c in s.text)]


def _no_speech(segment: dict[str, Any]) -> bool:
    """Whether an mlx-whisper segment is probably not speech (best effort: see DOUBTFUL_NO_SPEECH)."""
    no_speech = segment.get("no_speech_prob") or 0
    if no_speech > NO_SPEECH_THRESHOLD:
        return True
    if no_speech <= DOUBTFUL_NO_SPEECH:
        return False
    return _failed(segment) or bool(_CREDITS.search(str(segment.get("text") or "")))


def _failed(segment: dict[str, Any]) -> bool:
    """Whether Whisper failed to decode the segment's window at every temperature (its own thresholds)."""
    logprob = segment.get("avg_logprob")
    return ((logprob is not None and logprob < LOGPROB_THRESHOLD)  # NaN (no tokens) compares False
            or (segment.get("compression_ratio") or 0) > COMPRESSION_RATIO_THRESHOLD)


@lru_cache(maxsize=1)
def _faster_whisper_model(name: str):
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    return WhisperModel(name, device="auto", compute_type="int8")


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
    return _executor


def shutdown_pool() -> list[threading.Thread]:
    """Like ytdlp.shutdown_pool, for the transcription thread."""
    global _executor
    pool, _executor = _executor, None
    return stop_executor(pool)


def _run_backend(backend: str, audio_path: str, language: str | None) -> tuple[str | None, list[Segment]]:
    name = get_settings().whisper_model
    if backend == "faster-whisper":
        segments, info = _faster_whisper_model(name).transcribe(audio_path, language=language, vad_filter=True)
        return info.language, _speech([Segment(s.start, s.end, s.text.strip()) for s in segments])
    import mlx_whisper  # type: ignore[import-not-found]

    result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=mlx_repo(name), language=language)
    segments = result.get("segments") or []
    # mlx-whisper has no VAD, and its no-speech stats are per 30-s window: one that ends on a few seconds of speech
    # (then music, silence or the last window's padding) rates high too. So they only tell a clip without speech;
    # in one with speech, only the windows Whisper is unsure about and failed to decode (a repetition loop) go.
    if all(_no_speech(s) for s in segments):
        segments = []
    return result.get("language"), _speech([
        Segment(float(s["start"]), float(s["end"]), str(s["text"]).strip()) for s in segments
        if not ((s.get("no_speech_prob") or 0) > DOUBTFUL_NO_SPEECH and _failed(s))
    ])


# What ffmpeg (mlx-whisper's audio loader) says about a file without an audio stream.
_NO_AUDIO = re.compile(r"does not contain any stream")


def to_vtt(segments: list[Segment]) -> str:
    def ts(t: float) -> str:
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"

    cues = [f"{ts(s.start)} --> {ts(s.end)}\n{s.text}" for s in segments if s.text]
    return "WEBVTT\n\n" + "\n\n".join(cues) + ("\n" if cues else "")


async def transcribe(
    url: str,
    *,
    platform: str,
    language: str | None = None,
    allow_long: bool = False,
    duration: float | None = None,
    **ydl_opts: Any,
) -> Transcript:
    """Download `url`'s audio with yt-dlp and run local Whisper on it.

    `duration` (seconds), if already known, lets us refuse long media before downloading. `language` may be an
    ISO 639-1 or 639-2 code ('en', 'eng'); see check_language for the ones that are refused.
    """
    backend = require_enabled()
    language = check_language(language)
    if duration is not None and duration > SHORT_LIMIT_SECONDS and not allow_long:
        raise BadRequest(
            f"Media is {int(duration)}s long; AI transcription is limited to {SHORT_LIMIT_SECONDS}s "
            "unless allow_long_transcription=true",
            code="transcription_too_long",
        )

    with tempfile.TemporaryDirectory(prefix="socialscrape-audio-") as tmp:

        def download(ydl: yt_dlp.YoutubeDL) -> tuple[str, dict[str, Any]]:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise NotFound("No media found")
            if info.get("_type") == "playlist":  # carousels: transcribe the first video
                entries = [e for e in info.get("entries") or [] if e]
                if not entries:
                    raise NotFound("No audio/video in this post")
                info = entries[0]
            dur = info.get("duration")
            if dur and dur > SHORT_LIMIT_SECONDS and not allow_long:
                raise BadRequest(
                    f"Media is {int(dur)}s long; AI transcription is limited to {SHORT_LIMIT_SECONDS}s "
                    "unless allow_long_transcription=true",
                    code="transcription_too_long",
                )
            info = ydl.process_ie_result(info, download=True)
            files = [d.get("filepath") for d in info.get("requested_downloads") or [] if d.get("filepath")]
            if not files:
                raise NotFound("No downloadable audio for this media")
            return files[0], info

        path, info = await with_ydl(
            platform,
            download,
            format="bestaudio/best",
            skip_download=False,
            outtmpl={"default": str(Path(tmp) / "%(id)s.%(ext)s")},
            paths={"home": tmp, "temp": tmp},
            noplaylist=True,
            **ydl_opts,
        )
        lang, segments = None, []
        if (info or {}).get("acodec") != "none":  # a video-only stream has no speech to find
            try:
                lang, segments = await asyncio.get_running_loop().run_in_executor(
                    _pool(), _run_backend, backend, path, language)
            except Exception as exc:
                if type(exc) is ValueError and language in _V3_ONLY:  # both backends' answer to a language it lacks
                    raise BadRequest(f"language {language} is not supported by this server's Whisper model "
                                     f"({get_settings().whisper_model})") from exc
                if not _NO_AUDIO.search(str(exc)):  # no audio stream after all (yt-dlp didn't know the codecs)
                    # Bad model name, model download failure, a broken install: the server's problem.
                    log.warning("transcription failed (%s, model %s)", backend, get_settings().whisper_model,
                                exc_info=True)
                    raise ApiError(f"AI transcription failed ({backend}, model {get_settings().whisper_model}); "
                                   "see the server log", code="transcription_failed") from exc

    return Transcript(language=lang or language or "", name=None, is_auto=True, ext="vtt", raw=to_vtt(segments),
                      segments=segments)
