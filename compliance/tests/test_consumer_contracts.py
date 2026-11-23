"""Public contract versions consumed by downstream system repositories."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "compliance" / "datacontracts" / "consumer-contracts.json"


def test_consumer_contract_versions_are_a_small_integer_mapping() -> None:
    data = json.loads(CONTRACTS.read_text())

    assert data["schema_version"] == 1
    assert data["contracts"] == {"docx_roundtrip": 1, "oscal_input": 1}
    assert all(isinstance(version, int) for version in data["contracts"].values())
