"""Pure mappers from YouTube's raw web JSON (as yt-dlp's YouTube extractors
download it) to ScrapeCreators-shaped dicts. No network access here.

`ie` arguments are yt-dlp YoutubeIE/YoutubeTabIE instances; only their pure
helpers are used (relative-time parsing, comment and lockup parsing).
"""

from __future__ import annotations

import base64
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from yt_dlp.utils import datetime_from_str, parse_duration, parse_iso8601, traverse_obj

from ..util import clock, format_count, iso_utc, parse_count

YT = "https://www.youtube.com"
WATCH = YT + "/watch?v={}"


# --- small helpers -------------------------------------------------------------


def text_of(obj: Any) -> str | None:
    """Text of a YouTube text object (simpleText / runs / content)."""
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("simpleText"), str):
        return obj["simpleText"]
    if isinstance(obj.get("runs"), list):
        return "".join(r["text"] for r in obj["runs"] if isinstance(r, dict) and isinstance(r.get("text"), str)) or None
    if isinstance(obj.get("content"), str):
        return obj["content"]
    return None


def handle_of(path: str | None) -> str | None:
    """'/@starterstory' or 'https://www.youtube.com/@x' -> 'starterstory' / 'x'."""
    m = re.search(r"/@([^/?#]+)", path) if isinstance(path, str) else None
    return unquote(m.group(1)) if m else None


# A handle as a collab dialog's entry shows it, between bidi isolates:
# '\u200e\u2068@sauceplusofficial\u2069 • \u2068244K subscribers\u2069'.
_SHOWN_HANDLE = re.compile(r"@([^\s\u2068\u2069•]+)")


def channel_block(channel_id: str | None, title: str | None, path: str | None) -> dict[str, Any]:
    """SC's {id, url, handle, title} channel object from a browse path."""
    path = path if isinstance(path, str) else None
    handle = handle_of(path)
    if handle:
        url = f"{YT}/@{handle}"
    elif path:
        url = urljoin(YT, path)
    else:
        url = f"{YT}/channel/{channel_id}" if channel_id else None
    return {"id": channel_id, "url": url, "handle": handle, "title": title}


def abs_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    return "https:" + url if url.startswith("//") else urljoin(YT, url)


def largest(thumbnails: Any) -> str | None:
    """Largest of a thumbnails list (a size that isn't a number counts as 0)."""
    thumbs = [t for t in thumbnails if isinstance(t, dict) and isinstance(t.get("url"), str) and t["url"]] \
        if isinstance(thumbnails, list) else []
    if not thumbs:
        return None
    return abs_url(max(thumbs, key=lambda t: _size(t.get("width")) * _size(t.get("height")))["url"])


def _size(value: Any) -> int | float:
    return value if isinstance(value, (int, float)) else 0


# Unit words of YouTube's localized "N <unit> ago" labels, as prefixes of the word after the number (or of one
# of the two before it: Swahili 'saa 5 zilizopita', Hebrew 'לפני יום אחד (1)', Zulu 'amaviki angu-2 edlule'; else
# of the second one after it: Romanian 'acum 21 de ani'), for every YouTube UI language (en is left to yt-dlp).
# Checked in order, so a unit can rule out a longer word of a later one ('min(?!gg)').
_REL_UNITS = (
    ("second", r"sec|seg|sek|сек|san|soniya|detik|giây|másodperc|(?:ama|i)sekhondi|δευτερ|ثاني|ثوان|ثانیه|سیکنڈ|"
               r"שני(?!ם)|সেকেন্ড|ছেকেণ্ড|सेकंड|सेकंद|सेकेन्ड|ਸਕਿੰਟ|સેકન્ડ|સેકંડ|ସେକେଣ୍ଡ|வினாடி|விநாடி|సెకన|"
               r"ಸೆಕೆಂಡ|സെക്കൻഡ|තත්පර|วินาที|ວິນາທີ|စက္ကန့်|វិនាទី|წამ|վայրկյան|ሰከን|秒|초"),
    ("minute", r"min(?!gg)|mín|мин|мүнөт|хвилин|хвілін|dak|dəq|daqiq|menit|phút|perc|(?:ama|i)minithi|λεπτ|"
               r"دقيق|دقائق|دقیقه|منٹ|דק|মিনিট|मिनट|मिनिट|मिनेट|ਮਿੰਟ|મિનિટ|ମିନିଟ|நிமிட|నిమిష|ನಿಮಿಷ|മിനിറ്റ|"
               r"මිනිත්තු|นาที|ນາທີ|မိနစ်|នាទី|წუთ|րոպե|ደቂቃ|分|분"),
    ("hour", r"hour|hor|heure|stund|or[aeăë]\b|ór|ordu|ur(?!t)|uur|tim|tun|valand|klst|klukk|hod|godz|saa|sat|oras|"
             r"soat|jam|giờ|ihora|amahora|emahore|час|сат|гадз|саат|сағат|цаг|ώρ|ساع|گھنٹ|שע|ঘণ্টা|घंट|घण्ट|तास|"
             r"ਘੰਟ|કલાક|ଘଣ୍ଟା|மணி|గంట|ಗಂಟೆ|മണിക്കൂ|පැය|ชั่วโมง|ຊົ່ວໂມງ|နာရီ|ម៉ោង|საათ|ժամ|ሰዓ|時間|小时|小時|시간"),
    ("day", r"day|d[ií]a|dies|jour|tag|giorn|dag|dae|dan|dien|dn|dň|dög|døgn|dzie|dni|gün|kun|hari|araw|siku|ngày|"
            r"nap|zi(?:le)?\b|degi|päiv|päev|egun|ditë|usuku|izinsuku|osukwini|ezinsukwini|д[ен]|дзе|дзён|дан|күн|"
            r"өдр|ημέρ|يوم|أيام|روز|دن|ימים|יו[םמ]|দিন|दिन|दिवस|ਦਿਨ|દિવસ|ଦିନ|நா|రోజు|ದಿನ|ദിവസ|දින|วัน|ມື້|ရက်|"
            r"ថ្ងៃ|დღ|օր|ቀን|ቀና|日|天|일"),
    ("week", r"wee?k|semana|semaine|setman|woche|settiman|tydz|tyg|týd|týž|ted|tjed|sedmic|nedelj|nedēļ|savai[tč]|"
             r"veck|uke|uge|vik|viik|nädal|aste|javë|săptămân|hét|hafta|həft|minggu|linggo|wiki|(?:ama|e|ema|i)viki|"
             r"tuần|недел|недељ|седмиц|тиж|тыд|апта|долоо|εβδομ|أسبوع|أسابيع|هفته|ہفت|שבוע|সপ্তাহ|सप्ताह|हफ़्त|"
             r"हफ्त|हप्ता|आठवड|ਹਫ਼ਤ|અઠવાડ|ସପ୍ତାହ|வார|వార|ವಾರ|ആഴ്ച|සති|สัปดาห์|ອາທິດ|ပတ်|សប្[តដ]ាហ៍|კვირ|շաբաթ|"
             r"ሳምን|週|周|주|星期|禮拜"),
    ("month", r"month|mes|mês|mois|monat|maand|mån|mán|miesi|měsíc|mjesec|lun|kuu|mēneš|mėnes|hilabete|muaj|bulan|"
              r"buwan|mwezi|miezi|(?:e|i)(?:zi)?nyang|tháng|hónap|ay\b|oy\b|мес|місяц|ай\b|сар|μήν|شهر|أشهر|ماه|"
              r"مہین|חודש|মাস|মাহ|माह|महीन|महिन|ਮਹੀਨ|મહિન|ମାସ|மாத|నెల|ತಿಂಗಳ|മാസ|මාස|เดือน|ເດືອນ|လ|ខែ|თვ|ամիս|"
              r"ወር|ወራ|か月|ヶ月|カ月|个月|個月|개월"),
    ("year", r"year|año|an(?!g)|jahr|jaar|år|ár|rok|lat|let|yıl|yil|il\b|tahun|taon|mwaka|miaka|năm|év|vuo|aasta|"
             r"urte|vit|vjet|gad|met|(?:u|o|imi|emi)nyak|godin|год|гад|лет|рік|рок|жыл|жил|έτ|χρόν|سن|سال|שנ|বছর|"
             r"বছৰ|वर्ष|साल|ਸਾਲ|વર્ષ|ବର୍ଷ|ஆண்ட|సంవత్సర|ವರ್ಷ|വർഷ|වසර|ปี|ປີ|နှစ်|ឆ្នាំ|წლ|տար|ዓመ|年|년"),
)
# The same word as another unit in one UI language (checked first): uk 'годин' are hours, bg/mk/sr 'години'
# years; ms 'saat' are seconds, tr 'saat' hours.
_REL_UNITS_BY_LANG = {"uk": (("hour", "годин"),), "ms": (("second", "saat"),)}
_REL_TIME = re.compile(r"(\d+)\)?\s*([^\s\d()]*)")
# UI languages that spell one and two out ('לפני שנה', 'לפני שנתיים', 'قبل شهر واحد', 'قبل شهرين'); two is the
# unit's dual form.
_SPELLED_OUT, _DUAL = ("iw", "ar"), re.compile(r".+(?:יים|ين|ان)")


