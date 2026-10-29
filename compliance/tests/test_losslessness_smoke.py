import json
from pathlib import Path

from compliance.scripts.losslessness_smoke import main, run


def test_losslessness_smoke_reconciles_all_layers(tmp_path: Path):
    assert run(tmp_path) == []
    summary = json.loads(
        (tmp_path / "losslessness-smoke.json").read_text(encoding="utf-8")
    )
    assert summary["package_parts"] > 0
    assert summary["source_nodes"] > 0
    assert summary["source_tables"] > 0
    assert summary["classified_source_tables"] >= 0
    assert summary["opaque_artifacts"] > 0
    assert summary["managed_artifacts"] > 0
    assert summary["projected_rows"] > 0
    assert summary["oscal_lineage_records"] > 0


def test_losslessness_smoke_cli(tmp_path: Path):
    assert main(["--output", str(tmp_path)]) == 0
