#!/usr/bin/env python3
"""Diff two CCC tier YAMLs (or a CCC YAML vs. a cloud inheritance model YAML).

Emits a CSV of ADDED / REMOVED / TYPE-CHANGED rows with control IDs normalized
to canonical NIST SP 800-53 Rev. 5 short form.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Sequence

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "compliance").is_dir() and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from compliance.lib.control_identity import canonical_control_id
except ModuleNotFoundError:
    from control_identity import canonical_control_id  # type: ignore[no-redef]


def _norm(cid: str) -> str:
    return canonical_control_id(cid) if cid and cid.strip() else ""


_BLANK_IDS = {"", "-", "n/a", "none"}


def _row_id(row: dict, id_fields: Sequence[str]) -> str:
    """Return the first usable control ID from ``id_fields`` (then ``control_id``)."""
    for name in (*id_fields, "control_id"):
        value = str(row.get(name) or "").strip()
        if value.casefold() not in _BLANK_IDS:
            return _norm(value)
    raise ValueError(f"row has no control ID in {[*id_fields, 'control_id']}: {row}")


def _load(path: Path, id_fields: Sequence[str] = ()) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for row in data.get("controls", []):
        cid = _row_id(row, id_fields)
        if cid in out:
            raise ValueError(f"duplicate canonical control ID in CCC YAML: {cid}")
        out[cid] = row
    return out


def _federal_type(row: dict) -> str:
    return (row.get("federal_type") or row.get("gsa_ccc_control_type") or "").strip()


def _contractor_type(row: dict) -> str:
    return (
        row.get("contractor_type") or row.get("fcs_cloud_control_type") or ""
    ).strip()


def diff(current: dict[str, dict], new: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for cid in sorted(set(new) - set(current)):
        r = new[cid]
        rows.append(
            {
                "control_id": cid,
                "delta": "ADDED",
                "current_federal_type": "",
                "new_federal_type": _federal_type(r),
                "new_contractor_type": _contractor_type(r),
                "name": r.get("name", ""),
                "inheritance_source": r.get("inheritance_source", ""),
            }
        )
    for cid in sorted(set(current) - set(new)):
        r = current[cid]
        rows.append(
            {
                "control_id": cid,
                "delta": "REMOVED",
                "current_federal_type": _federal_type(r),
                "new_federal_type": "",
                "new_contractor_type": "",
                "name": r.get("name", ""),
                "inheritance_source": "",
            }
        )
    for cid in sorted(set(current) & set(new)):
        old = _federal_type(current[cid])
        n = _federal_type(new[cid])
        if old.lower() != n.lower():
            rows.append(
                {
                    "control_id": cid,
                    "delta": "TYPE-CHANGED",
                    "current_federal_type": old,
                    "new_federal_type": n,
                    "new_contractor_type": _contractor_type(new[cid]),
                    "name": new[cid].get("name", ""),
                    "inheritance_source": new[cid].get("inheritance_source", ""),
                }
            )
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--current", required=True, type=Path)
    ap.add_argument("--new", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument(
        "--current-id-field",
        action="append",
        default=[],
        help=(
            "Column holding the current side's control ID, tried in order before "
            "control_id (repeatable). Use when control_id repeats the base control "
            "for each enhancement."
        ),
    )
    ap.add_argument(
        "--new-id-field",
        action="append",
        default=[],
        help="Column holding the new side's control ID (repeatable); see above",
    )
    args = ap.parse_args(argv)

    current = _load(args.current, args.current_id_field)
    new = _load(args.new, args.new_id_field)
    rows = diff(current, new)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "control_id",
                "delta",
                "current_federal_type",
                "new_federal_type",
                "new_contractor_type",
                "name",
                "inheritance_source",
            ],
        )
        w.writeheader()
        w.writerows(rows)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["delta"]] = counts.get(r["delta"], 0) + 1
    summary = " ".join(f"{k}={counts[k]}" for k in sorted(counts))
    print(f"wrote {args.out} ({len(rows)} rows: {summary or 'no deltas'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
