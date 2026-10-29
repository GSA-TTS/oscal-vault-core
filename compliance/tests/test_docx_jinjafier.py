"""Tests for the docx jinjafier (Phase R16.3).

Synthetic tests build a small in-memory .docx + a matching in-memory Contract
and assert:
- tags are injected into the correct writable slot after the instruction,
- contiguous instruction blocks resolve to a single slot,
- ambiguous anchors fail closed (JinjafyError),
- anchors with no writable slot are reported (not silently dropped),
- every original instruction paragraph survives injection,
- the emitted template is docxtpl-renderable and each tag appears once.

Fixture-gated tests build the real Low + CRM jinja templates from the
committed contracts (skip when the blank .docx is absent).
"""

from __future__ import annotations

from pathlib import Path

import docx
import pytest
from docx.shared import RGBColor

from compliance.lib.docx_jinjafier import (
    JinjafyError,
    jinjafy,
    rendered_tags,
    resolve_bindings,
)
from compliance.lib.heading_parser import load_contract
from compliance.lib.instruction_anchors import find_instruction_anchors

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FISMATIC = _REPO_ROOT / "fismatic"
_LOW_BLANK = _FISMATIC / "Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx"
_CRM_BLANK = (
    _FISMATIC / "Leveraged-SaaS-CRM-SSPP-Template-[Contains-CUI]-02-01-2024.docx"
)
_LOW_CONTRACT = _REPO_ROOT / "compliance" / "datacontracts" / "low-sspp-roundtrip.yaml"
_CRM_CONTRACT = (
    _REPO_ROOT / "compliance" / "datacontracts" / "leveraged-saas-crm-roundtrip.yaml"
)

BLUE = RGBColor(0x00, 0x70, 0xC0)


# ---------------------------------------------------------------------------
# In-memory contract + docx builders
# ---------------------------------------------------------------------------


def _mini_contract(
    tmp_path: Path, *, anchor: str, tag: str, scope: str = "append"
) -> Path:
    """Write a minimal valid roundtrip contract with one write-back property."""
    yaml_text = f"""
apiVersion: v3.1.0
kind: DataContract
id: mini-roundtrip
name: mini_roundtrip
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
      - name: slot_field
        logicalType: string
        customProperties:
          - property: sourceHeading
            value: "H"
          - property: captureStrategy
            value: heading_paragraph
          - property: templateTag
            value: {tag}
          - property: writeStrategy
            value: paragraph_after_instruction
          - property: instructionAnchor
            value: "{anchor}"
          - property: writeScope
            value: {scope}
"""
    p = tmp_path / "mini.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    return p


