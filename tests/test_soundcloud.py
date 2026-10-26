from urllib.parse import parse_qs, urlsplit
import pytest
from socialscrape.platforms import soundcloud as s

USER = {
    "id": 7,
    "kind": "user",
    "permalink": "sample",
    "followers_count": 0,
    "comments_count": None,
}


@pytest.fixture
def public(monkeypatch):
    calls, replies = [], []

    class IE:
        _HEADERS = {"Authorization": "must-not-be-used"}

        def initialize(self):
            assert self._HEADERS == {}

        def _call_api(self, url, identity, **options):
            calls.append((url, identity, options))
            assert "Authorization" not in options.get("headers", {})
            return replies.pop(0)

    ie = IE()

    class YDL:
        def get_info_extractor(self, key):
            assert key == "Soundcloud"
            return ie

    async def run(platform, fn, **options):
        assert platform == "soundcloud" and options == {
            "use_cookies": False,
        }
        return fn(YDL())

    monkeypatch.setattr(s, "with_ydl", run)
    return calls, replies


def test_public_artist_preserves_real_zero_and_unknown_counts(client, public):
    calls, replies = public
    replies.append(USER)
    result = client.get("/v1/soundcloud/artist", params={"handle": "sample"})
    assert (
        result.status_code == 200
        and result.json()["followers_count"] == 0
        and result.json()["comments_count"] is None
    )
    assert calls[0][2]["query"] == {"url": "https://soundcloud.com/sample"}


def test_pages_keep_all_tracks_and_forward_only_the_native_offset(client, public):
    calls, replies = public
    items = [{"id": i + 1, "kind": "track", "sharing": "public"} for i in range(63)]
    offset = "2025-01-01T00:00:00.000Z,tracks,000000001"
    replies.extend(
        [
            USER,
            {
                "collection": items[:50],
                "next_href": s.API
                + "users/7/tracks?offset="
                + offset
                + "&client_id=public-app-id",
            },
            USER,
            {"collection": items[50:], "next_href": None},
        ]
    )
    one = client.get("/v1/soundcloud/artist/tracks", params={"handle": "sample"}).json()
    two = client.get(
        "/v1/soundcloud/artist/tracks",
        params={"handle": "sample", "cursor": one["cursor"]},
    ).json()
    assert (
        len(one["tracks"]) == 50
        and len(two["tracks"]) == 13
        and len({t["id"] for t in one["tracks"] + two["tracks"]}) == 63
    )
    assert (
        one["cursor"] == offset
        and two["cursor"] is None
        and "client_id" not in one["cursor"]
    )
    assert calls[-1][2]["query"]["offset"] == offset


def test_track_identity_and_counter_values_are_not_normalized_away(client, public):
    _, replies = public
    replies.append(
        {
            "id": 8,
            "kind": "track",
            "sharing": "public",
            "playback_count": 0,
            "comment_count": None,
        }
    )
    data = client.get(
        "/v1/soundcloud/track", params={"url": "https://soundcloud.com/sample/track"}
    ).json()
    assert (
        data["id"] == 8
        and data["playback_count"] == 0
        and data["comment_count"] is None
    )


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"id": 8, "kind": "track", "sharing": "private"}, 403),
        ({"id": 7, "kind": "user"}, 500),
        ({"kind": "track"}, 500),
    ],
)
def test_private_or_wrong_resource_does_not_pass(client, public, payload, status):
    _, replies = public
    replies.append(payload)
    assert (
        client.get(
            "/v1/soundcloud/track",
            params={"url": "https://soundcloud.com/sample/track"},
        ).status_code
        == status
    )


def test_invalid_urls_and_private_share_tokens_never_reach_soundcloud(client, public):
    calls, _ = public
    for url in [
        "https://evil.example/sample/track",
        "https://user@soundcloud.com/sample/track",
        "https://soundcloud.com/sample/track?secret_token=s-private",
        "https://soundcloud.com/sample/track/s-secret",
    ]:
        assert (
            client.get("/v1/soundcloud/track", params={"url": url}).status_code == 400
        )
    assert not calls


def test_different_collection_continuation_is_rejected(client, public):
    _, replies = public
    replies.extend(
        [
            USER,
            {"collection": [], "next_href": s.API + "users/another/tracks?offset=bad"},
        ]
    )
    assert (
        client.get(
            "/v1/soundcloud/artist/tracks", params={"handle": "sample"}
        ).status_code
        == 500
    )
