"""Native AppView identity, pagination and private/missing-resource behavior."""

from __future__ import annotations

import io
import json

import pytest
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError

from socialscrape.platforms import bluesky as b

DID = "did:plc:abcdefghijklmnopqrstuvwx"
HANDLE = "sample.bsky.social"
KEY = "3lqdfq7fkvm2g"
URI = f"at://{DID}/app.bsky.feed.post/{KEY}"


@pytest.fixture
def appview(monkeypatch):
    calls = []
    replies = []

    def request(ydl, method, params):
        calls.append((method, params))
        return replies.pop(0)

    async def run(platform, fn, **options):
        assert platform == "bluesky" and options == {"use_cookies": False}
        return fn(object())

    monkeypatch.setattr(b, "with_ydl", run)
    monkeypatch.setattr(b, "_json", request)
    return calls, replies


def test_profile_preserves_public_zero_and_unknown_counts(client, appview):
    calls, replies = appview
    replies.append(
        {"did": DID, "handle": HANDLE, "followersCount": 0, "postsCount": None}
    )
    result = client.get("/v1/bluesky/profile", params={"handle": "@SAMPLE.BSKY.SOCIAL"})
    assert result.status_code == 200 and result.json()["success"]
    assert result.json()["followersCount"] == 0 and result.json()["postsCount"] is None
    assert calls == [("app.bsky.actor.getProfile", {"actor": HANDLE})]


def test_two_pages_preserve_all_posts_and_the_real_cursor(client, appview):
    calls, replies = appview
    first = [
        {"post": {"uri": f"at://{DID}/app.bsky.feed.post/{i}", "likeCount": i}}
        for i in range(50)
    ]
    second = [
        {"post": {"uri": f"at://{DID}/app.bsky.feed.post/{i}"}} for i in range(50, 63)
    ]
    replies.extend([{"feed": first, "cursor": "provider-cursor"}, {"feed": second}])
    one = client.get("/v1/bluesky/user/posts", params={"handle": HANDLE}).json()
    two = client.get(
        "/v1/bluesky/user/posts", params={"user_id": DID, "cursor": one["cursor"]}
    ).json()
    assert len(one["feed"]) == 50 and len(two["feed"]) == 13 and two["cursor"] is None
    assert len({p["uri"] for p in one["feed"] + two["feed"]}) == 63
    assert calls[1] == (
        "app.bsky.feed.getAuthorFeed",
        {"actor": DID, "limit": 50, "cursor": "provider-cursor"},
    )


def test_post_resolves_handle_then_preserves_the_exact_thread(client, appview):
    calls, replies = appview
    children = [{"post": {"uri": URI + "reply"}, "replies": []}]
    replies.extend(
        [
            {"did": DID},
            {"thread": {"post": {"uri": URI, "replyCount": 1}, "replies": children}},
        ]
    )
    result = client.get(
        "/v1/bluesky/post",
        params={"url": f"https://bsky.app/profile/{HANDLE}/post/{KEY}"},
    )
    assert result.status_code == 200 and result.json()["post"]["uri"] == URI
    assert result.json()["replies"] == children
    assert calls == [
        ("com.atproto.identity.resolveHandle", {"handle": HANDLE}),
        ("app.bsky.feed.getPostThread", {"uri": URI, "depth": 6, "parentHeight": 0}),
    ]


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example/profile/example.com/post/abc",
        "https://bsky.app/profile/example.com/post/a%2Fb",
        "https://user@bsky.app/profile/example.com/post/abc",
        "https://bsky.app:9999/profile/example.com/post/abc",
        "https://bsky.app/profile/example.com/post/abc/extra",
    ],
)
def test_other_hosts_and_malformed_urls_never_reach_appview(client, appview, value):
    calls, _ = appview
    assert client.get("/v1/bluesky/post", params={"url": value}).status_code == 400
    assert not calls


@pytest.mark.parametrize(
    "payload", [{}, {"did": DID, "handle": "someoneelse.bsky.social"}]
)
def test_incomplete_or_wrong_identity_is_not_success(client, appview, payload):
    _, replies = appview
    replies.append(payload)
    result = client.get("/v1/bluesky/profile", params={"handle": HANDLE})
    assert result.status_code == 500 and not result.json()["success"]


@pytest.mark.parametrize("kind,status", [("notFoundPost", 404), ("blockedPost", 403)])
def test_missing_and_blocked_posts_keep_their_states(client, appview, kind, status):
    _, replies = appview
    replies.append({"thread": {"$type": "app.bsky.feed.defs#" + kind}})
    result = client.get(
        "/v1/bluesky/post", params={"url": f"https://bsky.app/profile/{DID}/post/{KEY}"}
    )
    assert result.status_code == status and not result.json()["success"]


def test_wrong_post_uri_is_not_accepted(client, appview):
    _, replies = appview
    replies.append({"thread": {"post": {"uri": URI + "other"}}})
    assert (
        client.get(
            "/v1/bluesky/post",
            params={"url": f"https://bsky.app/profile/{DID}/post/{KEY}"},
        ).status_code
        == 500
    )


@pytest.mark.parametrize(
    "body,status,expected",
    [
        (b'{"error":"ProfileNotFound"}', 400, b.NotFound),
        (b"{}", 429, b.UpstreamBlocked),
        (b"{}", 403, b.Forbidden),
    ],
)
def test_api_resource_states_are_classified(body, status, expected):
    class Network:
        def urlopen(self, request):
            assert request.url.startswith(b.ORIGIN)
            assert "Authorization" not in request.headers
            raise HTTPError(Response(io.BytesIO(body), request.url, {}, status=status))

    with pytest.raises(expected):
        b._json(Network(), "app.bsky.actor.getProfile", {"actor": HANDLE})
