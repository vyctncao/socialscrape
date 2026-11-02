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
