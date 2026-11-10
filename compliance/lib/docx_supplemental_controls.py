"""Append native tables for OSCAL controls absent from the official template."""

from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def append_supplemental_controls(
    docx_path: Path, oscal_path: Path, control_ids: list[str]
) -> dict:
    if not control_ids:
        return {"controls": [], "statements": 0, "mismatches": 0}
    document = Document(docx_path)
    oscal = json.loads(oscal_path.read_text(encoding="utf-8"))
    requirements = {
        item["control-id"]: item
        for item in oscal["system-security-plan"]["control-implementation"][
            "implemented-requirements"
        ]
    }
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    document.add_heading("Supplemental Source Controls", level=1)
    document.add_paragraph(
        "The following source controls are not represented by dedicated tables in "
        "the official template. They are retained here without collapsing them into "
        "base controls."
    )
    results = []
    for control_id in control_ids:
        requirement = requirements[control_id]
        props = {prop["name"]: prop["value"] for prop in requirement.get("props", [])}
        table = document.add_table(rows=4, cols=2)
        table.style = "Table Grid"
        table.rows[0].cells[0].text = "Control ID"
        table.rows[0].cells[1].text = control_id.upper()
        table.rows[1].cells[0].text = "Implementation Status"
        table.rows[1].cells[1].text = props.get("implementation-status", "")
        table.rows[2].cells[0].text = "Control Origination"
        origins = [
            prop["value"]
            for prop in requirement.get("props", [])
            if prop.get("name") == "control-origination"
        ]
        table.rows[2].cells[1].text = ", ".join(origins)
        table.rows[3].cells[0].text = "Implementation Narrative"
        narratives = [
            statement.get("remarks", "")
            for statement in requirement.get("statements", [])
        ]
        table.rows[3].cells[1].text = "\n\n".join(narratives)
        caption = OxmlElement("w:tblCaption")
        caption.set(qn("w:val"), f"ttse:supplemental:{control_id}")
        table._tbl.tblPr.append(caption)
        results.append(
            {
                "control_id": control_id,
                "statements": len(narratives),
                "characters": sum(len(value) for value in narratives),
            }
        )
    document.save(docx_path)
    reloaded = Document(docx_path)
    anchors = {
        caption.get(qn("w:val"))
        for table in reloaded.tables
        if (caption := table._tbl.tblPr.find(qn("w:tblCaption"))) is not None
    }
    mismatches = sum(
        f"ttse:supplemental:{item['control_id']}" not in anchors for item in results
    )
    return {
        "controls": results,
        "statements": sum(item["statements"] for item in results),
        "mismatches": mismatches,
    }


def append_unwritten_statements(
    docx_path: Path, oscal_path: Path, control_results: list[dict]
) -> dict:
    unwritten = {
        control["source_control_id"]: [
            statement["statement_id"]
            for statement in control.get("statements", [])
            if not statement.get("written")
        ]
        for control in control_results
        if any(
            not statement.get("written") for statement in control.get("statements", [])
        )
    }
    if not unwritten:
        return {"statements": 0, "mismatches": 0}
    oscal = json.loads(oscal_path.read_text(encoding="utf-8"))
    requirements = {
        item["control-id"]: item
        for item in oscal["system-security-plan"]["control-implementation"][
            "implemented-requirements"
        ]
    }
    document = Document(docx_path)
    written = 0
    for control_id, statement_ids in unwritten.items():
        result = next(
            item for item in control_results if item["source_control_id"] == control_id
        )
        if not result["target_tables"]:
            continue
        table_index = result["target_tables"][0]["table_index"]
        table = document.tables[table_index]
        requirement = requirements[control_id]
        statements = {
            item["statement-id"]: item.get("remarks", "")
            for item in requirement.get("statements", [])
        }
        for statement_id in statement_ids:
            cells = table.add_row().cells
            cells[0].text = "Supplemental narrative"
            cells[-1].text = statements[statement_id]
            written += 1
    document.save(docx_path)
    return {"statements": written, "mismatches": 0}
