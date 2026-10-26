"""Public Telegram channel previews and post widgets; no Telegram account login."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import unquote, urlencode, urlsplit

from bs4 import BeautifulSoup, Tag
from fastapi import APIRouter, Query

from ..errors import BadRequest, Forbidden, NotFound, UpstreamError
from ..registry import sc_get
from ..util import parse_count
from . import _public

router = APIRouter()
WEB = "https://t.me"
_HANDLE = re.compile(r"[A-Za-z0-9_]{1,32}$")
_BG = re.compile(r"background-image:\s*url\(['\"]?(https?://[^)'\"]+)['\"]?\)", re.I)


def _handle(value: str) -> str:
    name = value.strip().removeprefix("@")
    if not _HANDLE.fullmatch(name) or name.lower() in {
        "s",
        "c",
        "joinchat",
        "share",
        "iv",
        "proxy",
        "addstickers",
    }:
        raise BadRequest("handle must be a public Telegram channel username")
    return name


def _post_ref(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(value)
        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
        if parts and parts[0] == "s":
            parts = parts[1:]
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname not in ("t.me", "telegram.me", "www.t.me")
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 80, 443)
            or len(parts) != 2
            or not parts[1].isascii()
            or not parts[1].isdigit()
            or int(parts[1]) <= 0
        ):
            raise ValueError
        return _handle(parts[0]), str(int(parts[1]))
    except ValueError:
        raise BadRequest(
            "url must identify a post in a public Telegram channel"
        ) from None


def _text(node) -> str | None:
    return " ".join(node.stripped_strings) if isinstance(node, Tag) else None


def _message_text(node) -> str | None:
    if not isinstance(node, Tag):
        return None
    for br in node.find_all("br"):
        br.replace_with("\n")
    return node.get_text().strip()


def _count(value: str | None) -> int | None:
    # Telegram separates exact thousands with spaces as well as abbreviating M/K.
    return (
        parse_count(re.sub(r"(?<=\d)[\s\u00a0]+(?=\d)", "", value)) if value else None
    )


def _soup(ydl, path: str):
    response = _public.fetch(ydl, WEB + path, {"t.me", "www.t.me"})
    return BeautifulSoup(response.body, "html.parser")


def _channel(soup, handle: str) -> dict:
    info = soup.select_one(".tgme_channel_info")
    if not info:
        raise Forbidden(
            "No public channel preview is available for this Telegram username"
        )
    identity = info.select_one(".tgme_channel_info_header_username a")
    if (
        not identity
        or not _text(identity)
        or _text(identity).lstrip("@").lower() != handle.lower()
    ):
        raise UpstreamError("Telegram returned a different channel")
    avatar = info.select_one(
        ".tgme_page_photo_image img, img.tgme_page_photo_image, .tgme_page_photo img"
    )
    title = info.select_one(".tgme_channel_info_header_title")
    if not title or not _text(title):
        raise UpstreamError("Telegram returned no channel name")
    result = {
        "handle": _text(identity).lstrip("@"),
        "name": _text(title),
        "description": _message_text(info.select_one(".tgme_channel_info_description")),
        "url": WEB + "/" + handle,
        "avatar_url": avatar.get("src") if avatar else None,
        "is_verified": info.select_one(".verified-icon") is not None,
        **{
            key: None
            for key in (
                "subscriber_count",
                "subscriber_count_text",
                "member_count",
                "member_count_text",
                "photo_count",
                "video_count",
                "file_count",
                "link_count",
            )
        },
    }
    kinds = {
        "subscribers": "subscriber",
        "members": "member",
        "photos": "photo",
        "videos": "video",
        "files": "file",
        "links": "link",
    }
    for counter in info.select(".tgme_channel_info_counter"):
        kind = kinds.get((_text(counter.select_one(".counter_type")) or "").lower())
        if kind:
            value = _text(counter.select_one(".counter_value"))
            result[kind + "_count"] = _count(value)
            if kind in ("subscriber", "member"):
                result[kind + "_count_text"] = value
    return result


def _media(post) -> list[dict]:
    media = []
    for video in post.select("video[src]"):
        item = {"type": "video", "url": video["src"], "thumbnail": video.get("poster")}
        media.append(item)
    for photo in post.select(".tgme_widget_message_photo_wrap"):
        match = _BG.search(photo.get("style", ""))
        if match:
            media.append({"type": "photo", "url": match[1]})
    for audio in post.select("audio[src]"):
        media.append({"type": "audio", "url": audio["src"]})
    return list({item["url"]: item for item in media}.values())


def _post(node, handle: str) -> dict:
    identity = node.get("data-post", "").split("/")
    if (
        len(identity) != 2
        or identity[0].lower() != handle.lower()
        or not identity[1].isdigit()
    ):
        raise UpstreamError("Telegram returned a different post or no post identity")
    author = node.select_one(".tgme_widget_message_author a")
    timestamp = node.select_one("time[datetime]")
    if not timestamp:
        raise UpstreamError("Telegram returned a post without a publication time")
    stamp = timestamp.get("datetime")
    try:
        if datetime.fromisoformat(stamp).tzinfo is None:
            raise ValueError
    except (ValueError, TypeError):
        raise UpstreamError(
            "Telegram returned an invalid post publication time"
        ) from None
    views = _text(node.select_one(".tgme_widget_message_views"))
    reactions = []
    for reaction in node.select(".tgme_reaction"):
        emoji = reaction.select_one("tg-emoji[emoji-id]")
        label = "⭐" if reaction.select_one(".icon-telegram-stars") else None
        content = _text(reaction) or ""
        # Native emoji may be literal text before the displayed count.
        if label is None and not emoji:
            label = re.sub(r"[\d.,]+\s*[KMBkmb]?\s*$", "", content).strip() or None
        count = re.search(r"([\d.,]+\s*[KMBkmb]?)\s*$", content)
        count_text = count[1] if count else None
        reactions.append(
            {
                "emoji": label,
                "emoji_id": emoji.get("emoji-id") if emoji else None,
                "count": _count(count_text),
                "count_text": count_text,
            }
        )
    container = node.select_one(".tgme_widget_message_reactions")
    total = (
        sum(item["count"] for item in reactions)
        if container and all(item["count"] is not None for item in reactions)
        else None
    )
    forwarded = node.select_one(".tgme_widget_message_forwarded_from")
    link = forwarded.select_one("a[href]") if forwarded else None
    preview = node.select_one("a.tgme_widget_message_link_preview")
    return {
        "id": identity[1],
        "channel_handle": identity[0],
        "url": f"{WEB}/{identity[0]}/{identity[1]}",
        "author_name": _text(author),
        "author_url": author.get("href") if author else None,
        "text": _message_text(node.select_one(".tgme_widget_message_text")),
        "published_at": stamp,
        "view_count": _count(views),
        "view_count_text": views,
        "reactions": reactions,
        "reaction_count": total,
        "forwarded_from": {
            "name": _text(forwarded),
            "url": link["href"] if link else None,
        }
        if forwarded
        else None,
        "media": _media(node),
        "link_preview": {
            "url": preview.get("href"),
            "title": _text(preview.select_one(".link_preview_title")),
            "description": _message_text(
                preview.select_one(".link_preview_description")
            ),
        }
        if preview
        else None,
    }


@sc_get(
    router,
    "/v1/telegram/channel",
    source="Telegram public /s/{handle} channel preview HTML",
    notes="Public channel metadata and displayed counters. Abbreviated K/M counts are approximate; hidden counters stay null. No account login.",
)
async def channel(handle: str = Query(...)):
    name = _handle(handle)
    return await _public.run(
        "telegram", lambda ydl: _channel(_soup(ydl, f"/s/{name}"), name)
    )


@sc_get(
    router,
    "/v1/telegram/channel/posts",
    source="Telegram public /s/{handle} HTML with native before pagination",
    notes="Preserves the full public preview page and its before cursor. Private channels or disabled previews return 403. "
    "Displayed counts are approximate when abbreviated; unavailable media is not downloaded or invented.",
)
async def posts(handle: str = Query(...), cursor: str | None = None):
    name = _handle(handle)
    if cursor is not None and (
        not re.fullmatch(r"[0-9]{1,20}", cursor) or int(cursor) <= 0
    ):
        raise BadRequest(
            "cursor must be a positive Telegram post ID", code="bad_cursor"
        )

    def go(ydl):
        soup = _soup(
            ydl, f"/s/{name}" + ("?" + urlencode({"before": cursor}) if cursor else "")
        )
        info = _channel(soup, name)
        nodes = soup.select(".tgme_widget_message[data-post]")
        items = [_post(node, name) for node in nodes]
        if not items and not soup.select_one(".tgme_no_messages"):
            raise UpstreamError(
                "Telegram returned no readable posts or explicit empty-state message"
            )
        more = soup.select_one("a.tme_messages_more[data-before]")
        token = more.get("data-before") if more else None
        if token is not None and (
            not re.fullmatch(r"[0-9]{1,20}", token)
            or cursor
            and int(token) >= int(cursor)
        ):
            raise UpstreamError("Telegram returned a non-advancing pagination cursor")
        return {
            "channel": info,
            "posts": items,
            "cursor": token,
            "has_more": token is not None,
        }

    return await _public.run("telegram", go)


@sc_get(
    router,
    "/v1/telegram/post",
    source="Telegram public post embed widget HTML",
    notes="Exact public channel and post identity, publication time, visible text, reactions, media and displayed views. "
    "Custom reactions keep their emoji IDs when the widget omits Unicode text; hidden counts stay null.",
)
async def post(url: str = Query(...)):
    name, key = _post_ref(url)

    def go(ydl):
        soup = _soup(ydl, f"/{name}/{key}?embed=1")
        node = soup.select_one(".tgme_widget_message[data-post]")
        if not node:
            error = _text(soup.select_one(".tgme_widget_message_error")) or ""
            if "not found" in error.lower() or "deleted" in error.lower():
                raise NotFound("Telegram public post not found")
            if error:
                raise Forbidden(
                    "Telegram does not expose this post in its public widget"
                )
            raise UpstreamError("Telegram returned no readable public post widget")
        item = _post(node, name)
        if item["id"] != key:
            raise UpstreamError("Telegram returned a different requested post")
        return item

    return await _public.run("telegram", go)
