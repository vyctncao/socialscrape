import json
import logging
import os
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from socialscrape import cursor, subtitles, util
from socialscrape.catalog import CATALOG
from socialscrape.errors import BadRequest, NotFound
from socialscrape.ytdlp import translate_error
from yt_dlp import YoutubeDL
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import DownloadError, ExtractorError, clean_html, unescapeHTML


def test_every_catalog_path_is_routed(client):
    cov = client.get("/meta/coverage").json()
    assert cov["total"] == len(CATALOG) == 66
    # FastAPI >= 0.143 nests included routers (no flat `.path`), so read the schema instead.
    routed = set(client.app.openapi()["paths"])
    for row in cov["endpoints"]:
        assert row["path"] in routed, row["path"]
        if row["status"] == "not_supported":
            r = client.get(row["path"])
            assert r.status_code == 501, row["path"]
            assert r.json()["success"] is False and r.json()["error"] == "not_supported"
            assert row["notes"], f"{row['path']} needs an UNSUPPORTED reason"


def test_every_coverage_row_has_notes(client):
    rows = client.get("/meta/coverage").json()["endpoints"]
    assert [r["path"] for r in rows if not (r["notes"] or "").strip()] == []


def test_missing_required_param_is_400(client):
    implemented = [r for r in client.get("/meta/coverage").json()["endpoints"] if r["status"] == "implemented"]
    by_path = {e["path"]: e for e in CATALOG}
    for row in implemented:
        if not by_path[row["path"]]["required"]:
            continue
        r = client.get(row["path"])
        assert r.status_code == 400, (row["path"], r.status_code, r.text[:200])
        assert r.json()["success"] is False


def test_auth(monkeypatch):
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "k1,k2")
    get_settings.cache_clear()
    try:
        c = TestClient(create_app())
        assert c.get("/meta/coverage").status_code == 200
        assert c.get("/v1/youtube/shorts/trending").status_code == 401
        assert c.get("/v1/youtube/shorts/trending", headers={"x-api-key": "k2"}).status_code != 401
    finally:
        monkeypatch.delenv("SOCIALSCRAPE_API_KEYS")
        get_settings.cache_clear()


def test_cache_middleware(tmp_path):
    from fastapi import APIRouter, FastAPI

    from socialscrape.cache import CacheMiddleware, ResponseCache
    from socialscrape.registry import sc_get

    calls = {"n": 0}
    router = APIRouter()

    @sc_get(router, "/v9/test/thing", source="test")
    async def thing(x: str):
        calls["n"] += 1
        return {"x": x, "n": calls["n"]}

    app = FastAPI()
    app.add_middleware(CacheMiddleware, cache=ResponseCache(str(tmp_path / "c.sqlite3")))
    app.include_router(router)
    c = TestClient(app)
    assert c.get("/v9/test/thing?x=1").json()["n"] == 1
    assert c.get("/v9/test/thing?x=1").json()["n"] == 2  # no cache_max_age -> live
    hit = c.get("/v9/test/thing?x=1&cache_max_age=1d").json()
    assert hit["n"] == 2 and hit["cached"] is True and hit["cached_at"].endswith("Z")
    for pasted in ("1d\u200b", "\ufeff24h"):  # a copy's invisible ends, which every other param loses
        again = c.get("/v9/test/thing", params={"x": "1", "cache_max_age": pasted}).json()
        assert again["n"] == 2 and again["cached"] is True, pasted
    assert c.get("/v9/test/thing?x=2&cache_max_age=1d").json()["n"] == 3  # different key
    assert c.get("/v9/test/thing?x=1&cache_max_age=bogus").status_code == 400


def test_cursor_roundtrip():
    tok = cursor.encode_cursor({"o": 30, "id": "abc"})
    assert cursor.decode_cursor(tok) == {"o": 30, "id": "abc"}
    assert cursor.decode_cursor(None) is None
    with pytest.raises(BadRequest):
        cursor.decode_cursor("garbage")
    with pytest.raises(BadRequest):
        cursor.decode_cursor(cursor.PREFIX + "!!!")


def test_util():
    assert util.parse_count("1.2M views") == 1_200_000
    assert util.parse_count("9,221 videos") == 9221
    assert util.parse_count("2.75M subscribers") == 2_750_000
    assert util.parse_count(None) is None
    assert util.iso_utc(0) == "1970-01-01T00:00:00.000Z"
    assert util.iso_utc("1700000000", millis=False) == "2023-11-14T22:13:20Z"
    for junk in (10**20, -(10**20), float("nan"), float("inf"), "x", [1], {"a": 1}):  # fix14: was a 500
        assert util.iso_utc(junk) is None, junk
    assert util.clock(75) == "1:15" and util.clock(3675) == "1:01:15"
    assert util.clock(348, always_hours=True) == "00:05:48"


def test_parse_count_reads_a_number_too_big_to_hold_as_missing():
    """A count past a float's range ('999…9K views') or past the 4300 digits int() reads was a 500 (OverflowError,
    ValueError) wherever a listing or a post reads one. It reads as missing, and so do NaN and ±Infinity."""
    for junk in ("9" * 400 + "K views", "9" * 400 + ".5M", "9" * 5000, "9" * 5000 + " videos", "9" * 5000 + "M",
                 float("nan"), float("inf"), float("-inf")):
        assert util.parse_count(junk) is None, str(junk)[:20]
    assert util.parse_count("9" * 400 + " views") == int("9" * 400) and util.parse_count(10 ** 400) == 10 ** 400
    assert util.parse_count(1e308) == int(1e308) and util.parse_count(2.7) == 2


def test_times_too_big_to_hold_read_as_missing():
    """A time past a float's range was a 500 (OverflowError, ValueError): a json3 caption's start and length, and
    seconds of NaN or ±Infinity shown as a clock. It reads as missing."""
    seg = subtitles.Segment
    raw = json.dumps({"events": [{"segs": [{"utf8": "far"}], "tStartMs": 10 ** 400, "dDurationMs": 10 ** 400},
                                 {"segs": [{"utf8": "hi"}], "tStartMs": 1500, "dDurationMs": 2250}]})
    assert subtitles.parse(raw, "json3") == [seg(0.0, 0.0, "far"), seg(1.5, 3.75, "hi")]
    for junk in (float("nan"), float("inf"), float("-inf")):
        assert util.clock(junk) is None and util.clock(junk, always_hours=True) is None, junk


def test_character_references_too_big_to_read_read_as_their_text():
    """yt-html-entity-500: yt-dlp reads a character reference in a page's text ('&#38;' -> '&') with chr(), and one
    past Unicode's last character as its text, but not one past a C int ('&#2147483648;'): an OverflowError, so a
    500 wherever a page's text was read. It reads as its text, as one past Unicode does."""
    odd = "&#1114112; &#2147483648; &#99999999999999999999; &#x99999999999999999;"
    assert unescapeHTML(f"&#38; &#x2022; &amp; {odd}") == f"& \u2022 & {odd}"
    assert clean_html(f"<b>Odd</b> {odd}") == f"Odd {odd}"


def test_clean_param_drops_what_a_copied_value_carries_along():
    link = "https://youtu.be/jNQXAC9IVRw"
    for pasted in (f"\ufeff{link}", f"\u200b{link}\u200b", f"\u200e {link} \u200f", f"\u2068{link}\u2069",
                   f"\u202a{link}\u202c", f"\u00ad{link}\u2060", f"<{link}>", f" <{link}>\n", f"<\u200b{link}>"):
        assert util.clean_param(pasted) == link, ascii(pasted)
    for kept in ("<3", "<b>", "<br/>", f"a <{link}>", f"<{link} b>", f"<<{link}>>", "a\u200db", "", "jane"):
        assert util.clean_param(kept) == kept, kept
    england = "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"  # tag characters end it
    assert util.clean_param(f"\u200b{england}") == england
    started = time.perf_counter()  # scans, not backtracking regexes
    for long in (" " * 100_000 + "x", "x" + " " * 100_000 + "x", "<" + "a" * 100_000, "<" * 50_000):
        util.clean_param(long)
    assert time.perf_counter() - started < 1


def test_requestable_url():
    """URLs yt-dlp can request: it fails on the others before asking, with errors that aren't its own (a ValueError, a
    UnicodeError) or a RequestError that isn't a network error, which fatal=False lets through."""
    for url in ("https://v16-webapp.tiktok.com/a/b.vtt?x=1&y=%20", "http://127.0.0.1:8080/x", "https://[::1]/x",
                "https://\u00e9.example/x", "HTTPS://x.com/a b"):
        assert util.requestable_url(url), url
    for url in ("http://[", "https://[::1", f"https://{'a' * 64}.com/x", "https://x.com/\ud83d", "https://x:abc/",
                "https://x:99999/", "https:///x", "https://a b/c", "javascript:alert(1)", "file:///x.vtt", "x", "",
                None, 7, ["https://x.com/"]):
        assert not util.requestable_url(url), url


def test_sc_get_cleans_every_string_param(monkeypatch):
    from fastapi import APIRouter, FastAPI

    from socialscrape import registry

    monkeypatch.setattr(registry, "IMPLEMENTED", dict(registry.IMPLEMENTED))
    router = APIRouter()

    @registry.sc_get(router, "/v9/test/echo", source="test")
    async def echo(url: str, handle: str | None = None, cursor: str | None = None):
        return {"url": url, "handle": handle, "cursor": cursor}

    app = FastAPI()
    app.include_router(router)
    body = TestClient(app).get("/v9/test/echo", params={"url": "<https://youtu.be/x>", "handle": "\u200b@jane\u200e"})
    assert body.json() == {"success": True, "credits_charged": 0, "url": "https://youtu.be/x", "handle": "@jane",
                           "cursor": None}  # a param left out stays None


def test_sc_get_writes_what_json_cannot(monkeypatch):
    """json-render-500: upstream JSON as Python reads it can carry NaN, Infinity, a number too large for a float or
    half an emoji, none of which a response can write (ValueError, UnicodeEncodeError): a 500 wherever a raw upstream
    object passes through, as a TikTok video's stats or an Instagram post's play count do, or an error message
    quotes the platform. So was an int of more digits than Python writes out, as a length of 4300 digits (as many
    as it reads) is in ms. They are null, and U+FFFD."""
    from fastapi import APIRouter, FastAPI

    from socialscrape import registry
    from socialscrape.errors import ApiError, api_error_handler

    monkeypatch.setattr(registry, "IMPLEMENTED", dict(registry.IMPLEMENTED))
    router = APIRouter()
    raw = json.loads('{"stats": {"diggCount": NaN, "playCount": 1e999, "shareCount": -Infinity, "ratio": 0.5}, '
                     '"desc": "so good \\ud83d", "\\udc00": [1, 2.5], "emoji": "\\ud83d\\ude00"}')

    @registry.sc_get(router, "/v9/test/raw", source="test")
    async def raw_object():
        return {"aweme_detail": raw, "list": [float("nan"), (float("inf"), "\ud83d\ude00")],
                "durationMs": int("9" * 4300) * 1000, "count": int("9" * 4300)}

    @registry.sc_get(router, "/v9/test/gone", source="test")
    async def gone():
        raise NotFound(json.loads('"This video is unavailable \\ud83d"'))  # a player's reason, as yt-dlp quotes it

    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(router)
    client = TestClient(app)
    r = client.get("/v9/test/raw")
    assert r.status_code == 200 and r.json() == {"success": True, "credits_charged": 0, "aweme_detail": {
        "stats": {"diggCount": None, "playCount": None, "shareCount": None, "ratio": 0.5},
        "desc": "so good \ufffd", "\ufffd": [1, 2.5], "emoji": "\U0001f600"}, "list": [None, [None, "\U0001f600"]],
        "durationMs": None, "count": int("9" * 4300)}
    r = client.get("/v9/test/gone")
    assert r.status_code == 404 and r.json()["message"] == "This video is unavailable \ufffd"


