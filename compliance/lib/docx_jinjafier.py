"""Jinjafier: convert an official FedRAMP SSP blank .docx into a docxtpl
template (Phase R16.3).

Takes a blank SSP template + its roundtrip datacontract and injects Jinja2
``{{ tag }}`` placeholders into the **writable slot** immediately after each
instruction block the contract references (via ``instructionAnchor``). The
result is a docxtpl-renderable ``.docx`` that preserves *all* of the original
document — headings, instruction/example blocks, styling, tables — so the
emitted template still teaches an author while being machine-fillable.

This is the inverse-preparation step of the roundtrip: the contract's read
metadata (``sourceHeading``/``captureStrategy``) drives extraction, and its
write metadata (``templateTag``/``writeStrategy``/``instructionAnchor``) drives
this jinjafication + the later fill (``docx_fill.py``).

Fail-closed policy (§14.5): a contract ``templateTag`` that matches **zero** or
**more than one** instruction anchor is a hard error — the jinjafier refuses
to guess which slot to tag.

References
----------
- ``compliance/lib/instruction_anchors.py`` (R16.1 — anchor detection)
- ``compliance/lib/heading_parser.py`` (contract loading)
- ``archive/plans/ROUNDTRIP_TEMPLATE_PLAN.md`` §4.4
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Union

from docx import Document
from docx.document import Document as DocxDocument

from compliance.lib.heading_parser import Contract, PropertyMapping, load_contract
from compliance.lib.instruction_anchors import (
    InstructionAnchor,
    find_instruction_anchors,
)

# Table-cell write strategies get a tag injected into a specific cell; for the
# roundtrip demo we support paragraph slots (the common case) fully and expose
# the table case for future extension.
_PARAGRAPH_WRITE_STRATEGIES = {"paragraph_after_instruction"}
_TABLE_WRITE_STRATEGIES = {"table_after_instruction"}


class JinjafyError(RuntimeError):
    """Raised when a contract slot cannot be unambiguously placed."""


@dataclass
class SlotBinding:
    """A resolved binding between a contract property and an instruction anchor."""

    template_tag: str
    write_strategy: str
    write_scope: str
    instruction_anchor: str
    anchor: InstructionAnchor


@dataclass
class JinjafyResult:
    """Outcome of a jinjafication run."""

    output_path: Path
    bindings: List[SlotBinding] = field(default_factory=list)
    unmatched_tags: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def injected_count(self) -> int:
        return len(self.bindings)


# ---------------------------------------------------------------------------
# Anchor matching
# ---------------------------------------------------------------------------


def _anchor_matches(anchor: InstructionAnchor, pattern: str) -> bool:
    """Match an instruction anchor's text against a contract pattern.

    Supports plain prefix (case-insensitive, whitespace-normalized) and a
    ``regex:`` prefix. The instruction text in the .docx frequently has
    trailing detail, so we match on prefix / substring semantics.
    """
    text = re.sub(r"\s+", " ", anchor.instruction_text).strip().lower()
    pat = pattern.strip()
    if pat.startswith("regex:"):
        try:
            return (
                re.search(pat[len("regex:") :], anchor.instruction_text, re.I)
                is not None
            )
        except re.error:
            return False
    pat_norm = re.sub(r"\s+", " ", pat).strip().lower()
    return text.startswith(pat_norm) or pat_norm in text


def _writeback_properties(contract: Contract) -> List[PropertyMapping]:
    """Return all destination properties that declare a templateTag."""
    props: List[PropertyMapping] = []
    for obj in contract.destinations():
        for p in obj.properties:
            if p.template_tag:
                props.append(p)
    return props


def _instruction_config(contract: Contract) -> Tuple[List[str], List[str]]:
    """Extract per-template instructionColors + text prefixes from the source object.

    Returns ``(colors, prefixes)``; either may be empty (caller falls back to
    the detector defaults).
    """
    colors: List[str] = []
    prefixes: List[str] = []
    for src_id in contract.source_object_ids:
        obj = contract.objects_by_id.get(src_id)
        if obj is None:
            continue
        # customProperties are on the raw schema object; re-read them.
        for raw_obj in contract.raw.get("schema", []):
            if (raw_obj.get("id") or raw_obj.get("name")) != src_id:
                continue
            for cp in raw_obj.get("customProperties") or []:
                if cp.get("property") == "instructionColors" and cp.get("value"):
                    colors = [
                        c.strip() for c in str(cp["value"]).split(",") if c.strip()
                    ]
                if cp.get("property") == "instructionTextPrefixes" and cp.get("value"):
                    prefixes = [
                        c.strip() for c in str(cp["value"]).split(",") if c.strip()
                    ]
    return colors, prefixes


def resolve_bindings(
    anchors: List[InstructionAnchor],
    contract: Contract,
) -> Tuple[List[SlotBinding], List[str], List[str]]:
    """Bind each contract templateTag to exactly one instruction anchor.

    Returns ``(bindings, unmatched_tags, warnings)``.

    Fail-closed: raises :class:`JinjafyError` if a templateTag matches more
    than one anchor (ambiguous). A templateTag that matches zero anchors is
    reported in ``unmatched_tags`` (non-fatal — the tag simply won't appear
    in the template, and the fill stage will note it).
    """
    bindings: List[SlotBinding] = []
    unmatched: List[str] = []
    warnings: List[str] = []

    for prop in _writeback_properties(contract):
        if not prop.instruction_anchor:
            warnings.append(
                f"{prop.name}: templateTag set but no instructionAnchor; skipped"
            )
            continue
        matches = [a for a in anchors if _anchor_matches(a, prop.instruction_anchor)]
        # Prefer anchors that actually have a writable paragraph/table slot.
        slot_matches = [a for a in matches if a.has_slot]
        chosen = slot_matches or matches

        if len(chosen) == 0:
            unmatched.append(prop.template_tag)
            continue
        if len(chosen) > 1:
            raise JinjafyError(
                f"templateTag {prop.template_tag!r} (anchor "
                f"{prop.instruction_anchor!r}) matched {len(chosen)} instruction "
                f"blocks; refusing to guess. Tighten the instructionAnchor."
            )

        anchor = chosen[0]
        if not anchor.has_slot:
            warnings.append(
                f"{prop.template_tag}: matched anchor has no writable slot "
                f"(slot_kind={anchor.slot_kind}); tag not injected"
            )
            unmatched.append(prop.template_tag)
            continue

        bindings.append(
            SlotBinding(
                template_tag=prop.template_tag,
                write_strategy=prop.write_strategy or "paragraph_after_instruction",
                write_scope=prop.write_scope or "replace_slot",
                instruction_anchor=prop.instruction_anchor,
                anchor=anchor,
            )
        )

    return bindings, unmatched, warnings


# ---------------------------------------------------------------------------
# Tag injection
# ---------------------------------------------------------------------------


def _inject_paragraph_tag(binding: SlotBinding) -> None:
    """Inject a Jinja ``{{ tag }}`` into the writable paragraph slot.

    ``replace_slot`` clears the paragraph's existing runs and writes the tag
    (the placeholder text is placeholder-only). ``append`` keeps the existing
    text and appends the tag as a new run (used when the slot carries a real
    template sentence we want to preserve alongside the fill).
    """
    para = binding.anchor.slot_ref
    tag = f"{{{{ {binding.template_tag} }}}}"
    if binding.write_scope == "append":
        para.add_run((" " if (para.text or "").strip() else "") + tag)
    else:  # replace_slot
        for r in list(para.runs):
            r.text = ""
        if para.runs:
            para.runs[0].text = tag
        else:
            para.add_run(tag)


def _inject_table_tag(binding: SlotBinding) -> None:
    """Inject a Jinja tag into the first data cell of the writable table slot."""
    table = binding.anchor.slot_ref
    tag = f"{{{{ {binding.template_tag} }}}}"
    # Choose the last cell of the last row as the fill target (data cell).
    if not table.rows:
        return
    cell = table.rows[-1].cells[-1]
    if binding.write_scope == "append":
        cell.add_paragraph(tag)
    else:
        cell.text = tag


def _delete_paragraph(paragraph) -> None:
    """Remove a python-docx Paragraph from its parent body."""
    el = paragraph._element
    parent = el.getparent()
    if parent is not None:
        parent.remove(el)


def _instruction_paragraph_elements(anchor) -> list:
    """Return the XML elements of the instruction block's paragraphs.

    Captured *before* any deletion so multiple strips don't invalidate each
    other's indices. The block spans document-order items
    ``[instruction_body_index, body_index)`` (the slot is excluded).
    """
    from compliance.lib.instruction_anchors import _iter_body_blocks

    if anchor.slot_ref is None:
        return []
    doc = anchor.slot_ref.part.document
    blocks = list(_iter_body_blocks(doc))
    start = anchor.instruction_body_index
    end = anchor.body_index if anchor.body_index >= 0 else len(blocks)
    els = []
    for kind, obj in blocks[start:end]:
        if kind == "paragraph":
            els.append(obj._element)
    return els


def jinjafy(
    blank_docx: Union[str, Path, DocxDocument],
    contract: Union[str, Path, Contract],
    output_path: Union[str, Path],
    *,
    strip_instructions: bool = False,
) -> JinjafyResult:
    """Convert a blank SSP .docx into a docxtpl template using a contract.

    Parameters
    ----------
    blank_docx:
        Path to the official blank .docx, or an open ``Document``.
    contract:
        Path to the roundtrip datacontract, or a loaded ``Contract``.
    output_path:
        Where to write the jinjafied ``.docx``.
    strip_instructions:
        When ``True``, the instruction block preceding each injected tag is
        **removed** from the output template (a clean, author-facing fill
        target). When ``False`` (default), instructions are preserved so the
        emitted template still teaches the author. Configurable per the R16.6
        requirement.

    Returns
    -------
    JinjafyResult
    """
    doc = (
        blank_docx
        if isinstance(blank_docx, DocxDocument)
        else Document(str(blank_docx))
    )
    ctr = contract if isinstance(contract, Contract) else load_contract(contract)

    colors, prefixes = _instruction_config(ctr)
    anchors = find_instruction_anchors(
        doc,
        instruction_colors=colors or None,
        instruction_text_prefixes=prefixes or None,
    )

    bindings, unmatched, warnings = resolve_bindings(anchors, ctr)

    # Capture instruction-paragraph elements up front (before any mutation) so
    # stripping is index-independent and safe across multiple bindings.
    strip_targets: list = []
    if strip_instructions:
        for binding in bindings:
            strip_targets.extend(_instruction_paragraph_elements(binding.anchor))

    for binding in bindings:
        if binding.write_strategy in _PARAGRAPH_WRITE_STRATEGIES:
            _inject_paragraph_tag(binding)
        elif binding.write_strategy in _TABLE_WRITE_STRATEGIES:
            _inject_table_tag(binding)
        else:
            warnings.append(
                f"{binding.template_tag}: unknown writeStrategy "
                f"{binding.write_strategy!r}; skipped"
            )

    # Strip instruction paragraphs by element identity (never the slot itself).
    if strip_instructions:
        slot_els = {
            b.anchor.slot_ref._element
            for b in bindings
            if b.anchor.slot_ref is not None
        }
        for el in strip_targets:
            if el in slot_els:
                continue
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))

    return JinjafyResult(
        output_path=out,
        bindings=bindings,
        unmatched_tags=unmatched,
        warnings=warnings,
    )


def rendered_tags(docx_path: Union[str, Path]) -> Dict[str, int]:
    """Count Jinja ``{{ tag }}`` occurrences in a .docx (for verification).

    Returns ``{tag_name: count}`` across all paragraphs and table cells.
    """
    doc = Document(str(docx_path))
    pattern = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
    counts: Dict[str, int] = {}

    def scan_text(text: str) -> None:
        for m in pattern.finditer(text or ""):
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1

    for p in doc.paragraphs:
        scan_text(p.text)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    scan_text(p.text)
    return counts
