#!/usr/bin/env python3
"""Aggregate progressive coverage debt and reject new stable debt identifiers."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.scripts.table_coverage import inventory as table_inventory  # noqa: E402

_DEFAULT_BASELINE = (
    _REPO_ROOT / "compliance" / "data" / "progressive_coverage_baseline.json"
)
_VAULT_BASELINE = _REPO_ROOT / "scripts" / "vault_debt_baseline.json"


def _stable_id(category: str, value: str) -> str:
    digest = hashlib.sha256(f"{category}\0{value}".encode("utf-8")).hexdigest()[:20]
    return f"{category}:{digest}"


def build_report(vaults: Path) -> dict[str, Any]:
    tables = table_inventory(vaults)
    structural = json.loads(_VAULT_BASELINE.read_text(encoding="utf-8"))
    debt: dict[str, dict[str, str]] = {}
    for stable_id, item in tables["debt"].items():
        debt[stable_id] = {
            "category": "unmapped_table",
            "detail": json.dumps(item, sort_keys=True),
        }
    for category, values in structural.items():
        for value in values:
            debt[_stable_id(category, value)] = {"category": category, "detail": value}
    counts = {
        "table_records": tables["counts"],
        "structural": {key: len(value) for key, value in structural.items()},
        "debt_total": len(debt),
    }
    return {"schema_version": 1, "counts": counts, "debt": debt}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vaults", type=Path, default=_REPO_ROOT / "examples" / "vault"
    )
    parser.add_argument("--baseline", type=Path, default=_DEFAULT_BASELINE)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument(
        "--strict", action="store_true", help="Require no aggregate debt"
    )
    parser.add_argument("--json", type=Path, help="Write the current report")
    args = parser.parse_args()
    report = build_report(args.vaults.resolve())
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"Wrote progressive coverage baseline: {args.baseline}")
        return 0
    current = set(report["debt"])
    if args.strict:
        if current:
            print(f"FAIL: strict progressive coverage has {len(current)} debt item(s)")
            return 2
        return 0
    if not args.baseline.is_file():
        print(f"FAIL: progressive coverage baseline is missing: {args.baseline}")
        return 2
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    expected = set((baseline.get("debt") or {}).keys())
    new = sorted(current - expected)
    if new:
        print(f"FAIL: {len(new)} new progressive coverage debt item(s)")
        for stable_id in new[:20]:
            print(f"  {stable_id}: {report['debt'][stable_id]['detail']}")
        return 2
    print(json.dumps(report["counts"], indent=2, sort_keys=True))
    print(
        f"OK: {len(current)} baselined debt item(s); {len(expected - current)} resolved"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