@pytest.mark.parametrize("path,key", [
    ("/v1/instagram/user/reels", "handle"), ("/v1/instagram/user/highlight/detail", "id"),
    ("/v1/youtube/channel-videos", "continuationToken"), ("/v1/youtube/search", "query"),
    ("/v1/tiktok/song", "clipId"), ("/v1/tiktok/profile", "handle"),
])
def test_blank_value_answers_like_an_empty_one(client, path, key):
    empty = client.get(path, params={key: ""})
    assert empty.status_code == 400  # answered without a request upstream
    for blank in (" ", "\t\u200b ", "\ufeff"):
        r = client.get(path, params={key: blank})
        assert (r.status_code, r.json()) == (400, empty.json()), (ascii(blank), r.text)


def test_pasted_link_or_handle_reaches_each_platform_clean(client, monkeypatch):
    from socialscrape.platforms import instagram, tiktok, youtube_videos

    seen = []

    def record(value, *rest, **opts):
        seen.append(value)
        raise NotFound("stubbed")

    async def record_async(value, *rest, **opts):
        record(value)

    monkeypatch.setattr(youtube_videos, "extract_watch", lambda ydl, vid, process=True: record(vid))
    monkeypatch.setattr(tiktok, "_user_page", lambda ydl, ident: record(ident))
    monkeypatch.setattr(instagram, "fetch_profile", record_async)
    for path, key, value in (
            ("/v1/youtube/video", "url", "\ufeffhttps://www.youtube.com/watch?v=jNQXAC9IVRw"),
            ("/v1/youtube/video", "url", "<https://youtu.be/jNQXAC9IVRw>"),
            ("/v1/youtube/video", "url", "\u2068https://youtu.be/jNQXAC9IVRw\u2069"),
            ("/v1/youtube/video", "url", "\u200bjNQXAC9IVRw"),
            ("/v1/tiktok/profile", "handle", "\ufeff@stoolpresidente"),
            ("/v1/tiktok/profile", "handle", "<https://www.tiktok.com/@stoolpresidente>"),
            ("/v1/instagram/profile", "handle", "\u200bjane"),
            ("/v1/instagram/profile", "handle", "<https://www.instagram.com/jane/>")):
        r = client.get(path, params={key: value})
        assert r.status_code == 404, (ascii(value), r.text)
    assert seen == ["jNQXAC9IVRw"] * 4 + ["stoolpresidente"] * 2 + ["jane"] * 2


def test_error_translation():
    def wrap(msg):
        try:
            raise ExtractorError(msg, expected=True)
        except ExtractorError as e:
            return DownloadError("ERROR: " + msg, exc_info=(type(e), e, None))

    assert translate_error(wrap("Video unavailable. This video is private")).status == 403
    assert translate_error(wrap("Video unavailable")).status == 404
    assert translate_error(wrap("Sign in to confirm you’re not a bot. Use --cookies")).status == 503
    assert translate_error(wrap("Sign in to confirm your age")).status == 403
    assert translate_error(wrap("Something odd")).status == 500

    try:
        InfoExtractor(YoutubeDL({"quiet": True}))._parse_json("", "x")
    except ExtractorError as e:
        assert translate_error(e).status == 503


def test_pick_track():
    subs = {"en": [{}], "de": [{}]}
    auto = {"en": [{}], "fr": [{}], "es-orig": [{}]}
    assert subtitles.pick_track(subs, auto) == ("en", False)
    assert subtitles.pick_track(subs, auto, language="de") == ("de", False)
    assert subtitles.pick_track(subs, auto, language="fr") == ("fr", True)
    assert subtitles.pick_track(subs, auto, language="ja") is None
    assert subtitles.pick_track({}, auto, original=True) == ("es-orig", True)
    assert subtitles.pick_track({}, {}) is None


def test_pick_track_auto_dubbed_video():
    """An auto-dubbed video has one '-orig' ASR track per audio track (yt_videos-2: mx7MdPdn_a8 lists the English
    dub's first): the original audio language picks among them, and without it none counts as spoken."""
    auto = {"en-US-orig": [{}], "en-US": [{}], "ja-orig": [{}], "ja": [{}], "en": [{}], "de": [{}]}
    translated = {"ja", "en", "de"}
    for kwargs in ({}, {"original": True}):
        assert subtitles.pick_track({}, auto, video_language="ja", translated=translated, **kwargs) == ("ja-orig", True)
        assert subtitles.pick_track({}, auto, video_language="en-US", translated=translated, **kwargs) == (
            "en-US-orig", True)
    # unknown original audio: no '-orig' track is taken for the spoken one; English is the documented fallback
    assert subtitles.pick_track({}, auto, translated=translated) == ("en-US", True)
    assert subtitles.pick_track({}, auto, original=True, translated=translated) == ("en-US", True)
    assert subtitles.pick_track({}, {"ja-orig": [{}], "fr-orig": [{}]}) == ("ja-orig", True)  # nothing else left
    assert subtitles.pick_track({}, {"ja-orig": [{}], "ja": [{}], "en": [{}]}, translated={"en"}) == ("ja-orig", True)


def test_pick_track_prefers_a_real_track_to_a_machine_translation():
    """yt_videos-3: language=en took YouTube's translated 'en' (tlang=en, throttled anonymously) over the real
    'en-US' ASR track."""
    auto = {"en": [{}], "en-US-orig": [{}], "en-US": [{}], "ja-orig": [{}]}
    assert subtitles.pick_track({}, auto, language="en", translated={"en"}) == ("en-US", True)
    assert subtitles.pick_track({}, auto, language="en") == ("en", True)  # nothing known to be translated
    assert subtitles.pick_track({}, auto, language="en-US", translated={"en"}) == ("en-US", True)
    assert subtitles.pick_track({"en-GB": [{}]}, auto, language="en", translated={"en"}) == ("en-GB", False)
    # only a translation: still the answer (the caller decides what a throttled translation means)
    assert subtitles.pick_track({}, {"fr": [{}], "es-orig": [{}]}, language="fr", translated={"fr"}) == ("fr", True)
    # last resort 'any auto' is a real track too, and a translated key never counts as the spoken language
    assert subtitles.pick_track({}, {"de": [{}], "ko": [{}]}, translated={"de"}) == ("ko", True)
    assert subtitles.pick_track({}, {"ja": [{}], "ko": [{}]}, video_language="ja", translated={"ja"},
                                original=True) == ("ko", True)
    tracks = {"en": [{"url": "https://www.youtube.com/api/timedtext?lang=ja&tlang=en&fmt=json3"}],
              "ja": [{"url": "https://www.youtube.com/api/timedtext?lang=ja&fmt=json3"},
                     {"url": "https://www.youtube.com/api/timedtext?lang=en-US&tlang=ja&fmt=json3"}],
              "ja-orig": [{"url": "https://www.youtube.com/api/timedtext?lang=ja&fmt=json3"}], "ko": [{}]}
    assert subtitles.machine_translated(tracks) == {"en", "ja"} and subtitles.machine_translated({}) == set()


def test_pick_track_uploaded_track_is_never_a_machine_translation():
    """retest-cross-1: `translated` ranked uploaded tracks too, so a TED talk's uploaded 'fr' lost to 'fr-CA' (and
    'pt-PT' to 'pt-BR') because YouTube also offers tlang=fr / tlang=pt-PT translations of its English ASR."""
    def tlang(code: str) -> list[dict]:
        return [{"url": f"https://www.youtube.com/api/timedtext?lang=en&tlang={code}&fmt=json3"}]

    asr = [{"url": "https://www.youtube.com/api/timedtext?lang=en&kind=asr&fmt=json3"}]
    up = {"en": [{}], "fr": [{}], "fr-CA": [{}], "pt-BR": [{}], "pt-PT": [{}], "zh-Hans": [{}], "zh-TW": [{}]}
    auto = {"en-orig": asr, "en": asr, **{k: tlang(k) for k in ("fr", "pt-PT", "zh-Hans", "de")}}
    translated = subtitles.machine_translated(auto)
    assert translated == {"fr", "pt-PT", "zh-Hans", "de"}
    for want in ("fr", "fr-CA", "pt-PT", "pt-BR", "zh-Hans", "zh-TW", "en"):
        assert subtitles.pick_track(up, auto, language=want, translated=translated) == (want, False), want
    assert subtitles.pick_track(up, auto, translated=translated) == ("en", False)  # default: uploaded English
    assert subtitles.pick_track(up, auto, language="de", translated=translated) == ("de", True)  # only a translation
    # Korean ASR with uploaded en + en-GB and a tlang=en: the uploaded 'en' still wins over en-GB
    ko = {"ko-orig": asr, "ko": asr, "en": tlang("en")}
    up_en = {"en": [{}], "en-GB": [{}]}
    for kwargs in ({}, {"language": "en"}):
        assert subtitles.pick_track(up_en, ko, translated=subtitles.machine_translated(ko), **kwargs) == ("en", False)


def test_pick_track_keeps_the_script_of_a_chinese_variant():
    """fix3: TED talks upload zh-CN and zh-TW, and language=zh-Hant or zh-HK took zh-CN: a match that isn't exact
    went by YouTube's list order alone, so a request for Traditional Chinese got Simplified."""
    ted = {"en": [{}], "zh-CN": [{}], "zh-TW": [{}]}
    for want in ("zh-Hant", "zh-HK", "zh-MO", "zh-Hant-HK", "ZH-hk"):
        assert subtitles.pick_track(ted, {}, language=want) == ("zh-TW", False), want
    for want in ("zh-Hans", "zh-SG", "zh-Hans-HK"):
        assert subtitles.pick_track({"zh-TW": [{}], "zh-CN": [{}]}, {}, language=want) == ("zh-CN", False), want
    scripts = {"zh-Hans": [{}], "zh-Hant": [{}]}
    assert subtitles.pick_track(scripts, {}, language="zh-TW") == ("zh-Hant", False)
    assert subtitles.pick_track({}, scripts, language="zh-HK") == ("zh-Hant", True)
    assert subtitles.pick_track({}, {"zh-Hans-orig": [{}], "zh-Hant": [{}]}, language="zh-TW") == ("zh-Hant", True)
    # a tag that doesn't say its script comes before the other script; a plain 'zh' keeps YouTube's order
    assert subtitles.pick_track({"zh-CN": [{}], "zh": [{}]}, {}, language="zh-TW") == ("zh", False)
    assert subtitles.pick_track(scripts, {}, language="zh") == ("zh-Hans", False)
    assert subtitles.pick_track({"sr-Latn": [{}], "sr": [{}]}, {}, language="sr-Cyrl") == ("sr", False)
    # the script only ranks tracks of one tier: an uploaded Simplified track still beats a translation
    hant = [{"url": "https://www.youtube.com/api/timedtext?lang=en&tlang=zh-Hant&fmt=json3"}]
    assert subtitles.pick_track({"zh-CN": [{}]}, {"zh-Hant": hant}, language="zh-Hant",
                                translated=subtitles.machine_translated({"zh-Hant": hant})) == ("zh-CN", False)
    # region and script subtags of other languages change nothing: list order decides among en-GB and en
    assert subtitles.pick_track({"en-GB": [{}], "en": [{}]}, {}, language="en-US") == ("en-GB", False)
    assert subtitles.pick_track({}, {"en-orig": [{}], "en": [{}]}, language="en-Latn") == ("en", True)


