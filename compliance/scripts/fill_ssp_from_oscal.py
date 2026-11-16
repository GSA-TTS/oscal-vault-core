#!/usr/bin/env python3
"""Fill an SSP template's control tables from an OSCAL SSP JSON document."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FISMATIC_SRC = REPO_ROOT / "fismatic" / "src" / "phase_1"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(FISMATIC_SRC) not in sys.path:
    sys.path.insert(0, str(FISMATIC_SRC))

from compliance.lib.docx_integrity_render import build_from_oscal  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oscal", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--strict-mapping", action="store_true")
    parser.add_argument("--fail-unmatched-source", action="store_true")
    parser.add_argument("--fail-duplicate-targets", action="store_true")
    args = parser.parse_args(argv)

    for label, path in (("OSCAL SSP", args.oscal), ("template", args.template)):
        if not path.is_file():
            parser.error(f"{label} not found: {path}")

    manifest = build_from_oscal(
        args.oscal,
        args.template,
        args.output,
        args.manifest,
        strict_mapping=args.strict_mapping,
    )
    print(json.dumps(manifest, indent=2))
    print(f"DOCX: {args.output}")
    print(f"MANIFEST: {args.manifest}")

    failed = False
    if args.fail_unmatched_source and manifest["source_controls_without_target_table"]:
        print(
            "FAIL: source controls were not present in the target template",
            file=sys.stderr,
        )
        failed = True
    if args.fail_duplicate_targets and manifest["duplicate_target_control_ids"]:
        print(
            "FAIL: duplicate control IDs were found in the target template",
            file=sys.stderr,
        )
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
