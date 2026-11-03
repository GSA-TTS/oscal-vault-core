"""Workspace resolution, the vendored-core write guard, and init_workspace (ADR-028)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from compliance.lib import workspace as ws

ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_standalone(tmp_path: Path) -> Path:
    core = tmp_path / "core"
    core.mkdir(parents=True)
    _git("init", "-q", cwd=core)
    return core


def _make_nested(tmp_path: Path) -> Path:
    outer = tmp_path / "system"
    outer.mkdir(parents=True)
    _git("init", "-q", cwd=outer)
    core = outer / "vendor" / "core"
    core.mkdir(parents=True)
    return core


def _make_submodule(tmp_path: Path) -> Path:
    outer = tmp_path / "system"
    gitdir = outer / ".git" / "modules" / "vendor" / "core"
    gitdir.mkdir(parents=True)
    core = outer / "vendor" / "core"
    core.mkdir(parents=True)
    (core / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    return core


def _load(name: str):
    path = ROOT / "compliance" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_t_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ws.ENV_WORKSPACE, raising=False)
    monkeypatch.delenv(ws.ENV_ALLOW_IN_CORE, raising=False)
    monkeypatch.setattr(ws, "_noticed", False)


def test_vendoring_state_distinguishes_the_three_layouts(tmp_path):
    assert ws.vendoring_state(_make_standalone(tmp_path)) == ws.STANDALONE
    assert ws.vendoring_state(_make_submodule(tmp_path / "a")) == ws.SUBMODULE
    assert ws.vendoring_state(_make_nested(tmp_path / "b")) == ws.NESTED
    bare = tmp_path / "tarball"
    bare.mkdir()
    assert ws.vendoring_state(bare) == ws.STANDALONE


def test_linked_worktree_of_the_core_is_not_mistaken_for_a_submodule(tmp_path):
    core = tmp_path / "wt"
    gitdir = tmp_path / "core.git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    core.mkdir()
    (core / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    assert ws.vendoring_state(core) == ws.STANDALONE


def test_workspace_precedence_explicit_then_env_then_cwd(tmp_path, monkeypatch):
    a, b, c = (tmp_path / n for n in "abc")
    for d in (a, b, c):
        d.mkdir()
    monkeypatch.chdir(c)
    assert ws.resolve_workspace() == c.resolve()
    monkeypatch.setenv(ws.ENV_WORKSPACE, str(b))
    assert ws.resolve_workspace() == b.resolve()
    assert ws.resolve_workspace(a) == a.resolve()


@pytest.mark.parametrize("maker", [_make_submodule, _make_nested])
def test_vendored_core_refuses_writes_inside_itself(tmp_path, maker):
    core = maker(tmp_path)
    with pytest.raises(ws.WorkspaceError, match="vendored core"):
        ws.guard_output(core / ".exports" / "bundles", root=core)


@pytest.mark.parametrize("maker", [_make_submodule, _make_nested])
def test_vendored_core_allows_the_workspace_and_explicit_overrides(
    tmp_path, maker, monkeypatch
):
    core = maker(tmp_path)
    outside = tmp_path / "elsewhere" / "out"
    assert ws.guard_output(outside, root=core) == outside.resolve()
    inside = core / "x"
    assert ws.guard_output(inside, root=core, allow_in_core=True) == inside.resolve()
    monkeypatch.setenv(ws.ENV_ALLOW_IN_CORE, "1")
    assert ws.guard_output(inside, root=core) == inside.resolve()


def test_standalone_core_allows_writes_with_one_notice(tmp_path, capsys):
    core = _make_standalone(tmp_path)
    ws.guard_output(core / "out1", root=core)
    ws.guard_output(core / "out2", root=core)
    err = capsys.readouterr().err
    assert err.count("must not be committed") == 1


def test_a_path_that_only_shares_a_name_prefix_is_not_inside_the_core(tmp_path):
    core = _make_submodule(tmp_path)
    sibling = core.parent / (core.name + "-data")
    assert ws.guard_output(sibling, root=core) == sibling.resolve()


def test_bundle_defaults_to_the_workspace_not_the_core(tmp_path, monkeypatch):
    bundle = _load("bundle")
    core_exports = ROOT / ".exports"
    existed_before = core_exports.exists()
    work = tmp_path / "sys"
    (work / "examples" / "vault" / "s").mkdir(parents=True)
    monkeypatch.chdir(work)
    monkeypatch.setattr(sys, "argv", ["bundle.py", "--vault", "examples/vault/s"])
    monkeypatch.setattr(bundle, "build_bundle", lambda *a, **k: {"errors": []})
    try:
        bundle.main()
    except SystemExit:
        pass
    assert (work / ".exports" / "bundles").is_dir()
    assert core_exports.exists() == existed_before


def test_exporter_refuses_to_write_into_a_vendored_core(tmp_path, monkeypatch, capsys):
    core = _make_submodule(tmp_path)
    monkeypatch.setattr(ws, "core_root", lambda: core)
    exporter = _load("export_obsidian_vault")
    target = core / "examples" / "vault" / "s"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_obsidian_vault.py",
            "--oscal-json",
            str(ROOT / "compliance/tests/fixtures/oscal/ssp-source.example.json"),
            "--plan-id",
            "p",
            "--vault",
            str(target),
        ],
    )
    assert exporter.main() == 2
    assert "refusing to write inside the vendored core" in capsys.readouterr().err
    assert not target.exists()


def test_init_workspace_installs_idempotently_and_preserves_user_rules(tmp_path):
    init = _load("init_workspace")
    work = tmp_path / "sys"
    work.mkdir()
    (work / ".gitignore").write_text("node_modules/\n*.log\n", encoding="utf-8")
    assert init.main(["--workspace", str(work), "--dry-run"]) == 0
    assert not (work / "examples").exists()
    assert init.main(["--workspace", str(work), "--check"]) == 1
    assert init.main(["--workspace", str(work)]) == 0
    text = (work / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith("node_modules/\n*.log\n")
    for rule in ("*.docx", ".exports/", "review-output/", ".obsidian/"):
        assert rule in text.splitlines()
    assert "examples/vault/" not in text.splitlines()
    assert (work / "examples" / "vault").is_dir() and (work / "scripts").is_dir()
    before = text
    assert init.main(["--workspace", str(work)]) == 0
    assert (work / ".gitignore").read_text(encoding="utf-8") == before
    assert init.main(["--workspace", str(work), "--check"]) == 0


def test_init_workspace_ignore_vault_switches_in_and_out_cleanly(tmp_path):
    init = _load("init_workspace")
    work = tmp_path / "sys"
    work.mkdir()
    init.main(["--workspace", str(work)])
    init.main(["--workspace", str(work), "--ignore-vault"])
    assert "examples/vault/" in (work / ".gitignore").read_text().splitlines()
    init.main(["--workspace", str(work)])
    text = (work / ".gitignore").read_text()
    assert "examples/vault/" not in text.splitlines()
    assert text.count(init.BEGIN) == 1


def test_init_workspace_refuses_a_vendored_core_tree(tmp_path, monkeypatch):
    core = _make_submodule(tmp_path)
    monkeypatch.setattr(ws, "core_root", lambda: core)
    init = _load("init_workspace")
    assert init.main(["--workspace", str(core)]) == 2
    assert not (core / ".gitignore").exists()


def test_gitignore_block_actually_ignores_what_it_claims(tmp_path):
    init = _load("init_workspace")
    work = tmp_path / "sys"
    work.mkdir()
    _git("init", "-q", cwd=work)
    init.main(["--workspace", str(work)])
    for name in (
        "SSP.docx",
        ".exports/bundles/x.zst",
        "review-output/a.md",
        ".obsidian/app.json",
    ):
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        res = subprocess.run(
            ["git", "check-ignore", "-q", name], cwd=work, capture_output=True
        )
        assert res.returncode == 0, f"{name} is not ignored"
    keep = work / "examples" / "vault" / "s" / "Index.md"
    keep.parent.mkdir(parents=True)
    keep.write_text("x", encoding="utf-8")
    res = subprocess.run(
        ["git", "check-ignore", "-q", "examples/vault/s/Index.md"], cwd=work
    )
    assert res.returncode == 1  # tracked by default
