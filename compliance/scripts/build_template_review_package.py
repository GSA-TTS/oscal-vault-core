#!/usr/bin/env python3
"""Build a local, manual-review SSP template comparison package.

Transfers matching control-table status, origination, and narrative content
from a source SSP into an official blank target template. It appends a clearly
labeled source-control appendix and writes a JSON manifest, so target-only
controls and source controls without a target table cannot be mistaken for
implemented content. The output is deliberately local-only: source-derived
``.docx`` review artifacts are ignored by git and are not authorization or
submission packages.

Example:
    uv run python compliance/scripts/build_template_review_package.py \\
      --source fismatic/GSA-Pages_Low-SSPP_(2-13-2026).docx \\
      --template fismatic/Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx \\
      --contract compliance/datacontracts/moderate-sspp-roundtrip.yaml \\
      --out-dir review-output/gsa-pages-moderate-review
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FISMATIC_SRC = _REPO_ROOT / "fismatic" / "src" / "phase_1"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_FISMATIC_SRC) not in sys.path:
    sys.path.insert(0, str(_FISMATIC_SRC))

from docx import Document  # noqa: E402

from compliance.lib.control_table_anchors import find_control_tables  # noqa: E402
from compliance.lib.control_table_fill import fill_control_tables  # noqa: E402
from compliance.lib.oscal_source import (  # noqa: E402
    load_from_docx,
    statement_text_map_from_oscal_ssp,
)
from fismatic.ssp_converter import SSPConverter  # noqa: E402


_NIST_FAMILIES = {
    "AC",
    "AT",
    "AU",
    "CA",
    "CM",
    "CP",
    "IA",
    "IR",
    "MA",
    "MP",
    "PE",
    "PL",
    "PM",
    "PS",
    "PT",
    "RA",
    "SA",
    "SC",
    "SI",
    "SR",
}


def _control_family(control_id: str) -> str:
    return control_id.split("-", 1)[0].upper()


def _classify_source_only(control_id: str) -> str:
    """Classify a source-only control without discarding it from review."""
    return (
        "baseline_or_template_delta"
        if _control_family(control_id) in _NIST_FAMILIES
        else "legacy_or_custom_framework_control"
    )


def _add_cover(doc: Document, source: Path, template: Path) -> None:
    """Add an unambiguous review-only notice before any transferred content."""
    doc.add_heading("Manual Review Overlay", level=1)
    doc.add_paragraph(
        "NOT AN AUTHORIZATION OR SUBMISSION PACKAGE. This local review artifact "
        "copies matching control-table content from the source SSP into the target "
        "template only to support human comparison. It does not assert that the "
        "source system meets the target template's impact level or baseline."
    )
    doc.add_paragraph(f"Source SSP: {source.name}")
    doc.add_paragraph(f"Target template: {template.name}")
    doc.add_paragraph(
        "Target-only controls remain in their original template state. Source "
        "controls without a matching target table are listed in the appendix and "
        "manifest."
    )
    doc.add_page_break()


def _add_source_appendix(
    doc: Document,
    controls,
    statement_text_by_control: dict[str, dict[str, str]],
    unmatched_controls: list[str],
    unmatched_tables: list[str],
    source_only_controls: set[str],
) -> None:
    """Append source narratives and explicit coverage gaps for manual review."""
    doc.add_page_break()
    doc.add_heading("Source Control Appendix", level=1)
    doc.add_paragraph(
        "This appendix preserves source control content in review form. It is "
        "included because narrative headings in different SSP templates are not "
        "semantically interchangeable; blank target narrative slots must not be "
        "treated as missing source content."
    )

    doc.add_heading("Coverage Gaps", level=2)
    doc.add_paragraph(
        f"Source controls without a matching target table ({len(unmatched_controls)}): "
        f"{', '.join(unmatched_controls) or 'none'}"
    )
    doc.add_paragraph(
        f"Target template controls left unfilled ({len(unmatched_tables)}): "
        f"{', '.join(unmatched_tables)}"
    )

    doc.add_heading("Transferred Source Content", level=2)
    for control in sorted(controls, key=lambda c: c.control_id):
        doc.add_heading(control.control_id, level=3)
        doc.add_paragraph(
            "Implementation status: "
            f"{control.oscal_implementation_status or 'not recorded'}"
        )
        doc.add_paragraph(
            "Control origination: "
            f"{', '.join(control.oscal_origination) or 'not recorded'}"
        )
        statements = statement_text_by_control.get(control.control_id, {})
        if not statements:
            doc.add_paragraph("No parsed statement narrative available.")
            continue
        for statement_id, text in statements.items():
            # Official templates do not consistently define Word's built-in
            # ``List Bullet`` style. Use the document's default paragraph
            # style so a review package works across Low/Moderate/High files.
            doc.add_paragraph(f"- {statement_id}: {text}")

    if source_only_controls:
        doc.add_page_break()
        doc.add_heading("Supplemental Source-Only Controls", level=1)
        doc.add_paragraph(
            "The following source controls have no matching target-template "
            "table. They are retained as supplemental control records rather "
            "than discarded. Legacy/custom framework controls are not mapped "
            "to a NIST baseline control automatically."
        )
        by_id = {control.control_id: control for control in controls}
        for control_id in sorted(source_only_controls):
            control = by_id[control_id]
            doc.add_heading(control_id, level=2)
            doc.add_paragraph(f"Classification: {_classify_source_only(control_id)}")
            table = doc.add_table(rows=3, cols=2)
            table.style = "Table Grid"
            table.cell(0, 0).text = "Implementation status"
            table.cell(0, 1).text = (
                control.oscal_implementation_status or "not recorded"
            )
            table.cell(1, 0).text = "Control origination"
            table.cell(1, 1).text = (
                ", ".join(control.oscal_origination) or "not recorded"
            )
            table.cell(2, 0).text = "Source narratives"
            table.cell(2, 1).text = (
                "\n".join(
                    f"{statement_id}: {text}"
                    for statement_id, text in statement_text_by_control.get(
                        control_id, {}
                    ).items()
                )
                or "No parsed statement narrative available."
            )


def build_review_package(
    source: Path, template: Path, output_docx: Path, manifest_path: Path
) -> dict:
    """Create a review document and return/write its coverage manifest."""
    # fismatic logs table-shape diagnostics to stdout for every real SSP
    # control. A review package needs its concise manifest, not thousands of
    # parser-debug lines; exceptions still propagate to the caller.
    with contextlib.redirect_stdout(io.StringIO()):
        converter = SSPConverter(str(source))
        oscal = converter.get_oscal_data()
        controls = load_from_docx(str(source))
    if not oscal:
        raise ValueError(f"Unable to parse source SSP: {source}")
    statements = statement_text_map_from_oscal_ssp(oscal)

    table_result = fill_control_tables(
        template,
        controls,
        output_docx,
        statement_text_by_control=statements,
    )
    doc = Document(str(output_docx))
    _add_cover(doc, source, template)
    _add_source_appendix(
        doc,
        controls,
        statements,
        table_result.unmatched_control_ids,
        table_result.unmatched_table_ids,
        set(table_result.unmatched_control_ids),
    )
    doc.save(str(output_docx))

    manifest = {
        "purpose": "Manual review only; not an authorization or submission package.",
        "source_docx": source.name,
        "target_template": template.name,
        "source_controls_parsed": len(controls),
        "target_control_tables": len(find_control_tables(template)),
        "matching_control_tables_filled": len(table_result.filled_control_ids),
        "matching_physical_tables_filled": table_result.filled_table_count,
        "duplicate_target_control_ids": table_result.duplicate_table_ids,
        "source_controls_without_target_table": sorted(
            table_result.unmatched_control_ids
        ),
        "source_only_control_classification": {
            control_id: _classify_source_only(control_id)
            for control_id in sorted(table_result.unmatched_control_ids)
        },
        "target_controls_left_unfilled": sorted(table_result.unmatched_table_ids),
        "source_statement_controls": len(statements),
        "review_guidance": [
            "Filled target tables preserve source status, origination, and parsed statement text.",
            "Target-only controls are deliberately unchanged and must not be treated as implemented.",
            "The source-control appendix is the review surface for narratives that cannot map to target template headings.",
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--name", default="template-review", help="Output base name without extension"
    )
    args = parser.parse_args(argv)
    if not args.source.is_file():
        parser.error(f"Source SSP not found: {args.source}")
    if not args.template.is_file():
        parser.error(f"Target template not found: {args.template}")

    output_docx = args.out_dir / f"{args.name}.docx"
    manifest = args.out_dir / f"{args.name}.manifest.json"
    result = build_review_package(args.source, args.template, output_docx, manifest)
    print(json.dumps(result, indent=2))
    print(f"DOCX: {output_docx}")
    print(f"MANIFEST: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
