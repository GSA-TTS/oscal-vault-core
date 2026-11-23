"""Restricted local DOCX redline (synthetic fixtures only)."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document

from compliance.lib import docx_redline as rl

SCRIPT = Path(rl.__file__).resolve().parents[1] / "scripts" / "generate_docx_redline.py"


def _docx(path: Path, paragraphs: list[str], table_cell: str | None = None) -> Path:
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    if table_cell is not None:
        doc.add_table(rows=1, cols=1).cell(0, 0).text = table_cell
    doc.save(path)
    return path


def test_extract_text_includes_table_paragraphs(tmp_path: Path) -> None:
    path = _docx(tmp_path / "a.docx", ["body"], table_cell="cell text")
    assert rl.extract_text(path).splitlines() == ["body", "cell text"]


def test_split_hunks_keeps_headers_and_boundaries() -> None:
    lines = ["--- l", "+++ r", "@@ -1 +1 @@", "-a", "+b", "@@ -3 +3 @@", "-x", "+y"]
    chunks = rl.split_hunks(lines, max_lines=5)
    assert len(chunks) == 2
    assert all(c[:2] == ["--- l", "+++ r"] for c in chunks)
    assert all(any(x.startswith("@@") for x in c) for c in chunks)


def test_generate_writes_all_outputs_with_warning_and_escaped_html(
    tmp_path: Path,
) -> None:
    left = _docx(tmp_path / "left.docx", ["<unsafe>", "old"])
    right = _docx(tmp_path / "right.docx", ["<unsafe>", "new"])
    out = tmp_path / "review"
    manifest = rl.generate(left, right, out, max_chunk_lines=4)
    assert manifest["warning"] == rl.WARNING
    assert manifest["source"]["sha256"] == rl.sha256(left)
    html = Path(manifest["html"]).read_text(encoding="utf-8")
    assert "&lt;unsafe&gt;" in html and "<unsafe>" not in html
    assert rl.WARNING in html
    assert json.loads((out / "manifest.json").read_text())["diff_lines"]
    for name in [manifest["full_docx"], *manifest["split_docx"]]:
        with ZipFile(name) as archive:
            assert "word/document.xml" in archive.namelist()
        assert rl.WARNING in rl.extract_text(Path(name))


def test_identical_documents_still_produce_a_manifest(tmp_path: Path) -> None:
    left = _docx(tmp_path / "l.docx", ["same"])
    right = _docx(tmp_path / "r.docx", ["same"])
    manifest = rl.generate(left, right, tmp_path / "out")
    assert manifest["diff_lines"] == 0 and manifest["split_docx"]


@pytest.mark.parametrize("marker", [".git", ".git/"])
def test_rejects_output_directory_inside_a_git_worktree(
    tmp_path: Path, marker: str
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    left = _docx(tmp_path / "l.docx", ["a"])
    right = _docx(tmp_path / "r.docx", ["b"])
    with pytest.raises(rl.RedlineError, match="outside any git"):
        rl.generate(left, right, repo / "nested" / "out")
    assert not (repo / "nested").exists()


def test_rejects_output_directory_inside_a_linked_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "wt"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    with pytest.raises(rl.RedlineError):
        rl.check_output_dir(repo / "out")


def test_cli_returns_nonzero_for_unsafe_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    left = _docx(tmp_path / "l.docx", ["a"])
    right = _docx(tmp_path / "r.docx", ["b"])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(left),
            str(right),
            "--out-dir",
            str(repo / "o"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and "outside any git" in result.stderr


def test_module_has_no_network_or_launch_behavior() -> None:
    tree = ast.parse(Path(rl.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imported & {
        "subprocess",
        "webbrowser",
        "requests",
        "urllib",
        "socket",
        "os",
    }
