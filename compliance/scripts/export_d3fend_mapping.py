#!/usr/bin/env python3
"""Convert MITRE D3FEND to NIST SP 800-53 Rev. 5 catalog mapping into schema-valid JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.lib.control_decision import export_d3fend_mapping  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "compliance/data/d3fend_nist_rev5_mapping.json",
    )
    args = parser.parse_args()
    result = export_d3fend_mapping(args.workbook, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "source_sha256": result["source_sha256"],
                "techniques": len(result["mappings"]),
                "mapped_controls": sum(
                    len(m["nist_controls"]) for m in result["mappings"]
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
