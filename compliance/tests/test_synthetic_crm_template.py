from __future__ import annotations

from pathlib import Path

from docx import Document

from compliance.lib.control_table_anchors import find_control_tables
from compliance.lib.docx_fill import check_parity, fill_template, reextract_filled
from compliance.lib.docx_jinjafier import jinjafy
from compliance.lib.heading_parser import load_contract
from compliance.scripts.create_synthetic_crm_template import create


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "compliance" / "datacontracts" / "leveraged-saas-crm-roundtrip.yaml"


def test_synthetic_crm_template_has_generic_crm_layout_only(tmp_path: Path):
    blank = tmp_path / "crm.docx"
    create(blank)

    doc = Document(str(blank))
    anchors = find_control_tables(doc)
    text = "\n".join(
        cell.text for table in doc.tables for row in table.rows for cell in row.cells
    )

    assert [anchor.control_id for anchor in anchors] == ["CR-1", "CR-2", "CR-3"]
    assert all(anchor.origination_cell is None for anchor in anchors)
    assert all(anchor.blank_answer_cell is not None for anchor in anchors)
    assert not any(
        term in text.lower() for term in ("figma", "snyk", "zendesk", "okta")
    )


def test_synthetic_crm_template_roundtrips_contract_fixture(tmp_path: Path):
    blank = tmp_path / "crm.docx"
    jinja = tmp_path / "crm.jinja.docx"
    filled = tmp_path / "crm.filled.docx"
    create(blank)
    contract = load_contract(CONTRACT)
    context = {
        "types_of_users": "Synthetic users narrative.",
        "crm_requirements_intro": "Synthetic responsibility overview.",
    }

    result = jinjafy(blank, contract, jinja)
    fill_template(jinja, context, filled, contract=contract)
    parity = check_parity(context, reextract_filled(filled, contract, strict=True))

    assert result.injected_count == len(context)
    assert parity.ok
