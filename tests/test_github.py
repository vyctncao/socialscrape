"""GitHub identity, public-only extraction, page retention and HTML contracts."""

from __future__ import annotations

import io
import json

import pytest
from bs4 import BeautifulSoup
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError

from socialscrape.platforms import github as g

USER = {
    "id": 7,
    "node_id": "U7",
    "login": "sample",
    "html_url": "https://github.com/sample",
    "followers": 0,
    "following": None,
    "public_repos": 32,
    "type": "User",
    "site_admin": False,
}
REPO = {
    "id": 12,
    "node_id": "R12",
    "name": "example",
    "full_name": "sample/example",
    "owner": USER,
    "html_url": "https://github.com/sample/example",
    "private": False,
    "stargazers_count": 0,
    "forks_count": None,
    "license": {"key": "mit", "name": "MIT", "spdx_id": "MIT"},
}


@pytest.fixture
def upstream(monkeypatch):
    calls, replies = [], []

    async def run(platform, fn, **options):
        assert platform == "github" and options == {
            "cookiefile": None,
            "cookiesfrombrowser": None,
        }
        return fn(None)

    def read(ydl, path, params=None, **options):
        calls.append((path, params, options))
        return replies.pop(0)

    monkeypatch.setattr(g, "with_ydl", run)
    monkeypatch.setattr(g, "_read", read)
    return calls, replies


def test_profile_preserves_identity_zero_and_unknown(client, upstream):
    calls, replies = upstream
    replies.append((USER, {}))
    r = client.get("/v1/github/user", params={"url": "https://github.com/sample"})
    assert r.status_code == 200 and r.json()["login"] == "sample"
    assert r.json()["followers"] == 0 and r.json()["following"] is None
    assert r.json()["url"] == USER["html_url"]
    assert calls == [("/users/sample", None, {})]


@pytest.mark.parametrize(
    "path,key",
    [
        ("repositories", "repositories"),
        ("followers", "followers"),
        ("following", "following"),
    ],
)
def test_real_next_page_does_not_drop_list_items(client, upstream, path, key):
    calls, replies = upstream
    values = [dict(REPO if path == "repositories" else USER, id=i) for i in range(33)]
    replies.extend(
        [
            (
                values[:30],
                {
                    "Link": '<https://api.github.com/users/sample/list?page=2>; rel="next"'
                },
            ),
            (values[30:], {}),
        ]
    )
    one = client.get(f"/v1/github/user/{path}", params={"handle": "sample"}).json()
    two = client.get(
        f"/v1/github/user/{path}", params={"handle": "sample", "cursor": one["cursor"]}
    ).json()
    assert len(one[key]) == 30 and len(two[key]) == 3 and two["cursor"] is None
    assert len({x["id"] for x in one[key] + two[key]}) == 33
    assert calls[1][1]["page"] == 2 and calls[1][1]["per_page"] == 30
    if path == "repositories":
        assert one[key][0]["stars_count"] == 0 and one[key][0]["forks_count"] is None
        assert calls[0][1]["type"] == "owner" and calls[0][1]["sort"] == "updated"


def test_repository_shape_uses_public_urls_and_does_not_expose_private_fields(
    client, upstream
):
    _, replies = upstream
    replies.append((dict(REPO, permissions={"admin": True}), {}))
    body = client.get(
        "/v1/github/repository", params={"url": "https://github.com/sample/example.git"}
    ).json()
    repo = body["repository"]
    assert (
        repo["url"] == "https://github.com/sample/example"
        and repo["owner"]["url"] == USER["html_url"]
    )
    assert "permissions" not in repo and repo["license"]["spdx_id"] == "MIT"


