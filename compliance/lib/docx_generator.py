"""Generates a FedRAMP-SSP-shaped .docx file from OSCAL SSP JSON, using
python-docx (https://python-docx.readthedocs.io/).

This is the inverse of `fismatic.ssp_converter.SSPConverter`: it turns OSCAL
SSP JSON (`control-implementation.implemented-requirements`) back into a
`.docx` document whose control-summary tables `fismatic`'s parser
(`fismatic.docx_parser.DocxParser` / `fismatic.parser`) can re-parse.

OSCAL is treated as this pipeline's canonical interoperability/storage
format (see `compliance/lib/canonical_model.py`): this generator works from
the *raw* OSCAL SSP JSON (fismatic's own implementation-status/
control-origination vocabulary, e.g. "partially-implemented", ["hybrid"]),
not the further-collapsed OSBAPI `ControlSummary` shape, so no information
is lost to OSBAPI's coarser enums before regenerating the document.

Table layout, matched to what `fismatic.docx_parser.DocxParser.get_controls`
/ `fismatic.parser.parse_control_table` actually recognize (reverse-engineered
against `<ssp>.docx`):

  - One "Control Summary Information" (CIS) table per control, 2 columns:
      row 0: control_id | "Control Summary Information"
      row 1: "Implementation Status:" | <checkbox block>
      row 2: "Control Origination:"   | <checkbox block>
      row 3 (optional): "Responsible Role:" | <role text>
    Row order matters: `DocxParser.is_control_table` scores row 0 (header)
    and row 1 (data) to decide whether a table is a control table at all;
    keeping the Implementation Status row second guarantees that heuristic
    passes regardless of whether a Responsible Role row is present.

  - One single-row continuation table per OSCAL `statement` ("Part a", "Part
    b", ...), immediately following the CIS table. A single-row table always
    fails `DocxParser.is_control_table` (which requires >= 2 rows), so it's
    guaranteed to be attributed to the current control as narrative detail
    rather than mistaken for a new control table.

The real source document sometimes embeds "Part a/b/c" narrative rows
*inside* the same CIS table instead of a separate table -- `fismatic`'s
parser silently drops those in that layout (see
`compliance/tests/test_docx_generator.py`). Using dedicated single-row
continuation tables here achieves *better* narrative round-trip fidelity
than that in-place layout, which is a deliberate choice, not a bug.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from docx import Document

# Order mirrors fismatic.parser._STATUS_KEYWORDS; the checkbox line text must
# start with (case-insensitively) the keyword's regex prefix for the parser
# to recognize it once checked. "not-implemented" has no explicit checkbox --
# it's the implicit fallback when none of these are checked.
_STATUS_LINES: List[tuple] = [
    ("implemented", "Implemented"),
    ("partially-implemented", "Partially Implemented"),
    ("planned", "Planned"),
    ("alternative-implementation", "Alternative implementation"),
    ("customer-configured", "Configured by Customer"),
    ("not-applicable", "Not applicable"),
]

# Order mirrors fismatic.parser._ORIGINATION_KEYWORDS. Text for "inherited"/
# "hybrid" includes bracketed placeholder detail (real FedRAMP templates do
# too); this placeholder text does not affect round-trip parity since
# fismatic's SSPConverter never propagates that free text into OSCAL SSP
# JSON's `control-origination` list in the first place (only the flags do).
_ORIGINATION_LINES: List[tuple] = [
    ("inherited", "Inherited from: [Source System's Name]"),
    ("service-provider-corporate", "[Source System's Name] Common Control"),
    (
        "hybrid",
        "Hybrid Control (Shared Between [System Name] and [Source System's Name])",
    ),
    ("system-specific", "System Specific Control"),
    ("customer-configured", "Configured by Customer"),
    ("customer-provided", "Provided by Customer"),
    ("shared", "Shared"),
]


def _render_status_checkboxes(state: Optional[str]) -> str:
    lines = []
    for key, label in _STATUS_LINES:
        box = "\u2612" if key == state else "\u2610"  # ☒ / ☐
        lines.append(f"{box} {label}")
    return "\n".join(lines)


def _render_origination_checkboxes(origination: Optional[List[str]]) -> str:
    flags = {f.strip().lower() for f in (origination or []) if f}
    lines = []
    for key, label in _ORIGINATION_LINES:
        box = "\u2612" if key in flags else "\u2610"  # ☒ / ☐
        lines.append(f"{box} {label}")
    return "\n".join(lines)


def _part_label_from_statement_id(statement_id: Optional[str], index: int) -> str:
    """Invert `fismatic.parser.generate_statement_id`: 'ac-2_stmt.a' -> 'a'."""
    if statement_id and "_stmt." in statement_id:
        return statement_id.rsplit("_stmt.", 1)[1]
    return chr(ord("a") + index) if index < 26 else str(index + 1)


def _role_id(responsible_roles: Any) -> Optional[str]:
    if not responsible_roles:
        return None
    first = responsible_roles[0]
    if isinstance(first, dict):
        return first.get("role-id")
    return str(first) if first else None


def _add_control(doc: "Document", req: Dict[str, Any]) -> None:
    control_id = req.get("control-id")
    if not control_id:
        return

    # Extract status and origination from either first-class fields or props
    props = req.get("props") or []
    props_status = next(
        (p.get("value") for p in props if p.get("name") == "implementation-status"),
        None,
    )
    props_orig = [
        p.get("value")
        for p in props
        if p.get("name") == "control-origination" and p.get("value")
    ]

    status_state = (
        (req.get("implementation-status") or {}).get("state")
        or req.get("implementation_status")
        or props_status
    )
    origination = req.get("control-origination") or req.get("origination") or props_orig
    responsible_role = _role_id(req.get("responsible-roles"))
    statements = req.get("statements") or []

    # --- Control Summary Information (CIS) table ---
    cis = doc.add_table(rows=0, cols=2)
    cis.style = "Table Grid"

    heading_cells = cis.add_row().cells
    heading_cells[0].text = control_id
    heading_cells[1].text = "Control Summary Information"

    status_cells = cis.add_row().cells
    status_cells[0].text = "Implementation Status:"
    status_cells[1].text = _render_status_checkboxes(status_state)

    origination_cells = cis.add_row().cells
    origination_cells[0].text = "Control Origination:"
    origination_cells[1].text = _render_origination_checkboxes(origination)

    if responsible_role:
        role_cells = cis.add_row().cells
        role_cells[0].text = "Responsible Role:"
        role_cells[1].text = str(responsible_role)

    doc.add_paragraph()

    # --- narrative continuation tables, one per statement ---
    for index, statement in enumerate(statements):
        description = statement.get("description") or statement.get("remarks")
        if not description:
            continue
        part = _part_label_from_statement_id(statement.get("statement-id"), index)

        part_table = doc.add_table(rows=1, cols=2)
        part_table.style = "Table Grid"
        part_cells = part_table.rows[0].cells
        part_cells[0].text = f"Part {part}"
        part_cells[1].text = str(description)

        doc.add_paragraph()


def generate_control_interchange_docx(
    oscal_ssp: Dict[str, Any],
    output_path: Union[str, Path],
    *,
    title: str = "FedRAMP System Security Plan",
) -> None:
    """Generate a FedRAMP control-interchange .docx from an OSCAL SSP JSON document (#47).

    Note: This produces a structured control-interchange document containing title,
    system name, and FedRAMP CIS control tables. It is designed for cross-system control
    interchange and reviewer handoff, NOT full source DOCX template regeneration.
    """
    ssp = oscal_ssp.get("system-security-plan", oscal_ssp)
    system_name = (ssp.get("system-characteristics") or {}).get("system-name")

    doc = Document()
    doc.add_heading(title, level=1)
    if system_name:
        doc.add_paragraph(f"System Name: {system_name}")

    requirements = (ssp.get("control-implementation") or {}).get(
        "implemented-requirements", []
    )
    for req in requirements:
        _add_control(doc, req)

    doc.save(str(output_path))


# Backwards-compatibility alias
generate_ssp_docx = generate_control_interchange_docx
