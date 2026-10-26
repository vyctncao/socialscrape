"""TikTok Shop has no native request-signing or product/search integration yet.

Saving an access token does not make these routes operational. Keep the native
coverage honest until the separately authorized Shop APIs are implemented and
validated; the configured ScrapeCreators fallback may still serve these paths.
"""

from fastapi import APIRouter

router = APIRouter()
_REASON = (
    "TikTok Shop has no native implementation yet. App credential validation, request signing, "
    "product/search APIs and creator showcase authorization still need implementation and live qualification. "
    "Saving an access token alone does not enable this endpoint."
)
UNSUPPORTED = {
    path: _REASON
    for path in (
        "/v1/tiktok/product",
        "/v1/tiktok/shop/product/reviews",
        "/v1/tiktok/shop/products",
        "/v1/tiktok/shop/search",
        "/v1/tiktok/user/showcase",
    )
}
