"""Read literal objects in public hydration streams without executing JavaScript.

Supports the serializer's $R[n]= assignment wrappers and boolean shorthands.
References to unavailable state and all executable expressions are rejected.
"""

from __future__ import annotations

import json
import re

from ..errors import UpstreamError

MAX_STATE_BYTES = 2 * 1024 * 1024
_ASSIGN = re.compile(r"\$R\[[0-9]{1,8}\]\s*=")
_KEY = re.compile(r"(?<=[{,])(\s*)([A-Za-z_$][A-Za-z0-9_$]*)(\s*):")


def reference_object(script: str, key: str) -> dict:
    match = re.search(
        r"(?:\b"
        + re.escape(key)
        + r'|"'
        + re.escape(key)
        + r'")\s*:\s*\$R\[[0-9]{1,8}\]\s*=',
        script,
    )
    if not match or match.end() >= len(script) or script[match.end()] != "{":
        raise UpstreamError("The public page returned no literal profile state")
    start = match.end()
    depth, quote, escaped, end = 0, None, False, None
    for pos in range(start, min(len(script), start + MAX_STATE_BYTES + 1)):
        char = script[pos]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'", "`"):
            quote = char
        elif char in "{[":
            depth += 1
            if depth > 64:
                raise UpstreamError("The public profile state was nested too deeply")
        elif char in "}]":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break
    if end is None:
        raise UpstreamError(
            "The public profile state was incomplete or exceeded the size limit"
        )

    def syntax(value):
        value = _ASSIGN.sub("", value)
        value = _KEY.sub(lambda m: m[1] + json.dumps(m[2]) + ":", value)
        return value.replace("!0", "true").replace("!1", "false")

    out, pos, segment = [], start, start
    decoder = json.JSONDecoder()
    try:
        while pos < end:
            if script[pos] == '"':
                out.append(syntax(script[segment:pos]))
                value, pos = decoder.raw_decode(script, pos)
                out.append(json.dumps(value, ensure_ascii=False))
                segment = pos
            else:
                pos += 1
        out.append(syntax(script[segment:end]))
        value = json.loads("".join(out))
    except (ValueError, RecursionError) as exc:
        raise UpstreamError(
            "The public profile state contained unsupported expressions or references"
        ) from exc
    if not isinstance(value, dict):
        raise UpstreamError("The public page returned no profile object")
    return value
