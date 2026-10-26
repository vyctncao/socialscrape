import pytest
from types import SimpleNamespace
from socialscrape.platforms import linkme as l


@pytest.fixture
def public(monkeypatch):
    calls, replies = [], []

    async def run(platform, fn):
        assert platform == "linkme"
        return fn(None)

    def fetch(ydl, url, hosts):
        calls.append(url)
        return SimpleNamespace(body=replies.pop(0).encode())

    monkeypatch.setattr(l._public, "run", run)
    monkeypatch.setattr(l._public, "fetch", fetch)
    return calls, replies


def test_public_hydrated_profile_keeps_its_exact_fields_without_script_execution(
    client, public
):
    calls, replies = public
    replies.append(
        '<script id="$tsr-stream-barrier">profile:$R[1]={id:"7",username:"sample",isPrivate:0,totalLinks:0,webLinks:$R[2]=[]};</script>'
    )
    data = client.get("/v1/linkme", params={"url": "https://link.me/sample"}).json()[
        "profile"
    ]
    assert data == {
        "id": "7",
        "username": "sample",
        "isPrivate": 0,
        "totalLinks": 0,
        "webLinks": [],
    }
    assert calls == ["https://link.me/sample"]


@pytest.mark.parametrize(
    "body,status",
    [
        ('{id:"7",username:"sample",isPrivate:1}', 403),
        ('{id:"7",username:"another"}', 500),
        ('{id:maliciousFunction(),username:"sample"}', 500),
    ],
)
def test_private_wrong_and_nonliteral_profiles_cannot_pass(
    client, public, body, status
):
    _, replies = public
    replies.append(
        '<script id="$tsr-stream-barrier">profile:$R[1]=' + body + ";</script>"
    )
    assert (
        client.get("/v1/linkme", params={"url": "https://link.me/sample"}).status_code
        == status
    )
