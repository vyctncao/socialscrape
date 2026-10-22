"""Pure mappers from TikTok's web data (camelCase) to ScrapeCreators' app-API shapes.

ScrapeCreators mirrors TikTok's mobile API (`aweme_detail`, snake_case). yt-dlp's
TikTokIE/TikTokUserIE read the web app's rehydration data and item_list API
instead, so posts are re-keyed here. Only fields with a direct web counterpart
are emitted; app-only fields (download_count, has_watermark, ...) are left out.
"""

from __future__ import annotations

import re
from typing import Any

from ..util import compact, iso_utc, requestable_url, short_lang, sibling_lang

_IMAGE_URI = re.compile(r"^https?://[^/]+/(tos-[\w-]+/[\w.-]+)~")
_CODEC = re.compile(r"_(h264|bytevc1|bytevc2)_")
_CODEC_FLAG = {"h264": 0, "bytevc1": 1, "bytevc2": 2}
# Post pages, embeds (TikTok's embed player is /player/v1/<id>), share links and the old /v/<id>.html; the id
# ends the path, so '/video/123abc' isn't post 123
_POST_URL = re.compile(
    r"^https?://(?:www\.|m\.)?tiktokv?\.com/"
    r"(?:@(?P<user>[\w.-]*)/(?:video|photo)|embed(?:/v2)?|player/v1|share/video|v)/(?P<id>[0-9]+)"
    r"(?:\.html)?/?(?:[?#].*)?$", re.I)
_SHORT_URL = re.compile(r"^https?://(?:(?:vm|vt)\.tiktok\.com|(?:www\.)?tiktok\.com/t)/\w+", re.I)
_HANDLE = re.compile(r"[\w.-]{1,64}")
_PROFILE_URL = re.compile(
    r"^https?://(?:www\.|m\.)?tiktok\.com/@(?P<handle>[\w.-]+)(?:/live)?/?(?:[?#].*)?$", re.I)
# TikTokCollectionIE._VALID_URL, also accepting m./no-www hosts and a title-less path
_COLLECTION_URL = re.compile(
    r"^https?://(?:www\.|m\.)?tiktok\.com/@[\w.-]+/collection/(?:[^/?#]*-)?(?P<id>[0-9]+)/?(?:[?#].*)?$", re.I)
# A sound's page, as TikTokSoundIE._VALID_URL takes it (/music/<title>-<id>), likewise with m./no-www and no title
_MUSIC_URL = re.compile(
    r"^https?://(?:www\.|m\.)?tiktok\.com/music/(?:[^/?#]*-)?(?P<id>[0-9]+)/?(?:[?#].*)?$", re.I)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


def to_int(value: Any) -> int | None:
    """'123' / 123 / True -> int; '' / None / junk -> None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):  # OverflowError: an infinite float
        return None


def obj(value: Any) -> dict[str, Any]:
    """`value` if it is a JSON object, else {}: a field TikTok sends as another type reads as missing."""
    return value if isinstance(value, dict) else {}


def objs(value: Any) -> list[dict[str, Any]]:
    """The JSON objects of a list (its null or other entries skipped); [] for anything but a list."""
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def text(value: Any) -> str | None:
    """`value` if it is a string, else None."""
    return value if isinstance(value, str) else None


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


def music_id(url: str) -> str | None:
    m = _MUSIC_URL.match(with_scheme(url))
    return m.group("id") if m else None


def clean_handle(handle: str) -> str | None:
    """'@name' / 'name' / profile or /live URL -> 'name'; None if it can't be a TikTok handle. The '@' may be the
    full-width '＠' of Japanese and Chinese keyboards."""
    handle = handle.strip()
    if "/" in handle and (m := _PROFILE_URL.match(with_scheme(handle))):
        return m.group("handle")
    handle = handle.lstrip("@＠﹫")
    return handle if _HANDLE.fullmatch(handle) else None


def is_handle(value: Any) -> bool:
    """A bare handle, as a uniqueId is. One that isn't (a '/', a '?', half an emoji) can't go in a page's address."""
    return isinstance(value, str) and bool(_HANDLE.fullmatch(value))


def web_url(handle: str | None, aweme_id: str) -> str:
    return f"https://www.tiktok.com/@{text(handle) or '_'}/video/{aweme_id}"


def image(url: str | None) -> dict[str, Any] | None:
    """Image URL -> app-style {uri, url_list}; `uri` only when the CDN path has one."""
    if not text(url):
        return None
    m = _IMAGE_URI.match(url)
    return compact({"uri": m.group(1) if m else None, "url_list": [url]})


def codec_flag(url_key: str | None) -> int | None:
    """App `is_bytevc1`: 0 h264, 1 bytevc1 (h265), 2 bytevc2 — read from the url_key."""
    m = _CODEC.search(text(url_key) or "")
    return _CODEC_FLAG[m.group(1)] if m else None


def play_addr(pa: dict[str, Any] | None) -> dict[str, Any] | None:
    pa = obj(pa)
    if not pa.get("UrlList"):
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
        "is_bytevc1": codec_flag(obj(b.get("PlayAddr")).get("UrlKey")),
        "play_addr": play_addr(b.get("PlayAddr")),
        "quality_type": to_int(b.get("QualityType")),
        "video_extra": b.get("VideoExtra"),
    })


