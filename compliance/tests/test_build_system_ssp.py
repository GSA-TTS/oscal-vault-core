"""Tests for system response YAML to OSCAL SSP projection (synthetic data only)."""

import json

import yaml

from compliance.scripts import build_system_ssp


def test_build_oscal_preserves_raw_response_metadata_and_parts():
    system = {"slug": "example", "name": "Example System"}
    source = {
        "response_defaults": {
            "implementation_status": "planned",
            "origination": ["system-specific"],
        },
        "control_metadata": {
            "AC-2": {
                "implementation_status": "partially-implemented",
                "origination": ["hybrid", "inherited"],
                "responsible_roles": ["System Owner"],
            }
        },
        "responses": {
            "AC-2": [
                {"part": "a", "implementation": " Account narrative.\n"},
                {"part": None, "implementation": " General narrative. "},
            ],
            "AC-3": [{"part": None, "implementation": "Enforcement narrative."}],
        },
    }
    sidecar = {
        "sdr": {
            "parameter_values": {
                "AC-2": {"AC-2_prm_1": "monthly"},
            }
        }
    }
    countermeasures = {
        "countermeasures": [
            {
                "control_id": "AC-2",
                "mechanisms": [{"ref": "account-review"}, {"ref": "missing"}],
            }
        ]
    }
    evidence = {
        "evidence": {"account-review": "Account review evidence."},
        "evidence_links": {"account-review": "https://localhost/evidence"},
        "evidence_artifacts": {
            "account-review": {
                "title": "Account review",
                "artifact_type": "report",
                "is_continuous": False,
            }
        },
    }

    first = build_system_ssp.build_oscal(
        system,
        source,
        sidecar=sidecar,
        countermeasures=countermeasures,
        evidence=evidence,
    )
    second = build_system_ssp.build_oscal(
        system,
        source,
        sidecar=sidecar,
        countermeasures=countermeasures,
        evidence=evidence,
    )
    requirements = first["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ]

    assert first == second
    assert [item["control-id"] for item in requirements] == ["ac-2", "ac-3"]
    assert requirements[0]["props"] == [
        {"name": "implementation-status", "value": "partially-implemented"},
        {"name": "control-origination", "value": "hybrid"},
        {"name": "control-origination", "value": "inherited"},
    ]
    assert requirements[0]["responsible-roles"] == [{"role-id": "system-owner"}]
    assert requirements[0]["set-parameters"] == [
        {"param-id": "AC-2_prm_1", "values": ["monthly"]}
    ]
    assert [link["text"] for link in requirements[0]["links"]] == [
        "account-review",
        "missing",
    ]
    assert requirements[0]["statements"][0]["statement-id"] == "ac-2_stmt.a"
    assert requirements[0]["statements"][1]["statement-id"] == "ac-2_stmt"
    assert requirements[0]["statements"][0]["remarks"] == "Account narrative."
    assert requirements[0]["by-components"][0]["implementation-status"]["state"] == (
        "partially-implemented"
    )
    assert requirements[1]["props"][0]["value"] == "planned"
    resources = first["system-security-plan"]["back-matter"]["resources"]
    assert len(resources) == 2
    assert resources[0]["title"] == "Account review"
    assert resources[0]["rlinks"] == [{"href": "https://localhost/evidence"}]
    assert resources[1]["description"] == "Unresolved evidence reference: missing"


def test_statement_id_normalizes_template_specific_part_keys():
    assert build_system_ssp.statement_id("PT-7", "PT-07_01_") == "pt-7_stmt.pt-07-01"


def test_control_token_preserves_enhancement_identity():
    assert build_system_ssp.control_token("AC-2(13)") == "ac-2.13"


def test_cli_builds_ssp_from_a_system_directory(tmp_path):
    system_dir = tmp_path / "example"
    system_dir.mkdir()
    (system_dir / "system.yaml").write_text(
        yaml.safe_dump({"slug": "example", "name": "Example System"}), encoding="utf-8"
    )
    (system_dir / "responses.yaml").write_text(
        yaml.safe_dump(
            {
                "response_defaults": {
                    "implementation_status": "planned",
                    "origination": ["system-specific"],
                },
                "responses": {"AC-2": [{"part": None, "implementation": "Text."}]},
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out" / "ssp.json"

    assert build_system_ssp.main([str(system_dir), "--out", str(out)]) == 0

    document = json.loads(out.read_text(encoding="utf-8"))
    requirements = document["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ]
    assert [item["control-id"] for item in requirements] == ["ac-2"]


def test_cli_rejects_directory_without_responses(tmp_path, capsys):
    import pytest

    (tmp_path / "system.yaml").write_text("slug: example\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        build_system_ssp.main([str(tmp_path)])
    assert "responses.yaml" in capsys.readouterr().err


def test_uuids_are_deterministic_and_namespace_scoped():
    import uuid

    system = {"slug": "example", "name": "Example System"}
    source = {
        "response_defaults": {
            "implementation_status": "planned",
            "origination": ["system-specific"],
        },
        "responses": {"AC-2": [{"part": None, "implementation": "Text."}]},
    }
    other = uuid.uuid5(uuid.NAMESPACE_URL, "urn:example:other-namespace")

    default = build_system_ssp.build_oscal(system, source)
    again = build_system_ssp.build_oscal(system, source)
    scoped = build_system_ssp.build_oscal(system, source, namespace=other)

    assert default == again
    assert (
        default["system-security-plan"]["uuid"]
        != scoped["system-security-plan"]["uuid"]
    )
