"""Per-control authoring guidance extractor (R18).

The official GSA rev5 SSP blank templates carry two kinds of authoring
guidance that the vault control notes had been dropping:

1. **Section-level instruction blocks** — the blue (``#0070C0``) or
   ``Instruction:``/``Note:``-prefixed paragraphs detected by
   :mod:`compliance.lib.instruction_anchors`. Some of these immediately
   precede a control heading and explain how to fill that control family.

2. **Per-control CIS-table prompts** — inside every control-summary (CIS)
   table (see :mod:`compliance.lib.control_table_anchors`), row 3 is the
   ``"<CID> Describe how the control is implemented."`` prompt and each
   ``Part <key>`` row carries its own fill prompt.

This module walks the document in order, associates any contiguous
instruction block that appears *between* one control heading and the next
with the control it introduces, and also captures the CIS-table describe
prompt. The result is a ``{normalized_control_id: ControlGuidance}`` map that
:func:`compliance.lib.vault.build_control_note` renders as a collapsed
Obsidian callout so the guidance travels with each control note but stays
folded by default.

This is read-only extraction; it never mutates the document.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from compliance.lib.instruction_anchors import (
    DEFAULT_INSTRUCTION_COLORS,
    DEFAULT_INSTRUCTION_TEXT_PREFIXES,
    instruction_text_kind,
    is_instruction_colored,
)

# fismatic's normalize_control_id is the canonical id form the rest of the
# vault pipeline matches on; import it lazily-safely.
_FISMATIC_SRC = Path(__file__).resolve().parents[2] / "fismatic" / "src" / "phase_1"
if str(_FISMATIC_SRC) not in sys.path:
    sys.path.insert(0, str(_FISMATIC_SRC))

# A control-family heading like "AC-01: (Access Control) Policy and Procedures"
# or "AC-02: Account Management". Anchored, short, family-prefixed.
_CONTROL_HEADING_RE = re.compile(
    r"^([A-Z]{2})-0*(\d{1,3})(?:\(0*(\d+)\))?\s*[:\.]?",
    re.IGNORECASE,
)
_DESCRIBE_PROMPT_RE = re.compile(r"Describe how", re.IGNORECASE)


@dataclass
class ControlGuidance:
    """Authoring guidance recovered for one control."""

    control_id: str
    instruction_blocks: List[str] = field(default_factory=list)
    describe_prompt: Optional[str] = None

    def is_empty(self) -> bool:
        return not self.instruction_blocks and not self.describe_prompt


@dataclass
class SectionGuidance:
    """Authoring guidance recovered for one Chapters 1-12 front-matter section."""

    heading: str
    instruction_blocks: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.instruction_blocks


def _iter_body_blocks(doc: DocxDocument):
    body = doc.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield "paragraph", Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield "table", Table(child, doc)
        else:
            yield "other", child


def _is_instruction_para(para: Paragraph) -> bool:
    text = (para.text or "").strip()
    if not text:
        return False
    if instruction_text_kind(text, DEFAULT_INSTRUCTION_TEXT_PREFIXES) is not None:
        return True
    return is_instruction_colored(para, DEFAULT_INSTRUCTION_COLORS)


def _guidance_control_id(text: str) -> Optional[str]:
    """Return the template display form, e.g. ``AC-01(02)``.

    Guidance maps are rendered back into template-facing content, so they keep
    the official two-digit control and enhancement padding instead of the
    de-padded matching form used by fismatic internally.
    """
    match = _CONTROL_HEADING_RE.match((text or "").strip())
    if not match:
        return None
    family, number, enhancement = match.groups()
    control_id = f"{family.upper()}-{int(number):02d}"
    if enhancement is not None:
        control_id += f"({int(enhancement):02d})"
    return control_id


def _control_heading_id(text: str) -> Optional[str]:
    text = (text or "").strip()
    if not text or len(text) > 120:
        return None
    return _guidance_control_id(text)


# A section heading in the Chapters 1-12 front matter, either a Word "Heading N"
# style or a numbered heading like "9.2 General System Description".
_NUMBERED_HEADING_RE = re.compile(r"^\d+(\.\d+)*\s+\S")


def _section_heading_text(para: Paragraph) -> Optional[str]:
    """Return the heading text if this paragraph is a front-matter heading."""
    text = (para.text or "").strip()
    if not text or len(text) > 160:
        return None
    style = (para.style.name or "") if para.style is not None else ""
    if style.startswith("Heading") or style == "Title":
        return text
    if _NUMBERED_HEADING_RE.match(text):
        return text
    return None


def _table_describe_prompt(table: Table) -> Optional[str]:
    """Return row-3's 'Describe how...' prompt text if this is a CIS table."""
    if len(table.rows) < 4:
        return None
    try:
        row1 = table.rows[1].cells[0].text.strip()
    except Exception:
        return None
    if not row1.startswith("Implementation Status:"):
        return None
    describe = table.rows[3].cells[0].text.strip()
    return describe or None


