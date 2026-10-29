from pathlib import Path

from compliance.scripts.verify_obsidian_base_smoke import validate_manifest


def test_pending_template_is_valid_but_not_release_evidence(tmp_path: Path):
    manifest = tmp_path / "smoke.yaml"
    manifest.write_text(
        "schema_version: 1\nstatus: pending\nobsidian_version: 1.14.4\n",
        encoding="utf-8",
    )

    assert validate_manifest(manifest) == []
    assert validate_manifest(manifest, require_complete=True)


def test_complete_manifest_requires_counts_rewrite_check_and_evidence(tmp_path: Path):
    manifest = tmp_path / "smoke.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "status: passed\n"
        "obsidian_version: 1.14.4\n"
        "vault_commit: abc123\n"
        "vault: examples/vault/example\n"
        "tested_at: '2026-10-13T18:00:00Z'\n"
        "tester: reviewer\n"
        "views:\n"
        "  - {file: controls.base, view: All controls, rows: 3}\n"
        "no_rewrite_diff: true\n"
        "evidence: [artifact://base-smoke.png]\n",
        encoding="utf-8",
    )

    assert validate_manifest(manifest, require_complete=True) == []