def test_pick_track_finds_a_track_under_youtubes_own_code():
    """fix3: YouTube keys Hebrew captions 'iw', Norwegian 'no' and Filipino 'fil' (a TED talk lists all three), so
    language=he, nb or tl found no track and the transcript was null; so did a 3-letter code such as eng."""
    up = {k: [{}] for k in ("en", "fil", "id", "iw", "no")}
    for want, key in (("he", "iw"), ("iw", "iw"), ("heb", "iw"), ("nb", "no"), ("nob", "no"), ("tl", "fil"),
                      ("fil", "fil"), ("in", "id"), ("eng", "en"), ("EN", "en")):
        assert subtitles.pick_track(up, {}, language=want) == (key, False), want
    for want, key in (("ji", "yi"), ("jw", "jv"), ("he-IL", "iw")):
        assert subtitles.pick_track({}, {"iw": [{}], "jv": [{}], "yi": [{}]}, language=want) == (key, True), want


def test_pick_track_takes_the_other_norwegian():
    """Nynorsk ('nn') and Bokmål ('no', 'nb') found no track in the other; Persian's 'per' found none either."""
    tracks = lambda *keys: {k: [{}] for k in keys}  # noqa: E731
    for want in ("no", "nb", "nob", "nor"):
        assert subtitles.pick_track(tracks("en", "nn"), {}, language=want) == ("nn", False), want
        assert subtitles.pick_track({}, tracks("nn-NO"), language=want) == ("nn-NO", True), want
    assert subtitles.pick_track(tracks("en", "no"), {}, language="nn") == ("no", False)
    assert subtitles.pick_track(tracks("nn", "nb"), {}, language="no") == ("nb", False)  # its own first
    assert subtitles.pick_track(tracks("no"), tracks("nn"), language="nn") == ("nn", True)  # even auto-generated
    assert subtitles.pick_track(tracks("en"), tracks("da"), language="nn") is None
    assert subtitles.pick_track(tracks("en", "fa"), {}, language="per") == ("fa", False)


def test_parse_timed_text():
    """srv2 times (milliseconds) were read as seconds, a TTML cue's end as its duration (0), and a time that isn't
    one number ('1.2.3') was a 500."""
    seg = subtitles.Segment
    srv1 = '<transcript><text start="1.5" dur="2.25">Hi &amp; bye</text><text start="1.2.3" dur="">odd</text>'
    assert subtitles.parse(srv1, "srv1") == [seg(1.5, 3.75, "Hi & bye"), seg(0.0, 0.0, "odd")]
    srv2 = '<timedtext><body><text t="1500" d="2250" w="1">Hi <s>there</s></text></body></timedtext>'
    assert subtitles.parse(srv2, "srv2") == [seg(1.5, 3.75, "Hi there")]
    srv3 = '<timedtext format="3"><body><p t="1500" d="2250" w="1"><s ac="0">Hey</s></p><p t="." d="1">x</p>'
    assert subtitles.parse(srv3, "srv3") == [seg(1.5, 3.75, "Hey"), seg(0.0, 0.001, "x")]
    ttml = '<tt><body><div><p begin="00:00:01.500" end="00:00:03.750" style="s2">Yo</p><p begin="0:05.000">x</p>'
    assert subtitles.parse(ttml, "ttml") == [seg(1.5, 3.75, "Yo"), seg(5.0, 5.0, "x")]
    assert subtitles.parse(f'<text start="{"9" * 400}" dur="1">big</text>', "srv1") == [seg(0.0, 1.0, "big")]
    assert subtitles.parse("9" * 5000 + ":00:00.000 --> 00:00:01.000\nlong", "vtt")[0].text == "long"


def test_parse_captions_reads_a_long_character_reference():
    """A decimal character reference of over 4300 digits failed html.unescape in VTT, SRT, srv and TTML captions: it
    int()s the digits, and int() refuses that many (a ValueError, so a 500). The digits that count are read."""
    seg = subtitles.Segment
    text = "A&#" + "0" * 5000 + "38;B &#" + "9" * 5000 + ";"
    for raw, ext in ((f"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n{text}", "vtt"),
                     (f"1\n00:00:01,000 --> 00:00:02,000\n{text}", "srt"),
                     (f'<text start="1" dur="1">{text}</text>', "srv1"), (f'<p t="1000" d="1000">{text}</p>', "srv3"),
                     (f'<p begin="00:00:01.000" end="00:00:02.000">{text}</p>', "ttml")):
        assert subtitles.parse(raw, ext) == [seg(1.0, 2.0, "A&B \ufffd")], ext
    odd = "&#38; &#0038 &#x26; &#1114112; &#128; &#0; &#x0041; &#٣٨;"  # as html.unescape reads them
    assert subtitles._unescape(odd) == "& & & \ufffd \u20ac \ufffd A &#٣٨;"


def test_parse_json3_skips_what_isnt_a_caption():
    """JSON that isn't an object, null events or segments, and times that aren't numbers were a 500 (or lost the
    whole transcript). So was JSON nested deeper than Python reads it (a RecursionError, not a ValueError)."""
    for raw in ("[1, 2]", "null", '"x"', '{"events": 5}', '{"events": [null, 1, {"segs": null}, {"segs": {}}]}',
                '{"events": ' + "[" * 100_000 + "]" * 100_000 + "}"):
        assert subtitles.parse(raw, "json3") == [], raw[:40]
    raw = json.dumps({"events": [{"segs": [None, {"utf8": None}, {"utf8": 5}, {"utf8": "a"}], "tStartMs": "x"},
                                 {"segs": [{"utf8": "b"}], "tStartMs": 1500, "dDurationMs": True},
                                 {"segs": [{"utf8": "c"}], "tStartMs": float("nan"), "dDurationMs": 1e400}]})
    seg = subtitles.Segment
    assert subtitles.parse(raw, "json3") == [seg(0.0, 0.0, "a"), seg(1.5, 1.5, "b"), seg(0.0, 0.0, "c")]


def test_parse_vtt_dedupes_rolling_captions():
    raw = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n\n00:00:01.000 --> 00:00:02.000\nhello\nworld\n"
    segs = subtitles.parse(raw, "vtt")
    assert [s.text for s in segs] == ["hello", "world"]
    assert segs[1].start == 1.0


# --- HTTP layer: error envelope, auth, OpenAPI ---------------------------------------


def _envelope(r, status: int, code: str) -> dict:
    assert r.status_code == status, (r.status_code, r.text[:200])
    assert r.headers["content-type"].startswith("application/json"), r.headers["content-type"]
    body = r.json()
    assert body["success"] is False and body["error"] == code and body["message"], body
    return body


def test_unexpected_exception_uses_envelope(monkeypatch):
    from socialscrape import main
    from socialscrape.config import get_settings

    get_settings.cache_clear()
    app = main.create_app()

    @app.get("/v9/test/boom")
    async def boom():
        raise RuntimeError("secret detail")

    c = TestClient(app, raise_server_exceptions=False)
    body = _envelope(c.get("/v9/test/boom"), 500, "server_error")
    assert "secret detail" not in body["message"]  # internals stay in the server log

    def broken_settings():  # also caught when the failure is in a middleware (auth runs first)
        raise ValueError("bad setting")

    monkeypatch.setattr(main, "get_settings", broken_settings)
    _envelope(c.get("/v1/youtube/shorts/trending"), 500, "server_error")


def test_non_ascii_api_key_header_is_401(monkeypatch):
    # compare_digest raises on a non-ASCII str, which made such a header a 500
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "k1,k2")
    get_settings.cache_clear()
    try:
        c = TestClient(create_app(), raise_server_exceptions=False)
        for header in ("é".encode(), "k1é".encode(), "é".encode("latin-1"), "密".encode(), b"wrong", None):
            r = c.get("/v1/youtube/shorts/trending", headers={"x-api-key": header} if header else {})
            _envelope(r, 401, "unauthorized")
        assert c.get("/v1/youtube/shorts/trending", headers={"x-api-key": "k1"}).status_code == 501
    finally:
        monkeypatch.delenv("SOCIALSCRAPE_API_KEYS")
        get_settings.cache_clear()


def test_api_key_header_whitespace_is_ignored(monkeypatch):
    # gap-core-api-key-trailing-ows: uvicorn's httptools keeps trailing spaces/tabs in a header value, and the
    # untrimmed value could never match the (stripped) configured keys
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", " k1 , k2")
    get_settings.cache_clear()
    try:
        c = TestClient(create_app())
        for header in ("k1 ", " k1", "  k1\t", "k1\t", "\tk2 "):
            assert c.get("/v1/youtube/shorts/trending", headers={"x-api-key": header}).status_code == 501, header
        for header in ("k1x", "k 1", "k1\x0b", "k1,k2", " "):  # only SP/HTAB around the value are dropped
            _envelope(c.get("/v1/youtube/shorts/trending", headers={"x-api-key": header}), 401, "unauthorized")
    finally:
        get_settings.cache_clear()


def test_deeply_nested_cursor_is_400(client):
    import base64

    nested = cursor.PREFIX + base64.urlsafe_b64encode(b"[" * 10_000 + b"]" * 10_000).decode().rstrip("=")
    with pytest.raises(BadRequest) as exc:
        cursor.decode_cursor(nested)
    assert exc.value.code == "bad_cursor"
    for path, params in (("/v1/youtube/channel-videos", {}), ("/v1/youtube/search", {"query": "x"})):
        _envelope(client.get(path, params={**params, "continuationToken": nested}), 400, "bad_cursor")


@pytest.mark.parametrize("path", ["/v1/youtube/no-such-endpoint", "/v9/foo", "/", "/V1/youtube/video"])
def test_unknown_path_uses_envelope(client, path):
    body = _envelope(client.get(path), 404, "not_found")
    assert "/meta/coverage" in body["message"]


@pytest.mark.parametrize("method,path,allow", [
    ("POST", "/v1/tiktok/profile", "GET"), ("DELETE", "/v1/youtube/shorts/trending", "GET"),
    ("OPTIONS", "/v1/tiktok/profile", "GET"), ("PUT", "/health", "GET, HEAD"),
    ("HEAD", "/v1/youtube/shorts/trending", "GET"), ("POST", "/meta/coverage", "GET"),
    ("DELETE", "/meta/coverage", "GET"), ("PATCH", "/meta/coverage", "GET"), ("POST", "/docs", "GET, HEAD"),
])
def test_wrong_method_is_405_envelope(client, method, path, allow):
    r = client.request(method, path)
    assert r.status_code == 405 and r.headers["allow"] == allow  # every allowed method, in a fixed order
    if method != "HEAD":  # no body on HEAD
        assert _envelope(r, 405, "method_not_allowed")["message"] == f"Method {method} not allowed here; use {allow}"


def test_head_on_health(client):
    r = client.head("/health")
    assert r.status_code == 200 and not r.content


def test_root_is_not_public(monkeypatch):
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "k1")
    get_settings.cache_clear()
    try:
        c = TestClient(create_app())
        _envelope(c.get("/"), 401, "unauthorized")
        _envelope(c.get("/", headers={"x-api-key": "k1"}), 404, "not_found")
        assert c.head("/health").status_code == 200
    finally:
        monkeypatch.delenv("SOCIALSCRAPE_API_KEYS")
        get_settings.cache_clear()


