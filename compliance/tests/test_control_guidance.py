"""Tests for per-control authoring-guidance extraction (R18).

Covers:
- synthetic detection of control-heading / instruction-block association,
- CIS-table "Describe how..." prompt capture,
- collapsed-callout rendering,
- fixture-gated full-corpus extraction against the committed blank templates.
"""

from __future__ import annotations

from pathlib import Path

import docx
import pytest
from docx.shared import RGBColor

from compliance.lib.control_guidance import (
    ControlGuidance,
    SectionGuidance,
    extract_control_guidance,
    extract_section_guidance,
    render_guidance_callout,
    render_section_guidance_callout,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO_ROOT / "templates"
_LOW = _TEMPLATES / "Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx"
_MOD = _TEMPLATES / "Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx"
_HIGH = _TEMPLATES / "High-SSPP-Template-[Contains-CUI]-10-16-2024.docx"

BLUE = RGBColor(0x00, 0x70, 0xC0)


def _add_cis_table(doc: docx.document.Document, control_id: str, parts: int) -> None:
    """Append a minimal CIS-shaped table for ``control_id``."""
    rows = 4 + parts
    t = doc.add_table(rows=rows, cols=2)
    t.rows[0].cells[0].text = control_id
    t.rows[1].cells[0].text = "Implementation Status:\n\u2612 Implemented"
    t.rows[2].cells[0].text = "Control Origination:\n\u2612 System Specific Control"
    t.rows[3].cells[0].text = f"{control_id} Describe how the control is implemented."
    for i in range(parts):
        t.rows[4 + i].cells[0].text = f"Part {chr(ord('a') + i)}"


class TestSyntheticDetection:
    def test_blue_instruction_before_heading_attaches_to_control(self, tmp_path):
        doc = docx.Document()
        h = doc.add_paragraph("AC-01: (Access Control) Policy and Procedures")
        h.style = doc.styles["Heading 2"]
        p = doc.add_paragraph()
        run = p.add_run("Instruction: describe your access control policy.")
        run.font.color.rgb = BLUE
        _add_cis_table(doc, "AC-01", parts=2)
        out = tmp_path / "mini.docx"
        doc.save(str(out))

        g = extract_control_guidance(out)
        assert "AC-01" in g
        assert any(
            "describe your access control policy" in b
            for b in g["AC-01"].instruction_blocks
        )
        assert g["AC-01"].describe_prompt.startswith("AC-01 Describe how")

    def test_describe_prompt_captured_without_instruction(self, tmp_path):
        doc = docx.Document()
        h = doc.add_paragraph("AU-02: Event Logging")
        h.style = doc.styles["Heading 2"]
        _add_cis_table(doc, "AU-02", parts=0)
        out = tmp_path / "mini.docx"
        doc.save(str(out))

        g = extract_control_guidance(out)
        assert "AU-02" in g
        assert g["AU-02"].describe_prompt.startswith("AU-02 Describe how")
        assert g["AU-02"].instruction_blocks == []

    def test_non_instruction_text_ignored(self, tmp_path):
        doc = docx.Document()
        h = doc.add_paragraph("AC-03: Access Enforcement")
        h.style = doc.styles["Heading 2"]
        doc.add_paragraph("Ordinary black narrative text, not guidance.")
        _add_cis_table(doc, "AC-03", parts=1)
        out = tmp_path / "mini.docx"
        doc.save(str(out))

        g = extract_control_guidance(out)
        assert g["AC-03"].instruction_blocks == []


class TestRendering:
    def test_callout_is_collapsed_and_folded(self):
        g = ControlGuidance(
            control_id="AC-01",
            instruction_blocks=["Instruction: do the thing.\nSecond line."],
            describe_prompt="AC-01 Describe how the control is implemented.",
        )
        lines = render_guidance_callout(g)
        assert lines[0] == "> [!note]- Template guidance"
        assert "> Instruction: do the thing." in lines
        assert "> Second line." in lines
        assert lines[-1] == "> AC-01 Describe how the control is implemented."

    def test_empty_guidance_renders_nothing(self):
        assert render_guidance_callout(ControlGuidance(control_id="X")) == []


class TestSectionGuidance:
    def test_instruction_grouped_under_preceding_heading(self, tmp_path):
        doc = docx.Document()
        h = doc.add_paragraph("Network Architecture")
        h.style = doc.styles["Heading 2"]
        para = doc.add_paragraph()
        run = para.add_run("Instruction: describe the network architecture.")
        run.font.color.rgb = BLUE
        # A control heading terminates the front-matter scan.
        ch = doc.add_paragraph("AC-01: Access Control Policy and Procedures")
        ch.style = doc.styles["Heading 2"]
        cp = doc.add_paragraph()
        cp.add_run(
            "Instruction: this belongs to the control section."
        ).font.color.rgb = BLUE
        out = tmp_path / "sections.docx"
        doc.save(str(out))

        sg = extract_section_guidance(out)
        assert "Network Architecture" in sg
        assert any(
            "describe the network architecture" in b
            for b in sg["Network Architecture"].instruction_blocks
        )
        # Control-section instruction must not leak into front-matter guidance.
        assert all(
            "belongs to the control section" not in b
            for g in sg.values()
            for b in g.instruction_blocks
        )

    def test_instruction_before_any_heading_ignored(self, tmp_path):
        doc = docx.Document()
        p = doc.add_paragraph()
        p.add_run("Instruction: orphan before any heading.").font.color.rgb = BLUE
        out = tmp_path / "orphan.docx"
        doc.save(str(out))
        assert extract_section_guidance(out) == {}

    def test_section_callout_folded(self):
        g = SectionGuidance(
            heading="Network Architecture",
            instruction_blocks=["Instruction: do the thing.\nSecond line."],
        )
        lines = render_section_guidance_callout(g)
        assert lines[0] == "> [!note]- Template guidance"
        assert "> Instruction: do the thing." in lines
        assert "> Second line." in lines

    def test_empty_section_guidance_renders_nothing(self):
        assert render_section_guidance_callout(SectionGuidance(heading="X")) == []


@pytest.mark.skipif(not _LOW.exists(), reason="blank Low template not present")
def test_full_corpus_low():
    g = extract_control_guidance(_LOW)
    # Every CIS table should yield a describe prompt.
    with_desc = [v for v in g.values() if v.describe_prompt]
    assert len(with_desc) >= 150
    # A representative control with a known blue instruction block.
    assert "AC-02" in g and g["AC-02"].instruction_blocks


@pytest.mark.skipif(
    not (_MOD.exists() and _HIGH.exists()),
    reason="blank Moderate/High templates not present",
)
def test_full_corpus_moderate_high():
    for tpl, floor in ((_MOD, 300), (_HIGH, 400)):
        g = extract_control_guidance(tpl)
        with_desc = [v for v in g.values() if v.describe_prompt]
        assert len(with_desc) >= floor


@pytest.mark.skipif(not _MOD.exists(), reason="blank Moderate template not present")
def test_full_corpus_section_guidance_moderate():
    sg = extract_section_guidance(_MOD)
    # The Chapters 1-12 front matter carries dozens of instruction sections.
    assert len(sg) >= 40
    # A representative section with known guidance.
    keys = {k.lower() for k in sg}
    assert any("network architecture" in k for k in keys)
