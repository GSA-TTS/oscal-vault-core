"""Consistency tests for the descriptive interchange model and OSCAL coverage.

These do not re-test the pipeline mechanics; they assert that the two committed
*descriptive* artifacts stay honest:

* ``compliance/datacontracts/interchange-model.yaml`` names only note types and
  frontmatter keys the vault builder actually emits, and
* ``compliance/datacontracts/oscal-schema-coverage.yaml`` matches freshly
  computed coverage (so the USED/UNUSED inventory cannot silently drift).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERCHANGE = REPO_ROOT / "compliance" / "datacontracts" / "interchange-model.yaml"
COVERAGE = REPO_ROOT / "compliance" / "datacontracts" / "oscal-schema-coverage.yaml"
EXAMPLE_SSP = (
    REPO_ROOT
    / "compliance"
    / "tests"
    / "fixtures"
    / "oscal"
    / "ssp-source.example.json"
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_interchange_model_parses_and_has_all_five_stages():
    model = _load(INTERCHANGE)
    stages = model["stages"]
    assert set(stages) == {
        "s1_docx_source",
        "s2_vault_markdown",
        "s3_canonical_json",
        "s4_oscal_json",
        "s5_docx_writer",
    }


def test_interchange_note_types_match_the_vault_builder():
    """Every note type the model documents must be a type the vault emits."""
    if not EXAMPLE_SSP.exists():
        pytest.skip("example SSP source missing")
    from compliance.lib.vault import build_vault

    ssp = json.loads(EXAMPLE_SSP.read_text())
    notes = build_vault(ssp, plan_id="sample", parser_name="fismatic")
    emitted_types = set()
    for note in notes:
        if note.raw:
            continue
        fm = note.frontmatter or {}
        t = fm.get("type")
        if t:
            emitted_types.add(t)

    model = _load(INTERCHANGE)
    documented = set(model["stages"]["s2_vault_markdown"]["note_types"])
    # Collapse the two oscal.ssp flavors (oscal.ssp.system / .plan) and the
    # oscal.x.* wildcard to their base type.
    normalized_documented = set()
    for key in documented:
        if key.startswith("oscal.ssp"):
            normalized_documented.add("oscal.ssp")
        else:
            normalized_documented.add(key)

    for emitted in emitted_types:
        if emitted.startswith("oscal.x."):
            assert "oscal.x.*" in documented
        elif emitted.startswith("okf."):
            # OKF infrastructure notes are out of the interchange model's scope.
            continue
        else:
            assert emitted in normalized_documented, (
                f"emitted note type {emitted!r} is not in the interchange model"
            )


def test_interchange_control_frontmatter_keys_are_really_emitted():
    """Required control frontmatter keys claimed by the model must appear."""
    if not EXAMPLE_SSP.exists():
        pytest.skip("example SSP source missing")
    from compliance.lib.vault import build_vault

    ssp = json.loads(EXAMPLE_SSP.read_text())
    notes = build_vault(ssp, plan_id="sample", parser_name="fismatic")
    control = next(n for n in notes if n.relpath.startswith("Controls/"))
    fm_keys = set((control.frontmatter or {}).keys())

    model = _load(INTERCHANGE)
    required = set(
        model["stages"]["s2_vault_markdown"]["note_types"]["oscal.control"][
            "frontmatter_required"
        ]
    )
    missing = required - fm_keys
    assert not missing, f"model claims control frontmatter keys not emitted: {missing}"


def _synthetic_vaults(tmp_path: Path) -> Path:
    """One generated vault with canonical snapshots, built from the synthetic example SSP."""
    from compliance.lib.vault import build_vault
    from compliance.scripts.export_obsidian_vault import write_vault
    from compliance.scripts.generate_canonical_json import main as snapshot_main
    import json
    import sys

    root = tmp_path / "vaults"
    vault = root / "sample"
    ssp = json.loads(EXAMPLE_SSP.read_text(encoding="utf-8"))
    write_vault(build_vault(ssp, plan_id="sample", parser_name="fismatic"), vault)
    argv, sys.argv = sys.argv, ["generate_canonical_json.py", "--vault", str(vault)]
    try:
        assert snapshot_main() == 0
    finally:
        sys.argv = argv
    return root


def test_oscal_coverage_reflects_what_the_exporter_emits(tmp_path):
    """Coverage is computed by exporting a sample vault and diffing against the NIST schema.

    The committed report in ``datacontracts/`` is a snapshot of that computation; it needs a
    populated sample vault to regenerate, which the core does not ship. So this checks the
    computation itself on a synthetic vault: it exports, finds used fields, classifies the rest.
    """
    from compliance.scripts.oscal_schema_coverage import compute_coverage

    report = compute_coverage("system-security-plan", _synthetic_vaults(tmp_path))
    assert report["sample_exported"] is True
    assert report["used_count"] > 0
    assert report["used_count"] + report["unused_count"] == report["total_fields"]
    assert "control-implementation.implemented-requirements" in report["used"]


def test_coverage_without_a_sample_vault_reports_no_sample_instead_of_guessing(
    tmp_path,
):
    from compliance.scripts.oscal_schema_coverage import compute_coverage

    report = compute_coverage("system-security-plan", tmp_path / "none")
    assert report["sample_exported"] is False and report["used_count"] == 0


def test_oscal_coverage_reports_both_models_with_real_samples():
    coverage = _load(COVERAGE)
    models = coverage["models"]
    assert "system-security-plan" in models
    assert "component-definition" in models
    for model in models.values():
        assert model["sample_exported"] is True
        assert model["total_fields"] > 0
        assert model["used_count"] > 0
        # Every unused field must carry a disposition, so the report explains
        # WHY it is unused (extension_backed / prop_backed / genuinely_absent /
        # unclassified) rather than leaving a bare list.
        assert set(model["unused_dispositions"]) == set(model["unused"])
        assert model["unused_disposition_counts"]

    ssp = models["system-security-plan"]
    # The extension-backed native assemblies have now been wired, so they are
    # USED, not unused (this is the point of the extension bridge).
    assert "system-implementation.leveraged-authorizations" in ssp["used"]
    assert "back-matter" in ssp["used"]
    assert "system-characteristics.network-architecture" in ssp["used"]
    # Genuinely-absent fields remain unused and are labeled as such.
    genuinely_absent = {
        path
        for path, disp in ssp["unused_dispositions"].items()
        if disp == "genuinely_absent"
    }
    assert "metadata.revisions" in genuinely_absent