@pytest.mark.parametrize("path", ["/v1/youtube/search", "/v1/instagram/search/popular", "/v2/instagram/reels/search"])
def test_missing_query_param_is_named(client, path):
    assert _envelope(client.get(path), 400, "bad_request")["message"] == "query: Field required"


def test_openapi_declares_api_key_scheme(client):
    spec = client.app.openapi()
    assert spec["components"]["securitySchemes"]["apiKeyAuth"]["type"] == "apiKey"
    assert {k: spec["components"]["securitySchemes"]["apiKeyAuth"][k] for k in ("in", "name")} == {
        "in": "header", "name": "x-api-key"}  # same scheme as ScrapeCreators' spec
    for e in CATALOG:  # implemented and 501 routes alike; the header is not a query/header param
        op = spec["paths"][e["path"]]["get"]
        assert op["security"] == [{"apiKeyAuth": []}], e["path"]
        assert "x-api-key" not in {p["name"] for p in op.get("parameters", [])}, e["path"]
    assert "security" not in spec["paths"]["/meta/coverage"]["get"]  # public


def test_openapi_lists_cache_max_age(client):
    spec = client.app.openapi()
    status = {r["path"]: r["status"] for r in client.get("/meta/coverage").json()["endpoints"]}
    for e in CATALOG:
        names = [p["name"] for p in spec["paths"][e["path"]]["get"].get("parameters", [])]
        if status[e["path"]] == "implemented":  # CacheMiddleware serves it on every implemented GET
            assert names.count("cache_max_age") == 1, e["path"]
        else:
            assert "cache_max_age" not in names, e["path"]
    assert {e["path"] for e in CATALOG if "cache_max_age" in e["params"]} - {
        p for p, s in status.items() if s == "implemented"} == {"/v1/instagram/basic-profile"}  # a 501


def test_openapi_documents_the_error_envelope(client):
    # gap-core-openapi-422-schema: every operation with params documented FastAPI's 422 HTTPValidationError, which
    # is never sent (a bad or missing param is a 400 in the envelope), and none of the errors that are sent
    spec = client.app.openapi()
    assert "HTTPValidationError" not in str(spec)
    envelope = spec["components"]["schemas"]["ErrorResponse"]
    assert set(envelope["required"]) == {"success", "error", "message"} and envelope["additionalProperties"] is True
    status = {r["path"]: r["status"] for r in client.get("/meta/coverage").json()["endpoints"]}
    errors = {"400", "401", "403", "404", "500", "501", "502", "503", "default"}
    for e in CATALOG:
        responses = spec["paths"][e["path"]]["get"]["responses"]
        if status[e["path"]] == "implemented":
            assert set(responses) - {"422"} == {"200", *errors}, e["path"]
        else:  # what a 501 route sends: 501, or 400 (bad cache_max_age) and 401 (auth) before it
            assert set(responses) == {"400", "401", "501"}, e["path"]
        for code, r in responses.items():
            if code != "200":
                assert r["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
    # the one 422 that is sent: post-count's estimate_only
    assert [p for p, ops in spec["paths"].items() if "422" in ops.get("get", {}).get("responses", {})] == [
        "/v1/instagram/profile/post-count"]
    body = client.get("/v1/tiktok/profile").json()  # what the documented 400 looks like
    assert body["success"] is False and set(envelope["required"]) <= body.keys()


# --- settings, CLI, cache key ------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    """`env(NAME=value, ...)`: set variables and re-read the settings (fresh yt-dlp pool; restored afterwards)."""
    from socialscrape import ytdlp
    from socialscrape.config import get_settings

    def set_env(**values: str):
        for k, v in values.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        return get_settings

    monkeypatch.setattr(ytdlp, "_executor", None)
    yield set_env
    get_settings.cache_clear()


@pytest.mark.parametrize("name,value,says", [
    ("SOCIALSCRAPE_MAX_WORKERS", "abc", "must be an integer"), ("SOCIALSCRAPE_MAX_WORKERS", "2.5", "must be an integer"),
    ("SOCIALSCRAPE_MAX_WORKERS", "0", "at least 1"), ("SOCIALSCRAPE_MAX_WORKERS", "-3", "at least 1"),
    ("SOCIALSCRAPE_REQUEST_TIMEOUT", "0", "greater than 0"), ("SOCIALSCRAPE_REQUEST_TIMEOUT", "-5", "greater than 0"),
    ("SOCIALSCRAPE_REQUEST_TIMEOUT", "abc", "must be a number"), ("SOCIALSCRAPE_SOCKET_TIMEOUT", "nan", "finite"),
    ("SOCIALSCRAPE_SOCKET_TIMEOUT", "inf", "finite"), ("SOCIALSCRAPE_TRANSCRIBE", "off", "must be one of"),
    ("SOCIALSCRAPE_TRANSCRIBE", "whisper", "must be one of"), ("SOCIALSCRAPE_PROXY", "bogus://u:secret@h:1", "scheme"),
    # retest-runtime-proxy-url-leak: an allowed scheme with an unusable host/port started, then every request
    # failed with the URL (password included) in the response
    *[("SOCIALSCRAPE_PROXY", value, "valid host") for value in (
        "http://qauser:secret@127.0.0.1:99999", "http://127.0.0.1:notaport", "http://[::1", "http://u:secret@[::1",
        "http://", "http://:8080", "socks5://u:secret@:1080", "http://127.0.0.1:0", "u:secret", "http://ho st:80")],
    # HTTP clients send a non-ASCII key as UTF-8 or latin-1 bytes, or can't send it at all
    ("SOCIALSCRAPE_API_KEYS", "k1,secret-é", "must be ASCII"),
    ("SOCIALSCRAPE_API_KEYS", "secret-密钥", "must be ASCII"),
])
def test_bad_setting_is_rejected_by_name(env, name, value, says):
    # core-settings-unvalidated: these used to start fine and fail every request (or hang/crash later)
    from socialscrape.config import ConfigError

    with pytest.raises(ConfigError, match=name) as exc:
        env(**{name: value})()
    assert says in str(exc.value) and "secret" not in str(exc.value)  # a proxy URL's credentials stay out


def test_settings_accept_and_normalise(env):
    s = env(SOCIALSCRAPE_MAX_WORKERS=" 3 ", SOCIALSCRAPE_REQUEST_TIMEOUT="0.5", SOCIALSCRAPE_TRANSCRIBE="MLX-Whisper",
            SOCIALSCRAPE_PROXY="127.0.0.1:8080", SOCIALSCRAPE_SOCKET_TIMEOUT="")()
    assert (s.max_workers, s.request_timeout, s.socket_timeout) == (3, 0.5, 20.0)
    assert s.transcribe_backend == "mlx-whisper" and s.proxy == "127.0.0.1:8080"  # scheme-less proxy: http
    assert env(SOCIALSCRAPE_PROXY="SOCKS5H://127.0.0.1:1080")().proxy == "SOCKS5H://127.0.0.1:1080"
    for proxy in ("http://[::1]:8080", "u:p@proxy.example:3128", "//127.0.0.1:8080", "http://u:p@h:1/x",
                  "1.2.3", "http://proxy.example", "socks5h://tor"):  # no port: the scheme's default, as documented
        assert env(SOCIALSCRAPE_PROXY=proxy)().proxy == proxy


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_cache_path_disables_cache(env, value, monkeypatch):
    # core-cache-path-empty: blank used to mean the default file, so the cache could never be turned off
    from socialscrape.config import DEFAULT_CACHE_PATH
    from socialscrape.main import create_app

    assert env(SOCIALSCRAPE_CACHE_PATH=value)().cache_path == ""
    assert TestClient(create_app()).get("/health").json()["cache"] is False
    monkeypatch.delenv("SOCIALSCRAPE_CACHE_PATH")
    assert env()().cache_path == DEFAULT_CACHE_PATH


def test_unusable_cache_path_stops_startup(env, tmp_path):
    # gap-core-cache-path-unusable-500: Starlette builds the middleware (which opened the cache) on the first
    # request, so a directory or a file that isn't SQLite made every request a text/plain 500, /health included
    from socialscrape.cache import ResponseCache
    from socialscrape.config import ConfigError
    from socialscrape.main import create_app

    (tmp_path / "garbage").write_bytes(b"not a database\n" * 100)
    (tmp_path / "file").write_text("x")
    # Another program's responses table (requests-cache's) started, then failed every read and write with a warning
    foreign = sqlite3.connect(tmp_path / "http_cache.sqlite")
    foreign.execute("CREATE TABLE responses (key TEXT PRIMARY KEY, value BLOB, expires INTEGER)")
    foreign.commit()
    foreign.close()
    bad = [tmp_path, tmp_path / "garbage", tmp_path / "file" / "c.sqlite3", tmp_path / "http_cache.sqlite"]
    read_only, ro_dir = tmp_path / "ro.sqlite3", tmp_path / "ro-dir"
    ResponseCache(str(read_only)).close()
    ro_dir.mkdir()
    ResponseCache(str(ro_dir / "c.sqlite3")).close()
    if os.geteuid() != 0:  # root writes anyway
        # Both open and read fine: only a write shows it (WAL mode can't create its -wal file in a read-only dir)
        bad += [read_only, ro_dir / "c.sqlite3", ro_dir / "new.sqlite3"]
        read_only.chmod(0o444)
        ro_dir.chmod(0o555)
    try:
        for path in bad:
            env(SOCIALSCRAPE_CACHE_PATH=str(path))
            with pytest.raises(ConfigError, match="SOCIALSCRAPE_CACHE_PATH") as exc:
                create_app()
            assert str(path) in str(exc.value) and "\n" not in str(exc.value)
    finally:
        ro_dir.chmod(0o755)
    for path in (tmp_path / "new" / "dirs" / "c.sqlite3", tmp_path / "new" / "dirs" / "c.sqlite3"):  # new, reopened
        env(SOCIALSCRAPE_CACHE_PATH=str(path))
        assert TestClient(create_app()).get("/health").json()["cache"] is True and path.is_file()


def test_cache_failure_after_startup_serves_live(tmp_path, caplog):
    # A cache that breaks later (disk full, I/O error) is skipped with a warning instead of failing the request
    from fastapi import APIRouter, FastAPI

    from socialscrape.cache import CacheMiddleware, ResponseCache
    from socialscrape.registry import sc_get

    router = APIRouter()
    calls = {"n": 0}

    @sc_get(router, "/v9/test/flaky-cache", source="test")
    async def thing():
        calls["n"] += 1
        return {"n": calls["n"]}

    cache = ResponseCache(str(tmp_path / "c.sqlite3"))
    app = FastAPI()
    app.add_middleware(CacheMiddleware, cache=cache)
    app.include_router(router)
    c = TestClient(app)
    assert c.get("/v9/test/flaky-cache").json()["n"] == 1
    assert c.get("/v9/test/flaky-cache?cache_max_age=1d").json()["cached"] is True
    cache.close()  # every statement now raises sqlite3.ProgrammingError, as a failing disk raises OperationalError
    r = c.get("/v9/test/flaky-cache?cache_max_age=1d")
    assert r.status_code == 200 and r.json()["n"] == 2 and "cached" not in r.json()
    assert "cache read failed" in caplog.text and "cache write failed" in caplog.text


def test_cache_rows_it_did_not_write_are_served_live(tmp_path, caplog):
    # A row another program or a hand edit left in the table was a 500 on every request for it with cache_max_age:
    # it's skipped with a warning, as a failing cache is, and the live response replaces it
    from fastapi import APIRouter, FastAPI

    from socialscrape.cache import CacheMiddleware, ResponseCache
    from socialscrape.registry import sc_get

    router = APIRouter()
    calls = {"n": 0}

    @sc_get(router, "/v9/test/foreign-row", source="test")
    async def thing():
        calls["n"] += 1
        return {"n": calls["n"]}

    cache = ResponseCache(str(tmp_path / "c.sqlite3"))
    app = FastAPI()
    app.add_middleware(CacheMiddleware, cache=cache)
    app.include_router(router)
    c = TestClient(app)

    def seed(body, stored_at=None):
        cache._conn.execute("REPLACE INTO responses VALUES ('/v9/test/foreign-row?', ?, ?)",
                            (time.time() if stored_at is None else stored_at, body))
        cache._conn.commit()

    unreadable = [("[1, 2]",), ('"text"',), ("7",), ("null",), ("{",), (b'{"a": "\xff"}',),  # not UTF-8
                  ('{"a": ' + "9" * 5000 + "}",),  # more digits than Python reads
                  ("[" * 100_000 + "]" * 100_000,),  # nested deeper than Python reads
                  ('{"a": 1}', "an hour ago"), ('{"a": 1}', b"\x00"),  # stored_at: text, a blob
                  ('{"a": 1}', 1e20), ('{"a": 1}', float("inf"))]  # past what a datetime holds
    for row in unreadable:
        seed(*row)
        n = calls["n"]
        r = c.get("/v9/test/foreign-row?cache_max_age=1d")
        assert r.status_code == 200 and r.json()["n"] == n + 1 and r.headers["x-cache"] == "miss", repr(row)[:60]
        again = c.get("/v9/test/foreign-row?cache_max_age=1d")
        assert again.headers["x-cache"] == "hit" and again.json()["n"] == n + 1, repr(row)[:60]
    assert "the row for /v9/test/foreign-row? isn't a stored response" in caplog.text
    # A JSON object is replayed, what JSON can't write in it written as the routes write it
    replayed = {'{"a": NaN, "b": -Infinity}': {"a": None, "b": None}, '{"a": "\\ud83d"}': {"a": "\ufffd"}}
    for body, written in replayed.items():
        seed(body)
        r = c.get("/v9/test/foreign-row?cache_max_age=1d")
        assert r.status_code == 200 and r.headers["x-cache"] == "hit" and r.json()["cached"] is True, body
        assert {k: r.json()[k] for k in written} == written, body


@pytest.mark.parametrize("cache_path", ["", "cache.sqlite3"])
def test_bad_cache_max_age_is_400_with_or_without_the_cache(env, cache_path, tmp_path):
    # Only CacheMiddleware checked cache_max_age, so with the cache off cache_max_age=abc was ignored (200)
    from socialscrape.main import create_app

    env(SOCIALSCRAPE_CACHE_PATH=str(tmp_path / cache_path) if cache_path else "")
    c = TestClient(create_app())
    wrong_platform = {"url": "https://www.tiktok.com/@x/video/1"}  # rejected before any upstream request
    # gap-core-cache-age-huge-500: more digits than int() converts was a 500 server_error. An age is 1-6 ASCII
    # digits: Arabic-Indic and fullwidth digits ('\u0667d', '\uff17d') used to count as 7 days.
    for value in ("abc", "7x", "1.5d", "1" * 5000 + "d", "0" * 4400 + "1h", "9" * 4000 + "d", "1234567d", "\u0667d",
                  "\uff17d"):
        r = c.get("/v1/youtube/video", params={**wrong_platform, "cache_max_age": value})
        assert r.status_code == 400 and "cache_max_age" in r.json()["message"], (value[:20], r.json())
        # gap-instagram-5: with the cache off the 501 routes ignored it (CacheMiddleware rejects it when on)
        r = c.get("/v1/instagram/basic-profile", params={"cache_max_age": value})
        assert r.status_code == 400 and "cache_max_age" in r.json()["message"], (value[:20], r.json())
    # a valid (or blank) value reaches the handler, once the invisible ends a copy carries are cut
    for value in ("7d", "12h", "", "999999h", "7d\u200b", "\u200b"):
        r = c.get("/v1/youtube/video", params={**wrong_platform, "cache_max_age": value})
        assert r.status_code == 400 and "cache_max_age" not in r.json()["message"], (value[:20], r.json())
        assert c.get("/v1/instagram/basic-profile", params={"cache_max_age": value}).status_code == 501
    r = c.get("/v1/instagram/basic-profile", params=[("cache_max_age", "1d"), ("cache_max_age", "bogus")])
    assert r.status_code == 400  # the last value counts, as on the implemented routes
    # With the cache off, an unknown path was a 404 and a trailing slash a 307 here (400s with the cache on)
    for path in ("/v1/unknown", "/v1/youtube/video/"):
        r = c.get(path, params={"cache_max_age": "zz"}, follow_redirects=False)
        assert _envelope(r, 400, "bad_request")["message"].startswith("Invalid cache_max_age"), path


def test_create_app_fails_fast_on_bad_setting(env):
    from socialscrape.config import ConfigError
    from socialscrape.main import create_app

    env(SOCIALSCRAPE_MAX_WORKERS="abc")
    with pytest.raises(ConfigError, match="SOCIALSCRAPE_MAX_WORKERS"):
        create_app()


@pytest.fixture
def run_cli(monkeypatch):
    """Call main.run([]) with uvicorn.run and logging.basicConfig recorded instead of run."""
    import logging

    import uvicorn

    from socialscrape import main

    calls: dict[str, dict] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **k: calls.setdefault("uvicorn", {"app": app, **k}))
    monkeypatch.setattr(logging, "basicConfig", lambda **k: calls.setdefault("logging", k))
    for name in ("HOST", "PORT", "SOCIALSCRAPE_LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)

    def run(**values: str) -> dict[str, dict]:
        for k, v in values.items():
            monkeypatch.setenv(k, v)
        calls.clear()
        main.run([])
        return calls

    return run


@pytest.mark.parametrize("values,host,port,level", [
    ({}, "127.0.0.1", 8000, logging.INFO),
    ({"SOCIALSCRAPE_LOG_LEVEL": "debug"}, "127.0.0.1", 8000, logging.DEBUG),
    ({"SOCIALSCRAPE_LOG_LEVEL": " Warning "}, "127.0.0.1", 8000, logging.WARNING),
    ({"SOCIALSCRAPE_LOG_LEVEL": "10"}, "127.0.0.1", 8000, 10),
    ({"SOCIALSCRAPE_LOG_LEVEL": "warn"}, "127.0.0.1", 8000, logging.WARNING),  # uvicorn's own names lack WARN
    ({"SOCIALSCRAPE_LOG_LEVEL": "", "PORT": "", "HOST": ""}, "127.0.0.1", 8000, logging.INFO),
    ({"HOST": " ::1 ", "PORT": " 8123 "}, "::1", 8123, logging.INFO),
    ({"HOST": "[::1]"}, "::1", 8000, logging.INFO),  # gap-core-host-unusable-message: the URL form failed to bind
    ({"HOST": "localhost"}, "localhost", 8000, logging.INFO),
])
def test_run_reads_env_like_settings(env, run_cli, values, host, port, level):
    from fastapi import FastAPI

    env()
    calls = run_cli(**values)
    assert isinstance(calls["uvicorn"].pop("app"), FastAPI)
    # gap-core-log-level-ignores-uvicorn: uvicorn's loggers (startup lines, access log) stayed at INFO
    assert calls["uvicorn"] == {"host": host, "port": port, "log_level": level}
    assert calls["logging"]["level"] == level


@pytest.mark.parametrize("values,says", [
    ({"PORT": "abc"}, "PORT"), ({"PORT": "0"}, "PORT"), ({"PORT": "70000"}, "PORT"), ({"PORT": "-1"}, "PORT"),
    ({"SOCIALSCRAPE_LOG_LEVEL": "bogus"}, "SOCIALSCRAPE_LOG_LEVEL"),
    ({"SOCIALSCRAPE_MAX_WORKERS": "abc"}, "SOCIALSCRAPE_MAX_WORKERS"), ({"SOCIALSCRAPE_API_KEYS": "k1,é"}, "ASCII"),
    # retest-runtime-unicode-digit-traceback: str.isdigit() passed these to int(), which raised a traceback
    ({"PORT": "²"}, "PORT"), ({"PORT": "8000²"}, "PORT"), ({"PORT": "٨٠٠٠"}, "PORT"), ({"PORT": "+80"}, "PORT"),
    ({"SOCIALSCRAPE_LOG_LEVEL": "²"}, "SOCIALSCRAPE_LOG_LEVEL"),
    ({"SOCIALSCRAPE_LOG_LEVEL": "١٠"}, "SOCIALSCRAPE_LOG_LEVEL"),
    # gap-core-host-unusable-message: uvicorn failed to bind with "[Errno 8] nodename nor servname provided"
    ({"HOST": "bogus.invalid"}, "HOST"), ({"HOST": "[::1"}, "HOST"), ({"HOST": "[127.0.0.1]"}, "HOST"),
    ({"HOST": "a..b"}, "HOST"),
    # gap-core-cache-path-unusable-500: the cache was opened on the first request, so every request was a 500
    ({"SOCIALSCRAPE_CACHE_PATH": "/"}, "SOCIALSCRAPE_CACHE_PATH"),
    ({"SOCIALSCRAPE_TRANSCRIBE": "mlx-whisper", "SOCIALSCRAPE_WHISPER_MODEL": "distil-large-v2"},
     "SOCIALSCRAPE_WHISPER_MODEL"),
])
def test_run_rejects_bad_env_in_one_line(env, run_cli, monkeypatch, values, says):
    import ipaddress
    import socket

    real = socket.getaddrinfo

    def no_dns(host, *args, **kwargs):  # a name that needs a DNS query doesn't resolve: the test stays offline
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if host != "localhost":
                host.encode("idna")  # what socket.getaddrinfo does first: UnicodeError for 'a..b'
                raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known") from None
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    env()
    with pytest.raises(SystemExit) as exc:
        run_cli(**values)
    assert str(exc.value).startswith("socialscrape: ") and says in str(exc.value) and "\n" not in str(exc.value)


@pytest.mark.parametrize("args,code,says", [
    (["--help"], 0, "environment variables"), (["--version"], 0, "socialscrape "), (["-h"], 0, "usage"),
    (["serve"], 2, "unrecognized arguments"), (["--port", "9000"], 2, "unrecognized arguments"),
])
def test_run_answers_cli_flags_without_serving(run_cli, monkeypatch, capsys, args, code, says):
    # gap-core-cli-ignores-args: every argument was ignored, so `socialscrape --help` started the server
    import uvicorn

    from socialscrape import main

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("started the server"))
    with pytest.raises(SystemExit) as exc:
        main.run(args)
    assert exc.value.code == code
    out = capsys.readouterr()
    assert says in out.out + out.err


