import json
from pathlib import Path

from compliance.scripts import progressive_coverage


def test_stable_ids_are_deterministic():
    assert progressive_coverage._stable_id(
        "draft_markers", "x"
    ) == progressive_coverage._stable_id("draft_markers", "x")
    assert progressive_coverage._stable_id(
        "draft_markers", "x"
    ) != progressive_coverage._stable_id("thin_notes", "x")


def test_baseline_rejects_new_aggregate_debt(tmp_path: Path, monkeypatch):
    vaults = tmp_path / "vaults"
    vaults.mkdir()
    structural = tmp_path / "structural.json"
    structural.write_text(json.dumps({}), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"schema_version": 1, "counts": {}, "debt": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(progressive_coverage, "_VAULT_BASELINE", structural)
    monkeypatch.setattr(
        "sys.argv",
        [
            "progressive_coverage.py",
            "--vaults",
            str(vaults),
            "--baseline",
            str(baseline),
        ],
    )
    assert progressive_coverage.main() == 0


def test_strict_mode_rejects_existing_debt(tmp_path: Path, monkeypatch):
    vaults = tmp_path / "vaults"
    vaults.mkdir()
    structural = tmp_path / "structural.json"
    structural.write_text(
        json.dumps({"draft_markers": ["example.md: TODO"]}), encoding="utf-8"
    )
    monkeypatch.setattr(progressive_coverage, "_VAULT_BASELINE", structural)
    monkeypatch.setattr(
        "sys.argv", ["progressive_coverage.py", "--vaults", str(vaults), "--strict"]
    )
    assert progressive_coverage.main() == 2


def test_baseline_rejects_new_structural_debt(tmp_path: Path, monkeypatch):
    vaults = tmp_path / "vaults"
    vaults.mkdir()
    structural = tmp_path / "structural.json"
    structural.write_text(
        json.dumps({"draft_markers": ["example.md: TODO"]}), encoding="utf-8"
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"schema_version": 1, "counts": {}, "debt": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(progressive_coverage, "_VAULT_BASELINE", structural)
    monkeypatch.setattr(
        "sys.argv",
        [
            "progressive_coverage.py",
            "--vaults",
            str(vaults),
            "--baseline",
            str(baseline),
        ],
    )
    assert progressive_coverage.main() == 2
