<picture>
  <source media="(prefers-color-scheme: dark)" srcset="socialscrape/static/brand/logo-white.svg">
  <img src="socialscrape/static/brand/logo.svg" alt="Social Scrape by Mimic" width="320" height="96">
</picture>

# Social Scrape API

A self-hosted API working toward full [ScrapeCreators](https://docs.scrapecreators.com) compatibility, with native extractors for **YouTube, TikTok, Instagram, Bluesky, GitHub, Telegram, SoundCloud, public bio-link profiles and ad libraries**.

The pinned official API snapshot (2026-10-25) contains **199 HTTP operations across 196 paths**. All are registered and discoverable. **80 data operations have native implementations, 14 require account/backend setup and live qualification, 101 have no native implementation, and four account operations report local usage.** Registration is not proof of working extraction. This is not yet a qualified replacement for every ScrapeCreators feature; implemented endpoints can still have field, filter or access limitations. `/meta/coverage` records those limits, and optional paid fallback is explicitly labeled.

Refresh the official inventory with `uv run python scripts/refresh_upstream_contract.py`. Run `uv run python scripts/audit_upstream_coverage.py --require-native` for an inventory gate that fails while implementation or setup gaps remain. Live identity, field completeness and pagination tests are separate from this structural audit.

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

The proxy **Test** checks the selected platform through that proxy; an optional exit-IP lookup does not decide
whether it works. Results distinguish a failed proxy connection from a reachable proxy whose platform request is
blocked or inconclusive. A passing probe confirms that request only, not that every extraction endpoint will work.

### Signed-in account health

A dead or flagged signed-in account never makes a route worse than having no account. In **Settings → Proxies &
accounts**, each account in a platform's signed-in account dialog has a status badge in words: **Working**,
**Restricted by Instagram**, **Rejected by TikTok · Oct 25, 7:04 PM** (when it was rejected), **Check inconclusive**
(the check got no verdict, for example through an unreachable proxy) or **Not checked yet**. Below it are the reason the
platform gave, when the account was last checked and, for a rejected account, until when requests skip it. Instagram
and TikTok accounts have a **Check** action, like a proxy's **Test**: the button shows its progress and keeps focus,
the row updates in place, its result is announced from a live region inside the dialog, and a toast repeats it. While a dialog is open the toasts are shown inside it, so they stay visible and can be dismissed. A
new account is checked as soon as it is added; it is kept whatever the check says, and a rejection shows as a warning.
A platform's summary card reads **Account rejected** while any of its accounts was last rejected. YouTube, Facebook and
Google accounts read **No account check**. The checks are one or two requests made with the account's own cookies and
the proxy of its pool position:

- **TikTok**: the web passport API (`/passport/web/account/info/`). `session_expired` rejects the account. TikTok's app
  API answers a dead session exactly as it answers no cookies (`Please upgrade your TikTok app.`, status 5, empty lists),
  so routes that need a login check the account before the call (the verdict is reused for 10 minutes).
- **Instagram**: the account API (`/api/v1/accounts/current_user/`). Instagram rejects a session in only these ways: a
  `401 require_login` answer that deletes the `sessionid` cookie (a session it no longer knows), a redirect to the
  login, checkpoint or suspended page, or its logged-out home page served to the session. A redirect to the home page is
  settled by the signed-in home page: when its viewer is the account, the session still works but Instagram restricts
  it. Such a session is not dead: it still serves login-only routes such as `basic-profile`, `post/comment/replies`,
  `search`, `search/profiles`, `audio/reels` and `user/tagged-posts`, while other signed-in calls are refused.

The check endpoint (`POST /portal/api/settings/platforms/<platform>/cookies/<id>/check`, and the `check` field of the
answer to adding an account) returns `status` `alive`, `limited` (signed in, but restricted), `rejected` or `unknown`.
`ok` is `true` only for `alive`, `false` for `rejected` and `null` otherwise, so a script reading `ok` never takes a
restricted account for a healthy one.

In traffic, a call carrying an account is checked (the verdict is cached: 10 minutes when it works, 1 minute when it
was inconclusive) when it fails with a session error (`session_invalid`, `cookies_rejected`), `upstream_error`,
`upstream_blocked`, `forbidden` or one of Instagram's partial-answer codes (`no_profile_data`, `profile_id_unavailable`,
`profile_identity_mismatch`, `query_unavailable`, `no_session_token`), with a `404` from a restricted account, or when
its session cookie vanished during the call. Timeouts, network and proxy errors, bad input and other `404`s are not
the account's fault: they follow the proxy rotation only. Then:

- **Rejected**: the account is skipped for 6 hours (24 hours after a repeated rejection) and logged at WARNING with the
  platform and entry id, never a cookie value; a passing check or a replaced file ends the cooldown. The attempt is
  repeated through the same proxy with the next account, or without account cookies.
- **Restricted, inconclusive, or a session error the check did not contradict**: the attempt is repeated without account
  cookies through the same proxy, and the call goes on without them through the remaining proxies, as a call with no
  account would.
