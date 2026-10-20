import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compliance.lib.oscal_source import (  # noqa: E402
    load_from_oscal_ssp,
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
