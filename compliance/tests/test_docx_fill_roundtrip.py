"""Roundtrip parity tests for the fill-back pipeline (Phase R16.4).

The primary acceptance metric (plan §7): every value written into a jinjafied
template via docxtpl is recovered by re-extraction (100% slot parity).

Synthetic tests build an in-memory blank + contract and prove the full
jinjafy -> fill -> re-extract -> parity loop. Fixture-gated tests run the
committed Low/CRM contracts against the real blanks (skip when absent), and a
committed-template-only test runs the fill+re-extract half using the checked-in
jinja templates so parity is exercised even without any source .docx.
"""

from __future__ import annotations

from pathlib import Path

import docx
import pytest
from docx.shared import RGBColor

from compliance.lib.docx_fill import (
    check_parity,
    fill_template,
    load_fill_context,
    reextract_filled,
    roundtrip,
    synthetic_fill_context,
)
from compliance.lib.docx_jinjafier import jinjafy
from compliance.lib.heading_parser import load_contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FISMATIC = _REPO_ROOT / "fismatic"
_DC = _REPO_ROOT / "compliance" / "datacontracts"
_FIX = _REPO_ROOT / "compliance" / "tests" / "fixtures" / "roundtrip"
_TEMPLATES = _REPO_ROOT / "compliance" / "templates"

_LOW_BLANK = _FISMATIC / "Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx"
_CRM_BLANK = (
    _FISMATIC / "Leveraged-SaaS-CRM-SSPP-Template-[Contains-CUI]-02-01-2024.docx"
)
_MOD_BLANK = _FISMATIC / "Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx"
_HIGH_BLANK = _FISMATIC / "High-SSPP-Template-[Contains-CUI]-10-16-2024.docx"
_LOW_CONTRACT = _DC / "low-sspp-roundtrip.yaml"
_CRM_CONTRACT = _DC / "leveraged-saas-crm-roundtrip.yaml"
_MOD_CONTRACT = _DC / "moderate-sspp-roundtrip.yaml"
_HIGH_CONTRACT = _DC / "high-sspp-roundtrip.yaml"
_LOW_JINJA = _TEMPLATES / "low-sspp.jinja.docx"
_CRM_JINJA = _TEMPLATES / "leveraged-saas-crm.jinja.docx"
_MOD_JINJA = _TEMPLATES / "moderate-sspp.jinja.docx"
_HIGH_JINJA = _TEMPLATES / "high-sspp.jinja.docx"
_LOW_FIX = _FIX / "low-sspp.sample.json"
_CRM_FIX = _FIX / "leveraged-saas-crm.sample.json"
_MOD_FIX = _FIX / "moderate-sspp.sample.json"
_HIGH_FIX = _FIX / "high-sspp.sample.json"

BLUE = RGBColor(0x00, 0x70, 0xC0)


def _mini_contract(tmp_path: Path) -> Path:
    yaml_text = """
apiVersion: v3.1.0
kind: DataContract
id: mini
name: mini
version: 1.0.0
status: active
schema:
  - id: src
    name: src
    logicalType: object
    physicalType: file
    customProperties:
      - property: isSource
        value: "true"
      - property: instructionColors
        value: "0070C0"
    properties:
      - name: marker
        logicalType: string
        customProperties:
          - property: sourceKind
            value: constant
  - id: oscal_ssp_system
    name: oscal_ssp
    logicalType: object
    physicalType: object
    customProperties:
      - property: oscalType
        value: oscal.ssp
      - property: destinationFolder
        value: "Systems/"
    properties:
      - name: desc
        logicalType: string
        customProperties:
          - property: captureStrategy
            value: heading_paragraph
          - property: templateTag
            value: desc
          - property: writeStrategy
            value: paragraph_after_instruction
          - property: instructionAnchor
            value: "Instruction: describe the system"
          - property: writeScope
            value: replace_slot
"""
    p = tmp_path / "mini.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    return p


def _mini_table_contract(tmp_path: Path) -> Path:
    contract = _mini_contract(tmp_path)
    text = contract.read_text(encoding="utf-8").replace(
        "value: paragraph_after_instruction",
        "value: table_after_instruction",
    )
    contract.write_text(text, encoding="utf-8")
    return contract


