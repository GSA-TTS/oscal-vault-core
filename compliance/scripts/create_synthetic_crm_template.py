#!/usr/bin/env python3
"""Create a synthetic, public-safe CRM roundtrip blank template.

This generator deliberately uses generic labels and placeholders only. It
models the shared six-row customer-responsibility layout seen in CRM templates
without copying an SSP's provider names, control mappings, responsibilities,
or implementation narratives.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from docx import Document
from docx.shared import RGBColor


_INSTRUCTION_COLOR = RGBColor(0x1F, 0x49, 0x7D)
_UNSELECTED = "☐"


def _instruction(doc: Document, text: str) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.font.color.rgb = _INSTRUCTION_COLOR


def _crm_table(doc: Document, control_id: str) -> None:
    table = doc.add_table(rows=6, cols=2)
    table.style = "Table Grid"
    table.rows[0].cells[0].text = control_id
    table.rows[0].cells[1].text = "Responsibility Summary Information"
    status_cell = table.rows[1].cells[0].merge(table.rows[1].cells[1])
    status_cell.text = "Implementation Status:"
    for label in (
        "Implemented",
        "Partially Implemented",
        "Planned",
        "Alternative implementation",
        "Not applicable",
    ):
        status_cell.add_paragraph(f"{_UNSELECTED} {label}")
    table.rows[2].cells[0].merge(
        table.rows[2].cells[1]
    ).text = "Customer Responsibility: [Describe the customer responsibility.]"
    table.rows[3].cells[0].merge(
        table.rows[3].cells[1]
    ).text = "Controls Reference: [Record one or more mapped control references.]"
    table.rows[4].cells[0].merge(
        table.rows[4].cells[1]
    ).text = f"{control_id} Describe how the customer responsibility is implemented."
    table.rows[5].cells[0].merge(
        table.rows[5].cells[1]
    ).text = "[Implementation narrative.]"


def create(output: Path) -> None:
    """Write a deterministic generic CRM blank with three representative tables."""
    document = Document()
    document.add_heading("Synthetic Leveraged SaaS CRM SSPP Blank", level=0)
    document.add_paragraph(
        "This public-safe synthetic template demonstrates a customer-responsibility "
        "table layout. It contains no system, provider, customer, or control content."
    )
    document.add_heading("Types of Users", level=1)
    _instruction(document, "Instruction: For an External User, describe the role.")
    document.add_paragraph("[Types of users narrative placeholder.]")
    document.add_heading("Customer Responsibilities", level=1)
    _instruction(document, "Instruction: Appendices vary on a solution.")
    document.add_paragraph("[Customer responsibility overview placeholder.]")
    for control_id in ("CR-1", "CR-2", "CR-3"):
        _crm_table(document, control_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    create(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
