"""Build socialscrape/data/doc_examples.json from the fetched ScrapeCreators docs pages and bundle extract.

Usage (needs PyYAML, so use the repo venv, with -I; README.md has the whole sequence):
  .venv/bin/python -I scripts/doc_examples/build_doc_examples.py \
      --catalog $WORK/catalog.json --pages $WORK/raw/pages --bundle $WORK/bundle_endpoints.json \
      --spec $WORK/openapi.json --out socialscrape/data/doc_examples.json --report $WORK/build_report.json

Sources, per operation:
  params          the docs page's (.md twin) OpenAPI YAML parameter examples, typed by their schema (the YAML has
                  every example as a string; the docs renderer and openapi.json type them). POST pages document
                  the body without examples; the docs render the GET page's examples as the body, so POST reuses
                  them (checked against the POST pages' HTML). Falls back to openapi-latest.json per param.
  200             the page's YAML 200 example (a block string holding JSON); openapi-latest.json if missing.
  200 (trimmed)   the bundle's trimmedResponse for the path, when it has one (the bundle lists GET only; the POST
                  pages show the same tabs).
  404/403/500     this server's error envelope. Messages: the bundle's endpoint-specific errorResponseExamples,
                  else this server's own message for the route, else a platform-specific one (MESSAGES below).
"""

from __future__ import annotations

import argparse
import ast
import html
import json
import re
from pathlib import Path

import yaml

ENVELOPE = ("success", "credits_remaining", "credits_charged")
SERVER_500 = {"success": False, "error": "server_error", "message": "Internal server error"}  # errors.unhandled_error_handler
AGE_RESTRICTED = "This video is age-restricted; YouTube only shows it to signed-in, age-verified users"

