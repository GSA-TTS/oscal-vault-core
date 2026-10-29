from copy import deepcopy

from compliance.lib.cyclonedx_tmbom import (
    normalize_scenario_threats,
    project_tmbom_topology,
)


def test_projection_normalizes_legacy_topology_fields():
    document = {
        "blueprints": [
            {
                "zones": [
                    {
                        "bom-ref": "zone-child",
                        "name": "Child",
                        "type": "trust",
                        "trustLevel": 80,
                        "parentZone": "zone-parent",
                    }
                ],
                "boundaries": [{"zones": ["zone-child", "zone-parent"]}],
                "dataStores": [{"bom-ref": "store", "name": "Store"}],
                "flows": [
                    {
                        "bom-ref": "flow-request",
                        "source": "a",
                        "destination": "b",
                        "type": "data",
                        "authMechanism": "OIDC",
                        "crosses_trust_zone": True,
                        "data_classification": "Internal",
                    }
                ],
            }
        ],
        "controls": [{"mitigations": ["scenario-1"]}],
        "threats": {
            "threats": [{"bom-ref": "threat-1", "name": "Threat One"}],
            "scenarios": [
                {
                    "bom-ref": "scenario-1",
                    "threat": "threat-1",
                    "affectedFlows": ["flow-request"],
                    "inherentSeverity": "high",
                    "residualSeverity": "medium",
                }
            ],
        },
    }

    project_tmbom_topology(document)

    blueprint = document["blueprints"][0]
    zone = blueprint["zones"][0]
    assert zone["parent"] == "zone-parent"
    assert {p["name"]: p["value"] for p in zone["properties"]}[
        "precogly:trust-level"
    ] == "80"
    assert blueprint["boundaries"][0] == {
        "bom-ref": "boundary-1",
        "name": "Child / zone-parent Boundary",
        "type": "trust",
        "zones": ["zone-child", "zone-parent"],
    }
    assert blueprint["dataStores"][0]["type"]["name"] == "legacy-unspecified"
    flow = blueprint["flows"][0]
    assert flow["name"] == "a to b"
    assert "authMechanism" not in flow
    assert {p["name"]: p["value"] for p in flow["properties"]} == {
        "owasp:auth_mechanism": "OIDC",
        "resp:crosses-trust-zone": "true",
        "resp:data-classification": "Internal",
    }
    assert "mitigations" not in document["controls"][0]
    scenario = document["threats"]["scenarios"][0]
    assert scenario["name"] == "Threat One"
    assert scenario["threats"] == ["threat-1"]
    assert "affectedFlows" not in scenario
    assert {p["name"]: p["value"] for p in scenario["properties"]} == {
        "resp:inherent-severity": "high",
        "resp:residual-severity": "medium",
    }


def test_projection_is_idempotent():
    document = {
        "blueprints": [{"zones": [], "boundaries": [], "dataStores": [], "flows": []}],
        "controls": [{"mitigations": ["scenario-1"]}],
    }

    project_tmbom_topology(document)
    project_tmbom_topology(document)

    assert "properties" not in document["controls"][0]


def test_projection_preserves_native_topology_values_without_legacy_fields():
    document = {
        "blueprints": [
            {
                "zones": [
                    {
                        "bom-ref": "zone-parent",
                        "name": "Parent",
                        "parent": "zone-root",
                    }
                ],
                "boundaries": [
                    {
                        "bom-ref": "boundary-native",
                        "name": "Native Boundary",
                        "type": "network",
                    }
                ],
                "dataStores": [
                    {"bom-ref": "store-native", "type": {"name": "postgres"}}
                ],
                "flows": [
                    {
                        "bom-ref": "flow-native",
                        "source": "missing-source",
                        "destination": "missing-destination",
                        "name": "Native Flow",
                    }
                ],
            }
        ],
        "controls": [{"bom-ref": "control-native"}],
        "threats": {
            "threats": [{"bom-ref": "threat-native", "name": "Native Threat"}],
            "scenarios": [
                {
                    "bom-ref": "scenario-native",
                    "name": "Native Scenario",
                    "threats": ["threat-native"],
                }
            ],
        },
    }

    project_tmbom_topology(document)

    blueprint = document["blueprints"][0]
    assert blueprint["zones"][0] == {
        "bom-ref": "zone-parent",
        "name": "Parent",
        "parent": "zone-root",
    }
    assert blueprint["boundaries"][0] == {
        "bom-ref": "boundary-native",
        "name": "Native Boundary",
        "type": "network",
    }
    assert blueprint["dataStores"][0]["type"] == {"name": "postgres"}
    assert blueprint["flows"][0]["name"] == "Native Flow"
    assert document["threats"]["scenarios"][0] == {
        "bom-ref": "scenario-native",
        "name": "Native Scenario",
        "threats": ["threat-native"],
    }