def cla_info(cla: dict[str, Any] | None) -> dict[str, Any] | None:
    cla = obj(cla)
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
    }) for c in objs(cla.get("captionInfos"))]
    orig = obj(cla.get("originalLanguageInfo"))
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
    seconds = to_int(obj(item.get("video")).get("duration"))
    if not seconds or seconds <= 0:
        return None
    m, au = obj(item.get("music")), obj(item.get("author"))
    own = m.get("original") is True and bool(m.get("authorName")) and m["authorName"] in (
        au.get("nickname"), au.get("uniqueId"))
    precise = obj(m.get("preciseDuration")).get("preciseVideoDuration")
    try:
        if own and isinstance(precise, (int, float)) and abs(precise - seconds) < 2:
            return round(precise * 1000)
    except OverflowError:  # a length past a float's range
        pass
    return seconds * 1000


def video(item: dict[str, Any], *, with_captions: bool) -> dict[str, Any]:
    v = obj(item.get("video"))
    pas = obj(v.get("PlayAddrStruct"))
    return compact({
        "bit_rate": [bit_rate(b) for b in objs(v.get("bitrateInfo"))] or None,
        "cla_info": cla_info(v.get("claInfo")) if with_captions else None,
        "cover": image(v.get("cover")),
        "download_addr": {"url_list": [v["downloadAddr"]]} if v.get("downloadAddr") else None,
        "duration": duration_ms(item),
        "dynamic_cover": image(v.get("dynamicCover")),
        "height": v.get("height") or None,  # photo posts report 0
        "is_bytevc1": codec_flag(pas.get("UrlKey")),
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
    for src in (obj(v2), obj(v1)):
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
    a = obj(item.get("author"))
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
    m = obj(item.get("music"))
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
    for img in objs(obj(item.get("imagePost")).get("images")):
        urls = obj(img.get("imageURL")).get("urlList") or []
        if urls:
            images.append({"display_image": compact({
                "url_list": urls, "width": to_int(img.get("imageWidth")), "height": to_int(img.get("imageHeight"))})})
    return {"images": images} if images else None


def aweme(item: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    """Web itemStruct / item_list entry -> SC aweme object.

    detail=True adds what /v2/tiktok/video promises (music);
    otherwise video.cla_info is included, as in the /v3 profile feed.
    A photo post carries its images (image_post_info) either way.
    """
    aweme_id = str(item["id"])
    created = to_int(item.get("createTime"))
    handle = obj(item.get("author")).get("uniqueId")
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
        "image_post_info": image_post_info(item),
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
    and 'cmn' all pick 'cmn-Hans-CN' (3-letter codes are shortened first). With no track in the
    language, one in its sibling (sibling_lang: 'nno-NO' for 'no') is taken. Without a language
    the original-audio track wins. A track at a URL yt-dlp can't request (requestable_url) is
    left out, as if it weren't listed: yt-dlp fails on one before asking, a 500 here.
    """
    v = obj(item.get("video"))
    cla = obj(v.get("claInfo"))
    captions = objs(cla.get("captionInfos"))
    codes = {c["language"]: text(c.get("languageCode")) for c in captions if text(c.get("language"))}
    original = obj(cla.get("originalLanguageInfo")).get("language")
    tracks = [{"name": text(t.get("LanguageCodeName")), "url": t["Url"], "format": text(t.get("Format"))}
              for t in objs(v.get("subtitleInfos")) if requestable_url(t.get("Url"))]
    if not tracks:  # fall back to the caption list itself
        tracks = [{"name": text(c.get("language")), "url": c["url"], "format": text(c.get("captionFormat"))}
                  for c in captions if requestable_url(c.get("url"))]
    tracks = [t for t in tracks if (t["format"] or "webvtt").lower() == "webvtt"]
    if language:
        langs = [{short_lang(codes.get(t["name"])), short_lang(t["name"])} for t in tracks]  # 'eng-US' -> 'en'
        return next((t for want in filter(None, (short_lang(language), sibling_lang(language)))
                     for t, lang in zip(tracks, langs) if want in lang), None)
    return next((t for t in tracks if t["name"] == original), tracks[0] if tracks else None)


def live_user(user: dict[str, Any], stats: dict[str, Any] | None) -> dict[str, Any]:
    """Live page / profile page user -> SC liveRoomUserInfo (user fields + follower stats)."""
    keys = ("avatarLarger", "avatarMedium", "avatarThumb", "id", "nickname", "secUid", "secret", "uniqueId",
            "verified", "roomId", "signature", "status", "followStatus")
    user, stats = obj(user), obj(stats)
    out = {k: user[k] for k in keys if k in user}
    for k in ("followingCount", "followerCount"):
        if stats.get(k) is not None:
            out[k] = stats[k]
    return out


_ROOM_STATUS = {2: "live", 4: "ended"}


def live_room(data: dict[str, Any]) -> dict[str, Any]:
    """webcast/room/info `data` -> SC /v1/tiktok/live body."""
    owner, covers = obj(data.get("owner")), obj(data.get("cover")).get("url_list")
    like_count = to_int(data.get("like_count"))
    return compact({
        "room_id": data.get("id_str") or (str(data["id"]) if data.get("id") else None),
        "like_count": like_count or None,  # 0 here means "not reported", not "no likes"
        "viewer_count": to_int(data.get("user_count")),
        "status": _ROOM_STATUS.get(to_int(data.get("status")) or -1),
        "title": data.get("title"),
        "cover_url": covers[0] if isinstance(covers, list) and covers else None,
        "owner": compact({
            "id": owner.get("id_str") or (str(owner["id"]) if owner.get("id") else None),
            "username": owner.get("display_id"),
            "display_name": owner.get("nickname"),
        }) or None,
    })
