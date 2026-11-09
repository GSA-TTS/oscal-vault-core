"""Tests for generated-DOCX fill-manifest validation."""

import json
import zipfile

from compliance.scripts.validate_docx_fill_output import main, validate


def _write(tmp_path, manifest):
    docx = tmp_path / "ssp.docx"
    docx.write_bytes(b"docx")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return docx, path


def test_accepts_complete_manifest(tmp_path):
    docx, manifest = _write(
        tmp_path,
        {
            "source_controls": 2,
            "matching_control_ids_filled": 2,
            "source_controls_without_target_table": [],
            "duplicate_target_control_ids": [],
        },
    )

    assert validate(docx, manifest) == []
    assert main(["--docx", str(docx), "--manifest", str(manifest)]) == 0


def test_rejects_dropped_controls_and_duplicates(tmp_path):
    docx, manifest = _write(
        tmp_path,
        {
            "source_controls": 2,
            "matching_control_ids_filled": 1,
            "source_controls_without_target_table": ["AC-3"],
            "duplicate_target_control_ids": ["AC-2"],
        },
    )

    errors = validate(docx, manifest, fail_duplicate_targets=True)

    assert "source controls were omitted from the target template" in errors
    assert "target template contains duplicate control IDs" in errors
    assert "not every source control was filled" in errors


def test_missing_manifest_fails_closed(tmp_path):
    docx = tmp_path / "ssp.docx"
    docx.write_bytes(b"docx")

    errors = validate(docx, tmp_path / "absent.json")

    assert any("missing manifest" in error for error in errors)


def test_fail_placeholders_flags_unresolved_tokens(tmp_path):
    docx, manifest = _write(
        tmp_path,
        {"source_controls": 0, "matching_control_ids_filled": 0},
    )
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml", "<w:t>[ACRONYM]</w:t>")

    errors = validate(docx, manifest, fail_placeholders=True)

    assert any("unresolved placeholders" in error for error in errors)
