"""Tests for the optional go-oscal cross-check script (ADR-019 Layer 1.5, R17).

These tests do NOT require the go-oscal binary. They exercise the script's
resolution + skip/require behavior via subprocess, using a fake binary to
simulate the "installed" path deterministically.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "compliance" / "scripts" / "crosscheck_go_oscal.py"


def _run(args, env=None):
    full_env = dict(os.environ)
    # Neutralize any real binary so tests are deterministic.
    full_env["GO_OSCAL"] = ""
    full_env["PATH"] = ""
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=full_env,
    )


def _write_ssp(path: Path) -> None:
    path.write_text(
        json.dumps({"system-security-plan": {"uuid": str(uuid.uuid4())}}),
        encoding="utf-8",
    )


def _fake_go_oscal(tmp_path: Path, *, exit_code: int) -> Path:
    """A stub 'go-oscal' that ignores args and exits with a fixed code.

    Uses an absolute /bin/sh shebang because the test neutralizes PATH.
    """
    binary = tmp_path / "go-oscal"
    binary.write_text(
        f"#!/bin/sh\nexit {exit_code}\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


class TestSkipWhenBinaryAbsent:
    def test_skips_cleanly_without_binary(self, tmp_path):
        f = tmp_path / "ssp.json"
        _write_ssp(f)
        result = _run([str(f)])
        assert result.returncode == 0
        assert "SKIP" in result.stderr

    def test_require_fails_without_binary(self, tmp_path):
        f = tmp_path / "ssp.json"
        _write_ssp(f)
        result = _run(["--require", str(f)])
        assert result.returncode == 2
        assert "ERROR" in result.stderr


class TestWithFakeBinary:
    def test_passes_when_binary_reports_valid(self, tmp_path):
        f = tmp_path / "ssp.json"
        _write_ssp(f)
        binary = _fake_go_oscal(tmp_path, exit_code=0)
        result = _run([str(f)], env={"GO_OSCAL": str(binary)})
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_fails_when_binary_reports_invalid(self, tmp_path):
        f = tmp_path / "ssp.json"
        _write_ssp(f)
        binary = _fake_go_oscal(tmp_path, exit_code=1)
        result = _run([str(f)], env={"GO_OSCAL": str(binary)})
        assert result.returncode == 1
        assert "FAIL" in result.stderr

    def test_no_targets_is_ok(self, tmp_path):
        binary = _fake_go_oscal(tmp_path, exit_code=0)
        result = _run([], env={"GO_OSCAL": str(binary)})
        assert result.returncode == 0
        assert "No OSCAL documents" in result.stderr