def extract_control_guidance(
    source: Union[str, Path, DocxDocument],
) -> Dict[str, ControlGuidance]:
    """Build a ``{normalized_control_id: ControlGuidance}`` map from a template.

    Instruction blocks that appear between a control heading and the next
    control heading are attributed to the control the heading introduces. The
    CIS-table describe prompt is attached to the control whose id matches the
    table's row-0 heading.
    """
    doc = source if isinstance(source, DocxDocument) else Document(str(source))

    guidance: Dict[str, ControlGuidance] = {}
    current_id: Optional[str] = None
    pending_instr: List[str] = []

    def _flush_pending() -> None:
        if current_id and pending_instr:
            g = guidance.setdefault(current_id, ControlGuidance(control_id=current_id))
            for block in pending_instr:
                if block and block not in g.instruction_blocks:
                    g.instruction_blocks.append(block)
        pending_instr.clear()

    for kind, obj in _iter_body_blocks(doc):
        if kind == "paragraph":
            text = (obj.text or "").strip()
            if not text:
                continue
            heading_id = _control_heading_id(text)
            if heading_id is not None:
                # New control section starts; instructions seen since the last
                # heading belong to the *previous* control (post-heading
                # guidance), so flush them first, then switch context.
                _flush_pending()
                current_id = heading_id
                guidance.setdefault(current_id, ControlGuidance(control_id=current_id))
                continue
            if _is_instruction_para(obj):
                pending_instr.append(text)
        elif kind == "table":
            _flush_pending()
            prompt = _table_describe_prompt(obj)
            if prompt:
                tid = _guidance_control_id(obj.rows[0].cells[0].text.strip())
                if tid:
                    g = guidance.setdefault(tid, ControlGuidance(control_id=tid))
                    if not g.describe_prompt:
                        g.describe_prompt = prompt

    _flush_pending()
    return {cid: g for cid, g in guidance.items() if not g.is_empty()}


def extract_section_guidance(
    source: Union[str, Path, DocxDocument],
) -> Dict[str, SectionGuidance]:
    """Recover Chapters 1-12 front-matter authoring guidance keyed by section.

    Walks the document body up to the first control heading (the Chapter 13
    control section, handled by :func:`extract_control_guidance`) and groups
    every instruction paragraph (blue ``#0070C0`` / ``Instruction:`` prefixed)
    under its nearest preceding section heading. The result is a
    ``{heading_text: SectionGuidance}`` map that
    :func:`compliance.lib.vault.build_narrative_notes` /
    ``enrich_system_note_from_narrative`` render as folded callouts on the
    corresponding section note.
    """
    doc = source if isinstance(source, DocxDocument) else Document(str(source))

    guidance: Dict[str, SectionGuidance] = {}
    current_heading: Optional[str] = None

    for kind, obj in _iter_body_blocks(doc):
        if kind != "paragraph":
            continue
        text = (obj.text or "").strip()
        if not text:
            continue
        # Stop at the control section; that guidance is control-keyed instead.
        if _control_heading_id(text) is not None:
            break
        heading = _section_heading_text(obj)
        if heading is not None:
            current_heading = heading
            continue
        if current_heading is None:
            continue
        if _is_instruction_para(obj):
            g = guidance.setdefault(
                current_heading, SectionGuidance(heading=current_heading)
            )
            if text not in g.instruction_blocks:
                g.instruction_blocks.append(text)

    return {h: g for h, g in guidance.items() if not g.is_empty()}


def _normalize_heading_key(text: str) -> str:
    """Lowercase, strip leading section numbers/punctuation for fuzzy matching."""
    text = re.sub(r"^\d+(\.\d+)*\s*", "", (text or "").strip())
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def render_section_guidance_callout(guidance: SectionGuidance) -> List[str]:
    """Render :class:`SectionGuidance` as folded-callout markdown lines."""
    if guidance.is_empty():
        return []
    lines: List[str] = ["> [!note]- Template guidance"]
    for block in guidance.instruction_blocks:
        for ln in block.split("\n"):
            lines.append(f"> {ln}")
    return lines


def render_guidance_callout(guidance: ControlGuidance) -> List[str]:
    """Render a :class:`ControlGuidance` as collapsed-callout markdown lines.

    Matches the R16.6 hidden-instruction style (``> [!note]- ...`` folded by
    default) so template authoring guidance travels with the control note
    without cluttering it.
    """
    if guidance.is_empty():
        return []
    lines: List[str] = ["> [!note]- Template guidance"]
    for block in guidance.instruction_blocks:
        for ln in block.split("\n"):
            lines.append(f"> {ln}")
    if guidance.describe_prompt:
        if guidance.instruction_blocks:
            lines.append(">")
        lines.append(f"> {guidance.describe_prompt}")
    return lines
