#!/usr/bin/env python3
"""Diagnostic: which docx headings/tables are NOT captured by the contract?

The vault-coverage analysis (`vault_coverage_analysis.py`) shows an aggregate
gap between docx and vault word counts. This tool localizes that gap: for each
source `.docx` it walks the body in heading order and reports, per top-level
heading, how many words live under it and whether the contract's
`heading_parser` has a `sourceHeading` that would capture it.

Headings are matched against the contract's declared source headings (exact,
case-insensitive, and `regex:` patterns) the same way `heading_parser` does, so
"captured" here means "a contract property points at this heading".

Output ranks *uncaptured* headings by word count (descending) so the
highest-value gaps to close are at the top.

Usage:
    uv run python3 compliance/scripts/coverage_gap_analysis.py
    uv run python3 compliance/scripts/coverage_gap_analysis.py --system <system>
    uv run python3 compliance/scripts/coverage_gap_analysis.py --top 25
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

_CORE_ROOT = Path(__file__).resolve().parents[2]
if str(_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT))
_WORKSPACE = Path.cwd()
_CONTRACT = _CORE_ROOT / "compliance" / "datacontracts" / "fedramp-ssp-v1.yaml"


def load_vault_to_docx(registry: Path) -> Dict[str, Path]:
    """plan-id -> source .docx path (relative to the workspace), from the system registry."""
    import yaml

    data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    return {
        s["plan_id"]: _WORKSPACE / s["docx"]
        for s in data.get("systems", [])
        if s.get("plan_id") and s.get("docx")
    }


def _wc(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _iter_block_items(parent: DocxDocument):
    body = parent.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _load_contract_headings() -> Tuple[List[str], List[re.Pattern]]:
    """Return (exact_headings_lower, compiled_regexes) from the contract."""
    from compliance.lib.heading_parser import load_contract

    contract = load_contract(str(_CONTRACT))
    exact: set = set()
    regexes: List[re.Pattern] = []
    for obj in contract.destinations():
        for prop in obj.properties:
            for h in _all_headings_for_prop(prop):
                if not h:
                    continue
                if h.startswith("regex:"):
                    try:
                        regexes.append(re.compile(h[len("regex:") :], re.IGNORECASE))
                    except re.error:
                        pass
                else:
                    exact.add(h.strip().lower())
    return sorted(exact), regexes


def _all_headings_for_prop(prop) -> List[str]:
    out: List[str] = []
    for attr in ("source_heading", "alias_source_heading"):
        v = getattr(prop, attr, None)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, (list, tuple)):
            out.extend(x for x in v if isinstance(x, str))
    return out


def _heading_is_captured(
    text: str, exact: List[str], regexes: List[re.Pattern]
) -> bool:
    t = re.sub(r"\s+", " ", text or "").strip()
    tl = t.lower().rstrip(" .:")
    # exact / prefix match (contract headings are often a prefix of the doc's)
    for h in exact:
        h2 = h.rstrip(" .:")
        if tl == h2 or tl.startswith(h2) or h2.startswith(tl) and len(tl) > 6:
            return True
    for rx in regexes:
        if rx.search(t):
            return True
    return False


def _heading_level(p: Paragraph) -> Optional[int]:
    style = (p.style.name if p.style else "") or ""
    if style == "Title":
        return 0
    m = re.match(r"Heading (\d+)", style)
    if m:
        return int(m.group(1))
    return None


@dataclass
class HeadingBucket:
    text: str
    level: int
    para_words: int = 0
    table_words: int = 0
    table_count: int = 0
    captured: bool = False

    @property
    def total(self) -> int:
        return self.para_words + self.table_words


def analyze_docx(docx_path: Path, exact, regexes) -> List[HeadingBucket]:
    doc = Document(str(docx_path))
    buckets: List[HeadingBucket] = []
    current: Optional[HeadingBucket] = None
    # Only treat H1/H2 as bucket boundaries so we roll sub-sections up into the
    # nearest major heading (matches how the contract keys on major headings).
    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            lvl = _heading_level(block)
            text = block.text.strip()
            if lvl is not None and lvl <= 2 and text:
                current = HeadingBucket(
                    text=text,
                    level=lvl,
                    captured=_heading_is_captured(text, exact, regexes),
                )
                buckets.append(current)
            elif current is not None:
                current.para_words += _wc(text)
        elif isinstance(block, Table):
            if current is not None:
                for row in block.rows:
                    for cell in row.cells:
                        current.table_words += _wc(cell.text)
                current.table_count += 1
    return buckets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--system", help="Only analyze this vault plan-id")
    ap.add_argument("--top", type=int, default=15, help="Top-N uncaptured headings")
    ap.add_argument(
        "--registry",
        type=Path,
        default=_WORKSPACE / "scripts" / "registry.yaml",
        help="System registry naming each plan-id and its source .docx (default: ./scripts/registry.yaml).",
    )
    args = ap.parse_args()
    if not args.registry.is_file():
        print(f"ERROR: registry not found: {args.registry}", file=sys.stderr)
        return 2
    VAULT_TO_DOCX = load_vault_to_docx(args.registry)

    exact, regexes = _load_contract_headings()

    systems = (
        {args.system: VAULT_TO_DOCX[args.system]}
        if args.system and args.system in VAULT_TO_DOCX
        else VAULT_TO_DOCX
    )

    # Aggregate uncaptured heading -> total words across all systems.
    agg_uncaptured: Dict[str, int] = defaultdict(int)
    agg_uncaptured_tables: Dict[str, int] = defaultdict(int)
    grand_captured = 0
    grand_uncaptured = 0

    for plan_id, docx_path in systems.items():
        docx_name = docx_path.name
        if not docx_path.exists():
            print(f"[SKIP] {plan_id}: docx not found", file=sys.stderr)
            continue
        buckets = analyze_docx(docx_path, exact, regexes)
        cap = sum(b.total for b in buckets if b.captured)
        uncap = sum(b.total for b in buckets if not b.captured)
        grand_captured += cap
        grand_uncaptured += uncap

        print(f"\n=== {plan_id} ({docx_name}) ===")
        print(f"  captured under known headings : {cap:>8} words")
        print(f"  UNcaptured (dropped) headings : {uncap:>8} words")
        top = sorted(
            (b for b in buckets if not b.captured and b.total > 0),
            key=lambda b: b.total,
            reverse=True,
        )[: args.top]
        for b in top:
            norm = re.sub(r"\s+", " ", b.text)[:52]
            print(
                f"    - {b.total:>6}w  (tbl={b.table_words:>6}w x{b.table_count})  "
                f"H{b.level} {norm!r}"
            )
            key = re.sub(r"\s+", " ", b.text).strip().lower()[:60]
            agg_uncaptured[key] += b.total
            agg_uncaptured_tables[key] += b.table_words

    print("\n" + "=" * 78)
    print("AGGREGATE: highest-value UNCAPTURED headings across all systems")
    print("=" * 78)
    ranked = sorted(agg_uncaptured.items(), key=lambda kv: kv[1], reverse=True)
    for key, words in ranked[: args.top]:
        tw = agg_uncaptured_tables.get(key, 0)
        print(f"  {words:>8}w  (tbl={tw:>8}w)  {key!r}")

    total = grand_captured + grand_uncaptured
    pct = (grand_captured / total * 100) if total else 0.0
    print("\n" + "-" * 78)
    print(
        f"TOTALS: captured {grand_captured} / {total} heading-words "
        f"({pct:.1f}% of headed content is under a contract heading)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