def _rel_unit(word: str | None, lang: str | None) -> str | None:
    word = (word or "").lower()
    units = (*_REL_UNITS_BY_LANG.get(lang or "", ()), *_REL_UNITS)
    return next((u for u, stems in units if word and re.match(stems, word)), None)


def relative_ts(text: str, lang: str | None = None) -> int | None:
    """Localized 'hace 2 años' / 'vor 3 Tagen' / '2 年前' -> unix seconds, estimated from now.
    lang: the YouTube UI language of the text (for the few words that are different units in two languages)."""
    m = _REL_TIME.search(text or "")
    if m:
        n, after = m.groups()
        before = re.findall(r"[^\s\d()]+", text[:m.start()])[-2:]
        later = re.findall(r"[^\s\d()]+", text[m.end():])[:1]
        unit = next(filter(None, (_rel_unit(word, lang) for word in (after, *reversed(before), *later))), None)
    elif lang in _SPELLED_OUT and (word := next((w for w in (text or "").split() if _rel_unit(w, lang)), None)):
        n, unit = 2 if _DUAL.fullmatch(word) else 1, _rel_unit(word, lang)
    else:
        return None
    if not unit:
        return None
    return int(datetime_from_str(f"now-{int(n)}{unit}").replace(tzinfo=timezone.utc).timestamp())


def ui_lang(ie: Any) -> str | None:
    """The YouTube UI language an extractor asks for (the youtube:lang extractor arg); None for the default, en."""
    return getattr(ie, "_preferred_lang", None)


def approx_iso(ie: Any, time_text: str | None) -> str | None:
    """'2 days ago' -> ISO timestamp, estimated from now (to the unit of the text)."""
    if not time_text:
        return None
    ts = ie._parse_time_text(time_text, report_failure=False)
    return iso_utc(ts if ts is not None else relative_ts(time_text, ui_lang(ie)))


_SEP = r"[.,\s  ']"
_GROUPED = rf"\d{{1,3}}(?:{_SEP}\d{{3}})+|\d{{1,3}}(?:{_SEP}\d{{2}})+{_SEP}\d{{3}}"  # 10,641,372 / 1,06,41,372
# Powers of ten of the abbreviations in YouTube's localized short counts ('1,6 Mln', '46万', '4.6 lakh'; Swahili
# 'elfu 19' and Sinhala 'මි1.3' put them first; Assamese 'শঃ কোঃ' is two words), for every YouTube UI language.
# _SCALES_BY_LANG: the UI languages where the same letters are another power (Turkish 'B' is bin, a thousand;
# Czech 'mil.' a million).
_SCALES = {unicodedata.normalize("NFC", word.replace("_", " ")): power for power, words in (
    (3, "k mil tys тыс тис tis tuh tūkst þ hilj хиљ илј хил миң мың мян ming mijë հզր χιλ ألف آلاف هزار ہزار "
        "हज़ार हजार ह হাজাৰ হা ਹਜ਼ਾਰ હજાર ହ ද พัน ພັນ ထောင် ათ ሺ ពាន់ rb elfu 千 천"),
    (4, "万 萬 만 หมื่น သောင်း"),
    (5, "lakh लाख লাখ লা ਲੱਖ લાખ லட்சம் లక్షలు ಲಕ್ಷ ലക്ഷം لاکھ แสน သိန်း"),
    (6, "m mi mn mln mio mill milj млн мил м сая մլն მლნ εκ مليون ملايين میلیون jt নিযুত ନି මි ล้าน ລ້ານ "
        "သန်း ሚ លាន"),
    (7, "crore करोड़ करोड क॰ कोटी কো ਕਰੋੜ કરોડ கோடி కోటి కోట్లు ಕೋಟಿ കോടി کروڑ ကုဋေ ဋေ"),
    (8, "億 亿 억 নিঃ"),  # Assamese '2.5 নিঃ' is '257 নিযুতটা' (257 million) in the accessibility label
    (9, "b bn bi g md mrd mr mld mlrd mlr mljrd mjd mia ma mm млрд млд милј тэрбум δισ մլրդ მლრდ مليار مليارات "
        "میلیارد ارب अरब अ॰ अब्ज ਅਰਬ અબજ ବି බි พันล้าน ຕື້ ប៊ីលាន ቢ শঃ_কোঃ"),
) for word in words.split()}
_SCALES_BY_LANG = {
    "tr": {"b": 3}, "vi": {"n": 3, "tr": 6, "t": 9}, "hu": {"e": 3}, "fi": {"t": 3}, "ms": {"j": 6},
    "id": {"m": 9}, "kk": {"м": 3}, **dict.fromkeys(("cs", "sk", "ro", "hr", "bs", "sr-Latn"), {"mil": 6}),
}
# A number next to one of those abbreviations is a rounded count ('1,6 mil', '1063万件', '1 কো', 'elfu 19'); CJK
# ones are followed by the next word without a space. Others must end the word: no letter or combining mark
# follows (the Sinhala like label's '\u0daf\u0dd9\u0db1\u0dd9\u0d9a\u0dca' isn't the thousands '\u0daf').
_CJK = re.compile(r"[\u3000-\u9fff\uac00-\ud7af]+")
_MARKS = "".join(c for c in map(chr, range(0x300, 0x3000)) if unicodedata.category(c)[0] == "M")
_WORD_END = rf"(?![^\W\d_]|[{_MARKS}])"
_SCALE_WORD = "|".join(sorted((re.escape(w).replace(r"\ ", r"\s") + ("" if _CJK.fullmatch(w) else _WORD_END)
                               for w in {*_SCALES, *(w for ws in _SCALES_BY_LANG.values() for w in ws)}),
                              key=len, reverse=True))
