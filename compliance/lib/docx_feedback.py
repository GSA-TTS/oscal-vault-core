"""Build a compact skeleton/TOC .docx feedback document.

Reverse pipeline: uses the ODCS data contract as a template configuration to
render a one-page-per-document skeleton showing, for each source heading,
whether it was routed, to which destinations, and whether the routed
properties were filled. Designed for an agent to scan and take back to the
parsing logic + datacontract to investigate and fix.

Uses python-docx-template (docxtpl) for rendering. The template is built
programmatically with python-docx (no binary template file to commit).

Companion to:
  - compliance/lib/heading_parser.py (forward: extract records via contract)
  - compliance/lib/markdown_feedback.py (markdown skeleton form)
  - compliance/scripts/generate_feedback.py (CLI orchestrator)
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docxtpl import DocxTemplate

from .heading_parser import (
    Contract,
    HeadingSection,
    _heading_matches,
)


# ---------------------------------------------------------------------------
# Data shaping: one entry per source heading
# ---------------------------------------------------------------------------


@dataclass
class DestinationRef:
    """One property routing that maps to a source heading."""

    object_id: str
    object_name: str
    oscal_type: Optional[str]
    oscal_model: Optional[str]
    destination_folder: Optional[str]
    property_name: str
    oscal_path: Optional[str]
    capture_strategy: Optional[str]
    source_kind: Optional[str]
    source_heading: Optional[str]  # contract's sourceHeading pattern (for reviewer)
    note: Optional[str]
    provenance: Optional[str]
    extracted_value: Any
    is_filled: bool

    def source_heading_pretty(self) -> str:
        """Render the contract sourceHeading for display."""
        return self.source_heading or "(none)"


@dataclass
class FeedbackSection:
    """One source heading + its routing status."""

    source_heading: str
    level: int  # heading level from the catchall blob (1-6), or 0 if synthetic
    style: str
    destinations: List[DestinationRef] = field(default_factory=list)
    original_paragraph_count: int = 0
    original_table_count: int = 0
    original_image_count: int = 0


def build_feedback_sections(
    extract_result: Dict[str, Any],
    contract: Contract,
    sections: List[HeadingSection],
) -> List[FeedbackSection]:
    """Group extracted records by source heading for skeleton review.

    Returns one FeedbackSection per source heading, in document order. A
    heading appears if either (a) at least one destination property routes
    to it, or (b) it has original content but no routing (flagged UNROUTED).
    Empty headings and the dumper's own structural headings are skipped.
    """
    by_heading: Dict[str, FeedbackSection] = {}
    claimed: set = set()

    # 1. Walk every destination property and attach to its source heading
    for obj in contract.destinations():
        rec = next(
            (r for r in extract_result["objects"] if r["object_id"] == obj.object_id),
            None,
        )
        if rec is None:
            continue
        for prop in obj.properties:
            if not prop.source_heading or not prop.capture_strategy:
                continue
            if prop.capture_strategy == "derived":
                continue  # downstream hook, no source content to review
            matches = [
                (i, s)
                for i, s in enumerate(sections)
                if _heading_matches(s.title, prop.source_heading)
            ]
            for sec_idx, sec in matches:
                claimed.add(sec_idx)
                if prop.capture_scope == "until_next_same_or_higher":
                    for desc_idx in range(sec_idx + 1, len(sections)):
                        desc = sections[desc_idx]
                        if desc.level <= sec.level:
                            break
                        claimed.add(desc_idx)
                key = f"{sec.title}|{sec.level}"
                if key not in by_heading:
                    by_heading[key] = FeedbackSection(
                        source_heading=sec.title,
                        level=sec.level,
                        style=sec.style,
                        original_paragraph_count=len(sec.paragraphs),
                        original_table_count=len(sec.tables),
                        original_image_count=len(sec.images),
                    )
                fb = by_heading[key]
                fb.destinations.append(
                    DestinationRef(
                        object_id=obj.object_id,
                        object_name=obj.name,
                        oscal_type=obj.oscal_type,
                        oscal_model=obj.oscal_model,
                        destination_folder=obj.destination_folder,
                        property_name=prop.name,
                        oscal_path=prop.oscal_path,
                        capture_strategy=prop.capture_strategy,
                        source_kind=prop.source_kind,
                        source_heading=prop.source_heading,
                        note=prop.note,
                        provenance=prop.provenance,
                        extracted_value=rec["properties"].get(prop.name),
                        is_filled=prop.name in rec["properties"],
                    )
                )

    # 2. Find UNROUTED sections (original content with no destination)
    for i, sec in enumerate(sections):
        if i in claimed:
            continue
        if not sec.paragraphs and not sec.tables and not sec.images:
            continue
        if sec.title.startswith(
            (
                "Catchall Dump:",
                "Document ",
                "Headings ",
                "Body Content",
                "Images and Embedded",
                "Headers and Footers",
                "Currently captured",
            )
        ):
            continue
        if not sec.title:
            continue
        key = f"{sec.title}|{sec.level}"
        if key in by_heading:
            continue
        by_heading[key] = FeedbackSection(
            source_heading=sec.title,
            level=sec.level,
            style=sec.style,
            original_paragraph_count=len(sec.paragraphs),
            original_table_count=len(sec.tables),
            original_image_count=len(sec.images),
        )

    # 3. Sort by document order
    order: List[tuple] = []
    for fb in by_heading.values():
        earliest = min(
            (i for i, s in enumerate(sections) if s.title == fb.source_heading),
            default=len(sections),
        )
        order.append((earliest, fb))
    order.sort(key=lambda x: x[0])
    return [fb for _, fb in order]


# ---------------------------------------------------------------------------
# Skeleton status helpers (shared with markdown_feedback)
# ---------------------------------------------------------------------------


def section_status(fb: FeedbackSection) -> str:
    """One-word status: ROUTED-FILLED, ROUTED-PARTIAL, ROUTED-EMPTY, UNROUTED."""
    if not fb.destinations:
        return "UNROUTED"
    filled = sum(1 for d in fb.destinations if d.is_filled)
    total = len(fb.destinations)
    if filled == total:
        return "ROUTED-FILLED"
    if filled == 0:
        return "ROUTED-EMPTY"
    return "ROUTED-PARTIAL"


def status_glyph(status: str) -> str:
    return {
        "ROUTED-FILLED": "✅",
        "ROUTED-PARTIAL": "🟡",
        "ROUTED-EMPTY": "❌",
        "UNROUTED": "⚠️",
    }.get(status, "?")


def routing_summary_line(fb: FeedbackSection) -> str:
    """Compact one-line routing summary for TOC entries."""
    if not fb.destinations:
        return (
            f"⚠️ UNROUTED — original content "
            f"({fb.original_paragraph_count}p, {fb.original_table_count}t, "
            f"{fb.original_image_count}i) but no destination in contract"
        )
    filled = sum(1 for d in fb.destinations if d.is_filled)
    total = len(fb.destinations)
    parts = [
        f"{d.object_name}.{d.property_name}" + (" ✅" if d.is_filled else " ❌")
        for d in fb.destinations
    ]
    return f"{filled}/{total} filled: " + ", ".join(parts)


def unfilled_destinations(fb: FeedbackSection) -> List[DestinationRef]:
    return [d for d in fb.destinations if not d.is_filled]


# ---------------------------------------------------------------------------
# docxtpl template builder (compact skeleton)
# ---------------------------------------------------------------------------


def build_feedback_template(template_path: Path) -> None:
    """Build a compact skeleton .docx template with Jinja2 tags."""
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    # --- Title ---
    title = doc.add_heading("FedRAMP SSP Extraction — Feedback Skeleton", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # --- Source / summary block ---
    doc.add_heading("Source", level=1)
    doc.add_paragraph("Document: {{ source_docx }}")
    doc.add_paragraph("Generated: {{ generated_at }}")

    doc.add_heading("Summary", level=1)
    doc.add_paragraph(
        "Headings: {{ sections_count }} "
        "(routed: {{ routed_count }}, unrouted: {{ unrouted_count }})"
    )
    doc.add_paragraph(
        "Properties filled: {{ summary.properties_filled }} / "
        "{{ summary.properties_total }}  (unfilled: {{ summary.unfilled_count }})"
    )

    # --- TOC (one line per heading) ---
    doc.add_heading("Skeleton TOC", level=1)
    doc.add_paragraph(
        "Each line shows the heading, routing status, and per-destination fill state. "
        "Use the checkboxes to mark headings you've reviewed."
    )
    doc.add_paragraph("{%p for s in sections %}")
    doc.add_paragraph(
        "☐ {{ s.glyph }} H{{ s.level }}: {{ s.source_heading }} — {{ s.routing_line }}"
    )
    doc.add_paragraph("{%p endfor %}")

    # --- Per-heading notes (compact) ---
    doc.add_heading("Per-heading notes", level=1)
    doc.add_paragraph(
        "Add reviewer notes only for headings with issues. Skip headings that "
        "extracted correctly."
    )
    doc.add_paragraph("{%p for s in sections %}")
    doc.add_paragraph("{{ s.glyph }} H{{ s.level }}: {{ s.source_heading }}")
    doc.add_paragraph("Routing: {{ s.routing_line }}")
    doc.add_paragraph("{%p if s.has_unfilled %}")
    doc.add_paragraph("Unfilled: {{ s.unfilled_names }}")
    doc.add_paragraph("{%p endif %}")
    doc.add_paragraph("Notes: ____________________________________________")
    doc.add_paragraph("Suggested fix: _____________________________________")
    doc.add_paragraph("{%p endfor %}")

    # --- Unfilled properties appendix ---
    doc.add_heading("Appendix: Unfilled properties", level=1)
    doc.add_paragraph(
        "Properties the contract expected but the parser could not fill. "
        "Most `uuid` fields are `derived` (downstream hook, expected unfilled)."
    )
    doc.add_paragraph("{%p for u in unfilled_list %}")
    doc.add_paragraph(
        "• {{ u.object_id }}.{{ u.name }}  "
        "(sourceHeading: {{ u.sourceHeading or '(derived / none)' }})"
    )
    doc.add_paragraph("{%p endfor %}")

    template_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(template_path))


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def build_render_context(
    extract_result: Dict[str, Any],
    contract: Contract,
    feedback_sections: List[FeedbackSection],
    source_docx: str,
) -> Dict[str, Any]:
    """Build the Jinja2 context dict for docxtpl rendering."""
    sections_ctx = []
    routed_count = 0
    unrouted_count = 0
    for fb in feedback_sections:
        is_routed = len(fb.destinations) > 0
        if is_routed:
            routed_count += 1
        else:
            unrouted_count += 1
        status = section_status(fb)
        unfilled_names = ", ".join(d.property_name for d in unfilled_destinations(fb))
        sections_ctx.append(
            {
                "source_heading": fb.source_heading,
                "level": fb.level,
                "glyph": status_glyph(status),
                "status": status,
                "routing_line": routing_summary_line(fb),
                "has_unfilled": bool(unfilled_names),
                "unfilled_names": unfilled_names,
                "is_routed": is_routed,
            }
        )

    summary = extract_result.get("summary", {})
    unfilled_list = summary.get("unfilled", [])

    return {
        "source_docx": source_docx,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "objects_total": summary.get("objects_total", 0),
            "objects_with_data": summary.get("objects_with_data", 0),
            "properties_filled": summary.get("properties_filled", 0),
            "properties_total": summary.get("properties_total", 0),
            "unfilled_count": len(unfilled_list),
        },
        "sections": sections_ctx,
        "sections_count": len(sections_ctx),
        "routed_count": routed_count,
        "unrouted_count": unrouted_count,
        "unfilled_list": unfilled_list,
    }


# ---------------------------------------------------------------------------
# Top-level: render the skeleton .docx
# ---------------------------------------------------------------------------


def render_feedback_docx(
    extract_result: Dict[str, Any],
    contract: Contract,
    sections: List[HeadingSection],
    output_path: Path,
    source_docx: str = "(markdown input)",
    template_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Render a compact skeleton feedback .docx."""
    feedback_sections = build_feedback_sections(extract_result, contract, sections)
    context = build_render_context(
        extract_result, contract, feedback_sections, source_docx
    )

    if template_path is None:
        template_path = output_path.parent / "_feedback_template.docx"
    if not template_path.exists():
        build_feedback_template(template_path)

    tpl = DocxTemplate(str(template_path))
    tpl.render(context, autoescape=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tpl.save(str(output_path))

    return {
        "output": str(output_path),
        "sections_total": context["sections_count"],
        "routed_count": context["routed_count"],
        "unrouted_count": context["unrouted_count"],
        "unfilled_count": context["summary"]["unfilled_count"],
        "template": str(template_path),
    }
