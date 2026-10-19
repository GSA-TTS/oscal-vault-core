#!/usr/bin/env python3
"""Convert a guidance workbook into normalized decision-reference JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.lib.control_decision import export_assessment_reference  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--reference-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-historical-observations", action="store_true")
    args = parser.parse_args()
    result = export_assessment_reference(
        args.workbook,
        args.reference_id,
        include_historical_observations=args.include_historical_observations,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "reference_id": result["reference_id"],
                "controls": len(result["controls"]),
                "objectives": sum(
                    len(control["objectives"])
                    for control in result["controls"].values()
                ),
                "historical_observations_included": result["source"][
                    "historical_observations_included"
                ],
                "redactions": result["source"]["redactions"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