_EXACT = re.compile(rf"(?<![\d.,])({_GROUPED}|\d+)(?![\d.,])(?!\s*(?i:{_SCALE_WORD}))")


def exact_count(text: str | None) -> int | None:
    """The full number in a count text: '10,639,582 Comments' / '10.639.582 comentarios' / '댓글 10,641,372개' /
    '1,06,41,372 टिप्पणियां' -> 10639582...; None for rounded ones ('1.6K', '1,6 mil', '1063万')."""
    m = _EXACT.search(text or "")
    if not m or _clean(text[:m.start()]).rpartition(" ")[2].lower() in _SCALES:
        return None
    return int(re.sub(r"\D", "", m.group(1)))


_COMPACT = re.compile(r"(?:([^\d\s]+)\s*)?(\d+(?:[.,\s']\d+)*)(?:\s*([^\d\s]+(?: [^\d\s]+)?))?")


def compact_count(text: str | None, lang: str | None = None) -> int | None:
    """A short count in YouTube UI language `lang` -> int: '1,6 Mln' -> 1600000, '46万' -> 460000,
    '43.428' -> 43428; None when its abbreviation isn't known to be a power of ten in that language."""
    m = _COMPACT.fullmatch(_clean(text or ""))
    if not m or (m.group(1) and m.group(3)):
        return None
    word, number = unicodedata.normalize("NFC", (m.group(1) or m.group(3) or "").lower().rstrip(".")), m.group(2)
    if not word:
        return int(re.sub(r"\D", "", number)) if re.fullmatch(rf"{_GROUPED}|\d+", number) else None
    power = (_SCALES_BY_LANG.get(lang or "") or {}).get(word, _SCALES.get(word))
    if power is None or not (num := re.fullmatch(r"(\d+)(?:[.,](\d{1,2}))?", number)):
        return None
    return round(float(f"{num.group(1)}.{num.group(2) or 0}") * 10 ** power)


def iso_ts(text: str | None) -> float | None:
    """'2025-11-07T07:00:01.184449-08:00' -> unix seconds (sub-second precision kept)."""
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return parse_iso8601(text)
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def _utf16_slice(text: str, start: int, length: int) -> str:
    raw = text.encode("utf-16-le", "surrogatepass")  # half an emoji too: its bytes are dropped as they decode
    return raw[2 * start: 2 * (start + length)].decode("utf-16-le", errors="ignore")


_INVISIBLE = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c\u2066\u2067\u2068\u2069"))


def _clean(text: str) -> str:
    """Collapse whitespace (incl. NBSP) and drop bidi marks YouTube wraps around handles."""
    return " ".join(text.translate(_INVISIBLE).split())


# --- watch page (/v1/youtube/video) -------------------------------------------


def _watch_contents(initial_data: dict[str, Any]) -> list[dict[str, Any]]:
    return traverse_obj(initial_data, (
        "contents", "twoColumnWatchNextResults", "results", "results", "contents", ..., {dict})) or []


def _renderer(initial_data: dict[str, Any], name: str) -> dict[str, Any]:
    return traverse_obj(_watch_contents(initial_data), (..., name, {dict}, any)) or {}


def link_target(command: dict[str, Any], video_id: str | None = None) -> str | None:
    """Resolve an innertube tap command to the URL it opens (None for same-video seeks)."""
    url = traverse_obj(command, ("urlEndpoint", "url", {str}))
    if url:
        parsed = urlparse(url)
        if parsed.path == "/redirect" and "youtube.com" in parsed.netloc:
            return (parse_qs(parsed.query).get("q") or [url])[0]
        return abs_url(url)
    watch_id = traverse_obj(command, ("watchEndpoint", "videoId", {str}))
    if watch_id:
        return None if watch_id == video_id else WATCH.format(watch_id)  # same id: a chapter timestamp
    path = traverse_obj(command, (
        (("browseEndpoint", "canonicalBaseUrl"), ("commandMetadata", "webCommandMetadata", "url")), {str}, any))
    return abs_url(path)


def description_links(initial_data: dict[str, Any], video_id: str) -> list[dict[str, str]]:
    """Links YouTube renders in the description: [{text, url}]."""
    desc = traverse_obj(_renderer(initial_data, "videoSecondaryInfoRenderer"), ("attributedDescription", {dict})) or {}
    content = traverse_obj(desc, ("content", {str})) or ""
    out = []
    for run in traverse_obj(desc, ("commandRuns", ..., {dict})) or []:
        command = traverse_obj(run, ("onTap", "innertubeCommand", {dict})) or {}
        url = link_target(command, video_id)
        if not url or not isinstance(run.get("startIndex"), int):
            continue
        length = traverse_obj(run, ("length", {int})) or 0
        out.append({"text": _clean(_utf16_slice(content, run["startIndex"], length)), "url": url})
    return out


def comments_panel_header(initial_data: dict[str, Any]) -> dict[str, Any] | None:
    """The watch page's comments panel header; None when comments are off."""
    return traverse_obj(initial_data, (
        "engagementPanels", ..., "engagementPanelSectionListRenderer",
        {lambda r: r if r.get("panelIdentifier") == "engagement-panel-comments-section" else None},
        "header", "engagementPanelTitleHeaderRenderer", {dict}, any))


def comment_count_text(initial_data: dict[str, Any]) -> str | None:
    """Comment count as the watch page shows it ('358', '1.6K'); a video without comments has none."""
    return text_of((comments_panel_header(initial_data) or {}).get("contextualInfo"))


def publish_date_text(initial_data: dict[str, Any]) -> str | None:
    return text_of(_renderer(initial_data, "videoPrimaryInfoRenderer").get("dateText"))


_LIKE_BUTTON = ("likeButtonViewModel", "likeButtonViewModel", "toggleButtonViewModel", "toggleButtonViewModel",
                "defaultButtonViewModel", "buttonViewModel")


def _like_buttons(initial_data: dict[str, Any]) -> list[dict[str, Any]]:
    return traverse_obj(_renderer(initial_data, "videoPrimaryInfoRenderer"), (
        "videoActions", "menuRenderer", "topLevelButtons", ..., "segmentedLikeDislikeButtonViewModel", {dict})) or []


def like_label(initial_data: dict[str, Any]) -> str | None:
    """The like button's label, with the exact count ('like this video along with 56,758,819 other people')."""
    return traverse_obj(_like_buttons(initial_data), (..., *_LIKE_BUTTON, "accessibilityText", {str}, any))


def zero_likes(initial_data: dict[str, Any]) -> bool:
    """Nobody likes the video: its label has no number then ('like this video'), but the button shows 0. The like
    count entity says 0 as well or, on some fetches of the same page, is unset. Hidden likes and ratings off show
    'Like' instead."""
    for button in _like_buttons(initial_data):
        shown = traverse_obj(button, (*_LIKE_BUTTON, "title", {str})) or ""
        number = traverse_obj(button, ("likeCountEntity", "likeCountIfIndifferentNumber", {str}))
        if shown.strip().isdecimal() and int(shown) == 0 and number in (None, "0"):
            return True
    return False


