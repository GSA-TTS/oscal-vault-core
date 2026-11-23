"""Restricted, local-only DOCX and HTML prose redlines from two DOCX files.

The output carries raw document prose, so it is review-only and never meant to be published.
This module therefore:

* takes caller-supplied input paths and an explicit output directory;
* refuses an output directory inside any git working tree (or inside the core);
* never uploads, commits, attaches to a pull request, or launches a browser or office suite;
* stamps a restricted-review warning on every artifact and in the manifest.

Only extracted text is compared; formatting and DOCX metadata are not.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import json
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

WARNING = "RESTRICTED REVIEW-ONLY — DO NOT COMMIT, PUSH, OR DISTRIBUTE"


class RedlineError(ValueError):
    """The redline could not be produced safely."""


def extract_text(path: Path) -> str:
    """Paragraph-oriented text of a DOCX body, including paragraphs inside tables."""
    document = Document(str(path))
    lines = [
        "".join(node.text or "" for node in para.iter(qn("w:t"))).rstrip()
        for para in document.element.body.iter(qn("w:p"))
    ]
    return "\n".join(lines).strip() + "\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _enclosing_worktree(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def check_output_dir(out_dir: Path) -> Path:
    """Resolve ``out_dir`` and reject it if it sits inside any git working tree."""
    resolved = out_dir.expanduser().resolve()
    tree = _enclosing_worktree(resolved)
    if tree is not None:
        raise RedlineError(
            f"output directory must be outside any git repository or worktree: {resolved} "
            f"is inside {tree}"
        )
    return resolved


def unified_diff(left: Path, right: Path, context: int = 3) -> list[str]:
    return list(
        difflib.unified_diff(
            extract_text(left).splitlines(),
            extract_text(right).splitlines(),
            fromfile=left.name,
            tofile=right.name,
            lineterm="",
            n=context,
        )
    )


def split_hunks(lines: list[str], max_lines: int) -> list[list[str]]:
    """Split a unified diff on hunk boundaries without splitting individual hunks."""
    header = [line for line in lines if line.startswith(("---", "+++"))]
    hunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@") and current:
            hunks.append(current)
            current = []
        current.append(line)
    if current:
        hunks.append(current)
    chunks: list[list[str]] = []
    chunk = list(header)
    for hunk in hunks:
        if len(chunk) > len(header) and len(chunk) + len(hunk) > max_lines:
            chunks.append(chunk)
            chunk = list(header)
        chunk.extend(hunk)
    if len(chunk) > len(header) or not chunks:
        chunks.append(chunk)
    return chunks


def _kind(line: str) -> str:
    if line.startswith("+") and not line.startswith("+++"):
        return "addition"
    if line.startswith("-") and not line.startswith("---"):
        return "deletion"
    if line.startswith("@@"):
        return "hunk"
    return "context"


def write_docx(lines: list[str], path: Path, title: str, note: str) -> None:
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.6)
    section.bottom_margin = Inches(0.6)
    doc.add_heading(title, 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    warning = doc.add_paragraph()
    warning.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = warning.add_run(WARNING)
    run.bold = True
    run.font.size = Pt(11)
    doc.add_paragraph(note)
    style = doc.styles.add_style("Restricted Redline", WD_STYLE_TYPE.PARAGRAPH)
    style.font.name = "Liberation Mono"
    style.font.size = Pt(8)
    for line in lines:
        run = doc.add_paragraph(style="Restricted Redline").add_run(line or " ")
        kind = _kind(line)
        if kind == "addition":
            run.font.color.rgb = RGBColor(0x00, 0x66, 0x00)
        elif kind == "deletion":
            run.font.color.rgb = RGBColor(0x99, 0x00, 0x00)
        elif kind == "hunk":
            run.bold = True
    doc.save(path)


def write_html(lines: list[str], path: Path, title: str) -> None:
    rendered = "\n".join(
        f'<span class="{_kind(line)}">{html.escape(line)}</span>' for line in lines
    )
    path.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui;margin:2rem}pre{white-space:pre-wrap;"
        "font:12px monospace}.warning{color:#900;font-weight:bold}.addition{color:#063}"
        ".deletion{color:#900}.hunk{font-weight:bold;color:#036}</style></head><body>"
        f'<h1>{html.escape(title)}</h1><p class="warning">{html.escape(WARNING)}</p>'
        "<p>Extracted-text review aid. Formatting and DOCX metadata are not compared.</p>"
        f"<pre>{rendered}</pre></body></html>\n",
        encoding="utf-8",
    )


def generate(
    left: Path,
    right: Path,
    out_dir: Path,
    *,
    context: int = 3,
    max_chunk_lines: int = 600,
) -> dict[str, Any]:
    """Write a full DOCX redline, hunk-aligned split DOCX files, escaped HTML and a manifest."""
    out = check_output_dir(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lines = unified_diff(left, right, context)
    title = "Restricted DOCX Prose Redline"
    full_docx = out / "prose-redline-full.docx"
    html_path = out / "prose-redline.html"
    write_docx(lines, full_docx, title, "Complete extracted-text unified diff.")
    write_html(lines, html_path, title)
    chunks = split_hunks(lines, max_chunk_lines)
    split_paths = []
    for number, chunk in enumerate(chunks, start=1):
        path = out / f"prose-redline-part-{number:03d}-of-{len(chunks):03d}.docx"
        write_docx(chunk, path, title, f"Part {number} of {len(chunks)}.")
        split_paths.append(str(path))
    manifest = {
        "warning": WARNING,
        "source": {"path": str(left), "sha256": sha256(left)},
        "generated": {"path": str(right), "sha256": sha256(right)},
        "diff_lines": len(lines),
        "full_docx": str(full_docx),
        "html": str(html_path),
        "split_docx": split_paths,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