def _blue(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.color.rgb = BLUE
    return p


# ---------------------------------------------------------------------------
# Synthetic full-loop
# ---------------------------------------------------------------------------


class TestSyntheticRoundtrip:
    def test_synthetic_fill_context_marks_every_contract_tag(self, tmp_path):
        contract = load_contract(_mini_contract(tmp_path))

        context = synthetic_fill_context(contract)

        assert context == {
            "desc": "[SYNTHETIC-SSPP-TEMPLATE-TEST]:desc",
        }

    def test_fill_and_reextract_parity(self, tmp_path):
        d = docx.Document()
        d.add_heading("System", level=1)
        _blue(d, "Instruction: describe the system in detail")
        d.add_paragraph("placeholder")
        contract = load_contract(_mini_contract(tmp_path))

        jinja = tmp_path / "t.jinja.docx"
        jinjafy(d, contract, jinja)

        context = {"desc": "the system has three tiers"}
        filled = tmp_path / "filled.docx"
        fill_template(jinja, context, filled, contract=contract)

        recovered = reextract_filled(filled, contract)
        parity = check_parity(context, recovered)
        assert parity.ok
        assert parity.recovery_rate == 1.0
        assert recovered["desc"] == "the system has three tiers"

    def test_roundtrip_wrapper(self, tmp_path):
        d = docx.Document()
        d.add_heading("System", level=1)
        _blue(d, "Instruction: describe the system fully")
        d.add_paragraph("placeholder")
        contract = load_contract(_mini_contract(tmp_path))
        # Point the anchor at the built doc: rebuild contract with matching anchor.
        jinja = tmp_path / "t.jinja.docx"
        # The mini contract anchor is "Instruction: describe the system"; the
        # doc uses "...describe the system fully" which prefix-matches.
        jinjafy(d, contract, jinja)
        fixture = tmp_path / "fix.json"
        fixture.write_text(
            '{"_meta": {"x": 1}, "desc": "roundtrip value"}', encoding="utf-8"
        )
        out = tmp_path / "filled.docx"
        parity = roundtrip(jinja, fixture, contract, out)
        assert parity.ok

    def test_table_after_instruction_roundtrip_is_strict(self, tmp_path):
        d = docx.Document()
        d.add_heading("System", level=1)
        _blue(d, "Instruction: describe the system in detail")
        table = d.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Field"
        table.cell(0, 1).text = "Value"
        table.cell(1, 0).text = "Description"
        table.cell(1, 1).text = "placeholder"
        contract = load_contract(_mini_table_contract(tmp_path))

        jinja = tmp_path / "table.jinja.docx"
        jinjafy(d, contract, jinja)
        filled = tmp_path / "table.filled.docx"
        context = {"desc": "table-cell roundtrip value"}
        fill_template(jinja, context, filled, contract=contract)

        recovered = reextract_filled(filled, contract, strict=True)
        parity = check_parity(context, recovered)
        assert parity.ok
        assert "table-cell roundtrip value" in recovered["desc"]

    def test_strict_reextract_rejects_empty_table_slot(self, tmp_path):
        d = docx.Document()
        d.add_heading("System", level=1)
        _blue(d, "Instruction: describe the system in detail")
        d.add_table(rows=1, cols=1)
        contract = load_contract(_mini_table_contract(tmp_path))
        path = tmp_path / "empty-table.docx"
        d.save(path)

        with pytest.raises(ValueError, match="empty table slot"):
            reextract_filled(path, contract, strict=True)

    def test_missing_tag_is_reported(self, tmp_path):
        recovered = {}
        parity = check_parity({"desc": "value"}, recovered)
        assert parity.missing == ["desc"]
        assert not parity.ok

    def test_mismatch_is_reported(self, tmp_path):
        recovered = {"desc": "something completely different"}
        parity = check_parity({"desc": "expected value"}, recovered)
        assert "desc" in parity.mismatched
        assert not parity.ok

    def test_load_fill_context_drops_meta(self, tmp_path):
        fixture = tmp_path / "f.json"
        fixture.write_text('{"_meta": {"a": 1}, "x": "y"}', encoding="utf-8")
        ctx = load_fill_context(fixture)
        assert ctx == {"x": "y"}

    def test_filled_tags_scoped_to_declared_tags(self, tmp_path):
        """Context keys the template/contract doesn't declare aren't "filled"."""
        d = docx.Document()
        d.add_heading("System", level=1)
        _blue(d, "Instruction: describe the system in detail")
        d.add_paragraph("placeholder")
        contract = load_contract(_mini_contract(tmp_path))

        jinja = tmp_path / "t.jinja.docx"
        jinjafy(d, contract, jinja)

        context = {"desc": "the system has three tiers", "extra_unused_field": "x"}
        filled = tmp_path / "filled.docx"
        result = fill_template(jinja, context, filled, contract=contract)

        assert result.filled_tags == ["desc"]
        assert "extra_unused_field" not in result.filled_tags


# ---------------------------------------------------------------------------
# Fixture-gated: real blanks (full chain incl. jinjafy from blank)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(not _LOW_BLANK.exists(), reason="Low blank not present locally")
def test_low_full_roundtrip_parity(tmp_path):
    jinja = tmp_path / "low.jinja.docx"
    jinjafy(_LOW_BLANK, _LOW_CONTRACT, jinja)
    out = tmp_path / "low.filled.docx"
    parity = roundtrip(jinja, _LOW_FIX, _LOW_CONTRACT, out)
    assert parity.ok, f"mismatched={parity.mismatched} missing={parity.missing}"
    assert parity.recovery_rate == 1.0


@pytest.mark.slow
@pytest.mark.skipif(not _CRM_BLANK.exists(), reason="CRM blank not present locally")
def test_crm_full_roundtrip_parity(tmp_path):
    jinja = tmp_path / "crm.jinja.docx"
    jinjafy(_CRM_BLANK, _CRM_CONTRACT, jinja)
    out = tmp_path / "crm.filled.docx"
    parity = roundtrip(jinja, _CRM_FIX, _CRM_CONTRACT, out)
    assert parity.ok, f"mismatched={parity.mismatched} missing={parity.missing}"


# ---------------------------------------------------------------------------
# Committed-template-only (runs without any source .docx)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    not _LOW_JINJA.exists(), reason="committed Low jinja template absent"
)
def test_low_fill_from_committed_template(tmp_path):
    """Fill + re-extract using the checked-in jinja template — no blank needed."""
    out = tmp_path / "low.filled.docx"
    parity = roundtrip(_LOW_JINJA, _LOW_FIX, _LOW_CONTRACT, out)
    assert parity.ok, f"mismatched={parity.mismatched} missing={parity.missing}"
    assert parity.recovery_rate == 1.0


