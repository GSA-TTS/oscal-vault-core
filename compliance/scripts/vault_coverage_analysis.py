#!/usr/bin/env python3
"""Diagnostic: how close is the generated Obsidian vault to the source `.docx`?

For each system this computes a *content coverage ratio*:

    vault_words / docx_words

where:
  - docx_words  = every word in the source .docx (paragraphs + table cells),
                  the total universe of text a reader sees in Word.
  - vault_words = every word in the generated vault's human-authored content
                  (markdown note bodies), excluding generated scaffolding that
                  has no docx counterpart (Dataview dashboards, schema/registry
                  notes, validation-rule notes, templates, mapping notes) and
                  excluding YAML frontmatter and boilerplate provenance lines,
                  so the ratio reflects *recovered source prose*, not padding.

A ratio near 1.0 means the vault captures almost all of the document's text.
Chapter 13 control tables are captured by fismatic; Chapters 1-12 narrative is
captured by the heading_parser path. Note the vault legitimately *drops* some
docx text (blank-template boilerplate/instructions, repeated table headers,
page furniture), so 100% is not the target -- the useful signal is how much the
recent Chapters 1-12 + image + lossless-table work moved the ratio.

Usage:
    uv run python3 compliance/scripts/vault_coverage_analysis.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

_CORE_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE = Path.cwd()
_VAULT_DIR = _WORKSPACE / "examples" / "vault"


def load_vault_to_docx(registry: Path) -> Dict[str, Path]:
    """plan-id -> source .docx path (relative to the workspace), from the system registry."""
    import yaml

    data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    return {
        s["plan_id"]: _WORKSPACE / s["docx"]
        for s in data.get("systems", [])
        if s.get("plan_id") and s.get("docx")
    }


# Vault sub-trees that are *generated scaffolding*, not recovered from the docx.
# Excluded from the "recovered prose" numerator so the ratio isn't inflated by
# Dataview dashboards, schema/registry text, templates, and mapping notes.
_SCAFFOLD_DIRS = (
    "_meta/",
    "governance/",
    "Mappings/",
    ".snapshots/",
)
_SCAFFOLD_FILES = ("Index.md",)


def _wc(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _iter_block_items(parent: DocxDocument):
    body = parent.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def docx_word_count(docx_path: Path) -> Tuple[int, int, int]:
    """Return (total, paragraph_words, table_words) for a .docx."""
    doc = Document(str(docx_path))
    para_w = 0
    table_w = 0
    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            para_w += _wc(block.text)
        elif isinstance(block, Table):
            for row in block.rows:
                for cell in row.cells:
                    table_w += _wc(cell.text)
    return para_w + table_w, para_w, table_w


# --- Vault markdown word counting ------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
# Boilerplate provenance/scaffold lines we don't count as recovered prose.
_BOILERPLATE_LINES = (
    "> Recovered from SSP Chapters 1-12 by the contract-driven",
    "This component note was synthesized",
    "Synthetic placeholder",
    "Synthesized by `export_obsidian_vault.py`",
    "_(Not committed to the repo;",
)


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def _is_scaffold(relpath: str) -> bool:
    if relpath in _SCAFFOLD_FILES:
        return True
    return any(relpath.startswith(d) or f"/{d}" in relpath for d in _SCAFFOLD_DIRS)


def _markdown_prose_words(body: str) -> int:
    """Count words in a note body, excluding Dataview code blocks, embed
    directives, wikilink scaffolding markup, and known boilerplate lines."""
    # Drop fenced code blocks (Dataview dashboards, mermaid scaffolds).
    body = re.sub(r"```.*?```", " ", body, flags=re.DOTALL)
    kept: List[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(b) or b in stripped for b in _BOILERPLATE_LINES):
            continue
        # Image embeds carry no prose words.
        if stripped.startswith("![["):
            continue
        kept.append(line)
    text = "\n".join(kept)
    # Strip wikilink/markdown markup so we count words, not brackets.
    text = re.sub(r"\[\[([^\]|]+\|)?([^\]]+)\]\]", r"\2", text)  # [[a|b]] -> b
    text = text.replace("|", " ").replace("#", " ")
    return _wc(text)


@dataclass
class VaultStats:
    total_words: int = 0
    note_count: int = 0
    image_files: int = 0
    scaffold_words: int = 0


def vault_word_count(vault_dir: Path) -> VaultStats:
    stats = VaultStats()
    for md in sorted(vault_dir.rglob("*.md")):
        rel = md.relative_to(vault_dir).as_posix()
        if ".snapshots/" in rel:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue
        body = _strip_frontmatter(text)
        words = _markdown_prose_words(body)
        if _is_scaffold(rel):
            stats.scaffold_words += words
            continue
        stats.total_words += words
        stats.note_count += 1
    # Count committed image attachments.
    media = vault_dir / "Diagrams" / "media"
    if media.is_dir():
        stats.image_files = sum(1 for p in media.iterdir() if p.is_file())
    return stats


def main(argv: List[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--registry",
        type=Path,
        default=_WORKSPACE / "scripts" / "registry.yaml",
        help="System registry naming each plan-id and its source .docx (default: ./scripts/registry.yaml).",
    )
    args = parser.parse_args(argv)
    if not args.registry.is_file():
        print(f"ERROR: registry not found: {args.registry}", file=sys.stderr)
        return 2
    vault_to_docx = load_vault_to_docx(args.registry)

    header = (
        f"{'system':<28} {'docx':>8} {'vault':>8} {'ratio':>7} "
        f"{'para':>8} {'tables':>8} {'imgs':>5}"
    )
    print("=" * len(header))
    print("VAULT vs DOCX CONTENT COVERAGE  (vault recovered-prose / docx total)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    tot_docx = 0
    tot_vault = 0
    tot_imgs = 0
    rows: List[Tuple[str, float]] = []

    for plan_id, docx_path in vault_to_docx.items():
        docx_name = docx_path.name
        vault_path = _VAULT_DIR / plan_id
        if not docx_path.exists():
            print(f"{plan_id:<28} [SKIP] docx not found: {docx_name}", file=sys.stderr)
            continue
        if not vault_path.is_dir():
            print(f"{plan_id:<28} [SKIP] vault not found", file=sys.stderr)
            continue

        d_total, d_para, d_table = docx_word_count(docx_path)
        v = vault_word_count(vault_path)
        ratio = (v.total_words / d_total) if d_total else 0.0

        print(
            f"{plan_id:<28} {d_total:>8} {v.total_words:>8} {ratio * 100:>6.1f}% "
            f"{d_para:>8} {d_table:>8} {v.image_files:>5}"
        )
        tot_docx += d_total
        tot_vault += v.total_words
        tot_imgs += v.image_files
        rows.append((plan_id, ratio))

    print("-" * len(header))
    agg = (tot_vault / tot_docx) if tot_docx else 0.0
    print(
        f"{'TOTALS':<28} {tot_docx:>8} {tot_vault:>8} {agg * 100:>6.1f}% "
        f"{'':>8} {'':>8} {tot_imgs:>5}"
    )
    if rows:
        best = max(rows, key=lambda r: r[1])
        worst = min(rows, key=lambda r: r[1])
        print()
        print(f"  best coverage : {best[0]} ({best[1] * 100:.1f}%)")
        print(f"  worst coverage: {worst[0]} ({worst[1] * 100:.1f}%)")

    print()
    print("Notes:")
    print("  docx  = every word in the source .docx (paragraphs + table cells)")
    print("  vault = recovered prose in vault note bodies (excludes frontmatter,")
    print("          Dataview dashboards, schema/template/mapping/index scaffold,")
    print("          image-embed directives, and provenance boilerplate lines)")
    print("  ratio = vault / docx. <100% is expected: the vault legitimately")
    print("          drops blank-template instructions, repeated table headers,")
    print("          and page furniture that carry no system-specific content.")
    print("  imgs  = extracted diagram image files under Diagrams/media/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
