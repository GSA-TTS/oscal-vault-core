"""Tests for filling an SSP template directly from OSCAL JSON."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import docx

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "compliance" / "scripts" / "fill_ssp_from_oscal.py"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compliance.lib.control_checkbox_vocab import UNCHECKED_GLYPH  # noqa: E402
from compliance.lib.control_table_anchors import find_control_tables  # noqa: E402


def _load_script():
    spec = importlib.util.spec_from_file_location("fill_ssp_from_oscal", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fill_ssp_from_oscal = _load_script()


def _template(path: Path) -> None:
    document = docx.Document()
    table = document.add_table(rows=5, cols=2)
    table.rows[0].cells[0].text = "AC-2"
    table.rows[0].cells[1].text = "Control Summary Information"
    status = table.rows[1].cells[0].merge(table.rows[1].cells[-1])
    status.text = "Implementation Status:"
    status.add_paragraph(f"{UNCHECKED_GLYPH} Implemented")
    status.add_paragraph(f"{UNCHECKED_GLYPH} Planned")
    origination = table.rows[2].cells[0].merge(table.rows[2].cells[-1])
    origination.text = "Control Origination:"
    origination.add_paragraph(f"{UNCHECKED_GLYPH} System Specific Control")
    table.rows[3].cells[0].merge(
        table.rows[3].cells[-1]
    ).text = "Describe implementation"
    table.rows[4].cells[0].text = "Part a"
    document.save(path)


def _oscal(path: Path, *, control_id: str = "AC-2") -> None:
    path.write_text(
        json.dumps(
            {
                "system-security-plan": {
                    "control-implementation": {
                        "implemented-requirements": [
                            {
                                "control-id": control_id,
                                "props": [
                                    {
                                        "name": "implementation-status",
                                        "value": "implemented",
                                    },
                                    {
                                        "name": "control-origination",
                                        "value": "system-specific",
                                    },
                                ],
                                "statements": [
                                    {
                                        "statement-id": "ac-2_stmt.a",
                                        "remarks": "Accounts are centrally managed.",
                                    }
                                ],
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def test_build_from_oscal_fills_template_and_manifest(tmp_path):
    source = tmp_path / "ssp.json"
    template = tmp_path / "template.docx"
    output = tmp_path / "filled.docx"
    manifest_path = tmp_path / "manifest.json"
    _oscal(source)
    _template(template)

    manifest = fill_ssp_from_oscal.build_from_oscal(
        source, template, output, manifest_path
    )

    assert manifest["matching_control_ids_filled"] == 1
    assert manifest["source_controls_without_target_table"] == []
    assert manifest["target_controls_left_unfilled"] == []
    assert manifest_path.is_file()
    assert manifest["controls"][0]["source_control_id"] == "AC-2"
    assert manifest["controls"][0]["target_tables"][0]["table_index"] == 0
    assert manifest["controls"][0]["statements"] == [
        {
            "statement_id": "ac-2_stmt.a",
            "part": "a",
            "placement": "part-row",
            "written": True,
        }
    ]
    anchor = find_control_tables(output)[0]
    assert anchor.part_rows["a"].text == "Accounts are centrally managed."


def test_cli_can_fail_on_unmatched_source_control(tmp_path):
    source = tmp_path / "ssp.json"
    template = tmp_path / "template.docx"
    output = tmp_path / "filled.docx"
    manifest = tmp_path / "manifest.json"
    _oscal(source, control_id="AC-3")
    _template(template)

    result = fill_ssp_from_oscal.main(
        [
            "--oscal",
            str(source),
            "--template",
            str(template),
            "--output",
            str(output),
            "--manifest",
            str(manifest),
            "--fail-unmatched-source",
        ]
    )

    assert result == 1
    assert json.loads(manifest.read_text())["source_controls_without_target_table"] == [
        "AC-3"
    ]


def test_schema_valid_by_component_text_is_used_as_fallback(tmp_path):
    source = tmp_path / "ssp.json"
    template = tmp_path / "template.docx"
    output = tmp_path / "filled.docx"
    manifest = tmp_path / "manifest.json"
    _oscal(source)
    payload = json.loads(source.read_text())
    requirement = payload["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ][0]
    requirement["statements"][0].pop("remarks")
    requirement["by-components"] = [
        {"description": "Narrative from a schema-valid by-component."}
    ]
    source.write_text(json.dumps(payload), encoding="utf-8")
    _template(template)

    fill_ssp_from_oscal.build_from_oscal(source, template, output, manifest)

    assert (
        find_control_tables(output)[0].part_rows["a"].text
        == "Narrative from a schema-valid by-component."
    )


def test_statement_component_text_and_status_are_used(tmp_path):
    source = tmp_path / "ssp.json"
    template = tmp_path / "template.docx"
    output = tmp_path / "filled.docx"
    manifest = tmp_path / "manifest.json"
    _oscal(source)
    payload = json.loads(source.read_text())
    requirement = payload["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ][0]
    requirement["props"] = [
        prop for prop in requirement["props"] if prop["name"] != "implementation-status"
    ]
    requirement["statements"][0].pop("remarks")
    requirement["statements"][0]["by-components"] = [
        {
            "description": "Narrative from the statement-level component.",
            "implementation-status": {"state": "planned"},
        }
    ]
    source.write_text(json.dumps(payload), encoding="utf-8")
    _template(template)

    fill_ssp_from_oscal.build_from_oscal(source, template, output, manifest)

    anchor = find_control_tables(output)[0]
    assert anchor.part_rows["a"].text == "Narrative from the statement-level component."
    assert "☒ Planned" in anchor.status_cell.text
