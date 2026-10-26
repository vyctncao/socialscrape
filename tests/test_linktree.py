import json
from types import SimpleNamespace

import pytest

from socialscrape.platforms import linktree as l


@pytest.fixture
def public(monkeypatch):
    calls, replies = [], []

    async def run(platform, fn):
        assert platform == "linktree"
        return fn(None)

    def fetch(ydl, url, hosts):
        calls.append(url)
        assert hosts == {"linktr.ee", "www.linktr.ee"}
        return SimpleNamespace(body=replies.pop(0))

    monkeypatch.setattr(l._public, "run", run)
    monkeypatch.setattr(l._public, "fetch", fetch)
    return calls, replies


def page(**values):
    props = {
        "account": {
            "id": 7,
            "username": "sample",
            "isActive": True,
            "description": "Public biography",
            "timezone": "UTC",
        },
        "links": [
            {
                "id": 1,
                "type": "CLASSIC",
                "title": "Site",
                "url": "https://public.example/",
            }
        ],
        "socialLinks": [
            {"type": "INSTAGRAM", "url": "https://instagram.com/sample"},
            {
                "type": "EMAIL_ADDRESS",
                "url": "mailto:creator@example.com?subject=Business",
            },
        ],
        **values,
    }
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": props}})
        + "</script>"
    ).encode()


def test_public_profile_keeps_only_the_requested_account_and_declared_links(
    client, public
):
    calls, replies = public
    replies.append(page())
    r = client.get("/v1/linktree", params={"url": "https://linktr.ee/sample"})
    assert r.status_code == 200 and r.json()["username"] == "sample"
    assert (
        r.json()["email_address"] == "creator@example.com"
        and r.json()["links"][0]["url"] == "https://public.example/"
    )
    assert calls == ["https://linktr.ee/sample"]


def test_profile_lock_is_respected_and_individual_locked_destinations_are_redacted(
    client, public
):
    _, replies = public
    replies.extend(
        [
            page(hasPagePassword=True),
            page(
                links=[
                    {
                        "id": 1,
                        "title": "Locked",
                        "locked": True,
                        "url": "https://secret.example/",
                    }
                ]
            ),
        ]
    )
    assert (
        client.get(
            "/v1/linktree", params={"url": "https://linktr.ee/sample"}
        ).status_code
        == 403
    )
    result = client.get(
        "/v1/linktree", params={"url": "https://linktr.ee/sample"}
    ).json()
    assert result["links"][0]["url"] is None and "secret.example" not in json.dumps(
        result
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/sample",
        "https://linktr.ee/sample/another",
        "https://user@linktr.ee/sample",
        "https://linktr.ee/%2e%2e",
    ],
)
def test_invalid_profile_urls_never_fetch_a_page(client, public, url):
    calls, _ = public
    assert (
        client.get("/v1/linktree", params={"url": url}).status_code == 400 and not calls
    )


def test_wrong_or_unreadable_public_identity_does_not_become_success(client, public):
    _, replies = public
    replies.extend(
        [page(account={"id": 7, "username": "another"}), b"<html>Error</html>"]
    )
    for _ in range(2):
        assert (
            client.get(
                "/v1/linktree", params={"url": "https://linktr.ee/sample"}
            ).status_code
            == 500
        )
