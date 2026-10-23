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

The portal uses the kit’s Inter variable font and OFL license, butterfly monogram,
paired courtyard artwork, and a subset of its Lucide SVGs. The artwork appears only
on authentication screens. The butterfly is a static brand mark, not the animated
agent character. Source assets remain unchanged. No external font or icon service
is required at runtime.

The shell and all page renderers remain in `portal.html`, `portal.css`, and
`portal.js`; authentication and invitation markup remains in `portal.py`. The
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
