"""Tests for the color-aware instruction-anchor detector (Phase R16.1).

Two tiers:

1. **Synthetic** (always run): build small ``.docx`` documents in memory with
   known blue instruction runs + writable slots and assert the detector finds
   them, classifies detection method + slot kind, collapses contiguous blocks,
   and honors per-template color/prefix configuration.

2. **Fixture-gated** (skip when the .docx is absent): run against the real
   official blank templates under ``fismatic/`` and assert the counts match
   the probe evidence in ``archive/plans/ROUNDTRIP_TEMPLATE_PLAN.md`` within tolerance.
   These skip gracefully because ``*.docx`` is gitignored (ADR-009).
"""

from __future__ import annotations

from pathlib import Path

import docx
import pytest
from docx.shared import RGBColor

from compliance.lib.instruction_anchors import (
    DEFAULT_INSTRUCTION_COLORS,
    find_instruction_anchors,
    instruction_text_kind,
    is_instruction_colored,
    summarize_anchors,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FISMATIC = _REPO_ROOT / "fismatic"

BLUE = RGBColor(0x00, 0x70, 0xC0)  # #0070C0
CRM_BLUE_1 = RGBColor(0x1F, 0x49, 0x7D)  # #1F497D
CRM_BLUE_2 = RGBColor(0x36, 0x60, 0x91)  # #366091
BLACK = RGBColor(0x00, 0x00, 0x00)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _blue_para(doc, text, color=BLUE):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.color.rgb = color
    return p


def _black_para(doc, text):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.color.rgb = BLACK
    return p


# ---------------------------------------------------------------------------
# Unit: classification primitives
# ---------------------------------------------------------------------------


class TestClassificationPrimitives:
    def test_instruction_text_kind_variants(self):
        assert (
            instruction_text_kind("Instruction: do X", ("instruction",))
            == "instruction"
        )
        assert (
            instruction_text_kind("Instructions: do X", ("instructions",))
            == "instruction"
        )
        assert instruction_text_kind("Note: careful", ("note",)) == "note"
        assert instruction_text_kind("Example: GSA-A", ("example",)) == "example"

    def test_instruction_text_kind_no_false_positive(self):
        # "notebook" must not match the "note" prefix.
        assert instruction_text_kind("Notebook computers are assets", ("note",)) is None
        assert (
            instruction_text_kind("Regular narrative text", ("instruction", "note"))
            is None
        )
        assert instruction_text_kind("Note the setting", ("note",)) is None
        assert instruction_text_kind("Examples include A", ("example",)) is None

    def test_is_instruction_colored_all_blue(self):
        d = docx.Document()
        p = _blue_para(d, "Instruction: fill this in")
        assert is_instruction_colored(p, {"0070C0"}) is True

    def test_is_instruction_colored_black_is_not(self):
        d = docx.Document()
        p = _black_para(d, "Real content")
        assert is_instruction_colored(p, {"0070C0"}) is False

    def test_is_instruction_colored_mixed_is_not(self):
        # One incidental blue word inside an otherwise-black paragraph.
        d = docx.Document()
        p = d.add_paragraph()
        r1 = p.add_run("mostly black ")
        r1.font.color.rgb = BLACK
        r2 = p.add_run("one blue word")
        r2.font.color.rgb = BLUE
        assert is_instruction_colored(p, {"0070C0"}) is False

    def test_is_instruction_colored_wrong_color_set(self):
        d = docx.Document()
        p = _blue_para(d, "Instruction", color=CRM_BLUE_1)
        assert is_instruction_colored(p, {"0070C0"}) is False
        assert is_instruction_colored(p, {"1F497D"}) is True

    def test_is_instruction_colored_from_inherited_paragraph_style(self):
        d = docx.Document()
        style = d.styles.add_style("Template Guidance", 1)
        style.font.color.rgb = BLUE
        paragraph = d.add_paragraph("Inherited blue guidance")
        paragraph.style = style
        assert is_instruction_colored(paragraph, {"0070C0"}) is True


# ---------------------------------------------------------------------------
# Detection: paragraph slot
# ---------------------------------------------------------------------------


class TestParagraphSlotDetection:
    def test_instruction_then_paragraph_slot(self):
        d = docx.Document()
        d.add_heading("General System Description", level=1)
        _blue_para(d, "Instructions: describe the system.")
        _black_para(d, "")  # the writable (empty) slot
        anchors = find_instruction_anchors(d)
        assert len(anchors) == 1
        a = anchors[0]
        assert a.instruction_kind == "instruction"
        assert a.heading == "General System Description"
        assert a.heading_level == 1
        assert a.slot_kind == "paragraph"
        assert a.has_slot is True

    def test_detection_method_color_only(self):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue_para(d, "Provide a narrative without a prefix word")
        _black_para(d, "slot")
        anchors = find_instruction_anchors(d)
        assert len(anchors) == 1
        assert anchors[0].detected_by == "color"

    def test_detection_method_text_only(self):
        # Black text but starts with "Instruction" — still detected via text.
        d = docx.Document()
        d.add_heading("H", level=1)
        _black_para(d, "Instruction: this is black but prefixed")
        _black_para(d, "slot")
        anchors = find_instruction_anchors(d)
        assert len(anchors) == 1
        assert anchors[0].detected_by == "text"

    def test_detection_method_color_and_text(self):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue_para(d, "Instructions: blue and prefixed")
        _black_para(d, "slot")
        anchors = find_instruction_anchors(d)
        assert anchors[0].detected_by == "color+text"


# ---------------------------------------------------------------------------
# Detection: table slot + heading (no-slot) case
# ---------------------------------------------------------------------------


class TestTableAndHeadingSlots:
    def test_instruction_then_table_slot(self):
        d = docx.Document()
        d.add_heading("Information Types", level=2)
        _blue_para(d, "Instructions: complete the FIPS 199 table.")
        d.add_table(rows=2, cols=2)
        anchors = find_instruction_anchors(d)
        assert len(anchors) == 1
        assert anchors[0].slot_kind == "table"
        assert anchors[0].has_slot is True

    def test_instruction_then_heading_is_no_writable_slot(self):
        d = docx.Document()
        d.add_heading("Guidance", level=1)
        _blue_para(d, "Instruction: this guidance section is informational.")
        d.add_heading("Next Section", level=1)
        _black_para(d, "content")
        anchors = find_instruction_anchors(d)
        assert len(anchors) == 1
        assert anchors[0].slot_kind == "heading"
        assert anchors[0].has_slot is False


# ---------------------------------------------------------------------------
# Contiguous block collapse
# ---------------------------------------------------------------------------


class TestContiguousBlockCollapse:
    def test_multiple_blue_paras_collapse_to_one_anchor(self):
        d = docx.Document()
        d.add_heading("General System Description", level=1)
        _blue_para(d, "Instructions: the description must include:")
        _blue_para(d, "\u2022 How the system is secure")
        _blue_para(d, "\u2022 The who/what/when/where")
        _black_para(d, "The system is ...")  # slot
        anchors = find_instruction_anchors(d)
        assert len(anchors) == 1
        assert "How the system is secure" in anchors[0].instruction_text
        assert "who/what/when/where" in anchors[0].instruction_text
        assert anchors[0].slot_kind == "paragraph"

    def test_two_separate_blocks_yield_two_anchors(self):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue_para(d, "Instruction: first")
        _black_para(d, "first slot")
        _blue_para(d, "Instruction: second")
        _black_para(d, "second slot")
        anchors = find_instruction_anchors(d)
        assert len(anchors) == 2


# ---------------------------------------------------------------------------
# Per-template color configuration (CRM uses other blues)
# ---------------------------------------------------------------------------


class TestConfigurableColors:
    def test_crm_blues_detected_when_configured(self):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue_para(d, "Instruction: CRM guidance", color=CRM_BLUE_1)
        _black_para(d, "slot")
        # Default color set (0070C0) still catches it via TEXT prefix.
        default_anchors = find_instruction_anchors(d)
        assert len(default_anchors) == 1
        assert default_anchors[0].detected_by == "text"
        # With CRM color configured, it is detected by color+text.
        crm_anchors = find_instruction_anchors(
            d, instruction_colors=["1F497D", "366091", "0070C0"]
        )
        assert len(crm_anchors) == 1
        assert crm_anchors[0].detected_by == "color+text"

    def test_hash_prefixed_colors_normalized(self):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue_para(d, "unprefixed blue narrative", color=CRM_BLUE_2)
        _black_para(d, "slot")
        anchors = find_instruction_anchors(d, instruction_colors=["#366091"])
        assert len(anchors) == 1
        assert anchors[0].detected_by == "color"

    def test_default_colors_constant(self):
        assert DEFAULT_INSTRUCTION_COLORS == {"0070C0"}


# ---------------------------------------------------------------------------
# Summary helper
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summarize_counts(self):
        d = docx.Document()
        d.add_heading("H", level=1)
        _blue_para(d, "Instruction: para slot")
        _black_para(d, "slot")
        _blue_para(d, "Instructions: table slot")
        d.add_table(rows=1, cols=1)
        anchors = find_instruction_anchors(d)
        summary = summarize_anchors(anchors)
        assert summary["total"] == 2
        assert summary["with_slot"] == 2
        assert summary["slot_kind"].get("paragraph") == 1
        assert summary["slot_kind"].get("table") == 1


# ---------------------------------------------------------------------------
# Fixture-gated: real official blank templates
# ---------------------------------------------------------------------------

_LOW = _FISMATIC / "Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx"
_CRM = _FISMATIC / "Leveraged-SaaS-CRM-SSPP-Template-[Contains-CUI]-02-01-2024.docx"


@pytest.mark.slow
@pytest.mark.skipif(not _LOW.exists(), reason="Low blank template not present locally")
def test_low_template_instruction_count():
    """Low template: ~65 prefixed instruction blocks, most with a writable slot."""
    anchors = find_instruction_anchors(_LOW)
    # Probe evidence: 65 instruction-prefixed blue blocks. Detector collapses
    # contiguous blocks and also catches color-only blocks, so allow a range.
    prefixed = [
        a for a in anchors if a.instruction_kind in ("instruction", "note", "example")
    ]
    assert len(prefixed) >= 40, f"expected many instruction blocks, got {len(prefixed)}"
    # The overwhelming majority should resolve to a paragraph or table slot.
    with_slot = sum(1 for a in anchors if a.has_slot)
    assert with_slot >= len(anchors) * 0.6


@pytest.mark.slow
@pytest.mark.skipif(not _CRM.exists(), reason="CRM blank template not present locally")
def test_crm_template_requires_extra_colors():
    """CRM template uses #1F497D / #366091 blues, not only #0070C0."""
    # With default color only, text-prefixed instructions are still found.
    default_anchors = find_instruction_anchors(_CRM)
    assert len(default_anchors) >= 5
    # With the CRM palette configured, at least some blocks are detected by color.
    crm_anchors = find_instruction_anchors(
        _CRM, instruction_colors=["0070C0", "1F497D", "366091"]
    )
    by_color = [a for a in crm_anchors if "color" in a.detected_by]
    assert by_color, "expected color-detected instruction blocks with CRM palette"