def test_run_warns_when_exposed_without_keys(env, run_cli, caplog):
    env(SOCIALSCRAPE_API_KEYS="")
    run_cli(HOST="0.0.0.0")
    assert "SOCIALSCRAPE_API_KEYS unset" in caplog.text
    caplog.clear()
    env(SOCIALSCRAPE_API_KEYS="k1")
    run_cli(HOST="0.0.0.0")
    run_cli(HOST="localhost")
    assert "SOCIALSCRAPE_API_KEYS unset" not in caplog.text


def test_stop_workers_does_not_wait_for_abandoned_calls(env):
    # gap-core-sigint-waits-abandoned-workers: Python joins pool threads at exit, so after Ctrl-C a yt-dlp call that
    # outlived its request (stuck on a hung proxy) kept the process running until it gave up
    import asyncio
    import threading

    from socialscrape import main, ytdlp
    from socialscrape.errors import UpstreamError

    stuck = threading.Event()
    env(SOCIALSCRAPE_REQUEST_TIMEOUT="0.2", SOCIALSCRAPE_MAX_WORKERS="1")
    with pytest.raises(UpstreamError, match="Timed out"):
        asyncio.run(ytdlp.with_ydl("tiktok", lambda ydl: stuck.wait(30)))
    queued = ytdlp._pool().submit(stuck.wait, 30)  # waits behind the stuck call
    t0 = time.monotonic()
    try:
        assert main._stop_workers(grace=0.3) == 1
        assert time.monotonic() - t0 < 2 and queued.cancelled() and ytdlp._executor is None
    finally:
        stuck.set()
    assert main._stop_workers() == 0  # nothing left to stop


def test_run_exits_without_waiting_for_busy_workers(env, run_cli, monkeypatch, caplog):
    import logging

    from socialscrape import main

    exits: list[int] = []
    monkeypatch.setattr(logging, "shutdown", lambda: None)  # would close pytest's log capture too
    monkeypatch.setattr(os, "_exit", exits.append)
    env()
    monkeypatch.setattr(main, "_stop_workers", lambda: 0)
    run_cli()
    assert exits == []  # a normal exit
    monkeypatch.setattr(main, "_stop_workers", lambda: 2)
    run_cli()
    assert exits == [0] and "exiting without waiting for 2 yt-dlp/Whisper call(s)" in caplog.text


class _CtrlC(Exception):
    """Raised by the SIGINT handler the tests below install, standing in for Python's own: a real
    KeyboardInterrupt that got past the code under test would end the whole pytest session."""


def _ctrl_c(signum, frame):
    raise _CtrlC


def test_second_ctrl_c_while_waiting_for_workers_is_ignored(env, run_cli, monkeypatch, caplog):
    # retest-gaps-core-double-sigint-in-grace: once stopped, uvicorn puts Python's SIGINT handler back, so a 2nd
    # Ctrl-C during the 1 s wait for the workers raised KeyboardInterrupt past os._exit, and Python then waited
    # for the stuck yt-dlp call after all (~28 s)
    import logging
    import signal
    import threading

    import uvicorn

    from socialscrape import ytdlp

    exits: list[int] = []
    monkeypatch.setattr(logging, "shutdown", lambda: None)  # would close pytest's log capture too
    monkeypatch.setattr(os, "_exit", exits.append)
    env(SOCIALSCRAPE_MAX_WORKERS="1")
    stuck = threading.Event()
    ytdlp._pool().submit(stuck.wait, 30)  # a call that outlived its request
    timers: list[threading.Timer] = []
    stopped_at: list[float] = []

    def stopped_by_ctrl_c(app, **kwargs):  # uvicorn.run returning after a 1st Ctrl-C; the 2nd comes 0.3 s later
        stopped_at.append(time.monotonic())
        timers.append(threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGINT)))
        timers[0].start()

    monkeypatch.setattr(uvicorn, "run", stopped_by_ctrl_c)
    previous = signal.signal(signal.SIGINT, _ctrl_c)
    try:
        run_cli()
        took = time.monotonic() - stopped_at[0]
        timers[0].join(5)
        handler = signal.getsignal(signal.SIGINT)
    finally:
        stuck.set()
        signal.signal(signal.SIGINT, previous)
    assert exits == [0] and "exiting without waiting for 1 yt-dlp/Whisper call(s)" in caplog.text
    assert 0.3 < took < 2.5  # the 1 s grace, no more
    assert handler is _ctrl_c  # put back afterwards


def test_ctrl_c_as_uvicorn_returns_still_skips_busy_workers(env, run_cli, monkeypatch, caplog):
    # retest-gaps-core-double-sigint-in-grace: uvicorn swallows the KeyboardInterrupt of the Ctrl-C that stopped it,
    # but one more on its way out reached run() and skipped os._exit
    import logging
    import signal

    import uvicorn

    from socialscrape import main

    def interrupted(app, **kwargs):
        raise KeyboardInterrupt

    exits: list[int] = []
    monkeypatch.setattr(logging, "shutdown", lambda: None)
    monkeypatch.setattr(os, "_exit", exits.append)
    monkeypatch.setattr(main, "_stop_workers", lambda: 1)
    monkeypatch.setattr(uvicorn, "run", interrupted)
    env()
    previous = signal.signal(signal.SIGINT, _ctrl_c)
    try:
        run_cli()
        handler = signal.getsignal(signal.SIGINT)
    except KeyboardInterrupt:  # not raised: it would end the pytest session
        pytest.fail("the KeyboardInterrupt got out of run(), past os._exit")
    finally:
        signal.signal(signal.SIGINT, previous)
    assert exits == [0] and "exiting without waiting for 1 yt-dlp/Whisper call(s)" in caplog.text
    assert handler is _ctrl_c


def test_cache_key_keeps_repeated_param_order(tmp_path):
    # core-cache-repeated-param: ?h=a&h=b and ?h=b&h=a shared a key, though handlers read the last value
    from fastapi import FastAPI

    from socialscrape.cache import CacheMiddleware, ResponseCache

    app = FastAPI()
    app.add_middleware(CacheMiddleware, cache=ResponseCache(str(tmp_path / "c.sqlite3")))

    @app.get("/v9/test/echo")
    async def echo(h: str, x: str = ""):
        return {"success": True, "h": h}

    c = TestClient(app)
    assert c.get("/v9/test/echo?h=a&h=b").json()["h"] == "b"
    assert c.get("/v9/test/echo?h=b&h=a").json()["h"] == "a"
    for query, last in (("h=b&h=a", "a"), ("h=a&h=b", "b"), ("x=1&h=a&h=b", "b")):
        r = c.get(f"/v9/test/echo?{query}&cache_max_age=1d")
        assert r.json()["h"] == last, (query, r.headers.get("x-cache"))
    c.get("/v9/test/echo?h=a&x=1")
    assert c.get("/v9/test/echo?x=1&h=a&cache_max_age=1d").headers["x-cache"] == "hit"  # other names still sorted


# --- network errors -----------------------------------------------------------------------


def _extractor_failure(cause: BaseException, msg: str) -> DownloadError:
    """What an extractor's _download_webpage turns a networking error into."""
    try:
        raise ExtractorError(f"Unable to download webpage: {msg}", cause=cause)
    except ExtractorError as e:
        return DownloadError(f"ERROR: {e.msg}", exc_info=(type(e), e, None))


def _raised_from(exc: BaseException, cause: BaseException) -> BaseException:
    try:
        try:
            raise cause
        except BaseException as c:
            raise exc from c
    except BaseException as e:
        return e


def test_network_errors_are_classified(env):
    # core-proxy-unclassified: a dead proxy was a 500 upstream_error quoting curl, a CONNECT 403 a 403 forbidden
    from yt_dlp.networking.exceptions import NoSupportingHandlers, ProxyError, TransportError

    env(SOCIALSCRAPE_PROXY="")  # offline-suite-proxy-env-sensitive: with one, every network error is a proxy_error
    curl_dead_proxy = ("Failed to perform, curl: (7) Failed to connect to 127.0.0.1 port 1 after 0 ms: Couldn't "
                       "connect to server over proxy. See https://curl.se/libcurl/c/libcurl-errors.html")
    cases = [
        (ProxyError("Unable to connect to proxy"), "", 502, "proxy_error"),
        (ProxyError("Tunnel connection failed: 403 Forbidden"), "", 502, "proxy_error"),
        (TransportError(curl_dead_proxy), curl_dead_proxy, 502, "proxy_error"),
        (_raised_from(TransportError("read timed out"), TimeoutError()), "", 500, "timeout"),
        (TransportError("SocksHTTPSConnection(host='www.youtube.com', port=443): Failed to establish a new "
                        "connection: [Errno 61] Connection refused"), "", 502, "proxy_error"),  # dead socks5 proxy
        (TransportError("Connection reset by peer"), "Connection reset by peer", 502, "network_error"),
        (NoSupportingHandlers([], []), "", 500, "config_error"),
    ]
    for cause, msg, status, code in cases:
        for exc in (cause, _extractor_failure(cause, msg or str(cause))):
            err = translate_error(exc)
            assert (err.status, err.code) == (status, code), (cause, err.status, err.code, err.message)
            assert "curl" not in err.message and "127.0.0.1" not in err.message  # raw text: server log only


class Timeout(Exception):  # named like curl_cffi's, which it raises for every curl (28)
    pass


class ReadTimeoutError(Exception):  # named like urllib3's
    pass


@pytest.mark.parametrize("cause,msg,direct", [
    (Timeout, "Failed to perform, curl: (28) Connection timed out after 5001 milliseconds", (500, "timeout")),
    (Timeout, "Failed to perform, curl: (28) Operation timed out after 5000 milliseconds with 0 bytes received",
     (500, "timeout")),
    (ReadTimeoutError, "HTTPSConnectionPool(host='www.youtube.com', port=443): Read timed out. (read timeout=5.0)",
     (500, "timeout")),
    (ConnectionResetError, "Connection reset by peer", (502, "network_error")),
], ids=["curl-connect-timeout", "curl-read-timeout", "urllib3-read-timeout", "reset"])
def test_network_error_through_a_proxy_is_a_proxy_error(env, cause, msg, direct):
    """retest-runtime-blackholed-proxy-timeout: a proxy that never accepts, or accepts and never answers, was a 500
    timeout blaming the platform (curl's and urllib3's text don't always name the proxy)."""
    from yt_dlp.networking.exceptions import TransportError

    failure = _raised_from(TransportError(msg), cause(msg))
    for proxy, (status, code) in (("http://qauser:qasecret@127.0.0.1:9", (502, "proxy_error")), ("", direct)):
        env(SOCIALSCRAPE_PROXY=proxy)
        for exc in (failure, _extractor_failure(failure, msg)):
            err = translate_error(exc)
            assert (err.status, err.code) == (status, code), (proxy, err.status, err.code, err.message)
            assert "qasecret" not in err.message and "127.0.0.1" not in err.message


def test_proxy_url_never_reaches_the_client(env):
    # retest-runtime-proxy-url-leak: urllib3's LocationParseError text quoted the proxy URL in a 500 upstream_error
    from yt_dlp.networking.exceptions import RequestError

    for proxy in ("http://qauser:qasecret@127.0.0.1:8899", "qauser:qasecret@127.0.0.1:8899"):
        env(SOCIALSCRAPE_PROXY=proxy)
        for text in (f"Failed to parse: {proxy}", "Failed to parse: http://qauser:qasecret@127.0.0.1:8899",
                     "Proxy-Authorization for qauser:qasecret failed"):
            cause = RequestError(text)
            for exc in (cause, _extractor_failure(cause, text)):
                err = translate_error(exc)
                assert "qasecret" not in err.message and "<proxy>" in err.message, (proxy, err.message)
    env(SOCIALSCRAPE_PROXY="http://user@127.0.0.1:8899")  # a bare user name stays: it may be a word of the message
    assert translate_error(RequestError("This user does not exist")).message == "This user does not exist"


