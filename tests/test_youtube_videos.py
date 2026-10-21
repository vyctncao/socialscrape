"""YouTube video endpoints: offline mapper tests + live tests against YouTube (-m live)."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import quote

import pytest
import yt_dlp
from yt_dlp.extractor.youtube import YoutubeIE, YoutubeTabIE
from yt_dlp.utils import DownloadError, ExtractorError

from socialscrape import subtitles
from socialscrape.errors import ApiError, BadRequest, Forbidden
from socialscrape.platforms import _youtube_videos_parse as P
from socialscrape.platforms import youtube_videos as M
from tests.contract import assert_conforms

# Raw responses cached by the live probe runs (too large to commit); replay tests skip without them.
PROBE = Path(os.environ.get("SOCIALSCRAPE_PROBE_DIR", "/tmp/sc")) / "probe-youtube_videos"
ZOO = "jNQXAC9IVRw"  # "Me at the zoo": chapters, uploaded en/de captions, heatmap
SPONSORED = "AVO0ifle-OU"  # paid-promotion badge, BetterHelp in the description
POST = "Ugkxvj2KoApYAXoqLWnKVr6zZe5JjeHrQeP8"
MISSING = "Qq7xZ0nonex"
AGE_GATED = "HtVdAasjOgU"  # "Sign in to confirm your age"
PRIVATE = "yZIXLfi8CZQ"


def watch(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


# Native tokens YouTube issued for SPONSORED (comments page 2, and replies to its top comment).
COMMENTS_TOKEN = (
    "Eg0SC0FWTzBpZmxlLU9VGAYy_gIK1AJnZXRfcmFua2VkX3N0cmVhbXMtLUNxWUJDSUFFRlJlMzBUZ2Ftd0VLbGdFSTJGOFFnQVFZQnlLTEFidU5rMElNSk0wSUxpbzNXWWs5eVZIZ2xDOVFMcThyU3VyWlNUdzRoZWdPZnFybnMweFpaTEtCZnlHOHZ5eUR3OUFXQlRRWVRBelRkSGJOeDAzTUV6eG5SSTJCTDBCVUpZN3FxeGhaZDQ2U0xDYWMzd1FrMml6MzdDaWZickRLZy1JVjlhN3lyWlgwclFIUWFGZ0NyYV9IM3RNRlZjMXRQcWJFRk95ZmN5WHhCZ2tFc1hEUUtxVEkwQTBRRkJJRkNJWWdHQUFTQlFpSklCZ0FFZ1VJaUNBWUFCSUZDS2dnR0FBU0JRakFJQmdBRWdVSWh5QVlBQklIQ0lVZ0VBb1lBUklGQ0xzZ0dBQVNCd2lFSUJBUUdBRlFBRmdCIhEiC0FWTzBpZmxlLU9VMAB4ASgUQhBjb21tZW50cy1zZWN0aW9u")
REPLIES_TOKEN = (
    "Eg0SC0FWTzBpZmxlLU9VGAYygwEaUBIaVWd5eW9abC02MkZDMy1oMllCNTRBYUFCQWciAggAKhhVQzN2UUVqUmh3Z0gySEFPQkt3TGp4TkEyC0FWTzBpZmxlLU9VQAFICoIBAggBQi9jb21tZW50LXJlcGxpZXMtaXRlbS1VZ3l5b1psLTYyRkMzLWgyWUI1NEFhQUJBZw%3D%3D")


@pytest.fixture(scope="module")
def ies():
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        yield ydl.get_info_extractor("Youtube"), ydl.get_info_extractor("YoutubeTab")


def nest(path: tuple[str, ...], leaf: object) -> dict:
    for key in reversed(path):
        leaf = {key: leaf}
    return leaf


def cached(name: str) -> dict:
    path = PROBE / name
    if not path.exists():
        pytest.skip(f"probe cache {path} missing")
    return json.loads(path.read_text())


# --- offline: input validation ----------------------------------------------------


@pytest.mark.parametrize("url", [watch(ZOO), f"https://youtu.be/{ZOO}", f"https://www.youtube.com/shorts/{ZOO}",
                                 f"https://m.youtube.com/watch?v={ZOO}&list=PL123", ZOO,
                                 f"youtube.com/watch?v={ZOO}", f"youtu.be/{ZOO}", f"www.youtube.com/shorts/{ZOO}",
                                 f"HTTPS://WWW.YOUTUBE.COM/watch?v={ZOO}", f"Https://youtu.be/{ZOO}", f"Youtu.be/{ZOO}",
                                 f"M.YouTube.com/shorts/{ZOO}",
                                 # protocol-relative, as old embed codes give the iframe src (and YoutubeIE's tests)
                                 f"//www.youtube.com/embed/{ZOO}", f"//www.YouTube.com/watch?v={ZOO}",
                                 f"//www.youtube.com/v/{ZOO}?version=3&amp;hl=en_US"])
def test_video_id_accepts_youtube_urls(url):
    assert M.video_id(url) == ZOO


@pytest.mark.parametrize("url", ["https://www.tiktok.com/@x/video/1", "https://www.youtube.com/@jawed",
                                 "https://www.youtube.com/watch?v=short", "", "not a url",
                                 f"//www.example.com/embed/{ZOO}", f"///www.youtube.com/embed/{ZOO}"])
def test_video_id_rejects_other_urls(url):
    with pytest.raises(BadRequest):
        M.video_id(url)


@pytest.mark.parametrize("url", [f"https://www.youtube.com/post/{POST}", f"youtube.com/post/{POST}",
                                 f"https://www.youtube.com/channel/UChhw6DlKKTQ9mYSpTfXUYqA/community?lb={POST}",
                                 f"https://m.youtube.com/@starterstory/community?lb={POST}",
                                 f"https://www.youtube.com/@starterstory/posts?lb={POST}",
                                 f"//www.youtube.com/post/{POST}"])
def test_post_id(url):
    assert M.post_id(url) == POST


@pytest.mark.parametrize("url", [watch(ZOO), f"https://www.instagram.com/post/{POST}", "https://www.youtube.com/post/",
                                 f"https://www.youtube.com/channel/UChhw6DlKKTQ9mYSpTfXUYqA/posts/{POST}",
                                 # hosts urllib raises ValueError on: a stray bracket, a full-width '／'
                                 "[]", "http://[::1", f"https://www.youtube.com]/post/{POST}",
                                 f"https://www.youtube.com／post/{POST}"])
def test_post_id_rejects(url):
    with pytest.raises(BadRequest):
        M.post_id(url)


def test_token_target_classifies_native_tokens():
    assert P.token_target(COMMENTS_TOKEN) == (SPONSORED, "comments")
    assert P.token_target(REPLIES_TOKEN) == (SPONSORED, "replies")
    assert P.token_target(REPLIES_TOKEN.replace("%3D", "=")) == (SPONSORED, "replies")
    assert P.token_target("hello") is None
    other = base64.urlsafe_b64encode(b"\x12\r\x12\x0b" + ZOO.encode() + b"\x18\x06something-else").decode()
    assert P.token_target(other) is None


def test_native_token_checks_kind_and_video():
    assert M.native_token(COMMENTS_TOKEN, "comments", SPONSORED) == (COMMENTS_TOKEN, SPONSORED)
    assert M.native_token(REPLIES_TOKEN.replace("+", " "), "replies") == (REPLIES_TOKEN, SPONSORED)
    for args in ((COMMENTS_TOKEN, "comments", ZOO), (REPLIES_TOKEN, "comments", SPONSORED), (COMMENTS_TOKEN, "replies")):
        with pytest.raises(BadRequest) as exc:
            M.native_token(*args)
        assert exc.value.code == "bad_cursor"


def test_language_validation():
    assert M.ui_language_args("EN") == {"extractor_args": {"youtube": {"lang": ["en"]}}}
    assert M.ui_language_args(None) == {} and M.ui_language_args("xx") == {}  # no such UI: YouTube's default
    assert [M.ui_language(c) for c in ("zh", "zh-Hant", "he", "nb", "en-US", "pt-BR", "es-419", "ZH-tw")] == [
        "zh-CN", "zh-TW", "iw", "no", "en", "pt", "es-419", "zh-TW"]
    # zh-MO fell back to zh, a Simplified UI, for a region that writes Traditional
    assert [M.ui_language(c) for c in ("zh-MO", "zh-Hant-MO", "zh-Hans-MO", "zh-MY")] == [
        "zh-HK", "zh-TW", "zh-CN", "zh-CN"]
    assert M.caption_language("en-US") == "en-US"
    for bad in ("e!", "en_US!"):
        with pytest.raises(BadRequest):
            M.ui_language_args(bad)
    with pytest.raises(BadRequest):
        M.caption_language("e!")
    assert M.boolean("original_audio", "TRUE") is True and M.boolean("original_audio", None) is False
    with pytest.raises(BadRequest):
        M.boolean("original_audio", "maybe")


def test_ensure_available_raises_player_reason():
    M.ensure_available([{"videoDetails": {"videoId": ZOO}}])
    with pytest.raises(ApiError) as exc:
        M.ensure_available([{"playabilityStatus": {"status": "ERROR", "reason": "This video is unavailable"}}])
    assert exc.value.status == 404
    with pytest.raises(ApiError) as exc:
        M.ensure_available([{"playabilityStatus": {"status": "LOGIN_REQUIRED", "reason": "This video is private"}}])
    assert exc.value.status == 403


# --- offline: pure mappers ----------------------------------------------------------


def test_description_links_slice_utf16_and_decode_redirects():
    content = "😀 Go: example.com/x and 0:05 and @someone"
    start = len("😀 Go: ".encode("utf-16-le")) // 2
    data = {"contents": {"twoColumnWatchNextResults": {"results": {"results": {"contents": [{
        "videoSecondaryInfoRenderer": {"attributedDescription": {"content": content, "commandRuns": [
            {"startIndex": start, "length": 13, "onTap": {"innertubeCommand": {"urlEndpoint": {
                "url": "https://www.youtube.com/redirect?event=video_description&q=https%3A%2F%2Fexample.com%2Fx"}}}},
            {"startIndex": start + 18, "length": 4, "onTap": {"innertubeCommand": {"watchEndpoint": {"videoId": ZOO}}}},
            {"startIndex": start + 27, "length": 8, "onTap": {"innertubeCommand": {
                "browseEndpoint": {"browseId": "UCx", "canonicalBaseUrl": "/@someone"}}}},
        ]}}}]}}}}}
    assert P.description_links(data, ZOO) == [
        {"text": "example.com/x", "url": "https://example.com/x"},
        {"text": "@someone", "url": "https://www.youtube.com/@someone"},
    ]


def test_description_links_read_past_half_an_emoji():
    """json-render-500: a description with half an emoji (a lone UTF-16 surrogate, which Python's json reads from
    "\\ud83d") and a link was a 500 (UnicodeEncodeError). The half is dropped from a link's text."""
    content = json.loads('"\\ud83d Go: example.com/x \\ud83d and @someone"')
    data = nest(("contents", "twoColumnWatchNextResults", "results", "results", "contents"), [
        {"videoSecondaryInfoRenderer": {"attributedDescription": {"content": content, "commandRuns": [
            {"startIndex": 6, "length": 15, "onTap": {"innertubeCommand": {"urlEndpoint": {
                "url": "https://example.com/x"}}}},
            {"startIndex": 26, "length": 8, "onTap": {"innertubeCommand": {
                "browseEndpoint": {"browseId": "UCx", "canonicalBaseUrl": "/@someone"}}}},
        ]}}}])
    assert P.description_links(data, ZOO) == [
        {"text": "example.com/x", "url": "https://example.com/x"},
        {"text": "@someone", "url": "https://www.youtube.com/@someone"},
    ]


