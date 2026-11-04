"""A visible zero is data; a missing counter must not silently overwrite tracking."""
from socialscrape import quality


def test_known_zero_profile_passes_and_unknown_followers_fail():
    body = {"success": True, "channelId": "UCtest", "name": "test", "subscriberCount": 0,
            "videoCount": 0, "isVerified": False}
    assert quality.issues("/v1/youtube/channel", {"handle": "test"}, body) == []
    assert quality.issues("/v1/youtube/channel", {"handle": "test"}, {**body, "subscriberCount": None}) == ["profile.followers"]


def test_visible_video_count_text_can_supply_its_numeric_alias_without_inventing_hidden_counts():
    body = {"videoCountText": "1,234 videos", "subscriberCount": None}
    assert quality.prepare("/v1/youtube/channel", body) == {**body, "videoCount": 1234}
    assert quality.prepare("/v1/youtube/channel", {"videoCountText": None}) == {"videoCountText": None}


def test_missing_feed_like_counter_and_malformed_nested_data_cannot_pass():
    body = {"success": True, "items": [{"code": "abc", "media_type": 2, "play_count": 1,
        "taken_at": 1760000000, "caption": None, "like_count": None, "comment_count": 0}]}
    assert quality.issues("/v2/instagram/user/posts", {}, body) == ["feed[0].likes"]
    assert quality.issues("/v1/tiktok/profile", {}, {"user": "broken", "stats": {}}) == ["response.invalid_shape"]
    assert quality.issues("/v1/twitter/profile", {}, {"success": True}) == []


def test_media_archiving_and_resolved_short_links_are_not_rejected_as_bad_tracking_input():
    assert quality.issues("/v1/instagram/post", {"download_media": "true"}, {"media_url": "https://example.com/video.mp4"}) == []
    assert quality.identity("instagram", "DLDXI0fylTC") == "DLDXI0fylTC"
    body = {"data": {"xdt_shortcode_media": {"shortcode": "DLDXI0fylTC", "is_video": False,
        "edge_media_preview_like": {"count": 0}, "edge_media_to_parent_comment": {"count": 0},
        "taken_at_timestamp": 1760000000, "display_url": "https://example.com/image.jpg"}}}
    assert quality.issues("/v1/instagram/post", {"url": "https://www.instagram.com/share/reel/short-link"}, body) == []


def test_only_counts_youtube_shows_nobody_are_unfillable():
    """ScrapeCreators reads the same YouTube pages: for these its answer has the same null, so it isn't asked."""
    channel = {"success": True, "channelId": "UCG0U63MW03if8_vvLcpJAhA", "name": "Hana Lee", "subscriberCount": None,
               "subscriberCountText": None, "videoCount": 0, "isVerified": False, "joinedDateText": "Joined Oct 18, 2026"}
    missing = quality.issues("/v1/youtube/channel", {"handle": "Hana.lee.kori2"}, channel)
    assert missing == ["profile.followers"] and quality.unfillable("/v1/youtube/channel", channel, missing)
    # No about panel (a degraded page): the provider may still have the count.
    assert not quality.unfillable("/v1/youtube/channel", {**channel, "joinedDateText": None}, missing)
    assert not quality.unfillable("/v1/youtube/channel", channel, ["profile.followers", "profile.verified"])

    short = {"id": "y5rVhmATj9Q", "title": "t", "publishDate": "2026-10-19T00:10:41-07:00", "viewCountInt": 1283,
             "likeCountInt": None, "commentCountInt": 4}
    shorts = {"success": True, "shorts": [short, {**short, "id": "x", "commentCountInt": None}]}
    missing = quality.issues("/v1/youtube/channel/shorts", {"handle": "learnwbushra"}, shorts)
    assert missing == ["feed[0].likes", "feed[1].likes", "feed[1].comments"]
    assert quality.unfillable("/v1/youtube/channel/shorts", shorts, missing)
    # A short that wasn't read (no date) isn't the creator hiding a count.
    unread = {"success": True, "shorts": [{**short, "publishDate": None}]}
    missing = quality.issues("/v1/youtube/channel/shorts", {}, unread)
    assert missing == ["feed[0].likes", "feed[0].date"] and not quality.unfillable("/v1/youtube/channel/shorts", unread, missing)
    assert not quality.unfillable("/v1/youtube/channel/shorts", shorts, ["feed[7].likes"])
    assert not quality.unfillable("/v1/instagram/user/reels", {"items": []}, ["feed[0].likes"])
    assert not quality.unfillable("/v1/youtube/channel", channel, [])
