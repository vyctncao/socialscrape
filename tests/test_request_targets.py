"""Request history shows what each request was about: the link it sent, or the profile, channel, hashtag or search
its parameters name, with the address to open it at when one can be built for certain."""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from socialscrape.targets import describe


@pytest.mark.parametrize("path,query,expected", [
    # The link a request sent, decoded, and opened as sent.
    ("/v1/instagram/post", "url=https%3A%2F%2Fwww.instagram.com%2Fp%2FDbci-00NVcC%2F",
     ("Link", "https://www.instagram.com/p/Dbci-00NVcC/", "https://www.instagram.com/p/Dbci-00NVcC/")),
    ("/v2/tiktok/video", "url=https%3A%2F%2Fvm.tiktok.com%2FZMabc123%2F&trim=true",
     ("Link", "https://vm.tiktok.com/ZMabc123/", "https://vm.tiktok.com/ZMabc123/")),
    # A value that isn't an address is shown as sent, never opened.
    ("/v1/instagram/post", "url=Dbci-00NVcC", ("Link", "Dbci-00NVcC", None)),
    ("/v1/instagram/post", "url=javascript%3Aalert(1)", ("Link", "javascript:alert(1)", None)),
    ("/v1/instagram/post", "url=https%3A%2F%2Fa.com%2F%22onmouseover%3D", ("Link", 'https://a.com/"onmouseover=', None)),
    # Profiles, on the platform the route is for.
    ("/v1/tiktok/profile", "handle=stoolpresidente", ("Profile", "@stoolpresidente", "https://www.tiktok.com/@stoolpresidente")),
    ("/v1/tiktok/user/followers", "handle=%40rachel.johnson1182&count=24&limit=24",
     ("Profile", "@rachel.johnson1182", "https://www.tiktok.com/@rachel.johnson1182")),
    ("/v1/instagram/user/reels", "handle=chloeoylee", ("Profile", "@chloeoylee", "https://www.instagram.com/chloeoylee/")),
    ("/v1/twitter/profile", "handle=elonmusk", ("Profile", "@elonmusk", "https://x.com/elonmusk")),
    ("/v1/bluesky/profile", "handle=bsky.app", ("Profile", "@bsky.app", "https://bsky.app/profile/bsky.app")),
    ("/v1/rumble/channel/videos", "handle=CuteCats223", ("Profile", "@CuteCats223", "https://rumble.com/c/CuteCats223")),
    ("/v1/find-social-profiles", "platform=instagram&handle=creator",
     ("Profile", "@creator", "https://www.instagram.com/creator/")),
    ("/v1/instagram/profile", "handle=https%3A%2F%2Fwww.instagram.com%2Fnatgeo%2F",
     ("Profile", "https://www.instagram.com/natgeo/", "https://www.instagram.com/natgeo/")),
    ("/v1/cosmos/profile", "handle=design", ("Profile", "@design", None)),  # no address known for certain
    ("/v1/tiktok/profile", "handle=a%2F..%2Fb", ("Profile", "@a/../b", None)),
    # YouTube channels, read as the channel routes read channelId and handle.
    ("/v1/youtube/channel", "handle=MrBeast", ("Channel", "@MrBeast", "https://www.youtube.com/@MrBeast")),
    ("/v1/youtube/channel/shorts", "handle=%40learnwbushra&sort=newest",
     ("Channel", "@learnwbushra", "https://www.youtube.com/@learnwbushra")),
    ("/v1/youtube/channel", "channelId=UCX6OQ3DkcsbYNE6H8uQQuVA",
     ("Channel", "UCX6OQ3DkcsbYNE6H8uQQuVA", "https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA")),
    ("/v1/youtube/channel", "handle=not+a+handle%21", ("Channel", "@not a handle!", None)),
    # Hashtags, subreddits, searches and ids.
    ("/v1/tiktok/search/hashtag", "hashtag=%23skincare", ("Hashtag", "#skincare", "https://www.tiktok.com/tag/skincare")),
    ("/v1/instagram/search/hashtag", "hashtag=food", ("Hashtag", "#food", "https://www.instagram.com/explore/tags/food/")),
    ("/v1/reddit/subreddit", "subreddit=r%2Fpython", ("Subreddit", "r/python", "https://www.reddit.com/r/python/")),
    ("/v1/tiktok/search/keyword", "query=skincare+routine&region=US", ("Search", "“skincare routine”", None)),
    ("/v1/instagram/user/highlight/detail", "id=highlight%3A18067016518767507",
     ("Highlight", "highlight:18067016518767507", "https://www.instagram.com/stories/highlights/18067016518767507/")),
    ("/v1/spotify/track", "id=1ITJflybJsfarsUtiBvkfK",
     ("Track", "1ITJflybJsfarsUtiBvkfK", "https://open.spotify.com/track/1ITJflybJsfarsUtiBvkfK")),
    ("/v1/apple-music/album", "id=1468058165", ("Album", "1468058165", None)),
    ("/v1/tiktok/ad-library/ad", "ad_id=1869335220395266",
     ("Ad", "1869335220395266", "https://library.tiktok.com/ads/detail/?ad_id=1869335220395266")),
    ("/v1/youtube/playlist", "playlist_id=PLP32wGpgzmIlInfgKVFfCwVsxgGqZNIiS",
     ("Playlist", "PLP32wGpgzmIlInfgKVFfCwVsxgGqZNIiS", "https://www.youtube.com/playlist?list=PLP32wGpgzmIlInfgKVFfCwVsxgGqZNIiS")),
    ("/v1/facebook/profile/posts", "pageId=100063669491743",
     ("Page", "100063669491743", "https://www.facebook.com/100063669491743")),
    ("/v1/tiktok/live", "room_id=7123&user_id=6659752019493208069", ("Live room", "7123", None)),
    ("/v1/tiktok/song", "clipId=7439295283975702544", ("Sound", "7439295283975702544", None)),
])
def test_a_request_is_described_by_what_it_names(path, query, expected):
    target = describe(path, query)
    assert (target["label"], target["value"], target["url"]) == expected


