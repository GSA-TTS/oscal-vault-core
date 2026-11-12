import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compliance.lib.oscal_source import (  # noqa: E402
    load_from_oscal_ssp,
    origination_provider_map_from_oscal_ssp,
    statement_text_map_from_oscal_ssp,
)


def test_public_core_extracts_statement_component_response_and_status():
    ssp = {
        "system-security-plan": {
            "control-implementation": {
                "implemented-requirements": [
                    {
                        "control-id": "ac-1",
                        "statements": [
                            {
                                "statement-id": "ac-1_smt.a",
                                "by-components": [
                                    {
                                        "description": "Synthetic response",
                                        "implementation-status": {"state": "planned"},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        }
    }

    controls = load_from_oscal_ssp(ssp)
    statements = statement_text_map_from_oscal_ssp(ssp)

    assert controls[0].implementation_status == "planned"
    assert statements == {"ac-1": {"ac-1_smt.a": "Synthetic response"}}


def test_statement_text_map_prepends_whole_control_and_deduplicates():
    ssp = {
        "system-security-plan": {
            "system-implementation": {
                "components": [
                    {
                        "uuid": "c-provider",
                        "title": "cloud.gov Pages",
                        "type": "service",
                    },
                    {"uuid": "c-self", "title": "This System", "type": "this-system"},
                ]
            },
            "control-implementation": {
                "implemented-requirements": [
                    {
                        "control-id": "ac-2",
                        "by-components": [
                            {
                                "component-uuid": "c-self",
                                "description": "Whole-control automation narrative.",
                            },
                            {
                                "component-uuid": "c-self",
                                "description": "Part a:\n\nPart-a split text.\n\nPart b:\n\nPart-b split text.",
                            },
                        ],
                        "statements": [
                            {
                                "statement-id": "ac-2_smt.a",
                                "by-components": [
                                    {
                                        "description": "Policy is defined in Order 2100.1."
                                    },
                                    {
                                        "description": "1. Policy is defined in Order 2100.1. Additional tenant detail."
                                    },
                                ],
                            },
                            {
                                "statement-id": "ac-2_smt.b",
                                "by-components": [
                                    {"component-uuid": "c-provider", "description": ""}
                                ],
                            },
                        ],
                    }
                ]
            },
        }
    }

    statements = statement_text_map_from_oscal_ssp(ssp)
    assert "Whole-control automation narrative." in statements["ac-2"]["ac-2_smt.a"]
    assert "Part-a split text." in statements["ac-2"]["ac-2_smt.a"]
    assert statements["ac-2"]["ac-2_smt.a"].count("Order 2100.1") == 1
    assert statements["ac-2"]["ac-2_smt.b"] == "Part-b split text."
    assert origination_provider_map_from_oscal_ssp(ssp) == {"ac-2": "cloud.gov Pages"}