def test_most_replayed_and_caption_tracks():
    data = {"frameworkUpdates": {"entityBatchUpdate": {"mutations": [{"entityKey": "x"}, {"payload": {
        "macroMarkersListEntity": {"markersList": {"markerType": "MARKER_TYPE_HEATMAP", "markers": [
            {"startMillis": "0", "durationMillis": "190", "intensityScoreNormalized": 1}],
            "markersDecoration": {"timedMarkerDecorations": [{"visibleTimeRangeStartMillis": 0,
                                                              "visibleTimeRangeEndMillis": 1900,
                                                              "decorationTimeMillis": 190,
                                                              "label": {"runs": [{"text": "Most replayed"}]}}]}}}}}]}}}
    assert P.most_replayed(data) == {
        "markers": [{"startMillis": 0, "durationMillis": 190, "intensityScoreNormalized": 1.0}],
        "ranges": [{"startMillis": 0, "endMillis": 1900, "peakMillis": 190, "label": "Most replayed"}]}
    assert P.most_replayed({}) is None
    tracks = P.caption_tracks([{}, {"captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [
        {"baseUrl": "u", "name": {"runs": [{"text": "English"}]}, "vssId": ".en", "languageCode": "en",
         "isTranslatable": True, "trackName": ""}]}}}])
    assert tracks == [{"baseUrl": "u", "name": {"simpleText": "English"}, "vssId": ".en", "languageCode": "en",
                       "isTranslatable": True, "trackName": ""}]
    assert P.is_paid_promotion([{}, {"paidContentOverlay": {"paidContentOverlayRenderer": {}}}])
    assert not P.is_paid_promotion([{}])


def test_caption_tracks_skip_po_token_urls():
    """gap-youtube_videos-1: the initial web player response (no streamingData) lists caption URLs in YouTube's
    exp=xpe experiment, which serve an empty file without a PO token. Take the tracks yt-dlp downloads from."""
    def player(exp: str | None, *, streaming: bool = True) -> dict:
        url = "https://www.youtube.com/api/timedtext?v=x&caps=asr" + (f"&exp={exp}" if exp else "") + "&lang=en"
        return {**({"streamingData": {"adaptiveFormats": []}} if streaming else {}), "captions": {
            "playerCaptionsTracklistRenderer": {"captionTracks": [{"baseUrl": url, "languageCode": "en"}]}}}

    def exps(players: list[dict]) -> list[str | None]:
        return [P.parse_qs(P.urlparse(t["baseUrl"]).query).get("exp", [None])[0] for t in P.caption_tracks(players)]

    assert exps([player("xpe", streaming=False), player(None)]) == [None]
    assert exps([player("xpv"), player("xpe"), {}, player("xoaf")]) == ["xoaf"]  # another experiment is harmless
    assert exps([player("xpe", streaming=False), player("xpv")]) == ["xpe"]  # nothing better: the first one
    assert exps([player(None, streaming=False), {"streamingData": {}}]) == [None]
    for vid in (ZOO, SPONSORED):  # cached watch pages: the first player response is the web one
        players = cached(f"{vid}.players.json")
        assert any(map(P._needs_po_token, players[0]["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"]))
        tracks = P.caption_tracks(players)
        assert tracks and not any(map(P._needs_po_token, tracks)) and all(t["name"]["simpleText"] for t in tracks)


def test_video_payload_from_probe_cache(ies):
    info, initial = cached(f"{SPONSORED}.info.json"), cached(f"{SPONSORED}.initial.json")
    players = cached(f"{SPONSORED}.players.json")
    body = P.video_payload(info, initial, players, ies[1])
    assert body["id"] == SPONSORED and body["isPaidPromotion"] is True
    assert body["channel"] == {"id": "UC3vQEjRhwgH2HAOBKwLjxNA", "url": "https://www.youtube.com/@letsKWOOWK",
                               "handle": "letsKWOOWK", "title": "KWOOWK"}
    assert body["commentCountText"] and body["commentCountInt"] > 0
    assert {"text": "https://betterhelp.com/KWOOWK", "url": "https://betterhelp.com/KWOOWK"} in body["descriptionLinks"]
    assert body["durationFormatted"].count(":") == 2 and body["durationMs"] == info["duration"] * 1000
    first = body["watchNextVideos"][0]
    assert first["videoUrl"].endswith(first["id"]) and first["channel"]["id"].startswith("UC")
    assert first["lengthInSeconds"] > 0 and first["viewCountInt"] > 0
    assert_conforms({"success": True, **body}, "/v1/youtube/video", min_coverage=0.95)


def test_collaborators_from_probe_cache():
    collabs = P.collaborators(cached("brhfDfLdDZ8.initial.json"))
    assert [c["handle"] for c in collabs] == ["sauceplusofficial", "WilliamOsman2"]
    assert all(c["id"].startswith("UC") and c["url"].endswith(c["handle"]) for c in collabs)


def test_comment_pages_from_probe_cache(ies):
    ie = ies[0]
    page1, tok1 = P.map_comments(ie, cached(f"comments_{ZOO}_0.json"), replies=False)
    page2, tok2 = P.map_comments(ie, cached(f"comments_{ZOO}_1.json"), replies=False)
    assert len(page1) == 20 and tok1 and tok2 and tok1 != tok2
    assert not {c["id"] for c in page1} & {c["id"] for c in page2}
    top = page1[0]
    assert top["replyLevel"] == 0 and top["engagement"]["replies"] > 0 and top["author"]["channelId"].startswith("UC")
    assert P.token_target(top["repliesContinuationToken"]) == (ZOO, "replies")
    assert P.token_target(tok1) == (ZOO, "comments")
    newest, selected = P.sort_token(P.comments_header(P.continuation_items(cached(f"comments_{ZOO}_0.json"))), "newest")
    assert newest and not selected
    replies, rtok = P.map_comments(ie, cached(f"replies_{ZOO}_0.json"), replies=True)
    assert replies and all(r["replyLevel"] == 1 and r["id"].startswith(top["id"] + ".") for r in replies)
    assert "repliesContinuationToken" not in replies[0] and P.token_target(rtok) == (ZOO, "replies")


def test_post_payload_from_probe_cache(ies):
    post = P.find_post(cached(f"post_{POST}.json"), POST)
    body = P.post_payload(ies[1], post)
    assert body["id"] == POST and body["channel"]["handle"] == "starterstory"
    assert body["content"].startswith("How to build something meaningful")
    assert body["likeCount"] > 0 and body["images"] and all(u.startswith("https://") for u in body["images"])
    assert body["video"] is None and body["publishedTime"].endswith("Z")


def test_detect_sponsors():
    desc = ("If you're struggling, consider therapy with our sponsor BetterHelp. Click\n"
            "https://betterhelp.com/KWOOWK for a 10% discount.\n\nFOLLOW ME:\nInstagram: https://www.instagram.com/x/\n"
            "TikTok: https://www.tiktok.com/@x\n\nMy site: https://kwoowk.com")
    sponsors, methods = P.detect_sponsors(desc, "go to betterhelp.com for a discount", own={"kwoowk"},
                                          paid_promotion=True)
    assert [(s["name"], s["website"], s["confidence"]) for s in sponsors] == [("BetterHelp", "betterhelp.com", "high")]
    assert [e["source"] for e in sponsors[0]["evidence"]] == ["description_link", "transcript"]
    assert methods == ["paid promotion badge", "description link", "promo code", "transcript"]
    named, _ = P.detect_sponsors("Thanks to Squarespace for sponsoring this video!", None, own=set(),
                                 paid_promotion=False)
    assert [(s["name"], s["website"], s["confidence"]) for s in named] == [("Squarespace", None, "medium")]
    assert P.detect_sponsors("Follow me https://instagram.com/x. Merch at https://mystore.com", None, own=set(),
                             paid_promotion=False) == ([], [])


def test_transcript_payload_shapes():
    tr = subtitles.Transcript("en-orig", "English (Original)", True, "json3", "",
                              [subtitles.Segment(0.16, 1.92, "welcome back"), subtitles.Segment(75.0, 77.5, "bye")])
    body = M.transcript_payload(ZOO, {"media_type": "short"}, tr)
    assert body["language"] == "English" and body["type"] == "short" and body["transcript_only_text"] == "welcome back bye"
    assert body["transcript"][1] == {"text": "bye", "startMs": "75000", "endMs": "77500", "startTimeText": "1:15"}
    empty = M.transcript_payload(ZOO, {}, None)
    assert empty["transcript"] is None and empty["language"] is None and empty["type"] == "video"
    assert M.spoken_language({"automatic_captions": {"en-orig": [{}], "de": [{}]}}) == "en"
    assert M.spoken_language({}) is None


def test_transcript_end_ms_is_clipped_to_next_start():
    segs = [subtitles.Segment(0.0, 4.2, "a"), subtitles.Segment(2.5, 6.0, "b"), subtitles.Segment(6.5, 7.0, "c")]
    body = M.transcript_payload(ZOO, {}, subtitles.Transcript("en", "English", True, "json3", "", segs))
    assert [(c["startMs"], c["endMs"]) for c in body["transcript"]] == [("0", "2500"), ("2500", "6000"),
                                                                         ("6500", "7000")]
    assert M.segment_ends([]) == []


def test_relative_time_in_other_ui_languages():
    day = 86400
    for text, days in (("hace 2 años", 730), ("il y a 3 jours", 3), ("vor 5 Monaten", 150), ("2 年前", 730),
                       ("há 2 semanas", 14), ("5 месяцев назад", 150)):
        ts = P.relative_ts(text)
        assert ts is not None and abs((P.relative_ts("now 0 seconds") or 0) - ts - days * day) < 4 * day, text
    assert P.relative_ts("garbage") is None and P.relative_ts("") is None


def test_exact_and_rounded_counts():
    assert P.exact_count("10,639,553 Comments") == 10639553 and P.exact_count("10.639.553 comentarios") == 10639553
    assert P.exact_count("10\u00a0639\u00a0553 commentaires") == 10639553 and P.exact_count("358 Comments") == 358
    assert [P.exact_count(t) for t in ("1.6K", "1,6 mil comentarios", "12 mil", "1063万件", "", None)] == [None] * 6
    assert P.is_rounded("10M") and P.is_rounded("1,6 K") and not P.is_rounded("358") and not P.is_rounded(None)
    assert P.header_comment_count(cached(f"comments_{ZOO}_0.json")) == 10639553


def test_watch_next_channels_and_localized_labels(ies):
    collab = cached("fix/raw_eXbK8Cv7y0g.json")["initial"]
    videos = P.watch_next_videos(ies[1], collab)
    assert videos and all(v["channel"]["id"] and v["channel"]["id"].startswith("UC") and v["channel"]["url"]
                          and v["channel"]["handle"] for v in videos)
    tap = ("rendererContext", "commandContext", "onTap", "innertubeCommand")
    item = {"title": {"content": "Sauce+"}, "subtitle": {"content": "\u2068@sauceplusofficial\u2069 \u2022 1M"},
            **nest(tap, {"browseEndpoint": {"browseId": "UCabc"}})}
    stacked = {"image": {"avatarStackViewModel": nest((
        *tap, "showDialogCommand", "panelLoadingStrategy", "inlineContent", "dialogViewModel", "customContent",
        "listViewModel", "listItems"), [{"listItemViewModel": item}])}}
    assert P.lockup_channel(stacked, "Sauce+ and William Osman") == {
        "title": "Sauce+", "url": "https://www.youtube.com/@sauceplusofficial", "handle": "sauceplusofficial",
        "id": "UCabc"}
    assert P.lockup_channel({}, "Someone") == {"title": "Someone", "url": None, "handle": None, "id": None}
    spanish = cached("fix/raw_jNQXAC9IVRw_es.json")["initial"]
    first = P.watch_next_videos(ies[1], spanish)[0]
    assert "visualizaciones" in first["viewCountText"] and first["publishedTimeText"].startswith("hace")
    assert first["publishedTime"] and first["publishedTime"].endswith("Z")
    english = P.watch_next_videos(ies[1], cached(f"{SPONSORED}.initial.json"))[0]
    assert english["viewCountText"].endswith(" views")


