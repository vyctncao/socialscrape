"""YouTube video endpoints: details, transcript, comments/replies, community posts, sponsors.

Everything comes from yt-dlp's YouTube extractors: `extract_info` plus the raw
watch-page data YoutubeIE downloads anyway (captured from `_initial_extract`),
the `next` API YoutubeIE uses for comments, and YoutubeTabIE's page loader.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp
from fastapi import APIRouter, Query
from yt_dlp.extractor.youtube import YoutubeIE
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import DownloadError, ExtractorError, traverse_obj

from .. import subtitles
from ..errors import ApiError, BadRequest, Forbidden, NotFound, UpstreamBlocked
from ..registry import sc_get
from ..util import clock
from ..ytdlp import translate_error, with_ydl
from . import _youtube_videos_parse as P

router = APIRouter()

UNSUPPORTED = {
    "/v1/youtube/shorts/trending": (
        "YouTube removed its Trending page (yt-dlp's YoutubeTabIE now gets redirected to the home page for "
        "/feed/trending), and no yt-dlp extractor calls the Shorts feed/reel API that serves trending Shorts."),
    "/v1/youtube/search/typeahead": (
        "Search suggestions come from YouTube's suggest API (suggestqueries.google.com), which no yt-dlp "
        "extractor calls."),
}

_LANG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
# BCP-47 tags YouTube spells differently (or only regionally) in its UI language list.
_UI_LANG_ALIASES = {"zh": "zh-CN", "zh-hans": "zh-CN", "zh-sg": "zh-CN", "zh-hant": "zh-TW", "he": "iw",
                    "nb": "no", "nn": "no", "in": "id", "tl": "fil"}
_BOOL = {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False, "": False}


# --- input validation -----------------------------------------------------------


def video_id(url: str) -> str:
    url = (url or "").strip()
    if "://" not in url and not re.fullmatch(r"[\w-]{11}", url):
        url = "https://" + url  # 'youtube.com/watch?v=..', 'youtu.be/..'
    m = YoutubeIE._match_valid_url(url)
    if not m or not m.group("id"):
        raise BadRequest("Not a YouTube video URL", code="unsupported_url")
    return m.group("id")


def post_id(url: str) -> str:
    """Post id from youtube.com/post/<id> or a channel's community?lb=<id> / posts?lb=<id> URL."""
    url = (url or "").strip()
    parsed = urlparse(url if "://" in url else "https://" + url)
    if (parsed.hostname or "").lower() in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if m := re.fullmatch(r"/post/([\w-]{10,})/?", parsed.path):
            return m.group(1)
        if re.fullmatch(r"(?:/(?:channel/[\w-]+|@[^/]+|c/[^/]+|user/[^/]+))?/(?:community|posts)/?", parsed.path):
            lb = parse_qs(parsed.query).get("lb") or [""]
            if re.fullmatch(r"[\w-]{10,}", lb[0]):
                return lb[0]
    raise BadRequest("Not a YouTube community post URL", code="unsupported_url")


def ui_language(language: str | None) -> str | None:
    """Closest YouTube UI language for a BCP-47 tag ('zh' -> 'zh-CN', 'en-US' -> 'en'), None if YouTube has none."""
    tag = caption_language(language)
    if not tag:
        return None
    codes = {c.lower(): c for c in YoutubeIE._SUPPORTED_LANG_CODES}
    parts = tag.lower().split("-")
    for n in range(len(parts), 0, -1):
        prefix = "-".join(parts[:n])
        if code := codes.get(prefix) or codes.get(_UI_LANG_ALIASES.get(prefix, "").lower()):
            return code
    return None


def ui_language_args(language: str | None) -> dict[str, Any]:
    """`language` -> yt-dlp's youtube:lang extractor arg (the UI language YouTube answers in).
    Well-formed codes YouTube has no UI for fall back to its default (English)."""
    code = ui_language(language)
    return {"extractor_args": {"youtube": {"lang": [code]}}} if code else {}


def caption_language(language: str | None) -> str | None:
    if language is None or not language.strip():
        return None
    if not _LANG.fullmatch(language.strip()):
        raise BadRequest(f"Invalid language code: {language}")
    return language.strip()


def boolean(name: str, value: str | None) -> bool:
    v = (value or "").strip().lower()
    if v not in _BOOL:
        raise BadRequest(f"{name} must be true or false")
    return _BOOL[v]


