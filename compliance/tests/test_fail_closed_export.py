"""#51: canonical/OSCAL export must fail closed rather than silently omit.

Each test here encodes one way a green export could previously hide missing
data: an unreadable sidecar, an untyped sidecar, a control that cannot convert,
a plan whose control count no longer matches the vault, a fabricated placeholder
requirement, or a stale export left behind by a failed run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from compliance.lib.export_oscal import (
    ControlConversionError,
    ControlParityError,
    MissingControlsError,
    canonical_to_oscal_ssp,
)
from compliance.scripts.export_oscal import main


def _write_plan(vault: Path, *, controls_parsed: int | None = None) -> Path:
    snapshot = vault / ".snapshots/current"
    snapshot.mkdir(parents=True, exist_ok=True)
    plan: dict = {
        "type": "oscal.ssp",
        "note_type": "plan",
        "plan_id": "test-plan",
        "title": "Test plan",
    }
    if controls_parsed is not None:
        plan["controls_parsed"] = controls_parsed
    path = snapshot / "plan.json"
    path.write_text(json.dumps(plan))
    return path


def _write_control(vault: Path, control_id: str | None, name: str = "control") -> Path:
    snapshot = vault / ".snapshots/current"
    snapshot.mkdir(parents=True, exist_ok=True)
    path = snapshot / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "type": "oscal.control",
                "control_id": control_id,
                "implementation_status": "implemented",
            }
        )
    )
    return path


# ---------------------------------------------------------------------------
# CLI-level behavior
# ---------------------------------------------------------------------------


def test_production_export_rejects_empty_control_set(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_plan(vault)

    assert main(["--vault", str(vault)]) == 1
    assert not (vault / ".exports/oscal-json").exists()


def test_allow_synthetic_is_explicit_development_opt_in(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_plan(vault)

    assert main(["--vault", str(vault), "--allow-synthetic"]) == 0


def test_per_vault_policy_can_opt_in_with_a_recorded_rationale(tmp_path: Path) -> None:
    """A vault-scoped exception keeps the gate on for every other vault."""
    vault = tmp_path / "vault"
    _write_plan(vault)
    (vault / ".export-policy.json").write_text(
        json.dumps(
            {
                "allow_synthetic": True,
                "rationale": "source document is a FIPS-199 categorization",
            }
        )
    )

    assert main(["--vault", str(vault)]) == 0


def test_policy_opt_in_requires_a_rationale(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_plan(vault)
    (vault / ".export-policy.json").write_text(json.dumps({"allow_synthetic": True}))

    assert main(["--vault", str(vault)]) == 1


def test_malformed_policy_file_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_plan(vault)
    (vault / ".export-policy.json").write_text("{")

    assert main(["--vault", str(vault)]) == 1


def test_malformed_sidecar_fails_and_is_reported(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_plan(vault, controls_parsed=1)
    _write_control(vault, "AC-1")
    bad = vault / ".snapshots/current/bad.json"
    bad.write_text("{")
    report = tmp_path / "report.json"

    assert main(["--vault", str(vault), "--report-json", str(report)]) == 1

    outcomes = json.loads(report.read_text())
    assert any(
        item["path"] == str(bad) and item["status"] == "error" for item in outcomes
    )


def test_untyped_sidecar_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_plan(vault, controls_parsed=1)
    _write_control(vault, "AC-1")
    untyped = vault / ".snapshots/current/untyped.json"
    untyped.write_text(json.dumps({"title": "no type here"}))
    report = tmp_path / "report.json"

    assert main(["--vault", str(vault), "--report-json", str(report)]) == 1

    outcomes = json.loads(report.read_text())
    entry = next(item for item in outcomes if item["path"] == str(untyped))
    assert entry["status"] == "error"
    assert "no type" in entry["error"] or "type/note_type" in entry["error"]


def test_non_object_sidecar_is_an_error(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_plan(vault, controls_parsed=1)
    _write_control(vault, "AC-1")
    (vault / ".snapshots/current/list.json").write_text(json.dumps([1, 2, 3]))

    assert main(["--vault", str(vault)]) == 1


def test_report_accounts_for_every_sidecar(tmp_path: Path) -> None:
    """No sidecar may be absent from the machine-readable report."""
    vault = tmp_path / "vault"
    _write_plan(vault, controls_parsed=1)
    _write_control(vault, "AC-1")
    (vault / ".snapshots/current/Index.json").write_text(
        json.dumps({"type": "okf.index", "title": "Index"})
    )
    report = tmp_path / "report.json"

    assert main(["--vault", str(vault), "--report-json", str(report)]) == 0

    outcomes = json.loads(report.read_text())
    reported = {item["path"] for item in outcomes}
    sidecars = {str(path) for path in (vault / ".snapshots/current").rglob("*.json")}
    assert sidecars <= reported
    statuses = {item["status"] for item in outcomes}
    assert "not-exportable" in statuses


def test_failed_ssp_does_not_export_other_vault_documents(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_plan(vault)
    component = vault / ".snapshots/current/component.json"
    component.write_text(
        json.dumps({"type": "oscal.component_definition", "name": "Example component"})
    )

    assert main(["--vault", str(vault)]) == 1
    assert not (vault / ".exports/oscal-json").exists()


def test_failed_vault_clears_stale_exports(tmp_path: Path) -> None:
    """A failing run must not leave a previous run's document to be validated."""
    vault = tmp_path / "vault"
    plan_path = _write_plan(vault, controls_parsed=1)
    _write_control(vault, "AC-1")

    assert main(["--vault", str(vault)]) == 0
    exported = vault / ".exports/oscal-json/test-plan-ssp.json"
    assert exported.is_file()

    # The vault now loses its control note but still declares one.
    (vault / ".snapshots/current/control.json").unlink()
    assert plan_path.is_file()

    assert main(["--vault", str(vault)]) == 1
    assert not exported.exists()


