"""Generate a compact markdown skeleton/TOC feedback form.

One file per source document. Each heading gets one line in the TOC showing
routing status (routed/filled/unfilled/unrouted), followed by a compact
notes section per heading. Designed for an agent to scan and take back to
the parsing logic + datacontract to investigate and fix — not a verbose
per-section packet.

Companion to:
  - compliance/lib/docx_feedback.py (same skeleton as .docx)
  - compliance/scripts/generate_feedback.py (CLI orchestrator)
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List

from .docx_feedback import (
    build_feedback_sections,
    routing_summary_line,
    section_status,
    status_glyph,
    unfilled_destinations,
)
from .heading_parser import Contract, HeadingSection


def render_skeleton_markdown(
    extract_result: Dict[str, Any],
    contract: Contract,
    sections: List[HeadingSection],
    source_docx: str,
) -> str:
    """Render the compact skeleton feedback markdown.

    Shape:
        # Feedback Skeleton: <basename>
        > Source / generated / summary
        ## TOC
        - [ ] ✅ H1: Information System Name — 2/2 filled: oscal_ssp.title ✅, ...
        - [ ] ❌ H1: Systems Receiving Controls — 0/1 filled: ...received_by ❌
        - [ ] ⚠️ H2: Some Unrouted Heading — UNROUTED
        ## Per-heading notes
        ### ✅ H1: Information System Name
        Routing: ...
        Notes: ___
        Suggested fix: ___
        ### ❌ H1: Systems Receiving Controls
        Routing: ...
        Unfilled: received_by (sourceHeading: regex:^Systems Receiving Controls)
        Notes: ___
        Suggested fix: ___
        ## Unfilled properties (contract expected but parser found nothing)
        - oscal_ssp.uuid (sourceHeading: (derived / none))
        - ...
    """
    feedback_sections = build_feedback_sections(extract_result, contract, sections)
    summary = extract_result.get("summary", {})
    unfilled_list = summary.get("unfilled", [])
    generated_at = datetime.datetime.now().isoformat(timespec="seconds")
    routed = sum(1 for fb in feedback_sections if fb.destinations)
    unrouted = len(feedback_sections) - routed

    lines: List[str] = []
    lines.append(f"# Feedback Skeleton: {Path(source_docx).name}")
    lines.append("")
    lines.append(f"> **Source**: `{source_docx}`")
    lines.append(f"> **Generated**: {generated_at}")
    lines.append("> **Contract**: `compliance/datacontracts/fedramp-ssp-v1.yaml`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"- Headings: {len(feedback_sections)} (routed: {routed}, unrouted: {unrouted})"
    )
    lines.append(
        f"- Properties filled: {summary.get('properties_filled', 0)} / "
        f"{summary.get('properties_total', 0)}  "
        f"(unfilled: {len(unfilled_list)})"
    )
    lines.append("")
    lines.append(
        "**Status glyphs**: ✅ routed & filled  |  🟡 routed & partial  |  "
        "❌ routed & empty  |  ⚠️ unrouted (content with no destination)"
    )
    lines.append("")
    lines.append("## TOC")
    lines.append("")
    lines.append(
        "Each line: heading, routing status, per-destination fill state. "
        "Check the box when you've reviewed that heading."
    )
    lines.append("")
    for fb in feedback_sections:
        status = section_status(fb)
        glyph = status_glyph(status)
        indent = "  " * max(0, fb.level - 1)
        lines.append(
            f"{indent}- [ ] {glyph} H{fb.level}: {fb.source_heading} — "
            f"{routing_summary_line(fb)}"
        )
    lines.append("")
    lines.append("## Per-heading notes")
    lines.append("")
    lines.append(
        "Add notes only for headings with issues. Skip headings that "
        "extracted correctly. The agent consuming this form will use "
        "your notes to investigate the parsing logic and datacontract."
    )
    lines.append("")
    for fb in feedback_sections:
        status = section_status(fb)
        glyph = status_glyph(status)
        lines.append(f"### {glyph} H{fb.level}: {fb.source_heading}")
        lines.append("")
        lines.append(f"**Routing**: {routing_summary_line(fb)}")
        unfilled = unfilled_destinations(fb)
        if unfilled:
            lines.append("")
            lines.append("**Unfilled destinations**:")
            for d in unfilled:
                lines.append(
                    f"- `{d.object_name}.{d.property_name}` "
                    f"(sourceHeading: `{d.source_heading_pretty()}`, "
                    f"captureStrategy: `{d.capture_strategy or '?'}`)"
                )
        if not fb.destinations:
            lines.append("")
            lines.append(
                f"**Original content**: {fb.original_paragraph_count} paragraph(s), "
                f"{fb.original_table_count} table(s), "
                f"{fb.original_image_count} image(s) — no destination in contract"
            )
        lines.append("")
        lines.append("- **Notes**: ")
        lines.append(
            "- **Suggested fix**: "
            "(contract edit / parser bug / heading mismatch / other)"
        )
        lines.append("")
    lines.append("## Unfilled properties (contract expected but parser found nothing)")
    lines.append("")
    if not unfilled_list:
        lines.append("_(all properties filled)_")
    else:
        lines.append(
            "Most `uuid` fields are `derived` (downstream hook, expected "
            "unfilled). Investigate the others."
        )
        lines.append("")
        lines.append("| object | property | sourceHeading |")
        lines.append("|---|---|---|")
        for u in unfilled_list:
            sh = u.get("sourceHeading") or "(derived / none)"
            lines.append(
                f"| `{u.get('object_id', '?')}` | `{u.get('name', '?')}` | `{sh}` |"
            )
    lines.append("")
    return "\n".join(lines)


def generate_skeleton(
    extract_result: Dict[str, Any],
    contract: Contract,
    sections: List[HeadingSection],
    output_path: Path,
    source_docx: str = "(markdown input)",
) -> Dict[str, Any]:
    """Write the compact skeleton feedback markdown to `output_path`."""
    md = render_skeleton_markdown(extract_result, contract, sections, source_docx)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")
    feedback_sections = build_feedback_sections(extract_result, contract, sections)
    routed = sum(1 for fb in feedback_sections if fb.destinations)
    return {
        "output": str(output_path),
        "sections_total": len(feedback_sections),
        "routed_count": routed,
        "unrouted_count": len(feedback_sections) - routed,
    }