def native_token(token: str, kind: str, video: str | None = None) -> tuple[str, str]:
    """Validate a comments/replies continuation token -> (token, video_id)."""
    token = token.strip().replace(" ", "+")
    target = P.token_target(token)
    if not target or target[1] != kind or (video and target[0] != video):
        what = "comments of this video" if kind == "comments" else "comment replies"
        raise BadRequest(f"continuationToken is not a token for {what}", code="bad_cursor")
    return token, target[0]


# --- yt-dlp plumbing ------------------------------------------------------------


def capture_watch_data(ydl: yt_dlp.YoutubeDL) -> dict[str, Any]:
    """Keep the ytInitialData / player responses YoutubeIE downloads (it drops most of them)."""
    ie = ydl.get_info_extractor("Youtube")
    raw: dict[str, Any] = {"initial_data": {}, "player_responses": []}
    original = ie._initial_extract

    def initial_extract(*args: Any, **kwargs: Any):
        result = original(*args, **kwargs)
        raw["initial_data"], raw["player_responses"] = result[2] or {}, result[4] or []
        return result

    ie._initial_extract = initial_extract
    return raw


def ensure_available(player_responses: list[dict[str, Any]]) -> None:
    """With ignore_no_formats_error yt-dlp returns a placeholder ('youtube video #id') for
    unavailable videos; raise the player's own reason instead."""
    if traverse_obj(player_responses, (..., "videoDetails", "videoId", {str}, any)):
        return
    reason = traverse_obj(player_responses, (..., "playabilityStatus", "reason", {str}, any))
    raise translate_error(ExtractorError(reason or "Video unavailable", expected=True))


AGE_RESTRICTED = "This video is age-restricted; YouTube only shows it to signed-in, age-verified users"


def is_age_restricted(player_responses: list[dict[str, Any]]) -> bool:
    """YouTube asked to confirm age (a mere content warning, which anyone can click through, doesn't count)."""
    return any(YoutubeIE._is_agegated(pr)
               and traverse_obj(pr, ("playabilityStatus", "status")) != "CONTENT_CHECK_REQUIRED"
               for pr in player_responses if isinstance(pr, dict))


def ensure_playable(player_responses: list[dict[str, Any]], info: dict[str, Any]) -> None:
    """For routes that need the video itself (its captions): yt-dlp still returns the details of a video YouTube
    won't play anonymously (geo-blocked, members-only, login-gated), with no formats or captions; raise why.
    Videos that kept caption tracks, or that one player client could play, pass, and so do waits ('Premieres in
    2 hours')."""
    if info.get("subtitles") or info.get("automatic_captions"):
        return
    statuses = traverse_obj(player_responses, (..., "playabilityStatus", {dict})) or []
    if not statuses or any(s.get("status") == "OK" for s in statuses):
        return
    if is_age_restricted(player_responses):
        raise Forbidden(AGE_RESTRICTED, code="age_restricted")
    reasons = traverse_obj(statuses, (..., ("reason", ("errorScreen", (
        ("playerErrorMessageRenderer", "subreason"),
        ("playerInterstitialRenderer", "content", "interstitialViewModel", "description")))), {P.text_of}, {str}))
    reasons = [r for r in reasons or [] if r != "Video unavailable"]  # the headline over the actual reason
    geo = next((r for r in reasons if re.search(r"\bcountr(?:y|ies)\b", r, re.I)), None)
    if geo:
        raise Forbidden(geo, code="geo_restricted")  # 'The uploader has not made this video available in your country'
    members = next((r for r in reasons if re.search(r"\bmembers\b", r, re.I)), None)
    if members:
        raise Forbidden(members)  # "This video is available to this channel's members on level: ..."
    errors = (translate_error(ExtractorError(r, expected=True)) for r in reasons)
    err = next((e for e in errors if isinstance(e, (Forbidden, UpstreamBlocked))), None)
    if err:  # sign-in required, bot check
        raise err


