"""DI-007: keep integrity stages bounded and independent of executable scripts."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2]
MODULES = sorted((CORE / "compliance/lib").glob("docx_integrity*.py"))
MODULES.append(CORE / "compliance/scripts/docx_e2e_integrity.py")


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.stem)
def test_integrity_modules_and_functions_are_bounded(path):
    text = path.read_text()
    assert len(text.splitlines()) <= 400, path
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef):
            assert node.end_lineno - node.lineno + 1 <= 50, node.name
            # Preserve the existing optional decisions keyword on the fill API.
            assert len(node.args.args) <= 5, node.name
            decisions = sum(
                isinstance(
                    n, (ast.If, ast.For, ast.While, ast.IfExp, ast.ExceptHandler)
                )
                for n in ast.walk(node)
            )
            assert decisions <= 10, node.name


def test_integrity_libraries_do_not_import_executable_scripts():
    for path in MODULES:
        if path.parent.name != "lib":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("compliance.scripts"), path


def test_legacy_cli_imports_are_library_aliases():
    from compliance.scripts import docx_e2e_integrity as cli
    from compliance.lib import docx_integrity_pipeline as pipeline
    from compliance.lib import docx_integrity_fillback as fillback
    from compliance.lib import docx_integrity_context as context

    assert cli.execute_run is pipeline.execute_run
    assert cli.control_readback is fillback.control_readback
    assert cli.fillback_stage is fillback.fillback_stage
    assert cli.verified_fill_context is context.verified_fill_context


def test_fillback_library_imports_without_loading_cli_in_fresh_process():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import compliance.lib.docx_integrity_fillback; assert 'compliance.scripts.docx_e2e_integrity' not in sys.modules",
        ],
        cwd=CORE,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_executes_from_unrelated_directory_and_preserves_blocked_exit(tmp_path):
    import json

    output = tmp_path / "run"
    result = subprocess.run(
        [
            sys.executable,
            str(CORE / "compliance/scripts/docx_e2e_integrity.py"),
            "--source",
            str(tmp_path / "missing.docx"),
            "--system-id",
            "synthetic",
            "--workspace",
            str(tmp_path / "workspace"),
            "--output",
            str(output),
            "--authorized",
            "--strict",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads((output / "compare/final-report.json").read_text())
    assert report["status"] == "BLOCKED"
    assert report["review_status"] == "PENDING"