# path -> (404 message, 403 message). The server's own message where the route raises a fixed one (see
# socialscrape/platforms), otherwise one written for the endpoint. Overridden per status by ScrapeCreators'
# endpoint-specific errorResponseExamples.
MESSAGES: dict[str, tuple[str, str]] = {
    "/v1/instagram/audio/reels": ("Instagram audio not found", "This audio is not available on Instagram"),
    "/v1/instagram/basic-profile": ("Profile not found", "This account is private"),
    "/v1/instagram/post": ("Post not found", "This post is from a private account"),
    "/v1/instagram/post/comment/replies": ("Comment not found", "This post is from a private account"),
    "/v1/instagram/profile": ("Profile not found", "This account is private"),
    "/v1/instagram/profile/post-count": ("Profile not found", "This account is private"),
    "/v1/instagram/reels/trending": ("Instagram has no trending reels right now", "Instagram did not allow access to trending reels"),
    "/v1/instagram/search": ("No Instagram results for this query", "Instagram did not allow this search"),
    "/v1/instagram/search/hashtag": ("Hashtag not found", "Instagram restricts this hashtag"),
    "/v1/instagram/search/popular": ("Instagram has no popular page for this topic", "Instagram restricts this topic"),
    "/v1/instagram/search/profiles": ("No Instagram profiles match this query", "Instagram did not allow this search"),
    "/v1/instagram/user/embed": ("Instagram has no embeddable profile at this name (unknown, private or restricted)",
                                 "This account is private"),
    "/v1/instagram/user/highlight/detail": ("Highlight not found (or not visible without login)", "This account is private"),
    "/v1/instagram/user/highlights": ("Profile not found", "This account is private"),
    "/v1/instagram/user/reels": ("Profile not found", "This account is private"),
    "/v1/instagram/user/tagged-posts": ("Profile not found", "This account is private"),
    "/v2/instagram/media/transcript": ("Post not found", "This post is from a private account"),
    "/v2/instagram/post/comments": ("Post not found", "This post is from a private account"),
    "/v2/instagram/reels/search": ("No reels match this query", "Instagram did not allow this search"),
    "/v2/instagram/user/posts": ("Profile not found", "This account is private"),
    "/v1/tiktok/ad-library/ad": ("TikTok ad not found", "This TikTok ad is not available"),
    "/v1/tiktok/ad-library/search": ("No TikTok ads match this search", "TikTok's ad library did not allow this search"),
    "/v1/tiktok/collection/videos": ("TikTok collection not available (no collection has this id)", "This collection is private"),
    "/v1/tiktok/creators/popular": ("No popular creators match these filters", "TikTok Creative Center did not allow this request"),
    "/v1/tiktok/get-trending-feed": ("No trending videos for this region", "TikTok's trending feed is not available in this region"),
    "/v1/tiktok/live": ("TikTok live room not found", "This live room is not available"),
    "/v1/tiktok/product": ("TikTok Shop product not found", "This product is not available in this region"),
    "/v1/tiktok/profile": ("TikTok user not found", "This account is private"),
    "/v1/tiktok/profile/region": ("TikTok user not found", "This account is private"),
    "/v1/tiktok/search/hashtag": ("Hashtag not found", "TikTok restricts this hashtag"),
    "/v1/tiktok/search/keyword": ("No TikTok videos match this keyword", "TikTok did not allow this search"),
    "/v1/tiktok/search/suggestions": ("No search suggestions for this query", "TikTok did not allow this search"),
    "/v1/tiktok/search/top": ("No TikTok results for this query", "TikTok did not allow this search"),
    "/v1/tiktok/search/users": ("No TikTok users match this query", "TikTok did not allow this search"),
    "/v1/tiktok/shop/product/reviews": ("TikTok Shop product not found", "Reviews for this product are not available"),
    "/v1/tiktok/shop/products": ("TikTok Shop not found", "This shop is not available in this region"),
    "/v1/tiktok/shop/search": ("No TikTok Shop products match this query", "TikTok Shop is not available in this region"),
    "/v1/tiktok/song": ("TikTok sound not found", "This sound is not available"),
    "/v1/tiktok/song/videos": ("TikTok sound not found or has no videos", "This sound is not available"),
    "/v1/tiktok/user/audience": ("TikTok user not found", "This account is private"),
    "/v1/tiktok/user/followers": ("TikTok user not found", "This account's followers list is private"),
    "/v1/tiktok/user/following": ("TikTok user not found", "This account's following list is private"),
    "/v1/tiktok/user/live": ("TikTok user not found", "This account is private"),
    "/v1/tiktok/user/showcase": ("TikTok user not found", "This account's showcase is not available"),
    "/v1/tiktok/video/comment/replies": ("Comment not found", "This post is private"),
    "/v1/tiktok/video/comments": ("TikTok post not available", "This post is private"),
    "/v1/tiktok/video/transcript": ("TikTok post not available", "This post is private"),
    "/v2/tiktok/video": ("TikTok post not available", "This post is private"),
    "/v3/tiktok/profile/videos": ("TikTok user not found", "This account is private"),
    "/v1/youtube/channel": ("Channel not found", "This channel is not available"),
    "/v1/youtube/channel-videos": ("Channel not found", "This channel is not available"),
    "/v1/youtube/channel/community-posts": ("Channel not found", "This channel is not available"),
    "/v1/youtube/channel/lives": ("Channel not found", "This channel is not available"),
    "/v1/youtube/channel/playlists": ("Channel not found", "This channel is not available"),
    "/v1/youtube/channel/shorts": ("Channel not found", "This channel is not available"),
    "/v1/youtube/community-post": ("Community post not found", "This post is only available to channel members"),
    "/v1/youtube/playlist": ("Playlist not found", "This playlist is private"),
    "/v1/youtube/search": ("No YouTube results for this query", "YouTube did not allow this search"),
    "/v1/youtube/search/hashtag": ("Hashtag not found", "YouTube restricts this hashtag"),
    "/v1/youtube/search/typeahead": ("No suggestions for this query", "YouTube did not allow this search"),
    "/v1/youtube/shorts/trending": ("No trending shorts right now", "YouTube's trending shorts are not available"),
    "/v1/youtube/video": ("Video unavailable", AGE_RESTRICTED),
    "/v1/youtube/video/comment/replies": ("Comment not found", "Comments are turned off for this video"),
    "/v1/youtube/video/comments": ("Video unavailable", "Comments are turned off for this video"),
    "/v1/youtube/video/sponsors": ("Video unavailable", "This video is private"),
    "/v1/youtube/video/transcript": ("Video unavailable", AGE_RESTRICTED),
    "/v1/facebook/adLibrary/ad": ("Facebook ad was not found", "This ad is not available in the Ad Library"),
    "/v1/facebook/adLibrary/ad/transcript": ("Facebook ad was not found", "This ad is not available in the Ad Library"),
    "/v1/facebook/adLibrary/search/ads": ("No ads match this search", "Meta's Ad Library did not allow this search"),
    "/v1/facebook/adLibrary/company/ads": ("Facebook company was not found",
                                           "This company's ads are not available in the Ad Library"),
    "/v1/facebook/adLibrary/search/companies": ("No companies match this query", "Meta's Ad Library did not allow this search"),
    "/v1/google/company/ads": ("Google advertiser not found", "This advertiser's ads are not available in this region"),
    "/v1/google/ad": ("Google ad not found", "This ad is not available in this region"),
    "/v1/google/adLibrary/advertisers/search": ("No advertisers match this query",
                                                "Google's Ads Transparency Center did not allow this search"),
}

