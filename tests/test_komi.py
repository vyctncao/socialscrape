from types import SimpleNamespace
import pytest
from socialscrape.platforms import komi as k

UID = "64d82830-59aa-4488-bfb0-93426971d139"


@pytest.fixture
def public(monkeypatch):
    calls, replies = [], []

    async def run(platform, fn):
        assert platform == "komi"
        return fn(None)

    def fetch(ydl, url, hosts):
        assert hosts == {"api.komi.io"}
        calls.append(url)
        value = replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(json=lambda: value)

    monkeypatch.setattr(k._public, "run", run)
    monkeypatch.setattr(k._public, "fetch", fetch)
    return calls, replies


def account(**values):
    return {
        "username": "sample",
        "accountStatus": "active",
        "talentProfile": {
            "id": UID,
            "status": "completed",
            "bio": "",
            "socialProfileLinks": [
                {"type": "YOUTUBE", "link": "https://youtube.com/@sample"}
            ],
            **values,
        },
    }


def test_profile_and_visible_modules_are_read_together_and_hidden_content_stays_out(
    client, public
):
    calls, replies = public
    replies.extend(
        [
            account(),
            [
                {
                    "type": "GROUP",
                    "visible": True,
                    "items": [
                        {
                            "type": "LINK",
                            "visible": True,
                            "items": [
                                {
                                    "id": "l1",
                                    "url": "https://public.example",
                                    "visible": True,
                                }
                            ],
                        },
                        {
                            "type": "PRODUCT",
                            "visible": False,
                            "items": [
                                {"url": "https://hidden.example", "visible": True}
                            ],
                        },
                    ],
                }
            ],
        ]
    )
    data = client.get("/v1/komi", params={"url": "https://sample.komi.io/"}).json()
    assert data["id"] == UID and data["username"] == "sample" and data["bio"] == ""
    assert data["youtube"] == "https://youtube.com/@sample" and len(data["links"]) == 1
    assert (
        data["links"][0]["type"] == "LINK"
        and data["links"][0]["url"] == "https://public.example"
    )
    assert calls == [
        k.API + "/talent/usernames/sample",
        k.API + "/talent-profiles/" + UID + "/modules",
    ]


def test_failed_modules_are_not_a_partial_profile_success(client, public):
    _, replies = public
    replies.extend([account(), k.UpstreamError("modules unavailable")])
    assert (
        client.get("/v1/komi", params={"url": "https://sample.komi.io"}).status_code
        == 500
    )


def test_unpublished_profile_is_not_exposed(client, public):
    calls, replies = public
    replies.append(account(status="draft"))
    assert (
        client.get("/v1/komi", params={"url": "https://sample.komi.io"}).status_code
        == 403
        and len(calls) == 1
    )


def test_foreign_origins_do_not_trigger_api_requests(client, public):
    calls, _ = public
    for url in [
        "https://sample.komi.io.evil.example",
        "https://user@sample.komi.io",
        "https://sample.komi.io/other",
    ]:
        assert client.get("/v1/komi", params={"url": url}).status_code == 400
    assert not calls
