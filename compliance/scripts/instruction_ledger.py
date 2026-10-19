#!/usr/bin/env python3
"""Emit a source-located instruction ledger and optionally enforce it strictly."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import docx
from docx.shared import RGBColor

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compliance.lib.instruction_ledger import (  # noqa: E402
    assert_reconciled,
    build_instruction_ledger,
    canonical_reconciliation_findings,
    inventory_canonical_instructions,
    inventory_rendered_instructions,
    load_instruction_policy,
    reconciliation_findings,
    render_instruction_outputs,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--docx", type=Path)
    source.add_argument(
        "--synthetic",
        action="store_true",
        help="Generate a CUI-safe instruction fixture (for CI)",
    )
    parser.add_argument("--json", type=Path, help="Write the ledger report here")
    parser.add_argument("--policy", type=Path, help="Versioned YAML/JSON overrides")
    parser.add_argument(
        "--output-dir", type=Path, help="Rendered/export root to reconcile"
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Render guidance into --output-dir before reconciliation",
    )
    parser.add_argument(
        "--strict", action="store_true", help="Fail on unmapped nodes or duplicates"
    )
    parser.add_argument(
        "--require-canonical",
        action="store_true",
        help="Also require matching provenance in .snapshots/current sidecars",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Exit successfully when the explicitly requested DOCX is unavailable",
    )
    args = parser.parse_args(argv)

    if args.render and not args.output_dir:
        parser.error("--render requires --output-dir")
    if args.strict and not args.output_dir:
        parser.error("--strict requires --output-dir")
    if args.require_canonical and not args.output_dir:
        parser.error("--require-canonical requires --output-dir")
    if args.docx and not args.docx.is_file():
        if args.skip_missing:
            print(f"SKIP: instruction source is unavailable: {args.docx}")
            return 0
        parser.error(f"instruction source does not exist: {args.docx}")

    temp_dir = tempfile.TemporaryDirectory() if args.synthetic else None
    if args.synthetic:
        docx_path = Path(temp_dir.name) / "instruction-ledger.docx"
        document = docx.Document()
        document.add_paragraph("Instruction: front matter guidance.")
        document.add_heading("Synthetic Section", level=1)
        blue = document.add_paragraph()
        blue.add_run("Describe the synthetic system.").font.color.rgb = RGBColor(
            0x00, 0x70, 0xC0
        )
        cell = document.add_table(rows=1, cols=1).cell(0, 0)
        cell.paragraphs[0].text = "Instructions: complete this synthetic cell."
        document.save(docx_path)
    else:
        docx_path = args.docx

    try:
        overrides = load_instruction_policy(args.policy) if args.policy else None
        ledger = build_instruction_ledger(docx_path, overrides=overrides)
        if args.render:
            render_instruction_outputs(ledger, args.output_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    rendered_inventory = (
        inventory_rendered_instructions(args.output_dir) if args.output_dir else None
    )
    canonical_inventory = (
        inventory_canonical_instructions(args.output_dir)
        if args.require_canonical
        else []
    )
    findings = reconciliation_findings(ledger, rendered_inventory)
    if args.require_canonical:
        findings.extend(canonical_reconciliation_findings(ledger, canonical_inventory))
    report = ledger.to_dict()
    report["reconciliation"] = {
        "rendered_records": len(rendered_inventory or []),
        "canonical_records": len(canonical_inventory),
        "findings": len(findings),
        "coverage": (
            1.0
            if not ledger.entries
            else max(0.0, (len(ledger.entries) - len(findings)) / len(ledger.entries))
        ),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")

    for finding in findings:
        print(f"UNRECONCILED {docx_path}: {finding}", file=sys.stderr)
    if args.strict:
        try:
            assert_reconciled(ledger, rendered_inventory or [])
        except ValueError:
            return 1
        if args.require_canonical and canonical_reconciliation_findings(
            ledger, canonical_inventory
        ):
            return 1
    if temp_dir:
        temp_dir.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
