"""Pure mappers from TikTok's web data (camelCase) to ScrapeCreators' app-API shapes.

ScrapeCreators mirrors TikTok's mobile API (`aweme_detail`, snake_case). yt-dlp's
TikTokIE/TikTokUserIE read the web app's rehydration data and item_list API
instead, so posts are re-keyed here. Only fields with a direct web counterpart
are emitted; app-only fields (download_count, has_watermark, ...) are left out.
"""

from __future__ import annotations

import re
from typing import Any

from ..util import compact, iso_utc, short_lang

_IMAGE_URI = re.compile(r"^https?://[^/]+/(tos-[\w-]+/[\w.-]+)~")
_CODEC = re.compile(r"_(h264|bytevc1|bytevc2)_")
_CODEC_FLAG = {"h264": 0, "bytevc1": 1, "bytevc2": 2}
_POST_URL = re.compile(
    r"^https?://(?:www\.|m\.)?tiktokv?\.com/"
    r"(?:@(?P<user>[\w.-]*)/(?:video|photo)|embed(?:/v2)?|share/video|v)/(?P<id>[0-9]+)", re.I)
_SHORT_URL = re.compile(r"^https?://(?:(?:vm|vt)\.tiktok\.com|(?:www\.)?tiktok\.com/t)/\w+", re.I)
_HANDLE = re.compile(r"[\w.-]{1,64}")
_PROFILE_URL = re.compile(
    r"^https?://(?:www\.|m\.)?tiktok\.com/@(?P<handle>[\w.-]+)(?:/live)?/?(?:[?#].*)?$", re.I)
# TikTokCollectionIE._VALID_URL, also accepting m./no-www hosts and a title-less path
_COLLECTION_URL = re.compile(
    r"^https?://(?:www\.|m\.)?tiktok\.com/@[\w.-]+/collection/(?:[^/?#]*-)?(?P<id>[0-9]+)/?(?:[?#].*)?$", re.I)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


def to_int(value: Any) -> int | None:
    """'123' / 123 / True -> int; '' / None / junk -> None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def with_scheme(url: str) -> str:
    """'www.tiktok.com/...' -> 'https://www.tiktok.com/...' (clients often drop the scheme)."""
    url = url.strip()
    return url if _SCHEME.match(url) else "https://" + url.lstrip("/")


def post_url(url: str) -> tuple[str | None, str] | None:
    """Canonical post URL -> (handle or None, aweme_id); None if not a TikTok post URL."""
    m = _POST_URL.match(with_scheme(url))
    return (m.group("user") or None, m.group("id")) if m else None


def is_short_url(url: str) -> bool:
    return bool(_SHORT_URL.match(with_scheme(url)))


def collection_id(url: str) -> str | None:
    m = _COLLECTION_URL.match(with_scheme(url))
    return m.group("id") if m else None


def clean_handle(handle: str) -> str | None:
    """'@name' / 'name' / profile or /live URL -> 'name'; None if it can't be a TikTok handle."""
    handle = handle.strip()
    if "/" in handle and (m := _PROFILE_URL.match(with_scheme(handle))):
        return m.group("handle")
    handle = handle.lstrip("@")
    return handle if _HANDLE.fullmatch(handle) else None


def web_url(handle: str | None, aweme_id: str) -> str:
    return f"https://www.tiktok.com/@{handle or '_'}/video/{aweme_id}"


def image(url: str | None) -> dict[str, Any] | None:
    """Image URL -> app-style {uri, url_list}; `uri` only when the CDN path has one."""
    if not url:
        return None
    m = _IMAGE_URI.match(url)
    return compact({"uri": m.group(1) if m else None, "url_list": [url]})


def codec_flag(url_key: str | None) -> int | None:
    """App `is_bytevc1`: 0 h264, 1 bytevc1 (h265), 2 bytevc2 — read from the url_key."""
    m = _CODEC.search(url_key or "")
    return _CODEC_FLAG[m.group(1)] if m else None


