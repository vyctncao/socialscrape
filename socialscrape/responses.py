"""The JSON response routes and errors answer with."""

from __future__ import annotations

import math
from typing import Any

from fastapi.responses import JSONResponse


def jsonable(value: Any) -> Any:
    """value with what JSON can't write replaced: NaN and ±Infinity by None, and a lone UTF-16 surrogate (half an
    emoji, where text was cut to a UTF-16 length) by U+FFFD. Upstream JSON as Python reads it can carry both (NaN,
    1e999, "\\ud83d"), and raw upstream objects and yt-dlp's messages pass through."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    if isinstance(value, dict):
        return {jsonable(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


class LenientJSONResponse(JSONResponse):
    """A JSONResponse that writes what JSON can't (see jsonable) instead of failing on it."""

    def render(self, content: Any) -> bytes:
        try:
            return super().render(content)
        except ValueError:  # a NaN or ±Infinity, or a lone surrogate (UnicodeEncodeError)
            return super().render(jsonable(content))
