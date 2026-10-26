"""Signed-in client shells and GraphQL responses, without real account cookies."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from socialscrape.errors import Forbidden, NotFound, UpstreamBlocked, UpstreamError
from socialscrape.platforms import instagram as ig
from socialscrape.platforms import instagram_authenticated as auth
from socialscrape.platforms import instagram_private

HANDLE, USER_ID = "sample", "12345"
USER = {
    "pk": USER_ID,
    "id": "relay-profile",
    "fbid_v2": "fb-profile",
    "username": HANDLE,
    "full_name": "Sample",
    "is_private": False,
    "is_verified": False,
    "profile_pic_url": "https://example.com/profile.jpg",
    "follower_count": 5,
    "following_count": 0,
    "media_count": 2,
    "total_clips_count": 2,
}
CODE = "ABCDE"
PK = str(ig._id_to_pk(CODE))
MEDIA = {
    "pk": PK,
    "code": CODE,
    "media_type": 2,
    "product_type": "clips",
    "taken_at": 1720000000,
    "caption": {"text": "A real caption", "pk": "789"},
    "play_count": 17,
    "ig_play_count": 17,
    "like_count": 0,
    "comment_count": 0,
    "user": USER,
    "image_versions2": {
        "candidates": [
            {"url": "https://example.com/image.jpg", "width": 10, "height": 10}
        ]
    },
}


def shell(user_id=USER_ID):
    route = {
        "initialRouteInfo": {"route": {"rootView": {"props": {"id": user_id}}}},
        "viewer": {"id": "99999"},
    }
    return (
        '<script>{"pageID":"profilePage"}</script><script type="application/json" data-sjs>'
        + json.dumps(route)
        + '</script><script>["LSD",[],{"token":"test-lsd"}]</script>'
        '<script>["DTSGInitialData",[],{"token":"test-dtsg"}]</script>'
    )


def connection(nodes, more=False, cursor=None, *, reels=False):
    return {
        "edges": [{"node": {"media": node} if reels else node} for node in nodes],
        "page_info": {"has_next_page": more, "end_cursor": cursor},
    }


def profile_answer(user=USER):
    return {"data": {"user": copy.deepcopy(user)}}


def listing_answer(nodes=None, *, reels=False, more=False, cursor=None):
    value = connection(
        [copy.deepcopy(MEDIA)] if nodes is None else nodes, more, cursor, reels=reels
    )
    return (
        {"data": {"fetch__XDTUserDict": {"clips_connection": value}}}
        if reels
        else {"data": {"xdt_api__v1__feed__user_timeline_graphql_connection": value}}
    )


class Client:
    _is_logged_in = True
    _can_impersonate = True
    _api_headers = {"X-IG-App-ID": "936619743392459"}

    def __init__(self):
        self.requests = []
        self.answers = {
            auth.PROFILE[0]: profile_answer(),
            auth.POSTS[0]: listing_answer(),
            auth.REELS[0]: listing_answer(reels=True),
        }
        self.media = {PK: copy.deepcopy(MEDIA)}
        self.cookies = {
            "csrftoken": SimpleNamespace(value="test-csrf"),
            "ds_user_id": SimpleNamespace(value="99999"),
        }

    def _get_cookies(self, url):
        return self.cookies

    def _download_webpage_handle(
        self, url, item_id=None, *, data=None, headers=None, **kwargs
    ):
        if data is None:
            if "/api/v1/media/" in url:
                self.requests.append(("media", item_id))
                value = {"items": [self.media[item_id]]}
                return json.dumps(value), SimpleNamespace(status=200, url=url)
            self.requests.append(("page", url))
            return shell(), SimpleNamespace(status=200, url=url)
        form = {key: values[0] for key, values in parse_qs(data.decode()).items()}
        assert form["fb_dtsg"] == "test-dtsg" and form["av"] == "99999"
        assert form["jazoest"] == "2" + str(sum(map(ord, "test-dtsg")))
        assert headers["X-FB-LSD"] == form["lsd"] == "test-lsd"
        assert headers["X-CSRFToken"] == "test-csrf"
        name, variables = (
            form["fb_api_req_friendly_name"],
            json.loads(form["variables"]),
        )
        self.requests.append((name, form["doc_id"], variables))
        value = self.answers[name]
        value = value(variables) if callable(value) else value
        return json.dumps(value), SimpleNamespace(status=200, url=url)

    def ydl(self):
        return SimpleNamespace(
            get_info_extractor=lambda name: self, urlopen=lambda request: None
        )


def test_route_id_does_not_take_the_viewer_or_an_unrelated_user_id():
    assert auth.profile_id(shell()) == USER_ID
    other = (
        '<script type="application/json" data-sjs>{"viewer":{"id":"99999"}}</script>'
    )
    with pytest.raises(UpstreamError, match="one profile route"):
        auth.profile_id(other)
    with pytest.raises(UpstreamError):
        auth.profile_id(shell() + shell("7777"))


@pytest.mark.parametrize("missing", ["test-lsd", "test-dtsg"])
def test_shell_without_request_tokens_is_not_a_success(missing):
    html = shell().replace(missing, "")
    with pytest.raises(UpstreamBlocked):
        auth.Session(Client(), html, ig.WEB)


def test_profile_identity_and_exact_counts_include_known_zero():
    client = Client()
    user = auth.profile(client, shell(), ig.WEB + "/sample/", HANDLE)
    mapped = ig.map_profile(user, (12000, True))
    assert mapped["id"] == USER_ID and mapped["fbid"] == "fb-profile"
    assert mapped["edge_follow"]["count"] == 0
    assert mapped["edge_owner_to_timeline_media"]["count"] == 2
    assert (
        mapped["edge_owner_to_timeline_media"]["edges"][0]["node"]["shortcode"] == CODE
    )
    query = next(row for row in client.requests if row[0] == auth.POSTS[0])
    assert query[2][ig._CRAWLER] is True
    assert client.requests[0][2]["id"] == USER_ID


@pytest.mark.parametrize(
    "override", [{"pk": "99999"}, {"username": "someoneelse"}, {"username": None}]
)
def test_wrong_profile_is_rejected(override):
    client = Client()
    client.answers[auth.PROFILE[0]] = profile_answer({**USER, **override})
    with pytest.raises(UpstreamError, match="different profile"):
        auth.profile(client, shell(), ig.WEB, HANDLE)


def test_explicit_missing_profile_is_not_a_zero_filled_object():
    client = Client()
    client.answers[auth.PROFILE[0]] = profile_answer(None)
    with pytest.raises(NotFound):
        auth.profile(client, shell(), ig.WEB, HANDLE)


def test_feed_keeps_every_item_and_follows_the_real_cursor():
    client = Client()
    nodes = [
        {**copy.deepcopy(MEDIA), "pk": str(index + 100), "code": f"CODE{index}"}
        for index in range(15)
    ]
    client.answers[auth.POSTS[0]] = listing_answer(
        nodes, more=True, cursor="native-page-2"
    )
    feed = auth.feed(client, shell(), ig.WEB, HANDLE, "", "native-page-1")
    body = ig.map_user_posts(feed)
    assert len(body["items"]) == 15 and body["next_max_id"] == "native-page-2"
    assert body["items"][0]["taken_at"] == MEDIA["taken_at"]
    assert body["items"][0]["caption"]["text"] == "A real caption"
    assert body["items"][0]["like_count"] == 0
    assert client.requests[1][2]["after"] == "native-page-1"


@pytest.mark.parametrize(
    "malformed",
    [
        {"edges": [], "page_info": {}},
        {"edges": [], "page_info": {"has_next_page": True, "end_cursor": ""}},
        {"edges": [{"node": {}}], "page_info": {"has_next_page": False}},
        connection([MEDIA, MEDIA]),
    ],
)
def test_partial_or_duplicate_listings_are_errors(malformed):
    client = Client()
    client.answers[auth.POSTS[0]] = {
        "data": {"xdt_api__v1__feed__user_timeline_graphql_connection": malformed}
    }
    with pytest.raises(UpstreamError):
        auth.feed(client, shell(), ig.WEB, HANDLE, "", None)


def test_empty_existing_feed_is_refused_and_known_empty_profile_is_valid():
    client = Client()
    client.answers[auth.POSTS[0]] = listing_answer([])
    with pytest.raises(UpstreamBlocked):
        auth.feed(client, shell(), ig.WEB, HANDLE, "", None)
    client.answers[auth.PROFILE[0]] = profile_answer({**USER, "media_count": 0})
    body = ig.map_profile(auth.profile(client, shell(), ig.WEB, HANDLE))
    assert body["edge_owner_to_timeline_media"]["count"] == 0


def test_private_profile_metadata_survives_but_empty_private_feed_is_forbidden():
    client = Client()
    client.answers[auth.PROFILE[0]] = profile_answer({**USER, "is_private": True})
    client.answers[auth.POSTS[0]] = listing_answer([])
    assert auth.profile(client, shell(), ig.WEB, HANDLE)["is_private"] is True
    with pytest.raises(Forbidden):
        auth.feed(client, shell(), ig.WEB, HANDLE, "", None)


def test_reels_have_grid_plays_and_media_info_dates_and_captions():
    client = Client()
    grid = {
        k: copy.deepcopy(MEDIA[k])
        for k in ("pk", "code", "play_count", "media_type", "user")
    }
    client.answers[auth.REELS[0]] = listing_answer(
        [grid], reels=True, more=True, cursor="older"
    )
    feed = auth.feed(client, shell(), ig.WEB, HANDLE, "reels", "previous")
    body = ig.map_user_reels(feed)
    assert body["items"][0]["taken_at"] == MEDIA["taken_at"]
    assert body["items"][0]["play_count"] == 17
    assert body["items"][0]["caption"]["text"] == "A real caption"
    assert body["max_id"] == "older" and ("media", PK) in client.requests
    assert client.requests[1][2]["after"] == "previous"
    assert client.requests[1][2]["data"]["target_user_id"] == USER_ID


def test_reels_never_accept_details_for_another_media_item():
    client = Client()
    client.media[PK]["code"] = "different"
    with pytest.raises(UpstreamError, match="different media"):
        auth.feed(client, shell(), ig.WEB, HANDLE, "reels", None)


def test_highlights_use_the_response_alias_and_actual_cover_url():
    client = Client()
    client.answers[auth.HIGHLIGHTS[0]] = {
        "data": {
            "highlights": {
                "edges": [
                    {
                        "node": {
                            "id": "highlight:123456789",
                            "title": "Featured",
                            "cover_media": {
                                "cropped_image_version": {
                                    "url": "https://example.com/cover.jpg"
                                }
                            },
                        }
                    }
                ]
            }
        }
    }
    user = auth.profile(client, shell(), ig.WEB, HANDLE, highlights=True)
    items = ig.map_highlights(user)
    assert items[0]["id"] == "123456789"
    assert (
        items[0]["cover_media_cropped_thumbnail"]["url"]
        == "https://example.com/cover.jpg"
    )
    assert auth.POSTS[0] not in [row[0] for row in client.requests]


def test_search_uses_signed_in_graphql_and_preserves_explicit_empty_results():
    client = Client()
    found = {"users": [], "hashtags": [], "places": []}
    client.answers[auth.SEARCH[0]] = {
        "data": {"xdt_api__v1__fbsearch__topsearch_connection": found}
    }
    assert auth.search(client, shell(), ig.WEB, "no matching name", "user") == found
    request = client.requests[0]
    assert request[0:2] == auth.SEARCH
    assert request[2]["data"]["query"] == "no matching name"
    assert request[2]["data"]["search_surface"] == "web_top_search"


def test_actual_route_selects_authenticated_feed_instead_of_logged_out_relay(
    client, monkeypatch
):
    net = Client()

    async def run(platform, fn, **opts):
        return fn(net.ydl())

    monkeypatch.setattr(ig, "with_ydl", run)
    body = client.get(
        "/v2/instagram/user/posts",
        params={"handle": HANDLE, "require_play_counts": "true"},
    ).json()
    assert body["success"] and body["items"][0]["code"] == CODE
    assert body["items"][0]["play_count"] == 17


def test_basic_profile_id_uses_current_query_and_validates_identity(
    client, monkeypatch
):
    net = Client()

    async def run(platform, fn, **opts):
        return fn(net.ydl())

    monkeypatch.setattr(instagram_private, "with_ydl", run)
    response = client.get("/v1/instagram/basic-profile", params={"userId": USER_ID})
    assert response.status_code == 200 and response.json()["username"] == HANDLE
    assert (
        client.get(
            "/v1/instagram/basic-profile", params={"userId": "../wrong"}
        ).status_code
        == 400
    )
