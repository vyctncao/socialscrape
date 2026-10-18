"""Opaque pagination tokens.

ScrapeCreators clients pass back whatever token the previous page returned
(`continuationToken`, `cursor`, `max_id`, ...). When the platform's native
cursor isn't reachable through yt-dlp we hand out our own: url-safe base64 of
a small JSON object, prefixed so we can tell it apart from native tokens.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from .errors import BadRequest

PREFIX = "ss1."


def encode_cursor(state: dict[str, Any]) -> str:
    raw = json.dumps(state, separators=(",", ":"), sort_keys=True).encode()
    return PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(token: str | None) -> dict[str, Any] | None:
    """None/'' -> None (first page). Malformed -> 400."""
    if not token:
        return None
    if not token.startswith(PREFIX):
        raise BadRequest("Invalid cursor/continuation token", code="bad_cursor")
    body = token[len(PREFIX):]
    try:
        state = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, RecursionError) as exc:  # JSONDecodeError is a ValueError; deep nesting recurses
        raise BadRequest("Invalid cursor/continuation token", code="bad_cursor") from exc
    if not isinstance(state, dict):
        raise BadRequest("Invalid cursor/continuation token", code="bad_cursor")
    return state