def extract_watch(ydl: yt_dlp.YoutubeDL, vid: str, *, process: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    """(info as extract_info returns it, raw watch-page data) for an available video."""
    raw = capture_watch_data(ydl)
    info = ydl.extract_info(watch_url(vid), download=False, process=process)
    if not info:
        raise NotFound("No data returned for this video")
    ensure_available(raw["player_responses"])
    return info, raw


def track_formats(ie_info: dict[str, Any], key: str, is_auto: bool) -> list[dict[str, Any]]:
    return ((ie_info.get("automatic_captions") if is_auto else ie_info.get("subtitles")) or {}).get(key) or []


def is_translated(ie_info: dict[str, Any], key: str, is_auto: bool) -> bool:
    """True for YouTube's machine-translated caption tracks (their URLs carry tlang=)."""
    return any("tlang=" in (t.get("url") or "") for t in track_formats(ie_info, key, is_auto))


def is_live_feed(ie_info: dict[str, Any], key: str, is_auto: bool) -> bool:
    """True for a live stream's captions: an HLS playlist that grows while it airs, not a file to download."""
    tracks = track_formats(ie_info, key, is_auto)
    return bool(tracks) and all(t.get("protocol") in ("m3u8", "m3u8_native") for t in tracks)


def download_transcript(ydl: yt_dlp.YoutubeDL, ie_info: dict[str, Any], *, language: str | None = None,
                        original: bool = False, player_responses: list[dict[str, Any]] = ()
                        ) -> subtitles.Transcript | None:
    """Pick a caption track (subtitles.pick_track) and download it, from an already extracted (process=False) info.
    None when no track matches, the match is a machine translation YouTube refuses anonymously (HTTP 429), or the
    video is live or upcoming (its captions, if any, are the live HLS feed). An uploaded track that downloads blank
    (HtVdAasjOgU's 'en' is one event of ' ') gives way to the auto-generated track of its language."""
    if ie_info.get("live_status") in ("is_live", "is_upcoming"):
        return None
    automatic = ie_info.get("automatic_captions") or {}
    translated = subtitles.machine_translated(automatic)
    choice = subtitles.pick_track(ie_info.get("subtitles") or {}, automatic, language=language, original=original,
                                  video_language=spoken_track_language(ie_info, player_responses),
                                  translated=translated)
    if not choice or is_live_feed(ie_info, *choice):
        return None
    transcript = download_track(ydl, ie_info, *choice)
    if choice[1] or not transcript or transcript.segments:
        return transcript
    asr = subtitles.pick_track({}, automatic, language=choice[0], translated=translated)
    if not asr or asr[0] in translated or is_live_feed(ie_info, *asr):
        return transcript  # no ASR track of that language, or only YouTube's machine translation into it
    return download_track(ydl, ie_info, *asr) or transcript


def download_track(ydl: yt_dlp.YoutubeDL, ie_info: dict[str, Any], key: str, is_auto: bool
                   ) -> subtitles.Transcript | None:
    try:
        with tempfile.TemporaryDirectory(prefix="socialscrape-subs-") as tmp:
            return subtitles._download_track(ydl, ie_info, key, is_auto, tmp)
    except (DownloadError, ExtractorError) as exc:
        if is_translated(ie_info, key, is_auto) and isinstance(translate_error(exc), UpstreamBlocked):
            return None
        raise


def youtube_ie(ydl: yt_dlp.YoutubeDL) -> Any:
    ie = ydl.get_info_extractor("Youtube")
    ie.initialize()
    return ie


def call_next(ie: Any, vid: str, query: dict[str, Any], note: str) -> dict[str, Any]:
    """POST youtubei/v1/next the way YoutubeIE does for comments."""
    try:
        return ie._extract_response(item_id=vid, query=query, ep="next", note=note,
                                    headers=ie.generate_api_headers()) or {}
    except ExtractorError as exc:
        if isinstance(exc.cause, HTTPError) and exc.cause.status == 400:
            raise BadRequest("YouTube rejected the continuationToken", code="bad_cursor") from exc
        raise


def ensure_video_exists(ie: Any, vid: str) -> None:
    """An empty comments response means "no such video" or "comments off"; tell them apart."""
    resp = call_next(ie, vid, {"videoId": vid, **ie._get_checkok_params()}, "Checking video")
    if not P._renderer(resp, "videoPrimaryInfoRenderer"):
        message = traverse_obj(resp, (
            "contents", "twoColumnWatchNextResults", "results", "results", "contents", ..., "itemSectionRenderer",
            "contents", ..., "backgroundPromoRenderer", "title", {P.text_of}, any))
        raise NotFound(message or "Video unavailable")


def checked_response(resp: dict[str, Any]) -> dict[str, Any]:
    """YouTube answers an unusable (tampered/expired) continuation with an empty 200; make that a 400."""
    if not resp.get("onResponseReceivedEndpoints"):
        raise BadRequest("YouTube rejected the continuationToken", code="bad_cursor")
    return resp


def exact_comment_count(ydl: yt_dlp.YoutubeDL, vid: str) -> int | None:
    """Exact comment total from the first comments page header (the watch page rounds it: '10M')."""
    ie = youtube_ie(ydl)
    try:
        resp = call_next(ie, vid, {"continuation": ie._generate_comment_continuation(vid)}, "Downloading comment count")
    except (ApiError, DownloadError, ExtractorError):
        return None
    return P.header_comment_count(resp)


def watch_url(vid: str) -> str:
    return P.WATCH.format(vid)


async def run(fn: Callable[[yt_dlp.YoutubeDL], dict[str, Any]], **opts: Any) -> dict[str, Any]:
    return await with_ydl("youtube", fn, **opts)


# --- routes ---------------------------------------------------------------------


@sc_get(router, "/v1/youtube/video", source="yt-dlp YoutubeIE (info dict + its watch-page ytInitialData/player responses)",
        notes="language sets YouTube's UI language (youtube:lang extractor arg; BCP-47 tags are mapped to the closest "
              "YouTube UI language, unknown ones fall back to English). likeCountInt is exact, read from the like "
              "button's label in any UI language (null when likes are hidden). watchNextVideos publish dates are "
              "estimated from their relative text ('2 days ago', localized ones too) and their viewCountInt is read "
              "from YouTube's localized short count ('46万'); lengthText uses ':' separators. commentCountInt is "
              "exact: when the watch page rounds it ('10M') one extra comments-header request is made. captionTracks "
              "baseUrls download without a PO token. Age-restricted videos are 403 (YouTube withholds the watch page "
              "without an age-verified login). descriptionLinks/chapters/collaborators item shapes are our own (SC's "
              "example lists are empty). cache_max_age handled by the server cache.")
async def youtube_video(url: str = Query(...), language: str | None = None):
    vid = video_id(url)

    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any]:
        info, raw = extract_watch(ydl, vid)
        if is_age_restricted(raw["player_responses"]):
            raise Forbidden(AGE_RESTRICTED, code="age_restricted")
        exact = exact_comment_count(ydl, vid) if P.is_rounded(P.comment_count_text(raw["initial_data"])) else None
        return P.video_payload(ydl.sanitize_info(info), raw["initial_data"], raw["player_responses"],
                               ydl.get_info_extractor("YoutubeTab"), comment_count=exact)

    return await run(go, **ui_language_args(language))