def test_requests_without_a_subject_have_none():
    assert describe("/v1/account/credit-balance", "") is None
    assert describe("/v1/account/get-api-usage", "page=2") is None
    assert describe("/v1/tiktok/profile", "handle=+") is None  # blank values name nothing


@pytest.mark.parametrize("path,query", [
    ("/v1/instagram/post", "url=https%3A%2F%2F%5B%3A%3A1"),  # urllib raises on a broken IPv6 host
    ("/v1/tiktok/profile", "handle=https%3A%2F%2F%5Bx"),
    ("/v1/youtube/channel", "handle=https%3A%2F%2F%5Bx"),
    ("/v1/youtube/channel", "channelId=https%3A%2F%2Fwww.youtube.com%2F%5Bx"),
    ("/v1/instagram/user/highlight/detail", "id=https%3A%2F%2F%5Bx"),
    ("/v1/spotify/track", "id=https%3A%2F%2F%5Bx"),
    ("/v1/youtube/channel", "handle=%00%01"),
    ("", ""), ("/", "%"), ("/v1/tiktok/profile", "handle"),
])
def test_whatever_a_client_sent_is_described_without_raising(path, query):
    target = describe(path, query)
    assert target is None or (target["url"] is None and isinstance(target["value"], str))


def test_history_api_carries_each_requests_target(tmp_path, monkeypatch):
    from socialscrape.config import get_settings
    from socialscrape.main import create_app

    monkeypatch.setenv("SOCIALSCRAPE_SETUP_TOKEN", "setup")
    monkeypatch.setenv("SOCIALSCRAPE_PORTAL_PATH", str(tmp_path / "portal.sqlite3"))
    monkeypatch.setenv("SOCIALSCRAPE_API_KEYS", "")
    get_settings.cache_clear()
    try:
        app = create_app()
        client = TestClient(app, base_url="https://testserver")
        client.post("/setup", data={"email": "owner@example.com", "password": "correct horse battery",
                                    "setup_token": "setup"})
        store = app.state.portal
        store._conn.executemany(
            "INSERT INTO requests(member_id,created_at,method,path,query,status,duration_ms,fallback) VALUES(1,?,?,?,?,?,?,?)",
            [(time.time() - 5, "GET", "/v1/instagram/post", "url=https%3A%2F%2Fwww.instagram.com%2Fp%2Fabc%2F", 404, 10, 1),
             (time.time() - 4, "GET", "/v1/tiktok/profile", "handle=stoolpresidente", 200, 10, 0),
             (time.time() - 3, "GET", "/v1/account/credit-balance", "", 200, 1, 0)])
        store._conn.commit()
        rows = client.get("/portal/api/history").json()["requests"]
        assert [row["target"] for row in rows] == [
            None,
            {"label": "Profile", "value": "@stoolpresidente", "url": "https://www.tiktok.com/@stoolpresidente"},
            {"label": "Link", "value": "https://www.instagram.com/p/abc/", "url": "https://www.instagram.com/p/abc/"}]
        detail = client.get(f"/portal/api/history/{rows[2]['id']}").json()
        assert detail["target"]["url"] == "https://www.instagram.com/p/abc/" and detail["path"] == "/v1/instagram/post"
    finally:
        get_settings.cache_clear()


def test_history_shows_the_target_as_a_link_that_opens_elsewhere():
    script = (Path(__file__).resolve().parents[1] / "socialscrape" / "static" / "portal.js").read_text()
    assert 'class="text-link history-link"' in script and 'target="_blank" rel="noopener noreferrer"' in script
    # Only an http(s) address with nothing an attribute could hide in it becomes a link.
    assert r"""/^https?:\/\/[^\s"'<>\\]+$/i.test(target.url)""" in script
    assert '<div class="history-fact-wide"><dt>Request</dt>' in script
    assert "${historySubject(request)}" in script and "historyTargetValue(detail.target, false)" in script