def test_mappers_read_a_field_of_another_type_as_missing(ies):
    """yt-videos-field-type-500: a field of another type than the one YouTube sends (an object or a number for a
    string, a string for a list or an object, a string for an image size) was a 500 (TypeError or AttributeError)
    in the mappers of comments, community posts and watch-next videos. It reads as missing."""
    assert P.text_of({"runs": [{"text": {"x": 1}}, {"text": "2 weeks ago"}]}) == "2 weeks ago"
    assert P.handle_of({"x": 1}) is None and P.abs_url({"x": 1}) is None
    assert P.iso_ts({"x": 1}) is None and P.iso_ts(5) is None
    assert P.channel_block("UCx", "T", {"x": 1}) == {"id": "UCx", "url": "https://www.youtube.com/channel/UCx",
                                                   "handle": None, "title": "T"}
    assert P.largest(7) is None
    assert P.largest([{"url": {"x": 1}, "width": 900, "height": 900}, {"url": "//x/a.jpg", "width": "9", "height": 9},
                      {"url": "//x/b.jpg", "width": 2, "height": 2}]) == "https://x/b.jpg"
    assert P.sort_token({"sortMenu": {"sortFilterSubMenuRenderer": {"subMenuItems": ["x", "y"]}}}, "newest") == (
        None, False)
    tap = ("rendererContext", "commandContext", "onTap", "innertubeCommand")
    avatar = {"image": {"decoratedAvatarViewModel": nest(tap, {"browseEndpoint": "x"})}}
    assert P.lockup_channel(avatar, "Someone") == {"title": "Someone", "url": None, "handle": None, "id": None}
    item = {"title": {"content": "Sauce+"}, "subtitle": {"content": "\u2068@sauceplusofficial\u2069 \u2022 1M"},
            **nest(tap, {"browseEndpoint": "x"})}
    stacked = {"image": {"avatarStackViewModel": nest((
        *tap, "showDialogCommand", "panelLoadingStrategy", "inlineContent", "dialogViewModel", "customContent",
        "listViewModel", "listItems"), [{"listItemViewModel": item}])}}
    assert P.lockup_channel(stacked, "Sauce+ and William Osman") == {
        "title": "Sauce+", "url": "https://www.youtube.com/@sauceplusofficial", "handle": "sauceplusofficial",
        "id": None}
    view_model = ("commentThreadRenderer", "commentViewModel", "commentViewModel")
    for item, entities in [({"commentThreadRenderer": "x"}, []), ({"commentThreadRenderer": {"comment": "x"}}, []),
                           (nest(view_model, {"commentKey": {"x": 1}}), []),
                           ({"commentViewModel": {"commentKey": "K"}}, [{"entityKey": {"x": 1}}])]:
        assert P.map_comment(ies[0], item, entities, level=0, with_replies_token=True) is None, item


def test_watch_page_reads_a_field_of_another_type_as_missing(ies):
    """yt-watch-field-type-500: on a watch page, a description's text, link list or link length, a heatmap's marker
    list, a caption track's URL or a related video's thumbnail source of another type than YouTube sends was a 500
    (TypeError or AttributeError). It reads as missing."""
    link = {"startIndex": 0, "length": 4, "onTap": {"innertubeCommand": {"urlEndpoint": {"url": "https://x.com/"}}}}

    def described(**description):
        return nest(("contents", "twoColumnWatchNextResults", "results", "results", "contents"),
                    [{"videoSecondaryInfoRenderer": {"attributedDescription": description}}])

    assert P.description_links(described(content="Shop here", commandRuns=[link]), ZOO) == [
        {"text": "Shop", "url": "https://x.com/"}]
    assert P.description_links(described(content={"x": 1}, commandRuns=[link]), ZOO) == [
        {"text": "", "url": "https://x.com/"}]
    assert P.description_links(described(content="Shop here", commandRuns=[{**link, "length": "4"}]), ZOO) == [
        {"text": "", "url": "https://x.com/"}]
    assert P.description_links(described(content="Shop here", commandRuns=7), ZOO) == []
    heatmap = nest(("frameworkUpdates", "entityBatchUpdate", "mutations"), [nest(
        ("payload", "macroMarkersListEntity", "markersList"), {"markerType": "MARKER_TYPE_HEATMAP", "markers": 7})])
    assert P.most_replayed(heatmap) is None
    track = {"baseUrl": "https://www.youtube.com/api/timedtext?v=x&lang=de", "languageCode": "de"}
    tracks = ("captions", "playerCaptionsTracklistRenderer", "captionTracks")
    odd = {"baseUrl": {"x": 1}, "languageCode": "en"}
    responses = [{"streamingData": {"formats": []}, **nest(tracks, [odd, track])}]
    assert P.caption_tracks(responses) == [{**track, "name": {"simpleText": None}}]
    image = {"sources": ["x", {"url": "https://i.ytimg.com/vi/x/hq.jpg", "width": 168, "height": 94}]}
    lockup = {"contentType": "LOCKUP_CONTENT_TYPE_VIDEO", "contentId": "dQw4w9WgXcQ",
              "contentImage": {"thumbnailViewModel": {"image": image}}}
    video = P.watch_next_video(ies[1], lockup)
    assert video["id"] == "dQw4w9WgXcQ" and video["thumbnail"] == "https://i.ytimg.com/vi/x/hq.jpg"
    assert lockup["contentImage"]["thumbnailViewModel"]["image"] is image and len(image["sources"]) == 2


def test_video_payload_empty_description_is_null(ies):
    info = {**cached(f"{ZOO}.info.json"), "description": ""}
    body = P.video_payload(info, cached(f"{ZOO}.initial.json"), cached(f"{ZOO}.players.json"), ies[1],
                           comment_count=10639553)
    assert body["description"] is None and body["commentCountInt"] == 10639553 and body["commentCountText"] == "10M"


def test_age_restriction_detection():
    raw = cached("fix/raw_HtVdAasjOgU.json")
    assert M.is_age_restricted(raw["players"])
    assert not M.is_age_restricted(cached(f"{ZOO}.players.json"))
    warning = {"playabilityStatus": {"status": "CONTENT_CHECK_REQUIRED",
                                     "reason": "The following content may be inappropriate for some users"}}
    assert not M.is_age_restricted([warning, {}])


def test_post_payload_exact_from_microformat(ies):
    data = cached(f"post_{POST}.json")
    body = P.post_payload(ies[1], P.find_post(data, POST), data)
    assert body["likeCount"] == 782 and body["publishedTime"] == "2025-11-07T15:00:01.184Z"
    assert P.post_microformat(data, "UgkxSomeOtherPost") == {}  # e.g. the original of a shared post


def test_detect_sponsors_ignores_lowercase_code_and_own_links():
    desc = cached("fix/raw_rfscVS0vtbw.json")["info"]["description"]
    sponsors, methods = P.detect_sponsors(desc, None, own={"freecodecamporg", "freecodecamp"}, paid_promotion=False)
    assert [(s["name"], s["website"]) for s in sponsors] == [("Scrimba", "scrimba.com")]
    assert methods == ["description link"]
    assert P.detect_sponsors("Learn to code for free: https://www.freecodecamp.org", None, own={"freecodecamp"},
                             paid_promotion=False) == ([], [])
    assert P.detect_sponsors("This channel is supported by Patreon members like you.", None, own=set(),
                             paid_promotion=False) == ([], [])
    coded, methods = P.detect_sponsors("Use code SAVE20 at https://nordvpn.com/x", None, own=set(), paid_promotion=False)
    assert coded[0]["website"] == "nordvpn.com" and methods == ["description link", "promo code"]


def test_detect_sponsors_transcript_evidence_after_dotted_capital_i():
    """'İ'.lower() is two characters, so the mention's offset in the lower-cased transcript pointed past it in the
    transcript: after enough of them (Turkish captions) the evidence snippet no longer showed the brand."""
    transcript = " ".join(["İyi"] * 150) + " a word from our sponsor betterhelp " + " ".join(["tamam"] * 60)
    sponsors, _ = P.detect_sponsors("Sponsored by BetterHelp: https://betterhelp.com/x", transcript, own=set(),
                                    paid_promotion=False)
    [said] = [e["text"] for e in sponsors[0]["evidence"] if e["source"] == "transcript"]
    assert "sponsor betterhelp" in said and sponsors[0]["confidence"] == "high"


def test_detect_sponsors_survives_hosts_urllib_rejects():
    """A link whose host urllib raises ValueError on is no site, not a 500 from /v1/youtube/video/sponsors: the
    link pattern stops at ']' ('http://[::1'), and NFKC turns a full-width '：' typed after a URL into ':'."""
    assert P.site_of("http://[::1") is None and P.site_of("https://shop.example.jp：詳細はこちら") is None
    for junk in ("Docs: http://[::1]:8080/docs", "詳しくは https://shop.example.jp：詳細はこちら"):
        sponsors, methods = P.detect_sponsors(f"Use code SAVE20 at https://nordvpn.com/x. {junk}", None, own=set(),
                                              paid_promotion=False)
        assert [s["website"] for s in sponsors] == ["nordvpn.com"] and methods == ["description link", "promo code"]


def test_detect_sponsors_skips_redirect_hosts():
    """gap-youtube_videos-3 (LTT): the channel's own redirect domain in its boilerplate link list isn't a sponsor,
    and a sponsor named before the link it hides behind is."""
    boilerplate = ("► SHOP LTT PRODUCTS: https://lttstore.com\n► GET EXCLUSIVE CONTENT ON FLOATPLANE: "
                   "https://lmg.gg/lttfloatplane\n► DIVE DEEPER ON THE LTT LABS WEBSITE: https://lmg.gg/labs\n"
                   "► SPONSORS, AFFILIATES, AND PARTNERS: https://lmg.gg/partners")
    own = {"linustechtips"}
    assert P.detect_sponsors(boilerplate, None, own=own, paid_promotion=False) == ([], [])
    amd = "Thanks to AMD for being a great partner and sponsoring this series! See https://lmg.gg/f6PMg\n\n"
    sponsors, methods = P.detect_sponsors(amd + boilerplate, "amd sent us this", own=own, paid_promotion=False)
    assert [(s["name"], s["website"], s["confidence"]) for s in sponsors] == [("AMD", None, "high")]
    assert methods == ["description", "transcript"]
    ridge = "Sponsored by Ridge: https://ridge.com/a https://ridge.com/b https://ridge.com/c"
    assert [s["website"] for s in P.detect_sponsors(ridge, None, own=own, paid_promotion=False)[0]] == ["ridge.com"]
    keeb = "\n".join(f"Use code DAVE for 10% off {p}: https://keeb.io/{p}" for p in ("boards", "switches", "caps"))
    assert [s["website"] for s in P.detect_sponsors(keeb, None, own={"dave"}, paid_promotion=True)[0]] == ["keeb.io"]
    assert P.detect_sponsors("Thanks to everyone for watching and sponsoring us on Patreon", None, own=set(),
                             paid_promotion=False) == ([], [])


