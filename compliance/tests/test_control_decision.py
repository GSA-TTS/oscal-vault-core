import json
from pathlib import Path

import pytest

from compliance.lib.control_decision import (
    DEFAULT_D3FEND_MAPPING,
    BedrockGrader,
    build_packets,
    deterministic_grade,
    export_assessment_reference,
    export_d3fend_mapping,
    load_assessment_reference,
    load_d3fend,
    normalize_control_id,
    parse_assessment_workbook,
    validate_grade,
)

ROOT = Path(__file__).resolve().parents[2]


def test_control_id_normalization():
    assert normalize_control_id("AC-02") == "AC-2"
    assert normalize_control_id("SC-07(03)") == "SC-7(3)"
    assert normalize_control_id("IA-2.1") == "IA-2(1)"


def test_workbook_parser_reads_assessment_methods(tmp_path: Path):
    # The parser is exercised against the real ODS package when available in a
    # caller repository; this fixture verifies fail-closed ZIP handling.
    invalid = tmp_path / "invalid.ods"
    invalid.write_bytes(b"not an ods")
    with pytest.raises(Exception):
        parse_assessment_workbook(invalid)


def test_assessment_reference_export_and_load_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "compliance.lib.control_decision.parse_assessment_workbook",
        lambda path: {
            "AC-2": [
                {
                    "citation_id": "workbook:AC:row:3:AC-02a",
                    "procedure_id": "AC-02a",
                    "control_name": "Account Management",
                    "objective": "accounts are managed",
                    "methods": {"examine": "account records", "test": "configuration"},
                    "historical_observation": "internal screenshot.png",
                    "historical_result": "Fully Satisfied",
                    "historical_recommendation": "",
                    "historical_context_only": True,
                }
            ]
        },
    )
    workbook = tmp_path / "workbook.ods"
    workbook.write_bytes(b"fixture")

    exported = export_assessment_reference(workbook, "example-low-rev5")
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(exported), encoding="utf-8")
    loaded = load_assessment_reference(path)

    assert exported["source"]["historical_observations_included"] is False
    assert exported["source"]["redactions"] == {"urls": 0, "emails": 0}
    historical = exported["controls"]["AC-2"]["objectives"][0]["historical"]
    assert historical["observation"] is None
    assert historical["observation_present"] is True
    assert historical["observation_sha256"]
    assert loaded["AC-2"][0]["methods"] == {
        "examine": "account records",
        "test": "configuration",
    }