@pytest.mark.parametrize(
    "payload", [dict(REPO, private=True), dict(REPO, private=None)]
)
def test_repository_must_be_explicitly_public(client, upstream, payload):
    _, replies = upstream
    replies.append((payload, {}))
    assert (
        client.get(
            "/v1/github/repository", params={"url": "https://github.com/sample/example"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "path,params,payload",
    [
        ("user", {"handle": "sample"}, dict(USER, login="someone-else")),
        (
            "repository",
            {"url": "https://github.com/sample/example"},
            dict(REPO, full_name="someone/else"),
        ),
    ],
)
def test_wrong_resource_identity_is_not_success(
    client, upstream, path, params, payload
):
    _, replies = upstream
    replies.append((payload, {}))
    assert client.get("/v1/github/" + path, params=params).status_code == 500


@pytest.mark.parametrize(
    "path,params",
    [
        ("user", {"url": "https://github.com.evil.example/sample"}),
        ("user", {"url": "https://someone@github.com/sample"}),
        ("user", {"handle": "sample", "url": "https://github.com/another"}),
        ("repository", {"url": "https://github.com/sample/example/issues"}),
        ("repository", {"url": "https://github.com/sample/%2e%2e"}),
        ("user/followers", {"handle": "sample", "cursor": 0}),
        ("user/pull-requests", {"handle": "sample", "since": "2025-02-30"}),
        (
            "user/pull-requests",
            {"handle": "sample", "since": "2025-12-01", "until": "2025-01-01"},
        ),
        ("trending/repositories", {"language": "../../private"}),
    ],
)
def test_invalid_inputs_never_reach_github(client, upstream, path, params):
    calls, _ = upstream
    assert client.get("/v1/github/" + path, params=params).status_code == 400
    assert not calls


def test_pull_requests_keep_filters_and_provider_pagination(client, upstream):
    calls, replies = upstream
    replies.append(
        (
            {
                "incomplete_results": False,
                "total_count": 31,
                "items": [
                    {
                        "pull_request": {"url": "x"},
                        "html_url": "https://github.com/sample/example/pull/10",
                        "title": "Change",
                        "state": "closed",
                        "created_at": "2025-04-01T00:00:00Z",
                    }
                ],
            },
            {"Link": '<https://api.github.com/search/issues?page=2>; rel="next"'},
        )
    )
    body = client.get(
        "/v1/github/user/pull-requests",
        params={"handle": "sample", "since": "2025-01-01", "until": "2025-12-31"},
    ).json()
    assert (
        body["pull_requests"][0]["repo"] == "sample/example"
        and body["has_more"]
        and body["cursor"] == 2
    )
    assert (
        calls[0][1]["q"]
        == "is:pr author:sample created:>=2025-01-01 created:<=2025-12-31"
    )


def test_incomplete_search_is_not_a_complete_history(client, upstream):
    _, replies = upstream
    replies.append(({"incomplete_results": True, "items": [], "total_count": 0}, {}))
    assert (
        client.get(
            "/v1/github/user/pull-requests", params={"handle": "sample"}
        ).status_code
        == 500
    )


def test_calendar_reads_zero_tooltips_and_sorts_dates(client, upstream):
    _, replies = upstream
    replies.append(
        BeautifulSoup(
            """<h2 id="js-contribution-activity-description">9 contributions in 2025</h2>
    <td id="d2" data-date="2025-01-02" data-level="0"></td><tool-tip for="d2">No contributions on January 2nd.</tool-tip>
    <td id="d1" data-date="2025-01-01" data-level="2"></td><tool-tip for="d1">9 contributions on January 1st.</tool-tip>""",
            "html.parser",
        )
    )
    body = client.get(
        "/v1/github/user/contributions", params={"handle": "sample", "year": 2025}
    ).json()["contributions"]
    assert body["total_contributions"] == 9
    assert [day["date"] for day in body["days"]] == ["2025-01-01", "2025-01-02"]
    assert [day["contribution_count"] for day in body["days"]] == [9, 0]


def test_activity_is_monthly_and_preserves_public_detail_links(client, upstream):
    calls, replies = upstream
    replies.append(
        BeautifulSoup(
            """<div class="contribution-activity-listing"><div class="TimelineItem">
      <div class="TimelineItem-body"><summary><span class="color-fg-default">Created 2 commits in 1 repository</span></summary>
      <ul><li><a href="/sample/example" data-hovercard-type="repository">sample/example</a>
      <a href="/sample/example/commits">2 commits</a></li></ul></div></div></div>""",
            "html.parser",
        )
    )
    body = client.get(
        "/v1/github/user/activity",
        params={"handle": "sample", "year": 2025, "cursor": 2},
    ).json()
    assert (
        body["month"] == "November 2025"
        and body["cursor"] == 3
        and not body["no_activity"]
    )
    assert (
        body["activity"][0]["details"][0]["repository"]["url"]
        == "https://github.com/sample/example"
    )
    assert (
        calls[0][1]["from"] == "2025-11-01"
        and calls[0][1]["to"] == "2025-11-30"
        and calls[0][2]["xhr"]
    )


def test_unparsed_html_cannot_masquerade_as_an_empty_list(client, upstream):
    _, replies = upstream
    for path in (
        "user/activity",
        "trending/repositories",
        "trending/developers",
        "user/contributions",
    ):
        replies.append(
            BeautifulSoup("<html><h1>Something went wrong</h1></html>", "html.parser")
        )
        assert (
            client.get("/v1/github/" + path, params={"handle": "sample"}).status_code
            == 500
        )


def test_trending_repositories_read_rank_counts_and_builders(client, upstream):
    _, replies = upstream
    replies.append(
        BeautifulSoup(
            """<article class="Box-row"><h2><a href="/sample/example">sample / example</a></h2>
      <p>Description</p><span itemprop="programmingLanguage">Python</span><span class="repo-language-color" style="background-color: #3572A5"></span>
      <a href="/sample/example/stargazers">1,234</a><a href="/sample/example/forks">0</a><span class="float-sm-right">9 stars this week</span>
      <a href="/sample"><img class="avatar-user" alt="@sample" src="https://avatars.example/7"></a></article>""",
            "html.parser",
        )
    )
    item = client.get(
        "/v1/github/trending/repositories", params={"since": "weekly"}
    ).json()["repositories"][0]
    assert (
        item["rank"] == 1 and item["stars_count"] == 1234 and item["forks_count"] == 0
    )
    assert (
        item["stars_today"] == 9
        and item["language_color"] == "#3572A5"
        and item["built_by"][0]["username"] == "sample"
    )


def test_trending_developers_distinguish_developer_and_nested_repository(
    client, upstream
):
    _, replies = upstream
    replies.append(
        BeautifulSoup(
            """<article class="Box-row"><h1><a href="/sample">Sample Developer</a></h1>
      <img class="avatar-user" src="https://avatars.example/7"><div><article><h1><a href="/sample/example">example</a></h1>
      <div class="f6 mt-1">Repository description</div></article></div></article>""",
            "html.parser",
        )
    )
    item = client.get("/v1/github/trending/developers").json()["developers"][0]
    assert (
        item["username"] == "sample"
        and item["popular_repository"]["full_name"] == "sample/example"
    )


@pytest.mark.parametrize(
    "status,headers,expected",
    [
        (404, {}, g.NotFound),
        (429, {}, g.UpstreamBlocked),
        (403, {"X-RateLimit-Remaining": "0"}, g.UpstreamBlocked),
        (403, {}, g.Forbidden),
        (422, {}, g.BadRequest),
    ],
)
def test_http_failures_are_classified_and_requests_are_anonymous(
    status, headers, expected
):
    class Network:
        def urlopen(self, request):
            assert request.url == "https://api.github.com/users/sample"
            assert (
                request.headers.get("Cookie") == ""
                and "Authorization" not in request.headers
            )
            raise HTTPError(
                Response(io.BytesIO(b"{}"), request.url, headers, status=status)
            )

    with pytest.raises(expected):
        g._read(Network(), "/users/sample")
