#!/usr/bin/env python3
"""Diagnostic: approximate system-specific prose per SSP by subtracting the
baseline template word count from the actual document word count, and break the
result down by chapter (1-12 narrative vs 13 control tables).

This is a *diagnostic* tool (read-only, no vault writes) to help explain why
Chapters 1-12 narrative content is not landing in the Obsidian vault.

For each .docx it counts words in three buckets:
  - body_paragraphs : running paragraph text (narrative prose)
  - tables          : all table cell text
  - control_tables  : subset of `tables` that fismatic classifies as a
                      Chapter 13 control-implementation table (CAPTURED today)

"Chapters 1-12 prose" is approximated as body_paragraphs + (tables minus
control_tables), i.e. everything the current extractor drops.

Usage:
    uv run python3 compliance/scripts/prose_delta_analysis.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

_CORE_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE = Path.cwd()
_FISMATIC_SRC = _CORE_ROOT / "fismatic" / "src" / "phase_1"

# ---- Template classification -------------------------------------------------
# Each actual SSP is compared against the baseline template that matches its
# FIPS level / product type. Templates are the "blank" docs shipped by FedRAMP.
TEMPLATES = {
    "low": "Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
    "moderate": "Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
    "high": "High-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
    "saas": "Leveraged-SaaS-CRM-SSPP-Template-[Contains-CUI]-02-01-2024.docx",
    "lightweight": "Lightweight-Security-Authorization-Process-Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
}


# Actual SSPs -> which template baseline to subtract. Taken from the system registry
# (``fips_199`` selects the template); an entry whose level is not a template key is reported
# without a delta.
def load_actual_ssps(registry: Path) -> Dict[str, str]:
    import yaml

    data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    return {
        s["docx"]: str(s.get("fips_199", "")).lower()
        for s in data.get("systems", [])
        if s.get("docx")
    }


def _ensure_fismatic_importable() -> None:
    p = str(_FISMATIC_SRC)
    if _FISMATIC_SRC.is_dir() and p not in sys.path:
        sys.path.insert(0, p)


def _wc(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _iter_block_items(parent: DocxDocument):
    """Yield paragraphs and tables in document order."""
    body = parent.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


# Chapter heading detection.
#
# FedRAMP SSP templates use *named* H1 headings (mostly WITHOUT chapter
# numbers), in a fixed order that maps to chapters 1-13. A minority of docs
# (for example, some agency SSPs) prefix an explicit number ("10 System Environment"). We
# handle both: an explicit leading number wins; otherwise we match the known
# heading title to its chapter ordinal.
_CHAPTER_RE = re.compile(r"^\s*(\d{1,2})(?:\.\d+)*\s+\S")

# Canonical FedRAMP SSP chapter titles (normalized: lowercased, collapsed
# whitespace, trailing punctuation stripped) -> chapter number.
_CHAPTER_TITLES = {
    "information system name": 1,
    "information system categorization": 2,
    "information system owner": 3,
    "authorizing official": 4,
    "assignment of security responsibility": 5,
    "other designated contacts": 6,
    "information system operational status": 7,
    "information system type": 8,
    "general system description": 9,
    "system environment": 10,
    "system interconnections": 11,
    "applicable laws and regulations": 12,
    "minimum security controls": 13,
    "security controls": 13,
}


def _normalize_heading(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip().lower()
    # strip a leading chapter number if present ("10 system environment")
    t = re.sub(r"^\d{1,2}(?:\.\d+)*\s+", "", t)
    return t.rstrip(" .:")


def _chapter_for_heading(text: str) -> Optional[int]:
    """Return the chapter number for an H1/Title heading, or None."""
    m = _CHAPTER_RE.match(text or "")
    if m:
        n = int(m.group(1))
        if 1 <= n <= 13:
            return n
    return _CHAPTER_TITLES.get(_normalize_heading(text))


def _table_text(table: Table) -> str:
    parts: List[str] = []
    for row in table.rows:
        for cell in row.cells:
            parts.append(cell.text)
    return "\n".join(parts)


@dataclass
class DocStats:
    name: str
    para_words: int = 0
    table_words: int = 0
    control_table_words: int = 0
    control_tables: int = 0
    other_tables: int = 0
    # words per top-level chapter number (1..13, 0 = pre-chapter/front-matter)
    chapter_para_words: Dict[int, int] = field(default_factory=dict)
    chapter_table_words: Dict[int, int] = field(default_factory=dict)

    @property
    def total_words(self) -> int:
        return self.para_words + self.table_words

    @property
    def dropped_words(self) -> int:
        """Narrative + non-control tables = what the extractor drops today."""
        return self.para_words + (self.table_words - self.control_table_words)


def analyze(docx_path: Path) -> DocStats:
    _ensure_fismatic_importable()
    from fismatic.docx_parser import DocxParser  # noqa: E402

    # Silence fismatic's chatty is_control_table prints.
    import contextlib
    import io

    doc = Document(str(docx_path))
    stats = DocStats(name=docx_path.name)

    current_chapter = 0
    parser = None
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            parser = DocxParser(str(docx_path))
        except Exception:
            parser = None

    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = block.text
            style = (block.style.name or "") if block.style else ""
            # A chapter boundary is an H1 or Title paragraph whose text matches
            # a known FedRAMP SSP chapter title (or carries an explicit number).
            if "Heading 1" in style or style == "Title":
                ch = _chapter_for_heading(text)
                if ch is not None:
                    current_chapter = ch
            w = _wc(text)
            stats.para_words += w
            stats.chapter_para_words[current_chapter] = (
                stats.chapter_para_words.get(current_chapter, 0) + w
            )
        elif isinstance(block, Table):
            txt = _table_text(block)
            w = _wc(txt)
            stats.table_words += w
            stats.chapter_table_words[current_chapter] = (
                stats.chapter_table_words.get(current_chapter, 0) + w
            )
            is_ctrl = False
            if parser is not None:
                with contextlib.redirect_stdout(io.StringIO()):
                    try:
                        is_ctrl = parser.is_control_table(block)
                    except Exception:
                        is_ctrl = False
            if is_ctrl:
                stats.control_table_words += w
                stats.control_tables += 1
            else:
                stats.other_tables += 1
    return stats


def _chapters_1_12(d: Dict[int, int]) -> int:
    return sum(v for k, v in d.items() if 1 <= k <= 12)


def _chapter_13(d: Dict[int, int]) -> int:
    return d.get(13, 0)


def main(argv: List[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--registry",
        type=Path,
        default=_WORKSPACE / "scripts" / "registry.yaml",
        help="System registry naming each source .docx and its fips_199 level (default: ./scripts/registry.yaml).",
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=_WORKSPACE / "templates",
        help="Directory holding the blank FedRAMP templates (default: ./templates).",
    )
    args = parser.parse_args(argv)
    if not args.registry.is_file():
        print(f"ERROR: registry not found: {args.registry}", file=sys.stderr)
        return 2
    actual_ssps = load_actual_ssps(args.registry)
    docx_root = _WORKSPACE

    template_stats: Dict[str, DocStats] = {}
    for key, fname in TEMPLATES.items():
        p = args.templates_dir / fname
        if p.exists():
            template_stats[key] = analyze(p)
        else:
            print(f"[WARN] template not found: {fname}", file=sys.stderr)

    print("=" * 100)
    print("TEMPLATE BASELINES (blank FedRAMP templates)")
    print("=" * 100)
    print(
        f"{'template':<12} {'total':>8} {'para':>8} {'tables':>8} "
        f"{'ctrlTbl':>8} {'ch1-12':>8} {'ch13':>8}"
    )
    for key, s in template_stats.items():
        ch_1_12 = _chapters_1_12(s.chapter_para_words) + _chapters_1_12(
            s.chapter_table_words
        )
        ch_13 = _chapter_13(s.chapter_para_words) + _chapter_13(s.chapter_table_words)
        print(
            f"{key:<12} {s.total_words:>8} {s.para_words:>8} {s.table_words:>8} "
            f"{s.control_table_words:>8} {ch_1_12:>8} {ch_13:>8}"
        )

    print()
    print("=" * 100)
    print("ACTUAL SSPs — system-specific prose = actual − template")
    print("=" * 100)
    header = (
        f"{'system':<48} {'total':>7} {'tmpl':>7} {'delta':>7} "
        f"{'ch1-12Δ':>8} {'ch13Δ':>7} {'ctrlTbl':>7} {'dropped':>8}"
    )
    print(header)
    print("-" * len(header))

    totals = {
        "delta": 0,
        "ch1_12_delta": 0,
        "ch13_delta": 0,
        "dropped": 0,
    }
    for fname, tkey in actual_ssps.items():
        p = docx_root / fname
        fname = p.name
        if not p.exists():
            print(f"[SKIP] not found: {fname}", file=sys.stderr)
            continue
        s = analyze(p)
        t = template_stats.get(tkey)
        tmpl_words = t.total_words if t else 0
        delta = s.total_words - tmpl_words

        s_1_12 = _chapters_1_12(s.chapter_para_words) + _chapters_1_12(
            s.chapter_table_words
        )
        s_13 = _chapter_13(s.chapter_para_words) + _chapter_13(s.chapter_table_words)
        t_1_12 = (
            _chapters_1_12(t.chapter_para_words) + _chapters_1_12(t.chapter_table_words)
            if t
            else 0
        )
        t_13 = (
            _chapter_13(t.chapter_para_words) + _chapter_13(t.chapter_table_words)
            if t
            else 0
        )
        ch_1_12_delta = s_1_12 - t_1_12
        ch_13_delta = s_13 - t_13

        short = fname if len(fname) <= 47 else fname[:44] + "..."
        print(
            f"{short:<48} {s.total_words:>7} {tmpl_words:>7} {delta:>7} "
            f"{ch_1_12_delta:>8} {ch_13_delta:>7} {s.control_table_words:>7} "
            f"{s.dropped_words:>8}"
        )
        totals["delta"] += delta
        totals["ch1_12_delta"] += ch_1_12_delta
        totals["ch13_delta"] += ch_13_delta
        totals["dropped"] += s.dropped_words

    print("-" * len(header))
    print(
        f"{'TOTALS':<48} {'':>7} {'':>7} {totals['delta']:>7} "
        f"{totals['ch1_12_delta']:>8} {totals['ch13_delta']:>7} {'':>7} "
        f"{totals['dropped']:>8}"
    )

    print()
    print("Legend:")
    print("  total     = all words in the actual .docx (paragraphs + tables)")
    print("  tmpl      = all words in the matching blank template")
    print("  delta     = total − tmpl  ≈ system-specific content added by authors")
    print("  ch1-12Δ   = system-specific words added in Chapters 1-12 (narrative)")
    print("  ch13Δ     = system-specific words added in Chapter 13 (control tables)")
    print("  ctrlTbl   = words in tables fismatic classifies as control tables")
    print("             (the ONLY content the current extractor captures)")
    print("  dropped   = narrative + non-control-table words the extractor discards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
