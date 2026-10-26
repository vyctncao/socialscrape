"""Compare a response body with the ScrapeCreators example it imitates.

contracts/<path>.json maps every key path in ScrapeCreators' example response
(`a.b[].c`) to the JSON types seen there. A response *conforms* when every
key path it emits that also appears in the contract has a compatible type.
`null` is always acceptable, and so is any value where the example only had `null`.
Keys the example doesn't have are reported as extras (allowed, but keep them rare).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
ENVELOPE = {"success", "credits_charged", "credits_remaining", "cached", "cached_at"}

_TYPE = {dict: "object", list: "array", str: "string", bool: "boolean", int: "integer", float: "number",
         type(None): "null"}


def _walk(obj: Any, path: str, acc: dict[str, set[str]]) -> None:
    acc.setdefault(path, set()).add(_TYPE[type(obj)])
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk(v, f"{path}.{k}" if path else k, acc)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, f"{path}[]", acc)


def load_contract(sc_path: str) -> dict[str, list[str]]:
    name = sc_path.strip("/").replace("/", "__")
    path = CONTRACTS / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())["fields"]
    from socialscrape.catalog import UPSTREAM_OPERATIONS
    return next(operation["response_fields"] for operation in UPSTREAM_OPERATIONS
                if operation["path"] == sc_path)


@dataclass
class Report:
    mismatches: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    covered: int = 0
    total: int = 0

    @property
    def coverage(self) -> float:
        return self.covered / self.total if self.total else 1.0

    def __str__(self) -> str:
        return (f"coverage {self.covered}/{self.total} ({self.coverage:.0%}); "
                f"{len(self.mismatches)} type mismatches {self.mismatches[:10]}; "
                f"{len(self.extras)} extras {self.extras[:10]}")


def check(body: dict[str, Any], sc_path: str) -> Report:
    contract = load_contract(sc_path)
    seen: dict[str, set[str]] = {}
    _walk(body, "", seen)
    seen.pop("", None)
    report = Report()
    for path, types in sorted(seen.items()):
        if path.split(".")[0].split("[")[0] in ENVELOPE and "." not in path:
            continue
        expected = contract.get(path)
        if expected is None:
            if not any(path.startswith(e + ".") or path.startswith(e + "[") for e in report.extras):
                report.extras.append(path)
            continue
        if expected == ["null"]:
            continue
        for t in types - {"null"}:
            if t in expected or (t == "integer" and "number" in expected):
                continue
            report.mismatches.append(f"{path}: got {t}, expected {'/'.join(expected)}")
    leaves = [p for p, t in contract.items() if not ({"object", "array"} & set(t))]
    report.total = len(leaves)
    report.covered = sum(1 for p in leaves if p in seen)
    return report


def assert_conforms(body: dict[str, Any], sc_path: str, *, min_coverage: float = 0.0) -> Report:
    assert body.get("success") is True, body
    report = check(body, sc_path)
    assert not report.mismatches, str(report)
    assert report.coverage >= min_coverage, str(report)
    return report
