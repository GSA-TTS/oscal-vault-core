#!/usr/bin/env python3
"""Crosswalk a system's CDX + CRM index against a GSA Control Tailoring Workbook (.ods).

Emits ``build/<slug>-tailoring-crosswalk.csv`` with per-control alignment between
the workbook's ``Control Tailoring`` + ``SSPP`` sheets and the system's
``data/systems/<slug>/crm/index.yaml`` + ``build/<slug>-crm-threat-model.cdx.json``.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "compliance").is_dir() and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_REPO_CORE = Path(__file__).resolve().parents[3] / "vendor" / "oscal-vault-core"
if (_REPO_CORE / "compliance").is_dir() and str(_REPO_CORE) not in sys.path:
    sys.path.insert(0, str(_REPO_CORE))

try:
    from compliance.lib.control_identity import canonical_control_id
except ModuleNotFoundError:
    from control_identity import canonical_control_id  # type: ignore[no-redef]

CID_RX = re.compile(r"^[A-Z]{2}-\d+(?:\([0-9a-z]+\))?$", re.IGNORECASE)


def normalize(cid: str) -> str:
    """Normalize workbook IDs like ``AT-03`` or ``SC-07(03)`` to ``AT-3`` / ``SC-7(3)``."""
    return canonical_control_id(cid) if cid and cid.strip() else ""


def _read_sheet(ods_path: Path, name: str) -> list[list[str]]:
    from compliance.lib.gsa_workbooks import Workbook

    wb = Workbook(ods_path)
    return wb.rows(name)


def parse_control_tailoring(
    ods_path: Path, *, rows: list[list[str]] | None = None
) -> dict[str, dict]:
    sheet_rows = (
        rows if rows is not None else _read_sheet(ods_path, "Control Tailoring")
    )
    out: dict[str, dict] = {}
    for r in sheet_rows[2:]:
        if not r or not r[0] or not CID_RX.match(r[0].strip()):
            continue
        raw = r[0].strip()
        out[normalize(raw)] = {
            "raw": raw,
            "name": r[1].strip() if len(r) > 1 else "",
            "moderate": (r[2].strip().upper() == "X") if len(r) > 2 else False,
            "low": (r[3].strip().upper() == "X") if len(r) > 3 else False,
            "inheritance": r[4].strip() if len(r) > 4 else "",
            "providing_service": r[5].strip() if len(r) > 5 else "",
            "applicability": r[6].strip() if len(r) > 6 else "",
            "ebta": r[7].strip() if len(r) > 7 else "",
            "tenant_notes": r[8].strip() if len(r) > 8 else "",
        }
    return out


def parse_sspp(
    ods_path: Path, *, rows: list[list[str]] | None = None
) -> dict[str, dict]:
    sheet_rows = rows if rows is not None else _read_sheet(ods_path, "SSPP")
    out: dict[str, dict] = {}
    for r in sheet_rows[1:]:
        if not r or not r[0] or not CID_RX.match(r[0].strip()):
            continue
        raw = r[0].strip()
        out[normalize(raw)] = {
            "raw": raw,
            "name": r[1].strip() if len(r) > 1 else "",
            "moderate": (r[2].strip().upper() == "X") if len(r) > 2 else False,
            "inheritance": r[3].strip() if len(r) > 3 else "",
            "providing_service": r[4].strip() if len(r) > 4 else "",
        }
    return out


def main(argv: list[str] | None = None, *, repo_root: Path | None = None) -> int:
    root = repo_root or Path.cwd()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--system", required=True, help="system slug")
    p.add_argument(
        "--workbook", required=True, type=Path, help="path to the .ods workbook"
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output CSV (default: build/<slug>-tailoring-crosswalk.csv)",
    )
    args = p.parse_args(argv)

    slug = args.system
    idx_path = root / "data" / "systems" / slug / "crm" / "index.yaml"
    cdx_path = root / "build" / f"{slug}-crm-threat-model.cdx.json"
    out_path = args.out or root / "build" / f"{slug}-tailoring-crosswalk.csv"

    if not idx_path.is_file():
        sys.exit(f"missing {idx_path}")
    if not args.workbook.is_file():
        sys.exit(f"missing workbook {args.workbook}")

    idx = yaml.safe_load(idx_path.read_text(encoding="utf-8"))
    sys_meta = {c["control_id"]: c for c in idx["controls"]}
    sys_ids = set(sys_meta)

    ct = parse_control_tailoring(args.workbook)
    sspp = parse_sspp(args.workbook)

    cid_to_status: dict[str, str] = {}
    if cdx_path.is_file():
        cdx = json.loads(cdx_path.read_text(encoding="utf-8"))
        for c in cdx.get("controls", []):
            for prop in c.get("properties", []):
                if prop.get("name") == "ctrl:id":
                    cid_to_status.setdefault(prop["value"], c.get("status", ""))
                    break

    tenant_owned = {
        c
        for c, v in ct.items()
        if v["moderate"] and v["inheritance"].lower() in ("hybrid", "system-specific")
    }
    ss_only = {
        c
        for c, v in ct.items()
        if v["moderate"] and v["inheritance"].lower() == "system-specific"
    }

    def _sort_key(cid: str) -> tuple[str, int, str]:
        m = re.match(r"([A-Z]+)-(\d+)", cid)
        return (m.group(1), int(m.group(2)), cid) if m else ("ZZ", 0, cid)

    all_ids = sorted(sys_ids | set(ct), key=_sort_key)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "control_id",
                "in_system",
                "system_sources",
                "cdx_status",
                "in_ct",
                "ct_raw_id",
                "ct_name",
                "ct_moderate",
                "ct_low",
                "ct_inheritance",
                "ct_providing_service",
                "ct_ebta",
                "in_sspp",
                "sspp_inheritance",
                "sspp_providing_service",
                "gap",
            ]
        )
        for cid in all_ids:
            in_sys = cid in sys_ids
            c = ct.get(cid, {})
            s = sspp.get(cid, {})
            gap: list[str] = []
            if in_sys and cid not in ct:
                gap.append("system-only")
            if cid in tenant_owned and not in_sys:
                gap.append("workbook-tenant-not-in-system")
            if in_sys and c.get("inheritance", "").lower() == "fully inherited":
                gap.append("system-covers-fully-inherited")
            w.writerow(
                [
                    cid,
                    in_sys,
                    ",".join(sys_meta.get(cid, {}).get("sources") or [])
                    if in_sys
                    else "",
                    cid_to_status.get(cid, ""),
                    cid in ct,
                    c.get("raw", ""),
                    c.get("name", ""),
                    c.get("moderate", ""),
                    c.get("low", ""),
                    c.get("inheritance", ""),
                    c.get("providing_service", ""),
                    c.get("ebta", ""),
                    cid in sspp,
                    s.get("inheritance", ""),
                    s.get("providing_service", ""),
                    ";".join(gap),
                ]
            )

    print(f"Wrote {out_path}  (rows: {len(all_ids)})")
    print(f"  system controls:                    {len(sys_ids)}")
    print(f"  workbook 'Control Tailoring':       {len(ct)}")
    print(f"  workbook 'SSPP':                    {len(sspp)}")
    print(f"  system \\ workbook:                  {len(sys_ids - set(ct))}")
    print(
        f"  workbook tenant-owned Moderate \\ system: "
        f"{len(tenant_owned - sys_ids)} (of {len(tenant_owned)})"
    )
    print(
        f"  workbook System-Specific-only Moderate \\ system: "
        f"{len(ss_only - sys_ids)} (of {len(ss_only)})"
    )
    ebta_missing = [c for c in (tenant_owned - sys_ids) if ct[c]["ebta"].upper() == "X"]
    if ebta_missing:
        print(f"  EBTA-flagged missing:               {sorted(ebta_missing)}")

    by_family = Counter(c.split("-")[0] for c in (tenant_owned - sys_ids))
    if by_family:
        print("  Missing tenant-owned by family:")
        for fam, n in by_family.most_common():
            print(f"    {fam}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
