"""TikTok can hide a real user on one egress; confirmation stays bounded and opt-in."""
import asyncio

import pytest

from socialscrape import portal, ytdlp
from socialscrape.errors import NotFound


@pytest.mark.parametrize("confirm", [False, True])
def test_profile_not_found_confirmation_can_reach_the_third_connection(monkeypatch, confirm):
    attempts = []
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: (["one", "two", "three"], []))

    def run(platform, fn, opts):
        assert "confirm_not_found" not in opts  # a retry policy, never a yt-dlp option
        connection = opts["_dashboard_proxy"]
        attempts.append(connection)
        if connection != "three":
            raise NotFound("TikTok user not found (status 10221)")
        return {"id": "123", "uniqueId": "sample"}

    monkeypatch.setattr(ytdlp, "_run", run)
    call = ytdlp.with_ydl("tiktok", lambda ydl: None, confirm_not_found=confirm)
    if confirm:
        assert asyncio.run(call)["id"] == "123" and attempts == ["one", "two", "three"]
    else:
        with pytest.raises(NotFound):
            asyncio.run(call)
        assert attempts == ["one"]


def test_all_connections_missing_is_still_a_404(monkeypatch):
    attempts = []
    monkeypatch.setattr(portal, "active_platform_pool", lambda platform: (["one", "two", "three"], []))

    def run(platform, fn, opts):
        attempts.append(opts["_dashboard_proxy"])
        raise NotFound("TikTok user not found (status 10221)")

    monkeypatch.setattr(ytdlp, "_run", run)
    with pytest.raises(NotFound) as exc:
        asyncio.run(ytdlp.with_ydl("tiktok", lambda ydl: None, confirm_not_found=True))
    assert attempts == ["one", "two", "three"] and exc.value.status == 404
