#!/usr/bin/env python3
"""Generate and validate complete active OSCAL mapping capabilities."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compliance.lib.oscal_lineage import (  # noqa: E402
    mapping_lineage_report,
    validate_lineage_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        type=Path,
        default=REPO_ROOT / "compliance/datacontracts/fedramp-ssp-v1.yaml",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when --output differs from generated lineage",
    )
    args = parser.parse_args(argv)
    report = mapping_lineage_report(args.contract)
    findings = validate_lineage_report(report)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not args.output:
            parser.error("--check requires --output")
        if (
            not args.output.is_file()
            or args.output.read_text(encoding="utf-8") != rendered
        ):
            print(f"LINEAGE generated report differs: {args.output}", file=sys.stderr)
            return 1
    elif args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    for finding in findings:
        print(f"LINEAGE {finding}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
