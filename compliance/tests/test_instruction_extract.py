"""Tests for instruction extraction + hidden-markdown rendering (Phase R16.6).

Covers:
- extracting (instruction, content) pairs from a filled/blank docx via contract,
- rendering the instruction as hidden markdown (collapsed callout / html
  comment / plain / none),
- placement (instruction before content),
- round-trip strip of hidden instructions,
- configurable instruction stripping in the jinjafier output template.
"""

from __future__ import annotations

from pathlib import Path

import docx
import pytest
from docx.shared import RGBColor

from compliance.lib.docx_jinjafier import jinjafy, rendered_tags
from compliance.lib.heading_parser import load_contract
from compliance.lib.instruction_extract import (
    InstructionContentPair,
    RenderOptions,
    extract_instruction_content,
    render_extraction_markdown,
    render_hidden_instruction,
    render_pair_markdown,
    strip_hidden_instructions,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FISMATIC = _REPO_ROOT / "fismatic"
_LOW_BLANK = _FISMATIC / "Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx"
_LOW_CONTRACT = _REPO_ROOT / "compliance" / "datacontracts" / "low-sspp-roundtrip.yaml"

BLUE = RGBColor(0x00, 0x70, 0xC0)


def _mini_contract(tmp_path: Path, anchor: str, tag: str) -> Path:
    yaml_text = f"""
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
      - name: field
        logicalType: string
        customProperties:
          - property: captureStrategy
            value: heading_paragraph
          - property: templateTag
            value: {tag}
          - property: writeStrategy
            value: paragraph_after_instruction
          - property: instructionAnchor
            value: "{anchor}"
          - property: writeScope
            value: append
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
# Hidden-instruction rendering
# ---------------------------------------------------------------------------


class TestRenderHiddenInstruction:
    def test_collapsed_callout_default(self):
        out = render_hidden_instruction("Instruction: do X", RenderOptions())
        assert out.startswith("> [!note]- Instruction")
        assert "> Instruction: do X" in out

    def test_multiline_callout_quotes_each_line(self):
        out = render_hidden_instruction("line 1\nline 2", RenderOptions())
        assert "> line 1" in out
        assert "> line 2" in out

    def test_html_comment_style(self):
        out = render_hidden_instruction(
            "Instruction: do X", RenderOptions(hidden_style="html_comment")
        )
        assert out.startswith("<!-- Instruction:")
        assert out.rstrip().endswith("-->")

    def test_plain_style(self):
        out = render_hidden_instruction("do X", RenderOptions(hidden_style="plain"))
        assert out == "> do X"

    def test_none_style_is_empty(self):
        out = render_hidden_instruction("do X", RenderOptions(hidden_style="none"))
        assert out == ""

    def test_include_false_is_empty(self):
        out = render_hidden_instruction(
            "do X", RenderOptions(include_instructions=False)
        )
        assert out == ""

    def test_invalid_style_raises(self):
        with pytest.raises(ValueError):
            RenderOptions(hidden_style="bogus")

    def test_html_comment_escapes_terminator(self):
        out = render_hidden_instruction(
            "watch out --> here", RenderOptions(hidden_style="html_comment")
        )
        # The literal terminator must not appear mid-body.
        assert out.count("-->") == 1  # only the closing one


# ---------------------------------------------------------------------------
# Placement: instruction before content
# ---------------------------------------------------------------------------


class TestPairPlacement:
    def test_instruction_precedes_content(self):
        pair = InstructionContentPair(
            template_tag="t",
            heading="H",
            instruction_text="Instruction: describe",
            content="The actual content.",
        )
        md = render_pair_markdown(pair, RenderOptions())
        # Callout comes first, content after.
        callout_pos = md.index("[!note]-")
        content_pos = md.index("The actual content.")
        assert callout_pos < content_pos

    def test_content_only_when_disabled(self):
        pair = InstructionContentPair(
            template_tag="t",
            heading="H",
            instruction_text="Instruction: x",
            content="C",
        )
        md = render_pair_markdown(pair, RenderOptions(include_instructions=False))
        assert md == "C"


# ---------------------------------------------------------------------------
# Stripping
# ---------------------------------------------------------------------------


class TestStripHiddenInstructions:
    def test_strip_callout(self):
        md = "> [!note]- Instruction\n> do X\n> more\n\nReal content here."
        stripped = strip_hidden_instructions(md)
        assert "Instruction" not in stripped
        assert stripped == "Real content here."

    def test_strip_html_comment(self):
        md = "<!-- Instruction:\ndo X\n-->\n\nReal content."
        stripped = strip_hidden_instructions(md)
        assert "Instruction" not in stripped
        assert "Real content." in stripped

    def test_strip_is_noop_without_instructions(self):
        md = "Just content, no instructions."
        assert strip_hidden_instructions(md) == md


# ---------------------------------------------------------------------------
# Extraction from a synthetic docx
# ---------------------------------------------------------------------------


class TestExtractionSynthetic:
    def test_extract_pairs_instruction_and_content(self, tmp_path):
        d = docx.Document()
        d.add_heading("System", level=1)
        _blue(d, "Instruction: describe the system")
        d.add_paragraph("The system is a three-tier app.")
        src = tmp_path / "doc.docx"
        d.save(str(src))
        contract = load_contract(
            _mini_contract(
                tmp_path, anchor="Instruction: describe the system", tag="field"
            )
        )
        result = extract_instruction_content(src, contract)
        assert len(result.pairs) == 1
        pair = result.pairs[0]
        assert pair.template_tag == "field"
        assert pair.instruction_text.startswith("Instruction: describe the system")
        assert pair.content == "The system is a three-tier app."
        assert pair.source_locator.startswith("word/document.xml#body/p[")
        assert len(pair.source_hash) == 64

    def test_render_extraction_markdown_keyed_by_tag(self, tmp_path):
        d = docx.Document()
        d.add_heading("System", level=1)
        _blue(d, "Instruction: describe the system")
        d.add_paragraph("Content.")
        src = tmp_path / "doc.docx"
        d.save(str(src))
        contract = load_contract(
            _mini_contract(
                tmp_path, anchor="Instruction: describe the system", tag="field"
            )
        )
        result = extract_instruction_content(src, contract)
        md = render_extraction_markdown(result)
        assert "field" in md
        assert "[!note]- Instruction" in md["field"]
        assert "Content." in md["field"]
        assert "oscal-vault-instruction" in md["field"]
        assert "source_locator" in md["field"]
        assert "source_hash" in md["field"]


# ---------------------------------------------------------------------------
# Configurable stripping in the jinjafier output template
# ---------------------------------------------------------------------------


class TestJinjafierStripping:
    def test_strip_removes_instruction_keeps_tag(self, tmp_path):
        d = docx.Document()
        d.add_heading("System", level=1)
        _blue(d, "Instruction: describe the system fully")
        d.add_paragraph("placeholder")
        contract = _mini_contract(
            tmp_path, anchor="Instruction: describe the system", tag="field"
        )
        out = tmp_path / "stripped.docx"
        jinjafy(d, contract, out, strip_instructions=True)
        reopened = docx.Document(str(out))
        # Tag present, instruction gone.
        assert rendered_tags(out).get("field") == 1
        assert not any(
            "describe the system fully" in p.text for p in reopened.paragraphs
        )

    def test_keep_preserves_instruction(self, tmp_path):
        d = docx.Document()
        d.add_heading("System", level=1)
        _blue(d, "Instruction: describe the system fully")
        d.add_paragraph("placeholder")
        contract = _mini_contract(
            tmp_path, anchor="Instruction: describe the system", tag="field"
        )
        out = tmp_path / "kept.docx"
        jinjafy(d, contract, out, strip_instructions=False)
        reopened = docx.Document(str(out))
        assert any("describe the system fully" in p.text for p in reopened.paragraphs)
        assert rendered_tags(out).get("field") == 1


# ---------------------------------------------------------------------------
# Fixture-gated: real Low blank
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(not _LOW_BLANK.exists(), reason="Low blank not present locally")
def test_low_extraction_has_all_instructions():
    result = extract_instruction_content(_LOW_BLANK, _LOW_CONTRACT)
    tags = {p.template_tag for p in result.pairs}
    assert {"general_system_description", "network_architecture", "data_flow"} <= tags
    for p in result.pairs:
        assert p.instruction_text  # every pair carries its instruction


@pytest.mark.slow
@pytest.mark.skipif(not _LOW_BLANK.exists(), reason="Low blank not present locally")
def test_low_strip_instructions_output(tmp_path):
    out = tmp_path / "low.stripped.jinja.docx"
    jinjafy(_LOW_BLANK, _LOW_CONTRACT, out, strip_instructions=True)
    tags = rendered_tags(out)
    # All 4 tags still injected after stripping.
    assert len([t for t in tags if tags[t] >= 1]) == 4
    reopened = docx.Document(str(out))
    # The specific anchored instructions are removed.
    assert not any(
        "architecture diagram must fully" in p.text for p in reopened.paragraphs
    )
