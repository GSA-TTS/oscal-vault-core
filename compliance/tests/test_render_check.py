"""Tests for the optional headless LibreOffice render check (#10).

No real LibreOffice needed: these drive the script via subprocess and simulate
the "installed" path with a fake soffice stub (success + failure), plus verify
the skip/require behavior when the binary is absent.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "compliance" / "scripts" / "render_check.py"


def _run(args, env=None):
    full_env = dict(os.environ)
    full_env["SOFFICE"] = ""
    # Neutralize real-soffice discovery on PATH without breaking /bin/sh + the
    # coreutils the fake stub needs: drop only the common install dirs is
    # fragile, so instead the fake stub is a self-contained Python script (no
    # PATH-resolved helpers) and we simply ensure no 'soffice'/'libreoffice'
    # is resolvable by pointing PATH at an empty dir plus the interpreter dir.
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=full_env,
    )


def _empty_path_env(tmp_path: Path) -> dict:
    """PATH with no soffice/libreoffice, used for skip/require tests."""
    empty = tmp_path / "emptybin"
    empty.mkdir(exist_ok=True)
    return {"PATH": str(empty), "SOFFICE": ""}


def _fake_soffice(tmp_path: Path, *, succeed: bool) -> Path:
    """A self-contained Python stub soffice (no PATH-resolved helpers).

    On success it writes the expected ``<stem>.pdf`` into ``--outdir`` exactly
    the way render_check derives the output name (``Path(infile).stem``); on
    failure it exits non-zero and writes nothing.
    """
    binary = tmp_path / "soffice"
    body = (
        f"#!{sys.executable}\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"SUCCEED = {succeed!r}\n"
        "args = sys.argv[1:]\n"
        "outdir = None\n"
        "infile = None\n"
        "i = 0\n"
        "while i < len(args):\n"
        "    a = args[i]\n"
        "    if a == '--outdir':\n"
        "        outdir = args[i + 1]; i += 2; continue\n"
        "    if a == '--headless':\n"
        "        i += 1; continue\n"
        "    if a == '--convert-to':\n"
        "        i += 2; continue\n"
        "    infile = a; i += 1\n"
        "if not SUCCEED:\n"
        "    sys.exit(1)\n"
        "stem = Path(infile).stem\n"
        "Path(outdir, stem + '.pdf').write_text('%PDF-1.4 fake')\n"
        "sys.exit(0)\n"
    )
    binary.write_text(body, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


def _docx(tmp_path: Path, name: str = "t.jinja.docx") -> Path:
    # Content is irrelevant; the fake soffice ignores it. Must exist.
    f = tmp_path / name
    f.write_bytes(b"PK\x03\x04 not a real docx")
    return f


class TestSkipAndRequire:
    def test_skips_cleanly_without_soffice(self, tmp_path):
        f = _docx(tmp_path)
        result = _run([str(f)], env=_empty_path_env(tmp_path))
        assert result.returncode == 0
        assert "BLOCKED" in result.stderr

    def test_require_fails_without_soffice(self, tmp_path):
        f = _docx(tmp_path)
        result = _run(["--require", str(f)], env=_empty_path_env(tmp_path))
        assert result.returncode == 2
        assert "ERROR" in result.stderr


class TestWithFakeSoffice:
    def test_passes_when_render_succeeds(self, tmp_path):
        f = _docx(tmp_path)
        soffice = _fake_soffice(tmp_path, succeed=True)
        result = _run([str(f)], env={"SOFFICE": str(soffice)})
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_fails_when_render_fails(self, tmp_path):
        f = _docx(tmp_path)
        soffice = _fake_soffice(tmp_path, succeed=False)
        result = _run([str(f)], env={"SOFFICE": str(soffice)})
        assert result.returncode == 1
        assert "FAIL" in result.stderr

    def test_missing_input_file_is_failure(self, tmp_path):
        soffice = _fake_soffice(tmp_path, succeed=True)
        result = _run([str(tmp_path / "nope.docx")], env={"SOFFICE": str(soffice)})
        assert result.returncode == 1
        assert "not found" in result.stderr

    def test_no_targets_ok(self, tmp_path):
        # Point at an empty dir's (nonexistent) templates by passing no files
        # while soffice exists: committed templates resolve from the repo, so
        # instead assert the explicit-empty case via a bogus but present binary
        # and no file args still finds the committed templates (>=1).
        soffice = _fake_soffice(tmp_path, succeed=True)
        result = _run([], env={"SOFFICE": str(soffice)})
        # Repo ships committed templates, so this renders them all OK.
        assert result.returncode == 0, result.stderr
        assert "templates rendered" in result.stderr
