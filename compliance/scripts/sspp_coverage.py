#!/usr/bin/env python3
"""Cross-reference an SSPP countermeasures.yaml with a CycloneDX threat model.

Emits a per-control coverage report (``coverage.yaml``) and a CSV of gap
countermeasures (``gaps.csv``) under the SSPP output directory.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "compliance").is_dir() and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from compliance.lib.control_identity import canonical_control_id
except ModuleNotFoundError:
    from control_identity import canonical_control_id  # type: ignore[no-redef]


def norm_id(cid: str) -> str:
    return canonical_control_id(cid) if cid and cid.strip() else ""


def collect_cdx_controls(cdx_path: Path) -> dict[str, list[str]]:
    """Return ``{normalized_ctrl_id: [threat bom-refs, ...]}``."""
    data = json.loads(cdx_path.read_text(encoding="utf-8"))
    by_ctrl: dict[str, list[str]] = defaultdict(list)
    for t in data.get("threats", {}).get("threats", []):
        for prop in t.get("properties", []) or []:
            if prop.get("name") == "ctrl:id":
                by_ctrl[norm_id(prop["value"])].append(t["bom-ref"])
    return dict(by_ctrl)


def load_sspp(countermeasures_yaml: Path) -> dict[str, list[dict]]:
    payload = yaml.safe_load(countermeasures_yaml.read_text(encoding="utf-8"))
    by_ctrl: dict[str, list[dict]] = defaultdict(list)
    for cm in payload.get("countermeasures", []) or []:
        by_ctrl[norm_id(cm["control_id"])].append(cm)
    return dict(by_ctrl)


def build_coverage(
    sspp: dict[str, list[dict]],
    cdx_ctrl: dict[str, list[str]],
    *,
    in_threats_key: str = "in_model_threats",
    threat_refs_key: str = "model_threat_refs",
) -> list[dict]:
    rows = []
    for cid in sorted(set(sspp) | set(cdx_ctrl)):
        responses = sspp.get(cid, [])
        threats = cdx_ctrl.get(cid, [])
        parts = [r.get("part") for r in responses]
        statuses = [r.get("default_status") for r in responses]
        rows.append(
            {
                "control_id": cid,
                "in_sspp": bool(responses),
                in_threats_key: bool(threats),
                "sspp_response_count": len(responses),
                "sspp_parts": [p for p in parts if p],
                "sspp_default_statuses": statuses,
                threat_refs_key: threats,
            }
        )
    return rows


def build_gaps(
    sspp: dict[str, list[dict]],
    cdx_ctrl: dict[str, list[str]],
    *,
    in_threats_key: str = "in_model_threats",
) -> list[dict]:
    gaps = []
    for cid, responses in sspp.items():
        for r in responses:
            if r.get("default_status") == "gap":
                gaps.append(
                    {
                        "control_id": r["control_id"],
                        "part": r.get("part") or "",
                        "reason": "empty-or-placeholder",
                        "inheritance_type": r.get("inheritance_type") or "",
                        "providing_service": r.get("providing_service") or "",
                        in_threats_key: bool(cdx_ctrl.get(cid)),
                        "snippet": (r.get("description") or "")[:120],
                    }
                )
    for cid in sorted(set(cdx_ctrl) - set(sspp)):
        gaps.append(
            {
                "control_id": cid,
                "part": "",
                "reason": "missing-from-sspp",
                "inheritance_type": "",
                "providing_service": "",
                in_threats_key: True,
                "snippet": "",
            }
        )
    return gaps


def main(
    argv: list[str] | None = None,
    *,
    in_threats_key: str = "in_model_threats",
    threat_refs_key: str = "model_threat_refs",
) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("countermeasures", type=Path, help="Path to countermeasures.yaml")
    ap.add_argument("cdx", type=Path, help="Path to vault-projected .cdx.json")
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args(argv)

    out_dir = args.out_dir or args.countermeasures.parent
    sspp = load_sspp(args.countermeasures)
    cdx_ctrl = collect_cdx_controls(args.cdx)

    coverage = build_coverage(
        sspp,
        cdx_ctrl,
        in_threats_key=in_threats_key,
        threat_refs_key=threat_refs_key,
    )
    gaps = build_gaps(sspp, cdx_ctrl, in_threats_key=in_threats_key)

    both = sum(1 for r in coverage if r["in_sspp"] and r[in_threats_key])
    only_sspp = sum(1 for r in coverage if r["in_sspp"] and not r[in_threats_key])
    only_threats = sum(1 for r in coverage if not r["in_sspp"] and r[in_threats_key])

    cov_path = out_dir / "coverage.yaml"
    cov_path.write_text(
        yaml.safe_dump(
            {
                "summary": {
                    "sspp_controls": len(sspp),
                    "threat_controls": len(cdx_ctrl),
                    "in_both": both,
                    "only_in_sspp": only_sspp,
                    "only_in_threats": only_threats,
                    "gap_rows": len(gaps),
                },
                "controls": coverage,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    gaps_path = out_dir / "gaps.csv"
    with gaps_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "control_id",
                "part",
                "reason",
                "inheritance_type",
                "providing_service",
                in_threats_key,
                "snippet",
            ],
        )
        w.writeheader()
        w.writerows(gaps)

    print(
        f"Wrote {cov_path}  (both={both}, only_sspp={only_sspp}, only_threats={only_threats})"
    )
    print(f"Wrote {gaps_path} ({len(gaps)} gap rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
