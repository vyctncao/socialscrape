"""Komi public talent profile and visible modules, as requested by its web app."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit
from uuid import UUID

from fastapi import APIRouter, Query

from ..errors import BadRequest, Forbidden, UpstreamError
from ..registry import sc_get
from . import _public

router = APIRouter()
API = "https://api.komi.io/api"
_HANDLE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,98}[A-Za-z0-9])?$")


def _handle(url):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 80, 443)
        ):
            raise ValueError
        host = parsed.hostname or ""
        if (
            host.endswith(".komi.io")
            and host.count(".") == 2
            and parsed.path in ("", "/")
        ):
            name = host[: -len(".komi.io")]
        elif host in ("komi.io", "www.komi.io"):
            name = unquote(parsed.path.strip("/"))
        else:
            raise ValueError
        if not _HANDLE.fullmatch(name):
            raise ValueError
        return name
    except ValueError:
        raise BadRequest("url must be a Komi public profile URL") from None


def _links(modules, depth=0):
    if depth > 8 or not isinstance(modules, list):
        raise UpstreamError("Komi returned invalid or deeply nested public modules")
    out = []
    for module in modules:
        if not isinstance(module, dict):
            raise UpstreamError("Komi returned an invalid public module")
        if module.get("visible") is False or module.get("deletedAt"):
            continue
        kind = module.get("type")
        items = module.get("items")
        if not isinstance(items, list):
            raise UpstreamError("Komi returned a public module without its items")
        if kind == "GROUP":
            out.extend(_links(items, depth + 1))
            continue
        for item in items:
            if not isinstance(item, dict):
                raise UpstreamError("Komi returned an invalid public module item")
            if item.get("visible") is False or not isinstance(item.get("url"), str):
                continue
            keys = (
                "id",
                "url",
                "order",
                "title",
                "visible",
                "moduleId",
                "thumbnail",
                "versionId",
            )
            out.append({**{key: item.get(key) for key in keys}, "type": kind})
    return out


@sc_get(
    router,
    "/v1/komi",
    source="Komi anonymous public talent username and profile modules API",
    notes="Public profile plus visible, non-deleted module links. Draft/hidden modules and signup secrets are excluded. "
    "Both profile and modules must load; a failed second request is not a partial success. No account tokens.",
)
async def profile(url: str = Query(...)):
    name = _handle(url)

    def go(ydl):
        root = _public.fetch(
            ydl, f"{API}/talent/usernames/{name}", {"api.komi.io"}
        ).json()
        if (
            not isinstance(root, dict)
            or not isinstance(root.get("username"), str)
            or root["username"].lower() != name.lower()
        ):
            raise UpstreamError("Komi returned a different public account")
        data = root.get("talentProfile")
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            raise UpstreamError("Komi returned no public profile identity")
        try:
            uid = str(UUID(data["id"]))
        except ValueError:
            raise UpstreamError(
                "Komi returned an invalid public profile identity"
            ) from None
        if str(root.get("accountStatus", "")).lower() in {
            "suspended",
            "deleted",
            "disabled",
            "deactivated",
            "blocked",
        } or str(data.get("status", "")).lower() in {"draft", "private", "unpublished"}:
            raise Forbidden("This Komi profile is not public")
        modules = _public.fetch(
            ydl, f"{API}/talent-profiles/{uid}/modules", {"api.komi.io"}
        ).json()
        keys = (
            "id",
            "avatar",
            "bio",
            "firstName",
            "lastName",
            "displayName",
            "displayNameImage",
            "instagram",
            "tiktok",
            "youtube",
            "twitter",
            "facebook",
            "snapchat",
            "website",
        )
        result = {
            **{key: data.get(key) for key in keys},
            "username": root["username"],
            "links": _links(modules),
        }
        for link in data.get("socialProfileLinks") or []:
            if (
                isinstance(link, dict)
                and isinstance(link.get("type"), str)
                and isinstance(link.get("link"), str)
            ):
                key = link["type"].lower()
                if key in keys:
                    result[key] = link["link"]
        return result

    return await _public.run("komi", go)
