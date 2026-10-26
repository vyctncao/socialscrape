"""Linkme's public profile hydration data, parsed as data without script execution."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from fastapi import APIRouter, Query

from ..errors import BadRequest, Forbidden, UpstreamError
from ..registry import sc_get
from . import _public
from ._hydration import reference_object

router = APIRouter()
_HANDLE = re.compile(r"[A-Za-z0-9_.-]{1,100}$")


def _handle(url):
    try:
        parsed = urlsplit(url)
        name = unquote(parsed.path.strip("/"))
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname not in {"link.me", "www.link.me"}
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 80, 443)
            or not _HANDLE.fullmatch(name)
            or name in (".", "..")
        ):
            raise ValueError
        return name
    except ValueError:
        raise BadRequest("url must be a Linkme profile URL") from None


@sc_get(
    router,
    "/v1/linkme",
    source="Linkme public profile hydration state",
    notes="Reads the exact public profile object without executing scripts. Private profiles are refused. "
    "Unresolved serializer references cause an error instead of invented fields; no account cookies or paid API.",
)
async def profile(url: str = Query(...)):
    name = _handle(url)

    def go(ydl):
        response = _public.fetch(
            ydl, f"https://link.me/{name}", {"link.me", "www.link.me"}
        )
        soup = BeautifulSoup(response.body, "html.parser")
        script = soup.find("script", id="$tsr-stream-barrier")
        if not script:
            raise UpstreamError("Linkme returned no public profile hydration state")
        data = reference_object(script.string or script.get_text(), "profile")
        if (
            not data.get("id")
            or not isinstance(data.get("username"), str)
            or data["username"].lower() != name.lower()
        ):
            raise UpstreamError(
                "Linkme returned a different profile or no profile identity"
            )
        if data.get("isPrivate") in (True, 1, "1"):
            raise Forbidden("This Linkme profile is private")
        return {"profile": data}

    return await _public.run("linkme", go)
