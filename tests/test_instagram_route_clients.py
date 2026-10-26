"""Authenticated account routes and logged-out public topics select the right client."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import io
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from yt_dlp.networking import Request, Response
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError

from socialscrape import portal, ytdlp
from socialscrape.config import get_settings
from socialscrape.errors import Forbidden, UpstreamError
from socialscrape.platforms import instagram as ig
from socialscrape.platforms import instagram_authenticated as auth
from socialscrape.platforms import instagram_private
from tests.test_instagram_authenticated import (
    Client,
    MEDIA,
    PK,
    USER_ID,
    connection,
    shell,
)

COOKIE = "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\ttest-session\n"
AUDIO_ID = "3011705342342322"


def test_anonymous_request_keeps_proxy_but_imports_no_saved_file_or_browser_cookies(
    monkeypatch,
):
    settings = dataclasses.replace(
        get_settings(),
        cookie_files={"instagram": "/file-that-must-not-be-opened"},
        cookies_from_browser="chrome",
    )
    monkeypatch.setattr(ytdlp, "get_settings", lambda: settings)
    monkeypatch.setattr(
        portal,
        "active_platform_pool",
        lambda platform: (["http://proxy.example:8080"], [COOKIE]),
    )

    def inspect(ydl):
        return {
            "cookies": [cookie.name for cookie in ydl.cookiejar],
            "proxy": ydl.params.get("proxy"),
            "cookiefile": ydl.params.get("cookiefile"),
            "browser": ydl.params.get("cookiesfrombrowser"),
        }

    anonymous = asyncio.run(ytdlp.with_ydl("instagram", inspect, use_cookies=False))
    assert anonymous == {
        "cookies": [],
        "proxy": "http://proxy.example:8080",
        "cookiefile": None,
        "browser": None,
    }
    signed_in = asyncio.run(ytdlp.with_ydl("instagram", inspect))
    assert signed_in["cookies"] == ["sessionid"]


def test_public_topics_explicitly_disable_cookies_and_keep_the_caller_pool(monkeypatch):
    calls = []

    async def fetch_page(ie_name, url, item_id, what, **opts):
        calls.append(opts)
        return '<script>{"pageID":"popularSearchPage"}</script>', [
            {"data": {"xig_logged_out_popular_search_media_info": {"edges": []}}}
        ]

    monkeypatch.setattr(ig, "fetch_page", fetch_page)
    asyncio.run(ig.fetch_topic("cats"))
    assert calls == [{"use_cookies": False}]


def tagged_client():
    client = Client()
    client.answers[auth.TAGGED[0]] = {
        "data": {
            "xdt_api__v1__usertags__user_id__feed_connection": connection(
                [copy.deepcopy(MEDIA)], True, "next-tagged"
            )
        }
    }
    return client


def test_tagged_posts_keep_native_fields_and_cursor_without_renaming_play_counts():
    client = tagged_client()
    client.answers[auth.TAGGED[0]]["data"][
        "xdt_api__v1__usertags__user_id__feed_connection"
    ]["edges"][0]["node"]["view_count"] = None
    body = auth.Session(client, shell(), ig.WEB).tagged(USER_ID, "previous-tagged")
    assert body["posts"][0]["pk"] == PK
    assert body["posts"][0]["caption"] == MEDIA["caption"]["text"]
    assert (
        body["posts"][0]["like_count"] == 0 and body["posts"][0]["view_count"] is None
    )
    assert body["cursor"] == "next-tagged" and body["has_more"] is True
    assert client.requests[-1][2]["after"] == "previous-tagged"
    assert client.requests[-1][2]["user_id"] == USER_ID


def test_tagged_listing_cannot_succeed_without_a_progressing_cursor():
    client = tagged_client()
    with pytest.raises(UpstreamError, match="non-advancing"):
        auth.Session(client, shell(), ig.WEB).tagged(USER_ID, "next-tagged")


class AudioClient(Client):
    def __init__(self):
        super().__init__()
        self.payload = {
            "items": [
                {
                    "media": {
                        **copy.deepcopy(MEDIA),
                        "play_count": 25,
                        "fb_play_count": 8,
                        "ig_play_count": 17,
                    }
                }
            ],
            "metadata": {"original_sound_info": {"audio_asset_id": AUDIO_ID}},
            "is_music_page_restricted": False,
            "paging_info": {"more_available": True, "max_id": "next-audio"},
        }
        self.outer_error = None
        self.guard = True

    def _download_webpage_handle(
        self, url, item_id=None, *, data=None, headers=None, **kwargs
    ):
        if url.endswith("/api/v1/clips/music/"):
            assert data is not None
            form = {key: values[0] for key, values in parse_qs(data.decode()).items()}
            assert (
                form["audio_cluster_id"]
                == form["original_sound_audio_asset_id"]
                == AUDIO_ID
            )
            assert (
                form["fb_dtsg"] == "test-dtsg" and headers["X-CSRFToken"] == "test-csrf"
            )
            self.requests.append(("audio-post", form))
            body = json.dumps(
                {"__ar": 1, "payload": self.payload, "error": self.outer_error}
            )
            return ("for (;;);" if self.guard else "") + body, SimpleNamespace(
                status=200, url=url
            )
        return super()._download_webpage_handle(
            url, item_id, data=data, headers=headers, **kwargs
        )


def test_audio_post_unwraps_only_the_payload_and_preserves_distinct_native_counters():
    client = AudioClient()
    body = auth.Session(client, shell(), ig.WEB).audio(AUDIO_ID, "previous-audio")
    media = body["reels"][0]
    assert (media["play_count"], media["fb_play_count"], media["ig_play_count"]) == (
        25,
        8,
        17,
    )
    assert body["cursor"] == "next-audio" and body["has_more"] is True
    assert client.requests[-1][1]["max_id"] == "previous-audio"
    assert "payload" not in body and "fb_dtsg" not in body


@pytest.mark.parametrize(
    "problem",
    ["identity", "missing-paging", "cursor-cycle", "wrapper-error", "media-identity"],
)
def test_audio_never_greenwashes_a_partial_wrong_or_failed_response(problem):
    client = AudioClient()
    if problem == "identity":
        client.payload["metadata"]["original_sound_info"]["audio_asset_id"] = (
            "another-audio"
        )
    elif problem == "missing-paging":
        del client.payload["paging_info"]
    elif problem == "cursor-cycle":
        client.payload["paging_info"]["max_id"] = "previous"
    elif problem == "wrapper-error":
        client.outer_error = 1357004
    else:
        del client.payload["items"][0]["media"]["code"]
    with pytest.raises(UpstreamError):
        auth.Session(client, shell(), ig.WEB).audio(AUDIO_ID, "previous")


def test_restricted_audio_does_not_return_an_empty_success():
    client = AudioClient()
    client.payload["is_music_page_restricted"] = True
    with pytest.raises(Forbidden):
        auth.Session(client, shell(), ig.WEB).audio(AUDIO_ID, None)


def test_audio_and_tagged_http_routes_use_current_clients(client, monkeypatch):
    net = AudioClient()
    net.answers.update(tagged_client().answers)

    async def run(platform, fn, **opts):
        return fn(net.ydl())

    monkeypatch.setattr(instagram_private, "with_ydl", run)
    audio = client.get("/v1/instagram/audio/reels", params={"audio_id": AUDIO_ID})
    assert (
        audio.status_code == 200 and audio.json()["reels"][0]["code"] == MEDIA["code"]
    )
    tagged = client.get("/v1/instagram/user/tagged-posts", params={"user_id": USER_ID})
    assert (
        tagged.status_code == 200 and tagged.json()["posts"][0]["code"] == MEDIA["code"]
    )
    for path, param in [
        ("/v1/instagram/audio/reels", "audio_id"),
        ("/v1/instagram/user/tagged-posts", "user_id"),
    ]:
        assert client.get(path, params={param: "../invalid"}).status_code == 400


def test_known_media_unavailable_is_confirmed_by_the_public_client(monkeypatch):
    calls = []

    async def run(platform, fn, **opts):
        calls.append(opts)
        if len(calls) == 1:
            raise ig.MediaUnavailable("Not available to this account")
        return copy.deepcopy(MEDIA), {"public": True}

    monkeypatch.setattr(ig, "with_ydl", run)
    product, wrapper = asyncio.run(ig.fetch_post(ig.WEB + "/p/ABCDE/"))
    assert product["code"] == "ABCDE" and wrapper == {"public": True}
    assert calls == [{}, {"use_cookies": False, "confirm_not_found": True}]


@pytest.mark.parametrize(
    "message,unavailable",
    [("Media not found or unavailable", True), ("Invalid request", False)],
)
def test_post_error_body_distinguishes_unavailable_media_from_a_real_bad_request(
    message, unavailable
):
    ie = SimpleNamespace(
        _is_logged_in=True,
        _can_impersonate=False,
        _extract_product=lambda *args: None,
        _download_webpage_handle=lambda *args, **opts: None,
    )
    response = Response(
        io.BytesIO(json.dumps({"message": message, "status": "fail"}).encode()),
        url=ig.WEB + f"/api/v1/media/{PK}/info/",
        headers={},
        status=400,
    )

    def urlopen(request):
        raise HTTPError(response)

    ydl = SimpleNamespace(get_info_extractor=lambda name: ie, urlopen=urlopen)

    def extract(url):
        try:
            ydl.urlopen(Request(ig.WEB + f"/api/v1/media/{PK}/info/"))
        except HTTPError as exc:
            raise ExtractorError("Video info extraction failed", cause=exc)

    ie.extract = extract
    with pytest.raises(ig.MediaUnavailable if unavailable else ExtractorError):
        ig._post_flow(ig.WEB + "/p/ABCDE/")(ydl)


def test_public_feed_fallback_checks_identity_and_keeps_its_own_continuation(
    monkeypatch,
):
    from socialscrape.cursor import decode_cursor
    from tests.test_instagram_authenticated import USER

    calls = []

    async def run(platform, fn, **opts):
        calls.append(opts)
        if opts.get("use_cookies") is not False:
            raise auth.PublicListingRequired(USER_ID)
        return ig.Feed(
            copy.deepcopy(USER),
            [copy.deepcopy(MEDIA)],
            {"has_next_page": True, "end_cursor": "public-native-next"},
            {PK: copy.deepcopy(MEDIA)},
        )

    monkeypatch.setattr(ig, "with_ydl", run)
    feed = asyncio.run(ig.fetch_feed("sample", "reels"))
    token = ig.map_user_reels(feed)["max_id"]
    state = decode_cursor(token)
    assert state["t"] == "public-native-next" and state["uid"] == USER_ID
    assert calls[-1]["use_cookies"] is False
    calls.clear()
    asyncio.run(ig.fetch_feed("sample", "reels", token))
    assert calls == [{"confirm_not_found": True, "use_cookies": False}]
    with pytest.raises(ig.BadRequest):
        asyncio.run(ig.fetch_feed("another_profile", "reels", token))
    with pytest.raises(ig.BadRequest):
        asyncio.run(ig.fetch_feed("sample", "", token))


def test_public_and_signed_in_profile_identity_conflict_is_not_hidden(monkeypatch):
    from tests.test_instagram_authenticated import USER

    async def run(platform, fn, **opts):
        if opts.get("use_cookies") is not False:
            raise auth.PublicListingRequired(USER_ID)
        return ig.Feed(
            {**USER, "pk": "another-id"}, [MEDIA], {"has_next_page": False}, {}
        )

    monkeypatch.setattr(ig, "with_ydl", run)
    with pytest.raises(UpstreamError, match="identities disagree"):
        asyncio.run(ig.fetch_feed("sample", "reels"))