def test_projection_replaces_existing_legacy_properties_and_uses_fallback_labels():
    document = {
        "blueprints": [
            {
                "zones": [
                    {
                        "bom-ref": "zone-a",
                        "name": "Zone A",
                        "trustLevel": False,
                        "properties": [
                            {"name": "precogly:trust-level", "value": "stale"},
                            {"name": "unrelated", "value": "retained"},
                        ],
                    }
                ],
                "boundaries": [{"zones": ["zone-a", "unknown-zone"]}],
                "dataStores": [{}],
                "flows": [
                    {
                        "source": "unknown-source",
                        "destination": "unknown-destination",
                        "authMechanism": "OIDC",
                        "crosses_trust_zone": False,
                        "data_classification": ["CUI", "PII"],
                        "properties": [
                            {"name": "owasp:auth_mechanism", "value": "stale"},
                            {"name": "resp:crosses-trust-zone", "value": "true"},
                        ],
                    }
                ],
            }
        ],
        "threats": {
            "scenarios": [
                {
                    "bom-ref": "scenario-unknown-threat",
                    "threat": "unknown-threat",
                    "inherentSeverity": {"score": 5},
                    "residualSeverity": ["medium"],
                    "properties": [
                        {"name": "resp:inherent-severity", "value": "stale"}
                    ],
                },
                {},
            ]
        },
    }

    project_tmbom_topology(document)

    blueprint = document["blueprints"][0]
    assert blueprint["boundaries"][0] == {
        "bom-ref": "boundary-1",
        "name": "Zone A / unknown-zone Boundary",
        "type": "trust",
        "zones": ["zone-a", "unknown-zone"],
    }
    assert blueprint["dataStores"][0]["type"]["name"] == "legacy-unspecified"
    assert blueprint["flows"][0]["name"] == "unknown-source to unknown-destination"
    assert {p["name"]: p["value"] for p in blueprint["zones"][0]["properties"]} == {
        "precogly:trust-level": "false",
        "unrelated": "retained",
    }
    assert {p["name"]: p["value"] for p in blueprint["flows"][0]["properties"]} == {
        "owasp:auth_mechanism": "OIDC",
        "resp:crosses-trust-zone": "false",
        "resp:data-classification": '["CUI","PII"]',
    }
    first_scenario, second_scenario = document["threats"]["scenarios"]
    assert first_scenario["name"] == "Scenario Unknown Threat"
    assert first_scenario["threats"] == ["unknown-threat"]
    assert {p["name"]: p["value"] for p in first_scenario["properties"]} == {
        "resp:inherent-severity": '{"score":5}',
        "resp:residual-severity": '["medium"]',
    }
    assert second_scenario["name"] == "Scenario 2"


def test_projection_is_idempotent_for_mixed_native_and_legacy_documents():
    document = {
        "blueprints": [
            {
                "zones": [{"bom-ref": "zone", "trustLevel": 50}],
                "flows": [
                    {
                        "bom-ref": "flow",
                        "source": "zone",
                        "destination": "external",
                        "authMechanism": "mTLS",
                    }
                ],
            },
            {
                "boundaries": [{"zones": ["missing-zone"]}],
                "dataStores": [{"bom-ref": "store"}],
            },
        ],
        "controls": [{"mitigations": ["scenario"]}],
        "threats": {
            "threats": [{"bom-ref": "threat", "name": "Known Threat"}],
            "scenarios": [
                {"bom-ref": "scenario", "threat": "threat"},
                {"bom-ref": "scenario-native", "threats": ["threat"]},
            ],
        },
    }

    project_tmbom_topology(document)
    first_projection = deepcopy(document)
    project_tmbom_topology(document)

    assert document == first_projection


def test_scenario_name_defaults_from_plural_threats_relation() -> None:
    document = {
        "blueprints": [],
        "threats": {
            "threats": [{"bom-ref": "t1", "name": "Account takeover"}],
            "scenarios": [
                {"bom-ref": "s-singular", "threat": "t1"},
                {"bom-ref": "s-plural", "threats": ["t1"]},
            ],
        },
    }
    project_tmbom_topology(document)
    by_ref = {s["bom-ref"]: s for s in document["threats"]["scenarios"]}
    assert (
        by_ref["s-singular"]["name"] == by_ref["s-plural"]["name"] == "Account takeover"
    )
    assert by_ref["s-plural"]["threats"] == ["t1"]


def test_scenario_threat_is_rewritten_to_the_canonical_array():
    document = {
        "threats": {
            "threats": [{"bom-ref": "t1"}],
            "scenarios": [{"bom-ref": "s1", "threat": "t1"}, {"bom-ref": "s2"}],
        }
    }
    assert normalize_scenario_threats(document) == 1
    assert document["threats"]["scenarios"][0] == {"bom-ref": "s1", "threats": ["t1"]}
    assert normalize_scenario_threats(document) == 0
