import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FISMATIC_SRC = _REPO_ROOT / "fismatic" / "src" / "phase_1"
if str(_FISMATIC_SRC) not in sys.path:
    sys.path.insert(0, str(_FISMATIC_SRC))

from compliance.lib.export_oscal import (  # noqa: E402
    _oscal_set_parameters,
    control_to_oscal_implemented_requirement,
)
from fismatic.parser import sanitize_responsible_role  # noqa: E402


def test_sanitize_responsible_role():
    # Multiline text with colons should extract clean short title
    raw = "Information System Security Officer (ISSO):\nThe ISSO is responsible for monitoring..."
    assert (
        sanitize_responsible_role(raw) == "Information System Security Officer (ISSO)"
    )

    # Clean short text returns as is
    assert (
        sanitize_responsible_role("Cloud Service Provider") == "Cloud Service Provider"
    )

    # Empty / whitespace returns None
    assert sanitize_responsible_role("   ") is None


def test_oscal_set_parameters_mapping():
    # Supports both scalar value and values array
    raw = [
        {"param_id": "ac-1_prm_1", "value": "at least annually"},
        {
            "param_id": "ac-1_prm_2",
            "values": ["30 days", "quarterly"],
            "remarks": "Policy review frequency",
        },
    ]
    out = _oscal_set_parameters(raw)
    assert len(out) == 2
    assert out[0] == {"param-id": "ac-1_prm_1", "values": ["at least annually"]}
    assert out[1] == {
        "param-id": "ac-1_prm_2",
        "values": ["30 days", "quarterly"],
        "remarks": "Policy review frequency",
    }


def test_control_to_oscal_with_by_components_and_set_parameters():
    canonical_data = {
        "frontmatter": {
            "control_id": "AC-1",
            "implementation_status": "implemented",
            "set_parameters": [{"param_id": "ac-1_prm_1", "values": ["annually"]}],
            "by_components": [
                {
                    "component_uuid": "795533ab-9427-4abe-820f-0b571bacfe6d",
                    "description": "Enterprise Logging Policy implements AC-1.",
                    "implementation_status": {"state": "implemented"},
                }
            ],
        },
        "body_sections": [],
    }

    req = control_to_oscal_implemented_requirement(canonical_data)
    assert req["control-id"] == "ac-1"
    assert "set-parameters" in req
    assert req["set-parameters"][0]["param-id"] == "ac-1_prm_1"
    assert "by-components" in req
    assert len(req["by-components"]) == 1
    assert (
        req["by-components"][0]["component-uuid"]
        == "795533ab-9427-4abe-820f-0b571bacfe6d"
    )
    assert (
        req["by-components"][0]["description"]
        == "Enterprise Logging Policy implements AC-1."
    )
