"""The proxy path must use browser transport for JSON and challenge retries."""
import json

import pytest
from yt_dlp.utils import ExtractorError

from socialscrape.platforms import tiktok


@pytest.mark.parametrize("challenge", [False, True])
def test_json_api_uses_the_browser_transport_on_every_attempt(challenge):
    calls = []

    class Endpoint:
        def _download_json(self, url, display, note, *, query, impersonate=False):
            calls.append(impersonate)
            if not impersonate or (challenge and len(calls) == 1):
                body = "<title>Please wait...</title>" if impersonate else "<title>Site Maintenance</title>"
                raise ExtractorError("Failed to parse JSON", cause=json.JSONDecodeError("Expected JSON", body, 0))
            return {"statusCode": 0, "itemList": [{"id": "123"}]}

        def _solve_challenge_and_set_cookies(self, body):
            if "Please wait..." not in body:
                raise ExtractorError("Not a supported challenge")
            return []

    result = tiktok._api_answer(Endpoint(), "https://www.tiktok.com/api/creator/item_list/", "sample", "Feed",
                                {"secUid": "sample", "cursor": 123})
    assert result["itemList"] == [{"id": "123"}]
    assert calls == ([True, True] if challenge else [True])
