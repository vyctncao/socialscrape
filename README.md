# socialscrape API

A self-hosted, [ScrapeCreators](https://docs.scrapecreators.com)-compatible API for **YouTube, TikTok and Instagram**, built on [yt-dlp](https://github.com/yt-dlp/yt-dlp).

It uses ScrapeCreators' paths, query parameters, `x-api-key` header and response shapes, so most existing clients only need a new base URL. Each endpoint either returns real data from yt-dlp or answers `501` with the reason it can't. **37 of the 66** YouTube/TikTok/Instagram endpoints are implemented.

## Quick start

```bash
git clone https://github.com/vyctncao/socialscrape.git
cd socialscrape
uv sync                                  # Python 3.12, installs yt-dlp with curl_cffi impersonation
uv run socialscrape                      # http://127.0.0.1:8000  (HOST / PORT to change; --help, --version)

curl 'http://127.0.0.1:8000/v1/youtube/video?url=https://www.youtube.com/watch?v=jNQXAC9IVRw'
curl 'http://127.0.0.1:8000/v1/tiktok/profile?handle=stoolpresidente'
curl 'http://127.0.0.1:8000/v1/instagram/post?url=https://www.instagram.com/p/DeKe2vzD9xZ/'
```

- `/docs`: interactive OpenAPI docs. The spec declares the `x-api-key` header as ScrapeCreators' `apiKeyAuth` scheme, so use **Authorize** there when auth is on.
- `/meta/coverage`: every endpoint's status, the yt-dlp extractor it uses, and its limitations.
- `/health`: yt-dlp version and which features are enabled.

When auth is on, these three, `/redoc` and `/openapi.json` are the only paths that work without an API key.

Configuration is through environment variables (the `socialscrape` command takes no arguments besides `--help` and `--version`); see [`.env.example`](.env.example). A blank value means the default (except `SOCIALSCRAPE_CACHE_PATH`, where blank turns the cache off). A value that can't be used, such as `SOCIALSCRAPE_MAX_WORKERS=0`, an unknown `SOCIALSCRAPE_TRANSCRIBE`, a proxy with an unsupported scheme, a cache path that isn't a writable SQLite file or a `HOST` that doesn't resolve, stops startup with a one-line message naming the variable. The ones you're most likely to want:

| Variable | Purpose |
|---|---|
| `SOCIALSCRAPE_API_KEYS` | Comma-separated accepted `x-api-key` values. Empty means auth is off; set it before exposing the server. ASCII only: a non-ASCII key stops startup. |
| `SOCIALSCRAPE_PROXY` | Outbound proxy URL (`http`, `https`, `socks4(a)` or `socks5(h)`). Platforms throttle datacenter IPs quickly. |
| `SOCIALSCRAPE_COOKIES_{YOUTUBE,TIKTOK,INSTAGRAM}` | Netscape `cookies.txt` per platform. Use throwaway accounts. An unusable file makes that platform answer `500 cookies_error`. |
| `SOCIALSCRAPE_TRANSCRIBE` | `none` (default), `faster-whisper` or `mlx-whisper` enables AI transcription (`uv sync --extra whisper` / `--extra mlx`). Without the package, transcription answers `501 transcription_unavailable`. |
| `SOCIALSCRAPE_CACHE_PATH` | SQLite file backing `cache_max_age` (default `.cache/socialscrape.sqlite3`). Blank disables the cache. |
| `HOST` / `PORT` | Bind address and port (default `127.0.0.1:8000`). |

## Compatibility with ScrapeCreators

- **Envelope.** Responses look like `{"success": true, "credits_charged": 0, ...}`. There are no credits, so `credits_remaining` is never sent.
- **Errors.** Every error, including unknown paths and server bugs, returns `{"success": false, "error": "<code>", "message": "..."}` with these statuses:

  | Status | Meaning |
  |---|---|
  | `400` | Bad or missing params (`bad_request`; the message names the param), a URL for the wrong platform, or a bad cursor (`bad_cursor`) |
  | `401` | Bad or missing API key (`unauthorized`) |
  | `403` | Private, login-gated, age-restricted or geo-blocked content (`forbidden`, `age_restricted`, `geo_restricted`, `restricted_profile`) |
  | `404` | Nonexistent or removed content, or a path with no endpoint here (`not_found`, e.g. another platform's ScrapeCreators path) |
  | `405` | A method other than `GET` (`method_not_allowed`, with an `Allow` header). Endpoints are GET-only, like ScrapeCreators' |
  | `422` | `/v1/instagram/profile/post-count` only: Instagram shows an abbreviated count and `allow_estimated` isn't true (`estimate_only`, with `estimated_media_count`) |
  | `500` | Unclassified upstream error (`upstream_error`) or timeout (`timeout`), an unusable server setting (`cookies_error`, `config_error`, or `cookies_rejected` when Instagram logs the configured session out), a failing Whisper model (`transcription_failed`), or an internal error (`server_error`; the traceback is in the server log) |
  | `501` | Endpoint not supported (`not_supported`, with the reason), or transcription off (`transcription_disabled`) or not installed (`transcription_unavailable`) |
  | `502` | No answer from the platform: `proxy_error` through a proxy (socket timeouts included), otherwise `network_error`. Retryable; details are in the server log |
  | `503` | The platform rate-limited or bot-checked us (`upstream_blocked`). Retry later or use a proxy |

  The OpenAPI spec lists the ones each operation can return, with the envelope as the `ErrorResponse` schema.

- **Caching.** `cache_max_age` (`1d`, `3d`, `7d`, `14d`, `30d`, or hours like `12h`) works on every implemented GET, and the OpenAPI spec lists it on each one. Cache hits add `cached: true` and `cached_at`. Any other value is a `400`.
- **Pagination.** Pagination uses each endpoint's ScrapeCreators parameter name (`continuationToken`, `cursor`, `max_cursor`, ...). The tokens are the platform's own where yt-dlp's machinery exposes them, otherwise opaque `ss1.` tokens.
- **Response fidelity.** Every implemented endpoint is contract-tested against the key paths and JSON types of ScrapeCreators' example responses. Fields the source can't provide truthfully are left out or null; they are never invented.

## What "powered by yt-dlp" means here

An endpoint is implemented only when its data comes from one of two places:

- a yt-dlp extractor's output, or
- a platform API/page that a yt-dlp extractor **already calls**, reached through that extractor's own methods or yt-dlp's networking stack, possibly with another cursor, tab or field.

Search APIs and the other endpoints that no extractor touches stay as `501`. This keeps the scraping logic maintained upstream: when a platform changes, `uv lock --upgrade-package yt-dlp` usually fixes it.

## Coverage

### YouTube: 15 / 17

| Endpoint | Notes |
|---|---|
| `/v1/youtube/video` | Exact view/like/comment counts, chapters, `most_replayed`, caption tracks (their `baseUrl`s download anonymously), watch-next. Age-restricted videos return 403 (no age-verified login). |
| `/v1/youtube/video/transcript` | Native captions in the original spoken language by default (auto-dubbed videos too); `language` and `original_audio` are honored, and `language` prefers a real track to YouTube's machine translation (`en` gets `en-US`). An uploaded track that downloads blank falls back to the auto-generated one of its language. Live and upcoming streams return `transcript: null`; videos YouTube won't play anonymously (geo-blocked, members-only, sign-in, age-restricted) return 403, but an age-restricted video that YouTube's embedded player plays keeps its captions. |
| `/v1/youtube/video/comments` | YouTube's own continuation tokens; `order=top\|newest`. |
| `/v1/youtube/video/comment/replies` | Takes the `repliesContinuationToken` from a comments page. |
| `/v1/youtube/video/sponsors` | Heuristic from the description, links, promo codes and transcript; `isPaidPromotion` comes from YouTube. Links count only next to sponsor wording or a promo code, and shorteners or redirect hosts (e.g. LTT's `lmg.gg`) name no brand. |
| `/v1/youtube/community-post` | Text, image, poll and video posts. A shared post returns the share, with the post it shares as `originalPost`. |
| `/v1/youtube/channel` | About page data: subscribers (from abbreviated text), links, country, joined date. `email` is always null. |
| `/v1/youtube/channel-videos` | Exact view counts; `sort=latest\|popular`; no per-video description. |
| `/v1/youtube/channel/shorts`, `/channel/lives`, `/channel/playlists`, `/channel/community-posts` | Tab listings with continuations, YouTube's pages as they are (only search and hashtag skip repeats). A shared community post nests the post it shares as `originalPost`, as `/v1/youtube/community-post` does. |
| `/v1/youtube/playlist` | ~100 items per page, YouTube's pages as they are. Mixes (`RD…`) return 400. |
| `/v1/youtube/search` | `uploadDate`/`sortBy`/`type`/`duration` are encoded into YouTube's `sp` filter; `region` sets the content region (one YouTube doesn't serve, e.g. `CN`, is `400 unsupported_region`). `is_paid_promotions=true` returns 501, as on `channel-videos`: YouTube's paid-promotion search is out of yt-dlp's reach. Items already served on earlier pages (the last 200) are skipped, and a page of nothing but repeats is replaced by the next one; four such pages in a row end the results (`continuationToken` null). A page can still be empty before the end, so follow `continuationToken` until it is null. |
| `/v1/youtube/search/hashtag` | `type=all\|shorts`. Tags in any script work: they're matched in YouTube's canonical form, as on its `/hashtag/` pages. Items have no description. Repeats are skipped as on `search`. |
| ~~`/v1/youtube/shorts/trending`~~ | YouTube removed Trending, and no extractor calls the Shorts feed. |
| ~~`/v1/youtube/search/typeahead`~~ | Suggestions come from Google's suggest API, which no extractor calls. |

### TikTok: 10 / 29

| Endpoint | Notes |
|---|---|
| `/v2/tiktok/video` | When the post is on its sound's first app-feed page, you get TikTok's app `aweme_detail` with exact counts and `author.region`; a count past int32, which the app pins at 2147483597, is the web post's rounded one instead (2400000000). Otherwise it's the web item re-keyed to the app shape, with web-rounded counts. The app feed now and then fails or leaves the post out, so the same post can come back in either shape. Photo posts are supported. |
| `/v1/tiktok/video/transcript` | Native captions as WEBVTT. `language` takes ISO 639-1 or 639-2T codes (`en`, `eng`); `zh`, `zho` and `cmn` all pick TikTok's Mandarin track. `use_ai_as_fallback` uses local Whisper when enabled: a `language` the Whisper model can't transcribe is a 400, and the text is usually null when Whisper hears no speech (best effort, see AI transcription below). |
| `/v1/tiktok/profile` | TikTok's web `userInfo` as-is. |
| `/v3/tiktok/profile/videos` | Native `max_cursor`, newest first. `sort_by=popular` returns 501, and `include_pinned=false` can't be honored. TikTok's intermittent statusCode 10102 refusal is a retryable `503 upstream_blocked`. |
| `/v1/tiktok/profile/region` | Read from the app API through the creator's own original sound. Returns 404 if none of their recent posts uses one; if the app API can't be reached, it's the same retryable 502/503 as `/song`. |
| `/v1/tiktok/user/live`, `/v1/tiktok/live` | Live room data. `user/live` passes TikTok's live-page JS challenge with yt-dlp's solver; one it can't pass is a `503 upstream_blocked`. |
| `/v1/tiktok/song`, `/v1/tiktok/song/videos` | App API, `cursor` paging. Counts past int32 are the app's pinned 2147483597. |
| `/v1/tiktok/collection/videos` | Public collections, 30 per page. Only TikTok's statusCode 100001 (removed or non-public) is a 404; a private one (10216/10222) is a 403, a refusal of anonymous requests a 503, an unknown code a 500. |

The other 19 return 501: comments and replies, followers and following, audience, every search endpoint, trending, popular creators, Shop, showcase and the Ad Library. yt-dlp has no TikTok extractor for any of them.

### Instagram: 12 / 20

Anonymous Instagram is heavily restricted. Most endpoints return **only the first page** of what Instagram's logged-out pages show, and passing a pagination token returns 501.

| Endpoint | Notes |
|---|---|
| `/v1/instagram/post` | Reels, videos, images and carousels, plus the ~15 comments the logged-out query includes. No play/view counts anonymously. |
| `/v2/instagram/post/comments` | First ~15 comments only. |
| `/v1/instagram/profile`, `/v1/instagram/profile/post-count` | Bio, links, follower counts, first 12 posts. `allow_estimated` works as in ScrapeCreators (422 when only an estimate exists). A profile Instagram restricts for logged-out visitors (age-gated brands such as alcohol) returns `403 restricted_profile` here and on `user/posts`, `user/reels` and `user/highlights`; `post-count` still reads the count its page shows. |
| `/v2/instagram/user/posts`, `/v1/instagram/user/reels` | First 12. Counts only for reels. A private account with no visible reels returns 403 on `user/reels`. |
| `/v1/instagram/user/highlights`, `/v1/instagram/user/highlight/detail` | The first 10 logged-out highlights and the full highlight reel. A private account with none returns 403. |
| `/v1/instagram/search/popular`, `/v1/instagram/search/hashtag`, `/v2/instagram/reels/search` | Instagram's logged-out tag and Popular pages: the first page, usually 12 posts. Broad topics that Instagram serves as a category hub (e.g. basketball) return up to ~220 posts without captions or usernames. Topics are case-insensitive. Videos carry Instagram's play count (`play_count` on `search/popular`, `video_play_count` on the other two); there are no like/comment counts. Not a Google index like ScrapeCreators uses, so its `date_posted` values and later pages return 501. |
| `/v2/instagram/media/transcript` | Local Whisper only; 501 unless `SOCIALSCRAPE_TRANSCRIBE` is set. One entry per carousel video; text is null for videos without audio, and usually for videos without speech (best effort). Posts without a video return 404. |

The other 8 return 501: basic-profile by id, tagged posts, top search, profile search, audio reels, trending reels, comment replies and the profile embed.

`/meta/coverage` has the full, current notes for every endpoint.

## Caveats

- **Approximate values.** Some values are YouTube's display text, not exact figures: relative times (`"3 weeks ago"`) turned into dates, and abbreviated counts (`"2.5K"`). The notes say where.
- **Rate limits.** All three platforms throttle. Use `SOCIALSCRAPE_PROXY` (residential works best) and `cache_max_age`. Blocks surface as `503`.
- **Cookies.** Optional. They unlock login-gated paths (e.g. Instagram comments via `media/<pk>/comments/`), but those paths were **not verified** here, since only anonymous access was tested. Never use your personal account. An Instagram session that has been logged out answers `500 cookies_rejected` (replace the file); the files are never written. Check `/health` `cookie_errors` after changing one.
- **TikTok sound API.** `/v2/tiktok/video` (app path), `/profile/region` and `/song*` rely on the app API behind `TikTokSoundIE`, which yt-dlp marks as broken. They work today but could stop without notice.
- **No JS runtime needed.** YouTube stream URLs would need a JS runtime (deno), but no endpoint returns them, so metadata, comments and captions all work without one.
- **Timeouts.** A request that hits `SOCIALSCRAPE_REQUEST_TIMEOUT` answers `500 timeout`, but yt-dlp can't be interrupted: its call keeps a worker thread until it ends. Stopping the server (Ctrl-C included) doesn't wait for those calls: it gives them a second at most. With few `SOCIALSCRAPE_MAX_WORKERS`, calls queued behind a timed-out one share its wait and can time out too; they are dropped, not run late. This mainly happens when `SOCIALSCRAPE_REQUEST_TIMEOUT` <= `SOCIALSCRAPE_SOCKET_TIMEOUT`.
- **AI transcription.** Runs locally (Whisper) and, like ScrapeCreators, refuses media over 120 s unless `allow_long_transcription=true` (that literal value only). A `language` the configured model can't transcribe is a 400 (`*.en` models are English-only). Music, silence and noise usually give null text, but that is best effort: Whisper (mlx-whisper above all) can still invent a short line there, or over the music or silence of a video that has speech. Transcriptions run one at a time, in a thread of their own.

## Development

```bash
uv run pytest -q -m "not live"   # offline: mappers, routing, validation, contracts (~12 s)
uv run pytest -q -m live         # hits YouTube/TikTok/Instagram for real (~20 min; throttled platforms skip; AI-transcript tests need --extra mlx)
```

`contracts/*.json` holds the type contracts derived from ScrapeCreators' examples (`tests/contract.py`). Some offline replay tests read raw platform responses cached by the probe runs from `$SOCIALSCRAPE_PROBE_DIR` (default `/tmp/sc`). Those files are too large to commit, so the tests skip when they're missing.

The code is laid out as follows:

- `socialscrape/` is the shared core: `ytdlp` (async bridge and error mapping), `subtitles`, `transcribe`, `cache`, `cursor` and `registry`.
- `socialscrape/platforms/*.py` holds one auto-discovered module per area.
  - Each module declares its routes with `@sc_get(...)`.
  - Each module lists what it can't serve in `UNSUPPORTED = {path: reason}`.
