"""Public Linktree profile state, including declared social and contact links."""

from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from fastapi import APIRouter, Query

from ..errors import BadRequest, Forbidden, NotFound, UpstreamBlocked, UpstreamError
from ..registry import sc_get
from . import _public

router = APIRouter()
_HANDLE = re.compile(r"[A-Za-z0-9_.-]{1,100}$")


def _handle(url: str) -> str:
    try:
        parsed = urlsplit(url)
        name = unquote(parsed.path.strip("/"))
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname not in ("linktr.ee", "www.linktr.ee")
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 80, 443)
            or not _HANDLE.fullmatch(name)
            or name in (".", "..")
        ):
            raise ValueError
        return name
    except ValueError:
        raise BadRequest("url must be a Linktree profile URL") from None


def _visible_link(link: dict, depth: int = 0) -> dict:
    if depth > 8:
        raise UpstreamError("Linktree links were nested too deeply")
    if not isinstance(link, dict):
        raise UpstreamError("Linktree returned an invalid public link")
    if link.get("locked") is True:
        # The public UI exposes a placeholder, not the destination behind its lock.
        return {
            "id": link.get("id"),
            "type": link.get("type"),
            "title": link.get("title"),
            "url": None,
            "locked": True,
        }
    result = dict(link)
    if isinstance(result.get("children"), list):
        result["children"] = [
            _visible_link(child, depth + 1) for child in result["children"]
        ]
    return result


@sc_get(
    router,
    "/v1/linktree",
    source="Linktree public profile __NEXT_DATA__",
    notes="Public profile, visible links and declared social/contact links. Page locks are respected; "
    "locked link destinations are redacted. Browser transport carries no account cookies or paid-provider key.",
)
async def profile(url: str = Query(...)):
    name = _handle(url)

    def go(ydl):
        response = _public.fetch(
            ydl, f"https://linktr.ee/{name}", {"linktr.ee", "www.linktr.ee"}
        )
        soup = BeautifulSoup(response.body, "html.parser")
        script = soup.select_one("script#__NEXT_DATA__")
        if not script:
            if soup.title and re.search(
                r"just a moment|verify|challenge", soup.title.get_text(), re.I
            ):
                raise UpstreamBlocked("Linktree blocked this connection")
            raise UpstreamError("Linktree returned no readable public profile state")
        try:
            props = (
                json.loads(script.string or script.get_text())
                .get("props", {})
                .get("pageProps", {})
            )
        except (ValueError, AttributeError, RecursionError) as exc:
            raise UpstreamError(
                "Linktree returned invalid public profile state"
            ) from exc
        account = props.get("account")
        if (
            not isinstance(account, dict)
            or not account.get("id")
            or not isinstance(account.get("username"), str)
        ):
            raise UpstreamError("Linktree returned no profile identity")
        if account.get("deleted") is True or account.get("isActive") is False:
            raise NotFound("Linktree profile is not public")
        if account["username"].lower() != name.lower():
            raise UpstreamError("Linktree returned a different profile")
        if (
            props.get("hasPagePassword")
            or props.get("pageLockType")
            and not props.get("hasUnlockedProfile")
        ):
            raise Forbidden("This Linktree profile requires an unlock")
        links = props.get("links")
        if not isinstance(links, list):
            raise UpstreamError("Linktree returned no public link list")
        keys = (
            "id",
            "username",
            "profilePictureUrl",
            "description",
            "verticals",
            "linkPlatforms",
            "timezone",
        )
        result = {
            **{key: account.get(key) for key in keys},
            "links": [_visible_link(link) for link in links],
        }
        socials = props.get("socialLinks") or []
        if not isinstance(socials, list):
            raise UpstreamError("Linktree returned an invalid social link list")
        for link in socials:
            if (
                not isinstance(link, dict)
                or not isinstance(link.get("type"), str)
                or not isinstance(link.get("url"), str)
            ):
                continue
            key = link["type"].lower()
            if key in {
                "instagram",
                "tiktok",
                "spotify",
                "youtube",
                "soundcloud",
                "apple_music",
                "email_address",
                "twitter",
                "facebook",
                "twitch",
                "snapchat",
                "linkedin",
                "pinterest",
                "threads",
                "bluesky",
            }:
                value = link["url"]
                result[key] = (
                    unquote(value.removeprefix("mailto:").split("?", 1)[0])
                    if key == "email_address"
                    else value
                )
        return result

    return await _public.run("linktree", go)