# --- cookie files (fake cookies only) -------------------------------------------------------

_COOKIES = "# Netscape HTTP Cookie File\n.tiktok.com\tTRUE\t/\tTRUE\t2147483647\tqa_fake\tqa\n"


def test_request_never_rewrites_cookie_file(env, tmp_path):
    # core-cookie-file-race: YoutubeDL.close() saved the jar back with a plain open(path, 'w') on every request
    import http.cookiejar

    from socialscrape import ytdlp

    cookie = tmp_path / "cookies.txt"
    cookie.write_text(_COOKIES)
    cookie.chmod(0o444)  # a read-only file too: saving the jar to it was a PermissionError
    env(SOCIALSCRAPE_COOKIES_TIKTOK=str(cookie))

    def touch_jar(ydl):  # a platform setting a cookie mid-request, as TikTok does
        assert any(c.name == "qa_fake" for c in ydl.cookiejar)
        ydl.cookiejar.set_cookie(http.cookiejar.Cookie(
            0, "set_by_platform", "1", None, False, ".tiktok.com", True, True, "/", True, True, 2147483647, False,
            None, None, {}))
        return "done"

    assert ytdlp._run("tiktok", touch_jar, {}) == "done"
    assert cookie.read_text() == _COOKIES


@pytest.mark.parametrize("make,says", [
    (lambda p: None, "does not exist"),
    (lambda p: p.write_text("not a cookie file\n"), "not a valid Netscape cookies.txt"),
    (lambda p: p.write_bytes(b"\xff\xfe\x00garbage"), "not a valid Netscape cookies.txt"),
    (lambda p: p.write_text('{"cookies": []}'), "not a valid Netscape cookies.txt"),
    (lambda p: p.mkdir(), "is not a file"),
    (lambda p: (p.write_text(_COOKIES), p.chmod(0)), "is not readable"),
], ids=["missing", "garbage", "binary", "json", "directory", "unreadable"])
def test_unusable_cookie_file_is_named(env, tmp_path, make, says):
    # core-cookie-permission-500: was a text/plain 500 (PermissionError), or 403 forbidden for a garbage file
    from socialscrape.main import create_app

    if says == "is not readable" and os.geteuid() == 0:
        pytest.skip("file permissions are not enforced for root")
    cookie = tmp_path / "cookies.txt"
    make(cookie)
    env(SOCIALSCRAPE_COOKIES_TIKTOK=str(cookie), SOCIALSCRAPE_PROXY="http://127.0.0.1:9")  # no upstream traffic
    try:
        c = TestClient(create_app(), raise_server_exceptions=False)
        body = _envelope(c.get("/v1/tiktok/profile", params={"handle": "tiktok"}), 500, "cookies_error")
        health = c.get("/health").json()
    finally:
        if cookie.exists() and not cookie.is_dir():
            cookie.chmod(0o600)
    assert "SOCIALSCRAPE_COOKIES_TIKTOK" in body["message"] and says in body["message"]
    assert str(tmp_path) not in body["message"]  # the path stays in the server log
    assert health["cookies"] == [] and says in health["cookie_errors"]["tiktok"]


def test_cookie_file_is_reparsed_when_it_changes(env, tmp_path):
    from socialscrape import ytdlp

    cookie = tmp_path / "cookies.txt"
    cookie.write_text("garbage\n")
    assert ytdlp.cookie_file_problem(str(cookie))
    cookie.write_text(_COOKIES + "# fixed\n")  # new size and mtime: parsed again
    assert ytdlp.cookie_file_problem(str(cookie)) is None


def test_cookie_and_cache_paths_expand_home_and_variables(env, tmp_path, monkeypatch):
    # core-runtime-cookie-path-expansion: yt-dlp expands ~ and $VARS in its cookiefile, but the cookie check read the
    # raw value, so '~/cookies.txt' answered 500 cookies_error "does not exist"; a '~/' cache path made a ./~ directory
    from socialscrape import ytdlp
    from socialscrape.main import create_app

    (tmp_path / "c.txt").write_text(_COOKIES)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QA_COOKIE_DIR", str(tmp_path))
    s = env(SOCIALSCRAPE_COOKIES_YOUTUBE="~/c.txt", SOCIALSCRAPE_COOKIES_TIKTOK="$QA_COOKIE_DIR/c.txt",
            SOCIALSCRAPE_CACHE_PATH="~/x/c.sqlite3")()
    assert s.cookie_files == {"youtube": str(tmp_path / "c.txt"), "tiktok": str(tmp_path / "c.txt")}
    assert ytdlp.cookie_file_problem(s.cookie_files["youtube"]) is None
    assert ytdlp.base_options("youtube")["cookiefile"] == str(tmp_path / "c.txt")
    health = TestClient(create_app()).get("/health").json()
    assert (health["cookies"], health["cookie_errors"], health["cache"]) == (["tiktok", "youtube"], {}, True)
    assert s.cache_path == str(tmp_path / "x" / "c.sqlite3") and (tmp_path / "x" / "c.sqlite3").is_file()


def test_cookie_load_error_is_not_forbidden():
    from yt_dlp.cookies import CookieLoadError

    for exc in (CookieLoadError("failed to load cookies"),
                DownloadError("ERROR: failed to load cookies", exc_info=(CookieLoadError, CookieLoadError("x"), None))):
        err = translate_error(exc)
        assert (err.status, err.code) == (500, "cookies_error"), (err.status, err.code)


# --- AI transcription misconfiguration ----------------------------------------------------


def test_missing_whisper_backend_is_reported(env, monkeypatch):
    # core-transcribe-misconfig-500: advertised in /health, then a text/plain 500 after downloading the audio
    import asyncio

    from socialscrape import transcribe
    from socialscrape.errors import NotSupported
    from socialscrape.main import create_app

    monkeypatch.setattr(transcribe, "installed", lambda backend: False)
    env(SOCIALSCRAPE_TRANSCRIBE="faster-whisper")
    c = TestClient(create_app())
    assert c.get("/health").json()["transcription"] is None
    body = _envelope(c.get("/v2/instagram/media/transcript", params={"url": "DLDXI0fylTC"}), 501,
                     "transcription_unavailable")
    assert "faster-whisper" in body["message"] and "whisper" in body["message"]
    downloads = []
    monkeypatch.setattr(transcribe, "with_ydl", lambda *a, **k: downloads.append(a))
    with pytest.raises(NotSupported) as exc:
        asyncio.run(transcribe.transcribe("https://www.tiktok.com/@a/video/1", platform="tiktok"))
    assert exc.value.code == "transcription_unavailable" and downloads == []  # refused before the download
    env(SOCIALSCRAPE_TRANSCRIBE="none")
    assert _envelope(c.get("/v2/instagram/media/transcript", params={"url": "DLDXI0fylTC"}), 501,
                     "transcription_disabled")


def test_whisper_failure_is_an_envelope(env, monkeypatch):
    import asyncio

    from socialscrape import transcribe
    from socialscrape.errors import ApiError

    async def downloaded(*a, **k):
        return "/nonexistent/audio.m4a", {}

    failure: list[BaseException] = []

    def backend(name, path, language):
        raise failure[0]

    monkeypatch.setattr(transcribe, "installed", lambda backend: True)
    monkeypatch.setattr(transcribe, "with_ydl", downloaded)
    monkeypatch.setattr(transcribe, "_run_backend", backend)
    env(SOCIALSCRAPE_TRANSCRIBE="mlx-whisper", SOCIALSCRAPE_WHISPER_MODEL="bogus-model-xyz")

    def run(exc, language=None):
        failure[:] = [exc]
        return asyncio.run(transcribe.transcribe("https://www.tiktok.com/@a/video/1", platform="tiktok",
                                                 language=language))

    for exc, language in ((RuntimeError("Repository Not Found for url: https://huggingface.co/x"), None),
                          (ModuleNotFoundError("No module named 'mlx_whisper'"), "en"),
                          (ValueError("Repo id must use alphanumeric chars"), None)):
        with pytest.raises(ApiError) as err:
            run(exc, language)
        assert (err.value.status, err.value.code) == (500, "transcription_failed")
        assert "bogus-model-xyz" in err.value.message and "huggingface" not in err.value.message
    with pytest.raises(BadRequest) as err:  # tt_video-2: a language the model lacks, found out after the download
        run(ValueError("tuple.index(x): x not in tuple"), "yue")
    assert err.value.status == 400 and "bogus-model-xyz" in err.value.message
    with pytest.raises(ApiError) as err:  # the same ValueError for a language every model has: not the caller's
        run(ValueError("tuple.index(x): x not in tuple"), "en")
    assert err.value.code == "transcription_failed"
    # ig_post-3: ffmpeg finds no audio stream (a video-only file yt-dlp couldn't tell apart): an empty transcript
    no_audio = RuntimeError("Failed to load audio: [out#0/s16le] Output file does not contain any stream")
    assert run(no_audio).text == "" and run(no_audio, "eng").language == "en"


def test_video_only_media_is_not_transcribed(env, monkeypatch):
    import asyncio

    from socialscrape import transcribe

    ran = []

    async def downloaded(*a, **k):
        return "/tmp/video-only.mp4", {"acodec": "none", "vcodec": "avc1"}

    monkeypatch.setattr(transcribe, "installed", lambda backend: True)
    monkeypatch.setattr(transcribe, "with_ydl", downloaded)
    monkeypatch.setattr(transcribe, "_run_backend", lambda *a: ran.append(a))
    env(SOCIALSCRAPE_TRANSCRIBE="mlx-whisper")
    result = asyncio.run(transcribe.transcribe("https://www.instagram.com/p/x/", platform="instagram"))
    assert (result.text, result.segments, ran) == ("", [], [])


def test_queued_transcriptions_leave_the_ytdlp_pool_free(env, monkeypatch):
    # gap-core-whisper-lock-starves-pool: transcriptions ran in the yt-dlp pool and queued there for the model lock,
    # so with SOCIALSCRAPE_MAX_WORKERS=2, two of them held every worker and platform calls waited for them
    import asyncio
    import threading

    from socialscrape import transcribe, ytdlp

    release = threading.Event()
    ran: list[str] = []

    def backend(name, path, language):
        ran.append(threading.current_thread().name)
        release.wait(10)
        return "en", []

    async def downloaded(*a, **k):
        return "/nonexistent/audio.m4a", {}

    monkeypatch.setattr(transcribe, "installed", lambda backend: True)
    monkeypatch.setattr(transcribe, "with_ydl", downloaded)
    monkeypatch.setattr(transcribe, "_run_backend", backend)
    monkeypatch.setattr(transcribe, "_executor", None)
    env(SOCIALSCRAPE_TRANSCRIBE="mlx-whisper", SOCIALSCRAPE_MAX_WORKERS="2")

    async def scenario() -> int:
        jobs = [asyncio.create_task(transcribe.transcribe(f"https://www.tiktok.com/@a/video/{i}", platform="tiktok"))
                for i in range(3)]
        await asyncio.sleep(0.2)
        value = await asyncio.wait_for(ytdlp.with_ydl("tiktok", lambda ydl: 42), 5)  # a trivial platform call
        assert len(ran) == 1  # still one transcription at a time
        release.set()
        await asyncio.gather(*jobs)
        return value

    try:
        assert asyncio.run(scenario()) == 42
    finally:
        release.set()
        transcribe.shutdown_pool()
    assert len(ran) == 3 and all(name.startswith("whisper") for name in ran)