# (path, status) -> error code, where the route raises a code other than not_found/forbidden for that case.
CODES: dict[tuple[str, str], str] = {
    ("/v1/facebook/adLibrary/ad", "404"): "ad_not_found",
    ("/v1/facebook/adLibrary/ad/transcript", "404"): "ad_not_found",
    ("/v1/facebook/adLibrary/company/ads", "404"): "company_not_found",
    ("/v1/youtube/video", "403"): "age_restricted",
    ("/v1/youtube/video/transcript", "403"): "age_restricted",
}
DEFAULT_CODES = {"404": "not_found", "403": "forbidden"}
# Paths added after MESSAGES was written (the full inventory): "<Platform> <thing> not found", and ScrapeCreators'
# default 403 text.
_NAMES = {"apple-music": "Apple Music", "github": "GitHub", "linkedin": "LinkedIn", "soundcloud": "SoundCloud",
          "truthsocial": "Truth Social", "youtube": "YouTube", "tiktok": "TikTok", "linkbio": "Link in bio",
          "linkme": "Linkme", "linktree": "Linktree", "find-creator-emails": "Creator email",
          "find-social-profiles": "Social profile", "detect-age-gender": "Face"}
_THINGS = {"profile": "profile", "post": "post", "user": "user", "channel": "channel", "video": "video", "clip": "clip",
           "track": "track", "album": "album", "artist": "artist", "playlist": "playlist", "podcast": "podcast",
           "board": "board", "pin": "pin", "repository": "repository", "company": "company", "community": "community",
           "tweet": "tweet", "user-tweets": "user", "subreddit": "subreddit", "group": "group", "event": "event",
           "events": "event", "marketplace": "listing", "cluster": "cluster", "spotlight": "spotlight",
           "shop": "product", "ad": "ad", "ads": "advertiser", "search": "page", "trending": "page"}


def fallback_messages(path: str) -> tuple[str, str]:
    parts = path.strip("/").split("/")[1:]
    platform = parts[0]
    name = _NAMES.get(platform, platform.replace("-", " ").title())
    thing = _THINGS.get(parts[1], parts[1].replace("-", " ")) if len(parts) > 1 else "page"
    if platform in ("find-creator-emails", "find-social-profiles", "detect-age-gender"):
        return f"{name} not found", "This public resource is not available"
    return f"{name} {thing} not found", "This public resource is not available"


def load_catalog(path: Path) -> list[dict]:
    if str(path).endswith(".json"):  # the runtime catalog, dumped by the repo's own code
        return json.loads(Path(path).read_text())
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "CATALOG":
            return ast.literal_eval(node.value)
    raise SystemExit("CATALOG not found")


def page_operation(md_file: Path, method: str, path: str) -> dict:
    """The operation object from the page's embedded OpenAPI YAML."""
    text = md_file.read_text(encoding="utf-8")
    blocks = re.findall(r"^````yaml ([^\n]*)\n(.*?)^````", text, re.S | re.M)
    if len(blocks) != 1:
        raise ValueError(f"{md_file.name}: {len(blocks)} YAML blocks")
    return yaml.safe_load(blocks[0][1])["paths"][path][method.lower()]


def typed(value, schema_type: str | None):
    """An example as the spec types it: the YAML writes 'false' and '10' for a boolean and a number."""
    if not isinstance(value, str):
        return value
    if schema_type == "boolean" and value in ("true", "false"):
        return value == "true"
    if schema_type in ("number", "integer") and re.fullmatch(r"-?\d+(\.\d+)?", value):
        return float(value) if "." in value else int(value)
    return value


def example_200(op: dict):
    content = op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {})
    ex = content.get("example")
    return json.loads(ex) if isinstance(ex, str) else ex


def ours(sample: dict | list) -> dict | list:
    """ScrapeCreators' sample in this server's envelope (registry.envelope); a bare array stays as it is."""
    if isinstance(sample, list):
        return sample
    return {"success": True, "credits_charged": 0, **{k: v for k, v in sample.items() if k not in ENVELOPE}}


def rendered_post_body(html_file: Path) -> dict | None:
    """The JSON body the POST page's cURL snippet sends."""
    if not html_file.exists():
        return None
    m = re.search(r"--data '(\{.*?\})'</code>", html.unescape(html_file.read_text(encoding="utf-8")), re.S)
    return json.loads(m.group(1)) if m else None