def test_one_failing_vault_does_not_suppress_another(tmp_path: Path) -> None:
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    _write_plan(good, controls_parsed=1)
    _write_control(good, "AC-1")
    _write_plan(bad)

    # `bad` sorts before `good`, so a shared failure flag would previously have
    # skipped every later vault's exports.
    assert main(["--vault", str(bad), "--vault", str(good)]) == 1
    assert (good / ".exports/oscal-json/test-plan-ssp.json").is_file()
    assert not (bad / ".exports/oscal-json").exists()


def test_control_without_id_fails_the_command(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_plan(vault, controls_parsed=1)
    _write_control(vault, None)

    assert main(["--vault", str(vault)]) == 1
    assert not (vault / ".exports/oscal-json").exists()


def test_control_count_mismatch_fails_the_command(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_plan(vault, controls_parsed=2)
    _write_control(vault, "AC-1")

    assert main(["--vault", str(vault)]) == 1


# ---------------------------------------------------------------------------
# Library-level behavior
# ---------------------------------------------------------------------------


def test_library_rejects_invalid_control_without_synthetic_fallback() -> None:
    plan = {"frontmatter": {"plan_id": "test-plan", "title": "Test plan"}}
    invalid_control = {"frontmatter": {"control_id": None}}

    with pytest.raises(ControlConversionError, match="cannot export control"):
        canonical_to_oscal_ssp(
            plan,
            control_sidecars=[invalid_control],
        )


def test_library_default_is_fail_closed_on_zero_controls() -> None:
    plan = {"frontmatter": {"plan_id": "test-plan", "title": "Test plan"}}

    with pytest.raises(MissingControlsError):
        canonical_to_oscal_ssp(plan, control_sidecars=[])


def test_library_rejects_control_count_regression() -> None:
    plan = {
        "frontmatter": {
            "plan_id": "test-plan",
            "title": "Test plan",
            "controls_parsed": 3,
        }
    }
    control = {"frontmatter": {"control_id": "AC-1"}}

    with pytest.raises(ControlParityError, match="controls_parsed=3"):
        canonical_to_oscal_ssp(plan, control_sidecars=[control])


def test_library_accepts_matching_control_count() -> None:
    plan = {
        "frontmatter": {
            "plan_id": "test-plan",
            "title": "Test plan",
            "controls_parsed": 1,
        }
    }
    control = {"frontmatter": {"control_id": "AC-1"}}

    ssp = canonical_to_oscal_ssp(plan, control_sidecars=[control])
    requirements = ssp["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ]
    assert [r["control-id"] for r in requirements] == ["ac-1"]


def test_wipe_vault_preserves_export_policy_and_reads_registry_policy(
    tmp_path: Path,
) -> None:
    from compliance.scripts.export_obsidian_vault import (
        _read_registry_export_policy,
        _wipe_vault,
    )

    vault = tmp_path / "vault"
    vault.mkdir()
    policy_path = vault / ".export-policy.json"
    policy_path.write_text(
        json.dumps({"allow_synthetic": True, "rationale": "FIPS-199 categorization"})
    )
    stale = vault / "Controls" / "AC-01.md"
    stale.parent.mkdir()
    stale.write_text("stale")

    _wipe_vault(vault)
    assert policy_path.is_file()
    assert not stale.exists()

    reg = tmp_path / "registry.yaml"
    reg.write_text(
        "systems:\n"
        "  - plan_id: fpdf-ui-fips-199\n"
        "    export_policy:\n"
        "      allow_synthetic: true\n"
        "      rationale: FIPS-199 categorization\n"
    )
    loaded = _read_registry_export_policy(str(reg), "fpdf-ui-fips-199")
    assert loaded == {
        "allow_synthetic": True,
        "rationale": "FIPS-199 categorization",
    }
