"""The system-content guard: what it refuses, what it allows, and that it fails closed (ADR-028)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from compliance.lib.system_content_guard import (
    Entry,
    check_entries,
    entries_from_github_files,
    load_allowlist,
)

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = ROOT / "compliance" / "data" / "blank_template_allowlist.json"
SCRIPT = ROOT / "compliance" / "scripts" / "check_system_content.py"
BLANK_SHA = "b83e5223dbad8a518b4d54dbd3098dea944cfbfb"


def rules(entries, allowed=(), allowed_paths=()):
    return sorted({v.rule for v in check_entries(entries, allowed, allowed_paths)})


def test_unknown_docx_is_refused_even_with_an_innocent_name():
    assert rules([Entry("docs/notes.docx", blob_sha="a" * 40)]) == ["office-document"]


@pytest.mark.parametrize("name", ["a.doc", "b.ods", "c.xlsx", "d.DOCX"])
def test_other_office_formats_and_case_variants_are_refused(name):
    assert rules([Entry(name, blob_sha="0" * 40)]) == ["office-document"]


def test_allowlisted_blank_template_blob_is_accepted_only_by_hash():
    allowed = {BLANK_SHA}
    assert (
        rules(
            [
                Entry(
                    "compliance/templates/moderate-sspp.jinja.docx", blob_sha=BLANK_SHA
                )
            ],
            allowed,
        )
        == []
    )
    # same name, different bytes: refused
    assert rules(
        [Entry("compliance/templates/moderate-sspp.jinja.docx", blob_sha="f" * 40)],
        allowed,
    ) == ["office-document"]
    # allowlisted bytes under a new name are fine (it is the same blank template)
    assert rules([Entry("elsewhere/copy.docx", blob_sha=BLANK_SHA)], allowed) == []


def test_existing_allowlisted_template_path_may_be_reviewed_for_update():
    allowed_paths = {"templates/Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx"}
    assert (
        rules([Entry(next(iter(allowed_paths)), blob_sha="f" * 40)], (), allowed_paths)
        == []
    )
    assert rules(
        [Entry("templates/unreviewed.docx", blob_sha="f" * 40)], (), allowed_paths
    ) == ["office-document"]


def test_missing_blob_hash_fails_closed():
    assert rules([Entry("x.docx", blob_sha=None)], {BLANK_SHA}) == ["office-document"]


@pytest.mark.parametrize(
    "path",
    [
        "examples/vault/data-gov-2026/Controls/AC-01.md",
        "examples/anything.txt",
        "a/b/.exports/oscal-json/x.json",
        "x/.snapshots/current/y.json",
        "review-output/m.manifest.json",
        "sub/roundtrip_review/a.md",
        "examples/docx_catchall/s.catchall.md",
    ],
)
def test_generated_output_directories_are_refused(path):
    assert "generated-output" in rules([Entry(path)])


def test_look_alike_names_are_not_false_positives():
    assert (
        rules([Entry("docs/examples-guide.md"), Entry("compliance/lib/exports.py")])
        == []
    )
    assert rules([Entry("docs/ADR/ADR-009.md")]) == []


def test_generated_vault_note_frontmatter_is_refused_outside_docs_and_tests():
    note = "---\ntype: oscal.control\ncontrol_id: AC-01\n---\n"
    assert rules([Entry("notes/AC-01.md", text=note)]) == ["generated-note"]
    assert rules([Entry("docs/example.md", text=note)]) == []
    assert rules([Entry("compliance/tests/fixtures/AC-01.md", text=note)]) == []


def test_generated_note_detected_from_a_diff_hunk():
    patch = "@@ -0,0 +1,4 @@\n+---\n+type: oscal.control\n+control_id: AC-01\n+---"
    assert rules([Entry("AC-01.md", patch=patch)]) == ["generated-note"]


def test_removals_never_violate():
    assert (
        rules(
            [
                Entry("examples/x.md", status="removed"),
                Entry("y.docx", status="removed"),
            ]
        )
        == []
    )


def test_github_payload_adapter_maps_status_and_hash():
    files = [
        {"filename": "a.docx", "status": "added", "sha": "1" * 40},
        {"filename": "b.md", "status": "removed", "sha": "2" * 40},
        {
            "filename": "c.md",
            "status": "modified",
            "sha": "3" * 40,
            "patch": "@@\n+type: oscal.x",
        },
    ]
    entries = entries_from_github_files(files)
    assert [e.status for e in entries] == ["added", "removed", "modified"]
    assert rules(entries) == ["generated-note", "office-document"]


def test_allowlist_matches_the_tracked_blank_templates_exactly():
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    for item in data["blank_templates"]:
        sha = subprocess.run(
            ["git", "rev-parse", f"HEAD:{item['path']}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert sha == item["blob_sha"], item["path"]
    assert len(load_allowlist(ALLOWLIST)) == len(data["blank_templates"])


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _run(cwd: Path, *args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(cwd), *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_cli_passes_a_clean_range_and_fails_a_dropped_in_docx(repo):
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "notes.md").write_text("plain\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "clean")
    assert _run(repo, "--git-range", f"{base}..HEAD").returncode == 0

    (repo / "MY-SYSTEM-SSP.docx").write_bytes(b"PK\x03\x04 not really a document")
    (repo / "examples" / "vault" / "s").mkdir(parents=True)
    (repo / "examples" / "vault" / "s" / "Index.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-qm", "system content")
    res = _run(repo, "--git-range", f"{base}..HEAD")
    assert res.returncode == 1
    assert "MY-SYSTEM-SSP.docx" in res.stdout and "office-document" in res.stdout
    assert (
        "examples/vault/s/Index.md" in res.stdout and "generated-output" in res.stdout
    )


def test_cli_catches_a_force_added_file_that_gitignore_would_hide(repo):
    (repo / ".gitignore").write_text("*.docx\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ignore")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "secret.docx").write_bytes(b"x")
    _git(repo, "add", "-f", "secret.docx")
    _git(repo, "commit", "-qm", "force")
    assert _run(repo, "--git-range", f"{base}..HEAD").returncode == 1


def test_cli_staged_mode_and_tracked_mode(repo):
    (repo / "a.docx").write_bytes(b"x")
    _git(repo, "add", "-f", "a.docx")
    assert _run(repo, "--staged").returncode == 1
    _git(repo, "commit", "-qm", "x")
    assert _run(repo, "--tracked").returncode == 1


def test_cli_a_deleted_docx_does_not_fail(repo):
    (repo / "a.docx").write_bytes(b"x")
    _git(repo, "add", "-f", "a.docx")
    _git(repo, "commit", "-qm", "add")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "rm", "-q", "a.docx")
    _git(repo, "commit", "-qm", "remove")
    assert _run(repo, "--git-range", f"{base}..HEAD").returncode == 0


def test_cli_fails_closed_on_an_unreadable_change_set(repo):
    res = _run(repo, "--git-range", "does-not-exist..HEAD")
    assert res.returncode == 2
    missing = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--tracked",
            "--allowlist",
            str(repo / "nope.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 2


def test_the_real_core_tree_is_clean():
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(ROOT), "--tracked"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr


def _load_cli():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_t_check_system_content", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *a):
        return json.dumps(self._payload).encode()


def test_pr_mode_pages_through_every_file_and_flags_one_on_a_later_page(monkeypatch):
    cli = _load_cli()
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    page1 = [
        {"filename": f"f{i}.md", "status": "added", "sha": "1" * 40} for i in range(100)
    ]
    page2 = [{"filename": "late/SSP.docx", "status": "added", "sha": "2" * 40}]
    seen = []

    def fake_open(req, timeout=0):
        seen.append(req.full_url)
        return _Resp(page1 if req.full_url.endswith("&page=1") else page2)

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_open)
    entries = cli.entries_from_pr(7)
    assert len(entries) == 101 and len(seen) == 2
    assert [v.rule for v in check_entries(entries, set())] == ["office-document"]


def test_pr_mode_refuses_to_pass_a_truncated_3000_file_list(monkeypatch):
    cli = _load_cli()
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    full = [{"filename": "a.md", "status": "added", "sha": "1" * 40}] * 100
    monkeypatch.setattr(
        cli.urllib.request, "urlopen", lambda req, timeout=0: _Resp(full)
    )
    with pytest.raises(RuntimeError, match="3000"):
        cli.entries_from_pr(7)


def test_pr_mode_without_credentials_fails_closed(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--github-pr", "1"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
