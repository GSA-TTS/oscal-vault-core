#!/usr/bin/env python3
"""Report typed-table coverage and reject new unmapped table debt."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

_WORKSPACE = Path.cwd()
_DEFAULT_VAULTS = _WORKSPACE / "examples" / "vault"
# The baseline records the debt of *these* vaults, so it lives with them (the system repository),
# not in the core.
_DEFAULT_BASELINE = _WORKSPACE / "scripts" / "table_coverage_baseline.json"


def _debt_id(relative_sidecar: str, record: dict[str, Any], occurrence: int) -> str:
    identity = json.dumps(
        {
            "sidecar": relative_sidecar,
            "heading": record.get("source_heading"),
            "headers": record.get("headers") or [],
            "occurrence": occurrence,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "table:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def inventory(vaults_root: Path) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    debt: dict[str, dict[str, Any]] = {}
    for sidecar in sorted(vaults_root.glob("*/.snapshots/current/**/*.json")):
        document = json.loads(sidecar.read_text(encoding="utf-8"))
        relative = sidecar.relative_to(vaults_root).as_posix()
        occurrences: Counter[str] = Counter()
        for record in document.get("table_records") or []:
            counts["tables_total"] += 1
            classification = str(record.get("classification") or "opaque")
            disposition = str(record.get("disposition") or "unmapped")
            counts[f"classification:{classification}"] += 1
            counts[f"disposition:{disposition}"] += 1
            counts[f"kind:{record.get('kind') or 'opaque'}"] += 1
            if disposition != "unmapped":
                continue
            signature = json.dumps(
                [record.get("source_heading"), record.get("headers") or []],
                sort_keys=True,
            )
            occurrence = occurrences[signature]
            occurrences[signature] += 1
            stable_id = _debt_id(relative, record, occurrence)
            debt[stable_id] = {
                "sidecar": relative,
                "source_heading": record.get("source_heading"),
                "source_locator": record.get("source_locator"),
                "headers": record.get("headers") or [],
                "classification": classification,
            }
    return {"schema_version": 1, "counts": dict(sorted(counts.items())), "debt": debt}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vaults", type=Path, default=_DEFAULT_VAULTS)
    parser.add_argument("--baseline", type=Path, default=_DEFAULT_BASELINE)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument(
        "--strict", action="store_true", help="Require zero unmapped tables"
    )
    args = parser.parse_args()

    report = inventory(args.vaults.resolve())
    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"Wrote table coverage baseline: {args.baseline}")
        return 0

    current_debt = set(report["debt"])
    print(json.dumps(report["counts"], indent=2, sort_keys=True))
    if args.strict:
        if current_debt:
            print(
                f"FAIL: strict table coverage has {len(current_debt)} unmapped table(s)"
            )
            return 2
        return 0
    if not args.baseline.is_file():
        print(f"FAIL: table coverage baseline is missing: {args.baseline}")
        return 2
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    baseline_debt = set((baseline.get("debt") or {}).keys())
    new_debt = sorted(current_debt - baseline_debt)
    resolved = baseline_debt - current_debt
    if new_debt:
        print(f"FAIL: {len(new_debt)} new unmapped table(s)")
        for stable_id in new_debt[:20]:
            item = report["debt"][stable_id]
            print(f"  {stable_id}: {item['sidecar']} {item['source_heading']!r}")
        return 2
    print(
        f"OK: {len(current_debt)} baselined unmapped table(s); "
        f"{len(resolved)} debt item(s) resolved"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
