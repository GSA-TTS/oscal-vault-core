#!/usr/bin/env python3
"""Classify every unrouted block in a FedRAMP SSP ``.docx`` by what it *is*.

``docx_coverage_metrics.py`` answers "how much is dropped" (a count).  This
answers "what was dropped" (a disposition), so the drop can be reviewed and
either adopted or explicitly declined rather than left as an undifferentiated
loss budget.

Every block the extractors do not route is assigned exactly one class:

``catalog-statement``
    Verbatim (or parameter-substituted) NIST SP 800-53 Rev. 5 control
    statement text, matched against ``compliance/data/nist_sp800-53_rev5_
    control_statements.json``.  The authoritative copy is the catalog; the
    ``.docx`` is reprinting it.
``control-title``
    A control heading such as ``AC-02(03): Account Management | Disable
    Accounts`` that this template styles as body text rather than a heading,
    so it is counted as prose.  Structure, not content.
``structured-table``
    A table the control-table anchor does not claim.  Reported with cell
    counts, because a table's content is in its cells, not in prose length.
``glossary-definition``
    An Appendix A term definition (``Audit Log - A chronological record...``).
``authority-citation``
    An Appendix B statute, FIPS, NIST, OMB or EO reference.
``template-narrative``
    Prose that is neither catalog text nor furniture: the agency's own
    instructions, lead-ins, and section narrative.  **This is the material an
    adoption decision is actually about.**
``odp-placeholder``
    A bracketed organization-defined-parameter fragment (``[Annually];``).
``document-furniture``
    Signature rules, "Table of Contents", "Approvals", revision-history
    scaffolding.
``gsa-annotation``
    An explicit ``GSA Note:`` aside.
``diagram-image``
    An inline or floating image.

Usage::

    uv run python compliance/scripts/classify_dropped_blocks.py \\
        --docx "templates/Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx"

    # Machine-readable, for a coverage report or a gate:
    uv run python compliance/scripts/classify_dropped_blocks.py \\
        --docx <ssp>.docx --json out.json

Read-only.  Never writes to the source document.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FISMATIC_SRC = REPO_ROOT / "fismatic" / "src" / "phase_1"
CATALOG = (
    REPO_ROOT / "compliance" / "data" / "nist_sp800-53_rev5_control_statements.json"
)

for _path in (REPO_ROOT, FISMATIC_SRC):
    if str(_path) not in sys.path:
        sys.path.append(str(_path))


# A control heading the template renders as body text: "AC-02(03): ... | ...".
CONTROL_TITLE = re.compile(r"^[A-Z]{2}-\d{1,2}(\(\d{1,2}\))?\s*[:|]")
# "Audit Log - A chronological record of ..." (Appendix A shape).
GLOSSARY = re.compile(r"^[A-Z][A-Za-z /()\-]{2,48}\s[-\u2013]\s")
AUTHORITY = re.compile(
    r"^(\d+\s+U\.?S\.?\s?C|FIPS|NIST|OMB|EO |CISA|FedRAMP|Public Law|HSPD|Clinger"
    r"|Federal|Applicable Federal)"
)
SIGNATURE_RULE = re.compile(r"^_{10,}$")
BRACKETED = re.compile(r"^\[[^\]]+\]\.?;?$")

FURNITURE_LINES = frozenset(
    {
        "approvals",
        "attachments",
        "document prepared by",
        "document revision history",
        "guidance",
        "table of contents",
        "tables and figures",
    }
)

# Buckets used by the summary. The order is the reporting order.
CLASSES = (
    "catalog-statement",
    "control-title",
    "structured-table",
    "glossary-definition",
    "authority-citation",
    "template-narrative",
    "odp-placeholder",
    "document-furniture",
    "gsa-annotation",
    "diagram-image",
)

# Disposition groups. See docs/DROPPED_CONTENT_DISPOSITION.md for the rationale.
DISPOSITION = {
    "adopt": ("structured-table", "template-narrative"),
    "reference": ("catalog-statement", "glossary-definition", "authority-citation"),
    "decline": (
        "control-title",
        "odp-placeholder",
        "document-furniture",
        "gsa-annotation",
        "diagram-image",
    ),
}


def normalize(text: str) -> str:
    """Comparison form: drop ODP inserts, brackets, punctuation and case."""
    value = re.sub(r"\{\{[^}]*\}\}", "", text or "")
    value = re.sub(r"\[[^\]]*\]", "", value)
    value = re.sub(r"[^a-z0-9 ]", " ", value.lower())
    return " ".join(value.split())


@dataclass
class Block:
    kind: str
    heading: str
    text: str
    length: int
    cells: int = 0
    cell_chars: int = 0
    cls: str = ""


@dataclass
class Report:
    docx: str
    blocks: list[Block] = field(default_factory=list)

    def by_class(self) -> dict[str, dict[str, int]]:
        agg: dict[str, dict[str, int]] = defaultdict(
            lambda: {"blocks": 0, "chars": 0, "cells": 0}
        )
        for block in self.blocks:
            row = agg[block.cls]
            row["blocks"] += 1
            row["chars"] += block.length or block.cell_chars
            row["cells"] += block.cells
        return dict(agg)

    def by_heading(self, cls: str) -> list[tuple[str, int, int]]:
        agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for block in self.blocks:
            if block.cls != cls:
                continue
            row = agg[block.heading]
            row[0] += 1
            row[1] += block.length or block.cell_chars
        return sorted(
            ((h, n, c) for h, (n, c) in agg.items()), key=lambda item: -item[2]
        )


def _catalog_statements() -> tuple[set[str], list[str]]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    lines = [normalize(line) for group in data.values() for line in group]
    kept = [line for line in lines if line]
    return set(kept), kept


def collect(docx_path: Path) -> Report:
    """Walk the body in document order and keep only what nothing routes."""
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    from compliance.lib.control_table_anchors import find_control_tables
    from compliance.lib.instruction_anchors import (
        DEFAULT_INSTRUCTION_COLORS,
        DEFAULT_INSTRUCTION_TEXT_PREFIXES,
        _heading_level,
        instruction_text_kind,
        is_instruction_colored,
    )

    namespace_a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    document = Document(str(docx_path))
    # Hold the anchors for the whole walk. `id()` of an lxml element is only
    # stable while a proxy for it is alive: let the anchor list fall out of
    # scope and the proxies are freed, their ids get reused by new proxies, and
    # routed control tables start matching arbitrary other tables.
    anchors = find_control_tables(document)
    doc_tables = list(document.tables)
    routed_tables = {id(anchor.table._tbl) for anchor in anchors}
    try:
        from compliance.lib import heading_parser

        contract_path = (
            REPO_ROOT / "compliance" / "datacontracts" / "fedramp-ssp-v1.yaml"
        )
        if contract_path.is_file():
            contract = heading_parser.load_contract(contract_path)
            extracted = heading_parser.extract_from_docx(docx_path, contract)
            for obj_rec in extracted.get("objects") or []:
                for val in (obj_rec.get("properties") or {}).values():
                    items = val if isinstance(val, list) else [val]
                    for item in items:
                        if isinstance(item, dict) and isinstance(
                            item.get("table_index"), int
                        ):
                            t_idx = item["table_index"]
                            if 0 <= t_idx < len(doc_tables):
                                routed_tables.add(id(doc_tables[t_idx]._tbl))
    except Exception:
        pass

    def is_instruction(paragraph) -> bool:
        text = (paragraph.text or "").strip()
        if not text:
            return False
        if instruction_text_kind(text, DEFAULT_INSTRUCTION_TEXT_PREFIXES) is not None:
            return True
        return is_instruction_colored(paragraph, set(DEFAULT_INSTRUCTION_COLORS))

    report = Report(docx=docx_path.name)
    heading = "(front matter)"
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, document)
            text = (paragraph.text or "").strip()
            if (
                _heading_level(paragraph) is not None or CONTROL_TITLE.match(text)
            ) and text:
                heading = " ".join(text.split()).strip()
                continue
            images = len(paragraph._p.findall(f".//{{{namespace_a}}}blip"))
            if not text and not images:
                continue
            if is_instruction(paragraph):
                continue  # routed by the instruction-anchor extractor
            if text:
                report.blocks.append(Block("prose", heading, text, len(text)))
            elif images:
                report.blocks.append(Block("image", heading, "", 0))
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            if id(table._tbl) in routed_tables:
                continue
            rows = list(table.rows)
            header = (
                " | ".join((cell.text or "").strip()[:40] for cell in rows[0].cells)
                if rows
                else ""
            )
            report.blocks.append(
                Block(
                    "table",
                    heading,
                    header,
                    0,
                    cells=sum(len(row.cells) for row in rows),
                    cell_chars=sum(
                        len((cell.text or "").strip())
                        for row in rows
                        for cell in row.cells
                    ),
                )
            )
    return report


def classify(report: Report, *, fuzzy: bool = True) -> None:
    """Assign exactly one class to every block, in place."""
    exact, all_statements = _catalog_statements()
    for block in report.blocks:
        block.cls = _classify_one(block, exact, all_statements, fuzzy=fuzzy)


def _classify_one(
    block: Block, exact: set[str], statements: list[str], *, fuzzy: bool
) -> str:
    if block.kind == "table":
        return "structured-table"
    if block.kind == "image":
        return "diagram-image"

    text = block.text.strip()
    normalized = normalize(text)

    if normalized in exact:
        return "catalog-statement"
    if CONTROL_TITLE.match(text):
        return "control-title"
    if SIGNATURE_RULE.match(text) or text.lower() in FURNITURE_LINES:
        return "document-furniture"
    if BRACKETED.match(text):
        return "odp-placeholder"
    if text.startswith(("GSA Note", "GSA note", "Note:")):
        return "gsa-annotation"
    if GLOSSARY.match(text) and len(text) > 80:
        return "glossary-definition"
    if AUTHORITY.match(text):
        return "authority-citation"
    if len(normalized) < 12:
        return "document-furniture"

    if fuzzy:
        # Parameter substitution and agency tailoring make exact matching miss
        # text that is still the catalog's sentence. Compare only against
        # similar-length statements; the full cross product is far too slow.
        best = max(
            (
                difflib.SequenceMatcher(None, normalized, candidate).ratio()
                for candidate in statements
                if abs(len(candidate) - len(normalized)) < 30
            ),
            default=0.0,
        )
        if best >= 0.75:
            return "catalog-statement"

    return "template-narrative"


def render(report: Report, *, detail: int) -> str:
    lines: list[str] = []
    agg = report.by_class()
    total_blocks = sum(row["blocks"] for row in agg.values())
    total_chars = sum(row["chars"] for row in agg.values())

    lines.append(f"# Unrouted content disposition: {report.docx}")
    lines.append("")
    lines.append(f"{total_blocks} unrouted blocks, {total_chars} characters.")
    lines.append("")
    lines.append(f"| {'class':<20} | {'blocks':>6} | {'chars':>7} | disposition |")
    lines.append(f"| {'-' * 20} | {'-' * 6}:| {'-' * 7}:| --- |")
    group_of = {cls: name for name, members in DISPOSITION.items() for cls in members}
    for cls in CLASSES:
        row = agg.get(cls)
        if not row:
            continue
        lines.append(
            f"| {cls:<20} | {row['blocks']:>6} | {row['chars']:>7} "
            f"| {group_of.get(cls, '?')} |"
        )
    lines.append("")

    for name, members in DISPOSITION.items():
        blocks = sum(agg.get(cls, {}).get("blocks", 0) for cls in members)
        chars = sum(agg.get(cls, {}).get("chars", 0) for cls in members)
        share = (chars / total_chars * 100) if total_chars else 0.0
        lines.append(f"- **{name}**: {blocks} blocks, {chars} chars ({share:.1f}%)")

    if detail:
        for cls in ("template-narrative", "structured-table"):
            rows = report.by_heading(cls)[:detail]
            if not rows:
                continue
            lines.append("")
            lines.append(f"## {cls} by heading (top {len(rows)})")
            lines.append("")
            lines.append(f"| {'heading':<54} | {'n':>4} | {'chars':>7} |")
            lines.append(f"| {'-' * 54} | {'-' * 4}:| {'-' * 7}:|")
            for heading, count, chars in rows:
                lines.append(f"| {heading[:54]:<54} | {count:>4} | {chars:>7} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--json", type=Path, help="Write the full classification.")
    parser.add_argument(
        "--detail",
        type=int,
        default=15,
        help="Rows per per-heading breakdown; 0 to omit.",
    )
    parser.add_argument(
        "--no-fuzzy",
        action="store_true",
        help="Exact catalog matching only (faster, undercounts tailored text).",
    )
    args = parser.parse_args()

    if not args.docx.is_file():
        raise SystemExit(f"not a file: {args.docx}")

    report = collect(args.docx)
    classify(report, fuzzy=not args.no_fuzzy)
    print(render(report, detail=args.detail))

    if args.json:
        payload: dict[str, Any] = {
            "docx": report.docx,
            "summary": report.by_class(),
            "disposition": {
                name: {
                    "blocks": sum(
                        report.by_class().get(cls, {}).get("blocks", 0)
                        for cls in members
                    ),
                    "chars": sum(
                        report.by_class().get(cls, {}).get("chars", 0)
                        for cls in members
                    ),
                }
                for name, members in DISPOSITION.items()
            },
            "blocks": [
                {
                    "kind": block.kind,
                    "heading": block.heading,
                    "cls": block.cls,
                    "chars": block.length or block.cell_chars,
                    "cells": block.cells,
                    "text": block.text[:400],
                }
                for block in report.blocks
            ],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