_TRACK_SUFFIX = re.compile(r"(?:\s*\((?:auto-generated|Original)\))+\s*$")


def segment_ends(segments: list[subtitles.Segment]) -> list[float]:
    """Cue ends clipped to the next cue's start (auto-caption cues overlap the line that follows)."""
    ends = [s.end for s in segments]
    for i, nxt in enumerate(segments[1:]):
        if nxt.start >= segments[i].start:
            ends[i] = min(ends[i], nxt.start)
    return ends


def transcript_payload(vid: str, info: dict[str, Any], transcript: subtitles.Transcript | None) -> dict[str, Any]:
    segments = transcript.segments if transcript else []
    name = transcript and (transcript.name or transcript.language)
    return {
        "videoId": vid,
        "type": P.video_type(info),
        "url": watch_url(vid),
        "transcript": [{
            "text": s.text,
            "startMs": str(round(s.start * 1000)),
            "endMs": str(round(end * 1000)),
            "startTimeText": clock(s.start),
        } for s, end in zip(segments, segment_ends(segments))] or None,
        "transcript_only_text": (transcript.text or None) if segments else None,
        "language": _TRACK_SUFFIX.sub("", name) if segments and name else None,
    }


def original_audio_language(player_responses: list[dict[str, Any]]) -> str | None:
    """The language of the audio track YouTube labels original ('Japanese original' -> 'ja'), read from the raw
    player responses by yt-dlp's own rule (so also when yt-dlp lists none of that track's formats)."""
    return traverse_obj(player_responses, (
        ..., "streamingData", "adaptiveFormats",
        lambda _, f: "original" in str(f["audioTrack"].get("displayName") or "").lower(), "audioTrack", "id", {str},
        {lambda i: i.split(".")[0] or None}, any))