@pytest.mark.slow
@pytest.mark.skipif(
    not _CRM_JINJA.exists(), reason="committed CRM jinja template absent"
)
def test_crm_fill_from_committed_template(tmp_path):
    out = tmp_path / "crm.filled.docx"
    parity = roundtrip(_CRM_JINJA, _CRM_FIX, _CRM_CONTRACT, out)
    assert parity.ok, f"mismatched={parity.mismatched} missing={parity.missing}"


@pytest.mark.slow
@pytest.mark.skipif(
    not _MOD_JINJA.exists(), reason="committed Moderate jinja template absent"
)
def test_moderate_fill_from_committed_template(tmp_path):
    out = tmp_path / "mod.filled.docx"
    parity = roundtrip(_MOD_JINJA, _MOD_FIX, _MOD_CONTRACT, out)
    assert parity.ok, f"mismatched={parity.mismatched} missing={parity.missing}"
    assert parity.recovery_rate == 1.0


@pytest.mark.slow
@pytest.mark.skipif(
    not _HIGH_JINJA.exists(), reason="committed High jinja template absent"
)
def test_high_fill_from_committed_template(tmp_path):
    out = tmp_path / "high.filled.docx"
    parity = roundtrip(_HIGH_JINJA, _HIGH_FIX, _HIGH_CONTRACT, out)
    assert parity.ok, f"mismatched={parity.mismatched} missing={parity.missing}"
    assert parity.recovery_rate == 1.0


@pytest.mark.slow
@pytest.mark.parametrize(
    ("template", "contract"),
    [
        (_LOW_JINJA, _LOW_CONTRACT),
        (_MOD_JINJA, _MOD_CONTRACT),
        (_HIGH_JINJA, _HIGH_CONTRACT),
    ],
)
def test_committed_template_roundtrips_every_synthetic_contract_sentinel(
    tmp_path, template, contract
):
    """Every declared blank-template slot must render its synthetic sentinel."""
    if not template.exists():
        pytest.skip(f"committed Jinja template absent: {template.name}")
    context = synthetic_fill_context(contract)
    output = tmp_path / f"{template.stem}.synthetic.docx"

    fill_template(template, context, output, contract=contract)
    recovered = reextract_filled(output, contract)
    parity = check_parity(context, recovered)

    assert context
    assert all(
        value.startswith("[SYNTHETIC-SSPP-TEMPLATE-TEST]:")
        for value in context.values()
    )
    assert parity.ok, f"mismatched={parity.mismatched} missing={parity.missing}"
    assert parity.recovery_rate == 1.0


@pytest.mark.slow
@pytest.mark.skipif(
    not _MOD_BLANK.exists(), reason="Moderate blank not present locally"
)
def test_moderate_full_roundtrip_parity(tmp_path):
    jinja = tmp_path / "mod.jinja.docx"
    jinjafy(_MOD_BLANK, _MOD_CONTRACT, jinja)
    out = tmp_path / "mod.filled.docx"
    parity = roundtrip(jinja, _MOD_FIX, _MOD_CONTRACT, out)
    assert parity.ok, f"mismatched={parity.mismatched} missing={parity.missing}"


@pytest.mark.slow
@pytest.mark.skipif(not _HIGH_BLANK.exists(), reason="High blank not present locally")
def test_high_full_roundtrip_parity(tmp_path):
    jinja = tmp_path / "high.jinja.docx"
    jinjafy(_HIGH_BLANK, _HIGH_CONTRACT, jinja)
    out = tmp_path / "high.filled.docx"
    parity = roundtrip(jinja, _HIGH_FIX, _HIGH_CONTRACT, out)
    assert parity.ok, f"mismatched={parity.mismatched} missing={parity.missing}"
