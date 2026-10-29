"""Tests for R6: Snapshot & Export Pipeline.

Covers:
  - snapshot capture with timestamp
  - SHA-256 manifest generation
  - OSCAL export transformations (SSP, component definition, implemented requirement)
  - Bundle packaging (manifest, tarball creation)
  - CLI script execution
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from compliance.lib.snapshot import (
    capture_snapshot,
    collect_canonical_sidecars,
    generate_sha256_manifest,
)
from compliance.lib.export_oscal import (
    canonical_to_oscal_component_definition,
    canonical_to_oscal_ssp,
    control_to_oscal_implemented_requirement,
    get_exportable_types,
    load_canonical_json,
)
from compliance.lib.bundle import (
    create_bundle_manifest,
    build_bundle,
    _compute_file_hash,
)


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------


class TestSnapshotCapture:
    """Tests for time-stamped snapshot capture from canonical JSON."""

    def test_capture_snapshot_creates_timestamped_file(self):
        """Snapshot creates a file with the correct timestamp in the name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            current_root = vault_root / ".snapshots" / "current" / "Controls"
            current_root.mkdir(parents=True)
            history_root = vault_root / ".snapshots" / "history"

            # Write a canonical JSON sidecar
            sidecar_path = current_root / "AC-1.json"
            sidecar_data = {
                "type": "oscal.control",
                "control_id": "AC-1",
                "status": "active",
                "_meta": {
                    "schema_version": 1,
                    "content_hash": "sha256:abc123",
                },
            }
            sidecar_path.write_text(json.dumps(sidecar_data), encoding="utf-8")

            # Capture snapshot
            snap_path = capture_snapshot(
                sidecar_path,
                history_root,
                vault_root,
                timestamp_override="2026-07-18T12-00-00Z",
            )

            assert snap_path.exists()
            assert "2026-07-18T12-00-00Z" in snap_path.name
            assert snap_path.name.startswith("AC-1.")
            assert snap_path.name.endswith(".json")

            # Verify content includes snapshot metadata
            with open(snap_path, encoding="utf-8") as f:
                data = json.load(f)
            assert "_snapshot" in data
            assert data["_snapshot"]["snapshot_version"] == 1
            assert data["control_id"] == "AC-1"

    def test_capture_snapshot_missing_file(self):
        """Raises FileNotFoundError for non-existent canonical JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            history_root = vault_root / ".snapshots" / "history"
            missing_path = vault_root / ".snapshots" / "current" / "missing.json"

            with pytest.raises(FileNotFoundError):
                capture_snapshot(missing_path, history_root, vault_root)

    def test_collect_canonical_sidecars(self):
        """Collects all JSON files from .snapshots/current/."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            current_root = vault_root / ".snapshots" / "current" / "Controls"
            current_root.mkdir(parents=True)

            (current_root / "AC-1.json").write_text("{}", encoding="utf-8")
            (current_root / "AC-2.json").write_text("{}", encoding="utf-8")
            (current_root / "AC-3.json").write_text("{}", encoding="utf-8")

            sidecars = collect_canonical_sidecars(vault_root)
            assert len(sidecars) == 3
            names = {s.name for s in sidecars}
            assert names == {"AC-1.json", "AC-2.json", "AC-3.json"}

    def test_collect_no_sidecars_empty_vault(self):
        """Returns empty list when no sidecars exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            sidecars = collect_canonical_sidecars(vault_root)
            assert sidecars == []


class TestManifestGeneration:
    """Tests for SHA-256 manifest generation."""

    def test_generate_manifest(self):
        """Generates a manifest with file hashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            f1 = tmp / "file1.json"
            f2 = tmp / "file2.json"
            f1.write_text('{"key": "value1"}', encoding="utf-8")
            f2.write_text('{"key": "value2"}', encoding="utf-8")

            manifest_path = tmp / "manifest.json"
            result = generate_sha256_manifest([f1, f2], manifest_path)

            assert result.exists()
            with open(result, encoding="utf-8") as f:
                data = json.load(f)

            assert data["total_files"] == 2
            assert "file1.json" in data["files"]
            assert "file2.json" in data["files"]
            assert data["files"]["file1.json"].startswith("sha256:")
            assert data["files"]["file2.json"].startswith("sha256:")
            assert len(data["files"]["file1.json"]) == 71  # "sha256:" + 64 hex chars


# ---------------------------------------------------------------------------
# OSCAL Export tests
# ---------------------------------------------------------------------------


