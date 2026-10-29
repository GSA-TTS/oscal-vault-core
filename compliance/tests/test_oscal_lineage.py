from pathlib import Path
import json

import jsonschema

from compliance.lib.export_oscal import canonical_to_oscal_component_definition
from compliance.lib.oscal_lineage import (
    component_definition_lineage,
    control_export_lineage,
    mapping_lineage_report,
    runtime_derivation,
    validate_lineage_report,
    validate_runtime_derivations,
    verify_oscal_lineage,
)

_CONTRACT = Path(__file__).resolve().parents[1] / "datacontracts/fedramp-ssp-v1.yaml"


def _canonical():
    return {
        "frontmatter": {
            "type": "oscal.component_definition",
            "name": "Example service",
            "component_type": "service",
        },
        "body_sections": [{"heading": "Description", "content": "Example"}],
        "table_records": [
            {
                "source_locator": "markdown:line[4]",
                "kind": "external_service",
                "disposition": "mapped",
                "headers": ["Service Element", "Response"],
                "rows": [["Service Name", "login.gov"], ["Protocol", "SAML 2.0"]],
            }
        ],
        "source_table_id": "word/document.xml#body/tbl[1]",
        "source_lineage": [
            {
                "source_id": "word/document.xml#body/tbl[1]",
                "canonical_pointer": "/source_table_id",
            }
        ],
    }


def test_component_table_values_resolve_to_exact_oscal_props():
    canonical = _canonical()
    oscal = canonical_to_oscal_component_definition(canonical)
    lineage = component_definition_lineage(canonical, oscal)

    mapped = [item for item in lineage if item["export_disposition"] == "native_prop"]
    assert len(mapped) == 2
    assert all(item["oscal_pointer"] for item in mapped)
    assert lineage[-1]["export_disposition"] == "preserved_only"
    assert lineage[-1]["oscal_pointer"] is None
    assert verify_oscal_lineage(canonical, oscal, lineage) == []


def test_lineage_fails_when_oscal_value_is_removed_or_changed():
    canonical = _canonical()
    oscal = canonical_to_oscal_component_definition(canonical)
    lineage = component_definition_lineage(canonical, oscal)
    oscal["component-definition"]["components"][0]["props"][0]["value"] = "changed"

    findings = verify_oscal_lineage(canonical, oscal, lineage)

    assert any("values differ" in finding for finding in findings)


def test_every_active_mapping_has_a_schema_versioned_lineage_claim():
    report = mapping_lineage_report(_CONTRACT)

    assert report["schema_version"] == 1
    assert report["counters"] == {
        "native": 13,
        "extension_bridge": 5,
        "preserved_only": 0,
        "active_mappings": 18,
        "covered_active_mappings": 18,
    }
    assert len(report["derivations"]) == 18
    crm = next(
        item
        for item in report["derivations"]
        if item["mapping_source"] == "oscal_control.crm"
    )
    assert crm["cardinality"] == "one_to_many"
    assert crm["derivation_kind"] == "fan_out"
    assert len(crm["oscal_pointers"]) == 2
    assert all(item["canonical_pointers"] for item in report["derivations"])
    relationship = next(
        item
        for item in report["derivations"]
        if item["mapping_source"] == "oscal_x_relationship.ports_protocols_services"
    )
    assert set(relationship["allowed_operators"]) == {
        "gfm_table_parse",
        "port_expression_parse",
        "record_fan_out",
    }
    assert validate_lineage_report(report) == []
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "schemas/lineage/oscal_lineage_schema_v1.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.validate(report, schema)


def test_control_statement_and_parameter_lineage_uses_concrete_oscal_pointers():
    controls = [
        {
            "frontmatter": {
                "control_id": "AC-01",
                "parameters": [{"param_id": "ac-1_odp.01", "value": "annually"}],
            },
            "body_sections": [{"heading": "Part a", "content": "Policy narrative"}],
        }
    ]
    oscal = {
        "system-security-plan": {
            "control-implementation": {
                "implemented-requirements": [
                    {
                        "control-id": "ac-01",
                        "statements": [
                            {
                                "statement-id": "ac-01_smt.a",
                                "description": "Policy narrative",
                            }
                        ],
                        "set-parameters": [
                            {"param-id": "ac-1_odp.01", "values": ["annually"]}
                        ],
                    }
                ]
            }
        }
    }

    lineage = control_export_lineage(controls, oscal)

    assert {item["mapping_source"] for item in lineage} == {
        "oscal.control.control_id",
        "oscal.control.statement",
        "oscal.control.parameter",
    }
    assert all(
        "[]" not in pointer for item in lineage for pointer in item["oscal_pointers"]
    )


def test_runtime_operator_must_be_allowed_by_mapping_capability():
    capabilities = mapping_lineage_report(_CONTRACT)
    runtime = [
        runtime_derivation(
            mapping_source="oscal_x_boundary.description",
            source_ids=["source"],
            canonical_pointers=["/body_sections"],
            oscal_pointers=[
                "/system-security-plan/system-characteristics/network-architecture/description"
            ],
            events=[{"operator": "gfm_table_parse", "residual": []}],
        )
    ]

    assert any(
        "runtime operator is not allowed" in finding
        for finding in validate_runtime_derivations(capabilities, runtime)
    )
