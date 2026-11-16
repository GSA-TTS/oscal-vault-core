"""Exercise core synthetic tooling in an independent consumer-shaped staged tree."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_core_roundtrip_runs_when_relocated_without_consumer_repository(tmp_path):
    core = Path(__file__).resolve().parents[2]
    staged = tmp_path / "consumer/vendor/oscal-vault-core"
    staged.mkdir(parents=True)
    for name in ("compliance", "fismatic", "scripts"):
        shutil.copytree(
            core / name,
            staged / name,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.docx", "node_modules", ".venv"
            ),
        )
    shutil.copy2(core / "pyproject.toml", staged / "pyproject.toml")
    shutil.copy2(core / "vault-schemas.json", staged / "vault-schemas.json")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "compliance/tests/test_docx_fill_roundtrip.py",
            "-q",
            "-m",
            "not slow",
            "-k",
            "TestSynthetic",
        ],
        cwd=staged,
        env={**os.environ, "PYTHONPATH": str(staged)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout
