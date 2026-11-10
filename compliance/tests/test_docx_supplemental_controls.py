"""Tests for supplemental source-control tables."""

import json

from docx import Document

from compliance.lib.docx_supplemental_controls import (
    append_supplemental_controls,
    append_unwritten_statements,
)


def test_append_supplemental_control_preserves_identity_and_text(tmp_path):
    document_path = tmp_path / "ssp.docx"
    oscal_path = tmp_path / "ssp.json"
    Document().save(document_path)
    oscal_path.write_text(
        json.dumps(
            {
                "system-security-plan": {
                    "control-implementation": {
                        "implemented-requirements": [
                            {
                                "control-id": "ac-4(21)",
                                "props": [
                                    {
                                        "name": "implementation-status",
                                        "value": "planned",
                                    },
                                    {
                                        "name": "control-origination",
                                        "value": "system-specific",
                                    },
                                ],
                                "statements": [
                                    {
                                        "statement-id": "ac-4(21)_stmt",
                                        "remarks": "Narrative",
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

    result = append_supplemental_controls(document_path, oscal_path, ["ac-4(21)"])

    assert result == {
        "controls": [{"control_id": "ac-4(21)", "statements": 1, "characters": 9}],
        "statements": 1,
        "mismatches": 0,
    }
    table = Document(document_path).tables[0]
    assert table.rows[0].cells[1].text == "AC-4(21)"
    assert table.rows[3].cells[1].text == "Narrative"


def test_append_unwritten_statement_to_existing_control_table(tmp_path):
    document_path = tmp_path / "ssp.docx"
    oscal_path = tmp_path / "ssp.json"
    document = Document()
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "AC-2"
    document.save(document_path)
    oscal_path.write_text(
        json.dumps(
            {
                "system-security-plan": {
                    "control-implementation": {
                        "implemented-requirements": [
                            {
                                "control-id": "ac-2",
                                "statements": [
                                    {
                                        "statement-id": "ac-2_stmt",
                                        "remarks": "Whole control",
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

    result = append_unwritten_statements(
        document_path,
        oscal_path,
        [
            {
                "source_control_id": "ac-2",
                "target_tables": [{"table_index": 0}],
                "statements": [{"statement_id": "ac-2_stmt", "written": False}],
            }
        ],
    )

    assert result == {"statements": 1, "mismatches": 0}
    assert Document(document_path).tables[0].rows[-1].cells[-1].text == "Whole control"
