<picture>
  <source media="(prefers-color-scheme: dark)" srcset="socialscrape/static/brand/logo-white.svg">
  <img src="socialscrape/static/brand/logo.svg" alt="Social Scrape by Mimic" width="320" height="96">
</picture>

# Social Scrape API

A self-hosted, [ScrapeCreators](https://docs.scrapecreators.com)-compatible API for **YouTube, TikTok, Instagram, and the Facebook, TikTok, and Google ad libraries**, built on [yt-dlp](https://github.com/yt-dlp/yt-dlp).

It uses ScrapeCreators' paths, query parameters, `x-api-key` header and response shapes, so most existing clients only need a new base URL. Each endpoint either returns real data from yt-dlp or answers `501` with the reason it can't. **51 of 74** cataloged endpoints are implemented, including all 10 ad-library operations (12 HTTP operations when the two Facebook POST variants are counted).

## Quick start

```bash
git clone https://github.com/vyctncao/socialscrape.git
cd socialscrape
uv sync                                  # Python 3.12, installs yt-dlp with curl_cffi impersonation
uv run socialscrape                      # http://127.0.0.1:8000  (HOST / PORT to change; --help, --version)

curl 'http://127.0.0.1:8000/v1/youtube/video?url=https://www.youtube.com/watch?v=jNQXAC9IVRw'
curl 'http://127.0.0.1:8000/v1/tiktok/profile?handle=stoolpresidente'
curl 'http://127.0.0.1:8000/v1/instagram/post?url=https://www.instagram.com/p/DeKe2vzD9xZ/'
curl 'http://127.0.0.1:8000/v1/facebook/adLibrary/search/ads?query=running%20shoes'
```

- `/docs`: interactive OpenAPI docs without the portal; with the portal enabled it redirects to the dashboard's documentation at `/app/docs`, which anyone can read without signing in. The OpenAPI spec powers the dashboard docs and playground.
- `/meta/coverage`: every endpoint's status, the yt-dlp extractor it uses, and its limitations.
- `/health`: yt-dlp version and which features are enabled.

With only `SOCIALSCRAPE_API_KEYS`, these three, `/redoc` and `/openapi.json` are public. With the portal enabled, the API reference stays public (`/app/docs`, `/redoc`, `/openapi.json`, which lists the API endpoints only) while the rest of the dashboard and the API itself need a session or key; `/health` and `/meta/coverage` remain public for probes and capability checks.

## Private dashboard

Set `SOCIALSCRAPE_PORTAL_PATH` to a persistent SQLite file to enable the private web dashboard at `/app`. With an
empty database, the first visitor creates the owner account. After that there is no public registration: the owner
creates seven-day invitation links on the Team page, and each member signs in with their own password and creates
separate revocable API keys. An owner can make any other member an owner, or an owner a member, from the same page;
nobody can change their own role, so the workspace always keeps an owner. The dashboard includes a live playground, request history, usage, and documentation
generated from the server's OpenAPI schema. Request history keeps every request, but its stored response bodies only
for 14 days and up to 256 MiB in all (oldest cleared first); the response cache drops entries older than 30 days and
keeps at most 256 MiB. Both share the database volume with members and keys, so neither can fill it.

**Endpoint tests** (a Playground tab, `/app/tests`) checks what works right now: it calls every endpoint with preset parameters (public
content that should stay up, mostly the live test suite's fixtures, kept in `socialscrape/selftest.py`) and marks each
one passed, failed, needs setup (a feature this server has turned off, such as AI transcription) or not supported. A
pass needs a `200`, `success: true` and real data in the fields that preset names. The tests are live requests made with
your session, so they also appear in Request history; platforms run side by side, each platform's tests one at a time,
and the latest results stay in your browser.

Passwords use PBKDF2-SHA256. Session and API tokens are stored only as SHA-256 hashes; session cookies are HTTP-only
and SameSite strict; state-changing JSON requests require a CSRF token. For deployment, mount the database directory
on persistent storage. Set a long random `SOCIALSCRAPE_SETUP_TOKEN` as a deployment secret while creating the first
owner, then remove it; without that code, nobody can claim an empty deployment. Alternatively,
`SOCIALSCRAPE_OWNER_EMAIL` and `SOCIALSCRAPE_OWNER_PASSWORD` can bootstrap the first owner non-interactively. Supply
all three only as deployment secrets and remove them after the first successful startup—never commit them. Existing
`SOCIALSCRAPE_API_KEYS` remain accepted for backward compatibility. Owners can configure ordered pools of up to five proxies and five authenticated-account cookie files for Instagram, TikTok, YouTube, Facebook, and Google under **Settings → Proxies & accounts**. Values are encrypted in the portal database with
`SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY`, never returned after saving, and applied to new requests immediately. On a
retryable block or network/login failure, the service advances to the next proxy and account without merging cookie
jars. Use only dedicated throwaway accounts; the dashboard accepts Netscape `cookies.txt` exports and never stores
platform passwords.

Configuration is through environment variables (the `socialscrape` command takes no arguments besides `--help` and `--version`); see [`.env.example`](.env.example). A blank value means the default (except `SOCIALSCRAPE_CACHE_PATH`, where blank turns the cache off). A value that can't be used, such as `SOCIALSCRAPE_MAX_WORKERS=0`, an unknown `SOCIALSCRAPE_TRANSCRIBE`, a proxy with an unsupported scheme, a cache path that isn't a writable SQLite file or a `HOST` that doesn't resolve, stops startup with a one-line message naming the variable. The ones you're most likely to want:

| Variable | Purpose |
|---|---|
| `SOCIALSCRAPE_PORTAL_PATH` | Persistent SQLite database for invite-only sign-in, member API keys, history, usage, playground and docs. Empty disables the dashboard. |
| `SOCIALSCRAPE_API_KEYS` | Comma-separated accepted `x-api-key` values. Empty means auth is off; set it before exposing the server. ASCII only: a non-ASCII key stops startup. |
| `SOCIALSCRAPE_PROXY` | Outbound proxy URL (`http`, `https`, `socks4(a)` or `socks5(h)`). Platforms throttle datacenter IPs quickly. |
| `SOCIALSCRAPE_COOKIES_{YOUTUBE,TIKTOK,INSTAGRAM,FACEBOOK,GOOGLE}` | Netscape `cookies.txt` per platform. Use throwaway accounts. An unusable file makes that platform answer `500 cookies_error`. |
| `SOCIALSCRAPE_TRANSCRIBE` | `none` (default), `faster-whisper` or `mlx-whisper` enables AI transcription (`uv sync --extra whisper` / `--extra mlx`). Without the package, transcription answers `501 transcription_unavailable`. |
| `SOCIALSCRAPE_CACHE_PATH` | SQLite file backing `cache_max_age` (default `.cache/socialscrape.sqlite3`). Blank disables the cache. |
| `SOCIALSCRAPE_SCRAPECREATORS_API_KEY` | ScrapeCreators key for the [fallback](#scrapecreators-fallback). Empty disables it unless an owner saves one in the dashboard. Ignored without `SOCIALSCRAPE_API_KEYS` or the portal. |
| `SOCIALSCRAPE_SCRAPECREATORS_FALLBACK` | When it falls back: `unsupported`, `errors`, `restricted`, `not_found`, comma separated, or `all` or `none` (default `unsupported,errors`). |
| `SOCIALSCRAPE_SCRAPECREATORS_PLATFORMS` | The platforms it falls back for, comma separated (default all five). |
| `SOCIALSCRAPE_SCRAPECREATORS_URL` | Where it asks (default `https://api.scrapecreators.com`): a relay's base URL, or a test server. |
| `HOST` / `PORT` | Bind address and port (default `127.0.0.1:8000`). |

## Compatibility with ScrapeCreators

- **Envelope.** Responses look like `{"success": true, "credits_charged": 0, ...}`. There are no credits, so `credits_remaining` is never sent, except in answers the [ScrapeCreators fallback](#scrapecreators-fallback) served, which are ScrapeCreators' own.
- **Errors.** Every error, including unknown paths and server bugs, returns `{"success": false, "error": "<code>", "message": "..."}` with these statuses:

  | Status | Meaning |
  |---|---|
  | `400` | Bad or missing params (`bad_request`; the message names the param), a URL for the wrong platform, or a bad cursor (`bad_cursor`) |
  | `401` | Bad or missing API key (`unauthorized`) |
  | `403` | Private, login-gated, age-restricted or geo-blocked content (`forbidden`, `age_restricted`, `geo_restricted`, `restricted_profile`) |
  | `404` | Nonexistent or removed content, or a path with no endpoint here (`not_found`, e.g. another platform's ScrapeCreators path) |
  | `405` | A method the operation does not support (`method_not_allowed`, with an `Allow` header). Most endpoints are GET-only; Facebook ad search and company ads also accept POST JSON bodies. |
  | `422` | `/v1/instagram/profile/post-count` only: Instagram shows an abbreviated count and `allow_estimated` isn't true (`estimate_only`, with `estimated_media_count`) |
  | `500` | Unclassified upstream error (`upstream_error`) or timeout (`timeout`), an unusable server setting (`cookies_error`, `config_error`, or `cookies_rejected` when Instagram logs the configured session out), a failing Whisper model (`transcription_failed`), or an internal error (`server_error`; the traceback is in the server log) |
  | `501` | Endpoint not supported (`not_supported`, with the reason), or transcription off (`transcription_disabled`) or not installed (`transcription_unavailable`) |
  | `502` | No answer from the platform: `proxy_error` through a proxy (socket timeouts included), otherwise `network_error`. Retryable; details are in the server log |
  | `503` | The platform rate-limited or bot-checked us (`upstream_blocked`). Retry later or use a proxy |

  The OpenAPI spec lists the ones each operation can return, with the envelope as the `ErrorResponse` schema.

- **Caching.** `cache_max_age` (`1d`, `3d`, `7d`, `14d`, `30d`, or hours like `12h`) works on every implemented GET, and the OpenAPI spec lists it on each one. Cache hits add `cached: true` and `cached_at`. Any other value is a `400`.
- **Pagination.** Pagination uses each endpoint's ScrapeCreators parameter name (`continuationToken`, `cursor`, `max_cursor`, ...). The tokens are the platform's own where yt-dlp's machinery exposes them, otherwise opaque `ss1.` tokens.
- **Params.** A value is read without the whitespace and invisible characters (a byte order mark, zero-width spaces, bidi marks) a copy carries along at either end, and a link without the angle brackets plain text puts around it (`<https://youtu.be/...>`). A value of only those counts as left out, like an empty one.
- **Response fidelity.** Every implemented endpoint is contract-tested against the key paths and JSON types of ScrapeCreators' example responses. Fields the source can't provide truthfully are left out or null; they are never invented.

## ScrapeCreators fallback

With a [ScrapeCreators](https://scrapecreators.com) API key, a request that fails here can be answered by ScrapeCreators instead: the same path and params are sent to `https://api.scrapecreators.com` with your ScrapeCreators key, and its answer comes back. Owners set the key and choose when it applies under the dashboard's **Settings → Fallback** (`/app/fallback`), or with the `SOCIALSCRAPE_SCRAPECREATORS_*` variables; a saved dashboard value takes the place of its variable, and changes apply to the next request.

- **When.** Each trigger can be turned on or off, and the fallback limited to some platforms:

  | Trigger | Local answer | Default |
  |---|---|---|
  | `unsupported` | `501` (an endpoint or option this server can't serve: TikTok comments, Instagram pagination, ...), or `400 bad_cursor` (a token from an earlier ScrapeCreators page) | on |
  | `errors` | `500`, `502`, `503`: blocks, rate limits, timeouts, network failures, a server bug | on |
  | `restricted` | `403`: private, login-gated, age- or geo-restricted content | off |
  | `not_found` | `404 not_found` | off |

  Other errors (a bad param, a missing key, a wrong method, `422 estimate_only`) never fall back, and neither do requests carrying this server's own `ss1.` cursors or an `x-socialscrape-fallback: off` header (the dashboard's endpoint tests send it unless you allow the fallback there; with `cache_max_age`, such a request isn't answered from a cached ScrapeCreators response either).
- **What you get.** ScrapeCreators' success, unchanged, with an `x-socialscrape-fallback: scrapecreators` header. When this server had no answer of its own (`501`, `bad_cursor`), its `400`, `403`, `404` or `422` too. Otherwise (its key is refused, its credits ran out, it fails or doesn't answer in time) the local error, with a `fallback` object saying what ScrapeCreators answered: `{"provider": "scrapecreators", "status": 401, "error": "unauthorized", "message": "..."}`.
- **Credits.** Fallback requests spend ScrapeCreators credits (its errors cost none; a request that timed out may still have been charged). Its answers are cached like any other response, so `cache_max_age` avoids paying twice. ScrapeCreators is always asked live, without `cache_max_age`: an answer from its cache would otherwise be stored here as new. Request history marks the requests it served, and Usage counts them.
- **Safety.** The fallback runs only behind authentication (`SOCIALSCRAPE_API_KEYS` or the dashboard): with neither, the key is ignored, since anyone could spend the credits. Only the ScrapeCreators key is sent, never the caller's headers or key, and redirects aren't followed. A dashboard key is encrypted with `SOCIALSCRAPE_PORTAL_ENCRYPTION_KEY` and never shown again. Requests carry `Via: 1.1 socialscrape`, and one that arrives with it doesn't fall back, so an upstream URL pointed back at a socialscrape server can't loop.

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
| `/v1/youtube/video/transcript` | Native captions in the original spoken language by default (auto-dubbed videos too); `language` and `original_audio` are honored, and `language` prefers a real track to YouTube's machine translation (`en` gets `en-US`) and a track in its script to one in another (`zh-HK` and `zh-Hant` get `zh-TW`, not `zh-CN`). Any code of a language finds its track: `he` YouTube's `iw`, `nb` its `no`, `tl` its `fil`, `eng` and `ger` (ISO 639-2/B) their `en` and `de`; with no track in Nynorsk (`nn`), Bokmål (`no`) is taken, and the other way round. An uploaded track that downloads blank falls back to the auto-generated one of its language. Live and upcoming streams return `transcript: null`; videos YouTube won't play anonymously (geo-blocked, members-only, sign-in, age-restricted) return 403, but an age-restricted video that YouTube's embedded player plays keeps its captions. |
| `/v1/youtube/video/comments` | YouTube's own continuation tokens; `order=top\|newest`. |
| `/v1/youtube/video/comment/replies` | Takes the `repliesContinuationToken` from a comments page. |
| `/v1/youtube/video/sponsors` | Heuristic from the description, links, promo codes and transcript; `isPaidPromotion` comes from YouTube. Links count only next to sponsor wording or a promo code, and shorteners or redirect hosts (e.g. LTT's `lmg.gg`) name no brand. |
| `/v1/youtube/community-post` | Text, image, poll and video posts. A shared post returns the share, with the post it shares as `originalPost`. |
| `/v1/youtube/channel` | About page data: subscribers (from abbreviated text), links, country, joined date. `email` is always null. |
| `/v1/youtube/channel-videos` | Exact view counts; `sort=latest\|popular`. Without `includeExtras` there is no per-video description and `publishedTime` is estimated from YouTube's relative text ("3 weeks ago"); `includeExtras=true` adds `description`, `likeCountText`/`Int`, `commentCountText`/`Int` and YouTube's own `publishDate`, and makes `publishedTime` exact (one extra request per video, as on `channel/shorts`). |
| `/v1/youtube/channel/shorts` | The Shorts tab (48 per page) with ScrapeCreators' per-Short fields: `publishDate` (YouTube's own, with its offset), exact `viewCountInt`/`likeCountInt`/`commentCountInt`, `commentCountText` (the button's rounded text, "40K"), `description`, `genre`, `durationMs`/`durationFormatted`. The grid carries none of them, so each Short costs one more InnerTube request (`reel/reel_item_watch`, field-masked to ~1 KB): at most 4 at a time, 60 items and 20 s per page, cached 15 minutes per video. Unknown values are `null`, never `0` (likes hidden, comments off); when YouTube bot-checks or rate-limits those requests the page is still served with `null` extras for the items it didn't answer, and a dashboard proxy pool is tried in turn. |
| `/v1/youtube/channel/lives`, `/channel/playlists`, `/channel/community-posts` | Tab listings with continuations, YouTube's pages as they are (only search and hashtag skip repeats). A shared community post nests the post it shares as `originalPost`, as `/v1/youtube/community-post` does. |
| `/v1/youtube/playlist` | ~100 items per page, YouTube's pages as they are. Mixes (`RD<video id>`, `RDAMVM…`) return 400; YouTube Music's curated `RDCLAK5uy_…` playlists list, and so do its albums (`music.youtube.com/browse/MPREb_…`, one extra request). |
| `/v1/youtube/search` | `uploadDate`/`sortBy`/`type`/`duration` are encoded into YouTube's `sp` filter; `region` sets the content region (one YouTube doesn't serve, e.g. `CN`, is `400 unsupported_region`). `is_paid_promotions=true` returns 501, as on `channel-videos`: YouTube's paid-promotion search is out of yt-dlp's reach. Items already served on earlier pages (the last 200) are skipped, and a page of nothing but repeats is replaced by the next one; four such pages in a row end the results (`continuationToken` null). A page can still be empty before the end, so follow `continuationToken` until it is null. |
| `/v1/youtube/search/hashtag` | `type=all\|shorts`. Tags in any script work: they're matched in YouTube's canonical form, as on its `/hashtag/` pages. Items have no description. Repeats are skipped as on `search`. A Shorts page that comes back empty is asked for again, up to twice a second apart: YouTube sends one for a second or so after the page before it. |
| ~~`/v1/youtube/shorts/trending`~~ | YouTube removed Trending, and no extractor calls the Shorts feed. |
| ~~`/v1/youtube/search/typeahead`~~ | Suggestions come from Google's suggest API, which no extractor calls. |

### TikTok: 14 / 29

| Endpoint | Notes |
|---|---|
| `/v2/tiktok/video` | When the post is on its sound's first app-feed page, you get TikTok's app `aweme_detail` with exact counts and `author.region`; a count past int32, which the app pins at 2147483597, is the web post's rounded one instead (2400000000). Otherwise it's the web item re-keyed to the app shape, with web-rounded counts. The app feed now and then fails or leaves the post out, so the same post can come back in either shape. Photo posts are supported. |
| `/v1/tiktok/video/transcript` | Native captions as WEBVTT. `language` takes ISO 639-1 or 639-2 codes (`en`, `eng`; `deu` or `ger`); `zh`, `zho` and `cmn` all pick TikTok's Mandarin track, and Nynorsk and Bokmål stand in for each other as on YouTube. `use_ai_as_fallback` uses local Whisper when enabled: a `language` the Whisper model can't transcribe is a 400, and the text is usually null when Whisper hears no speech (best effort, see AI transcription below). |
| `/v1/tiktok/profile` | TikTok's web `userInfo` as-is. |
| `/v3/tiktok/profile/videos` | Native `max_cursor`, newest first. Photo posts carry their images as `image_post_info`, as on `/v2/tiktok/video`; posts carry no `music`. `sort_by=popular` returns 501, and `include_pinned=false` can't be honored. TikTok's intermittent statusCode 10102 refusal is a retryable `503 upstream_blocked`. |
| `/v1/tiktok/profile/region` | Read from the app API through the creator's own original sound. Returns 404 if none of their recent posts uses one; if the app API can't be reached, it's the same retryable 502/503 as `/song`. |
| `/v1/tiktok/user/live`, `/v1/tiktok/live` | Live room data. `user/live` passes TikTok's live-page JS challenge with yt-dlp's solver; one it can't pass is a `503 upstream_blocked`. |
| `/v1/tiktok/song`, `/v1/tiktok/song/videos` | App API, `cursor` paging. `clipId` is the sound's id, the address of its page (`tiktok.com/music/<title>-<id>`) or a short link to it (`vm.tiktok.com/<code>`). Counts past int32 are the app's pinned 2147483597. |
| `/v1/tiktok/collection/videos` | Public collections, 30 per page. Only TikTok's statusCode 100001 (removed or non-public) is a 404; a private one (10216/10222) is a 403, a refusal of anonymous requests a 503, an unknown code a 500. |

The other 15 return 501: comments and replies, followers and following, audience, the remaining search endpoints, trending, popular creators, Shop, and showcase. The TikTok Ad Library search and detail operations use TikTok's public Commercial Content Library and Creative Center surfaces.

### Instagram: 12 / 20

Anonymous Instagram is heavily restricted. Most endpoints return **only the first page** of what Instagram's logged-out pages show, and passing a pagination token returns 501.

| Endpoint | Notes |
|---|---|
| `/v1/instagram/post` | Reels, videos, images and carousels, plus the ~15 comments the logged-out query includes. Each video in a carousel has its own `has_audio` and `video_duration`. No play/view counts anonymously. Like `post/comments` and `media/transcript`, it takes a post or reel URL, a bare shortcode, an `instagr.am` link or a share link (`/share/reel/…` from the app's share sheet), which is followed to its post. |
| `/v2/instagram/post/comments` | First ~15 comments only. |
| `/v1/instagram/profile`, `/v1/instagram/profile/post-count` | Bio, links, follower counts, first 12 posts. The logged-out page gives those posts as slim nodes, with no counts, timestamps, video URLs, `has_audio` or `comments_disabled`. `allow_estimated` works as in ScrapeCreators (422 when only an estimate exists). A profile Instagram restricts for logged-out visitors (age-gated brands such as alcohol) returns `403 restricted_profile` here and on `user/posts`, `user/reels` and `user/highlights`; `post-count` still reads the count its page shows. |
| `/v2/instagram/user/posts`, `/v1/instagram/user/reels` | First 12. Counts only for reels. A private account with no visible reels returns 403 on `user/reels`. |
| `/v1/instagram/user/highlights`, `/v1/instagram/user/highlight/detail` | The first 10 logged-out highlights and the full highlight reel. A private account with none returns 403. |
| `/v1/instagram/search/popular`, `/v1/instagram/search/hashtag`, `/v2/instagram/reels/search` | Instagram's logged-out tag and Popular pages: the first page, usually 12 posts. Broad topics that Instagram serves as a category hub (e.g. basketball) return up to ~220 posts without captions or usernames. Topics are case-insensitive. Videos carry Instagram's play count (`play_count` on `search/popular`, `video_play_count` on the other two); there are no like/comment counts. Not a Google index like ScrapeCreators uses, so its `date_posted` values and later pages return 501. |
| `/v2/instagram/media/transcript` | Local Whisper only; 501 unless `SOCIALSCRAPE_TRANSCRIBE` is set. One entry per carousel video; text is null for videos without audio, and usually for videos without speech (best effort). Posts without a video return 404. |

The other 8 return 501: basic-profile by id, tagged posts, top search, profile search, audio reels, trending reels, comment replies and the profile embed.

### Ad libraries: 10 / 10 endpoints (12 HTTP operations)

| Library | Endpoints | Notes |
|---|---|---|
| Facebook Ad Library | `/v1/facebook/adLibrary/ad`, `/ad/transcript`, `/search/ads`, `/company/ads`, `/search/companies` | Detail, transcript, keyword/company search, company lookup, filters, and opaque pagination. Search and company ads accept either GET query parameters or a POST JSON object, which is useful for long cursors. |
| TikTok Ad Library | `/v1/tiktok/ad-library/search`, `/ad` | Searches the Commercial Content Library and reads Creative Center Top Ad details, with a library fallback for non-Top Ads. |
| Google Ad Library | `/v1/google/company/ads`, `/v1/google/ad`, `/v1/google/adLibrary/advertisers/search` | Searches Ads Transparency Center creatives and advertisers and optionally hydrates company results with creative details. |

Ad-library traffic uses the same ordered proxy/account pools as the other platforms. Configure Facebook and Google with `SOCIALSCRAPE_COOKIES_FACEBOOK` and `SOCIALSCRAPE_COOKIES_GOOGLE`, or add encrypted proxy and cookie entries under **Settings → Proxies & accounts**. Public ad-library surfaces can work anonymously, but suitable egress may still be necessary when Meta, TikTok, or Google rate-limits a server IP.

`/meta/coverage` has the full, current notes for every endpoint.

## Caveats

- **Approximate values.** Some values are YouTube's display text, not exact figures: relative times (`"3 weeks ago"`) turned into dates, and abbreviated counts (`"2.5K"`). The notes say where.
- **Rate limits.** Every platform throttles, and the ad libraries quickly: Google answers a burst from one IP with its "sorry" CAPTCHA page for a while, and Meta can block an IP after a few dozen Ad Library requests. Use `SOCIALSCRAPE_PROXY` or the per-platform proxy pools (residential works best) and `cache_max_age`. Blocks surface as `503`.
- **Cookies.** Optional. They unlock login-gated paths (e.g. Instagram comments via `media/<pk>/comments/`), but those paths were **not verified** here, since only anonymous access was tested. Never use your personal account. An Instagram session that has been logged out answers `500 cookies_rejected` (replace the file); the files are never written. Check `/health` `cookie_errors` after changing one.
- **TikTok sound API.** `/v2/tiktok/video` (app path), `/profile/region` and `/song*` rely on the app API behind `TikTokSoundIE`, which yt-dlp marks as broken. They work today but could stop without notice.
- **No JS runtime needed.** YouTube stream URLs would need a JS runtime (deno), but no endpoint returns them, so metadata, comments and captions all work without one.
- **Timeouts.** A request that hits `SOCIALSCRAPE_REQUEST_TIMEOUT` answers `500 timeout`, but yt-dlp can't be interrupted: its call keeps a worker thread until it ends. Stopping the server (Ctrl-C included) doesn't wait for those calls: it gives them a second at most. With few `SOCIALSCRAPE_MAX_WORKERS`, calls queued behind a timed-out one share its wait and can time out too; they are dropped, not run late. This mainly happens when `SOCIALSCRAPE_REQUEST_TIMEOUT` <= `SOCIALSCRAPE_SOCKET_TIMEOUT`.
- **AI transcription.** Runs locally (Whisper) and, like ScrapeCreators, refuses media over 120 s unless `allow_long_transcription=true` (that literal value only). A `language` the configured model can't transcribe is a 400 (`*.en` models are English-only). Music, silence and noise usually give null text, but that is best effort: Whisper (mlx-whisper above all) can still invent a short line there, or over the music or silence of a video that has speech. Transcriptions run one at a time, in a thread of their own.

## MCP server

SocialScrape exposes its 74 compatibility routes across YouTube, TikTok, Instagram, Facebook, and Google plus four account tools at `POST /mcp`. It implements stateless MCP Streamable HTTP for protocol versions `2025-06-18` and `2025-03-26`; no session ID is required. Configure a client with the same API key used for REST:

```json
{
  "mcpServers": {
    "socialscrape": {
      "url": "https://scrape.mimic.host/mcp",
      "headers": {"x-api-key": "YOUR_API_KEY"}
    }
  }
}
```

Every tool is read-only. Tool calls run through the application in-process, so REST authentication, request history, caching, and configured fallback behavior also apply to MCP. Set `SOCIALSCRAPE_PUBLIC_URL` in production so authentication challenges point at the canonical public origin.

MCP clients can alternatively discover OAuth 2.1 from the protected-resource metadata advertised by `/mcp`. SocialScrape supports public-client dynamic registration, authorization code grants with PKCE S256, member consent, one-hour bearer tokens, and rotating refresh tokens. The portal must be enabled because approval and token attribution use portal members. Cursor, Claude, Claude Code, and VS Code can therefore use a URL-only configuration:

```json
{"mcpServers":{"socialscrape":{"url":"https://scrape.mimic.host/mcp"}}}
```

OAuth discovery lives at `/.well-known/oauth-protected-resource/mcp` and `/.well-known/oauth-authorization-server`; registration, authorization, and token endpoints are under `/oauth`. Bearer-authenticated REST requests act as the approving member as well as authenticating `/mcp`; removing that member revokes their grants through the portal database's foreign-key cascade.

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
