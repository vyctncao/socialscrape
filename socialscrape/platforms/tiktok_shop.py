"""TikTok Shop routes. TikTok Shop is a separately authorized, signed API product."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..config import get_settings
from ..errors import NotSupported
from ..registry import sc_get

router = APIRouter()


def _needs_shop() -> None:
    from ..portal import active_platform_credential
    saved = active_platform_credential("tiktok.shop_credentials")
    if not (saved or get_settings().tiktok_shop_token):
        raise NotSupported("Configure TikTok Shop app credentials and access token to use this endpoint",
                           code="platform_shop_auth_required", platform="tiktok",
                           required=["app_key", "app_secret", "access_token", "shop_cipher"])
    raise NotSupported("TikTok Shop request signing needs app_key, app_secret and shop_cipher in addition to the "
                       "access token", code="platform_shop_credentials_incomplete", platform="tiktok")


@sc_get(router, "/v1/tiktok/product", source="TikTok Shop Partner API", status="needs_setup", notes="Requires TikTok Shop app credentials.")
async def product(url: str = Query(...), region: str | None = None):
    _needs_shop()


@sc_get(router, "/v1/tiktok/shop/product/reviews", source="TikTok Shop Partner API",
        status="needs_setup", notes="Requires TikTok Shop app credentials.")
async def reviews(url: str | None = None, product_id: str | None = None,
                  region: str | None = None, page: str | None = None):
    _needs_shop()


@sc_get(router, "/v1/tiktok/shop/products", source="TikTok Shop Partner API",
        status="needs_setup", notes="Requires TikTok Shop app credentials.")
async def products(url: str = Query(...), cursor: str | None = None, sort_by: str | None = None,
                   region: str | None = None):
    _needs_shop()


@sc_get(router, "/v1/tiktok/shop/search", source="TikTok Shop Partner API",
        status="needs_setup", notes="Requires TikTok Shop app credentials.")
async def search(query: str = Query(...), page: str | None = None, region: str | None = None):
    _needs_shop()


@sc_get(router, "/v1/tiktok/user/showcase", source="TikTok Shop authorized showcase API",
        status="needs_setup", notes="Requires TikTok Shop app credentials and creator authorization.")
async def showcase(handle: str = Query(...), region: str | None = None, cursor: str | None = None):
    _needs_shop()
