# Mimic UI integration

SocialScrape’s portal follows the **workspace** profile from
`mimic-brandkit` at `8efb1e39a5fb2cedbecb84e0503dfb38f6f5810c`.
The current neutral workspace contract in that repository’s `DESIGN.md` takes
precedence over its historical violet direction and the separate brand-book palette.

`socialscrape/static/brand/tokens.css` is an unchanged copy of the generated
`public/brand-kit/brand/dashboard-tokens.css`. Update the upstream profile and copy
its output when changing themes; do not hand-edit the vendored generated file.
Components read semantic roles, including `--overlay` for native dialogs and
`--selected` for persistent selection. The theme lives on the document root so
native top-layer surfaces inherit the same roles.

The portal uses the kit’s Inter variable font and OFL license, paired courtyard
artwork, and a subset of its Lucide SVGs. Its own logo is a webpage with data rows
and an extraction arrow. The lockup pairs that mark with **Social Scrape**, with
**by Mimic** on a smaller line below. Authentication, navigation and mobile pages
share this lockup; the collapsed sidebar keeps the icon and an accessible name.
The courtyard artwork appears without a logo overlay, only on authentication
screens. No external font or icon service is required at runtime.

`brand/logomark.svg` and `brand/logomark-chalk.svg` contain the new icon in ink and
white. Their PNG equivalents support email clients. `brand/logo.svg` and
`brand/logo-white.svg` contain the full lockup with outlined Inter lettering;
their transparent PNG exports are 960 × 288 and can display at 240 × 72 in emails.
Use `logo.png` on light backgrounds and `logo-white.png` on dark backgrounds,
with alt text “Social Scrape by Mimic”. Email images are served from
`/assets/brand/` when the portal is enabled. Email templates should use absolute
PNG URLs from the configured public origin, since SVG support varies between
email clients.

`brand/favicon.svg`, `brand/favicon.png` and the 16/32/48px ICO contain only the
white extraction icon on a transparent background. Root favicon routes also
serve the icon without an API key, including when the portal is disabled. The
standalone Swagger and ReDoc pages use it too.

The shell and all page renderers remain in `portal.html`, `portal.css`, and
`portal.js`, except the playground's response preview, which lives in
`preview.js`; authentication and invitation markup remains in `portal.py`. The
FastAPI static mount and Python wheel include the entire asset directory, so the
Docker deployment requires no Node build step. `appearance.js` initializes the
saved theme before rendering and is shared with authentication screens.

Controls are 40px on desktop and at least 44px on touch layouts, with 12px corners
and 16px cards. Mobile navigation and credential/confirmation dialogs use native
modal dialogs for focus containment and Escape dismissal. Reduced-motion settings
remove surface animation. Typing fields show focus through their border, while
navigation, buttons, selects, and other keyboard controls retain visible rings.

The playground derives its choices and fields from the authenticated OpenAPI
schema. Optional parameters are disclosed separately; drafts and the selected
language survive in-app navigation. A workspace session can run requests without
an API key, while exported snippets use an integration credential placeholder.
Responses, usage totals, and request history come from the existing server APIs.
A JSON response opens on a Preview tab that maps each endpoint onto shared views:
a profile (picture, bio, links, every count, details, recent posts), a media card
(engagement tiles, audio, tagged accounts, carousel slides, comments, chapters), item
lists, comments and transcripts. Endpoints without a mapping get a summary of their
top-level fields, and every response ends with a collapsed "All fields" list of each
scalar value, so nothing is reachable only through the raw JSON.
Stats a platform normally reports but this response omits read "Not returned", and
rounded counts read "Approximate", so the preview never invents a number. The Raw
JSON tab re-indents the response text without parsing numbers, so IDs beyond 2^53
keep every digit, and the copy button always copies it.
Proxy settings retain owner permissions and the server’s encrypted storage contract.

The sidebar can collapse to an icon rail. Its state is initialized on the document
root before rendering and saved independently from the appearance theme. Every
icon-only destination retains its accessible name and tooltip. The account trigger
uses one-line identity text and opens the kit's neutral account popup, with theme,
settings, and log-out actions. The mobile drawer owns its own popup instance so its
controls stay inside the active modal's interaction scope.

Instagram, TikTok, and YouTube use the kit's original colored brand artwork in
`brand/platforms/`. One platform-label helper keeps the picker, overview, reference,
settings, route activity, and footer consistent. The picker uses a native popover
with labeled listbox options and keyboard navigation because ordinary native
select options cannot display the brand images consistently. Only the three data
platforms appear in the picker; metadata such as coverage remains outside it.

The server versions shell scripts, styles, and SVG references with a digest of
the UI assets. Authentication pages use the same version. This keeps a returning
browser from combining new markup with cached scripts from an earlier deployment.

The live Stackdome resource owns `scrape.mimic.host` with TLS enabled. Release
published code with `stackdome create release --stack socialscrape-app --wait`.
The Stackfile schema cannot represent `custom_domain`; applying that file would
clear the saved hostname. Preserve the resource's domain, secret connection,
and persistent volume when changing its deployment definition.

Loading follows the kit's `ui-motion-loading` and feedback specimens. The first
server response and client navigation share inert page templates in `loading.html`,
so all eight destinations reserve their own cards, forms, charts, and table rows.
Table headings and the workspace shell stay visible. The header counters use
placeholders until real counts arrive, and only fetching regions are marked busy.
The templates participate in the UI asset digest and ship in the Python wheel.

Refreshes retain existing content. A partial overview keeps successful sections
and retries only failed reads; a failed refresh keeps the last loaded rows with
inline recovery. Read requests time out after 30 seconds rather than leaving an
indefinite placeholder. Route indicators are indeterminate, and a slow-load label
describes the actual wait without inventing progress or delaying a response.

Real content is committed and busy state cleared before a short opacity reveal.
Decorative overlays are inaccessible and pointer-free, with their final opacity
committed before playback. Reduced motion, including a preference change during
playback, removes the overlay and exposes usable content immediately.

The shared pending-button helper keeps each action's width and focus, provides a
spinner and specific label, and guards repeat submissions. Authentication,
credential creation, settings, ordering, confirmations, and playground requests
use it. Failures restore the action and preserve entered values. The playground
keeps the previous response readable while a request runs and retains its actual
pending/result state across in-app navigation; it never reports client navigation
as server cancellation.

Browser startup is a required check after portal merges. A syntactically valid
script can still stop the whole UI if a shared initializer was removed while its
call remained. Smoke-test both the account menu and platform picker in addition
to waiting for every page's busy state to settle.

Account-scoped session cache can fill the header while fresh reads run; those
reads share in-flight promises with the active page. Startup failures expose a
normal reload link even when script initialization has stopped. Stored response
dialogs open immediately with a local skeleton, preserve close-button focus,
offer inline retry, and ignore completions after the dialog closes.

The `/ui-ux-pro-max` review supplements Mimic's visual rules with checks for
keyboard focus, stable pending controls, explicit recovery, responsive layouts,
contrast, and reduced motion. Its generated palette and typography do not replace
Mimic's workspace tokens or original platform artwork.

Endpoint tests (`/app/tests`) lives in `selftest.js` and `selftest.css`, like the Fallback
page’s own files, and renders through the same router with its own loading skeleton. Its
platform filter is a radio group drawn as the kit’s segmented control, every result pairs a
status badge with words, failures are announced politely as they happen, and the run’s one
primary action becomes Stop while tests are running.