def _blue(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.color.rgb = BLUE
    return p


# ---------------------------------------------------------------------------
# Binding resolution
# ---------------------------------------------------------------------------


class TestBindingResolution:
    def test_single_match_binds(self, tmp_path):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue(d, "Instruction: describe the thing")
        d.add_paragraph("existing slot text")
        contract = load_contract(
            _mini_contract(
                tmp_path, anchor="Instruction: describe the thing", tag="slot_field"
            )
        )
        anchors = find_instruction_anchors(d)
        bindings, unmatched, warnings = resolve_bindings(anchors, contract)
        assert len(bindings) == 1
        assert bindings[0].template_tag == "slot_field"
        assert unmatched == []

    def test_zero_match_is_unmatched_not_error(self, tmp_path):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue(d, "Instruction: something else entirely")
        d.add_paragraph("slot")
        contract = load_contract(
            _mini_contract(
                tmp_path, anchor="Instruction: no such text", tag="slot_field"
            )
        )
        anchors = find_instruction_anchors(d)
        bindings, unmatched, warnings = resolve_bindings(anchors, contract)
        assert bindings == []
        assert unmatched == ["slot_field"]

    def test_ambiguous_match_fails_closed(self, tmp_path):
        d = docx.Document()
        d.add_heading("H1", level=1)
        _blue(d, "Instruction: describe X")
        d.add_paragraph("slot 1")
        d.add_heading("H2", level=1)
        _blue(d, "Instruction: describe X")
        d.add_paragraph("slot 2")
        contract = load_contract(
            _mini_contract(tmp_path, anchor="Instruction: describe X", tag="slot_field")
        )
        anchors = find_instruction_anchors(d)
        with pytest.raises(JinjafyError):
            resolve_bindings(anchors, contract)


# ---------------------------------------------------------------------------
# Tag injection
# ---------------------------------------------------------------------------


class TestTagInjection:
    def test_append_scope_preserves_and_appends(self, tmp_path):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue(d, "Instruction: describe the thing")
        d.add_paragraph("Keep this sentence.")
        contract = _mini_contract(
            tmp_path,
            anchor="Instruction: describe the thing",
            tag="slot_field",
            scope="append",
        )
        out = tmp_path / "out.docx"
        result = jinjafy(d, contract, out)
        assert result.injected_count == 1
        tags = rendered_tags(out)
        assert tags.get("slot_field") == 1
        # The original sentence survives.
        reopened = docx.Document(str(out))
        texts = [p.text for p in reopened.paragraphs]
        assert any("Keep this sentence." in t and "slot_field" in t for t in texts)

    def test_replace_scope_replaces_slot_text(self, tmp_path):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue(d, "Instruction: describe the thing")
        d.add_paragraph("placeholder to be replaced")
        contract = _mini_contract(
            tmp_path,
            anchor="Instruction: describe the thing",
            tag="slot_field",
            scope="replace_slot",
        )
        out = tmp_path / "out.docx"
        jinjafy(d, contract, out)
        reopened = docx.Document(str(out))
        texts = [p.text for p in reopened.paragraphs]
        assert any(t.strip() == "{{ slot_field }}" for t in texts)
        assert not any("placeholder to be replaced" in t for t in texts)

    def test_instruction_paragraph_survives_injection(self, tmp_path):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue(d, "Instruction: describe the thing")
        d.add_paragraph("slot")
        contract = _mini_contract(
            tmp_path, anchor="Instruction: describe the thing", tag="slot_field"
        )
        out = tmp_path / "out.docx"
        jinjafy(d, contract, out)
        reopened = docx.Document(str(out))
        assert any(
            "Instruction: describe the thing" in p.text for p in reopened.paragraphs
        )

    def test_contiguous_block_resolves_to_single_slot(self, tmp_path):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue(d, "Instruction: describe the thing")
        _blue(d, "\u2022 bullet one")
        _blue(d, "\u2022 bullet two")
        d.add_paragraph("slot")
        contract = _mini_contract(
            tmp_path, anchor="Instruction: describe the thing", tag="slot_field"
        )
        out = tmp_path / "out.docx"
        result = jinjafy(d, contract, out)
        assert result.injected_count == 1
        assert rendered_tags(out).get("slot_field") == 1


# ---------------------------------------------------------------------------
# docxtpl renderability
# ---------------------------------------------------------------------------


class TestRenderable:
    def test_emitted_template_renders_with_docxtpl(self, tmp_path):
        from docxtpl import DocxTemplate

        d = docx.Document()
        d.add_heading("H", level=1)
        _blue(d, "Instruction: describe the thing")
        d.add_paragraph("slot")
        contract = _mini_contract(
            tmp_path,
            anchor="Instruction: describe the thing",
            tag="slot_field",
            scope="replace_slot",
        )
        out = tmp_path / "template.docx"
        jinjafy(d, contract, out)
        tpl = DocxTemplate(str(out))
        tpl.render({"slot_field": "RENDERED_VALUE"})
        rendered = tmp_path / "rendered.docx"
        tpl.save(str(rendered))
        reopened = docx.Document(str(rendered))
        assert any("RENDERED_VALUE" in p.text for p in reopened.paragraphs)


# ---------------------------------------------------------------------------
# Fixture-gated: real blank templates
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(not _LOW_BLANK.exists(), reason="Low blank not present locally")
def test_low_blank_jinjafies(tmp_path):
    out = tmp_path / "low.jinja.docx"
    result = jinjafy(_LOW_BLANK, _LOW_CONTRACT, out)
    # All 4 Low write-back tags should bind and inject exactly once.
    assert result.injected_count == 4
    tags = rendered_tags(out)
    for b in result.bindings:
        assert tags.get(b.template_tag) == 1
    # Instruction/heading/table counts preserved vs. blank.
    blank = docx.Document(str(_LOW_BLANK))
    emitted = docx.Document(str(out))
    assert len(emitted.tables) == len(blank.tables)


@pytest.mark.slow
@pytest.mark.skipif(not _CRM_BLANK.exists(), reason="CRM blank not present locally")
def test_crm_blank_jinjafies(tmp_path):
    out = tmp_path / "crm.jinja.docx"
    result = jinjafy(_CRM_BLANK, _CRM_CONTRACT, out)
    assert result.injected_count == 2
    tags = rendered_tags(out)
    for b in result.bindings:
        assert tags.get(b.template_tag) == 1
