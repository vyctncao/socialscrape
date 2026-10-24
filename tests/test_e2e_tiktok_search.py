"""Live checks of /v1/tiktok/search/keyword and /hashtag from TikTok's public keyword pages (anonymous)."""

from __future__ import annotations

import pytest

from tests.contract import assert_conforms
from tests.e2e import skip_if_blocked, spaced_get

KEYWORD, HASHTAG = "/v1/tiktok/search/keyword", "/v1/tiktok/search/hashtag"


def get(client, path, **params):
    response = spaced_get(client, path, 2.0, **params)
    skip_if_blocked(response)
    return response


@pytest.mark.live
@pytest.mark.parametrize("query", ["travel", "Skincare routine for oily skin"])
def test_keyword_search(client, query):
    r = get(client, KEYWORD, query=query)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert_conforms(body, KEYWORD, min_coverage=0.18)
    posts = body["search_item_list"]
    assert len(posts) >= 10 and isinstance(body["cursor"], int)
    for post in posts:
        assert post["aweme_id"].isdigit() and post["author"]["unique_id"]
        assert post["statistics"]["play_count"] >= 0 and post["video"]["cover"]["url_list"]


@pytest.mark.live
def test_keyword_search_second_page(client):
    first = get(client, KEYWORD, query="travel")
    assert first.status_code == 200, first.text[:300]
    second = get(client, KEYWORD, query="travel", cursor=str(first.json()["cursor"]))
    assert second.status_code == 200, second.text[:300]
    ids = [p["aweme_id"] for p in first.json()["search_item_list"]]
    assert not set(ids) & {p["aweme_id"] for p in second.json()["search_item_list"]}


@pytest.mark.live
def test_hashtag_search(client):
    r = get(client, HASHTAG, hashtag="booktok")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert_conforms(body, HASHTAG, min_coverage=0.15)
    posts = body["aweme_list"]  # kept for #booktok in their caption's hashtags or challenges (not all in desc)
    assert posts and sum("#booktok" in p.get("desc", "").lower() for p in posts) >= len(posts) / 2


@pytest.mark.live
def test_a_keyword_without_a_page_is_a_setup_error(client):
    r = get(client, KEYWORD, query="fitness")
    assert r.status_code == 501 and r.json()["error"] == "platform_auth_required", r.text[:300]
