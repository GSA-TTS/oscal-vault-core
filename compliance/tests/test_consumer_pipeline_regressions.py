"""Core regressions for consumer performance and fail-closed canonical checks."""

import json
import sys

import pytest

from compliance.lib import oscal_mapping_registry as registry
from compliance.scripts import generate_canonical_json as canonical


def test_mapping_cache_parses_once_and_invalidates_on_content_change(
    tmp_path, monkeypatch
):
    contract = tmp_path / "contract.yaml"
    contract.write_text("schema: []\n")
    registry._parse_mappings.cache_clear()
    original = registry.yaml.safe_load
    calls = []

    def parse(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(registry.yaml, "safe_load", parse)
    assert registry.load_mappings(contract) == []
    assert registry.load_mappings(contract) == []
    assert len(calls) == 1
    contract.write_text("schema: []\n# changed content\n")
    registry.load_mappings(contract)
    assert len(calls) == 2
    registry._parse_mappings.cache_clear()


def test_cached_mapping_list_is_not_shared_mutable_state(tmp_path):
    contract = tmp_path / "contract.yaml"
    contract.write_text("schema: []\n")
    first = registry.load_mappings(contract)
    first.append("caller mutation")
    assert registry.load_mappings(contract) == []


def test_hidden_workspace_parent_does_not_hide_vault_notes(tmp_path):
    from compliance.scripts.validate_frontmatter import _find_md_files
    from compliance.scripts.validate_wikilinks import _build_note_index

    vault = tmp_path / ".hidden-parent" / "vault"
    vault.mkdir(parents=True)
    note = vault / "Visible.md"
    note.write_text("---\ntype: oscal.system\n---\n[[Other]]")
    hidden = vault / ".hidden-child"
    hidden.mkdir()
    (hidden / "Ignored.md").write_text("ignored")
    assert _find_md_files([vault]) == [note]
    assert "Visible" in _build_note_index(vault)
    assert "Ignored" not in _build_note_index(vault)


@pytest.mark.parametrize("case", ["missing_hash", "generation_error"])
def test_canonical_check_fails_closed(tmp_path, monkeypatch, case):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "System.md"
    note.write_text("---\ntype: oscal.system\n---\n# Synthetic\n")
    sidecar = vault / ".snapshots/current/System.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({"_meta": {}}))
    monkeypatch.setattr(
        sys, "argv", ["generate_canonical_json.py", "--vault", str(vault), "--check"]
    )
    if case == "generation_error":

        def fail(*args):
            raise ValueError("synthetic failure")

        monkeypatch.setattr(canonical, "generate_canonical_data", fail)
    assert canonical.main() != 0