class TestOscalExport:
    """Tests for transforming canonical JSON into OSCAL-standard documents."""

    def test_control_to_implemented_requirement(self):
        """Converts a control canonical JSON to OSCAL implemented-requirement."""
        canonical_data = {
            "type": "oscal.control",
            "control_id": "AC-2",
            "implementation_status": "implemented",
            "origination": ["shared"],
            "uuid": "0f8fad5b-d9cb-469f-a165-70867728950e",
            "body_sections": [
                {"heading": "Part a", "content": "Account creation process"},
                {"heading": "Part b", "content": "Account review quarterly"},
                {"heading": "CRM Guidance", "content": "Standard pattern"},
            ],
            "inline_fields": {},
        }

        result = control_to_oscal_implemented_requirement(canonical_data)

        # OSCAL control-ids are lowercase tokens; enhancements use dots.
        assert result["control-id"] == "ac-2"
        # A valid RFC-4122 uuid is passed through unchanged.
        assert result["uuid"] == "0f8fad5b-d9cb-469f-a165-70867728950e"
        # implementation-status + control-origination are carried as props on
        # the implemented-requirement (not first-class OSCAL fields).
        prop_names = {p["name"]: p["value"] for p in result.get("props", [])}
        assert prop_names.get("implementation-status") == "implemented"
        assert prop_names.get("control-origination") == "shared"
        assert len(result["statements"]) == 2
        assert result["statements"][0]["statement-id"] == "ac-2_smt.a"
        assert result["statements"][1]["statement-id"] == "ac-2_smt.b"

        # by-components is generated and references a component uuid
        assert len(result["by-components"]) == 1
        assert result["by-components"][0]["component-uuid"]
        assert (
            result["by-components"][0]["implementation-status"]["state"]
            == "implemented"
        )

    def test_control_id_enhancement_normalized(self):
        """Parenthesized enhancements become dotted OSCAL tokens (AT-2(2) -> at-2.2)."""
        result = control_to_oscal_implemented_requirement(
            {"control_id": "AT-2(2)", "uuid": "u"}
        )
        assert result["control-id"] == "at-2.2"

    def test_canonical_to_oscal_ssp(self):
        """Converts SSP plan data to a full, schema-valid system-security-plan."""
        canonical_data = {
            "type": "oscal.ssp",
            "title": "Test SSP",
            "uuid": "ssp-uuid-1",
            "reviewed": "2026-07-18",
            "version": "2.0",
            "oscasl_version": "1.0.4",
            "profile_ref": "profiles/moderate-baseline.md",
            "system_name": "Test System",
            "fips_199": "moderate",
            "status": "operational",
            "body_sections": [{"heading": None, "content": "System description here."}],
        }

        # No control sidecars are supplied, so the documented placeholder is
        # opted into explicitly; production exports fail closed (#51).
        result = canonical_to_oscal_ssp(
            canonical_data, plan_id="test-plan", allow_synthetic=True
        )

        ssp = result["system-security-plan"]
        assert ssp["metadata"]["title"] == "Test SSP"
        assert ssp["metadata"]["version"] == "2.0"
        # Version is now the R17 default (1.1.3), not the vault's stale value.
        assert ssp["metadata"]["oscal-version"] == "1.1.3"
        assert ssp["import-profile"]["href"] == "profiles/moderate-baseline.md"
        assert ssp["system-characteristics"]["system-name"] == "Test System"
        assert ssp["system-characteristics"]["security-sensitivity-level"] == "moderate"
        # Full graph is now present.
        assert "system-implementation" in ssp
        assert "control-implementation" in ssp
        assert ssp["system-implementation"]["components"][0]["type"] == "this-system"

    def test_canonical_to_oscal_component_definition(self):
        """Converts component data to OSCAL component-definition."""
        canonical_data = {
            "type": "oscal.component_definition",
            "name": "IAM Service",
            "title": "IAM Service",
            "component_type": "service",
            "status": "operational",
            "version": "1.0",
            "body_sections": [
                {"heading": None, "content": "Identity and access management service."}
            ],
        }

        result = canonical_to_oscal_component_definition(canonical_data)

        cd = result["component-definition"]
        assert cd["metadata"]["title"] == "IAM Service Component Definition"
        assert len(cd["components"]) == 1
        assert cd["components"][0]["title"] == "IAM Service"
        assert cd["components"][0]["type"] == "service"
        # OSCAL defined-component has no status field; it is preserved as a prop.
        prop_names = {
            p["name"]: p["value"] for p in cd["components"][0].get("props", [])
        }
        assert prop_names.get("status") == "operational"

    def test_get_exportable_types(self):
        """Returns the list of types that can be exported as standalone OSCAL docs."""
        types = get_exportable_types()
        assert "oscal.ssp" in types
        assert "oscal.component_definition" in types
        assert "oscal.poam" in types
        assert "oscal.assessment_plan" in types
        assert "oscal.assessment_results" in types
        assert "oscal.profile" in types
        assert "oscal.catalog" in types
        # Controls are not standalone exports
        assert "oscal.control" not in types

    def test_load_canonical_json(self):
        """Loads a canonical JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            path = tmp / "test.json"
            data = {"type": "oscal.control", "control_id": "AC-1"}
            path.write_text(json.dumps(data), encoding="utf-8")

            loaded = load_canonical_json(path)
            assert loaded["type"] == "oscal.control"
            assert loaded["control_id"] == "AC-1"


# ---------------------------------------------------------------------------
# Bundle tests
# ---------------------------------------------------------------------------


class TestBundleManifest:
    """Tests for bundle manifest creation."""

    def test_create_manifest(self):
        """Creates a manifest with file listing and hashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            controls_dir = vault_root / "Controls"
            controls_dir.mkdir(parents=True)
            (controls_dir / "AC-1.md").write_text(
                "---\ntype: oscal.control\n---\n# Body\n", encoding="utf-8"
            )
            (controls_dir / "AC-2.md").write_text(
                "---\ntype: oscal.control\n---\n# Body\n", encoding="utf-8"
            )

            (vault_root / "Index.md").write_text("# Index\n", encoding="utf-8")

            manifest = create_bundle_manifest(
                vault_root,
                bundle_name="test-vault",
                version="1.0.0",
                description="Test bundle",
            )

            assert manifest["manifest_version"] == 1
            assert manifest["bundle"]["name"] == "test-vault"
            assert manifest["bundle"]["version"] == "1.0.0"
            assert manifest["bundle"]["description"] == "Test bundle"
            assert manifest["file_count"] == 2  # 2 control notes (Index.md excluded)
            assert manifest["categories"]["notes"] == 2
            assert "total_size_bytes" in manifest

            # Each file should have path, size_bytes, hash
            for file_entry in manifest["files"]:
                assert "path" in file_entry
                assert "size_bytes" in file_entry
                assert "hash" in file_entry
                assert file_entry["hash"].startswith("sha256:")

    def test_compute_file_hash(self):
        """Computes SHA-256 hash of a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            f = tmp / "test.txt"
            f.write_text("hello world", encoding="utf-8")

            result = _compute_file_hash(f, "sha256")
            assert result.startswith("sha256:")
            assert len(result) == 71  # "sha256:" + 64 hex chars

    def test_build_bundle_tarball(self):
        """Builds a tarball bundle with manifest and notes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            controls_dir = vault_root / "Controls"
            controls_dir.mkdir(parents=True)
            (controls_dir / "AC-1.md").write_text(
                "---\ntype: oscal.control\n---\n# AC-1\n", encoding="utf-8"
            )

            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()

            tarball_path = build_bundle(
                vault_root=vault_root,
                output_path=output_dir,
                bundle_name="test-vault",
                version="1.0.0",
                description="Test",
                compression="gz",
            )

            assert tarball_path.exists()
            assert tarball_path.name.endswith(".tar.gz")
            assert tarball_path.stat().st_size > 0

    def test_build_bundle_no_compression(self):
        """Builds an uncompressed tarball."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            (vault_root / "Index.md").write_text("# Index\n", encoding="utf-8")

            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()

            tarball_path = build_bundle(
                vault_root=vault_root,
                output_path=output_dir,
                bundle_name="test-vault",
                compression="none",
            )

            assert tarball_path.exists()
            assert tarball_path.suffix == ".tar"


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestSnapshotCLI:
    """Integration tests for the snapshot CLI script."""

    def test_snapshot_cli_single_vault(self):
        """CLI captures snapshots for a vault."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            current_root = vault_root / ".snapshots" / "current" / "Controls"
            current_root.mkdir(parents=True)
            (current_root / "AC-1.json").write_text(
                json.dumps(
                    {
                        "type": "oscal.control",
                        "control_id": "AC-1",
                        "_meta": {"schema_version": 1},
                    }
                ),
                encoding="utf-8",
            )

            from compliance.lib.snapshot import (
                capture_snapshot,
                collect_canonical_sidecars,
            )

            sidecars = collect_canonical_sidecars(vault_root)
            history_root = vault_root / ".snapshots" / "history"
            for sc in sidecars:
                capture_snapshot(
                    sc,
                    history_root,
                    vault_root,
                    timestamp_override="2026-07-18T00-00-00Z",
                )

            # Verify snapshot was created
            history_dir = vault_root / ".snapshots" / "history" / "Controls"
            snapshots = list(history_dir.glob("*.json"))
            assert len(snapshots) == 1
            assert "2026-07-18T00-00-00Z" in snapshots[0].name


