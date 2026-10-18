"""Key shapes of ScrapeCreators responses that pass Instagram objects through raw.

Instagram keeps adding fields to its private-API objects; `prune` trims a raw
object to the keys ScrapeCreators' example carries so responses stay comparable.
"""

from __future__ import annotations

from typing import Any

Shape = dict[str, "Shape | None"]


def _leaves(*keys: str) -> dict[str, None]:
    return dict.fromkeys(keys)


def prune(value: Any, shape: Shape | None) -> Any:
    """Keep only the keys in `shape` (recursively; lists apply the shape to each element)."""
    if shape is None:
        return value
    if isinstance(value, list):
        return [prune(v, shape) for v in value]
    if isinstance(value, dict):
        return {k: prune(v, shape[k]) for k, v in value.items() if k in shape}
    return value


# feed/reels_media `reels["highlight:<id>"]` (/v1/instagram/user/highlight/detail)
HIGHLIGHT_REEL: Shape = {
    **_leaves("ad_expiry_timestamp_in_millis", "app_sticker_info", "can_gif_quick_reply", "can_react_with_avatar",
             "can_reply", "can_reshare", "created_at", "disabled_reply_types", "highlight_reel_type", "id",
             "is_added_to_main_grid", "is_archived", "is_converted_to_clips", "is_cta_sticker_available",
             "is_fit_green", "is_nux", "is_pinned_highlight", "latest_reel_media", "media_count", "media_ids",
             "pool_refresh_ttl_in_sec", "prefetch_count", "reel_type", "seen", "should_treat_link_sticker_as_cta",
             "title"),
    "cover_media": {
        **_leaves("crop_rect", "full_image_version", "media_id", "upload_id"),
        "cropped_image_version": {
            **_leaves("height", "scans_profile", "url", "width"),
        },
    },
    "items": {
        **_leaves("archive_story_deletion_ts", "boost_unavailable_identifier", "boost_unavailable_reason",
                 "boost_unavailable_reason_v2", "can_play_spotify_audio", "can_reply", "can_reshare",
                 "can_see_insights_as_brand", "can_send_prompt", "can_viewer_save", "caption", "caption_is_edited",
                 "caption_position", "client_cache_key", "coauthor_producer_can_see_organic_insights",
                 "coauthor_producers", "code", "comment_threading_enabled", "commerciality_status",
                 "cutout_sticker_info", "deleted_reason", "device_timestamp", "fb_aggregated_comment_count",
                 "fb_aggregated_like_count", "fbid", "filter_type", "has_audio", "has_privately_liked",
                 "has_translation", "hide_view_all_comment_entrypoint", "id", "ig_media_sharing_disabled",
                 "imported_taken_at", "integrity_review_decision", "invited_coauthor_producers",
                 "is_comments_gif_composer_enabled", "is_cutout_sticker_allowed", "is_dash_eligible",
                 "is_first_take", "is_from_discovery_surface", "is_in_profile_grid",
                 "is_open_to_public_submission", "is_organic_product_tagging_eligible", "is_paid_partnership",
                 "is_photo_mash_story", "is_post_live_clips_media", "is_quicksnap_recap", "is_reel_media",
                 "is_reshare_of_text_post_app_media_in_ig", "is_rollcall_v2", "is_superlative",
                 "is_tagged_media_shared_to_viewer_profile_grid", "is_terminal_video_segment", "is_unified_video",
                 "is_visual_reply_commenter_notice_enabled", "like_and_view_counts_disabled", "likers",
                 "media_type", "mezql_token", "music_metadata", "number_of_qualities",
                 "open_carousel_show_follow_button", "original_height", "original_media_has_visual_reply_media",
                 "original_width", "photo_of_you", "pk", "product_suggestions", "product_type",
                 "profile_grid_control_enabled", "shop_routing_user_id", "should_request_ads",
                 "should_show_author_pog_for_tagged_media_shared_to_profile_grid", "show_one_tap_fb_share_tooltip",
                 "strong_id__", "subscribe_cta_visible", "supports_reel_reactions", "taken_at",
                 "timeline_pinned_user_ids", "video_dash_manifest", "video_duration", "video_sticker_locales"),
        "image_versions2": {
            "candidates": {
                **_leaves("height", "scans_profile", "url", "width"),
            },
        },
        "owner": {
            **_leaves("account_badges", "account_type", "full_name", "id", "is_private", "is_verified", "pk",
                     "pk_id", "profile_pic_id", "profile_pic_url", "strong_id__", "username"),
            "hd_profile_pic_versions": {
                **_leaves("height", "url", "width"),
            },
        },
        "sharing_friction_info": {
            **_leaves("bloks_app_url", "sharing_friction_payload", "should_have_sharing_friction"),
        },
        "user": {
            **_leaves("fbid_v2", "full_name", "id", "username"),
        },
        "video_versions": {
            **_leaves("bandwidth", "height", "type", "url", "width"),
        },
    },
    "user": {
        **_leaves("full_name", "id", "interop_messaging_user_fbid", "is_private", "is_verified", "pk", "pk_id",
                 "profile_pic_id", "profile_pic_url", "strong_id__", "transparency_product_enabled", "username"),
    },
}
