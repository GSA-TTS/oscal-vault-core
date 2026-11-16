"""Generic review registry belongs to core, with no consumer corpus embedded."""

import json

import pytest

from compliance.scripts.init_docx_integrity_registry import initialize, main
from compliance.scripts.init_workspace import render_block


def test_explicit_registry_is_private_blocked_and_exclusive(tmp_path):
    output = tmp_path / "registry"
    manifest = initialize(
        output, ["synthetic-a", "synthetic-b"], tmp_path / "workspace"
    )
    assert len(manifest["records"]) == 2
    assert all(
        r["run_status"] == "blocked"
        and r["source_docx"] is None
        and r["authorized_by"] is None
        for r in manifest["records"]
    )
    assert output.stat().st_mode & 0o777 == 0o700
    original = (output / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        initialize(output, ["synthetic-a"], tmp_path / "workspace")
    assert (output / "manifest.json").read_bytes() == original
    assert json.loads((output / "report-index.json").read_text())["reports"] == []


@pytest.mark.parametrize(
    "ids", [[], ["duplicate", "duplicate"], ["../escape"], ["invalid space"]]
)
def test_invalid_ids_create_no_artifacts(tmp_path, ids):
    with pytest.raises(ValueError):
        initialize(tmp_path / "output", ids, tmp_path / "workspace")
    assert not (tmp_path / "output").exists()


def test_discovery_reads_only_immediate_vault_identifiers(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "examples/vault/synthetic-system").mkdir(parents=True)
    (workspace / "examples/vault/.hidden").mkdir()
    output = tmp_path / "registry"
    assert (
        main(
            [
                "--workspace",
                str(workspace),
                "--output",
                str(output),
                "--discover-vault-ids",
            ]
        )
        == 0
    )
    records = json.loads((output / "manifest.json").read_text())["records"]
    assert [r["system_id"] for r in records] == ["synthetic-system"]
    assert records[0]["source_sha256"] is None


def test_workspace_defaults_ignore_integrity_run_artifacts():
    assert "docx-integrity-run/" in render_block(False).splitlines()
