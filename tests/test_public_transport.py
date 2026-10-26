"""Public fetches cannot inherit account secrets or follow external redirects."""

from types import SimpleNamespace

import pytest
from curl_cffi.requests.exceptions import RequestException

from socialscrape.platforms import _public as p


@pytest.fixture
def upstream(monkeypatch):
    calls, replies = [], []

    class Session:
        def __init__(self, **options):
            assert options == {"trust_env": False}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **options):
            calls.append((url, options))
            assert (
                options["allow_redirects"] is False
                and options["headers"]["Cookie"] == ""
            )
            assert "Authorization" not in options["headers"]
            status, headers, chunks = replies.pop(0)
            for chunk in chunks:
                if options["content_callback"](chunk) != len(chunk):
                    raise RequestException("upstream write callback aborted")
            return SimpleNamespace(status_code=status, headers=headers)

    monkeypatch.setattr(p.requests, "Session", Session)
    return calls, replies


def test_public_redirect_keeps_the_anonymous_context_and_full_body(upstream):
    calls, replies = upstream
    replies.extend(
        [
            (302, {"location": "/canonical"}, []),
            (200, {"content-type": "application/json"}, [b'{"id":', b"1}"]),
        ]
    )
    result = p.fetch(
        SimpleNamespace(params={}), "https://public.example/profile", {"public.example"}
    )
    assert (
        result.json() == {"id": 1} and result.url == "https://public.example/canonical"
    )
    assert [call[0] for call in calls] == [
        "https://public.example/profile",
        "https://public.example/canonical",
    ]


@pytest.mark.parametrize(
    "target",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://external.example/private",
        "https://user:password@public.example/secret",
        "https://public.example:8888/internal",
    ],
)
def test_profile_redirects_cannot_be_used_to_fetch_other_origins(upstream, target):
    calls, replies = upstream
    replies.append((302, {"location": target}, []))
    with pytest.raises(p.Forbidden):
        p.fetch(
            SimpleNamespace(params={}),
            "https://public.example/profile",
            {"public.example"},
        )
    assert len(calls) == 1


def test_oversized_decompressed_response_stops_before_buffering_more(
    upstream, monkeypatch
):
    _, replies = upstream
    monkeypatch.setattr(p, "MAX_BYTES", 8)
    replies.append((200, {}, [b"12345678", b"9" * 100]))
    with pytest.raises(p.UpstreamError, match="size limit"):
        p.fetch(
            SimpleNamespace(params={}),
            "https://public.example/profile",
            {"public.example"},
        )


@pytest.mark.parametrize(
    "status,expected",
    [
        (404, p.NotFound),
        (401, p.Forbidden),
        (403, p.UpstreamBlocked),
        (429, p.UpstreamBlocked),
        (503, p.UpstreamError),
        (422, p.BadRequest),
    ],
)
def test_platform_failures_keep_their_resource_or_connection_state(
    upstream, status, expected
):
    _, replies = upstream
    replies.append((status, {}, [b"error"]))
    with pytest.raises(expected):
        p.fetch(
            SimpleNamespace(params={}),
            "https://public.example/profile",
            {"public.example"},
        )
