"""Tests for the consolidated Obsidian vault validator (scripts/validate_vault.py)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "compliance" / "scripts" / "validate_vault.py"

_spec = importlib.util.spec_from_file_location("validate_vault", _SCRIPT)
validate_vault = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_vault)


def _note(vault: Path, relpath: str, frontmatter: str = "", body: str = "") -> Path:
    p = vault / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    content = ""
    if frontmatter:
        content += f"---\n{frontmatter}\n---\n"
    content += body
    p.write_text(content, encoding="utf-8")
    return p


class TestDuplicateNoteNames:
    def test_flags_duplicate_stems_only_when_bare_linked(self, tmp_path):
        # duplicate stems are only a problem if referenced by a BARE [[foo]]
        _note(tmp_path, "Components/foo.md")
        _note(tmp_path, "Systems/foo.md")
        _note(tmp_path, "ref.md", body="see [[foo]]")
        issues = validate_vault.check_duplicate_note_names(tmp_path)
        assert len(issues) == 1
        assert "foo" in issues[0]

    def test_path_qualified_links_not_flagged(self, tmp_path):
        # Same duplicate stems, but linked path-qualified -> unambiguous, no issue
        _note(tmp_path, "Components/foo.md")
        _note(tmp_path, "Systems/foo.md")
        _note(tmp_path, "ref.md", body="see [[Systems/foo]] and [[Components/foo]]")
        assert validate_vault.check_duplicate_note_names(tmp_path) == []

    def test_unique_names_ok(self, tmp_path):
        _note(tmp_path, "Components/foo.md")
        _note(tmp_path, "Systems/bar.md")
        _note(tmp_path, "ref.md", body="[[foo]] [[bar]]")
        assert validate_vault.check_duplicate_note_names(tmp_path) == []


class TestEmbedsResolve:
    def test_missing_embed_flagged(self, tmp_path):
        _note(tmp_path, "a.md", body="see ![[does-not-exist]]")
        issues = validate_vault.check_embeds_resolve(tmp_path)
        assert len(issues) == 1
        assert "does-not-exist" in issues[0]

    def test_note_embed_resolves(self, tmp_path):
        _note(tmp_path, "a.md", body="see ![[target]]")
        _note(tmp_path, "target.md", body="hi")
        assert validate_vault.check_embeds_resolve(tmp_path) == []

    def test_attachment_embed_resolves(self, tmp_path):
        _note(tmp_path, "a.md", body="![[diagram.png]]")
        (tmp_path / "diagram.png").write_bytes(b"\x89PNG")
        assert validate_vault.check_embeds_resolve(tmp_path) == []

    def test_plain_wikilink_not_treated_as_embed(self, tmp_path):
        # [[x]] (no leading !) is a link, not an embed; not this check's job.
        _note(tmp_path, "a.md", body="see [[missing]]")
        assert validate_vault.check_embeds_resolve(tmp_path) == []


class TestTagSyntax:
    def test_valid_tags_pass(self, tmp_path):
        _note(
            tmp_path,
            "a.md",
            frontmatter="tags:\n- control/family/ac\n- system/status/operational",
        )
        assert validate_vault.check_tag_syntax(tmp_path) == []

    def test_tag_with_space_flagged(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="tags:\n- 'has space'")
        issues = validate_vault.check_tag_syntax(tmp_path)
        assert len(issues) == 1
        assert "has space" in issues[0]

    def test_inline_tag_list(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="tags: [good/tag, bad tag]")
        issues = validate_vault.check_tag_syntax(tmp_path)
        assert any("bad tag" in i for i in issues)

    def test_template_notes_skipped(self, tmp_path):
        _note(
            tmp_path,
            "_meta/templates/Control.md",
            frontmatter="tags:\n- control/family/{{title|slice:0:2}}",
        )
        # Jinja slot in a template note is not a real tag error.
        assert validate_vault.check_tag_syntax(tmp_path) == []


class TestEndToEndExitCodes:
    """The end-to-end path shells out to the focused sub-validators, one of
    which (frontmatter) computes paths relative to the repo root — so these use
    a vault created *inside* the repo tree (cleaned up after) rather than a
    pytest tmp_path outside it.
    """

    @pytest.fixture
    def repo_vault(self):
        import shutil
        import uuid

        vault = _REPO_ROOT / "examples" / "vault" / f"_test_{uuid.uuid4().hex[:8]}"
        vault.mkdir(parents=True)
        try:
            yield vault
        finally:
            shutil.rmtree(vault, ignore_errors=True)

    def test_default_run_returns_zero_on_warnings(self, repo_vault):
        # A duplicate name is a warning by default -> exit 0. Notes carry no
        # frontmatter (frontmatter check warns, not errors) so the duplicate
        # check is the only signal under test.
        _note(repo_vault, "Components/dup.md", body="# dup")
        _note(repo_vault, "Systems/dup.md", body="# dup")
        rc = validate_vault.main(["--vault", str(repo_vault)])
        assert rc == 0

    def test_strict_run_fails_on_structural_warning(self, repo_vault):
        _note(repo_vault, "Components/dup.md", body="# dup")
        _note(repo_vault, "Systems/dup.md", body="# dup")
        rc = validate_vault.main(["--vault", str(repo_vault), "--strict"])
        assert rc == 1

    def test_no_vault_is_noop(self, tmp_path):
        empty = tmp_path / "nope"
        rc = validate_vault.main(["--vault", str(empty)])
        # empty/nonexistent vault: no md files, checks pass trivially
        assert rc == 0


class TestOKFConformance:
    def test_missing_type_flagged(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="title: x", body="# a")
        issues = validate_vault.check_okf_conformance(tmp_path)
        assert len(issues) == 1 and "type" in issues[0]

    def test_non_empty_type_passes(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="type: oscal.control", body="# a")
        assert validate_vault.check_okf_conformance(tmp_path) == []

    def test_no_frontmatter_flagged(self, tmp_path):
        _note(tmp_path, "a.md", body="# a\nbody")
        issues = validate_vault.check_okf_conformance(tmp_path)
        assert len(issues) == 1 and "frontmatter" in issues[0]

    def test_reserved_files_exempt(self, tmp_path):
        _note(tmp_path, "index.md", body="# Index")
        _note(tmp_path, "log.md", body="# Log")
        assert validate_vault.check_okf_conformance(tmp_path) == []

    def test_gardener_ignore_exempts(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="gardener-ignore: true", body="# a")
        assert validate_vault.check_okf_conformance(tmp_path) == []


class TestOrphanNotes:
    def test_isolated_note_flagged(self, tmp_path):
        _note(tmp_path, "lonely.md", frontmatter="type: x", body="# lonely\nno links")
        issues = validate_vault.check_orphan_notes(tmp_path)
        assert any("lonely" in i for i in issues)

    def test_note_with_outbound_not_orphan(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="type: x", body="see [[b]]")
        _note(tmp_path, "b.md", frontmatter="type: x", body="hi")
        issues = validate_vault.check_orphan_notes(tmp_path)
        # a links out (not orphan); b has inbound (not orphan)
        assert issues == []

    def test_okf_infra_exempt(self, tmp_path):
        _note(tmp_path, "Index.md", frontmatter="type: okf.index", body="# Index")
        assert validate_vault.check_orphan_notes(tmp_path) == []


class TestDraftMarkers:
    def test_todo_flagged(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="type: x", body="# a\nTODO finish this")
        issues = validate_vault.check_draft_markers(tmp_path)
        assert any("TODO" in i for i in issues)

    def test_marker_only_in_frontmatter_ignored(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="type: x\nnote: TODO", body="# a\ndone")
        assert validate_vault.check_draft_markers(tmp_path) == []

    def test_substring_not_matched(self, tmp_path):
        # 'todos' should not match whole-word TODO
        _note(tmp_path, "a.md", frontmatter="type: x", body="# a\nmanage todos list")
        assert validate_vault.check_draft_markers(tmp_path) == []


class TestThinAndStale:
    def test_thin_note_flagged(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="type: x", body="# a\nshort")
        issues = validate_vault.check_thin_notes(tmp_path, min_chars=80)
        assert any("thin" in i for i in issues)

    def test_thin_disabled_at_zero(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="type: x", body="# a\nshort")
        assert validate_vault.check_thin_notes(tmp_path, min_chars=0) == []

    def test_substantial_note_not_thin(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="type: x", body="# a\n" + "x" * 200)
        assert validate_vault.check_thin_notes(tmp_path, min_chars=80) == []

    def test_stale_disabled_at_zero(self, tmp_path):
        _note(tmp_path, "a.md", frontmatter="type: x", body="# a")
        assert validate_vault.check_stale_notes(tmp_path, max_days=0) == []

    def test_stale_flags_old_file(self, tmp_path):
        import os
        import time

        p = _note(tmp_path, "a.md", frontmatter="type: x", body="# a\ncontent")
        old = time.time() - 100 * 86400
        os.utime(p, (old, old))
        issues = validate_vault.check_stale_notes(tmp_path, max_days=30)
        assert any("stale" in i for i in issues)


class TestTemplateGuidanceInvariant:
    def test_non_moderate_template_vault_not_checked(self, tmp_path):
        # A vault that is not the moderate template is never flagged, even
        # with no guidance callouts at all.
        controls = tmp_path / "some-other-system" / "Controls"
        controls.mkdir(parents=True)
        (controls / "AC-01.md").write_text("---\ntype: oscal.control\n---\n# AC-01\n")
        assert (
            validate_vault.check_template_guidance_invariant(
                tmp_path / "some-other-system"
            )
            == []
        )

    def test_moderate_template_missing_guidance_flagged(self, tmp_path):
        vault = tmp_path / "moderate-sspp-template-2024"
        controls = vault / "Controls"
        controls.mkdir(parents=True)
        # Only 1 control note, and it lacks the guidance callout.
        (controls / "AC-01.md").write_text("---\ntype: oscal.control\n---\n# AC-01\n")
        issues = validate_vault.check_template_guidance_invariant(vault)
        # Both the count invariant (expected 330) and the guidance invariant fire.
        assert any("330" in i for i in issues)
        assert any("Template guidance" in i for i in issues)

    def test_moderate_template_missing_controls_dir_flagged(self, tmp_path):
        vault = tmp_path / "moderate-sspp-template-2024"
        vault.mkdir(parents=True)
        issues = validate_vault.check_template_guidance_invariant(vault)
        assert any("Controls/" in i for i in issues)

    def test_template_with_all_guidance_callouts_passes(self, tmp_path):
        vault = tmp_path / "moderate-sspp-template-2024"
        controls = vault / "Controls"
        controls.mkdir(parents=True)
        for i in range(330):
            (controls / f"AC-{i:03d}.md").write_text(
                "---\ntype: oscal.control\n---\n> [!note]- Template guidance\n> text\n",
                encoding="utf-8",
            )
        assert validate_vault.check_template_guidance_invariant(vault) == []


class TestScriptInvocation:
    def test_runs_as_a_script_on_a_synthetic_vault(self, tmp_path):
        vault = tmp_path / "demo"
        (vault / "Controls").mkdir(parents=True)
        (vault / "Controls" / "AC-01.md").write_text(
            "---\ntype: oscal.control\ncontrol_id: AC-01\n---\n\n# AC-01\n\nBody text that is long enough.\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--vault", str(vault)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert "frontmatter" in proc.stdout
        assert proc.returncode in (0, 1), proc.stderr
