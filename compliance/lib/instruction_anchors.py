"""Color-aware instruction-block + writable-slot detector (Phase R16.1).

FedRAMP SSP templates (the official GSA rev5 blanks) mark authoring guidance
with **instruction blocks** — paragraphs whose runs are colored blue
(``#0070C0`` in the primary High/Moderate/Low/Lightweight templates; a small
palette of blues — ``#1F497D``, ``#366091``, ``#0070C0`` — in the
Leveraged-SaaS-CRM template) and/or whose text begins with ``Instruction``,
``Instructions``, ``Note``, or ``Example``.

The **writable slot** — where an author (or this pipeline) actually reads and
writes content — is the first body item **immediately after** a contiguous
instruction block that is neither another instruction nor a heading.

This module walks the ``.docx`` body in document order (paragraphs and tables
interleaved, via the same XML body iterator used by
``scripts/docx_catchall_dump.py``) and produces one :class:`InstructionAnchor`
per instruction block, recording the writable slot that follows it.

It has **no** dependency on the datacontract or any capture/write strategy —
it is a pure structural pass. Downstream (``docx_jinjafier.py``,
``docx_fill.py``, and the contracts) join to these anchors by their
``instruction_text`` / ``heading``.

Design notes
------------
- Instruction color is a **configurable set of hex strings per template**
  (``instruction_colors``), defaulting to ``{"0070C0"}`` but overridable — it
  is *not* a hardcoded constant, because the CRM template uses other blues.
- Text-prefix detection (``instruction_text_prefixes``) is an independent
  fallback so a mis-colored or default-colored instruction is still detected.
- A paragraph counts as *colored* only when it has at least one run and
  **every** non-empty run is in the instruction color set (so a normal
  paragraph containing one incidental blue word is not misclassified).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple, Union

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

#: Default instruction color (primary GSA rev5 High/Moderate/Low/Lightweight).
DEFAULT_INSTRUCTION_COLORS: Set[str] = {"0070C0"}

#: Text prefixes that mark an instruction paragraph regardless of color.
DEFAULT_INSTRUCTION_TEXT_PREFIXES: Tuple[str, ...] = (
    "instruction",
    "instructions",
    "note",
    "example",
)

_HEADING_STYLE_RE = re.compile(r"^Heading\s+(\d+)$")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class InstructionAnchor:
    """One instruction block and the writable slot that follows it.

    Attributes
    ----------
    heading:
        Text of the nearest preceding ``Heading N`` / ``Title`` paragraph, or
        ``None`` if the instruction appears before any heading.
    heading_level:
        ``0`` for ``Title``, ``1``-``9`` for ``Heading N``, ``None`` if no
        heading precedes the block.
    instruction_text:
        The full text of the contiguous instruction block (multiple
        instruction paragraphs joined by ``\\n``).
    instruction_kind:
        ``instruction``, ``note``, or ``example`` — derived from the leading
        text prefix; ``instruction`` when only color (no prefix) matched.
    detected_by:
        How the block was detected: ``color``, ``text``, or ``color+text``.
    slot_kind:
        Kind of the writable slot that follows: ``paragraph``, ``table``,
        ``heading`` (the next item is itself a heading — i.e. the instruction
        has no dedicated writable body), or ``none`` (end of document).
    body_index:
        Index of the writable slot within the document-order body list.
    instruction_body_index:
        Index of the **first** paragraph of the instruction block within the
        document-order body list.
    slot_ref:
        The ``python-docx`` ``Paragraph`` / ``Table`` handle for the writable
        slot (``None`` when ``slot_kind == 'none'``).
    """

    heading: Optional[str]
    heading_level: Optional[int]
    instruction_text: str
    instruction_kind: str
    detected_by: str
    slot_kind: str
    body_index: int
    instruction_body_index: int
    slot_ref: Any = field(default=None, repr=False)

    @property
    def has_slot(self) -> bool:
        """True when a fillable paragraph/table slot follows the block."""
        return self.slot_kind in ("paragraph", "table")


# ---------------------------------------------------------------------------
# Body iteration (document order) — mirrors docx_catchall_dump._iter_body_blocks
# ---------------------------------------------------------------------------


def _iter_body_blocks(doc: DocxDocument) -> Iterable[Tuple[str, Any]]:
    """Yield ``(kind, obj)`` in document order.

    ``kind`` is ``"paragraph"``, ``"table"``, or ``"other"``.
    """
    body = doc.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield "paragraph", Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield "table", Table(child, doc)
        else:
            yield "other", child


# ---------------------------------------------------------------------------
# Classification primitives
# ---------------------------------------------------------------------------


def _run_color_hex(run: Any) -> Optional[str]:
    """Return the uppercase RRGGBB hex of a run's font color, or ``None``.

    ``None`` covers: no explicit color, theme colors (no ``rgb``), and any
    access error from python-docx.
    """
    try:
        color = run.font.color
    except Exception:  # pragma: no cover - defensive
        return None
    if color is None:
        return None
    try:
        rgb = color.rgb
    except Exception:  # pragma: no cover - defensive
        return None
    if rgb is None:
        return None
    return str(rgb).upper()


def _effective_run_color_hex(run: Any, para: Paragraph) -> Optional[str]:
    """Return direct or inherited run/paragraph style color."""
    direct = _run_color_hex(run)
    if direct is not None:
        return direct
    styles = [getattr(run, "style", None), getattr(para, "style", None)]
    for style in styles:
        seen = set()
        while style is not None and id(style) not in seen:
            seen.add(id(style))
            try:
                rgb = style.font.color.rgb
            except (AttributeError, ValueError):
                rgb = None
            if rgb is not None:
                return str(rgb).upper()
            style = getattr(style, "base_style", None)
    return None


def is_instruction_colored(
    para: Paragraph,
    instruction_colors: Set[str],
) -> bool:
    """True if the paragraph is entirely in an instruction color.

    Requires at least one run with a color, and **every** run that has an
    explicit color to be in ``instruction_colors``. Runs with no explicit
    color (theme/default) are ignored so a fully-blue paragraph split across
    colored + inherited runs still counts, but a normal paragraph with one
    incidental blue word does not.
    """
    if not instruction_colors:
        return False
    text_runs = [run for run in para.runs if (run.text or "").strip()]
    direct_colors = [_run_color_hex(run) for run in text_runs]
    use_inherited = not any(direct_colors)
    saw_color = False
    for run, direct in zip(text_runs, direct_colors):
        hex_ = _effective_run_color_hex(run, para) if use_inherited else direct
        if hex_ is None:
            continue
        saw_color = True
        if hex_ not in instruction_colors:
            return False
    return saw_color


def instruction_text_kind(
    text: str,
    prefixes: Sequence[str],
) -> Optional[str]:
    """Return the instruction kind if ``text`` starts with a known prefix.

    Returns ``"note"``/``"example"`` when the paragraph leads with those
    words, ``"instruction"`` for the ``instruction``/``instructions`` prefixes,
    or ``None`` when no prefix matches. Matching is case-insensitive and
    requires the configured label to be followed immediately by ``:``.
    """
    stripped = text.strip().lower()
    if not stripped:
        return None
    for prefix in prefixes:
        p = prefix.lower()
        if stripped.startswith(p):
            # Template guidance uses a labelled prefix. Requiring punctuation
            # avoids treating ordinary prose such as "Note the setting" or
            # "Examples include" as an instruction.
            rest = stripped[len(p) :]
            if rest.startswith(":"):
                if p.startswith("note"):
                    return "note"
                if p.startswith("example"):
                    return "example"
                return "instruction"
    return None


def _heading_level(para: Paragraph) -> Optional[int]:
    """Return ``0`` for Title, ``1``-``9`` for ``Heading N``, else ``None``."""
    style_name = para.style.name if para.style else ""
    if style_name == "Title":
        return 0
    m = _HEADING_STYLE_RE.match(style_name or "")
    if m:
        n = int(m.group(1))
        if 1 <= n <= 9:
            return n
    return None


# ---------------------------------------------------------------------------
# Anchor detection
# ---------------------------------------------------------------------------


def find_instruction_anchors(
    source: Union[str, Path, DocxDocument],
    *,
    instruction_colors: Optional[Iterable[str]] = None,
    instruction_text_prefixes: Optional[Sequence[str]] = None,
) -> List[InstructionAnchor]:
    """Detect instruction blocks + their writable slots in a ``.docx``.

    Parameters
    ----------
    source:
        Path to a ``.docx`` file, or an already-open ``python-docx``
        ``Document``.
    instruction_colors:
        Iterable of ``RRGGBB`` hex strings (case-insensitive, ``#`` optional)
        to treat as instruction color. Defaults to
        :data:`DEFAULT_INSTRUCTION_COLORS` (``0070C0``).
    instruction_text_prefixes:
        Text prefixes that mark an instruction regardless of color. Defaults
        to :data:`DEFAULT_INSTRUCTION_TEXT_PREFIXES`.

    Returns
    -------
    list[InstructionAnchor]
        One anchor per contiguous instruction block, in document order.
    """
    colors = _normalize_colors(instruction_colors)
    prefixes = (
        tuple(instruction_text_prefixes)
        if instruction_text_prefixes is not None
        else DEFAULT_INSTRUCTION_TEXT_PREFIXES
    )

    doc = source if isinstance(source, DocxDocument) else Document(str(source))
    blocks = list(_iter_body_blocks(doc))

    # Precompute per-block classification for paragraphs.
    def classify(idx: int) -> Tuple[str, Optional[str], Optional[str]]:
        """Return (role, instruction_kind, detected_by) for a body block.

        role is one of: 'instruction', 'heading', 'content', 'other'.
        """
        kind, obj = blocks[idx]
        if kind == "table":
            return "content", None, None
        if kind != "paragraph":
            return "other", None, None
        if _heading_level(obj) is not None:
            return "heading", None, None
        text_kind = instruction_text_kind(obj.text, prefixes)
        colored = is_instruction_colored(obj, colors)
        if text_kind is not None and colored:
            return "instruction", text_kind, "color+text"
        if text_kind is not None:
            return "instruction", text_kind, "text"
        if colored:
            return "instruction", "instruction", "color"
        return "content", None, None

    anchors: List[InstructionAnchor] = []
    current_heading: Optional[str] = None
    current_heading_level: Optional[int] = None

    n = len(blocks)
    i = 0
    while i < n:
        kind, obj = blocks[i]
        # Track heading context.
        if kind == "paragraph":
            hl = _heading_level(obj)
            if hl is not None:
                current_heading = obj.text.strip()
                current_heading_level = hl
                i += 1
                continue

        role, text_kind, detected_by = classify(i)
        if role != "instruction":
            i += 1
            continue

        # Collapse the contiguous instruction block (consecutive instruction
        # paragraphs, ignoring interleaved empty paragraphs).
        block_start = i
        block_texts: List[str] = []
        first_kind = text_kind or "instruction"
        detected_set = set()
        j = i
        while j < n:
            role_j, _tk_j, det_j = classify(j)
            if role_j == "instruction":
                block_texts.append(blocks[j][1].text.strip())
                if det_j:
                    detected_set.add(det_j)
                j += 1
                continue
            # Allow a blank content paragraph inside a block to be skipped only
            # if the NEXT block is again an instruction (keeps a run together).
            if (
                role_j == "content"
                and blocks[j][0] == "paragraph"
                and not (blocks[j][1].text or "").strip()
                and j + 1 < n
                and classify(j + 1)[0] == "instruction"
            ):
                j += 1
                continue
            break

        # Find the writable slot: first following block that is content
        # (paragraph or table) and not empty-structural. A heading or EOF
        # means there is no dedicated writable body for this instruction.
        slot_kind = "none"
        slot_ref: Any = None
        slot_index = -1
        k = j
        while k < n:
            bkind, bobj = blocks[k]
            if bkind == "other":
                k += 1
                continue
            if bkind == "paragraph":
                if _heading_level(bobj) is not None:
                    slot_kind = "heading"
                    slot_ref = None
                    slot_index = k
                    break
                # A content paragraph is the slot (even if currently empty —
                # empty paragraphs are common fillable slots in templates).
                slot_kind = "paragraph"
                slot_ref = bobj
                slot_index = k
                break
            if bkind == "table":
                slot_kind = "table"
                slot_ref = bobj
                slot_index = k
                break
            k += 1

        detected_by_final = (
            "color+text"
            if detected_set == {"color+text"} or detected_set >= {"color", "text"}
            else next(iter(detected_set), detected_by or "color")
        )

        anchors.append(
            InstructionAnchor(
                heading=current_heading,
                heading_level=current_heading_level,
                instruction_text="\n".join(t for t in block_texts if t),
                instruction_kind=first_kind,
                detected_by=detected_by_final,
                slot_kind=slot_kind,
                body_index=slot_index,
                instruction_body_index=block_start,
                slot_ref=slot_ref,
            )
        )
        i = j

    return anchors


def _normalize_colors(colors: Optional[Iterable[str]]) -> Set[str]:
    """Normalize a color iterable to uppercase RRGGBB (strip ``#``)."""
    if colors is None:
        return set(DEFAULT_INSTRUCTION_COLORS)
    out: Set[str] = set()
    for c in colors:
        if not c:
            continue
        h = str(c).strip().lstrip("#").upper()
        if h:
            out.add(h)
    return out or set(DEFAULT_INSTRUCTION_COLORS)


# ---------------------------------------------------------------------------
# Summary helper (for CLI / feedback surfaces)
# ---------------------------------------------------------------------------


def summarize_anchors(anchors: Sequence[InstructionAnchor]) -> dict:
    """Return counts by slot_kind, detection method, and instruction kind."""
    summary = {
        "total": len(anchors),
        "with_slot": sum(1 for a in anchors if a.has_slot),
        "slot_kind": {},
        "detected_by": {},
        "instruction_kind": {},
    }
    for a in anchors:
        summary["slot_kind"][a.slot_kind] = summary["slot_kind"].get(a.slot_kind, 0) + 1
        summary["detected_by"][a.detected_by] = (
            summary["detected_by"].get(a.detected_by, 0) + 1
        )
        summary["instruction_kind"][a.instruction_kind] = (
            summary["instruction_kind"].get(a.instruction_kind, 0) + 1
        )
    return summary