def play_addr(pa: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pa or not pa.get("UrlList"):
        return None
    return compact({
        "data_size": to_int(pa.get("DataSize")),
        "file_cs": pa.get("FileCs") or None,
        "file_hash": pa.get("FileHash") or None,
        "height": pa.get("Height"),
        "width": pa.get("Width"),
        "uri": pa.get("Uri") or None,
        "url_key": pa.get("UrlKey") or None,
        "url_list": pa["UrlList"],
    })


def bit_rate(b: dict[str, Any]) -> dict[str, Any]:
    return compact({
        "bit_rate": to_int(b.get("Bitrate")),
        "fps": to_int(b.get("BitrateFPS")),
        "gear_name": b.get("GearName"),
        "is_bytevc1": codec_flag((b.get("PlayAddr") or {}).get("UrlKey")),
        "play_addr": play_addr(b.get("PlayAddr")),
        "quality_type": to_int(b.get("QualityType")),
        "video_extra": b.get("VideoExtra"),
    })


def cla_info(cla: dict[str, Any] | None) -> dict[str, Any] | None:
    if not cla:
        return None
    captions = [compact({
        "caption_format": c.get("captionFormat"),
        "cla_subtitle_id": to_int(c.get("claSubtitleID")),
        "expire": to_int(c.get("expire")),
        "is_auto_generated": c.get("isAutoGen"),
        "is_original_caption": c.get("isOriginalCaption"),
        "lang": c.get("language"),
        "language_code": c.get("languageCode"),
        "language_id": to_int(c.get("languageID")),
        "sub_id": to_int(c.get("subID")),
        "subtitle_type": to_int(c.get("subtitleType")),
        "translation_type": to_int(c.get("translationType")),
        "url": c.get("url"),
        "url_list": c.get("urlList"),
        "variant": c.get("variant"),
    }) for c in cla.get("captionInfos") or []]
    orig = cla.get("originalLanguageInfo") or {}
    return compact({
        "caption_infos": captions,
        "captions_type": to_int(cla.get("captionsType")),
        "enable_auto_caption": to_int(cla.get("enableAutoCaption")),
        "has_original_audio": to_int(cla.get("hasOriginalAudio")),
        "no_caption_reason": to_int(cla.get("noCaptionReason")),
        "original_language_info": compact({
            "lang": orig.get("language"),
            "language_code": orig.get("languageCode"),
            "language_id": to_int(orig.get("languageID")),
        }) or None,
    })


def duration_ms(item: dict[str, Any]) -> int | None:
    """The post's own length in ms; None for photo posts (web video.duration is 0).

    Web `video.duration` is whole seconds. The poster's own original sound's preciseVideoDuration
    is the same clip measured precisely, so it refines the value when the two agree. For a reused
    sound it is the *source* video's length and must not be used - and music.original marks the
    sound, not this post: reusing another creator's original sound keeps it True.
    """
    seconds = to_int((item.get("video") or {}).get("duration"))
    if not seconds or seconds <= 0:
        return None
    m, au = item.get("music") or {}, item.get("author") or {}
    own = m.get("original") is True and bool(m.get("authorName")) and m["authorName"] in (
        au.get("nickname"), au.get("uniqueId"))
    precise = (m.get("preciseDuration") or {}).get("preciseVideoDuration")
    if own and isinstance(precise, (int, float)) and abs(precise - seconds) < 2:
        return round(precise * 1000)
    return seconds * 1000


def video(item: dict[str, Any], *, with_captions: bool) -> dict[str, Any]:
    v = item.get("video") or {}
    pas = v.get("PlayAddrStruct")
    return compact({
        "bit_rate": [bit_rate(b) for b in v.get("bitrateInfo") or []] or None,
        "cla_info": cla_info(v.get("claInfo")) if with_captions else None,
        "cover": image(v.get("cover")),
        "download_addr": {"url_list": [v["downloadAddr"]]} if v.get("downloadAddr") else None,
        "duration": duration_ms(item),
        "dynamic_cover": image(v.get("dynamicCover")),
        "height": v.get("height") or None,  # photo posts report 0
        "is_bytevc1": codec_flag((pas or {}).get("UrlKey")),
        "origin_cover": image(v.get("originCover")),
        "play_addr": play_addr(pas),
        "ratio": v.get("ratio") or v.get("definition"),
        "width": v.get("width") or None,
    })


def statistics(item: dict[str, Any]) -> dict[str, Any]:
    s, s2 = item.get("stats") or {}, item.get("statsV2") or {}
    return compact({
        "aweme_id": str(item["id"]),
        "collect_count": exact_count(s2, s, "collectCount"),
        "comment_count": exact_count(s2, s, "commentCount"),
        "digg_count": exact_count(s2, s, "diggCount"),
        "play_count": exact_count(s2, s, "playCount"),
        "repost_count": exact_count(s2, s, "repostCount"),
        "share_count": exact_count(s2, s, "shareCount"),
    })


def exact_count(v2: dict[str, Any] | None, v1: dict[str, Any] | None, *keys: str) -> int | None:
    """First non-negative count for `keys`, preferring the string-valued *V2 block.

    TikTok's int fields overflow int32 (heartCount = -1894967296 for 2.4B likes); the
    V2 strings don't, and they are exact where the int fields are rounded.
    """
    for src in (v2 or {}, v1 or {}):
        for key in keys:
            n = to_int(src.get(key))
            if n is not None and n >= 0:
                return n
    return None


_COUNT_ALIASES = {"heartCount": ("heartCount", "heart"), "heart": ("heart", "heartCount")}


def exact_stats(stats: dict[str, Any] | None, stats_v2: dict[str, Any] | None) -> dict[str, Any] | None:
    """User-page `stats` with each count replaced by its exact, non-overflowed statsV2 value."""
    if not isinstance(stats, dict):
        return stats
    return {k: exact_count(stats_v2, stats, *_COUNT_ALIASES.get(k, (k,)))
            if isinstance(v, int) and not isinstance(v, bool) else v for k, v in stats.items()}


def author(item: dict[str, Any]) -> dict[str, Any]:
    a = item.get("author") or {}
    st, st2 = item.get("authorStats") or {}, item.get("authorStatsV2") or {}
    secret = a.get("secret")
    return compact({
        "uid": a.get("id") or None,
        "unique_id": a.get("uniqueId") or None,
        "nickname": a.get("nickname"),
        "signature": a.get("signature"),
        "sec_uid": a.get("secUid") or None,
        "avatar_larger": image(a.get("avatarLarger")),
        "avatar_medium": image(a.get("avatarMedium")),
        "avatar_thumb": image(a.get("avatarThumb")),
        "create_time": to_int(a.get("createTime")),
        "secret": int(secret) if isinstance(secret, bool) else None,
        "comment_setting": to_int(a.get("commentSetting")),
        "duet_setting": to_int(a.get("duetSetting")),
        "stitch_setting": to_int(a.get("stitchSetting")),
        "download_setting": to_int(a.get("downloadSetting")),
        "unique_id_modify_time": to_int(a.get("uniqueIdModifyTime")),
        "room_id": to_int(a.get("roomId")),
        "follower_count": exact_count(st2, st, "followerCount"),
        "following_count": exact_count(st2, st, "followingCount"),
        "total_favorited": exact_count(st2, st, "heartCount", "heart"),
        "aweme_count": exact_count(st2, st, "videoCount"),
        "favoriting_count": exact_count(st2, st, "diggCount"),
    })


def music(item: dict[str, Any]) -> dict[str, Any] | None:
    m = item.get("music") or {}
    if not m.get("id"):
        return None
    return compact({
        "id_str": str(m["id"]),
        "title": m.get("title"),
        "author": m.get("authorName"),
        "album": m.get("album") or None,
        "duration": to_int(m.get("duration")),
        "is_original": m.get("original"),
        "play_url": {"url_list": [m["playUrl"]]} if m.get("playUrl") else None,
        "cover_large": image(m.get("coverLarge")),
        "cover_medium": image(m.get("coverMedium")),
        "cover_thumb": image(m.get("coverThumb")),
    })


def image_post_info(item: dict[str, Any]) -> dict[str, Any] | None:
    """Photo/slideshow posts: web `imagePost.images[].imageURL` -> app `display_image`."""
    images = []
    for img in (item.get("imagePost") or {}).get("images") or []:
        urls = (img.get("imageURL") or {}).get("urlList") or []
        if urls:
            images.append({"display_image": compact({
                "url_list": urls, "width": to_int(img.get("imageWidth")), "height": to_int(img.get("imageHeight"))})})
    return {"images": images} if images else None


def aweme(item: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    """Web itemStruct / item_list entry -> SC aweme object.

    detail=True adds what /v2/tiktok/video promises (music, image_post_info);
    otherwise video.cla_info is included, as in the /v3 profile feed.
    """
    aweme_id = str(item["id"])
    created = to_int(item.get("createTime"))
    handle = (item.get("author") or {}).get("uniqueId")
    return compact({
        "aweme_id": aweme_id,
        "desc": item.get("desc"),
        "desc_language": item.get("textLanguage") or None,
        "region": item.get("locationCreated") or None,
        "create_time": created,
        "create_time_utc": iso_utc(created) if created else None,
        "is_ad": item.get("isAd") if isinstance(item.get("isAd"), bool) else None,
        "url": web_url(handle, aweme_id),
        "statistics": statistics(item),
        "video": video(item, with_captions=not detail),
        "author": author(item),
        "music": music(item) if detail else None,
        "image_post_info": image_post_info(item) if detail else None,
    })


_AUTHOR_COUNTS = ("follower_count", "following_count", "total_favorited", "aweme_count", "favoriting_count")
# The app pins counts past int32 just below 2**31 - 1 (2.4B plays -> play_count 2147483597).
_PINNED = 2**31 - 1000


def uncapped(app: dict[str, Any], web: dict[str, Any]) -> dict[str, int]:
    """The web counts that replace app counts pinned at the int32 ceiling: only where the web's is larger."""
    return {k: n for k, n in web.items()
            if isinstance(app.get(k), int) and app[k] >= _PINNED and isinstance(n, int) and n > app[k]}


def app_detail(a: dict[str, Any], web: dict[str, Any] | None = None) -> dict[str, Any]:
    """App aweme (music/aweme) -> SC aweme_detail: TikTok's object as-is plus the keys SC adds.

    music/aweme zeroes the author's counts; they are filled from the web item when given. Counts
    the app pins at the int32 ceiling take the web item's larger (rounded) count.
    """
    au = a.get("author") if isinstance(a.get("author"), dict) else {}
    created = to_int(a.get("create_time"))
    extra = compact({
        "url": web_url(au.get("unique_id"), str(a["aweme_id"])),
        "create_time_utc": iso_utc(created) if created else None,
        "is_ad": a["is_ads"] if isinstance(a.get("is_ads"), bool) else None,
    })
    out = {**a, **{k: v for k, v in extra.items() if k not in a}}
    if web and isinstance(a.get("statistics"), dict):
        out["statistics"] = {**a["statistics"], **uncapped(a["statistics"], statistics(web))}
    if web and au:
        web_author = {k: v for k, v in author(web).items() if k in _AUTHOR_COUNTS}
        counts = {k: v for k, v in web_author.items() if not au.get(k)}
        out["author"] = {**au, **counts, **uncapped(au, web_author)}
    return out


def caption_track(item: dict[str, Any], language: str | None = None) -> dict[str, Any] | None:
    """Pick the caption track yt-dlp would expose (video.subtitleInfos), matched by language code.

    TikTok names tracks like 'eng-US' or 'cmn-Hans-CN'; claInfo.captionInfos maps those names to
    ISO 639-1 codes ('en', 'zh-Hans'). Both are compared by their language part, so 'zh', 'zho'
    and 'cmn' all pick 'cmn-Hans-CN' (3-letter codes are shortened first). Without a language
    the original-audio track wins.
    """
    v = item.get("video") or {}
    cla = v.get("claInfo") or {}
    codes = {c.get("language"): c.get("languageCode") for c in cla.get("captionInfos") or [] if c.get("language")}
    original = (cla.get("originalLanguageInfo") or {}).get("language")
    tracks = [{"name": t.get("LanguageCodeName"), "url": t["Url"], "format": t.get("Format")}
              for t in v.get("subtitleInfos") or [] if t.get("Url")]
    if not tracks:  # fall back to the caption list itself
        tracks = [{"name": c.get("language"), "url": c["url"], "format": c.get("captionFormat")}
                  for c in cla.get("captionInfos") or [] if c.get("url")]
    tracks = [t for t in tracks if (t["format"] or "webvtt").lower() == "webvtt"]
    if language:
        lang = short_lang(language)  # 'eng' -> 'en', 'cmn' -> 'zh'
        return next((t for t in tracks if lang in {short_lang(codes.get(t["name"])), short_lang(t["name"])}), None)
    return next((t for t in tracks if t["name"] == original), tracks[0] if tracks else None)


def live_user(user: dict[str, Any], stats: dict[str, Any] | None) -> dict[str, Any]:
    """Live page / profile page user -> SC liveRoomUserInfo (user fields + follower stats)."""
    keys = ("avatarLarger", "avatarMedium", "avatarThumb", "id", "nickname", "secUid", "secret", "uniqueId",
            "verified", "roomId", "signature", "status", "followStatus")
    out = {k: user[k] for k in keys if k in user}
    for k in ("followingCount", "followerCount"):
        if (stats or {}).get(k) is not None:
            out[k] = stats[k]
    return out


_ROOM_STATUS = {2: "live", 4: "ended"}


def live_room(data: dict[str, Any]) -> dict[str, Any]:
    """webcast/room/info `data` -> SC /v1/tiktok/live body."""
    owner = data.get("owner") or {}
    like_count = to_int(data.get("like_count"))
    return compact({
        "room_id": data.get("id_str") or (str(data["id"]) if data.get("id") else None),
        "like_count": like_count or None,  # 0 here means "not reported", not "no likes"
        "viewer_count": to_int(data.get("user_count")),
        "status": _ROOM_STATUS.get(to_int(data.get("status")) or -1),
        "title": data.get("title"),
        "cover_url": next(iter((data.get("cover") or {}).get("url_list") or []), None),
        "owner": compact({
            "id": owner.get("id_str") or (str(owner["id"]) if owner.get("id") else None),
            "username": owner.get("display_id"),
            "display_name": owner.get("nickname"),
        }) or None,
    })
