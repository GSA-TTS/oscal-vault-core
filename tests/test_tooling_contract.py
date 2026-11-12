"""Protect the base-only inventory boundary and pinned tooling wiring."""

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_inventory_gate_never_checks_out_or_runs_pr_code():
    path = ROOT / ".github/workflows/guard-system-content.yml"
    data = yaml.safe_load(path.read_text())
    assert data["permissions"] == {"contents": "read", "pull-requests": "read"}
    steps = data["jobs"]["guard"]["steps"]
    checkout = next(
        step for step in steps if step.get("uses", "").startswith("actions/checkout@")
    )
    assert "ref" not in checkout["with"]
    assert checkout["with"]["persist-credentials"] is False
    for step in steps:
        assert "${{" not in step.get("run", "")
    assert (
        steps[-1]["run"]
        == 'python compliance/scripts/check_system_content.py --github-pr "$PR_NUMBER"'
    )
    config = yaml.safe_load((ROOT / "zizmor.yml").read_text())
    assert config["rules"]["dangerous-triggers"]["ignore"] == [
        "guard-system-content.yml"
    ]


def test_mise_verify_contains_all_required_gates():
    data = tomllib.loads((ROOT / "mise.toml").read_text())
    assert set(data["tasks"]["verify"]["depends"]) == {
        "lint",
        "audit",
        "security",
        "test",
    }
    for tool in (
        "python",
        "node",
        "uv",
        "pnpm",
        "ruff",
        "prek",
        "git-cliff",
        "betterleaks",
        "zizmor",
    ):
        assert data["tools"][tool] != "latest"
    assert yaml.safe_load((ROOT / "pnpm-workspace.yaml").read_text())["ignoreScripts"]
    assert (ROOT / "pnpm-lock.yaml").is_file()


def test_ci_uses_sha_pinned_mise_and_local_verify():
    data = yaml.safe_load((ROOT / ".github/workflows/validate.yml").read_text())
    steps = data["jobs"]["validate"]["steps"]
    assert any(
        step.get("uses") == "jdx/mise-action@d5574b52816a478f9d4e6529a3f6ff17a8a36785"
        for step in steps
    )
    assert any(step.get("run") == "mise run verify" for step in steps)
