"""Validate socialscrape/data/doc_examples.json against the catalog, the bundle extract and the contracts.

Usage: .venv/bin/python -I scripts/doc_examples/validate_doc_examples.py . $WORK/bundle_endpoints.json
Imports the repo's own socialscrape.catalog and tests.contract (trusted code); the data file is read as JSON.
Exits 1 on any failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

repo, bundle_file = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(repo))
from socialscrape.catalog import CATALOG  # noqa: E402
from tests.contract import check  # noqa: E402

data_file = repo / "socialscrape" / "data" / "doc_examples.json"
data = json.loads(data_file.read_text(encoding="utf-8"))
ops = data["operations"]
trimmed_paths = {e["path"] for e in json.loads(bundle_file.read_text()) if isinstance(e.get("trimmedResponse"), dict)}
failures: list[str] = []

expected = [f"{m} {e['path']}" for e in CATALOG for m in ["GET"] + [m for m in e.get("methods", []) if m == "POST"]]
if list(ops) != expected:
    failures.append(f"keys: missing {sorted(set(expected) - set(ops))}, extra {sorted(set(ops) - set(expected))}")
if not isinstance(data.get("_source"), str) or "\n" in data["_source"]:
    failures.append("_source is not one line")

params_by_path = {e["path"]: set(e["params"]) for e in CATALOG}
rows = []
for key, op in ops.items():
    method, path = key.split(" ", 1)
    responses = op["responses"]
    want = ["200"] + (["200 (trimmed)"] if path in trimmed_paths else []) + ["404", "403", "500"]
    if list(responses) != want:
        failures.append(f"{key}: response keys {list(responses)}, want {want}")
    for status in ("200", "200 (trimmed)"):
        body = responses.get(status)
        if body is None:
            continue
        if isinstance(body, list):  # a documented bare array, kept as it is
            continue
        if not isinstance(body, dict) or body.get("success") is not True or body.get("credits_charged") != 0 \
                or "credits_remaining" in body or list(body)[:2] != ["success", "credits_charged"]:
            failures.append(f"{key} {status}: envelope {list(body)[:3] if isinstance(body, dict) else type(body)}")
    for status in ("404", "403", "500"):
        err = responses.get(status, {})
        if list(err) != ["success", "error", "message"] or err["success"] is not False \
                or not err["error"] or not err["message"]:
            failures.append(f"{key} {status}: {err}")
    if responses.get("500") != {"success": False, "error": "server_error", "message": "Internal server error"}:
        failures.append(f"{key} 500: {responses.get('500')}")
    unknown = set(op["params"]) - params_by_path[path]
    if unknown:
        failures.append(f"{key}: params not in the catalog: {sorted(unknown)}")
    report = check(responses["200"], path)
    if report.mismatches:
        failures.append(f"{key} 200: {report.mismatches}")
    trimmed = check(responses["200 (trimmed)"], path) if "200 (trimmed)" in responses else None
    if trimmed and trimmed.mismatches:
        failures.append(f"{key} 200 (trimmed): {trimmed.mismatches}")
    size = len(json.dumps(op, separators=(",", ":"), ensure_ascii=False).encode())
    rows.append((key, report, trimmed, size, len(op["params"])))

print(f"{data_file}: {data_file.stat().st_size:,} bytes, {len(ops)} operations")
print(f"{'operation':58} {'params':>6} {'bytes':>9}  200 coverage / extras   trimmed coverage")
for key, report, trimmed, size, n in rows:
    t = f"{trimmed.covered}/{trimmed.total} ({trimmed.coverage:.0%})" if trimmed else "-"
    print(f"{key:58} {n:6} {size:9,}  {report.covered:3}/{report.total:<3} ({report.coverage:4.0%}) / {len(report.extras):3}"
          f"   {t}")
mismatches = sum(len(r.mismatches) for _, r, _, _, _ in rows)
trimmed_mismatches = sum(len(t.mismatches) for _, _, t, _, _ in rows if t)
print(f"200 type mismatches: {mismatches}; trimmed type mismatches: {trimmed_mismatches}")
print("largest:", ", ".join(f"{k} {s / 1e3:.0f} kB" for k, _, _, s, _ in sorted(rows, key=lambda r: -r[3])[:5]))
if failures:
    print(f"\n{len(failures)} FAILURES")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("all checks passed")
