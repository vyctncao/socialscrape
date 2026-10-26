from types import SimpleNamespace

import pytest

from socialscrape.platforms import telegram as t

INFO = """<div class="tgme_channel_info"><div class="tgme_channel_info_header_title">Sample Channel</div>
<div class="tgme_channel_info_header_username"><a href="https://t.me/sample">@sample</a></div><i class="verified-icon"></i>
<div class="tgme_channel_info_description">Public description</div><div class="tgme_channel_info_counter"><span class="counter_value">1.2M</span><span class="counter_type">subscribers</span></div></div>"""


def message(key, views="0"):
    return f"""<div class="tgme_widget_message" data-post="sample/{key}"><div class="tgme_widget_message_author"><a href="https://t.me/sample">Sample</a></div>
    <div class="tgme_widget_message_text">First line<br>Second <b>line</b></div><time datetime="2025-01-01T00:00:00+00:00"></time>
    <span class="tgme_widget_message_views">{views}</span><div class="tgme_widget_message_reactions"><span class="tgme_reaction"><i class="icon-telegram-stars"></i>2K</span>
    <span class="tgme_reaction"><tg-emoji emoji-id="123"></tg-emoji>0</span></div></div>"""


@pytest.fixture
def public(monkeypatch):
    calls, replies = [], []

    async def run(platform, fn):
        assert platform == "telegram"
        return fn(None)

    def fetch(ydl, url, hosts):
        calls.append(url)
        assert hosts == {"t.me", "www.t.me"}
        return SimpleNamespace(body=replies.pop(0).encode())

    monkeypatch.setattr(t._public, "run", run)
    monkeypatch.setattr(t._public, "fetch", fetch)
    return calls, replies


def test_public_channel_counts_are_displayed_values_and_hidden_counters_stay_unknown(
    client, public
):
    _, replies = public
    replies.append(INFO)
    data = client.get("/v1/telegram/channel", params={"handle": "sample"}).json()
    assert (
        data["handle"] == "sample"
        and data["subscriber_count"] == 1200000
        and data["is_verified"]
    )
    assert data["photo_count"] is None and data["subscriber_count_text"] == "1.2M"
    assert t._count("9 242 921") == 9242921


def test_full_public_pages_keep_the_native_before_cursor_without_overlap(
    client, public
):
    calls, replies = public
    replies.extend(
        [
            INFO
            + message(10)
            + message(11)
            + '<a class="tme_messages_more" data-before="10"></a>',
            INFO + message(8) + message(9),
        ]
    )
    one = client.get("/v1/telegram/channel/posts", params={"handle": "sample"}).json()
    two = client.get(
        "/v1/telegram/channel/posts",
        params={"handle": "sample", "cursor": one["cursor"]},
    ).json()
    assert [p["id"] for p in one["posts"] + two["posts"]] == ["10", "11", "8", "9"]
    assert one["has_more"] and not two["has_more"] and two["cursor"] is None
    assert calls[1] == "https://t.me/s/sample?before=10"


def test_post_identity_text_zero_and_custom_reactions_survive(client, public):
    _, replies = public
    replies.append(message(10))
    data = client.get(
        "/v1/telegram/post", params={"url": "https://t.me/sample/10"}
    ).json()
    assert (
        data["id"] == "10"
        and data["text"] == "First line\nSecond line"
        and data["view_count"] == 0
    )
    assert (
        data["reactions"][0]["emoji"] == "⭐"
        and data["reactions"][1]["emoji_id"] == "123"
    )
    assert data["reaction_count"] == 2000


@pytest.mark.parametrize(
    "url",
    [
        "https://t.me/c/123/1",
        "https://t.me/+private/1",
        "https://evil.example/sample/10",
        "https://user@t.me/sample/10",
    ],
)
def test_private_or_invalid_post_urls_do_not_fetch(client, public, url):
    calls, _ = public
    assert (
        client.get("/v1/telegram/post", params={"url": url}).status_code == 400
        and not calls
    )


def test_wrong_post_identity_and_nonadvancing_page_cannot_pass(client, public):
    _, replies = public
    replies.extend(
        [
            message(11),
            INFO + message(10) + '<a class="tme_messages_more" data-before="10"></a>',
        ]
    )
    assert (
        client.get(
            "/v1/telegram/post", params={"url": "https://t.me/sample/10"}
        ).status_code
        == 500
    )
    assert (
        client.get(
            "/v1/telegram/channel/posts", params={"handle": "sample", "cursor": "10"}
        ).status_code
        == 500
    )
