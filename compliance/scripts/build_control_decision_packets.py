#!/usr/bin/env python3
"""Build deterministic, cited control decision packets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.lib.control_decision import (  # noqa: E402
    DEFAULT_D3FEND_MAPPING,
    build_packets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssp", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--assessment-reference", type=Path)
    source.add_argument("--workbook", type=Path)
    parser.add_argument("--instruction-ledger", type=Path)
    parser.add_argument(
        "--d3fend-mapping",
        type=Path,
        default=DEFAULT_D3FEND_MAPPING if DEFAULT_D3FEND_MAPPING.exists() else None,
    )
    parser.add_argument("--control", action="append", default=[])
    parser.add_argument("--include-historical-observations", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    packets = build_packets(
        args.ssp,
        args.workbook,
        assessment_reference=args.assessment_reference,
        instruction_ledger=args.instruction_ledger,
        d3fend_mapping=args.d3fend_mapping,
        controls=set(args.control) or None,
        include_historical_observations=args.include_historical_observations,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for packet in packets:
        name = packet["control_id"].lower().replace("(", ".").replace(")", "")
        (args.output_dir / f"{name}.packet.json").write_text(
            json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    summary = {
        "schema_version": 1,
        "packets": len(packets),
        "controls": [packet["control_id"] for packet in packets],
        "d3fend_available": any(
            packet["d3fend"]["status"] == "available" for packet in packets
        ),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
