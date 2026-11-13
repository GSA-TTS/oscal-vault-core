import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compliance.lib.oscal_source import (  # noqa: E402
    load_from_oscal_ssp,
    origination_provider_map_from_oscal_ssp,
    statement_text_map_from_oscal_ssp,
    whole_control_text_map_from_oscal_ssp,
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
    assert "Part-b split text." not in statements["ac-2"]["ac-2_smt.a"]
    assert statements["ac-2"]["ac-2_smt.a"].count("Part-a split text.") == 1
    assert origination_provider_map_from_oscal_ssp(ssp) == {"ac-2": "cloud.gov Pages"}


def test_split_parts_preserve_preamble_and_unmatched_sections():
    requirement = {
        "control-id": "ac-1",
        "by-components": [
            {
                "description": "Preamble.\n\nPART C:\n\nThird part.\n\nPart d:\n\nUnmatched detail."
            }
        ],
        "statements": [{"statement-id": "ac-1_smt.c", "description": "Existing."}],
    }
    result = statement_text_map_from_oscal_ssp(
        {"control-implementation": {"implemented-requirements": [requirement]}}
    )
    text = result["ac-1"]["ac-1_smt.c"]
    assert text.count("Third part.") == 1
    assert "Existing." in text
    assert "PART C:" not in text
    assert "Preamble." in text
    assert "Part d:" in text
    assert "Unmatched detail." in text


def test_repeated_part_sections_are_preserved_without_cross_part_duplication():
    req = {
        "control-id": "ac-1",
        "by-components": [
            {
                "description": "Part a:\n\nFirst A.\n\nPart a:\n\nSecond A.\n\nPart b:\n\nOnly B."
            }
        ],
        "statements": [{"statement-id": "ac-1_smt.a"}, {"statement-id": "ac-1_smt.b"}],
    }
    result = statement_text_map_from_oscal_ssp(
        {"control-implementation": {"implemented-requirements": [req]}}
    )["ac-1"]
    assert "First A." in result["ac-1_smt.a"]
    assert "Second A." in result["ac-1_smt.a"]
    assert "Only B." not in result["ac-1_smt.a"]
    assert result["ac-1_smt.b"] == "Only B."


def test_statement_text_map_strips_vault_metadata_from_all_narrative_paths():
    meta = "statement_part:: a\ninheritance_mode:: local\n"
    ssp = {
        "control-implementation": {
            "implemented-requirements": [
                {
                    "control-id": "ac-1",
                    "statements": [
                        {
                            "statement-id": "ac-1_smt.a",
                            "remarks": f"{meta}Statement remarks narrative.",
                        },
                        {
                            "statement-id": "ac-1_smt.b",
                            "by-components": [
                                {
                                    "description": f"Part b: {meta}Statement component narrative."
                                }
                            ],
                        },
                    ],
                    "by-components": [
                        {
                            "description": f"Part a: {meta}Requirement component narrative."
                        }
                    ],
                },
                {
                    "control-id": "ia-6",
                    "by-components": [
                        {"description": f"{meta}Whole-control narrative."}
                    ],
                },
            ]
        }
    }

    statements = statement_text_map_from_oscal_ssp(ssp)["ac-1"]
    whole_control = whole_control_text_map_from_oscal_ssp(ssp)

    assert (
        statements["ac-1_smt.a"]
        == "Requirement component narrative.\n\nStatement remarks narrative."
    )
    assert statements["ac-1_smt.b"] == "Part b:\nStatement component narrative."
    assert whole_control == {"ia-6": "Whole-control narrative."}
    assert "statement_part::" not in "\n".join(
        (*statements.values(), *whole_control.values())
    )


def test_vault_export_metadata_is_not_copied_into_docx_narratives():
    meta = (
        "statement_part:: a\ninheritance_mode:: local\n"
        "implementation_status:: implemented\n\n"
    )
    body = "Cloud.gov Customer Responsibility: Synthetic narrative."
    requirement = {
        "control-id": "ac-1",
        "by-components": [{"description": f"Part a: {meta}{body}"}],
        "statements": [{"statement-id": "ac-1_smt.a", "remarks": f"{meta}{body}"}],
    }
    result = statement_text_map_from_oscal_ssp(
        {"control-implementation": {"implemented-requirements": [requirement]}}
    )["ac-1"]["ac-1_smt.a"]
    assert "statement_part::" not in result
    assert "inheritance_mode::" not in result
    assert result.count("Synthetic narrative.") == 1
