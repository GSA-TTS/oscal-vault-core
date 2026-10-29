from pathlib import Path

from compliance.lib.lineage_attestation import validate_attestations


def test_committed_attestations_match_current_implementation():
    root = Path(__file__).resolve().parents[2]
    path = root / "compliance/data/lineage_operator_attestations_v1.json"

    assert validate_attestations(path, root) == []


def test_transform_implementation_change_invalidates_attestation(tmp_path: Path):
    (tmp_path / "compliance/lib").mkdir(parents=True)
    (tmp_path / "compliance/lib/transforms.py").write_text(
        "# changed implementation\n", encoding="utf-8"
    )
    (tmp_path / "compliance/lib/oscal_extension_bridge.py").write_text(
        "# unchanged orchestration fixture\n", encoding="utf-8"
    )
    source = (
        Path(__file__).resolve().parents[2]
        / "compliance/data/lineage_operator_attestations_v1.json"
    )
    attestation = tmp_path / "attestations.json"
    attestation.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    findings = validate_attestations(attestation, tmp_path)

    assert all("stale review attestation" in finding for finding in findings)
