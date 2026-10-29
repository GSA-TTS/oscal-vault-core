"""Debt baselines belong to the vaults they describe; the OSCAL namespace is a valid URI."""

import json
import re
from pathlib import Path

from compliance.lib.export_oscal import FEDRAMP_PARSER_NS
from compliance.scripts import progressive_coverage, table_coverage


def _sidecar(vaults: Path, name: str, records: list[dict]) -> None:
    path = vaults / "demo" / ".snapshots" / "current" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"table_records": records}), encoding="utf-8")


OPAQUE = {
    "source_heading": "Example Table",
    "source_locator": "markdown:line[1]",
    "headers": ["A", "B"],
    "classification": "opaque",
    "disposition": "unmapped",
}


def test_oscal_extension_namespace_is_a_scheme_qualified_uri_with_no_person_or_repo():
    assert re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]+:.+$", FEDRAMP_PARSER_NS)
    assert "github.com" not in FEDRAMP_PARSER_NS


def test_default_baselines_resolve_against_the_working_directory_not_the_core(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    import importlib

    tc = importlib.reload(table_coverage)
    pc = importlib.reload(progressive_coverage)
    assert tc._DEFAULT_BASELINE == tmp_path / "scripts" / "table_coverage_baseline.json"
    assert (
        pc._DEFAULT_BASELINE
        == tmp_path / "scripts" / "progressive_coverage_baseline.json"
    )
    assert pc._VAULT_BASELINE == tmp_path / "scripts" / "vault_debt_baseline.json"
    assert tc._DEFAULT_VAULTS == tmp_path / "examples" / "vault"


def test_table_coverage_baseline_round_trip_on_synthetic_vaults(tmp_path, monkeypatch):
    vaults = tmp_path / "vaults"
    _sidecar(vaults, "a.json", [OPAQUE])
    baseline = tmp_path / "baseline.json"
    argv = ["table_coverage.py", "--vaults", str(vaults), "--baseline", str(baseline)]

    monkeypatch.setattr("sys.argv", [*argv, "--write-baseline"])
    assert table_coverage.main() == 0
    written = json.loads(baseline.read_text(encoding="utf-8"))
    assert len(written["debt"]) == 1 and written["counts"]["tables_total"] == 1

    monkeypatch.setattr("sys.argv", argv)
    assert table_coverage.main() == 0

    _sidecar(vaults, "b.json", [{**OPAQUE, "source_heading": "A New Table"}])
    assert table_coverage.main() != 0


def test_missing_baseline_is_reported_not_silently_accepted(
    tmp_path, monkeypatch, capsys
):
    vaults = tmp_path / "vaults"
    vaults.mkdir()
    monkeypatch.setattr(
        "sys.argv",
        [
            "table_coverage.py",
            "--vaults",
            str(vaults),
            "--baseline",
            str(tmp_path / "none.json"),
        ],
    )
    assert table_coverage.main() != 0
    assert "missing" in capsys.readouterr().out.lower()