def main() -> None:
    ap = argparse.ArgumentParser()
    for name in ("catalog", "pages", "bundle", "spec", "out", "report"):
        ap.add_argument(f"--{name}", required=True, type=Path)
    args = ap.parse_args()

    catalog = load_catalog(args.catalog)
    manifest = json.loads((args.pages / "manifest.json").read_text())
    bundle = {(e["method"], e["path"]): e for e in json.loads(args.bundle.read_text())}
    spec = json.loads(args.spec.read_text())["paths"]
    report: dict[str, dict] = {}
    operations: dict[str, dict] = {}

    for entry in catalog:
        path, allowed = entry["path"], set(entry["params"])
        get_page = manifest.get(f"GET {path}", {})
        get_op = page_operation(args.pages / get_page["file"], "GET", path) if get_page.get("file") else None
        spec_params = {p["name"]: p for p in spec.get(path, {}).get("get", {}).get("parameters", [])}
        sc = bundle.get(("GET", path), {})
        for method in ["GET"] + [m for m in entry.get("methods", []) if m == "POST"]:
            key = f"{method} {path}"
            notes = report.setdefault(key, {"sources": {}, "gaps": []})
            page = manifest.get(key, {})
            op = page_operation(args.pages / page["file"], method, path) if page.get("file") else None

            # params: the GET page's examples (the POST page renders those as its body)
            params, sources = {}, set()
            documented = get_op.get("parameters", []) if get_op else list(spec_params.values())
            for p in documented:
                name = p["name"]
                if "example" in p:
                    value, src = p["example"], "page" if get_op else "openapi-latest.json"
                elif "example" in spec_params.get(name, {}):
                    value, src = spec_params[name]["example"], "openapi-latest.json"
                else:
                    notes["gaps"].append(f"no example for param {name}")
                    continue
                if name not in allowed:
                    notes["gaps"].append(f"dropped example for {name}: not a catalog param")
                    continue
                params[name] = typed(value, p.get("schema", {}).get("type"))
                sources.add(src)
            notes["gaps"] += [f"catalog param {n} not documented on the page" for n in entry["params"]
                              if n not in {p["name"] for p in documented}]
            notes["sources"]["params"] = "GET page YAML" if sources == {"page"} else ", ".join(sorted(sources)) or "none"
            if method == "POST":
                notes["sources"]["params"] += " (as the POST page renders its body)"
                body = rendered_post_body(args.pages / f"POST_{path.strip('/').replace('/', '_')}.html")
                notes["post_body_matches_html"] = body == params if body is not None else None

            sample = example_200(op) if op else None
            if sample is not None:
                notes["sources"]["200"] = f"{method} page YAML"
            else:
                sample = spec.get(path, {}).get(method.lower(), {}).get("responses", {}).get("200", {}) \
                    .get("content", {}).get("application/json", {}).get("example")
                notes["sources"]["200"] = "openapi-latest.json"
                notes["gaps"].append("no 200 example on the page")
            if isinstance(sample, list):  # a few unimplemented routes document a bare array: shown as it is
                notes["gaps"].append("200 example is a JSON array, kept without an envelope")
            elif not isinstance(sample, dict):
                raise SystemExit(f"{key}: 200 example is {type(sample).__name__}")
            notes["matches_bundle_sample"] = sample == sc.get("sampleResponse") if isinstance(sample, list) else (
                {k: v for k, v in sample.items() if k not in ENVELOPE}
                == {k: v for k, v in (sc.get("sampleResponse") or {}).items() if k not in ENVELOPE})
            responses = {"200": ours(sample)}
            if isinstance(sc.get("trimmedResponse"), dict):
                responses["200 (trimmed)"] = ours(sc["trimmedResponse"])
                notes["sources"]["200 (trimmed)"] = "bundle trimmedResponse"

            messages = dict(zip(("404", "403"), MESSAGES.get(path) or fallback_messages(path)))
            for status in ("404", "403"):
                sc_error = (sc.get("errorResponseExamples") or {}).get(status) or {}
                message = sc_error.get("message") or messages[status]
                notes["sources"][status] = "bundle errorResponseExamples message" if sc_error.get("message") \
                    else "server/endpoint message"
                code = CODES.get((path, status), DEFAULT_CODES[status])
                responses[status] = {"success": False, "error": code, "message": message}
            responses["500"] = dict(SERVER_500)
            operations[key] = {"params": params, "responses": responses}

    data = {
        "_source": ("ScrapeCreators docs (docs.scrapecreators.com), fetched 2026-10-25: params and 200 from each "
                    "page's .md twin (POST bodies are the GET examples, as its pages render them), 200 (trimmed) "
                    "and endpoint error messages from its docs bundle; envelopes are this server's (success true, "
                    "credits_charged 0, no credits_remaining; errors {success, error, message} with its codes)"),
        "operations": operations,
    }
    args.out.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=1))
    print(f"wrote {args.out} ({args.out.stat().st_size:,} bytes, {len(operations)} operations)")


if __name__ == "__main__":
    main()
