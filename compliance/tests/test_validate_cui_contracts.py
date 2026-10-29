"""Tests for the CUI classification + provenance sidecar validator (ADR-024).

See scripts/validate_cui_contracts.py and
docs/ADR/ADR-024-cui-provenance-datacontract.md.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "compliance" / "scripts" / "validate_cui_contracts.py"

_spec = importlib.util.spec_from_file_location("validate_cui_contracts", _SCRIPT)
validate_cui_contracts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_cui_contracts)


def _valid_record(**overrides) -> dict:
    record = {
        "schema_version": 1,
        "system_slug": "example-system",
        "classification": {
            "cui_category": "not_cui",
            "cui_marking": "not_cui",
            "authority": "example only",
            "review_cadence": "annual",
        },
        "ingestion_approval": {
            "approved_by_role": "example-owner",
            "approval_date": "2026-09-28",
            "approval_ref": "example only — no real approval record",
        },
        "source_documents": [
            {
                "source_doc_ref": "example.docx",
                "sha256": "0" * 64,
                "source_type": "manual-source",
            }
        ],
        "downstream_artifacts": ["build/systems/example/example.cdx.json"],
        "retention": {
            "retention_period": "example",
            "decontrol_condition": "example",
        },
    }
    record.update(overrides)
    return record


def _write_sidecar(vault: Path, data: dict) -> Path:
    p = vault / "classification" / "cui.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


class TestValidateSidecar:
    def test_valid_record_passes(self, tmp_path):
        schema = validate_cui_contracts._load_schema()
        p = _write_sidecar(tmp_path, _valid_record())
        assert validate_cui_contracts.validate_sidecar(p, schema) == []

    def test_bad_review_cadence_flagged(self, tmp_path):
        schema = validate_cui_contracts._load_schema()
        record = _valid_record()
        record["classification"]["review_cadence"] = "not-a-cadence"
        p = _write_sidecar(tmp_path, record)
        issues = validate_cui_contracts.validate_sidecar(p, schema)
        assert any("review_cadence" in i for i in issues)

    def test_bad_system_slug_flagged(self, tmp_path):
        schema = validate_cui_contracts._load_schema()
        record = _valid_record(system_slug="Not_Valid_Slug")
        p = _write_sidecar(tmp_path, record)
        issues = validate_cui_contracts.validate_sidecar(p, schema)
        assert any("system_slug" in i for i in issues)

    def test_missing_required_field_flagged(self, tmp_path):
        schema = validate_cui_contracts._load_schema()
        record = _valid_record()
        del record["retention"]
        p = _write_sidecar(tmp_path, record)
        issues = validate_cui_contracts.validate_sidecar(p, schema)
        assert any("retention" in i for i in issues)

    def test_bad_sha256_flagged(self, tmp_path):
        schema = validate_cui_contracts._load_schema()
        record = _valid_record()
        record["source_documents"][0]["sha256"] = "not-a-hash"
        p = _write_sidecar(tmp_path, record)
        issues = validate_cui_contracts.validate_sidecar(p, schema)
        assert any("sha256" in i for i in issues)

    def test_not_cui_with_real_authority_is_valid(self, tmp_path):
        """A reviewed 'not CUI' determination is a valid, complete record —
        not merely an absence of classification (ADR-024)."""
        schema = validate_cui_contracts._load_schema()
        record = _valid_record()
        record["classification"]["authority"] = "44 USC 3554 (reviewed, not applicable)"
        p = _write_sidecar(tmp_path, record)
        assert validate_cui_contracts.validate_sidecar(p, schema) == []


class TestSidecarIsOptional:
    def test_system_without_sidecar_is_not_an_error(self, tmp_path):
        vault = tmp_path / "some-system"
        (vault / "Controls").mkdir(parents=True)
        (vault / "Controls" / "AC-01.md").write_text("---\ntype: oscal.control\n---\n")
        rc = validate_cui_contracts.main(["--vault", str(vault)])
        assert rc == 0


class TestCoverage:
    def test_coverage_reports_candidate_and_sidecar_status(self, tmp_path):
        vault_with = tmp_path / "with-sidecar"
        (vault_with / "Controls").mkdir(parents=True)
        (vault_with / "Controls" / "AC-01.md").write_text(
            "---\ntype: oscal.control\n---\n"
        )
        _write_sidecar(vault_with, _valid_record(system_slug="with-sidecar"))

        vault_without = tmp_path / "without-sidecar"
        (vault_without / "Controls").mkdir(parents=True)
        (vault_without / "Controls" / "AC-01.md").write_text(
            "---\ntype: oscal.control\n---\n"
        )

        rows, with_sidecar, candidates = validate_cui_contracts.build_coverage(
            [vault_with, vault_without]
        )
        assert candidates == 2
        assert with_sidecar == 1
        by_name = {r["system"]: r for r in rows}
        assert by_name["with-sidecar"]["has_sidecar"] is True
        assert by_name["without-sidecar"]["has_sidecar"] is False


class TestScriptInvocation:
    def test_runs_as_a_script_with_no_sidecars(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--vault", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert proc.returncode == 0

    def test_no_sidecars_present_is_ok(self, tmp_path):
        rc = validate_cui_contracts.main(["--vault", str(tmp_path)])
        assert rc == 0