def _ssp(tmp_path: Path) -> Path:
    path = tmp_path / "ssp.json"
    path.write_text(
        json.dumps(
            {
                "system-security-plan": {
                    "system-implementation": {
                        "components": [{"uuid": "component", "title": "This System"}]
                    },
                    "control-implementation": {
                        "implemented-requirements": [
                            {
                                "control-id": "ac-2",
                                "by-components": [
                                    {
                                        "component-uuid": "component",
                                        "description": "",
                                        "implementation-status": {"state": "planned"},
                                    }
                                ],
                            }
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_d3fend_is_explicitly_unavailable_without_source():
    mappings, metadata = load_d3fend(None)
    assert mappings == {}
    assert metadata["status"] == "unavailable"


def test_pinned_d3fend_mapping_is_loaded_by_control(tmp_path: Path):
    path = tmp_path / "d3fend.json"
    payload = {
        "schema_version": 1,
        "source": "MITRE D3FEND pinned export",
        "source_sha256": "d" * 64,
        "mappings": [
            {
                "d3fend_id": "D3-UAC",
                "name": "User Account Control",
                "nist_controls": ["AC-02"],
                "description": "Account lifecycle countermeasure.",
                "source_uri": "https://d3fend.mitre.org/",
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    mappings, metadata = load_d3fend(path)

    assert metadata["status"] == "available"
    assert mappings["AC-2"][0]["d3fend_id"] == "D3-UAC"


def test_packet_and_deterministic_grade_are_review_gated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "compliance.lib.control_decision.parse_assessment_workbook",
        lambda path: {
            "AC-2": [
                {
                    "citation_id": "workbook:AC:row:3:AC-02a",
                    "procedure_id": "AC-02a",
                    "control_name": "Account Management",
                    "objective": "accounts are managed",
                    "methods": {
                        "examine": "account management policy",
                        "test": "account records",
                    },
                    "historical_observation": "",
                    "historical_result": "",
                    "historical_recommendation": "",
                    "historical_context_only": True,
                }
            ]
        },
    )
    workbook = tmp_path / "workbook.ods"
    workbook.write_bytes(b"fixture")
    packet = build_packets(_ssp(tmp_path), workbook, controls={"AC-2"})[0]
    grade = deterministic_grade(packet)

    assert packet["d3fend"]["status"] == "unavailable"
    assert packet["data_handling"]["llm_safe_default"] is True
    assert "historical_observation" not in packet["assessment_objectives"][0]
    assert packet["assessment_objectives"][0]["historical_observation_present"] is False
    assert {item["id"] for item in packet["deterministic_findings"]} >= {
        "missing-substantive-narrative",
        "unapproved-implementation-status",
        "missing-examine-evidence-reference",
        "missing-test-evidence-reference",
    }
    assert grade["grade"] == "insufficient"
    assert grade["human_review"]["status"] == "pending"


def test_grade_rejects_uncited_claim(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "compliance.lib.control_decision.parse_assessment_workbook", lambda path: {}
    )
    workbook = tmp_path / "workbook.ods"
    workbook.write_bytes(b"fixture")
    packet = build_packets(_ssp(tmp_path), workbook, controls={"AC-2"})[0]
    grade = deterministic_grade(packet)
    grade["citations"].append("invented:citation")

    with pytest.raises(ValueError, match="unknown citations"):
        validate_grade(grade, packet)


def test_bedrock_grader_requires_structured_cited_output(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "compliance.lib.control_decision.parse_assessment_workbook", lambda path: {}
    )
    workbook = tmp_path / "workbook.ods"
    workbook.write_bytes(b"fixture")
    packet = build_packets(_ssp(tmp_path), workbook, controls={"AC-2"})[0]
    citation = packet["allowed_citations"][0]

    class FakeClient:
        def converse(self, **kwargs):
            assert kwargs["inferenceConfig"]["maxTokens"] == 1024
            assert kwargs["inferenceConfig"]["temperature"] == 0
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "name": "submit_control_grade",
                                    "toolUseId": "tool-1",
                                    "input": {
                                        "grade": "partial",
                                        "confidence": 0.7,
                                        "summary": "Narrative and status review remain incomplete.",
                                        "strengths": [],
                                        "missing_prerequisites": [],
                                        "recommended_actions": ["Review status."],
                                        "citations": [citation],
                                    },
                                }
                            }
                        ]
                    }
                }
            }

    grader = object.__new__(BedrockGrader)
    grader.client = FakeClient()
    grader.model_id = "test-model"
    grader.max_tokens = 1024

    grade = grader.grade(packet)
    assert grade["method"] == "llm_assisted"
    assert grade["human_review"]["status"] == "pending"


def test_committed_d3fend_mapping_loads_and_supplies_citations(
    tmp_path: Path, monkeypatch
):
    assert DEFAULT_D3FEND_MAPPING.exists()
    mappings, metadata = load_d3fend(DEFAULT_D3FEND_MAPPING)
    assert metadata["status"] == "available"
    assert metadata["source"] == "MITRE D3FEND to NIST SP 800-53 Rev. 5 Catalog Mapping"
    assert "D3-MFA" in [x["d3fend_id"] for x in mappings["AC-2(1)"]]
    assert "D3-AL" in [x["d3fend_id"] for x in mappings["AC-2(1)"]]

    # Verify export_d3fend_mapping produces a valid mapping artifact
    xlsx_sample = ROOT.parent / "sp800-53r5-control-catalog-d3fend-mapping.xlsx"
    if xlsx_sample.exists():
        out_json = tmp_path / "exported_d3fend.json"
        exported = export_d3fend_mapping(xlsx_sample, out_json)
        assert exported["schema_version"] == 1
        assert len(exported["mappings"]) == 42
        assert out_json.exists()

    monkeypatch.setattr(
        "compliance.lib.control_decision.parse_assessment_workbook", lambda path: {}
    )
    workbook = tmp_path / "workbook.ods"
    workbook.write_bytes(b"fixture")
    ssp_path = _ssp(tmp_path)
    packet = build_packets(
        ssp_path,
        workbook,
        d3fend_mapping=DEFAULT_D3FEND_MAPPING,
        controls={"AC-2(1)"},
    )[0]
    assert packet["d3fend"]["status"] == "available"
    assert any(t["d3fend_id"] == "D3-MFA" for t in packet["d3fend"]["techniques"])
    assert "d3fend:D3-MFA" in packet["allowed_citations"]
