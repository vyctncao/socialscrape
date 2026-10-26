"""Lnk.Bio's public profile identity and owner-published links."""

from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from fastapi import APIRouter, Query

from ..errors import BadRequest, Forbidden, UpstreamError
from ..registry import sc_get
from . import _public

router = APIRouter()
_HANDLE = re.compile(r"[A-Za-z0-9_.-]{1,100}$")


def _handle(url):
    try:
        value = urlsplit(url)
        handle = unquote(value.path.strip("/"))
        if (
            value.scheme not in ("http", "https")
            or value.hostname not in {"lnk.bio", "www.lnk.bio"}
            or value.username
            or value.password
            or value.port not in (None, 80, 443)
            or not _HANDLE.fullmatch(handle)
            or handle in (".", "..")
        ):
            raise ValueError
        return handle
    except ValueError:
        raise BadRequest("url must be a Lnk.Bio profile URL") from None


@sc_get(
    router,
    "/v1/linkbio",
    source="Lnk.Bio public profile HTML with explicit identity and link attributes",
    notes="Extracts the requested public profile's identity and its own published links, excluding navigation "
    "and recommendations. Social fields use dedicated social links; unexposed fields remain null. No account cookies.",
)
async def profile(url: str = Query(...)):
    handle = _handle(url)

    def go(ydl):
        response = _public.fetch(
            ydl, f"https://lnk.bio/{handle}", {"lnk.bio", "www.lnk.bio"}
        )
        soup = BeautifulSoup(response.body, "html.parser")
        identity = soup.select_one("#profile_picture_catch_error[data-username]")
        if not identity:
            if soup.select_one('input[type="password"]'):
                raise Forbidden("This Lnk.Bio profile requires an unlock")
            raise UpstreamError("Lnk.Bio returned no public profile identity")
        try:
            metadata = json.loads(identity.get("rel", "{}"))
        except (ValueError, TypeError) as exc:
            raise UpstreamError(
                "Lnk.Bio returned invalid profile identity metadata"
            ) from exc
        if (
            not isinstance(metadata, dict)
            or not metadata.get("id")
            or identity["data-username"].lower() != handle.lower()
            or str(metadata.get("username", "")).lower() != handle.lower()
        ):
            raise UpstreamError("Lnk.Bio returned a different profile")
        uid = str(metadata["id"])
        result = {
            "handle": identity["data-username"],
            "id": uid,
            "links": [],
            **{
                name: None
                for name in (
                    "instagram",
                    "email",
                    "tiktok",
                    "youtube",
                    "twitter",
                    "whatsapp",
                    "website",
                )
            },
        }
        seen = set()
        for link in soup.select("a[data-uid][data-href]"):
            if link.get("data-uid") != uid or link.get("data-locked") in ("true", "1"):
                continue
            href = link.get("data-href")
            if not href or href in seen:
                continue
            seen.add(href)
            result["links"].append(
                {"url": href, "text": link.get_text(" ", strip=True)}
            )
            if "SOCIAL" in link.get("data-type", ""):
                host = (urlsplit(href).hostname or "").removeprefix("www.")
                platform = {
                    "instagram.com": "instagram",
                    "tiktok.com": "tiktok",
                    "youtube.com": "youtube",
                    "twitter.com": "twitter",
                    "x.com": "twitter",
                    "wa.me": "whatsapp",
                }.get(host)
                if platform:
                    result[platform] = href
                elif href.startswith("mailto:"):
                    result["email"] = unquote(href[7:].split("?", 1)[0])
        return result

    return await _public.run("linkbio", go)