def spoken_track_language(info: dict[str, Any], player_responses: list[dict[str, Any]] = ()) -> str | None:
    """The original audio language, as YouTube identifies it ('en-US'): the audio track labelled original, else the
    '-orig' ASR track if there is only one (an auto-dubbed video has one per dub)."""
    orig = [k[: -len("-orig")] for k in info.get("automatic_captions") or {} if k.endswith("-orig")]
    return original_audio_language(player_responses) or (orig[0] if len(orig) == 1 else None)


def spoken_language(info: dict[str, Any], player_responses: list[dict[str, Any]] = ()) -> str | None:
    """Base code of the original audio language, as YouTube identifies it."""
    lang = spoken_track_language(info, player_responses)
    return lang.split("-")[0].lower() if lang else None


@sc_get(router, "/v1/youtube/video/transcript", source="yt-dlp YoutubeIE subtitles/automatic_captions (socialscrape.subtitles pick/download)",
        notes="Default and original_audio=true pick the caption track in the original spoken language (the audio "
              "track YouTube labels original, else the '-orig' ASR track when there is only one: auto-dubbed videos "
              "have one per dub); when that language is unknown the default falls back to "
              "uploaded English, then any uploaded track, then auto-generated. language picks a real track of that "
              "language over YouTube's machine translation into it (en gets an en-US track); a language only "
              "available as a machine translation returns transcript null (YouTube rate-limits anonymous translated captions with "
              "HTTP 429). Live and upcoming streams return transcript null (a live stream's captions are an HLS feed, "
              "not a file). An uploaded track that downloads blank gives way to the auto-generated track of its "
              "language. Videos YouTube won't play anonymously are 403: geo_restricted, age_restricted, or "
              "forbidden (members-only, sign-in required); an age-restricted video that YouTube's embedded player "
              "plays keeps its captions. endMs is clipped to the next line's start. cache_max_age handled by the "
              "server cache.")
async def youtube_video_transcript(url: str = Query(...), language: str | None = None,
                                   original_audio: str | None = None):
    vid = video_id(url)
    lang = caption_language(language)
    original = boolean("original_audio", original_audio)
    by_spoken = original or not lang

    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any]:
        ie_info, raw = extract_watch(ydl, vid, process=False)
        ensure_playable(raw["player_responses"], ie_info)
        info = ydl.sanitize_info(ie_info)
        transcript = download_transcript(ydl, ie_info, language=None if by_spoken else lang, original=by_spoken,
                                         player_responses=raw["player_responses"])
        spoken = spoken_language(info, raw["player_responses"])
        if transcript and by_spoken and (spoken or original) and transcript.language.split("-")[0].lower() != spoken:
            transcript = None  # the identified original language has no captions (or it can't be identified)
        return transcript_payload(vid, info, transcript)

    return await run(go)


@sc_get(router, "/v1/youtube/video/comments", source="yt-dlp YoutubeIE comment continuations (youtubei/v1/next)",
        notes="Uses YouTube's own continuation tokens; order is ignored when continuationToken is given (the token "
              "already carries the sort). Like counts are YouTube's rounded figures ('4.8K'); publishedTime is "
              "estimated from relative text ('6 years ago'). A continuationToken YouTube can't use is a 400 "
              "bad_cursor.")
async def youtube_video_comments(url: str = Query(...), continuationToken: str | None = None, order: str | None = None):
    vid = video_id(url)
    sort = (order or "top").strip().lower()
    if sort not in ("top", "newest"):
        raise BadRequest("order must be 'top' or 'newest'")
    token = native_token(continuationToken, "comments", vid)[0] if continuationToken else None

    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any]:
        ie = youtube_ie(ydl)
        if token:
            resp = checked_response(call_next(ie, vid, {"continuation": token}, "Downloading comments"))
        else:
            resp = call_next(ie, vid, {"continuation": ie._generate_comment_continuation(vid)}, "Downloading comments")
            if not P.continuation_items(resp):
                try:
                    ensure_video_exists(ie, vid)
                except NotFound:
                    extract_watch(ydl, vid, process=False)  # the player's own reason: private, removed, ...
                return {"comments": [], "continuationToken": None}
            newest, selected = P.sort_token(P.comments_header(P.continuation_items(resp)), sort)
            if sort == "newest" and newest and not selected:
                resp = call_next(ie, vid, {"continuation": newest}, "Downloading newest comments")
        comments, next_token = P.map_comments(ie, resp, replies=False)
        return {"comments": comments, "continuationToken": next_token}

    return await run(go)


