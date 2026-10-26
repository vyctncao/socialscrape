"""Fetch the markdown twin of every ScrapeCreators docs page this server serves, sequentially.

Usage: python3 -I scripts/doc_examples/fetch_pages.py $WORK/catalog.json $WORK/raw
Writes <raw dir>/pages/<op slug>.md and <raw dir>/pages/manifest.json ({op: {url, status, file}}).
GET /a/b -> https://docs.scrapecreators.com/a/b.md, POST /a/b -> .../a/b/post.md.
Also saves the two POST pages' HTML (to confirm the rendered request body).
"""

import ast
import json
import sys
import subprocess
import time
from pathlib import Path

BASE = "https://docs.scrapecreators.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) socialscrape-docs-sync"


def load_catalog(path: str) -> list[dict]:
    if str(path).endswith(".json"):  # the runtime catalog, dumped by the repo's own code
        return json.loads(Path(path).read_text())
    for node in ast.parse(Path(path).read_text()).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "CATALOG":
            return ast.literal_eval(node.value)
    raise SystemExit("CATALOG not found")


def operations(catalog: list[dict]) -> list[tuple[str, str]]:
    ops = []
    for e in catalog:
        ops.append(("GET", e["path"]))
        if "POST" in e.get("methods", []):
            ops.append(("POST", e["path"]))
    return ops


def page_path(method: str, path: str) -> str:
    return path + ("/post" if method == "POST" else "")


def slug(method: str, path: str) -> str:
    return f"{method}_{path.strip('/').replace('/', '_')}"


def get(url: str) -> tuple[int, bytes]:
    """curl, not urllib: this machine's python.org build has no CA bundle."""
    r = subprocess.run(["curl", "-sSL", "-A", UA, "--max-time", "30", "-w", "\n%{http_code}", url],
                       capture_output=True, check=True)
    body, _, code = r.stdout.rpartition(b"\n")
    return int(code), body if int(code) == 200 else b""


def main() -> None:
    catalog_path, raw = sys.argv[1], Path(sys.argv[2])
    out = raw / "pages"
    out.mkdir(parents=True, exist_ok=True)
    manifest_file = out / "manifest.json"
    manifest = json.loads(manifest_file.read_text()) if manifest_file.exists() else {}
    for method, path in operations(load_catalog(catalog_path)):
        key = f"{method} {path}"
        if manifest.get(key, {}).get("status") == 200:
            continue  # already have it; delete manifest.json to refetch everything
        url = f"{BASE}{page_path(method, path)}.md"
        status, body = get(url)
        name = slug(method, path) + ".md"
        if status == 200:
            (out / name).write_bytes(body)
        manifest[key] = {"url": url, "status": status, "file": name if status == 200 else None}
        print(status, len(body), url, flush=True)
        manifest_file.write_text(json.dumps(manifest, indent=1))
        time.sleep(0.8)
        if method == "POST":
            html_url = f"{BASE}{page_path(method, path)}"
            status, body = get(html_url)
            if status == 200:
                (out / (slug(method, path) + ".html")).write_bytes(body)
            print(status, len(body), html_url, flush=True)
            time.sleep(0.8)


if __name__ == "__main__":
    main()
