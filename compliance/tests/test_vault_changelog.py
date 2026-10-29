"""Tests for the pin-advance change-summary tool (scripts/vault_changelog.py).

See docs/ADR/ADR-024 context and the SSPP handoff Need 6 (OPEN_WORK 6.4).
The frontmatter/parsing helpers are unit-tested directly; the git-backed
end-to-end path is smoke-tested against real repository history when the
expected commits are present (skips on a shallow/rewritten checkout).
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "compliance" / "scripts" / "vault_changelog.py"

_spec = importlib.util.spec_from_file_location("vault_changelog", _SCRIPT)
vault_changelog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vault_changelog)


def _make_control(vault_root: Path, system: str, name: str, body: str) -> None:
    p = vault_root / "examples" / "vault" / system / "Controls" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


class TestParseFrontmatter:
    def test_parses_control_id_and_params(self):
        text = (
            "---\n"
            "control_id: AC-02\n"
            "parameters:\n"
            "- param_id: ac-02_odp.06\n"
            "  value: 14 days\n"
            "---\n"
            "# AC-02\n"
        )
        fm = vault_changelog._parse_frontmatter(text)
        assert fm["control_id"] == "AC-02"
        assert fm["parameters"][0]["param_id"] == "ac-02_odp.06"

    def test_no_frontmatter_returns_empty(self):
        assert vault_changelog._parse_frontmatter("# just a heading\n") == {}

    def test_malformed_yaml_returns_empty(self):
        text = "---\n: : : not valid\n  - broken\n---\nbody\n"
        assert vault_changelog._parse_frontmatter(text) == {}


class TestTreeReaders:
    def test_control_counts(self, tmp_path):
        _make_control(tmp_path, "sys-a", "AC-01.md", "---\ncontrol_id: AC-01\n---\n")
        _make_control(tmp_path, "sys-a", "AC-02.md", "---\ncontrol_id: AC-02\n---\n")
        _make_control(tmp_path, "sys-b", "AC-01.md", "---\ncontrol_id: AC-01\n---\n")
        counts = vault_changelog._control_counts(tmp_path)
        assert counts == {"sys-a": 2, "sys-b": 1}

    def test_guidance_counts(self, tmp_path):
        _make_control(
            tmp_path,
            "sys-a",
            "AC-01.md",
            "---\ncontrol_id: AC-01\n---\n> [!note]- Template guidance\n",
        )
        _make_control(tmp_path, "sys-a", "AC-02.md", "---\ncontrol_id: AC-02\n---\n")
        guidance = vault_changelog._guidance_counts(tmp_path)
        assert guidance == {"sys-a": (2, 1)}

    def test_odp_values(self, tmp_path):
        _make_control(
            tmp_path,
            "sys-a",
            "AC-02.md",
            "---\n"
            "control_id: AC-02\n"
            "parameters:\n"
            "- param_id: ac-02_odp.06\n"
            "  value: 14 days\n"
            "---\n",
        )
        odp = vault_changelog._odp_values(tmp_path)
        assert odp == {"sys-a::AC-02": {"ac-02_odp.06": "14 days"}}

    def test_vault_systems_skips_dotdirs(self, tmp_path):
        _make_control(tmp_path, "sys-a", "AC-01.md", "---\ncontrol_id: AC-01\n---\n")
        (tmp_path / "examples" / "vault" / ".snapshots").mkdir(parents=True)
        assert vault_changelog._vault_systems(tmp_path) == {"sys-a"}


def _init_repo(root: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
            cwd=root,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    _make_control(root, "sys-a", "AC-01.md", "---\ncontrol_id: AC-01\n---\n")
    git("add", ".")
    git("commit", "-q", "-m", "one")
    _make_control(root, "sys-a", "AC-02.md", "---\ncontrol_id: AC-02\n---\n")
    git("add", ".")
    git("commit", "-q", "-m", "two")


class TestEndToEnd:
    """Runs against a throwaway git repository, not the checkout's own history."""

    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        monkeypatch.setattr(vault_changelog, "_WORKSPACE", tmp_path)
        return tmp_path

    def test_added_control_is_reported_between_refs(self, repo):
        report = vault_changelog.build_changelog("HEAD~1", "HEAD")
        assert "sys-a" in report
        assert "_No control-count changes._" not in report

    def test_identical_refs_report_no_changes(self, repo):
        report = vault_changelog.build_changelog("HEAD", "HEAD")
        assert "_No control-count changes._" in report
        assert "_No ODP value changes._" in report
