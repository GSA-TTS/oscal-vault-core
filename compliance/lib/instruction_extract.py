"""Extract instruction/content pairs and render them for vaults (Phase R16.6).

The roundtrip jinjafier (``docx_jinjafier.py``) uses instruction anchors to
locate *where to write*. This module uses the same anchors to *read*: for each
contract write-back property it pairs the **instruction text** with the
**extracted content** of the writable slot, and renders them into vault
markdown with the instruction stored as a **hidden, collapsed callout**
immediately before the content (per the R16.6 decision).

Two directions are configurable:

- **Extraction** (docx -> markdown): whether to keep the instruction alongside
  the content, and how to encode it (``collapsed_callout`` default). Driven by
  ``RenderOptions``.
- **Rendering** (fill / template output): whether the instruction blocks are
  stripped from the output template. Driven by ``strip_instructions`` on the
  jinjafier (see ``docx_jinjafier.jinjafy``).

Hidden encoding
---------------
``collapsed_callout`` renders an Obsidian collapsed callout::

    > [!note]- Instruction
    > <instruction text, one blockquote line per source line>

The ``-`` after ``[!note]`` makes Obsidian fold it by default, so the guidance
travels with the content but stays out of the way. ``html_comment`` and
``plain`` are also supported for non-Obsidian consumers / stripping.

References
----------
- ``compliance/lib/instruction_anchors.py`` (anchor + slot detection)
- ``compliance/lib/docx_jinjafier.py`` (resolve_bindings, shared slot logic)
- ``archive/plans/ROUNDTRIP_TEMPLATE_PLAN.md`` §R16.6
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

from docx import Document

from compliance.lib.docx_jinjafier import (
    _instruction_config,
    resolve_bindings,
)
from compliance.lib.docx_ir import OPCPackageWalker
from compliance.lib.heading_parser import Contract, load_contract
from compliance.lib.instruction_anchors import find_instruction_anchors
from compliance.lib.instruction_ledger import normalized_sha256

# Recognized hidden-instruction encodings.
HIDDEN_STYLES = {"collapsed_callout", "html_comment", "plain", "none"}


@dataclass
class RenderOptions:
    """Configuration for how instructions are stored alongside content.

    Attributes
    ----------
    include_instructions:
        When ``True`` (default), the instruction text is emitted before the
        content. When ``False``, only the content is emitted.
    hidden_style:
        How the instruction is encoded: ``collapsed_callout`` (Obsidian
        collapsed callout, default), ``html_comment`` (``<!-- ... -->``),
        ``plain`` (visible blockquote), or ``none`` (no wrapper — same as
        ``include_instructions=False``).
    callout_kind:
        Callout admonition type for ``collapsed_callout`` (default ``note``).
    """

    include_instructions: bool = True
    hidden_style: str = "collapsed_callout"
    callout_kind: str = "note"

    def __post_init__(self) -> None:
        if self.hidden_style not in HIDDEN_STYLES:
            raise ValueError(
                f"hidden_style must be one of {sorted(HIDDEN_STYLES)}; "
                f"got {self.hidden_style!r}"
            )


@dataclass
class InstructionContentPair:
    """One extracted (instruction, content) pair for a contract templateTag."""

    template_tag: str
    heading: Optional[str]
    instruction_text: str
    content: str
    source_locator: Optional[str] = None
    source_hash: Optional[str] = None


@dataclass
class ExtractionResult:
    """All instruction/content pairs extracted from a document."""

    pairs: List[InstructionContentPair] = field(default_factory=list)

    def by_tag(self) -> Dict[str, InstructionContentPair]:
        return {p.template_tag: p for p in self.pairs}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_instruction_content(
    docx_source: Union[str, Path],
    contract: Union[str, Path, Contract],
) -> ExtractionResult:
    """Pair each contract write-back slot's instruction with its content.

    Runs the instruction-anchor pass, resolves the contract bindings (the same
    resolution the jinjafier uses), and reads the writable slot's text as the
    content for each ``templateTag``.
    """
    ctr = contract if isinstance(contract, Contract) else load_contract(contract)
    doc = Document(str(docx_source))
    colors, prefixes = _instruction_config(ctr)
    anchors = find_instruction_anchors(
        doc,
        instruction_colors=colors or None,
        instruction_text_prefixes=prefixes or None,
    )
    bindings, _unmatched, _warnings = resolve_bindings(anchors, ctr)

    pairs: List[InstructionContentPair] = []
    ir = OPCPackageWalker(docx_source).parse()
    instruction_nodes = [
        node
        for node in sorted(ir.nodes, key=lambda item: item.locator.order)
        if node.kind == "paragraph" and node.text
    ]
    used_locators: set[str] = set()
    for b in bindings:
        slot = b.anchor.slot_ref
        content = (getattr(slot, "text", "") or "").strip() if slot is not None else ""
        source_node = next(
            (
                node
                for node in instruction_nodes
                if node.locator.stable_id not in used_locators
                and node.text.strip() in b.anchor.instruction_text
            ),
            None,
        )
        source_locator = source_node.locator.stable_id if source_node else None
        if source_locator:
            used_locators.add(source_locator)
        pairs.append(
            InstructionContentPair(
                template_tag=b.template_tag,
                heading=b.anchor.heading,
                instruction_text=b.anchor.instruction_text,
                content=content,
                source_locator=source_locator,
                source_hash=normalized_sha256(b.anchor.instruction_text),
            )
        )
    return ExtractionResult(pairs=pairs)


# ---------------------------------------------------------------------------
# Rendering to vault markdown
# ---------------------------------------------------------------------------


def render_hidden_instruction(
    instruction_text: str,
    options: RenderOptions,
) -> str:
    """Render an instruction as hidden markdown per ``options.hidden_style``.

    Returns an empty string when instructions are disabled.
    """
    if not options.include_instructions or options.hidden_style == "none":
        return ""
    text = instruction_text.strip()
    if not text:
        return ""
    lines = text.split("\n")

    if options.hidden_style == "collapsed_callout":
        # Obsidian collapsed callout: `> [!note]- Instruction` then quoted body.
        out = [f"> [!{options.callout_kind}]- Instruction"]
        out.extend(f"> {line}" for line in lines)
        return "\n".join(out)

    if options.hidden_style == "html_comment":
        # Escape any nested comment terminator.
        safe = text.replace("-->", "--\u200b>")
        return f"<!-- Instruction:\n{safe}\n-->"

    if options.hidden_style == "plain":
        return "\n".join(f"> {line}" for line in lines)

    return ""


def render_pair_markdown(
    pair: InstructionContentPair,
    options: RenderOptions,
) -> str:
    """Render one (instruction, content) pair as vault markdown.

    The hidden instruction (if enabled) precedes the content, matching the
    document order (instruction, then the writable slot).
    """
    parts: List[str] = []
    if pair.source_locator and pair.source_hash:
        parts.append(
            "<!-- oscal-vault-instruction "
            + json.dumps(
                {
                    "source_locator": pair.source_locator,
                    "source_hash": pair.source_hash,
                },
                sort_keys=True,
            )
            + " -->"
        )
    hidden = render_hidden_instruction(pair.instruction_text, options)
    if hidden:
        parts.append(hidden)
    parts.append(pair.content or "")
    return "\n\n".join(p for p in parts if p).strip()


def render_extraction_markdown(
    result: ExtractionResult,
    options: Optional[RenderOptions] = None,
) -> Dict[str, str]:
    """Render each extracted pair to markdown, keyed by templateTag."""
    opts = options or RenderOptions()
    return {p.template_tag: render_pair_markdown(p, opts) for p in result.pairs}


# ---------------------------------------------------------------------------
# Stripping (for render/output templates that must not carry instructions)
# ---------------------------------------------------------------------------

_CALLOUT_RE = re.compile(r"^> \[![a-zA-Z]+\]-? Instruction\n(?:^>.*\n?)*", re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--\s*Instruction:.*?-->", re.DOTALL)


def strip_hidden_instructions(markdown: str) -> str:
    """Remove hidden-instruction blocks (callout or html comment) from markdown.

    Used when a downstream consumer wants the content without the guidance.
    """
    out = _CALLOUT_RE.sub("", markdown)
    out = _HTML_COMMENT_RE.sub("", out)
    # Collapse the blank lines a removal may leave behind.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