- **Working** (or no check for the platform): the account keeps its proxy rotation, and its last failure is retried once
  without account cookies through the last proxy, except a throttle (`upstream_blocked`) answered to a working account,
  which a logged-out call through the same egress would share.

Routes that work logged out therefore answer as they do with no account. A route that needs a login fails a call
without account cookies at once, before any request; the account then takes over again where it failed (on the next
proxy if its error was retryable). When it still fails, the answer is a setup error only when the evidence is about the
session: `501 platform_auth_required` with `reason: "account_rejected"` (every account rejected, or a session error from
an account no check cleared) or `"account_limited"` (Instagram sent the restricted session to its home or login page),
and a message naming the account and where to replace it. Any other failure (a `404` for a bad id, a throttle, a field
error) keeps its own status and code, with `account_health: "limited"` when the account is restricted.

A session cookie that vanishes during a call is a reason to check the account (in the background when the call
succeeded), not a verdict: yt-dlp clears Instagram's `sessionid` itself after an HTTP error on a URL it counts as a
login redirect, the home page included. YouTube and Facebook accounts, which have no check, are rejected when their
session cookie (`LOGIN_INFO`, `c_user`) is deleted. Instagram's topic pages (`search/hashtag`, `search/popular`,
`reels/search`, `reels/trending`) and the profile embed page (`user/embed`) are always read without account cookies: a
signed-in session is sent to Instagram's signed-in keyword search, which carries none of the topic data, and Instagram
renders the visitor's own `csrftoken`, `ds_user_id`, `ig_did` and `mid` into the embed HTML, which the route returns
verbatim.

Configuration is through environment variables (the `socialscrape` command takes no arguments besides `--help` and `--version`); see [`.env.example`](.env.example). A blank value means the default (except `SOCIALSCRAPE_CACHE_PATH`, where blank turns the cache off). A value that can't be used, such as `SOCIALSCRAPE_MAX_WORKERS=0`, an unknown `SOCIALSCRAPE_TRANSCRIBE`, a proxy with an unsupported scheme, a cache path that isn't a writable SQLite file or a `HOST` that doesn't resolve, stops startup with a one-line message naming the variable. The ones you're most likely to want:

| Variable | Purpose |
|---|---|
| `SOCIALSCRAPE_PORTAL_PATH` | Persistent SQLite database for invite-only sign-in, member API keys, history, usage, playground and docs. Empty disables the dashboard. |
| `SOCIALSCRAPE_API_KEYS` | Comma-separated accepted `x-api-key` values. Empty means auth is off; set it before exposing the server. ASCII only: a non-ASCII key stops startup. |
| `SOCIALSCRAPE_PROXY` | Outbound proxy URL (`http`, `https`, `socks4(a)` or `socks5(h)`). Platforms throttle datacenter IPs quickly. |
| `SOCIALSCRAPE_COOKIES_{YOUTUBE,TIKTOK,INSTAGRAM,FACEBOOK,GOOGLE}` | Netscape `cookies.txt` per platform. Use throwaway accounts. An unusable file makes that platform answer `500 cookies_error`. The file's account gets the same health checks, skipping and logged-out retry as a dashboard account ([Signed-in account health](#signed-in-account-health)); `/health` shows its verdict under `cookie_health`, and a replaced file starts fresh. |
| `SOCIALSCRAPE_TRANSCRIBE` | `none` (default), `faster-whisper` or `mlx-whisper` enables AI transcription (`uv sync --extra whisper` / `--extra mlx`). Without the package, transcription answers `501 transcription_unavailable`. The Docker image sets `faster-whisper` and bakes in the `small` model (`docker build --build-arg WHISPER_MODEL=base` bakes another); `SOCIALSCRAPE_TRANSCRIBE=none` turns it off. |
| `SOCIALSCRAPE_CACHE_PATH` | SQLite file backing `cache_max_age` (default `.cache/socialscrape.sqlite3`). Blank disables the cache. |
| `SOCIALSCRAPE_SCRAPECREATORS_API_KEY` | ScrapeCreators key for the [fallback](#scrapecreators-fallback). Empty disables it unless an owner saves one in the dashboard. Ignored without `SOCIALSCRAPE_API_KEYS` or the portal. |
| `SOCIALSCRAPE_SCRAPECREATORS_FALLBACK` | When it falls back: `unsupported`, `errors`, `restricted`, `not_found`, comma separated, or `all` or `none` (default `unsupported,errors`). |
| `SOCIALSCRAPE_SCRAPECREATORS_PLATFORMS` | The platforms it falls back for, comma separated (default all catalog platforms). |
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
  | `500` | Unclassified upstream error (`upstream_error`) or timeout (`timeout`), an unusable server setting (`cookies_error`, `config_error`), a failing Whisper model (`transcription_failed`), or an internal error (`server_error`; the traceback is in the server log) |
  | `501` | Endpoint not supported (`not_supported`, with the reason), transcription off (`transcription_disabled`) or not installed (`transcription_unavailable`), or a route that needs a signed-in account when none is configured or the platform rejected it (`platform_auth_required`; with `reason: "account_rejected"` or `"account_limited"` when an account is configured, see [Signed-in account health](#signed-in-account-health)) |
  | `502` | No answer from the platform: `proxy_error` through a proxy (socket timeouts included), otherwise `network_error`. Retryable; details are in the server log |
  | `503` | The platform rate-limited or bot-checked us (`upstream_blocked`). Retry later or use a proxy; a `Retry-After` header, when present, says how many seconds this server will wait before asking the platform again |

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

## Native data sources

Native endpoints read real platform data:

- a yt-dlp extractor's output, or
- a platform API/page reached through yt-dlp's networking stack, or
- a platform's public guest API, public HTML or embedded profile data, through bounded requests to fixed platform origins.

Bluesky uses its public XRPC API. GitHub uses anonymous REST and public profile/trending pages. Telegram reads public channel previews and post embeds. SoundCloud uses yt-dlp's guest client and original API pagination. Linktree, Lnk.Bio, Komi and Linkme read their public profile data. Anonymous requests do not inherit browser cookies, CLI tokens or authorization headers. Redirects cannot leave the platform's allowed origins; oversized responses fail explicitly.

With Instagram account cookies, profiles, feeds, highlights, basic profile lookup and search use the signed-in web client's current GraphQL queries and page request tokens. The requested route identifies the profile independently of the signed-in viewer, and returned profile/media identities are checked. Posts keep every item of each native page; reels add dates and captions from authenticated media-info responses. Request tokens and cookie values are never returned. Anonymous Instagram continues to use its logged-out client separately.

One bounded exception is YouTube's public Subscribe widget: when the channel page omits its subscriber count,
`/v1/youtube/channel` asks the matching channel's widget once through yt-dlp's networking stack. Only a visible
count is used, including an explicit `0`; a missing or hidden count remains `null`.

Sign-in-gated Shorts can still expose public like/comment buttons. Those counts are kept only when the action
bar's entity key matches the requested video; dates and other player-only fields remain `null` when unavailable.

Endpoints without a native data source stay as `501`. TikTok audience demographics remain unsupported: its Research User Info API does not provide audience country analytics, so adding a Research token does not enable that feature. TikTok Shop also remains unsupported: its request-signing and product/search integrations are not implemented, so storing credentials alone does not enable those routes. Coverage notes distinguish missing implementation from account or service setup.

## Coverage

The tables below describe the original video-platform routes. `/meta/coverage` and `scripts/audit_upstream_coverage.py` include the complete pinned API inventory and all additional public platforms. The `implemented` label describes available code, not a sustained live qualification result.

### YouTube: 17 / 17

| Endpoint | Notes |
|---|---|
| `/v1/youtube/video` | Exact view/like/comment counts, chapters, `most_replayed`, caption tracks (their `baseUrl`s download anonymously), watch-next. Age-restricted videos return 403 (no age-verified login). |
| `/v1/youtube/video/transcript` | Native captions in the original spoken language by default (auto-dubbed videos too); `language` and `original_audio` are honored, and `language` prefers a real track to YouTube's machine translation (`en` gets `en-US`) and a track in its script to one in another (`zh-HK` and `zh-Hant` get `zh-TW`, not `zh-CN`). Any code of a language finds its track: `he` YouTube's `iw`, `nb` its `no`, `tl` its `fil`, `eng` and `ger` (ISO 639-2/B) their `en` and `de`; with no track in Nynorsk (`nn`), Bokmål (`no`) is taken, and the other way round. An uploaded track that downloads blank falls back to the auto-generated one of its language. Live and upcoming streams return `transcript: null`; videos YouTube won't play anonymously (geo-blocked, members-only, sign-in, age-restricted) return 403, but an age-restricted video that YouTube's embedded player plays keeps its captions. |
| `/v1/youtube/video/comments` | YouTube's own continuation tokens; `order=top\|newest`. |
| `/v1/youtube/video/comment/replies` | Takes the `repliesContinuationToken` from a comments page. |
| `/v1/youtube/video/sponsors` | Heuristic from the description, links, promo codes and transcript; `isPaidPromotion` comes from YouTube. Links count only next to sponsor wording or a promo code, and shorteners or redirect hosts (e.g. LTT's `lmg.gg`) name no brand. |
| `/v1/youtube/community-post` | Text, image, poll and video posts. A shared post returns the share, with the post it shares as `originalPost`. |
| `/v1/youtube/channel` | About page data: subscribers (from abbreviated text, or the public Subscribe widget when the page omits it), numeric `videoCount`, `isVerified` from the channel header, links, country, joined date. An explicit empty-channel state means zero public videos; missing counts stay null. `email` is always null. |
| `/v1/youtube/channel-videos` | Exact view counts; `sort=latest\|popular`. Without `includeExtras` there is no per-video description and `publishedTime` is estimated from YouTube's relative text ("3 weeks ago"); `includeExtras=true` adds `description`, `likeCountText`/`Int`, `commentCountText`/`Int` and YouTube's own `publishDate`, and makes `publishedTime` exact (one extra request per video, as on `channel/shorts`). |
| `/v1/youtube/channel/shorts` | The Shorts tab (48 per page) with ScrapeCreators' per-Short fields: `publishDate` (YouTube's own, with its offset), exact `viewCountInt`/`likeCountInt`/`commentCountInt`, `commentCountText` (the button's rounded text, "40K"), `description`, `genre`, `durationMs`/`durationFormatted`. The grid carries none of them, so each Short costs one more InnerTube request (`reel/reel_item_watch`, field-masked to ~1 KB): at most 4 at a time, 60 items and 20 s per page, cached 15 minutes per video. Unknown values are `null`, never `0` (likes hidden, comments off); when YouTube bot-checks or rate-limits those requests the page is still served with `null` extras for the items it didn't answer, and a dashboard proxy pool is tried in turn. |
| `/v1/youtube/channel/lives`, `/channel/playlists`, `/channel/community-posts` | Tab listings with continuations, YouTube's pages as they are (only search and hashtag skip repeats). A shared community post nests the post it shares as `originalPost`, as `/v1/youtube/community-post` does. |
| `/v1/youtube/playlist` | ~100 items per page, YouTube's pages as they are. Mixes (`RD<video id>`, `RDAMVM…`) return 400; YouTube Music's curated `RDCLAK5uy_…` playlists list, and so do its albums (`music.youtube.com/browse/MPREb_…`, one extra request). |
| `/v1/youtube/search` | `uploadDate`/`sortBy`/`type`/`duration` are encoded into YouTube's `sp` filter; `region` sets the content region (one YouTube doesn't serve, e.g. `CN`, is `400 unsupported_region`). `is_paid_promotions=true` returns 501, as on `channel-videos`: YouTube's paid-promotion search is out of yt-dlp's reach. Items already served on earlier pages (the last 200) are skipped, and a page of nothing but repeats is replaced by the next one; four such pages in a row end the results (`continuationToken` null). A page can still be empty before the end, so follow `continuationToken` until it is null. |
| `/v1/youtube/search/hashtag` | `type=all\|shorts`. Tags in any script work: they're matched in YouTube's canonical form, as on its `/hashtag/` pages. Items have no description. Repeats are skipped as on `search`. A Shorts page that comes back empty is asked for again, up to twice a second apart: YouTube sends one for a second or so after the page before it. |
| `/v1/youtube/shorts/trending` | YouTube retired its Trending page: these are the popular Shorts YouTube's own search surfaces now (`YoutubeTabIE`), not an official global chart. |
| `/v1/youtube/search/typeahead` | Text suggestions from Google's public suggestion service (`suggestqueries.google.com`); its JSON has no channel metadata, so `channel` is null. An empty or HTML answer (throttled) is a `503 upstream_blocked`. |

When YouTube rate-limits the server's IP, it answers a video's watch page and player requests with HTTP 429 or empty bodies, and its InnerTube API with a web page or nothing where JSON belongs: `video`, `video/transcript`, `video/sponsors` and the InnerTube listings return a retryable `503 upstream_blocked` (the next proxy in the pool is tried), not a 500 "Failed to extract any player response" or "Failed to parse JSON".

### TikTok: 15 / 29

| Endpoint | Notes |
|---|---|
| `/v2/tiktok/video` | When the post is on its sound's first app-feed page, you get TikTok's app `aweme_detail` with exact counts and `author.region`; a count past int32, which the app pins at 2147483597, is the web post's rounded one instead (2400000000). Otherwise it's the web item re-keyed to the app shape, with web-rounded counts. The app feed now and then fails or leaves the post out, so the same post can come back in either shape. Photo posts are supported. |
| `/v1/tiktok/video/transcript` | Native captions as WEBVTT. `language` takes ISO 639-1 or 639-2 codes (`en`, `eng`; `deu` or `ger`); `zh`, `zho` and `cmn` all pick TikTok's Mandarin track, and Nynorsk and Bokmål stand in for each other as on YouTube. `use_ai_as_fallback` uses local Whisper when enabled: a `language` the Whisper model can't transcribe is a 400, and the text is usually null when Whisper hears no speech (best effort, see AI transcription below). |
| `/v1/tiktok/profile` | TikTok's web `userInfo` as-is. |
| `/v3/tiktok/profile/videos` | Native `max_cursor`, newest first. Photo posts carry their images as `image_post_info`, as on `/v2/tiktok/video`; posts carry no `music`. `sort_by=popular` returns 501, and `include_pinned=false` can't be honored. From a throttled or bot-checked server IP, TikTok's feed API answers with an empty body or its JS challenge page instead of JSON (once a `500 "Failed to parse JSON"`) while its pages still load: the challenge is solved with yt-dlp's solver, and that answer or a statusCode 10102 refusal is asked again twice (1.5 s, then 3 s). Still blocked, the first page comes from the creator's embed page (`/embed/@handle`, its ~10 newest posts), each post read from its own page, so it has the same fields; its `max_cursor` continues on the feed API. A pinned post older than those is left for the feed API's date order, and the page ends just before a post that answers 10204 or a bot check (TikTok sends both to a throttled IP for posts it has) or after 30 s, so `max_cursor` asks for the rest again instead of skipping it. For the next 60 s (doubling while TikTok keeps blocking) the feed API isn't asked from that IP or proxy at all: calls go straight to the embed page instead of repeating three blocked asks. Only then is it a retryable `503 upstream_blocked` (with `Retry-After` while cooling down). A post with comments turned off has no `comment_count` (TikTok's web data reports it as 0), and a verified author carries `verification_type: 1` and `custom_verify`, as the app reports them. |
| `/v1/tiktok/profile/region` | Read from the app API through the creator's own original sound. Returns 404 if none of their recent posts uses one; if the app API can't be reached, it's the same retryable 502/503 as `/song`. When the feed API is blocked, the posts come from the embed page, as above. Also served at `/v1/tiktok/profile-region` (a redirect). A handle's answer (its region, the 404 or the 503) is reused for 90 s, so a client's immediate retry, through the alias or not, asks TikTok nothing. |
| `/v1/tiktok/user/live`, `/v1/tiktok/live` | Live room data. `user/live` passes TikTok's live-page JS challenge with yt-dlp's solver; a live page it can't pass is answered from the profile page and `room/info` (`is_live` without `liveRoom`), and only when those fail too is it a `503 upstream_blocked`. A `room/info` answer that isn't JSON is asked again twice (1.5 s, 3 s), then a 503, never a guessed `is_live`. |
| `/v1/tiktok/song`, `/v1/tiktok/song/videos` | App API, `cursor` paging. `clipId` is the sound's id, the address of its page (`tiktok.com/music/<title>-<id>`) or a short link to it (`vm.tiktok.com/<code>`). Counts past int32 are the app's pinned 2147483597. |
| `/v1/tiktok/collection/videos` | Public collections, 30 per page. Only TikTok's statusCode 100001 (removed or non-public) is a 404; a private one (10216/10222) is a 403, a refusal of anonymous requests a 503, an unknown code a 500. An empty body or JS challenge page in place of JSON is handled as on `/v3/tiktok/profile/videos`, then a `503 upstream_blocked` (the collection's page has no items to fall back on). |
| `/v1/tiktok/search/keyword`, `/v1/tiktok/search/hashtag` | Logged out, from TikTok's public keyword pages (`tiktok.com/discover/<keyword>`, their `/api/seo/kap/video_list/`): TikTok's own web and app search answer an anonymous request with an empty body (they want request signatures). The query is spelled as those pages are (`Skincare routine` → `skincare-routine`); posts are TikTok web items in the app shape (web-rounded counts, `author.follower_count`, `music.play_url`), 30 per request and ~60–90 per keyword, ranked for `region` (default US). `cursor` is an offset, omitted on the last page. Hashtag search keeps only posts carrying the tag. A keyword TikTok made no page for (`fitness`, `skincare`; TikTok answers status_code 4), and `date_posted`/`sort_by` other than `all-time`/`relevance`, are `501 platform_auth_required`: only the signed-in app API (TikTok account cookies) searches those. Comment counts and verification badges as on `/v3/tiktok/profile/videos`. An empty body, a challenge page, a cut-off body or HTTP 403/429/5xx is asked once more, then is a retryable `503 upstream_blocked`. |
| `/v1/tiktok/search/users` | An exact-handle lookup: the matching public profile, not TikTok's ranked user search. |

The other 14 return 501: comments and replies, followers and following, audience, top search and suggestions, trending, popular creators, Shop, and showcase. Popular creators ranks the creators in TikTok's regional recommended feed, which TikTok serves only to a signed-in account (Creative Center's creator chart now needs a TikTok One login), so it works with TikTok account cookies in Platform settings and is otherwise `501 platform_auth_required`. The TikTok Ad Library search and detail operations (counted here) use TikTok's public Commercial Content Library and Creative Center surfaces.

### Instagram: 14 public routes and 6 account routes

With account cookies configured in Platform settings, profiles, post counts, posts, reels, highlights, basic profile lookup and search use Instagram's signed-in web client. Its page supplies the requested profile's ID and request tokens; the response must identify that same profile. Profile counts are exact when exposed, every native feed item and continuation cursor is kept, and reels receive dates/captions from authenticated media info. `require_play_counts=true` on posts/reels rejects incomplete play counts. Missing counts remain unknown. Search uses the current GraphQL query; the retired top-search URL is no longer used. Account cookies do not establish that every gated route is working: the live endpoint tests and coverage notes remain the readiness check.

Public topic, hashtag and trending pages deliberately run without account cookies, including when an account is configured: Instagram serves those pages only to public visitors. Tagged posts use the current GraphQL connection and retain their real cursor; audio uses the web client's read-only POST and preserves its native media objects and separate Instagram/Facebook counters. When a signed-in feed omits public media, the public reader must identify the same profile before its items can be returned. Any continuation from that reader uses a scoped `ss1.` token so later pages keep the same client. An authenticated media-unavailable response is confirmed with the public client before returning 404.

Anonymous Instagram is heavily restricted. Most endpoints return **only the first page** of what Instagram's logged-out pages show, and passing a pagination token returns 501; `user/posts` and `user/reels` page through the profile's tabs the way the logged-out web client scrolls them.

| Endpoint | Notes |
|---|---|
| `/v1/instagram/post` | Reels, videos, images and carousels, plus the ~15 comments the logged-out query includes. Each video in a carousel has its own `has_audio` and `video_duration`. The logged-out post has no play or view count, so a reel's `video_play_count` (Instagram's own plays, the figure `user/reels` shows) is read from its owner's logged-out Reels tab, paged back to the reel: up to 6 requests reach about the 800th reel, and the pages are reused for 5 minutes, so refreshing an account's reels pages its tab once. It is left out, never 0, when the tab doesn't list the reel within that, Instagram refuses the tab, or `include_play_count=false`; feed videos, IGTV and carousels aren't on the tab and have none. `video_view_count` (the legacy 3-second count) needs cookies. Like `post/comments` and `media/transcript`, it takes a post or reel URL, a bare shortcode, an `instagr.am` link or a share link (`/share/reel/…` from the app's share sheet), which is followed to its post. |
| `/v2/instagram/post/comments` | First ~15 comments only. |
| `/v1/instagram/profile`, `/v1/instagram/profile/post-count` | Bio, links, follower counts, first 12 posts. The logged-out page gives those posts as slim nodes, with no counts, timestamps, video URLs, `has_audio` or `comments_disabled`. `allow_estimated` works as in ScrapeCreators (422 when only an estimate exists). A profile Instagram restricts for logged-out visitors (age-gated brands such as alcohol) returns `403 restricted_profile` here and on `user/posts`, `user/reels` and `user/highlights`; `post-count` still reads the count its page shows. |
| `/v2/instagram/user/posts`, `/v1/instagram/user/reels` | 12 per page, with Instagram's own cursor as `next_max_id` (posts, plus `more_available`) or `max_id` (reels): the next page is the tab's logged-out GraphQL query with `after`, as the page's scrolling makes it; a cursor Instagram can't continue from is a 400 `bad_cursor`. Posts carry `like_count` and `play_count` (= `ig_play_count`) from that query as Instagram serves it to crawlers, reels the tab's like, comment and play counts. Each item is then completed by the logged-out post query yt-dlp's `InstagramIE` makes (one request per item): `taken_at`/`created_at`, the caption (`caption.text`, `pk`, `created_at`), `comment_count`, `image_versions2`, `video_versions`, `has_audio`, `video_duration`. An item either listing got in the last 15 minutes isn't asked again, so listing an account's posts and then its reels (or the other way round) costs one post query per distinct item, and a reel the post query wasn't answered for takes the caption the posts tab gave its shortcode. Those requests stop at the first one Instagram refuses (a GraphQL `errors` list or "Please wait a few minutes" included), or after 40 s, and the remaining items go without those fields (never zeros or guesses); hidden like/view counts are left out. A page takes about 12 s. A private account with no visible reels returns 403 on `user/reels`. |
| `/v1/instagram/user/highlights`, `/v1/instagram/user/highlight/detail` | The first 10 logged-out highlights and the full highlight reel, read from the `/stories/highlights/<id>/` page, which carries Instagram's `feed/reels_media` answer prefetched (the API call itself answers anonymous clients with a web page). `/v1/instagram/highlights/details` and `/v1/instagram/highlight/details` redirect to `highlight/detail`, renaming `highlight_id` to `id`. A private account with none returns 403. |
| `/v1/instagram/search/popular`, `/v1/instagram/search/hashtag`, `/v2/instagram/reels/search` | Instagram's logged-out tag and Popular pages: the first page, usually 12 posts. Broad topics that Instagram serves as a category hub (e.g. basketball) return up to ~220 posts without captions or usernames. Topics are case-insensitive. Videos carry Instagram's play count (`play_count` on `search/popular`, `video_play_count` on the other two); there are no like/comment counts. Not a Google index like ScrapeCreators uses, so its `date_posted` values and later pages return 501. |
| `/v1/instagram/reels/trending` | The reels on Instagram's public Popular page for "reels": what it surfaces now, not an official global ranking. |
| `/v1/instagram/user/embed` | Instagram's public profile embed page as HTML (`html`), to embed or sanitize. 404 when the embed has no profile to show (an unknown name); an empty or block page in its place is a `503`, not HTML returned as success. |
| `/v2/instagram/media/transcript` | Local Whisper only; 501 unless `SOCIALSCRAPE_TRANSCRIBE` is set. One entry per carousel video; text is null for videos without audio, and usually for videos without speech (best effort). Posts without a video return 404. |

The other 6 return 501: basic-profile by id, tagged posts, top search, profile search, audio reels and comment replies. Top search (`/v1/instagram/search`) and profile search need a saved Instagram account: logged-out Instagram has no search, so without one they return `501 platform_auth_required`, a setup gap the ScrapeCreators fallback can take.

Instagram turns away anonymous API calls it is throttling with an empty body, a web page instead of JSON, `401 {"require_login": true}`, `{"status": "fail", "message": "Please wait a few minutes…"}` or a GraphQL `errors` list without data; these are `503 upstream_blocked` (one retry for an empty or non-JSON answer, then the next proxy in the pool), not a "Failed to parse JSON" 500. So is an empty page, or one that isn't Instagram's app, in place of a profile, post, topic or embed page.

### Ad libraries: 10 / 10 endpoints (12 HTTP operations)

| Library | Endpoints | Notes |
|---|---|---|
| Facebook Ad Library | `/v1/facebook/adLibrary/ad`, `/ad/transcript`, `/search/ads`, `/company/ads`, `/search/companies` | Detail, transcript, keyword/company search, company lookup, filters, and opaque pagination. Search and company ads accept either GET query parameters or a POST JSON object, which is useful for long cursors. `/ad` fills `aaa_info` from Meta's "See ad details" query (`AdLibraryV3AdDetailsQuery`), one extra request made only for ads delivered under EU/UK/Brazil transparency rules (`isAAAEligible`) or in the political category: payer/beneficiary rows plus the EU (`eu_total_reach`), else UK or Brazil (`total_reach`) audience and reach breakdown in absolute counts, or a political ad's delivery as `location_audience` rows with `reach` and an `age_country_gender_reach_breakdown`, both fractional shares (0.08 is 8%). It is `null` for other ads, and when Meta refuses that query (logged) the ad itself is still returned. Only Meta's Ad Library landing page (its answer for an unknown id) is a `404 ad_not_found`; an empty page or one without the Ad Library app, as Meta serves a throttled IP, is a `503 upstream_blocked` on all five routes. |
| TikTok Ad Library | `/v1/tiktok/ad-library/search`, `/ad` | Searches the Commercial Content Library and reads Creative Center Top Ad details, with a library fallback for non-Top Ads. The library is asked as its web app asks on load: `/api/v1/location`, then `/api/v1/support-regions` for the `X-CCL-STR` token, then the search (all regions, last 30 days). A token minted without the location call is refused with HTTP 421 `system busy`, and minting one voids the one before, so a 421 gets one fresh token before it is a `503`. An answer that isn't JSON (an empty body, Akamai's "Access Denied" page) is a `503` too. Library-only details have no engagement, cost or video-size figures: those are null. |
| Google Ad Library | `/v1/google/company/ads`, `/v1/google/ad`, `/v1/google/adLibrary/advertisers/search` | Searches Ads Transparency Center creatives and advertisers and optionally hydrates company results with creative details. Each call is one RPC, plus one fetch per distinct ad preview; a preview that can't be fetched leaves only that variation's copy (`headline`, `description`, `destinationUrl`) `null`. |

Ad-library traffic uses the same ordered proxy/account pools as the other platforms. Configure Facebook and Google with `SOCIALSCRAPE_COOKIES_FACEBOOK` and `SOCIALSCRAPE_COOKIES_GOOGLE`, or add encrypted proxy and cookie entries under **Settings → Proxies & accounts**. Public ad-library surfaces can work anonymously, but suitable egress may still be necessary when Meta, TikTok, or Google rate-limits a server IP.

`/meta/coverage` has the full, current notes for every endpoint.

## Caveats

TikTok web JSON requests use yt-dlp's browser transport, matching its public page requests. On the production
proxy path, the standard Python transport received a "Site Maintenance" HTML page while the same request through
browser transport returned paginated JSON. Instagram and TikTok profile lookups also confirm a missing user through the remaining
configured connections: one egress can report 404 for a public user that another serves. The pool bounds those
checks; with no configured backups a missing user remains final.

- **Approximate values.** Some values are YouTube's display text, not exact figures: relative times (`"3 weeks ago"`) turned into dates, and abbreviated counts (`"2.5K"`). The notes say where.
- **Rate limits.** Every platform throttles, and the ad libraries quickly: Google answers a burst from one IP with its "sorry" CAPTCHA page for a while, and Meta can block an IP after a few dozen Ad Library requests. Use `SOCIALSCRAPE_PROXY` or the per-platform proxy pools (residential works best) and `cache_max_age`. Blocks surface as `503`. After Google refuses an egress (HTTP 429 or a redirect to `google.com/sorry`), the Google routes leave that IP or proxy alone for a cooling window of 60 seconds that doubles, up to 15 minutes, while Google keeps refusing (or Google's own `Retry-After`, if longer): calls inside it answer `503 upstream_blocked` with a `Retry-After` header and no request, so a burst can't keep the flag fresh, and with a Google proxy pool the next proxy is tried. A clean answer ends the window.
- **Cookies.** Optional. They unlock login-gated paths (e.g. Instagram comments via `media/<pk>/comments/`), but those paths were **not verified** here, since only anonymous access was tested. Never use your personal account. A session the platform rejects is skipped and the call answered as without cookies, or with `501 platform_auth_required` (`reason: "account_rejected"`) on a route that needs a login ([Signed-in account health](#signed-in-account-health)); replace the file. The files are never written. Check `/health` `cookie_errors` and `cookie_health` after changing one.
- **TikTok sound API.** `/v2/tiktok/video` (app path), `/profile/region` and `/song*` rely on the app API behind `TikTokSoundIE`, which yt-dlp marks as broken. They work today but could stop without notice.
- **No JS runtime needed.** YouTube stream URLs would need a JS runtime (deno), but no endpoint returns them, so metadata, comments and captions all work without one.
- **Timeouts.** A request that hits `SOCIALSCRAPE_REQUEST_TIMEOUT` answers `500 timeout`, but yt-dlp can't be interrupted: its call keeps a worker thread until it ends. Stopping the server (Ctrl-C included) doesn't wait for those calls: it gives them a second at most. With few `SOCIALSCRAPE_MAX_WORKERS`, calls queued behind a timed-out one share its wait and can time out too; they are dropped, not run late. This mainly happens when `SOCIALSCRAPE_REQUEST_TIMEOUT` <= `SOCIALSCRAPE_SOCKET_TIMEOUT`.
- **AI transcription.** Runs locally (Whisper) and, like ScrapeCreators, refuses media over 120 s unless `allow_long_transcription=true` (that literal value only). A `language` the configured model can't transcribe is a 400 (`*.en` models are English-only). Music, silence and noise usually give null text, but that is best effort: Whisper (mlx-whisper above all) can still invent a short line there, or over the music or silence of a video that has speech. Transcriptions run one at a time, in a thread of their own.

## MCP server

SocialScrape exposes one MCP tool per documented path, including the four local account tools, at `POST /mcp`. The complete tool inventory uses the pinned API snapshot; a listed tool may still return `501`. It implements stateless MCP Streamable HTTP for protocol versions `2025-06-18` and `2025-03-26`; no session ID is required. Configure a client with the same API key used for REST:

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

Every data tool is read-only. Tool calls run through the application in-process, so REST authentication, request history, caching, and configured fallback behavior also apply to MCP. Set `SOCIALSCRAPE_PUBLIC_URL` in production so authentication challenges point at the canonical public origin.

### Managing proxies and accounts

A workspace owner's API key can also manage the per-platform proxy and signed-in account pools. The REST routes are
`GET /v1/admin/platforms`, `POST /v1/admin/platforms/{platform}/proxies` (`{"proxy": "...", "test": true}`),
`POST /v1/admin/platforms/{platform}/accounts` (`{"cookies": "..."}`), `POST .../proxies/{id}/test`,
`POST .../accounts/{id}/check`, `POST .../{proxies|accounts}/{id}/move` (`{"direction": "up|down"}`), and
`DELETE .../{proxies|accounts}/{id}`. Accounts accept a Netscape `cookies.txt`, a browser JSON cookie export, that export
base64-encoded, or a Cookie header. Values travel only in JSON bodies, are encrypted like dashboard entries, are never
returned, and are excluded from request history.

The same owner key unlocks seven MCP tools: `admin_list_platform_settings`, `admin_add_proxy`, `admin_test_proxy`,
`admin_add_account`, `admin_check_account`, `admin_move_platform_setting`, and `admin_remove_platform_setting` (marked
destructive). Member keys, legacy `SOCIALSCRAPE_API_KEYS`, dashboard sessions, and MCP OAuth grants cannot use them; the
OAuth consent only covers data tools. The CLI equivalents are `socialscrape proxies ...` and `socialscrape accounts ...`.

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

For a replacement gate over a real customer cohort, use the read-only runner. Keep the cohort and results outside
the repository; they can contain customer account names and captured responses.

```bash
SOCIALSCRAPE_API_KEY=... uv run python scripts/validate_replacement.py \
  --base-url https://your-service.example --cohort /private/cohort.json \
  --output /private/replacement-run --workers 6 --per-platform 2 \
  --feeds-per-platform 20 --pages-per-feed 3 --instagram-reels
```

The cohort is `{"profiles":[{"platform":"instagram","handle":"example"}],"videos":[{"platform":"youtube",
"url":"https://www.youtube.com/shorts/abcdefghijk","previous_views":123}]}` (all three platforms are accepted).
Every request disables ScrapeCreators fallback and omits response-cache opt-ins. The gate checks dates, identities,
numeric counters, previously positive views becoming zero, and pagination overlaps/cycles. It writes private raw
responses, `results.jsonl` and `summary.json`, and exits nonzero on any failed check, unavailable fixture or hidden
required count. It does not classify a 404 as a deleted account without a separate source/provider control.
`--only-failures /private/previous/results.jsonl` reruns the failed fixtures with the same case IDs.

The code is laid out as follows:

- `socialscrape/` is the shared core: `ytdlp` (async bridge and error mapping), `subtitles`, `transcribe`, `cache`, `cursor` and `registry`.
- `socialscrape/platforms/*.py` holds one auto-discovered module per area.
  - Each module declares its routes with `@sc_get(...)`.
  - Each module lists what it can't serve in `UNSUPPORTED = {path: reason}`.
