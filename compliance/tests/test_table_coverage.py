from __future__ import annotations

import json
from pathlib import Path

from compliance.scripts.table_coverage import inventory, main


def _sidecar(root: Path, disposition: str = "unmapped") -> Path:
    path = root / "example" / ".snapshots" / "current" / "Components" / "app.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "table_records": [
                    {
                        "source_heading": "Asset Inventory",
                        "source_locator": "markdown:line[4]",
                        "headers": ["Unknown", "Columns"],
                        "classification": "opaque",
                        "kind": "opaque",
                        "disposition": disposition,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_debt_id_is_stable_when_source_line_moves(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path)
    first = inventory(tmp_path)
    document = json.loads(sidecar.read_text(encoding="utf-8"))
    document["table_records"][0]["source_locator"] = "markdown:line[40]"
    sidecar.write_text(json.dumps(document), encoding="utf-8")

    assert set(inventory(tmp_path)["debt"]) == set(first["debt"])


def test_baseline_rejects_new_debt(tmp_path: Path, monkeypatch) -> None:
    vaults = tmp_path / "vaults"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"schema_version": 1, "counts": {}, "debt": {}}), encoding="utf-8"
    )
    _sidecar(vaults)
    monkeypatch.setattr(
        "sys.argv",
        ["table_coverage.py", "--vaults", str(vaults), "--baseline", str(baseline)],
    )

    assert main() == 2


def test_strict_mode_accepts_fully_mapped_vault(tmp_path: Path, monkeypatch) -> None:
    vaults = tmp_path / "vaults"
    _sidecar(vaults, disposition="mapped")
    monkeypatch.setattr(
        "sys.argv", ["table_coverage.py", "--vaults", str(vaults), "--strict"]
    )

    assert main() == 0
