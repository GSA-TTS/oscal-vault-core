#!/usr/bin/env python3
"""Dump ALL content from a FedRAMP SSP .docx into a single reviewable blob.

The current pipeline (`fismatic.SSPConverter` -> `compliance.lib.oscal_source`
-> `CanonicalControl` -> CRM/Backstage/Adapter) only extracts content from
Chapter 13 control-implementation tables. Everything else -- system
description, architecture, data flow, asset inventory, system
interconnections, inheritance, applicable laws, all paragraph text, all
images, all headers/footers, document metadata -- is silently dropped.

This script walks the .docx body in document order and emits a markdown
"catchall" blob organized by heading, with every paragraph, table, and
image labeled as either CAPTURED (currently routed into the pipeline) or
DROPPED (currently discarded). The blob is a review surface for deciding
where each heading's content should land in the target architecture.

Output is written under `examples/docx_catchall/` (gitignored by default
because real-SSP content can be sensitive). Override with `--output`.

Usage:
    uv run python3 compliance/scripts/docx_catchall_dump.py \\
        --docx <ssp>.docx

    # Custom output path:
    uv run python3 compliance/scripts/docx_catchall_dump.py --docx <ssp>.docx --output /tmp/my-dump.md

    # Dump every .docx under ./fismatic (or --docx-dir):
    uv run python3 compliance/scripts/docx_catchall_dump.py --all
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import io
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

_CORE_ROOT = Path(__file__).resolve().parents[2]
import sys as _ws_sys  # noqa: E402

_WS_CORE = Path(__file__).resolve().parents[2]
if str(_WS_CORE) not in _ws_sys.path:
    _ws_sys.path.insert(0, str(_WS_CORE))
from compliance.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_arguments,
    guard_output,
    resolve_workspace,
)

_WORKSPACE = resolve_workspace()
_FISMATIC_SRC = _CORE_ROOT / "fismatic" / "src" / "phase_1"

# Drawingml namespace for inline/anchor image detection
_NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_DEFAULT_OUT_DIR = _WORKSPACE / "examples" / "docx_catchall"

_CELL_TRUNCATE = 400
_PARA_TRUNCATE = 2000
_TABLE_ROW_CAP = 12  # show first N/2 and last N/2 rows when truncating


# ---------------------------------------------------------------------------
# fismatic.is_control_table (quiet reuse) -- classifies a table as a
# Chapter 13 control implementation table (CAPTURED) or OTHER (DROPPED).
# ---------------------------------------------------------------------------


def _ensure_fismatic_importable() -> None:
    p = str(_FISMATIC_SRC)
    if _FISMATIC_SRC.is_dir() and p not in sys.path:
        sys.path.insert(0, p)


def _is_control_table_quiet(table: Table) -> Tuple[bool, Optional[str]]:
    """Reuse fismatic.parser.is_control_table without its import side-effects."""
    _ensure_fismatic_importable()
    from fismatic.parser import is_control_table  # noqa: E402

    try:
        return is_control_table(table)
    except Exception:
        return False, None


# ---------------------------------------------------------------------------
# Body iteration in document order (paragraphs + tables interleaved)
# ---------------------------------------------------------------------------


def _iter_body_blocks(doc: DocxDocument) -> Iterable[Tuple[str, Any]]:
    """Yield (kind, obj) tuples in document order.

    kind is one of: 'paragraph', 'table', 'sectPr'.
    """
    body = doc.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield "paragraph", Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield "table", Table(child, doc)
        elif child.tag.endswith("}sectPr"):
            yield "sectPr", child
        else:
            yield f"other:{child.tag}", child


def _heading_level(para: Paragraph) -> Optional[int]:
    """Return 1-9 for Heading N styles, 0 for Title, None otherwise."""
    sn = para.style.name if para.style else ""
    if sn == "Title":
        return 0
    m = re.match(r"^Heading\s+(\d+)$", sn)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 9:
            return n
    return None


def _detect_images(para: Paragraph) -> List[str]:
    """Return rIds of inline/anchor images in this paragraph."""
    rids: List[str] = []
    el = para._element
    for tag_local in ("inline", "anchor"):
        for node in el.iter(f"{{{_NS_WP}}}{tag_local}"):
            blip = node.find(f".//{{{_NS_A}}}blip")
            if blip is not None:
                rid = blip.get(f"{{{_NS_R}}}embed") or blip.get(f"{{{_NS_R}}}link")
                if rid:
                    rids.append(rid)
    return rids


def _image_part_name(doc: DocxDocument, rid: str) -> str:
    try:
        part = doc.part.related_parts.get(rid)
        if part is not None and hasattr(part, "partname"):
            return str(part.partname)
    except Exception:
        pass
    return f"<unresolved {rid}>"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _trunc(s: str, n: int) -> str:
    if n is None or n <= 0:
        return s
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _md_escape_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def _render_table_grid(
    tbl: Table, cell_truncate: int = _CELL_TRUNCATE
) -> List[List[str]]:
    rows: List[List[str]] = []
    for row in tbl.rows:
        cells = []
        for cell in row.cells:
            txt = cell.text or ""
            cells.append(_trunc(txt, cell_truncate))
        rows.append(cells)
    return rows


def _emit_table_markdown(
    rows: List[List[str]], out: List[str], row_cap: int = _TABLE_ROW_CAP
) -> None:
    if not rows:
        out.append("_(empty table)_")
        return
    ncols = max(len(r) for r in rows)
    # Normalize ragged rows
    norm = [r + [""] * (ncols - len(r)) for r in rows]
    # Header + separator
    out.append("| " + " | ".join(_md_escape_cell(c) for c in norm[0]) + " |")
    out.append("|" + "|".join("---" for _ in range(ncols)) + "|")
    body_rows = norm[1:]
    if row_cap and row_cap > 0 and len(body_rows) > row_cap:
        half = row_cap // 2
        for r in body_rows[:half]:
            out.append("| " + " | ".join(_md_escape_cell(c) for c in r) + " |")
        out.append(f"| _… {len(body_rows) - row_cap} rows omitted …_ ||")
        for r in body_rows[-half:]:
            out.append("| " + " | ".join(_md_escape_cell(c) for c in r) + " |")
    else:
        for r in body_rows:
            out.append("| " + " | ".join(_md_escape_cell(c) for c in r) + " |")


def _list_marker(para: Paragraph) -> str:
    sn = para.style.name if para.style else ""
    if "Bullet" in sn:
        return "- "
    if "List Number" in sn or sn.startswith("List Number"):
        return "1. "
    return ""


# ---------------------------------------------------------------------------
# Main dump routine
# ---------------------------------------------------------------------------


def dump_docx(docx_path: Path, out_path: Path, *, full: bool = False) -> dict:
    """Produce the catchall markdown for one .docx. Returns inventory stats.

    ``full=True`` disables the human-readability truncation caps (per-cell
    ``_CELL_TRUNCATE``, per-paragraph ``_PARA_TRUNCATE``, per-table
    ``_TABLE_ROW_CAP``) so **no content is omitted**. This lossless mode is
    used by the data-extraction path (``heading_parser.extract_from_docx``);
    the default (``full=False``) keeps the caps for the human review blob.
    """
    doc = Document(str(docx_path))
    blocks = list(_iter_body_blocks(doc))

    # Truncation limits: 0 == unlimited (lossless).
    cell_cap = 0 if full else _CELL_TRUNCATE
    para_cap = 0 if full else _PARA_TRUNCATE
    row_cap = 0 if full else _TABLE_ROW_CAP

    # Pre-classify all tables once so we can label them as CAPTURED/DROPPED.
    table_classification: dict[int, Tuple[bool, Optional[str]]] = {}
    table_objs: dict[int, Table] = {}
    table_index = 0
    for kind, obj in blocks:
        if kind == "table":
            table_objs[table_index] = obj
            is_ctrl, ctrl_id = _is_control_table_quiet(obj)
            table_classification[table_index] = (is_ctrl, ctrl_id)
            table_index += 1

    # Walk blocks, grouping under the most recent heading.
    # Track per-heading paragraph + table + image counts.
    current_heading: Optional[Tuple[int, str]] = None  # (level, text)
    heading_stack: List[Tuple[int, str]] = []  # for outline
    heading_counts: Counter = Counter()
    body_out: List[str] = []
    para_idx = 0
    table_idx = 0
    image_idx = 0
    image_locations: List[Tuple[int, str, Optional[str], str]] = []
    # (image_idx, rId, heading_text, part_name)

    para_total = 0
    para_dropped = 0
    table_total = 0
    table_captured = 0
    table_dropped = 0
    image_total = 0

    for kind, obj in blocks:
        if kind == "paragraph":
            para = obj
            level = _heading_level(para)
            text = para.text.strip()
            style = para.style.name if para.style else "(none)"

            if level is not None and text:
                # New heading
                heading_stack.append((level, text))
                current_heading = (level, text)
                # Header line in body
                hashes = (
                    "#" * (level + 1) if level > 0 else "#"
                )  # Title=H1, Heading N -> H(N+1)
                # We'll use H1 for Title, H(level+1) for Heading N so the doc
                # stays nested correctly under the top-level # blob title.
                body_out.append("")
                body_out.append(f"{hashes} {text}")
                body_out.append(f"_style={style}, paragraph_idx={para_idx}_")
                heading_counts[text] += 0
                para_idx += 1
                para_total += 1
                para_dropped += 1  # heading itself is dropped by current parser
                continue

            # Non-heading paragraph
            para_total += 1
            images = _detect_images(para)
            if images:
                for rid in images:
                    image_total += 1
                    image_idx += 1
                    part_name = _image_part_name(doc, rid)
                    heading_text = current_heading[1] if current_heading else None
                    image_locations.append((image_idx, rid, heading_text, part_name))
                    body_out.append("")
                    body_out.append(
                        f"_[IMAGE #{image_idx} @ paragraph_idx={para_idx} "
                        f"style={style} rId={rid} part={part_name} — DROPPED]_"
                    )
            if text or images:
                # Treat as DROPPED (current parser ignores all paragraph text)
                para_dropped += 1
                marker = _list_marker(para)
                body_out.append(
                    f"_[PARA @ idx={para_idx} style={style} — DROPPED]_  \n"
                    f"{marker}{_trunc(text, para_cap)}"
                )
            para_idx += 1

        elif kind == "table":
            tbl = obj
            table_total += 1
            is_ctrl, ctrl_id = table_classification[table_idx]
            label = "CONTROL TABLE (CAPTURED)" if is_ctrl else "OTHER TABLE (DROPPED)"
            if is_ctrl:
                table_captured += 1
            else:
                table_dropped += 1
            heading_text = (
                current_heading[1] if current_heading else "(no preceding heading)"
            )
            heading_counts[heading_text] += 1

            body_out.append("")
            body_out.append(
                f"_[TABLE #{table_idx} @ heading={heading_text!r} "
                f"rows={len(tbl.rows)} cols={len(tbl.columns)} "
                f"class={label}" + (f" control_id={ctrl_id!r}_" if is_ctrl else "_")
            )
            _emit_table_markdown(
                _render_table_grid(tbl, cell_truncate=cell_cap),
                body_out,
                row_cap=row_cap,
            )
            table_idx += 1

        elif kind == "sectPr":
            # Section break — note it but don't render content
            body_out.append("")
            body_out.append("_(section break)_")

        else:
            body_out.append("")
            body_out.append(f"_(unhandled body element: {kind})_")

    # -- Compose final markdown --
    md: List[str] = []
    md.append(f"# Catchall Dump: {docx_path.name}")
    md.append("")
    md.append(
        f"> Generated by `scripts/docx_catchall_dump.py` on "
        f"{datetime.datetime.now().isoformat(timespec='seconds')}"
    )
    md.append(f"> Source: `{docx_path}`")
    md.append("> ")
    md.append(
        "> **Purpose**: surface ALL .docx content (not just Chapter 13 "
        "control tables) so architecture routing decisions can be made. "
        "Items tagged `DROPPED` are silently discarded by the current "
        "`fismatic` -> `oscal_source.py` -> `CanonicalControl` pipeline; "
        "items tagged `CAPTURED` are the only content that reaches "
        "`build_crm_projection.py` / `export_backstage_summary.py` / "
        "`assemble_adapter_response.py`."
    )
    md.append("")

    # -- Core properties --
    md.append("## Document Metadata (core properties)")
    md.append("")
    cp = doc.core_properties
    cp_fields = [
        ("author", cp.author),
        ("category", cp.category),
        ("comments", cp.comments),
        ("content_status", cp.content_status),
        ("created", cp.created.isoformat() if cp.created else None),
        ("identifier", cp.identifier),
        ("keywords", cp.keywords),
        ("language", cp.language),
        ("last_modified_by", cp.last_modified_by),
        ("modified", cp.modified.isoformat() if cp.modified else None),
        ("revision", cp.revision),
        ("subject", cp.subject),
        ("title", cp.title),
        ("version", cp.version),
    ]
    md.append("| field | value |")
    md.append("|---|---|")
    for k, v in cp_fields:
        md.append(f"| {k} | {_md_escape_cell(str(v)) if v is not None else ''} |")
    md.append("")

    # -- Inventory --
    md.append("## Document Inventory")
    md.append("")
    md.append(f"- body blocks: {len(blocks)}")
    md.append(f"- paragraphs (total): {para_total}")
    md.append(
        f"- tables (total): {table_total}  — "
        f"control tables (CAPTURED): {table_captured}, "
        f"other tables (DROPPED): {table_dropped}"
    )
    md.append(f"- inline/floating images: {image_total}  (all DROPPED)")
    md.append(f"- sections: {len(doc.sections)}")
    md.append("")

    # -- Heading outline --
    md.append("## Headings Outline (in document order)")
    md.append("")
    for level, text in heading_stack:
        if level == 0:
            md.append(f"- **{text}**  _(Title)_")
        else:
            indent = "  " * (level - 1)
            md.append(f"{indent}- H{level}: {text}")
    md.append("")

    # -- Body content (already composed in body_out) --
    md.append("## Body Content (in document order, grouped under each heading)")
    md.append("")
    md.append(
        "Each block is tagged with its paragraph/table index, style, and "
        "CAPTURED/DROPPED status. Tables show their full grid (truncated "
        "to first/last few rows when very long; cells truncated to "
        f"{_CELL_TRUNCATE} chars)."
    )
    md.append("")
    md.extend(body_out)
    md.append("")

    # -- All images inventory --
    md.append("## Images and Embedded Objects")
    md.append("")
    if image_locations:
        md.append("| # | rId | under heading | media part |")
        md.append("|---|---|---|---|")
        for n, rid, heading_text, part_name in image_locations:
            md.append(
                f"| {n} | {rid} | "
                f"{_md_escape_cell(heading_text or '(none)')} | "
                f"{_md_escape_cell(part_name)} |"
            )
    else:
        md.append("_(no images detected)_")
    md.append("")

    # -- Headers / footers per section --
    md.append("## Headers and Footers (per section)")
    md.append("")
    for i, section in enumerate(doc.sections):
        md.append(f"### Section {i + 1}")
        md.append("")
        for hf_kind, hf in [
            ("default header", section.header),
            ("first-page header", section.first_page_header),
            ("even-page header", section.even_page_header),
            ("default footer", section.footer),
            ("first-page footer", section.first_page_footer),
            ("even-page footer", section.even_page_footer),
        ]:
            try:
                is_linked = getattr(hf, "is_linked_to_previous", False)
            except Exception:
                is_linked = False
            if is_linked:
                md.append(f"- **{hf_kind}**: _linked to previous_")
                continue
            text_chunks: List[str] = []
            try:
                for p in hf.paragraphs:
                    t = p.text.strip()
                    if t:
                        text_chunks.append(t)
            except Exception as e:
                text_chunks.append(f"(error reading: {e})")
            joined = " / ".join(text_chunks) if text_chunks else "_(empty)_"
            md.append(f"- **{hf_kind}**: {joined}")
        md.append("")

    # -- Captured vs dropped summary --
    md.append("## Currently captured vs. dropped — summary")
    md.append("")
    md.append("| content type | CAPTURED | DROPPED | total |")
    md.append("|---|---|---|---|")
    md.append(f"| paragraphs | 0 | {para_dropped} | {para_total} |")
    md.append(f"| tables | {table_captured} | {table_dropped} | {table_total} |")
    md.append(f"| images | 0 | {image_total} | {image_total} |")
    md.append("")
    md.append(
        "**Observation**: the current pipeline ONLY sees control "
        "implementation tables (Chapter 13). Everything else — system "
        "name/owner/authorizing official, FIPS 199 categorization, "
        "system description, network architecture, asset inventory, "
        "data flow, system interconnections, applicable laws, all "
        "images — is silently discarded."
    )
    md.append("")
    md.append("### Top headings by DROPPED block count")
    md.append("")
    md.append("| heading | DROPPED blocks (tables + paragraphs) |")
    md.append("|---|---|")
    for h, n in heading_counts.most_common(20):
        md.append(f"| {_md_escape_cell(h or '(no preceding heading)')} | {n} |")
    md.append("")

    # -- Write --
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    return {
        "docx": str(docx_path),
        "output": str(out_path),
        "paragraphs": para_total,
        "tables": table_total,
        "tables_captured": table_captured,
        "tables_dropped": table_dropped,
        "images": image_total,
        "sections": len(doc.sections),
        "headings": len(heading_stack),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--docx",
        type=Path,
        default=None,
        help="Path to a FedRAMP SSP .docx (required unless --all)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output markdown path (default: examples/docx_catchall/<basename>.catchall.md)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Dump every .docx under --docx-dir (one .catchall.md each)",
    )
    p.add_argument(
        "--docx-dir",
        type=Path,
        default=_WORKSPACE / "fismatic",
        help="Directory of source .docx files for --all (default: ./fismatic)",
    )
    add_workspace_arguments(p)
    args = p.parse_args(argv)
    if not args.all and args.docx is None:
        p.error("give --docx <file> or --all")
    default_out_dir = resolve_workspace(args.workspace) / "examples" / "docx_catchall"

    if args.all:
        docx_dir = args.docx_dir
        files = sorted(docx_dir.glob("*.docx"))
        if not files:
            print(f"No .docx files found under {docx_dir}", file=sys.stderr)
            return 1
        try:
            out_dir = guard_output(
                default_out_dir, allow_in_core=args.allow_write_in_core
            )
        except WorkspaceError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            base = re.sub(r"[^\w.-]+", "_", f.stem)
            out = out_dir / f"{base}.catchall.md"
            with contextlib.redirect_stdout(io.StringIO()):
                # Silence fismatic's chatty parser internals
                stats = dump_docx(f, out)
            print(
                f"wrote {out}  — "
                f"tables={stats['tables']} "
                f"(captured={stats['tables_captured']}, "
                f"dropped={stats['tables_dropped']}), "
                f"paras={stats['paragraphs']}, "
                f"images={stats['images']}, "
                f"headings={stats['headings']}"
            )
        return 0

    docx = args.docx
    if not docx.exists():
        print(f"ERROR: docx not found: {docx}", file=sys.stderr)
        print(
            "Supply --docx, or place a sample at the default path. "
            "See README.md (no .docx files are committed to this repo).",
            file=sys.stderr,
        )
        return 2
    safe_stem = re.sub(r"[^\w.-]+", "_", docx.stem)
    try:
        out = guard_output(
            args.output or (default_out_dir / f"{safe_stem}.catchall.md"),
            allow_in_core=args.allow_write_in_core,
        )
    except WorkspaceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    with contextlib.redirect_stdout(io.StringIO()):
        stats = dump_docx(docx, out)
    print(f"wrote {stats['output']}")
    print(f"  paragraphs: {stats['paragraphs']}")
    print(
        f"  tables: {stats['tables']} "
        f"(captured={stats['tables_captured']}, dropped={stats['tables_dropped']})"
    )
    print(f"  images: {stats['images']}")
    print(f"  sections: {stats['sections']}")
    print(f"  headings: {stats['headings']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