def test_mlx_model_names_map_to_existing_repos(env):
    # gap-core-mlx-turbo-model-name: turbo and large-v3-turbo became mlx-community/whisper-turbo-mlx and
    # whisper-large-v3-turbo-mlx, which don't exist: every transcription was a 500 transcription_failed
    from socialscrape import transcribe
    from socialscrape.config import ConfigError
    from socialscrape.main import create_app

    for name, repo in (("small", "mlx-community/whisper-small-mlx"), ("tiny.en", "mlx-community/whisper-tiny.en-mlx"),
                       ("large-v3", "mlx-community/whisper-large-v3-mlx"), ("turbo", "mlx-community/whisper-turbo"),
                       ("Large-V3-Turbo", "mlx-community/whisper-large-v3-turbo"),
                       ("distil-large-v3", "mlx-community/distil-whisper-large-v3"),
                       ("distil-medium.en", "mlx-community/distil-whisper-medium.en"),
                       ("org/whisper-x", "org/whisper-x"), ("./models/whisper", "./models/whisper")):
        assert transcribe.mlx_repo(name) == repo, name
    # faster-whisper names mlx-community has no conversion of: refused at startup, by name
    for name in ("distil-large-v2", "distil-small.en", "Distil-Large-V3.5"):
        env(SOCIALSCRAPE_TRANSCRIBE="mlx-whisper", SOCIALSCRAPE_WHISPER_MODEL=name)
        with pytest.raises(ConfigError, match="SOCIALSCRAPE_WHISPER_MODEL"):
            create_app()
        env(SOCIALSCRAPE_TRANSCRIBE="faster-whisper")
        create_app()  # faster-whisper has them


def test_whisper_language():
    from socialscrape.transcribe import whisper_language

    # the Whisper backends raise ValueError on 3-letter codes (tt_video-2), so they are shortened first
    for given, code in (("en", "en"), ("EN", "en"), ("eng", "en"), ("zho", "zh"), ("spa", "es"), ("haw", "haw"),
                        ("yue", "yue"), ("jav", "jw"), ("jv", "jw"), ("nob", "no"), ("he", "he"), ("en-US", "en"),
                        ("cmn", "zh"), ("cmn-Hans-CN", "zh")):  # gap-tiktok-2: Mandarin's ISO 639-3 code
        assert whisper_language(given) == code, given
    # fix3: Whisper's codes for Hebrew, Indonesian, Bokmål and Filipino under the other codes they go by
    for given, code in (("iw", "he"), ("in", "id"), ("nb", "no"), ("fil", "tl"), ("fil-PH", "tl"), ("jw", "jw")):
        assert whisper_language(given) == code, given
    # ISO 639-2/B codes were None, and 'per' (Persian) a 'pe' that is no language
    for given, code in (("ger", "de"), ("fre", "fr"), ("per", "fa"), ("chi", "zh"), ("nno", "nn")):
        assert whisper_language(given) == code, given
    for unknown in ("zz", "zzz", "pe", "", None):
        assert whisper_language(unknown) is None, unknown


def test_check_language():
    from socialscrape.transcribe import check_language

    assert check_language(None, "small") is None and check_language("eng", "small") == "en"
    with pytest.raises(BadRequest, match="not supported for AI transcription"):
        check_language("zz", "large-v3")
    # retest-tiktok-1: yue is Whisper's 100th language, only in large-v3-based models
    for model in ("tiny", "base", "small", " Small ", "medium", "large-v1", "large-v2", "distil-large-v2"):
        with pytest.raises(BadRequest, match="large-v3"):
            check_language("yue", model)
        assert check_language("zh", model) == "zh", model
    for model in ("large-v3", "large-v3-turbo", "turbo", "distil-large-v3", "large",
                  "mlx-community/whisper-small-mlx", "/models/whisper-small"):  # can't tell: the model decides
        assert check_language("yue", model) == "yue", model
    # retest-tiktok-2: English-only models ignore `language` and transcribe as English, so the rest is refused
    for model in ("tiny.en", "small.en", "distil-small.en", "mlx-community/whisper-small.en-mlx"):
        for language in ("fr", "yue", "spa"):
            with pytest.raises(BadRequest, match="English-only"):
                check_language(language, model)
        assert check_language("eng", model) == "en", model
    assert check_language("fr", "org/whisper.encoder-v1") == "fr"  # '.en' only as a whole name part


def test_mlx_no_speech_segments_are_dropped(env, monkeypatch):
    # ig_post-2: mlx-whisper (no VAD) returned 'you you' for a music-only video. The stats are the ones measured
    # with whisper-small on nasajpl's music-only carousel videos and on a speech clip.
    import sys
    from types import SimpleNamespace

    from socialscrape import transcribe

    results = {
        "music": [{"start": 0.0, "end": 2.1, "text": " you", "no_speech_prob": 0.938, "avg_logprob": -0.498},
                  {"start": 30.0, "end": 32.1, "text": " you", "no_speech_prob": 0.722, "avg_logprob": -0.838}],
        "dog": [{"start": 0.0, "end": 10.0, "text": "!", "no_speech_prob": 0.44, "avg_logprob": float("nan")}],
        "speech": [{"start": 0.0, "end": 2.6, "text": " Rise and shine.", "no_speech_prob": 0.309,
                    "avg_logprob": -0.183},
                   {"start": 2.6, "end": 4.0, "text": " ♪", "no_speech_prob": 0.2, "avg_logprob": -0.3},
                   {"start": 4.0, "end": 5.5, "text": " 東京へ行く", "no_speech_prob": 0.495, "avg_logprob": -0.42}],
    }
    seen = []

    def fake_transcribe(path, **kw):
        seen.append(kw)
        return {"language": "en", "segments": results[path]}

    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(transcribe=fake_transcribe))
    env(SOCIALSCRAPE_WHISPER_MODEL="small")
    assert transcribe._run_backend("mlx-whisper", "music", None) == ("en", [])
    assert transcribe._run_backend("mlx-whisper", "dog", None) == ("en", [])  # no letters: not speech
    _, kept = transcribe._run_backend("mlx-whisper", "speech", "en")
    assert [s.text for s in kept] == ["Rise and shine.", "東京へ行く"]
    assert seen[-1] == {"path_or_hf_repo": "mlx-community/whisper-small-mlx", "language": "en"}


def test_mlx_hallucinations_with_a_forced_language_are_dropped(env, monkeypatch):
    """retest-cross-2: with a forced language, Whisper's no_speech_prob on a dog-barking clip is 0.44 (under 0.6) and
    it hallucinates. These are the stats whisper-small gave on it, on a tone and on synthetic speech."""
    import sys
    from types import SimpleNamespace

    from socialscrape import transcribe

    def seg(text: str, nsp: float, lp: float, cr: float) -> dict:
        return {"start": 0.0, "end": 9.0, "text": text, "no_speech_prob": nsp, "avg_logprob": lp,
                "compression_ratio": cr}

    hallucinated = [
        seg(" ripping", 0.44, -7.293, 0.65), seg(" umble", 0.44, -2.985, 0.38), seg(" them", 0.565, -3.296, 0.33),
        seg(" A l'espoir, c'est l'homel, c'est l'homel, c'est l'homel", 0.44, -0.315, 9.3),  # repetition loop
        seg(" Tekstet i byenet stedet ògne HÅLÅLÅLÅLÅLÅLÅLÅLÅ", 0.44, -0.326, 5.13),
        seg("字幕by索兰娅", 0.44, -0.671, 0.65),
        seg(" Sous-titres réalisés par la communauté d'Amara.org", 0.44, -0.406, 0.87),
        seg(" ¡Suscríbete!", 0.44, -0.922, 0.64), seg(" Thanks for watching!", 0.5, -0.6, 0.8),
        seg(" 구독과 좋아요 부탁드려요!", 0.44, -0.893, 0.8), seg(" Altyazı M.K.", 0.44, -0.735, 0.62),
        seg(" Sottotitoli e revisione a cura di QTSS", 0.565, -0.785, 0.83),
        seg(" Hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn", 0.44, -0.5, 0.9),
        seg(" Terima kasih kerana menonton!", 0.44, -0.665, 0.78),
    ]
    speech = [
        seg(" Good morning everyone.", 0.003, -0.286, 1.17), seg(" Bonne matin à tous !", 0.003, -0.791, 1.09),
        seg("大家好,今天我想給你們看看", 0.252, -0.199, 0.99), seg(" 東京へ行く", 0.495, -0.42, 1.0),
        # decoding failed, but Whisper is confident there is speech: kept (low-resource language, heavy accent)
        seg(" Halo semuanya", 0.05, -1.4, 1.1), seg(" la la la la la la la la la la", 0.1, -0.3, 3.1),
        seg(" Thanks for watching!", 0.02, -0.3, 0.9),  # said for real
    ]
    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(
        transcribe=lambda path, **kw: {"language": "fr", "segments": hallucinated if path == "dog" else speech}))
    env(SOCIALSCRAPE_WHISPER_MODEL="small")
    assert transcribe._run_backend("mlx-whisper", "dog", "fr") == ("fr", [])
    _, kept = transcribe._run_backend("mlx-whisper", "speech", "fr")
    assert [s.text for s in kept] == [s["text"].strip() for s in speech]
    # no tokens (NaN logprob): left to _speech, which drops it for having no letters
    assert transcribe._no_speech({"text": "!", "no_speech_prob": 0.44, "avg_logprob": float("nan")}) is False


def test_mlx_speech_clip_keeps_its_last_window(env, monkeypatch):
    """transcripts-mlx-no-speech-filter-drops-final-speech: mlx-whisper's no-speech stats are per 30-s window, so the
    one holding a clip's last seconds of speech (then padding, music or silence) rated as no speech and lost its
    line. whisper-small's stats on 33 s of speech, and on 24 s of speech then 15 s of silence, where it loops."""
    import sys
    from types import SimpleNamespace

    from socialscrape import transcribe

    def seg(start: float, text: str, nsp: float, lp: float, cr: float) -> dict:
        return {"start": start, "end": start + 3.0, "text": text, "no_speech_prob": nsp, "avg_logprob": lp,
                "compression_ratio": cr}

    results = {
        "speech33": [seg(0.0, " Today I want to show you 3 quick tips for better coffee at home.", 0.008, -0.177, 1.81),
                     seg(29.96, " Thanks for watching, and don't forget to subscribe for more.", 0.839, -0.248, 0.95)],
        "speech_then_silence": [seg(0.0, " Welcome back to the channel.", 0.001, -0.164, 1.57)] + [
            seg(30.0 + 4 * i, " You can also use a small pot to make a small pot.", 0.867, -0.669, 6.71)
            for i in range(7)],  # a repetition loop: decoding failed
    }
    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(
        transcribe=lambda path, **kw: {"language": "en", "segments": results[path]}))
    env(SOCIALSCRAPE_WHISPER_MODEL="small")
    _, kept = transcribe._run_backend("mlx-whisper", "speech33", None)
    assert [s.text for s in kept] == [s["text"].strip() for s in results["speech33"]]
    _, kept = transcribe._run_backend("mlx-whisper", "speech_then_silence", None)
    assert [s.text for s in kept] == ["Welcome back to the channel."]