def test_translated_track_throttle_yields_no_transcript(monkeypatch):
    info = {"language": "es", "automatic_captions": {
        "es-orig": [{"url": "https://www.youtube.com/api/timedtext?lang=es&fmt=json3", "ext": "json3"}],
        "en": [{"url": "https://www.youtube.com/api/timedtext?lang=es&tlang=en&fmt=json3", "ext": "json3"}]}}

    def throttled(*_):
        raise DownloadError("ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(subtitles, "_download_track", throttled)
    assert M.is_translated(info, "en", True) and not M.is_translated(info, "es-orig", True)
    assert M.download_transcript(None, info, language="en") is None
    with pytest.raises(DownloadError):
        M.download_transcript(None, info, original=True)  # a real track: the 429 is reported


def ui_tab(lang: str) -> YoutubeTabIE:
    """A YoutubeTab extractor asking YouTube for UI language `lang`, as the routes' language= does."""
    return yt_dlp.YoutubeDL({"quiet": True, **M.ui_language_args(lang)}).get_info_extractor("YoutubeTab")


def lockup(views: str, views_label: str, ago: str, length: str) -> dict:
    """A watch-next lockupViewModel as YouTube renders it in a localized UI."""
    stats = [{"text": {"content": views}, "accessibilityLabel": views_label},
             {"text": {"content": ago}, "accessibilityLabel": ago}]
    badge = {"thumbnailBottomOverlayViewModel": {"badges": [{"thumbnailBadgeViewModel": {"text": length}}]}}
    return {"contentId": "abcdefghijk", "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "contentImage": {"thumbnailViewModel": {"overlays": [badge]}},
            "metadata": {"lockupMetadataViewModel": {"title": {"content": "A video"}, "metadata": {
                "contentMetadataViewModel": {"metadataRows": [{"metadataParts": [{"text": {"content": "A channel"}}]},
                                                              {"metadataParts": stats}]}}}}}


@pytest.mark.parametrize("text", ["댓글 10,641,372개", "1,06,41,372 टिप्पणियां", "ความคิดเห็น 10,641,372 รายการ",
                                  "1,06,41,372টি মন্তব্য", "Maoni 10,641,372"])
def test_exact_comment_count_in_other_scripts(text):
    assert P.exact_count(text) == 10641372


def test_short_counts_in_other_ui_languages():
    rounded = ("1064만", "1 क॰", "10 ล้าน", "4.6 lakh", "댓글 1.2만개", "elfu 19", "මි10")
    assert [P.exact_count(t) for t in rounded] == [None] * len(rounded)
    for lang, text, count in (
            ("ja", "46万", 460000), ("ko", "1064만", 10640000), ("zh-CN", "3.2亿", 320000000),
            ("id", "459 rb", 459000), ("id", "5,5 jt", 5500000), ("tr", "766 B", 766000), ("en", "1.2B", 1200000000),
            ("cs", "3,5 mil.", 3500000), ("pt", "461 mil", 461000), ("hi", "1 क॰", 10000000),
            ("hi", "4.6 lakh", 460000), ("iw", "\u202b11M\u200f\u202c", 11000000), ("sw", "elfu 19", 19000),
            ("de", "43.428", 43428), ("fr", "461\u202f030", 461030), ("en", "12 zz", None), ("en", "", None)):
        assert P.compact_count(text, lang) == count, (lang, text)


@pytest.mark.parametrize("lang,text", [
    ("pt", "2,5\u00a0bi"), ("af", "2,5\u00a0mjd"), ("da", "2,5\u00a0mia."), ("fr-CA", "2,5\u00a0G"),
    ("pt-PT", "2,5\u00a0mM"), ("hr", "2,5\u00a0mlr."), ("is", "2,5\u00a0ma."), ("lv", "2,5\u00a0mljrd."),
    ("tr", "2,5\u00a0Mr"), ("el", "2,5\u00a0δισ."), ("mk", "2,5\u00a0милј."), ("ky", "2,5\u00a0млд"),
    ("mn", "2.5\u00a0тэрбум"), ("hy", "2,5\u00a0մլրդ"), ("ka", "2,5\u00a0მლრდ."), ("ar", "2.5\u00a0مليار"),
    ("fa", "2.5\u00a0میلیارد"), ("ur", "2.5\u00a0ارب"), ("hi", "2.5\u00a0अ॰"), ("ne", "2.5\u00a0अरब"),
    ("mr", "2.5\u00a0अब्ज"), ("pa", "2.5\u00a0ਅਰਬ"), ("gu", "2.5\u00a0અબજ"), ("as", "2.5\u00a0শঃ\u00a0কোঃ"),
    ("or", "2.5ବି"), ("si", "බි2.5"), ("th", "2.5 พันล้าน"), ("lo", "2,5\u00a0ຕື້"), ("km", "2.5\u00a0ប៊ីលាន"),
    ("my", "250\u00a0ဋေ"), ("am", "2.5\u00a0ቢ"),
])
def test_billion_short_counts(lang, text):
    # each UI language's own abbreviation for 2.5 billion views (Burmese: 250 crore), as YouTube renders it
    assert P.compact_count(text, lang) == 2500000000
    assert P.exact_count(text) is None


def test_plural_short_counts():
    # retest-youtube_videos-4: Assamese নিঃ is 10^8 (CLDR as: 257000000 -> '২.৬ নিঃ'), নিযুত a million
    for lang, text, count in (("ar", "9 آلاف", 9000), ("ar", "3 ملايين", 3000000), ("ar", "3 مليارات", 3000000000),
                              ("ar", "11 ألف", 11000), ("as", "2.5 নিঃ", 250000000), ("as", "2.5 নিযুত", 2500000)):
        assert P.compact_count(text, lang) == count, (lang, text)
    assert P.compact_count("2.5 শঃ কোঃ ভিউ", "as") is None  # a third word: not a short count


def test_video_payload_comment_count_in_other_ui_languages():
    info = {"id": "abcdefghijk", "comment_count": 1064}  # yt-dlp read '1064만' as English
    panel = {"engagementPanelSectionListRenderer": {
        "panelIdentifier": "engagement-panel-comments-section",
        "header": {"engagementPanelTitleHeaderRenderer": {"contextualInfo": {"runs": [{"text": "1064만"}]}}}}}
    body = P.video_payload(info, {"engagementPanels": [panel]}, [], ui_tab("ko"))
    assert body["commentCountText"] == "1064만" and body["commentCountInt"] == 10640000
    panel["engagementPanelSectionListRenderer"]["header"]["engagementPanelTitleHeaderRenderer"]["contextualInfo"] = {
        "runs": [{"text": "1064백"}]}  # an abbreviation we don't know: no count beats a wrong one
    assert P.video_payload(info, {"engagementPanels": [panel]}, [], ui_tab("ko"))["commentCountInt"] is None
    assert P.video_payload(info, {"engagementPanels": [panel]}, [], ui_tab("en"))["commentCountInt"] == 1064


def like_button_page(label: str, title: str = "56\u00a0M", entity: dict | None = None) -> dict:
    """A watch page's ytInitialData with just the like button, labelled as in a localized UI."""
    button = nest(("segmentedLikeDislikeButtonViewModel", "likeButtonViewModel", "likeButtonViewModel",
                   "toggleButtonViewModel", "toggleButtonViewModel", "defaultButtonViewModel", "buttonViewModel"),
                  {"accessibilityText": label, "title": title})
    if entity is not None:
        button["segmentedLikeDislikeButtonViewModel"]["likeCountEntity"] = entity
    primary = {"videoPrimaryInfoRenderer": {"videoActions": {"menuRenderer": {"topLevelButtons": [button]}}}}
    return nest(("contents", "twoColumnWatchNextResults", "results", "results"), {"contents": [primary]})


@pytest.mark.parametrize("lang,label,yt_dlp_count", [
    ("fr", "J'aime cette vidéo, comme 56\u202f758\u202f781\u00a0autres personnes avant moi", 56),
    ("ru", 'поставить отметку "Нравится" (это видео понравилось 56\u00a0758\u00a0781 пользователям)', 56),
    ("af", "duim hierdie video op tesame met 56\u00a0758\u00a0781 ander mense", 56),
    ("ko", "다른 사용자 56,758,781명과 함께 이 동영상에 좋아요 표시", None),
    ("am", "ይህን ቪድዮ ከ56,758,781 ሌሎች ሰዎች ጋር ውደድ", None),
    ("si", "මෙම වීඩියෝවට තව පුද්ගලයින් 56,758,781 දෙනෙක් සමගින් කැමතියි", 56758781),  # 'දෙනෙක්' isn't 'ද' (thousand)
    ("ta", "மேலும் 5,67,58,781 நபர்களுடன் சேர்ந்து இந்த வீடியோவிற்கு விருப்பம் தெரிவித்துள்ளீர்கள்", 56758781),
    ("de", "Dieses Video liken (bisher 56.758.781\u00a0positive Bewertungen)", 56758781),
    ("en", "like this video along with 56,758,781 other people", 56758781),
])
def test_video_payload_like_count_in_other_ui_languages(lang, label, yt_dlp_count):
    # yt-dlp reads the like label as English, stopping at a space separator ('56 758 781' -> 56)
    body = P.video_payload({"id": "abcdefghijk", "like_count": yt_dlp_count}, like_button_page(label), [], ui_tab(lang))
    assert body["likeCountInt"] == 56758781 and body["likeCountText"] == "56758781", lang


def test_video_payload_like_count_without_a_count_in_the_label():
    hidden = like_button_page("I like this")  # likes hidden by the uploader
    assert P.video_payload({"id": "abcdefghijk"}, hidden, [], ui_tab("en"))["likeCountInt"] is None
    assert P.video_payload({"id": "abcdefghijk", "like_count": 7}, {}, [], ui_tab("en"))["likeCountInt"] == 7
    # a non-English label we can't read: no count beats yt-dlp's English misreading
    assert P.video_payload({"id": "abcdefghijk", "like_count": 56}, {}, [], ui_tab("fr"))["likeCountInt"] is None


def test_video_payload_zero_likes():
    # 6n1d992ODAU: nobody likes it, so its label has no number, but the button shows 0; its like count entity
    # says 0 too, or is unset (two fetches of the same page)
    zero = {"likeCountIfIndifferentNumber": "0", "likeCountIfLikedNumber": "0", "likeCountIfIndifferent": {
        "content": "0"}}
    unset = {"key": "unset_like_count_entity_key"}
    for entity in (zero, unset, None):
        page = like_button_page("like this video", "0", entity)
        body = P.video_payload({"id": "6n1d992ODAU"}, page, [], ui_tab("en"))
        assert body["likeCountInt"] == 0 and body["likeCountText"] == "0"
    assert P.video_payload({"id": "6n1d992ODAU"}, like_button_page("J'aime cette vidéo", "0", zero), [],
                           ui_tab("fr"))["likeCountInt"] == 0
    # ratings off (KDqkFCM0CQ0) or likes hidden: the button says 'Like' and the entity has no number
    for page in (like_button_page("Like", "Like", unset), like_button_page("like this video", "Like"),
                 like_button_page("like this video", "Like", zero)):
        assert P.video_payload({"id": "KDqkFCM0CQ0"}, page, [], ui_tab("en"))["likeCountInt"] is None
    # a button showing 0 next to an entity with a count is no count we can trust
    page = like_button_page("like this video", "0", {"likeCountIfIndifferentNumber": "3"})
    assert P.video_payload({"id": "6n1d992ODAU"}, page, [], ui_tab("en"))["likeCountInt"] is None


def test_video_payload_live_stream_title():
    # yt-dlp appends the extraction time to a live stream's title; fulltitle is the title as shown
    info = {"id": "jfKfPfyJRdk", "title": "lofi hip hop radio \U0001f4da beats to relax/study to 2026-10-19 14:05",
            "fulltitle": "lofi hip hop radio \U0001f4da beats to relax/study to", "live_status": "is_live"}
    assert P.video_payload(info, {}, [], ui_tab("en"))["title"] == "lofi hip hop radio \U0001f4da beats to relax/study to"
    assert P.video_payload({"id": "abcdefghijk", "title": "Old"}, {}, [], ui_tab("en"))["title"] == "Old"


def owner_page(path: str, *, in_title: bool = False) -> dict:
    """A watch page whose videoOwnerRenderer links the channel at path, from its title run when in_title."""
    endpoint = {"browseEndpoint": {"browseId": "UCx", "canonicalBaseUrl": path}}
    owner = {"title": {"runs": [{"text": "A channel", "navigationEndpoint": endpoint}]}} if in_title else {
        "navigationEndpoint": endpoint}
    return {"contents": {"twoColumnWatchNextResults": {"results": {"results": {"contents": [
        {"videoSecondaryInfoRenderer": {"owner": {"videoOwnerRenderer": owner}}}]}}}}}


def owner_profile(url: str) -> list[dict]:
    """Player responses whose microformat links the channel at url."""
    return [{}, {"microformat": {"playerMicroformatRenderer": {"ownerProfileUrl": url}}}]


def test_video_channel_handle_with_combining_marks():
    """yt-dlp's uploader_id stops at a handle's first middle dot or combining mark (the vowel signs of Thai,
    Bengali, Malayalam...): 'TVമലയാളം' came out 'TVമലയ', and 'ช่องทําอาหาร', with a mark in its first three
    characters, as no handle. The handle is read from the owner links yt-dlp reads it from."""
    info = {"id": "abcdefghijk", "channel_id": "UCx", "channel": "TV മലയാളം", "uploader_id": "@TVമലയ",
            "channel_url": "https://www.youtube.com/channel/UCx"}
    players = owner_profile("http://www.youtube.com/@" + quote("TVമലയാളം"))
    assert P.video_payload(info, {}, players, ui_tab("en"))["channel"] == {
        "id": "UCx", "url": "https://www.youtube.com/@TVമലയാളം", "handle": "TVമലയാളം", "title": "TV മലയാളം"}
    no_id = {**info, "uploader_id": None}
    for page in (owner_page("/@" + quote("ช่องทําอาหาร")), owner_page("/@" + quote("ช่องทําอาหาร"), in_title=True)):
        assert P.video_channel(no_id, page, [{}])["handle"] == "ช่องทําอาหาร"
    assert P.video_channel(no_id, {}, owner_profile("http://www.youtube.com/@l%C2%B7l"))["handle"] == "l·l"
    # no link names a handle: yt-dlp's uploader_id, else the channel URL
    no_handle = owner_page("/channel/UCx"), owner_profile("http://www.youtube.com/channel/UCx")
    assert P.video_channel({**info, "uploader_id": "@tvmalayalam"}, *no_handle)["handle"] == "tvmalayalam"
    assert P.video_channel(no_id, *no_handle) == {
        "id": "UCx", "url": "https://www.youtube.com/channel/UCx", "handle": None, "title": "TV മലയാളം"}


def test_collaborators_handle_with_combining_marks():
    """A collab entry's handle comes from its subtitle, which was read only as far as \\w goes: 'TVമലയ' for
    'TVമലയാളം', 'ช' for 'ช่องทําอาหาร'."""
    def entry(title: str, handle: str, channel_id: str) -> dict:
        browse = {"browseId": channel_id, "canonicalBaseUrl": f"/channel/{channel_id}"}
        return {"listItemViewModel": {
            "title": {"content": title, "commandRuns": [{"onTap": {"innertubeCommand": {"browseEndpoint": browse}}}]},
            "subtitle": {"content": f"\u200e\u2068@{handle}\u2069 • \u2068790 subscribers\u2069"}}}

    items = [entry("TV മലയാളം", "TVമലയാളം", "UCa"), entry("ช่องทำอาหาร", "ช่องทําอาหาร", "UCb"),
             entry("l·l", "l·l", "UCc")]
    dialog = {"customContent": {"listViewModel": {"listItems": items}}}
    owner = {"attributedTitle": {"commandRuns": [{"onTap": {"innertubeCommand": {"showDialogCommand": {
        "panelLoadingStrategy": {"inlineContent": {"dialogViewModel": dialog}}}}}}]}}
    page = {"contents": {"twoColumnWatchNextResults": {"results": {"results": {"contents": [
        {"videoSecondaryInfoRenderer": {"owner": {"videoOwnerRenderer": owner}}}]}}}}}
    assert [(c["id"], c["handle"], c["url"]) for c in P.collaborators(page)] == [
        ("UCa", "TVമലയാളം", "https://www.youtube.com/@TVമലയാളം"),
        ("UCb", "ช่องทําอาหาร", "https://www.youtube.com/@ช่องทําอาหาร"),
        ("UCc", "l·l", "https://www.youtube.com/@l·l")]


def test_watch_next_videos_in_other_ui_languages():
    indonesian = P.watch_next_video(ui_tab("id"), lockup("5,5 jt", "5,5 jt x ditonton", "3 minggu yang lalu", "18.32"))
    assert indonesian["viewCountInt"] == 5500000 and indonesian["lengthText"] == "18:32"
    assert indonesian["lengthInSeconds"] == 1112 and indonesian["publishedTime"]
    for lang, views, count in (("ja", "46万", 460000), ("ko", "1064만", 10640000), ("zh-TW", "46萬", 460000),
                               ("iw", "\u202b11M\u200f\u202c", 11000000), ("hi", "4.6 lakh", 460000)):
        video = P.watch_next_video(ui_tab(lang), lockup(views, views, "", "1:02:03"))
        assert video["viewCountInt"] == count and video["lengthInSeconds"] == 3723, lang
    dutch = P.watch_next_video(ui_tab("nl"), lockup("2,1 mln.", "2,1 mln. weergaven", "2 weken geleden", "4:05"))
    assert dutch["viewCountInt"] == 2100000 and dutch["publishedTime"]
    portuguese = P.watch_next_video(ui_tab("pt"), lockup("3,9\u00a0bi", "3,9 bi de visualizações", "há 12 anos",
                                                          "4:42"))
    assert portuguese["viewCountInt"] == 3900000000 and portuguese["publishedTime"]
    arabic = P.watch_next_video(ui_tab("ar"), lockup("9\u00a0آلاف", "9 آلاف مشاهدة", "قبل 3 أسابيع", "4:42"))
    assert arabic["viewCountInt"] == 9000 and arabic["publishedTime"]
    amharic = P.watch_next_video(ui_tab("am"), lockup("6\u00a0ቢ", "6 ቢሊዮን ዕይታዎች", "ከ2 ሳምንታት በፊት", "4:42"))
    assert amharic["viewCountInt"] == 6000000000 and amharic["publishedTime"]


def test_watch_next_video_years_long_stream():
    # a years-long stream's hours are digit-grouped (LofiGirl's 5qap5aO4i9A), and Danish puts '.' between the units
    for lang, badge, shown in (("en", "20,772:51:34", "20,772:51:34"), ("da", "20.772.51.34", "20.772:51:34")):
        video = P.watch_next_video(ui_tab(lang), lockup("5", "5 views", "", badge))
        assert (video["lengthText"], video["lengthInSeconds"]) == (shown, 74782294), lang
    for junk in ("1,234", "20,77:51:34", "1,234:56", "12.345", "1:02:03:04", "LIVE", "", None):
        assert P.badge_length(junk) == (None, None), junk


def test_counts_too_big_to_hold_read_as_missing(ies):
    """A count past the 4300 digits int() reads or past a float's range ('999…9K views') was a 500 (ValueError,
    OverflowError) in a comment count, a related video's views, a comment's likes and a post's likes. It reads as
    missing; a related video or a comment that yt-dlp can't read for one is skipped."""
    assert P.exact_count("9" * 5000 + " Comments") is None and P.compact_count("9" * 5000) is None
    assert P.compact_count("9" * 400 + "K") is None and P.compact_count("9" * 400) == int("9" * 400)
    video = P.watch_next_video(ui_tab("en"), lockup("9" * 5000, "9" * 5000 + " views", "", "4:05"))
    assert video["viewCountInt"] is None and video["lengthInSeconds"] == 245
    big = "9" * 400 + "K"  # yt-dlp's own reading of it overflows too: the related video is skipped
    assert P.watch_next_video(ui_tab("en"), lockup(big, f"{big} views", "", "4:05")) is None
    liked = {"interactionType": {"type": "LikeAction"}, "userInteractionCount": float("inf")}
    assert P.post_likes({}, {"interactionStatistic": [liked]}) is None
    assert P.post_likes({"voteCount": {"simpleText": big}}, {}) is None
    label = {"accessibilityData": {"accessibilityData": {"label": f"Like along with {'9' * 5000} other people"}}}
    button = {"commentActionButtonsRenderer": {"likeButton": {"toggleButtonRenderer": label}}}
    assert P.post_likes({"actionButtons": button, "voteCount": {"simpleText": "1.2K"}}, {}) == 1200
    for likes, age in (("1.2K", "2 days ago"), (big, "2 days ago"), ("5", f"{'9' * 400} years ago")):
        payload = {"properties": {"commentId": "UgxA", "content": {"content": "Hi"}, "publishedTime": age},
                   "toolbar": {"likeCountA11y": f"{likes} likes"}}
        entities = [{"entityKey": "K", "payload": {"commentEntityPayload": payload}}]
        reply = P.map_comment(ies[0], {"commentViewModel": {"commentKey": "K"}}, entities, level=1,
                              with_replies_token=False)
        renderer = {"commentRenderer": {"commentId": "UgxB", "voteCount": {"simpleText": likes},
                                        "publishedTimeText": {"simpleText": age}}}
        old_reply = P.map_comment(ies[0], renderer, [], level=1, with_replies_token=False)  # the older shape
        if likes == "1.2K":
            assert reply["engagement"]["likes"] == old_reply["engagement"]["likes"] == 1200
        else:
            assert reply is None and old_reply is None, (likes[:5], age[:5])


def test_lengths_too_big_to_hold_read_as_missing(ies):
    """A length or a time past a float's range was a 500 (OverflowError) in a related video's length, the video's
    length, a chapter, a most-replayed marker and a transcript cue. It reads as missing."""
    hours = "9" * 400 + ":51:34"
    assert P.badge_length(hours) == (None, None)
    video = P.watch_next_video(ui_tab("en"), lockup("5", "5 views", "", hours))
    assert (video["lengthText"], video["lengthInSeconds"]) == (None, None) and video["viewCountInt"] == 5
    assert P.millis(1.5) == 1500 and P.millis(10 ** 400) == 10 ** 403 and P.millis(None) is None
    info = {"id": "abcdefghijk", "duration": 1.7e308, "chapters": [
        {"title": "a", "start_time": 1.5, "end_time": float("inf")}, {"title": "b", "start_time": float("nan")}]}
    body = P.video_payload(info, {}, [], ies[1])
    assert body["durationMs"] is None and body["chapters"] == [
        {"title": "a", "startMs": 1500, "endMs": None, "startTimeText": "0:01"},
        {"title": "b", "startMs": None, "endMs": None, "startTimeText": None}]
    markers = [{"startMillis": "0", "durationMillis": "190", "intensityScoreNormalized": 10 ** 400},
               {"startMillis": "190", "durationMillis": "190", "intensityScoreNormalized": 0.5}]
    heatmap = {"frameworkUpdates": {"entityBatchUpdate": {"mutations": [{"payload": {"macroMarkersListEntity": {
        "markersList": {"markerType": "MARKER_TYPE_HEATMAP", "markers": markers}}}}]}}}
    assert P.most_replayed(heatmap)["markers"] == [
        {"startMillis": 190, "durationMillis": 190, "intensityScoreNormalized": 0.5}]
    cues = subtitles.parse(f'<text start="1.5" dur="2.25">hi</text><text start="{"9" * 308}" dur="1">far</text>',
                           "srv1")  # 1e308 s: a float, but not in ms
    body = M.transcript_payload(ZOO, {}, subtitles.Transcript("en", "English", True, "srv1", "", cues))
    assert [(c["text"], c["startMs"], c["endMs"]) for c in body["transcript"]] == [("hi", "1500", "3750"),
                                                                                   ("far", None, None)]


def test_relative_time_words_of_more_ui_languages():
    now, day = P.relative_ts("now 0 seconds"), 86400
    for lang, text, seconds in (
            ("nl", "2 weken geleden", 14 * day), ("id", "3 minggu yang lalu", 21 * day),
            ("id", "5 hari yang lalu", 5 * day), ("hi", "5 दिन पहले", 5 * day), ("th", "5 วันที่ผ่านมา", 5 * day),
            ("iw", "לפני יום אחד (1)", day), ("iw", "לפני יומיים (2)", 2 * day), ("sw", "saa 5 zilizopita", 5 * 3600),
            ("uk", "8 годин тому", 8 * 3600), ("bg", "преди 3 години", 3 * 365 * day), ("ms", "5 saat lalu", 5),
            ("tr", "8 saat önce", 8 * 3600), ("iw", "לפני שנה", 365 * day), ("iw", "לפני שבועיים", 14 * day),
            ("ar", "قبل يوم واحد", day), ("ar", "قبل شهرين", 60 * day), ("am", "ከ2 ሳምንታት በፊት", 14 * day),
            ("am", "ከ1 ሳምንት በፊት", 7 * day), ("az", "5 dəqiqə öncə", 300), ("uz", "5 daqiqa oldin", 300),
            ("uz", "5 soniya oldin", 5), ("ky", "5 мүнөт мурун", 300), ("ne", "5 मिनेट पहिले", 300),
            ("ne", "५ सेकेन्ड पहिले", 5), ("mr", "5 मिनिटांपूर्वी", 300), ("mr", "5 सेकंदापूर्वी", 5),
            ("as", "5 ছেকেণ্ড পূৰ্বে", 5), ("gu", "5 સેકંડ પહેલાં", 5), ("ta", "5 விநாடிகளுக்கு முன்", 5),
            ("is", "fyrir 1 degi", day), ("lt", "prieš 11 savaičių", 77 * day), ("ro", "acum 20 de minute", 1200),
            ("ro", "acum 21 de ani", 21 * 365 * day), ("zu", "8 iminyaka edlule", 8 * 365 * day),
            ("zu", "Kusakazwe 1 unyaka odlule", 365 * day), ("zu", "6 izinyanga ezedlule", 180 * day),
            ("zu", "amaviki angu-2 edlule", 14 * day), ("zu", "ezinsukwini ezingu-8 ezedlule.", 8 * day),
            ("zu", "emahoreni angu-5 edlule", 5 * 3600), ("zu", "5 amaminithi edlule", 300)):
        ts = P.relative_ts(text, lang)
        slack = max(seconds // 10, 60) + (6 * day if seconds > 30 * day else 0)  # months and years vary in length
        slack += day // 2 if seconds >= day else 0  # yt-dlp rounds 'now-1day' to the nearest midnight (UTC)
        assert ts is not None and abs(now - ts - seconds) <= slack, (lang, text)
    assert P.relative_ts("לפני שנה") is None and P.relative_ts("שידור חי", "iw") is None  # no number, no unit


def dubbed_info(original: str | None, dubs: tuple[str, ...], uploaded: tuple[str, ...] = ()) -> dict:
    """An auto-dubbed video as yt-dlp extracts it: one '-orig' ASR track per audio track (the original one first
    only by chance)."""
    track = [{"url": "https://www.youtube.com/api/timedtext?fmt=json3", "ext": "json3"}]
    langs = sorted({*dubs, *([original] if original else [])})
    return {"subtitles": {key: track for key in uploaded},
            "automatic_captions": {key: track for lang in langs for key in (lang, f"{lang}-orig")}}


def dubbed_players(original: str, dubs: tuple[str, ...]) -> list[dict]:
    """Its player responses: one audio track per language, YouTube's label marking the original."""
    return [{"streamingData": {"adaptiveFormats": [
        {"itag": 251, "audioTrack": {"displayName": f"{lang} original" if lang == original else lang,
                                     "id": f"{lang}.4"}} for lang in (original, *dubs)]}}]


def test_dubbed_video_transcript_is_in_the_original_language(monkeypatch):
    picked = []
    monkeypatch.setattr(subtitles, "_download_track", lambda ydl, info, key, auto, tmp: picked.append((key, auto)))
    japanese = dubbed_info("ja", ("en-US",))  # mx7MdPdn_a8: en-US-orig is listed before ja-orig
    ja_players = dubbed_players("ja", ("en-US",))
    assert M.spoken_language(japanese, ja_players) == "ja" and M.spoken_track_language(japanese, ja_players) == "ja"
    for kwargs in ({}, {"original": True}):
        M.download_transcript(None, japanese, player_responses=ja_players, **kwargs)
    assert picked == [("ja-orig", True)] * 2
    # RU6gEobXVHA: English NASA stream with 20 dubs (ar-orig first) and two uploaded English broadcast tracks
    dubs = ("ar", "bn", "de-DE", "es-US", "fr-FR", "ja", "ko")
    nasa, nasa_players = dubbed_info("en-US", dubs, ("en-uYU-mmqFLq8", "en-JkeT")), dubbed_players("en-US", dubs)
    assert M.spoken_language(nasa, nasa_players) == "en"
    picked.clear()
    M.download_transcript(None, nasa, original=True, player_responses=nasa_players)
    M.download_transcript(None, {**nasa, "subtitles": {}}, player_responses=nasa_players)
    assert picked == [("en-uYU-mmqFLq8", False), ("en-US-orig", True)]
    # the original audio can't be told apart: no guess among the dubs' tracks
    unknown = {**nasa, "subtitles": {}}
    assert M.spoken_language(unknown) is None
    picked.clear()
    M.download_transcript(None, unknown, original=True)
    assert picked and not picked[0][0].endswith("-orig")
    # an explicit language still gets its own track, and a single '-orig' track is the spoken language
    picked.clear()
    M.download_transcript(None, japanese, language="en-US")
    assert picked == [("en-US", True)]
    assert M.spoken_language({"automatic_captions": {"de-orig": [{}], "de": [{}], "en": [{}]}}) == "de"


def test_bare_language_gets_the_real_regional_track(monkeypatch):
    """yt_videos-3 (mx7MdPdn_a8): language=en picked YouTube's machine translation 'en' (tlang=en, a 429
    anonymously, so transcript null) over the real 'en-US' ASR track of the English dub."""
    picked = []
    monkeypatch.setattr(subtitles, "_download_track", lambda ydl, info, key, auto, tmp: picked.append((key, auto)))
    real = [{"url": "https://www.youtube.com/api/timedtext?lang=en-US&fmt=json3", "ext": "json3"}]
    info = {**dubbed_info("ja", ("en-US",)), "automatic_captions": {
        "en": [{"url": "https://www.youtube.com/api/timedtext?lang=ja&tlang=en&fmt=json3", "ext": "json3"}],
        "en-US-orig": real, "en-US": real,
        "ja-orig": [{"url": "https://www.youtube.com/api/timedtext?lang=ja&fmt=json3", "ext": "json3"}]}}
    for language in ("en", "EN", "en-US"):
        M.download_transcript(None, info, language=language)
    M.download_transcript(None, info, language="ja")
    assert picked == [("en-US", True)] * 3 + [("ja-orig", True)]


def test_original_audio_from_the_player_responses():
    """The audio track YouTube labels original, read from the raw player responses when yt-dlp lists none of its
    formats (yt_videos-2)."""
    def player(*tracks):
        return {"streamingData": {"adaptiveFormats": [
            {"itag": 251, "audioTrack": {"displayName": name, "id": track, "audioIsDefault": default}}
            for name, track, default in tracks] + [{"itag": 137}]}}
    responses = [{}, player(("English (US)", "en-US.10", False), ("Japanese original", "ja.4", True))]
    assert M.original_audio_language(responses) == "ja"
    assert M.original_audio_language([player(("English (US)", "en-US.10", True))]) is None  # default isn't original
    assert M.original_audio_language([]) is None
    dubbed = dubbed_info("ja", ("en-US",))
    assert M.spoken_track_language(dubbed) is None and M.spoken_language(dubbed, responses) == "ja"


def test_live_stream_captions_are_no_transcript(monkeypatch):
    def download(*_):
        raise AssertionError("a live caption feed is not a file to download")

    monkeypatch.setattr(subtitles, "_download_track", download)
    feed = {"en": [{"url": "https://manifest.googlevideo.com/api/manifest/timedtext/en.m3u8", "ext": "vtt",
                    "protocol": "m3u8_native"}]}
    assert M.download_transcript(None, {"live_status": "is_live", "automatic_captions": feed}) is None
    assert M.download_transcript(None, {"live_status": "is_upcoming", "subtitles": feed}) is None
    assert M.is_live_feed({"automatic_captions": feed}, "en", True)
    assert M.download_transcript(None, {"live_status": "post_live", "automatic_captions": feed}) is None
    vod = {"en": [{"url": "https://www.youtube.com/api/timedtext?lang=en&fmt=json3", "ext": "json3"}]}
    assert not M.is_live_feed({"automatic_captions": vod}, "en", True)


def unplayable(reason: str, *, screen: str | None = None, status: str = "UNPLAYABLE") -> list[dict]:
    """Player responses for a video yt-dlp still describes but YouTube won't play anonymously."""
    error = {"errorScreen": {"playerInterstitialRenderer": {"content": {"interstitialViewModel": {
        "title": {"content": reason}, "description": {"content": screen}}}}}} if screen else {}
    return [{"playabilityStatus": {"status": status, "reason": reason, **error}, "videoDetails": {"videoId": "x"}}]


def test_unplayable_videos_are_403():
    geo = "The uploader has not made this video available in your country"
    for players in (unplayable("Video unavailable", screen=geo), unplayable(geo)):
        with pytest.raises(Forbidden) as exc:
            M.ensure_playable(players, {})
        assert exc.value.code == "geo_restricted" and exc.value.message == geo
    members = unplayable("This video is available to this channel's members on level: Plus (or any higher level). "
                         "Join this channel to get access to members-only content and other exclusive perks.")
    with pytest.raises(Forbidden):
        M.ensure_playable(members, {})
    with pytest.raises(ApiError) as exc:
        M.ensure_playable(unplayable("Sign in to confirm you're not a bot", status="LOGIN_REQUIRED"), {})
    assert exc.value.status == 503
    M.ensure_playable(members, {"automatic_captions": {"en": [{"url": "u"}]}})  # captions kept: try them
    M.ensure_playable(unplayable("Premieres in 2 hours", status="LIVE_STREAM_OFFLINE"), {})  # waiting is no error
    M.ensure_playable([*members, {"playabilityStatus": {"status": "OK"}}], {})  # another client can play it
    M.ensure_playable([], {})


# --- offline: HTTP errors that never reach YouTube -----------------------------------


@pytest.mark.parametrize("path,params", [
    ("/v1/youtube/video", {"url": "https://www.tiktok.com/@scout2015/video/6718335390845095173"}),
    ("/v1/youtube/video/transcript", {"url": "https://www.instagram.com/p/abc/"}),
    ("/v1/youtube/video/comments", {"url": "https://www.tiktok.com/@scout2015"}),
    ("/v1/youtube/video/sponsors", {"url": "https://example.com/watch?v=jNQXAC9IVRw"}),
    ("/v1/youtube/community-post", {"url": watch(ZOO)}),
    ("/v1/youtube/video/comments", {"url": watch(ZOO), "order": "best"}),
    ("/v1/youtube/video/comments", {"url": watch(ZOO), "continuationToken": COMMENTS_TOKEN}),
    ("/v1/youtube/video/comments", {"url": watch(SPONSORED), "continuationToken": REPLIES_TOKEN}),
    ("/v1/youtube/video/comment/replies", {"continuationToken": COMMENTS_TOKEN}),
    ("/v1/youtube/video/comment/replies", {"continuationToken": "not-a-token"}),
    ("/v1/youtube/video/transcript", {"url": watch(ZOO), "original_audio": "maybe"}),
    ("/v1/youtube/video/transcript", {"url": watch(ZOO), "language": "en_US!"}),
    ("/v1/youtube/video", {"url": watch(ZOO), "language": "e!!"}),
    ("/v1/youtube/video", {}),
    ("/v1/youtube/video/comment/replies", {}),
    ("/v1/youtube/community-post", {}),
    ("/v1/youtube/community-post", {"url": "[]"}),  # urllib raises ValueError on the host
    ("/v1/youtube/community-post", {"url": f"youtube.com／post/{POST}"}),
])
def test_bad_input_is_400(client, path, params):
    r = client.get(path, params=params)
    assert r.status_code == 400, r.text
    assert r.json()["success"] is False


@pytest.mark.parametrize("path", ["/v1/youtube/shorts/trending", "/v1/youtube/search/typeahead"])
def test_unsupported_paths_explain_why(client, path):
    r = client.get(path)
    assert r.status_code == 501 and r.json()["error"] == "not_supported"
    row = next(e for e in client.get("/meta/coverage").json()["endpoints"] if e["path"] == path)
    assert row["notes"] == M.UNSUPPORTED[path]


def test_my_paths_are_routed(client):
    paths = client.app.openapi()["paths"]
    for path in ("/v1/youtube/video", "/v1/youtube/video/transcript", "/v1/youtube/video/comments",
                 "/v1/youtube/video/comment/replies", "/v1/youtube/video/sponsors", "/v1/youtube/community-post",
                 *M.UNSUPPORTED):
        assert path in paths, path


# --- offline: routes with yt-dlp stubbed (cached YouTube responses) -------------------


def stub_watch(monkeypatch, vid: str, name: str | None = None) -> None:
    """Make extract_watch return a cached watch page instead of downloading it."""
    if name:
        raw = cached(name)
        info, initial, players = raw["info"], raw["initial"], raw["players"]
    else:
        info, initial, players = (cached(f"{vid}.{k}.json") for k in ("info", "initial", "players"))
    monkeypatch.setattr(M, "extract_watch", lambda ydl, v, process=True: (
        info, {"initial_data": initial, "player_responses": players}))


def test_video_route_age_restricted_is_403(client, monkeypatch):
    stub_watch(monkeypatch, AGE_GATED, "fix/raw_HtVdAasjOgU.json")
    r = client.get("/v1/youtube/video", params={"url": watch(AGE_GATED)})
    assert r.status_code == 403 and r.json()["error"] == "age_restricted", r.text


@pytest.mark.parametrize("status,expected", [
    ({"status": 5}, 200), ({"status": "OK", "reason": 7}, 200), ({"status": "LOGIN_REQUIRED", "reason": ["x"]}, 200),
    ({"status": "ERROR", "reason": {"simpleText": "Video unavailable"}}, 200),
    ({"status": True, "reason": "Sign in to confirm your age"}, 403),
], ids=["status_number", "reason_number", "reason_list", "reason_object", "status_bool_age_gate"])
def test_video_routes_read_a_playability_status_of_another_type_as_none(client, monkeypatch, status, expected):
    """yt-playability-type-500: yt-dlp reads each player response's playability status and reason as strings (its
    age-gate check on every response, then the reason it gives for a video without formats), so one of another
    type was a 500 (TypeError or AttributeError) before any of our code ran. It reads as missing; an age-gate
    reason still makes a 403."""
    def player(self, client, video_id, *args, **kwargs):  # the player API's answer
        return {"playabilityStatus": dict(status), "videoDetails": {
            "videoId": video_id, "title": "Odd", "lengthSeconds": "10", "author": "a", "channelId": "UCx"}}

    monkeypatch.setattr(YoutubeIE, "_extract_player_response", player)
    for name, value in (("_download_initial_webpage", None), ("_download_initial_data", {}), ("_download_ytcfg", {}),
                        ("_download_player_url", None)):  # no watch page, no other request
        monkeypatch.setattr(YoutubeIE, name, lambda self, *args, value=value, **kwargs: value)
    video = client.get("/v1/youtube/video", params={"url": watch(ZOO)})
    transcript = client.get("/v1/youtube/video/transcript", params={"url": watch(ZOO)})
    assert (video.status_code, transcript.status_code) == (expected, expected), (video.text, transcript.text)
    if expected == 403:
        assert video.json()["error"] == transcript.json()["error"] == "age_restricted"
    else:
        assert video.json()["title"] == "Odd" and transcript.json()["success"]


def test_video_route_exact_comment_count(client, monkeypatch):
    stub_watch(monkeypatch, ZOO)
    calls = []

    def fake_next(ie, vid, query, note):
        calls.append(note)
        return cached(f"comments_{ZOO}_0.json")

    monkeypatch.setattr(M, "call_next", fake_next)
    body = client.get("/v1/youtube/video", params={"url": watch(ZOO)}).json()
    assert body["commentCountText"] == "10M" and body["commentCountInt"] == 10639553 and len(calls) == 1
    monkeypatch.setattr(M, "call_next", lambda *a: (_ for _ in ()).throw(ExtractorError("HTTP Error 500")))
    body = client.get("/v1/youtube/video", params={"url": watch(ZOO)}).json()
    assert body["success"] and body["commentCountInt"] > 1_000_000  # falls back to the rounded figure


def test_video_route_zero_comments(client, monkeypatch):
    # 6n1d992ODAU has comments on but none: its comments panel header has no count, the comments header '0'
    panel = {"engagementPanelSectionListRenderer": {
        "panelIdentifier": "engagement-panel-comments-section",
        "header": {"engagementPanelTitleHeaderRenderer": {"title": {"runs": [{"text": "Comments"}]}}}}}
    page = {"initial_data": {"engagementPanels": [panel]}, "player_responses": []}
    monkeypatch.setattr(M, "extract_watch", lambda ydl, v, process=True: ({"id": v}, page))
    calls = []

    def fake_next(ie, vid, query, note):
        calls.append(note)
        header = {"commentsHeaderRenderer": {"countText": {"runs": [{"text": "0"}, {"text": " Comments"}]}}}
        return {"onResponseReceivedEndpoints": [{"reloadContinuationItemsCommand": {"continuationItems": [header]}}]}

    monkeypatch.setattr(M, "call_next", fake_next)
    body = client.get("/v1/youtube/video", params={"url": watch("6n1d992ODAU")}).json()
    assert body["commentCountInt"] == 0 and body["commentCountText"] is None and len(calls) == 1
    # comments off (-62C9R_41Sc): no comments panel at all, no extra request, no count
    page["initial_data"] = {"engagementPanels": [
        {"engagementPanelSectionListRenderer": {"panelIdentifier": "engagement-panel-structured-description"}}]}
    body = client.get("/v1/youtube/video", params={"url": watch("-62C9R_41Sc")}).json()
    assert body["success"] and body["commentCountInt"] is None and len(calls) == 1


@pytest.mark.parametrize("path,params", [
    ("/v1/youtube/video/comments", {"url": watch(SPONSORED), "continuationToken": COMMENTS_TOKEN}),
    ("/v1/youtube/video/comment/replies", {"continuationToken": REPLIES_TOKEN}),
])
def test_rejected_token_is_400(client, monkeypatch, path, params):
    rejected = {"responseContext": {"visitorData": "x"}, "trackingParams": "y"}  # YouTube's answer to a tampered token
    monkeypatch.setattr(M, "call_next", lambda *a: rejected)
    r = client.get(path, params=params)
    assert r.status_code == 400 and r.json()["error"] == "bad_cursor", r.text


def test_comments_route_reports_player_reason(client, monkeypatch):
    monkeypatch.setattr(M, "call_next", lambda *a: {})

    def private(ydl, vid, process=True):
        raise Forbidden("Private video")

    monkeypatch.setattr(M, "extract_watch", private)
    r = client.get("/v1/youtube/video/comments", params={"url": watch(PRIVATE)})
    assert r.status_code == 403, r.text
    stub_watch(monkeypatch, ZOO)  # available, yet no comments section: comments are off
    r = client.get("/v1/youtube/video/comments", params={"url": watch(ZOO)})
    assert r.status_code == 200 and r.json()["comments"] == []


def test_sponsors_route_transcript_fallback(client, monkeypatch):
    stub_watch(monkeypatch, SPONSORED)
    asked = []

    def fake_download(ydl, ie_info, *, language=None, original=False, player_responses=()):
        asked.append(language)
        if language == "fr":
            return None  # e.g. only a throttled machine translation
        return subtitles.Transcript("en", "English", True, "json3", "",
                                    [subtitles.Segment(0, 1, "thanks to betterhelp for sponsoring")])

    monkeypatch.setattr(M, "download_transcript", fake_download)
    body = client.get("/v1/youtube/video/sponsors", params={"url": watch(SPONSORED), "language": "fr"}).json()
    assert asked == ["fr", None] and body["sponsorDetection"]["transcriptAvailable"] is True

    def blocked(*a, **k):
        raise DownloadError("ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(M, "download_transcript", blocked)
    r = client.get("/v1/youtube/video/sponsors", params={"url": watch(SPONSORED)})
    assert r.status_code == 503 and r.json()["error"] == "upstream_blocked"


def test_sponsors_route_channel_handle_with_combining_marks(client, monkeypatch):
    """The sponsors route reads the uploader's handle as the video route does, and its brand keys don't count the
    channel's own site as a sponsor; yt-dlp had no handle for 'ครัวป้าแดง-PaDaengKitchen'."""
    handle = "ครัวป้าแดง-PaDaengKitchen"
    info = {"id": "abcdefghijk", "title": "A video", "channel_id": "UCx", "channel": "ครัวป้าแดง", "uploader_id": None,
            "description": "Use code PADAENG10 at https://padaengkitchen.com for 10% off."}
    raw = {"initial_data": owner_page("/@" + quote(handle)), "player_responses": [{}]}
    monkeypatch.setattr(M, "extract_watch", lambda ydl, v, process=True: (info, raw))
    monkeypatch.setattr(M, "download_transcript", lambda *a, **k: None)
    body = client.get("/v1/youtube/video/sponsors", params={"url": watch("abcdefghijk")}).json()
    assert body["video"]["channel"] == {"id": "UCx", "url": f"https://www.youtube.com/@{handle}", "handle": handle,
                                        "title": "ครัวป้าแดง"}
    assert body["suspectedSponsors"] == [] and body["sponsorDetection"]["status"] == "not_found"


def test_community_post_route(client, monkeypatch):
    data = cached(f"post_{POST}.json")
    monkeypatch.setattr(YoutubeTabIE, "_extract_data", lambda self, url, item_id, *a, **k: (data, {}))
    body = client.get("/v1/youtube/community-post", params={"url": f"https://www.youtube.com/post/{POST}"}).json()
    assert body["likeCount"] == 782 and body["publishedTime"] == "2025-11-07T15:00:01.184Z"
    assert_conforms(body, "/v1/youtube/community-post", min_coverage=1.0)

    def missing(self, url, item_id, *a, **k):
        raise ExtractorError("Unable to download API page: HTTP Error 404: Not Found")

    monkeypatch.setattr(YoutubeTabIE, "_extract_data", missing)
    r = client.get("/v1/youtube/community-post", params={"url": "https://www.youtube.com/post/UgkxAAAAAAAAAAAAAAAAAAAA"})
    assert r.status_code == 404 and r.json()["message"] == "Community post not found"


@pytest.mark.parametrize("players,status,error", [
    (unplayable("Video unavailable", screen="The uploader has not made this video available in your country"),
     403, "geo_restricted"),
    (unplayable("This video is available to this channel's members on level: Plus (or any higher level). "
                "Join this YouTube channel from your computer or mobile app."), 403, "forbidden"),
    ([{"playabilityStatus": {"status": "OK"}, "videoDetails": {"videoId": "x"}}], 200, None),
])
def test_transcript_route_unplayable_and_live(client, monkeypatch, players, status, error):
    info = {"id": "abcdefghijk", "title": "x", "live_status": "is_live" if status == 200 else "not_live",
            "automatic_captions": {"en": [{"url": "https://manifest.googlevideo.com/x.m3u8", "ext": "vtt",
                                           "protocol": "m3u8_native"}]} if status == 200 else {}}
    monkeypatch.setattr(M, "extract_watch", lambda ydl, v, process=True: (
        info, {"initial_data": {}, "player_responses": players}))
    r = client.get("/v1/youtube/video/transcript", params={"url": watch("abcdefghijk")})
    assert r.status_code == status, r.text
    assert r.json().get("error") == error and (error or r.json()["transcript"] is None)


def fake_tracks(monkeypatch, blank: set[tuple[str, bool]]) -> list[tuple[str, bool]]:
    """Stub the caption download: the tracks in `blank` come back as YouTube's one-event ' ' stub."""
    fetched = []

    def download(ydl, info, key, is_auto, tmp):
        fetched.append((key, is_auto))
        segments = [] if (key, is_auto) in blank else [subtitles.Segment(0, 1, f"{key} {is_auto}")]
        return subtitles.Transcript(key, f"{key} name", is_auto, "json3", "", segments)

    monkeypatch.setattr(subtitles, "_download_track", download)
    return fetched


def test_blank_uploaded_track_falls_back_to_its_asr_track(monkeypatch):
    """gap-youtube_videos-2 (HtVdAasjOgU): the uploaded 'en' track is a blank stub, the English ASR track is real."""
    track = [{"url": "https://www.youtube.com/api/timedtext?lang=en&fmt=json3", "ext": "json3"}]
    info = {"subtitles": {"en": track, "fr": track},
            "automatic_captions": {"en-orig": track, "en": track, "fr": [
                {"url": "https://www.youtube.com/api/timedtext?lang=en&tlang=fr&fmt=json3", "ext": "json3"}]}}
    fetched = fake_tracks(monkeypatch, {("en", False), ("fr", False)})
    for kwargs in ({}, {"original": True}, {"language": "en"}):
        fetched.clear()
        assert M.download_transcript(None, info, **kwargs).text == "en True"
        assert fetched == [("en", False), ("en", True)]
    fetched.clear()  # only a machine translation into French: not the uploaded track's text, so still blank
    assert M.download_transcript(None, info, language="fr").segments == [] and fetched == [("fr", False)]
    fetched.clear()
    assert M.download_transcript(None, {**info, "automatic_captions": {}}, language="en").segments == []
    fake_tracks(monkeypatch, set())  # a real uploaded track or a blank ASR track isn't retried
    assert M.download_transcript(None, info).text == "en False"
    fetched = fake_tracks(monkeypatch, {("en-orig", True)})
    assert M.download_transcript(None, {"automatic_captions": info["automatic_captions"]}).segments == []
    assert fetched == [("en-orig", True)]


@pytest.mark.parametrize("params", [{}, {"original_audio": "true"}, {"language": "en"}])
def test_transcript_route_age_gated_blank_uploaded_track(client, monkeypatch, params):
    stub_watch(monkeypatch, AGE_GATED, "fix/raw_HtVdAasjOgU.json")
    fake_tracks(monkeypatch, {("en", False)})
    body = client.get("/v1/youtube/video/transcript", params={"url": watch(AGE_GATED), **params}).json()
    assert body["success"] and body["transcript_only_text"] == "en True" and body["language"] == "en name", body
    sponsors = client.get("/v1/youtube/video/sponsors", params={"url": watch(AGE_GATED)}).json()
    assert sponsors["sponsorDetection"]["transcriptAvailable"] is True


SHARE = "UgkxShareOfAnotherChannelsPost00000"


def shared_post_page(like_action: int | None) -> dict:
    """A youtube.com/post/<id> page for a share: the sharing channel's sharedPostRenderer around the original."""
    original = {"postId": "UgkxTheOriginalPostOfTheOtherChan", "authorText": {"runs": [{"text": "Other channel"}]},
                "authorEndpoint": {"browseEndpoint": {"browseId": "UCother", "canonicalBaseUrl": "/@other"}},
                "contentText": {"runs": [{"text": "The original text"}]},
                "publishedTimeText": {"runs": [{"text": "3 weeks ago"}]},
                "voteCount": {"simpleText": "485", "accessibility": {"accessibilityData": {"label": "485 likes"}}}}
    share = {"postId": SHARE, "displayName": {"runs": [{"text": "Sharing channel"}]},
             "endpoint": {"browseEndpoint": {"browseId": "UCsharer", "canonicalBaseUrl": "/@sharer"}},
             "content": {"runs": [{"text": "Look at this one"}]},
             "publishedTimeText": {"runs": [{"text": "1 month ago"}]},
             "originalPost": {"backstagePostRenderer": original}}
    thread = {"backstagePostThreadRenderer": {"post": {"sharedPostRenderer": share}}}
    tab = {"tabRenderer": {"content": {"sectionListRenderer": {"contents": [
        {"itemSectionRenderer": {"contents": [thread]}}]}}}}
    posting = {"datePublished": "2026-09-09T15:41:25.621373-07:00", "text": "Look at this one"}
    if like_action is not None:
        posting["interactionStatistic"] = [{"interactionType": {"type": "LikeAction"},
                                            "userInteractionCount": str(like_action)}]
    return {"contents": {"twoColumnBrowseResultsRenderer": {"tabs": [tab]}},
            "microformat": {"microformatDataRenderer": {"postDetails": {
                "externalPostId": SHARE, "discussionForumPosting": posting}}}}


def test_community_post_route_shared_post(client, monkeypatch):
    data = shared_post_page(12)
    monkeypatch.setattr(YoutubeTabIE, "_extract_data", lambda self, url, item_id, *a, **k: (data, {}))
    body = client.get("/v1/youtube/community-post", params={"url": f"https://www.youtube.com/post/{SHARE}"}).json()
    assert body["id"] == SHARE and body["content"] == "Look at this one" and body["likeCount"] == 12
    assert body["channel"] == {"id": "UCsharer", "title": "Sharing channel", "url": "https://www.youtube.com/@sharer",
                               "handle": "sharer"}
    assert body["publishedTime"] == "2026-09-09T22:41:25.621Z" and body["publishedTimeText"] == "1 month ago"
    original = body["originalPost"]
    assert original["id"] == "UgkxTheOriginalPostOfTheOtherChan" and original["likeCount"] == 485
    assert original["channel"]["handle"] == "other" and original["content"] == "The original text"
    assert_conforms(body, "/v1/youtube/community-post", min_coverage=0.8)
    data = shared_post_page(None)  # YouTube often counts no likes for the share itself
    assert P.post_payload(ui_tab("en"), P.find_post(data, SHARE), data)["likeCount"] is None


# --- live ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def get(client):
    seen: dict[tuple, dict] = {}

    def call(path: str, **params) -> dict:
        key = (path, tuple(sorted(params.items())))
        if key not in seen:
            r = client.get(path, params=params)
            assert r.status_code == 200, (r.status_code, r.text[:300])
            seen[key] = r.json()
        return seen[key]

    return call


@pytest.mark.live
def test_live_video(get):
    body = get("/v1/youtube/video", url=watch(ZOO))
    assert_conforms(body, "/v1/youtube/video", min_coverage=0.9)
    assert body["id"] == ZOO and body["title"] == "Me at the zoo" and body["type"] == "video"
    assert body["channel"]["id"] == "UC4QobU6STFB0P71PMvOGN5A" and body["channel"]["handle"] == "jawed"
    assert body["viewCountInt"] > 300_000_000 and body["likeCountInt"] > 0 and body["commentCountInt"] > 0
    assert body["durationMs"] == 19000 and body["durationFormatted"] == "00:00:19"
    assert [c["startMs"] for c in body["chapters"]] == [0, 5000, 17000]
    assert {t["languageCode"] for t in body["captionTracks"]} >= {"en", "de"}
    assert body["watchNextVideos"] and all(v["id"] != ZOO for v in body["watchNextVideos"])
    assert body["most_replayed"] is None or body["most_replayed"]["markers"]


@pytest.mark.live
def test_live_video_paid_promotion(get):
    body = get("/v1/youtube/video", url=f"https://youtu.be/{SPONSORED}")
    assert_conforms(body, "/v1/youtube/video", min_coverage=0.95)
    assert body["id"] == SPONSORED and body["isPaidPromotion"] is True
    assert any(link["url"].startswith("https://betterhelp.com/") for link in body["descriptionLinks"])


@pytest.mark.live
def test_live_transcript(get):
    body = get("/v1/youtube/video/transcript", url=watch(SPONSORED))
    assert_conforms(body, "/v1/youtube/video/transcript", min_coverage=1.0)
    assert body["videoId"] == SPONSORED and body["language"] == "English"
    assert len(body["transcript"]) > 50 and "meal prep" in body["transcript_only_text"].lower()
    starts = [int(s["startMs"]) for s in body["transcript"]]
    assert starts == sorted(starts)


@pytest.mark.live
def test_live_transcript_language_and_original(get):
    german = get("/v1/youtube/video/transcript", url=watch(ZOO), language="de")
    assert_conforms(german, "/v1/youtube/video/transcript", min_coverage=1.0)
    assert german["language"] == "German" and "Elefanten" in german["transcript_only_text"]
    # YouTube doesn't identify this video's spoken language, so original_audio=true yields nulls.
    original = get("/v1/youtube/video/transcript", url=watch(ZOO), original_audio="true")
    assert original["transcript"] is None and original["language"] is None and original["videoId"] == ZOO


@pytest.mark.live
def test_live_comments_pagination(get):
    page1 = get("/v1/youtube/video/comments", url=watch(SPONSORED))
    assert_conforms(page1, "/v1/youtube/video/comments", min_coverage=1.0)
    assert len(page1["comments"]) >= 10 and page1["continuationToken"]
    assert all(c["replyLevel"] == 0 and c["content"] for c in page1["comments"])
    page2 = get("/v1/youtube/video/comments", url=watch(SPONSORED), continuationToken=page1["continuationToken"])
    assert_conforms(page2, "/v1/youtube/video/comments", min_coverage=0.9)
    ids1, ids2 = [c["id"] for c in page1["comments"]], [c["id"] for c in page2["comments"]]
    assert ids2 and len(set(ids1)) == len(ids1) and not set(ids1) & set(ids2)
    assert page2["continuationToken"] != page1["continuationToken"]


@pytest.mark.live
def test_live_comments_newest(get):
    top = get("/v1/youtube/video/comments", url=watch(SPONSORED))
    newest = get("/v1/youtube/video/comments", url=watch(SPONSORED), order="newest")
    assert_conforms(newest, "/v1/youtube/video/comments", min_coverage=0.9)
    assert newest["comments"] and [c["id"] for c in newest["comments"]] != [c["id"] for c in top["comments"]]
    stamps = [c["publishedTime"] for c in newest["comments"]]
    assert stamps == sorted(stamps, reverse=True)


@pytest.mark.live
def test_live_replies_pagination(get):
    page = get("/v1/youtube/video/comments", url=watch(SPONSORED))
    parent = max(page["comments"], key=lambda c: c["engagement"]["replies"])
    assert parent["engagement"]["replies"] > 10, "need a comment with more than one page of replies"
    r1 = get("/v1/youtube/video/comment/replies", continuationToken=parent["repliesContinuationToken"])
    assert_conforms(r1, "/v1/youtube/video/comment/replies", min_coverage=1.0)
    assert r1["comments"] and all(c["replyLevel"] == 1 and c["id"].startswith(parent["id"] + ".")
                                  for c in r1["comments"])
    assert r1["continuationToken"]
    r2 = get("/v1/youtube/video/comment/replies", continuationToken=r1["continuationToken"])
    assert r2["comments"] and not {c["id"] for c in r1["comments"]} & {c["id"] for c in r2["comments"]}


@pytest.mark.live
def test_live_community_post(get):
    body = get("/v1/youtube/community-post", url=f"https://www.youtube.com/post/{POST}")
    assert_conforms(body, "/v1/youtube/community-post", min_coverage=1.0)
    assert body["id"] == POST and body["channel"]["id"] == "UChhw6DlKKTQ9mYSpTfXUYqA"
    assert body["channel"]["handle"] == "starterstory" and body["likeCount"] > 0
    assert "starterstory.com/bsm" in body["content"] and body["images"]
    assert body["likeCount"] % 100 != 0 or body["likeCount"] < 1000  # exact, not '1.2K'
    assert body["publishedTime"].startswith("2025-11-07T15:00:01")


@pytest.mark.live
def test_live_video_language_alias_and_age_gate(client, get):
    body = get("/v1/youtube/video", url=watch(ZOO), language="zh")
    assert body["id"] == ZOO and body["commentCountInt"] > 10_000_000 and body["commentCountInt"] % 1000 != 0
    r = client.get("/v1/youtube/video", params={"url": watch(AGE_GATED)})
    assert r.status_code == 403 and r.json()["error"] == "age_restricted", r.text


@pytest.mark.live
def test_live_tampered_token_is_400(client, get):
    token = get("/v1/youtube/video/comments", url=watch(SPONSORED))["continuationToken"]
    mid = len(token) // 2
    tampered = token[:mid] + ("A" if token[mid] != "A" else "B") + token[mid + 1:]
    r = client.get("/v1/youtube/video/comments", params={"url": watch(SPONSORED), "continuationToken": tampered})
    assert r.status_code == 400 and r.json()["error"] == "bad_cursor", r.text


@pytest.mark.live
def test_live_sponsors(get):
    body = get("/v1/youtube/video/sponsors", url=watch(SPONSORED))
    assert_conforms(body, "/v1/youtube/video/sponsors", min_coverage=1.0)
    assert body["video"]["id"] == SPONSORED and body["video"]["isPaidPromotion"] is True
    top = body["suspectedSponsors"][0]
    assert top["website"] == "betterhelp.com" and top["confidence"] in ("high", "medium")
    assert body["sponsorDetection"]["status"] == "found" and "paid promotion badge" in body["sponsorDetection"]["methods"]


@pytest.mark.live
def test_live_sponsors_none(get):
    body = get("/v1/youtube/video/sponsors", url=watch(ZOO))
    assert body["video"]["isPaidPromotion"] is False and body["suspectedSponsors"] == []
    assert body["sponsorDetection"]["status"] == "not_found"


@pytest.mark.live
@pytest.mark.parametrize("path,params", [
    ("/v1/youtube/video", {"url": watch(MISSING)}),
    ("/v1/youtube/video/comments", {"url": watch(MISSING)}),
    ("/v1/youtube/community-post", {"url": "https://www.youtube.com/post/UgkxNonexistentPostId000000000000000"}),
])
def test_live_missing_is_404(client, path, params):
    r = client.get(path, params=params)
    assert r.status_code == 404, r.text
    assert r.json()["error"] == "not_found"