@sc_get(router, "/v1/youtube/video/comment/replies", source="yt-dlp YoutubeIE comment-reply continuations (youtubei/v1/next)",
        notes="Takes a repliesContinuationToken from /v1/youtube/video/comments or a continuationToken from a "
              "previous replies page. Like counts are rounded by YouTube; publishedTime is estimated from relative "
              "text. A continuationToken YouTube can't use is a 400 bad_cursor.")
async def youtube_video_comment_replies(continuationToken: str = Query(...)):
    token, vid = native_token(continuationToken, "replies")

    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any]:
        ie = youtube_ie(ydl)
        resp = checked_response(call_next(ie, vid, {"continuation": token}, "Downloading replies"))
        comments, next_token = P.map_comments(ie, resp, replies=True)
        return {"comments": comments, "continuationToken": next_token}

    return await run(go)


@sc_get(router, "/v1/youtube/community-post", source="yt-dlp YoutubeTabIE page data (youtube.com/post/<id>)",
        notes="publishedTime and likeCount are exact (the post page's schema.org microformat); publishedTime falls "
              "back to an estimate from relative text ('11 months ago') when the page doesn't describe the post "
              "itself. A shared post's URL returns the share (the sharing channel, its text, publish time and like "
              "count, null when YouTube shows none) with the shared post nested as originalPost, an extra field "
              "whose publishedTime is estimated. Polls and quizzes are returned as their text content only.")
async def youtube_community_post(url: str = Query(...)):
    pid = post_id(url)

    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any]:
        tab = ydl.get_info_extractor("YoutubeTab")
        tab.initialize()
        try:
            data, _ = tab._extract_data(f"{P.YT}/post/{pid}", pid)
        except (DownloadError, ExtractorError) as exc:
            if isinstance(translate_error(exc), NotFound):
                raise NotFound("Community post not found") from exc
            raise
        post = P.find_post(data or {}, pid)
        if not post:
            raise NotFound("Community post not found")
        return P.post_payload(tab, post, data)

    return await run(go)


def sponsors_payload(info: dict[str, Any], paid: bool, transcript: subtitles.Transcript | None) -> dict[str, Any]:
    channel = P.video_channel(info)
    own = {P._brand_key(v) for v in (channel["title"], channel["handle"]) if v} - {""}
    text = transcript.text if transcript and transcript.segments else None
    sponsors, methods = P.detect_sponsors(info.get("description"), text, own=own, paid_promotion=paid)
    return {
        "video": {"id": info["id"], "url": watch_url(info["id"]), "title": info.get("title"), "channel": channel,
                  "isPaidPromotion": paid},
        "suspectedSponsors": sponsors,
        "sponsorDetection": {
            "status": "found" if sponsors else "not_found",
            "methods": methods,
            "transcriptAvailable": text is not None,
            "note": P.SPONSOR_NOTE,
        },
    }


@sc_get(router, "/v1/youtube/video/sponsors", source="yt-dlp YoutubeIE (description, paidContentOverlay) + subtitles pipeline",
        notes="Heuristic, like SC's: sponsor wording / promo codes / non-social links in the description, "
              "corroborated by the transcript (language picks the caption track; the default track is used when "
              "that language has none or only a throttled machine translation). A link counts only in a paragraph "
              "with sponsor wording or a promo code. Link shorteners and redirect hosts (bit.ly, LTT's lmg.gg...) "
              "name no brand, so a sponsor behind one is found only when the text names it. isPaidPromotion is "
              "YouTube's 'Includes paid promotion' flag; YouTube never names the sponsor.")
async def youtube_video_sponsors(url: str = Query(...), language: str | None = None):
    vid = video_id(url)
    lang = caption_language(language)

    def go(ydl: yt_dlp.YoutubeDL) -> dict[str, Any]:
        ie_info, raw = extract_watch(ydl, vid, process=False)
        info = ydl.sanitize_info(ie_info)
        transcript = None
        for want in dict.fromkeys((lang, None)):
            try:
                transcript = download_transcript(ydl, ie_info, language=want, player_responses=raw["player_responses"])
            except (DownloadError, ExtractorError) as exc:
                if isinstance(translate_error(exc), UpstreamBlocked):
                    raise  # rate-limited: say so rather than report "no transcript"
                transcript = None  # best effort: the description alone still counts
            if transcript and transcript.segments:
                break
        return sponsors_payload(info, P.is_paid_promotion(raw["player_responses"]), transcript)

    return await run(go)