def collaborators(initial_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Channels credited on a multi-channel ("collab") upload; [] otherwise."""
    owner = traverse_obj(_renderer(initial_data, "videoSecondaryInfoRenderer"), ("owner", "videoOwnerRenderer", {dict})) or {}
    items = traverse_obj(owner, (
        "attributedTitle", "commandRuns", ..., "onTap", "innertubeCommand", "showDialogCommand",
        "panelLoadingStrategy", "inlineContent", "dialogViewModel", "customContent", "listViewModel",
        "listItems", ..., "listItemViewModel", {dict})) or []
    out = []
    for item in items:
        browse = traverse_obj(item, ("title", "commandRuns", ..., "onTap", "innertubeCommand", "browseEndpoint", {dict}, any)) or {}
        handle = traverse_obj(item, ("subtitle", "content", {_SHOWN_HANDLE.search}, {lambda m: m.group(1)}))
        block = channel_block(browse.get("browseId"), traverse_obj(item, ("title", "content", {str})),
                              f"/@{handle}" if handle else browse.get("canonicalBaseUrl"))
        if block["id"] or block["title"]:
            out.append(block)
    return out


def most_replayed(initial_data: dict[str, Any]) -> dict[str, Any] | None:
    """YouTube's "Most replayed" heatmap: normalized buckets and highlighted ranges."""
    lists = traverse_obj(initial_data, (
        "frameworkUpdates", "entityBatchUpdate", "mutations", ..., "payload", "macroMarkersListEntity",
        "markersList", {dict})) or []
    for markers_list in lists:
        if markers_list.get("markerType") != "MARKER_TYPE_HEATMAP":
            continue
        markers = []
        for m in traverse_obj(markers_list, ("markers", ..., {dict})) or []:
            try:
                markers.append({"startMillis": int(m["startMillis"]), "durationMillis": int(m["durationMillis"]),
                                "intensityScoreNormalized": float(m["intensityScoreNormalized"])})
            except (KeyError, TypeError, ValueError):
                continue
        ranges = [{
            "startMillis": d.get("visibleTimeRangeStartMillis"),
            "endMillis": d.get("visibleTimeRangeEndMillis"),
            "peakMillis": d.get("decorationTimeMillis"),
            "label": text_of(d.get("label")),
        } for d in traverse_obj(markers_list, ("markersDecoration", "timedMarkerDecorations", ..., {dict})) or []]
        if markers:
            return {"markers": markers, "ranges": ranges}
    return None


def _needs_po_token(track: Any) -> bool:
    """yt-dlp's rule: a caption URL in YouTube's exp=xpe/xpv experiment serves an empty file without a PO token."""
    url = traverse_obj(track, ("baseUrl", {str})) or ""
    return any(e in ("xpe", "xpv") for e in parse_qs(urlparse(url).query).get("exp") or [])


def caption_tracks(player_responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The caption tracks of the first player response yt-dlp would take subtitles from: one with streamingData
    (the initial web response has none) whose URLs work without a PO token. Else the first one listing tracks."""
    lists = [(bool(pr.get("streamingData")), tracks) for pr in player_responses or [] if isinstance(pr, dict)
             for tracks in [traverse_obj(pr, ("captions", "playerCaptionsTracklistRenderer", "captionTracks", {list}))]
             if tracks]
    tracks = next((t for streaming, t in lists if streaming and not any(map(_needs_po_token, t))),
                  lists[0][1] if lists else [])
    out = []
    for t in tracks:
        base_url = traverse_obj(t, ("baseUrl", {str}))
        if not base_url:
            continue
        track = {
            "baseUrl": base_url,
            "name": {"simpleText": text_of(t.get("name"))},
            "vssId": t.get("vssId"),
            "languageCode": t.get("languageCode"),
            "kind": t.get("kind"),
            "isTranslatable": t.get("isTranslatable"),
            "trackName": t.get("trackName"),
        }
        out.append({k: v for k, v in track.items() if v is not None})
    return out


def is_paid_promotion(player_responses: list[dict[str, Any]]) -> bool:
    """True when YouTube shows the "Includes paid promotion" disclosure."""
    return any(isinstance(pr, dict) and pr.get("paidContentOverlay") for pr in player_responses)


def publish_date(player_responses: list[dict[str, Any]], info: dict[str, Any]) -> str | None:
    """Microformat publishDate ('2019-02-22T03:19:54-08:00'), else the UTC timestamp."""
    return traverse_obj(player_responses, (
        ..., "microformat", "playerMicroformatRenderer", "publishDate", {str}, any)) or iso_utc(info.get("timestamp"))


_TAP = ("rendererContext", "commandContext", "onTap", "innertubeCommand")
# A length badge: '18:32', '1:02:03', or '18.32' in Indonesian, Danish, Finnish... A years-long stream's hours are
# digit-grouped: '20,772:51:34', or '20.772.51.34' in Danish.
_LENGTH_BADGE = re.compile(r"(\d{1,3}(?:[,.\u00a0\u202f' ]\d{3})+(?=(?:[:.]\d\d){2}$)|\d+)((?:[:.]\d\d){1,2})")


def lockup_channel(meta: dict[str, Any], row_title: str | None) -> dict[str, Any]:
    """{title, url, handle, id} of a lockup's channel; the first credited channel of a collab lockup."""
    image = meta.get("image") or {}
    command = traverse_obj(image, ("decoratedAvatarViewModel", *_TAP, {dict})) or {}
    title = row_title
    if not command:
        first = traverse_obj(image, (
            "avatarStackViewModel", *_TAP, "showDialogCommand", "panelLoadingStrategy", "inlineContent",
            "dialogViewModel", "customContent", "listViewModel", "listItems", 0, "listItemViewModel", {dict})) or {}
        command = traverse_obj(first, (*_TAP, {dict})) or {}
        title = traverse_obj(first, ("title", "content", {str})) or row_title
        handle = traverse_obj(first, ("subtitle", "content", {_SHOWN_HANDLE.search}, {lambda m: m.group(1)}))
        if handle:
            command = {**command, "browseEndpoint": {
                **(traverse_obj(command, ("browseEndpoint", {dict})) or {}), "canonicalBaseUrl": f"/@{handle}"}}
    browse = traverse_obj(command, ("browseEndpoint", {dict})) or {}
    path = browse.get("canonicalBaseUrl") or traverse_obj(command, ("commandMetadata", "webCommandMetadata", "url", {str}))
    block = channel_block(browse.get("browseId"), title, path)
    return {"title": block["title"], "url": block["url"], "handle": block["handle"], "id": block["id"]}


def badge_length(badge: str | None) -> tuple[str | None, int | None]:
    """A length badge -> (lengthText, seconds) with ':' between the units: '18.32' -> ('18:32', 1112),
    '20.772.51.34' -> ('20.772:51:34', 74782294). Anything else -> (None, None)."""
    m = _LENGTH_BADGE.fullmatch(badge or "")
    if not m:
        return None, None
    hours, rest = m.group(1), m.group(2).replace(".", ":")
    return hours + rest, int(parse_duration(re.sub(r"\D", "", hours) + rest))


def _readable_sources(lockup: dict[str, Any]) -> dict[str, Any]:
    """The lockup with only the thumbnail sources that are objects: yt-dlp calls .get() on each one."""
    content = traverse_obj(lockup, ("contentImage", {dict})) or {}
    view = traverse_obj(content, ("thumbnailViewModel", {dict})) or {}
    image = traverse_obj(view, ("image", {dict}))
    if image is None:
        return lockup
    image = {**image, "sources": traverse_obj(image, ("sources", ..., {dict})) or []}
    return {**lockup, "contentImage": {**content, "thumbnailViewModel": {**view, "image": image}}}


def watch_next_video(tab_ie: Any, lockup: dict[str, Any]) -> dict[str, Any] | None:
    """One related video from the watch page's lockupViewModel."""
    if lockup.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
        return None
    entry = tab_ie._extract_lockup_view_model(_readable_sources(lockup))
    if not entry or not entry.get("id"):
        return None
    meta = traverse_obj(lockup, ("metadata", "lockupMetadataViewModel", {dict})) or {}
    rows = traverse_obj(meta, ("metadata", "contentMetadataViewModel", "metadataRows", ..., "metadataParts", {list})) or []
    stats = next((r for r in rows[1:] if len(r) == 2), [])
    views_text = traverse_obj(stats, (0, "text", "content", {str}))
    views_label = traverse_obj(stats, (0, "accessibilityLabel", {str}))
    time_text = traverse_obj(stats, (1, ("accessibilityLabel", ("text", "content")), {str}, any))
    length_text, seconds = badge_length(traverse_obj(lockup, (
        "contentImage", "thumbnailViewModel", "overlays", ..., "thumbnailBottomOverlayViewModel", "badges", ...,
        "thumbnailBadgeViewModel", "text", {lambda s: s if _LENGTH_BADGE.fullmatch(s) else None}, any)))
    published = approx_iso(tab_ie, time_text)
    lang = ui_lang(tab_ie)
    views = compact_count(views_text, lang)
    if views is None and (not lang or lang.startswith("en")):
        views = entry.get("view_count")  # yt-dlp reads the label as English ('459 rb' -> 459, '4.6 lakh' -> 46)
    vid = entry["id"]
    return {
        "id": vid,
        "title": entry.get("title"),
        "thumbnail": traverse_obj(entry, ("thumbnails", -1, "url", {str})),
        "channel": lockup_channel(meta, traverse_obj(rows, (0, 0, "text", "content", {str}))),
        "publishedTimeText": time_text,
        "publishedTime": published,
        "publishDateText": time_text,
        "publishDate": published,
        # English keeps SC's '1.8M views'; other UI languages get YouTube's own localized label.
        "viewCountText": (f"{views_text} views" if re.fullmatch(r"[\d.,]+[KMB]?", views_text or "")
                          and (views_label or "").endswith(" views") else views_label or views_text),
        "viewCountInt": views,
        "lengthText": length_text,
        "lengthInSeconds": seconds,
        "videoUrl": WATCH.format(vid),
    }


def watch_next_videos(tab_ie: Any, initial_data: dict[str, Any]) -> list[dict[str, Any]]:
    lockups = traverse_obj(initial_data, (
        "contents", "twoColumnWatchNextResults", "secondaryResults", "secondaryResults", "results", ...,
        (("itemSectionRenderer", "contents", ..., "lockupViewModel"), "lockupViewModel"), {dict})) or []
    return [v for lv in lockups if (v := watch_next_video(tab_ie, lv))]


def chapters(info: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for c in info.get("chapters") or []:
        start, end = c.get("start_time"), c.get("end_time")
        out.append({
            "title": c.get("title"),
            "startMs": int(start * 1000) if start is not None else None,
            "endMs": int(end * 1000) if end is not None else None,
            "startTimeText": clock(start),
        })
    return out


def owner_handle(initial_data: dict[str, Any], player_responses: list[dict[str, Any]]) -> str | None:
    """The uploader's handle, from the owner links yt-dlp reads uploader_id from. yt-dlp's handle pattern
    ('@[\\w.-]{3,30}') stops at a middle dot or a combining mark, such as a vowel sign of Thai, Gujarati or
    Malayalam, so its uploader_id cuts such a handle short ('TVമലയാളം' -> 'TVമലയ') or, with fewer than three
    characters before the mark, has none ('ช่องทําอาหาร')."""
    owner = traverse_obj(_renderer(initial_data, "videoSecondaryInfoRenderer"), ("owner", "videoOwnerRenderer", {dict}))
    profiles = (..., "microformat", "playerMicroformatRenderer", "ownerProfileUrl", {str})
    links = (("navigationEndpoint", ("title", "runs", ..., "navigationEndpoint")),
             (("commandMetadata", "webCommandMetadata", "url"), ("browseEndpoint", "canonicalBaseUrl")), {str})
    urls = [*traverse_obj(player_responses, profiles), *traverse_obj(owner, links)]
    return next((handle for url in urls if (handle := handle_of(url))), None)


def video_channel(info: dict[str, Any], initial_data: dict[str, Any],
                  player_responses: list[dict[str, Any]]) -> dict[str, Any]:
    handle = owner_handle(initial_data, player_responses) or (info.get("uploader_id") or "").lstrip("@") or None
    return {
        "id": info.get("channel_id"),
        "url": f"{YT}/@{handle}" if handle else info.get("channel_url"),
        "handle": handle,
        "title": info.get("channel") or info.get("uploader"),
    }


def video_type(info: dict[str, Any]) -> str:
    return "short" if info.get("media_type") == "short" else "video"


def date_text(ts: int | float | None) -> str | None:
    """Unix seconds -> 'Feb 22, 2019' (the watch page's date format)."""
    if ts is None:
        return None
    dt = datetime.fromtimestamp(float(ts), timezone.utc)
    return f"{dt:%b} {dt.day}, {dt.year}"


def is_rounded(count_text: str | None) -> bool:
    """True for abbreviated display counts ('10M', '1,6 K', '2,5 mil'), False for exact ones ('358')."""
    return bool(count_text) and not re.fullmatch(r"[\d.,\s  ']+", count_text)


def video_payload(info: dict[str, Any], initial_data: dict[str, Any], player_responses: list[dict[str, Any]],
                  tab_ie: Any, comment_count: int | None = None) -> dict[str, Any]:
    """comment_count: the exact count from the comments header, when it was fetched."""
    vid = info["id"]
    comments_text = comment_count_text(initial_data)
    lang = ui_lang(tab_ie)
    if comment_count is None:
        comment_count = compact_count(comments_text, lang)
    if comment_count is None and (not lang or lang.startswith("en")):
        comment_count = info.get("comment_count")  # yt-dlp parses the label as English ('1064만' -> 1064)
    like_count = exact_count(like_label(initial_data))
    if like_count is None and zero_likes(initial_data):
        like_count = 0
    if like_count is None and (not lang or lang.startswith("en")):
        like_count = info.get("like_count")  # yt-dlp parses the label as English ('56 758 819' -> 56)
    duration, view_count = info.get("duration"), info.get("view_count")
    categories = info.get("categories") or []
    return {
        "id": vid,
        "thumbnail": info.get("thumbnail"),
        "url": WATCH.format(vid),
        "publishDate": publish_date(player_responses, info),
        "type": video_type(info),
        "title": info.get("fulltitle") or info.get("title"),  # 'title' of a live stream ends with the time
        "description": info.get("description") or None,
        "descriptionLinks": description_links(initial_data, vid),
        "commentCountText": comments_text,
        "commentCountInt": comment_count,
        "likeCountText": str(like_count) if like_count is not None else None,
        "likeCountInt": like_count,
        "viewCountText": format_count(view_count),
        "viewCountInt": view_count,
        "publishDateText": publish_date_text(initial_data) or date_text(info.get("timestamp")),
        "collaborators": collaborators(initial_data),
        "channel": video_channel(info, initial_data, player_responses),
        "chapters": chapters(info),
        "watchNextVideos": watch_next_videos(tab_ie, initial_data),
        "keywords": info.get("tags") or [],
        "genre": categories[0] if categories else None,
        "durationMs": int(duration * 1000) if duration is not None else None,
        "durationFormatted": clock(duration, always_hours=True),
        "isPaidPromotion": is_paid_promotion(player_responses),
        "most_replayed": most_replayed(initial_data),
        "captionTracks": caption_tracks(player_responses),
    }


# --- comments ------------------------------------------------------------------

_TOKEN_CHARS = re.compile(r"^[A-Za-z0-9_\-+/=%]{16,4096}$")


def decode_token(token: str) -> bytes | None:
    """Decode a native continuation token (base64 protobuf), or None if malformed."""
    token = (token or "").strip().replace(" ", "+")
    if not _TOKEN_CHARS.match(token):
        return None
    s = unquote(token).replace("+", "-").replace("/", "_").rstrip("=")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except ValueError:
        return None


def token_target(token: str) -> tuple[str, str] | None:
    """(video_id, 'comments'|'replies') a comments continuation token points at."""
    raw = decode_token(token)
    if not raw or not raw.startswith(b"\x12\r\x12\x0b") or len(raw) < 15:
        return None
    video_id = raw[4:15].decode("ascii", errors="replace")
    if not re.fullmatch(r"[\w-]{11}", video_id):
        return None
    if b"comment-replies-item-" in raw:
        return video_id, "replies"
    if b"comments-section" in raw:
        return video_id, "comments"
    return None


def continuation_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    return traverse_obj(response, (
        "onResponseReceivedEndpoints", ..., ("reloadContinuationItemsCommand", "appendContinuationItemsAction"),
        "continuationItems", ..., {dict})) or []


def comments_header(items: list[dict[str, Any]]) -> dict[str, Any]:
    return traverse_obj(items, (..., "commentsHeaderRenderer", {dict}, any)) or {}


def header_comment_count(response: dict[str, Any]) -> int | None:
    """Exact total from the first comments page's header ('10,639,582 Comments')."""
    return exact_count(text_of(comments_header(continuation_items(response)).get("countText")))


def sort_token(header: dict[str, Any], order: str) -> tuple[str | None, bool]:
    """(token, already_selected) for the 'top' (0) / 'newest' (1) sort menu entry."""
    items = traverse_obj(header, ("sortMenu", "sortFilterSubMenuRenderer", "subMenuItems", {list})) or []
    idx = 1 if order == "newest" else 0
    if idx >= len(items):
        return None, False
    item = items[idx]
    return (traverse_obj(item, ("serviceEndpoint", "continuationCommand", "token", {str})),
            bool(traverse_obj(item, "selected")))


def next_token(ie: Any, items: list[dict[str, Any]]) -> str | None:
    query = ie._extract_continuation({"contents": items})
    return (query or {}).get("continuation")


def _entities(response: dict[str, Any]) -> list[dict[str, Any]]:
    return traverse_obj(response, ("frameworkUpdates", "entityBatchUpdate", "mutations", ..., {dict})) or []


def map_comment(ie: Any, item: dict[str, Any], entities: list[dict[str, Any]], *, level: int,
                with_replies_token: bool) -> dict[str, Any] | None:
    """A commentThreadRenderer (top level) or commentViewModel (reply) -> SC comment."""
    thread = traverse_obj(item, ("commentThreadRenderer", {dict})) or {}
    view_model = traverse_obj(thread, ("commentViewModel", "commentViewModel", {dict})) or item.get("commentViewModel")
    raw_level = None
    if isinstance(view_model, dict):
        keys = {k for k in (view_model.get("commentKey"), view_model.get("toolbarStateKey"))
                if k and isinstance(k, str)}
        own = [e for e in entities if traverse_obj(e, ("entityKey", {str})) in keys]
        comment = ie._extract_comment(own) if own else None
        payload = traverse_obj(own, (..., "payload", "commentEntityPayload", {dict}, any)) or {}
        replies = parse_count(traverse_obj(payload, ("toolbar", "replyCount", {str}))) or 0
        raw_level = traverse_obj(payload, ("properties", "replyLevel", {int}))
    else:
        renderer = (traverse_obj(thread, ("comment", "commentRenderer", {dict}))
                    or traverse_obj(item, ("commentRenderer", {dict})))
        comment = ie._extract_comment_old(renderer) if renderer else None
        replies = (renderer or {}).get("replyCount") or 0
    if not comment:
        return None
    out = {
        "id": comment["id"],
        "content": comment.get("text"),
        "publishedTimeText": comment.get("_time_text") or None,
        "publishedTime": iso_utc(comment.get("timestamp")),
        "replyLevel": raw_level if raw_level is not None else level,
        "author": {
            "name": comment.get("author"),
            "channelId": comment.get("author_id"),
            "isVerified": bool(comment.get("author_is_verified")),
            "isCreator": bool(comment.get("author_is_uploader")),
            "avatarUrl": comment.get("author_thumbnail"),
            "channelUrl": comment.get("author_url"),
        },
        "engagement": {"likes": comment.get("like_count"), "replies": replies},
    }
    if with_replies_token:
        renderer = traverse_obj(thread, ("replies", "commentRepliesRenderer", {dict}))
        out["repliesContinuationToken"] = (ie._extract_continuation(renderer) or {}).get("continuation") if renderer else None
    return out


def map_comments(ie: Any, response: dict[str, Any], *, replies: bool) -> tuple[list[dict[str, Any]], str | None]:
    """One `next` response -> (comments, continuationToken)."""
    items = continuation_items(response)
    entities = _entities(response)
    out = []
    for item in items:
        if replies and "commentViewModel" not in item and "commentRenderer" not in item:
            continue
        if not replies and "commentThreadRenderer" not in item:
            continue
        if c := map_comment(ie, item, entities, level=1 if replies else 0, with_replies_token=not replies):
            out.append(c)
    return out, next_token(ie, items)


# --- community post ------------------------------------------------------------


def find_post(data: dict[str, Any], post_id: str) -> dict[str, Any] | None:
    """The post page's post with this id: a backstagePostRenderer, or the sharedPostRenderer of a shared post."""
    posts = traverse_obj(data, (
        "contents", "twoColumnBrowseResultsRenderer", "tabs", ..., "tabRenderer", "content", "sectionListRenderer",
        "contents", ..., "itemSectionRenderer", "contents", ..., "backstagePostThreadRenderer", "post",
        ("backstagePostRenderer", "sharedPostRenderer",
         ("sharedPostRenderer", "originalPost", "backstagePostRenderer")), {dict})) or []
    return next((p for p in posts if p.get("postId") == post_id), posts[0] if posts else None)


def post_images(post: dict[str, Any]) -> list[str]:
    attachment = post.get("backstageAttachment") or {}
    images = traverse_obj(attachment, ("postMultiImageRenderer", "images", ..., "backstageImageRenderer", "image", {dict}))
    if not images:
        images = traverse_obj(attachment, ("backstageImageRenderer", "image", {dict}), default=None)
        images = [images] if images else []
    return [u for img in images if (u := largest(img.get("thumbnails")))]


def post_video(tab_ie: Any, post: dict[str, Any]) -> dict[str, Any] | None:
    renderer = traverse_obj(post, ("backstageAttachment", "videoRenderer", {dict}))
    if not renderer or not renderer.get("videoId"):
        return None
    vid = renderer["videoId"]
    return {
        "id": vid,
        "title": text_of(renderer.get("title")),
        "url": WATCH.format(vid),
        "thumbnail": largest(traverse_obj(renderer, ("thumbnail", "thumbnails"))),
        "lengthText": text_of(renderer.get("lengthText")),
        "viewCountText": text_of(renderer.get("viewCountText")),
    }


def post_microformat(data: dict[str, Any], post_id: str | None) -> dict[str, Any]:
    """The page's schema.org posting details, if they describe this post (a shared post's page describes the share)."""
    details = traverse_obj(data, ("microformat", "microformatDataRenderer", "postDetails", {dict})) or {}
    if not post_id or details.get("externalPostId") != post_id:
        return {}
    return traverse_obj(details, ("discussionForumPosting", {dict})) or {}


def post_likes(post: dict[str, Any], posting: dict[str, Any]) -> int | None:
    """Exact like count (schema.org LikeAction or the like button's label), else the rounded voteCount."""
    exact = traverse_obj(posting, (
        "interactionStatistic", lambda _, v: traverse_obj(v, ("interactionType", "type")) == "LikeAction",
        "userInteractionCount", {lambda n: int(n)}, any))
    if exact is None:
        label = traverse_obj(post, (
            "actionButtons", "commentActionButtonsRenderer", "likeButton", "toggleButtonRenderer",
            (("accessibilityData", "accessibilityData", "label"), ("accessibility", "label")), {str}, any))
        exact = exact_count(m.group(1)) if (m := re.search(r"along with ([\d,.\s ]+) other", label or "")) else None
    if exact is not None:
        return exact
    likes = text_of(post.get("voteCount"))
    if likes is None:
        likes = traverse_obj(post, ("voteCount", "accessibility", "accessibilityData", "label", {str}))
    return parse_count(likes) if likes else 0 if "voteCount" in post else None


def post_payload(tab_ie: Any, post: dict[str, Any], data: dict[str, Any] | None = None) -> dict[str, Any]:
    """data: the whole post page, for its exact publish time / like count. A shared post (sharedPostRenderer: the
    sharing channel's displayName/endpoint/content) also gets the post it shares, as originalPost."""
    browse = traverse_obj(post, (("authorEndpoint", "endpoint"), "browseEndpoint", {dict}, any)) or {}
    time_text = text_of(post.get("publishedTimeText"))
    posting = post_microformat(data or {}, post.get("postId"))
    published = iso_ts(posting.get("datePublished"))
    payload = {
        "id": post.get("postId"),
        "channel": {
            "id": browse.get("browseId"),
            "title": text_of(post.get("authorText") or post.get("displayName")),
            "url": channel_block(browse.get("browseId"), None, browse.get("canonicalBaseUrl"))["url"],
            "handle": handle_of(browse.get("canonicalBaseUrl")),
        },
        "content": text_of(post.get("contentText") or post.get("content")),
        "images": post_images(post),
        "likeCount": post_likes(post, posting),
        "publishedTimeText": time_text,
        "publishedTime": iso_utc(published) if published is not None else approx_iso(tab_ie, time_text),
        "video": post_video(tab_ie, post),
    }
    original = traverse_obj(post, ("originalPost", "backstagePostRenderer", {dict}))
    if original:
        payload["originalPost"] = post_payload(tab_ie, original)
    return payload


# --- sponsors (heuristic) ------------------------------------------------------

SPONSOR_NOTE = ("Sponsors are inferred from the public description, its links, promo-code text and the transcript; "
                "YouTube only discloses that a video includes paid promotion, never the sponsor.")

_SPONSOR_CUE = re.compile(
    r"\bsponsor(?:ed|ing|s|ship)?\b|\bpaid (?:promotion|partnership)\b|\b(?:partnered|partnering|in partnership) with\b|"
    r"\bbrought to you by\b|\bthanks? (?:to )?\S+(?: \S+)? for (?:sponsoring|supporting this video)\b|"
    r"\bmade possible by\b|\b(?:a grant|funding) from\b|\bsupported by\b|\bthanks? to our friends at\b", re.I)
_PROMO_CUE = re.compile(  # the code itself must be upper-case: 'code SAVE20', not 'code for free'
    r"(?i:\b(?:promo|discount|coupon) ?code\b|\buse (?:my |the )?code\b|\d+ ?% off\b|\bdiscount\b|\bfree trial\b)|"
    r"\b(?i:code):? [A-Z0-9]{3,}\b")
_URL = re.compile(
    r"\bhttps?://[^\s<>\"')\]]+|\bwww\.[^\s<>\"')\]]+|"
    r"\b(?:[a-z0-9-]+\.)+(?:com|co|io|net|org|app|gg|me|tv|ly|to|shop|store|ai|us|uk)(?:/[^\s<>\"')\]]*)?", re.I)
_NAME = r"[A-Z0-9][\w&'+-]*(?:\.[a-z]{2,4}\b)?(?:\s+[A-Z][\w&'+-]*){0,2}"
_NAMED = re.compile(
    r"(?i:sponsored by|sponsor(?:ed)? (?:of|for) (?:this|today's) video(?: is|,)?|(?:our|today's|this video's) sponsor,?|"
    r"brought to you by|partnered with|in partnership with|made possible by(?: a grant from)?|"
    r"(?:a grant|funding) from|supported by|thanks? to our friends at)\s+(?i:our friends at\s+|the folks at\s+)?"
    rf"(?P<name>{_NAME})|(?i:thanks? (?:to )?)(?P<name2>{_NAME}) (?i:for (?:[^.!?\n]{{0,60}}? )?sponsoring)")
_TRANSCRIPT_NAMED = re.compile(
    r"(?:sponsored by|brought to you by|today's sponsor(?: is)?|our sponsor(?: is)?|partnered with)\s+"
    r"(?:our friends at\s+|the folks at\s+|the people at\s+)?(?P<name>[a-z0-9][\w'.-]+)|"
    r"thanks? to (?P<name2>[a-z0-9][\w'.-]+) for sponsoring", re.I)
_NOT_A_NAME = {"the", "a", "an", "our", "my", "this", "today", "today's", "you", "your", "all", "everyone", "us",
               "me", "them", "him", "her", "it", "these", "those", "some", "viewers", "patrons", "members"}
_NON_SPONSOR_HOSTS = {
    "youtube.com", "youtu.be", "instagram.com", "tiktok.com", "twitter.com", "x.com", "facebook.com", "fb.com",
    "fb.me", "patreon.com", "discord.com", "discord.gg", "twitch.tv", "linkedin.com", "threads.net", "reddit.com",
    "snapchat.com", "pinterest.com", "linktr.ee", "bsky.app", "t.me", "whatsapp.com", "github.com", "ko-fi.com",
    "buymeacoffee.com", "spotify.com", "apple.com", "soundcloud.com", "goo.gl", "google.com", "wikipedia.org",
}
_NON_SPONSOR_NAMES = {h.split(".")[0] for h in _NON_SPONSOR_HOSTS}  # 'supported by Patreon members'
_SHORTENERS = {"bit.ly", "geni.us", "amzn.to", "rebrand.ly", "tinyurl.com", "ow.ly", "buff.ly", "lnk.to", "go.skimresources.com",
               "shorturl.at", "cutt.ly", "t.co", "is.gd", "hubs.la", "hubs.ly", "lmg.gg"}
_EVIDENCE_CHARS = 120


def site_of(url: str) -> str | None:
    """Registrable domain of a URL ('https://www.betterhelp.com/x' -> 'betterhelp.com')."""
    try:
        host = urlparse(url if "://" in url else "http://" + url).hostname or ""
    except ValueError:  # 'http://[::1' (_URL stops at ']'), a full-width '：' after the host ('https://x.jp：詳細')
        return None
    labels = [p for p in host.lower().split(".") if p]
    if len(labels) < 2:
        return None
    n = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ("co", "com", "org", "net") else 2
    return ".".join(labels[-n:])


def _brand_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _lower(text: str) -> str:
    """text.lower() keeping text's offsets: 'İ'.lower() is two characters ('i' and a combining dot)."""
    low = text.lower()
    return low if len(low) == len(text) else "".join(c.lower()[0] for c in text)


def _snippet(text: str, around: int | None = None) -> str:
    text = _clean(text)
    if around is not None and len(text) > _EVIDENCE_CHARS:
        start = max(0, around - _EVIDENCE_CHARS // 3)
        text = ("..." if start else "") + text[start:]
    return text if len(text) <= _EVIDENCE_CHARS else text[:_EVIDENCE_CHARS].rsplit(" ", 1)[0] + "..."


def description_units(description: str) -> list[str]:
    """Description paragraphs; link lists (several URL lines) are split into lines."""
    units = []
    for para in re.split(r"\n\s*\n", description or ""):
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        if sum(bool(_URL.search(ln)) for ln in lines) > 1:
            units.extend(lines)
        elif lines:
            units.append(" ".join(lines))
    return units


def _spelled(unit: str, label: str) -> str:
    """The brand as the creator wrote it ('BetterHelp'), else the capitalized domain label."""
    bare = _URL.sub(" ", unit)
    for m in re.finditer(r"[A-Za-z0-9][\w&+.'-]*(?:\s+[A-Z][\w&+.'-]*)?", bare):
        if _brand_key(m.group(0)) == label:
            return m.group(0)
    m = re.search(rf"\b{re.escape(label)}\b", bare, re.I)
    return m.group(0) if m and not m.group(0).islower() else label.capitalize()


def detect_sponsors(description: str | None, transcript_text: str | None, *, own: set[str],
                    paid_promotion: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """Suspected sponsors with evidence, plus the detection methods that fired.

    own: lower-cased brand keys of the uploading channel(s), so self-promotion
    (their own site, merch, Patreon...) isn't reported as a sponsor.
    """
    found: dict[str, dict[str, Any]] = {}
    methods = ["paid promotion badge"] if paid_promotion else []

    def add(name: str, website: str | None, evidence: dict[str, str], strength: str) -> bool:
        """Record a candidate; False when it is the uploader's own brand."""
        key = _brand_key(website.split(".")[0] if website else name)
        if not key or key in own or any(key in o or o in key for o in own if len(o) > 3):
            return False
        entry = found.setdefault(key, {"name": name, "website": website, "_strength": strength, "evidence": []})
        entry["website"] = entry["website"] or website
        if strength == "explicit":
            entry["_strength"] = "explicit"
        if evidence not in entry["evidence"]:
            entry["evidence"].append(evidence)
        return True

    for unit in description_units(description or ""):
        explicit, promo = _SPONSOR_CUE.search(unit), _PROMO_CUE.search(unit)
        if not (explicit or promo):
            continue
        linked = named = False
        for m in _URL.finditer(unit):
            site = site_of(m.group(0))
            if not site or site in _NON_SPONSOR_HOSTS or any(site.endswith("." + h) for h in _NON_SPONSOR_HOSTS):
                continue
            if site in _SHORTENERS:
                continue  # where it leads is unknown: the brand, if any, is named in the text
            label = site.split(".")[0]
            linked |= add(_spelled(unit, label), site, {"source": "description_link", "text": _snippet(unit)},
                          "explicit" if explicit else "promo")
        for m in _NAMED.finditer(unit):
            name = (m.group("name") or m.group("name2") or "").rstrip(".,:;!")
            if not name or name.split()[0].lower() in _NOT_A_NAME:
                continue
            if site := site_of(name) if _URL.fullmatch(name) else None:
                name = _spelled(unit, site.split(".")[0])
            key = _brand_key(name)
            if key in _NON_SPONSOR_NAMES:
                continue
            if linked and key in found:
                continue  # already evidenced by its link in this paragraph
            named |= add(name, site, {"source": "description", "text": _snippet(unit)}, "explicit")
        if linked or named:
            methods.append("description link" if linked else "description")
            if promo:
                methods.append("promo code")

    transcript = _clean(transcript_text or "")
    low = _lower(transcript)  # a match's offset in low is its offset in transcript (the snippet's)
    for entry in found.values():
        keys = {_lower(entry["name"]), *([entry["website"].split(".")[0]] if entry["website"] else [])}
        pos = min((m.start() for k in keys if len(k) > 2 for m in [re.search(rf"\b{re.escape(k)}\b", low)] if m),
                  default=None)
        if pos is not None:
            entry["evidence"].append({"source": "transcript", "text": _snippet(transcript, pos)})
            entry["_transcript"] = True
    if not found and transcript:
        for m in _TRANSCRIPT_NAMED.finditer(transcript):
            name = (m.group("name") or m.group("name2") or "").rstrip(".,'")
            if name.lower() in _NOT_A_NAME or len(name) < 3:
                continue
            add(name if not name.islower() else name.capitalize(), None,
                {"source": "transcript", "text": _snippet(transcript, m.start())}, "transcript")
            for entry in found.values():
                entry["_transcript"] = True
    if any(f.get("_transcript") for f in found.values()):
        methods.append("transcript")

    sponsors = []
    for entry in found.values():
        described = any(e["source"] != "transcript" for e in entry["evidence"])
        if described and entry.get("_transcript"):
            confidence = "high"
        elif entry["_strength"] == "explicit" and described:
            confidence = "medium"
        else:
            confidence = "low"
        sponsors.append({"name": entry["name"], "website": entry["website"], "confidence": confidence,
                         "evidence": entry["evidence"]})
    order = {"high": 0, "medium": 1, "low": 2}
    sponsors.sort(key=lambda s: order[s["confidence"]])
    return sponsors, list(dict.fromkeys(methods))
