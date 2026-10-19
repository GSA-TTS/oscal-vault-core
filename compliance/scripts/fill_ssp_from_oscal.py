#!/usr/bin/env python3
"""Fill an SSP template's control tables from an OSCAL SSP JSON document."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FISMATIC_SRC = REPO_ROOT / "fismatic" / "src" / "phase_1"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(FISMATIC_SRC) not in sys.path:
    sys.path.insert(0, str(FISMATIC_SRC))

from compliance.lib.control_table_anchors import find_control_tables  # noqa: E402
from compliance.lib.control_table_fill import fill_control_tables  # noqa: E402
from compliance.lib.oscal_source import (  # noqa: E402
    load_from_oscal_ssp,
    statement_text_map_from_oscal_ssp,
)


def build_from_oscal(
    oscal_path: Path,
    template_path: Path,
    output_path: Path,
    manifest_path: Path,
    *,
    strict_mapping: bool = False,
) -> dict[str, Any]:
    oscal = json.loads(oscal_path.read_text(encoding="utf-8"))
    controls = load_from_oscal_ssp(oscal, strict=strict_mapping)
    statements = statement_text_map_from_oscal_ssp(oscal)
    result = fill_control_tables(
        template_path,
        controls,
        output_path,
        statement_text_by_control=statements,
    )
    manifest = {
        "purpose": "Manual review only; not an authorization or submission package.",
        "source_oscal": oscal_path.name,
        "target_template": template_path.name,
        "source_controls": len(controls),
        "source_statement_controls": len(statements),
        "target_control_tables": len(find_control_tables(template_path)),
        "matching_control_ids_filled": len(result.filled_control_ids),
        "matching_physical_tables_filled": result.filled_table_count,
        "source_controls_without_target_table": sorted(result.unmatched_control_ids),
        "target_controls_left_unfilled": sorted(result.unmatched_table_ids),
        "duplicate_target_control_ids": result.duplicate_table_ids,
        "source_id_collisions": result.source_id_collisions,
        "controls": result.control_results,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


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
