import json
from pathlib import Path

from compliance.lib.lineage_attestation import build_attestations, validate_attestations
from compliance.scripts import validate_lineage_attestations as cli

CORE = Path(__file__).resolve().parents[2]


def _write(path: Path, **overrides) -> Path:
    doc = build_attestations(
        CORE, reviewer="role:example-reviewer", rationale="Reviewed."
    )
    doc.update(overrides)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_attestation_matching_the_current_implementation_is_valid(tmp_path):
    assert validate_attestations(_write(tmp_path / "a.json"), CORE) == []


def test_implementation_change_invalidates_the_attestation(tmp_path):
    (tmp_path / "compliance/lib").mkdir(parents=True)
    for name in ("transforms.py", "oscal_extension_bridge.py"):
        (tmp_path / "compliance/lib" / name).write_text("# changed\n", encoding="utf-8")
    findings = validate_attestations(_write(tmp_path / "a.json"), tmp_path)
    assert findings and all("stale review attestation" in f for f in findings)


def test_missing_reviewer_or_rejected_decision_is_reported(tmp_path):
    doc = build_attestations(CORE, reviewer="", rationale="")
    path = tmp_path / "a.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert any("incomplete" in f for f in validate_attestations(path, CORE))
    path.write_text(
        json.dumps(
            build_attestations(CORE, reviewer="r", rationale="x", decision="rejected")
        ),
        encoding="utf-8",
    )
    assert any("not approved" in f for f in validate_attestations(path, CORE))


def test_missing_operator_and_unknown_operator_are_reported(tmp_path):
    doc = build_attestations(CORE, reviewer="r", rationale="x")
    doc["attestations"] = doc["attestations"][1:] + [{"operator": "invented"}]
    path = tmp_path / "a.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    findings = validate_attestations(path, CORE)
    assert any("missing review attestation" in f for f in findings)
    assert any("unknown attested operators" in f for f in findings)


def test_cli_defaults_to_the_workspace_and_reports_a_missing_file(tmp_path, capsys):
    assert cli.main(["--attestations", str(tmp_path / "nope.json")]) == 2
    assert "attestations not found" in capsys.readouterr().err
    assert cli.main(["--attestations", str(_write(tmp_path / "ok.json"))]) == 0