class TestExportOscalCLI:
    """Integration tests for the OSCAL export CLI."""

    def test_export_oscal_ssp(self):
        """Exports an SSP with controls from canonical JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            current_root = vault_root / ".snapshots" / "current"
            plans_dir = current_root / "Plans"
            controls_dir = current_root / "Controls"
            plans_dir.mkdir(parents=True)
            controls_dir.mkdir(parents=True)

            # Plan sidecar
            (plans_dir / "test-plan.json").write_text(
                json.dumps(
                    {
                        "type": "oscal.ssp",
                        "oscal_model": "system-security-plan",
                        "title": "Test SSP",
                        "plan_id": "test-plan",
                        "uuid": "ssp-uuid",
                        "reviewed": "2026-07-18",
                        "version": "1.0",
                        "oscasl_version": "1.0.4",
                        "profile_ref": "profiles/test.md",
                        "system_name": "Test",
                        "fips_199": "low",
                        "status": "operational",
                        "body_sections": [],
                    }
                ),
                encoding="utf-8",
            )

            # Control sidecar
            (controls_dir / "AC-1.json").write_text(
                json.dumps(
                    {
                        "type": "oscal.control",
                        "control_id": "AC-1",
                        "uuid": "ac1-uuid",
                        "implementation_status": "implemented",
                        "origination": ["inherited"],
                        "body_sections": [
                            {"heading": "Part a", "content": "Test part a"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            # Run export
            import subprocess
            import sys

            result = subprocess.run(
                [
                    sys.executable,
                    str(
                        Path(__file__).resolve().parents[2]
                        / "compliance"
                        / "scripts"
                        / "export_oscal.py"
                    ),
                    "--vault",
                    str(vault_root),
                ],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parents[2]),
            )

            assert result.returncode == 0, (
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

            # Verify export output
            exports_dir = vault_root / ".exports" / "oscal-json"
            ssp_files = list(exports_dir.glob("*ssp*.json"))
            assert len(ssp_files) >= 1

            with open(ssp_files[0], encoding="utf-8") as f:
                ssp = json.load(f)
            assert "system-security-plan" in ssp
            assert ssp["system-security-plan"]["metadata"]["title"] == "Test SSP"
            assert "control-implementation" in ssp["system-security-plan"]
            impl_reqs = ssp["system-security-plan"]["control-implementation"][
                "implemented-requirements"
            ]
            assert len(impl_reqs) == 1
            assert impl_reqs[0]["control-id"] == "ac-1"


class TestBundleCLI:
    """Integration tests for the bundle CLI."""

    def test_bundle_cli(self):
        """CLI builds a bundle tarball."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            (vault_root / "Index.md").write_text("# Index\n", encoding="utf-8")
            controls_dir = vault_root / "Controls"
            controls_dir.mkdir(parents=True)
            (controls_dir / "AC-1.md").write_text(
                "---\ntype: oscal.control\n---\n# Body\n", encoding="utf-8"
            )

            import subprocess
            import sys

            output_dir = Path(tmpdir) / "bundles"
            result = subprocess.run(
                [
                    sys.executable,
                    str(
                        Path(__file__).resolve().parents[2]
                        / "compliance"
                        / "scripts"
                        / "bundle.py"
                    ),
                    "--vault",
                    str(vault_root),
                    "--output-dir",
                    str(output_dir),
                    "--compression",
                    "none",
                    "--version",
                    "1.0.0",
                ],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parents[2]),
            )

            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert "Bundle created:" in result.stdout

            # Verify tarball exists
            bundles = list(output_dir.glob("*.tar"))
            assert len(bundles) == 1
