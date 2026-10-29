"""Tests for Fix #26 / R26: real component inventory wired into OSCAL export.

Verifies that ``canonical_to_oscal_component_definition()`` surfaces
``inventory_items``, ``software_inventory``, ``external_services``, and
``aws_services_in_use`` frontmatter fields as discrete OSCAL ``props[]`` on
the exported component — matching the prop names declared in the contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.export_oscal import canonical_to_oscal_component_definition  # noqa: E402


def _component_props(result: dict) -> list[dict]:
    """Unwrap the first component's props list from a component-definition result."""
    return result["component-definition"]["components"][0].get("props", [])


def _prop_names(props: list[dict]) -> list[str]:
    return [p["name"] for p in props]


def test_inventory_items_become_props():
    """inventory_items list is emitted as one prop per item, name=inventory-item."""
    canonical = {
        "frontmatter": {
            "name": "TestSystem",
            "component_type": "system",
            "inventory_items": ["Server A", "Server B", "Server C"],
        }
    }
    result = canonical_to_oscal_component_definition(canonical)
    props = _component_props(result)
    inv_props = [p for p in props if p["name"] == "inventory-item"]
    assert len(inv_props) == 3, f"Expected 3 inventory-item props, got {inv_props}"
    values = {p["value"] for p in inv_props}
    assert values == {"Server A", "Server B", "Server C"}


def test_software_and_external_service_props():
    """software_inventory and external_services each produce the right prop names."""
    canonical = {
        "frontmatter": {
            "name": "MyApp",
            "software_inventory": ["Python 3.11", "nginx 1.24"],
            "external_services": ["login.gov", "api.data.gov"],
        }
    }
    result = canonical_to_oscal_component_definition(canonical)
    props = _component_props(result)
    names = _prop_names(props)
    assert "software-inventory" in names
    assert "external-service" in names
    sw_vals = {p["value"] for p in props if p["name"] == "software-inventory"}
    assert sw_vals == {"Python 3.11", "nginx 1.24"}
    service_vals = {p["value"] for p in props if p["name"] == "external-service"}
    assert service_vals == {"login.gov", "api.data.gov"}
    assert "capabilities" not in result["component-definition"]


def test_external_service_table_values_become_discrete_props():
    canonical = {
        "frontmatter": {"name": "MyApp"},
        "table_records": [
            {
                "headers": ["Service Element", "Response"],
                "rows": [["Name", "login.gov"], ["Authorization", "FedRAMP"]],
                "kind": "external_service",
                "disposition": "mapped",
            }
        ],
    }

    props = _component_props(canonical_to_oscal_component_definition(canonical))

    assert {
        (prop["name"], prop["value"])
        for prop in props
        if prop["name"].startswith("external-service-")
    } == {
        ("external-service-name", "login.gov"),
        ("external-service-authorization", "FedRAMP"),
    }
    assert all("table_records" not in prop["value"] for prop in props)


def test_aws_services_prop():
    """aws_services_in_use is emitted as aws-service props."""
    canonical = {
        "frontmatter": {
            "name": "CloudSys",
            "aws_services_in_use": ["S3", "RDS"],
        }
    }
    result = canonical_to_oscal_component_definition(canonical)
    props = _component_props(result)
    aws_props = [p for p in props if p["name"] == "aws-service"]
    assert len(aws_props) == 2
    assert {p["value"] for p in aws_props} == {"S3", "RDS"}


def test_no_inventory_no_extra_props():
    """When no inventory fields are present, no inventory props are emitted."""
    canonical = {
        "frontmatter": {
            "name": "Minimal",
            "status": "operational",
        }
    }
    result = canonical_to_oscal_component_definition(canonical)
    props = _component_props(result)
    inv_names = {
        "inventory-item",
        "software-inventory",
        "external-service",
        "aws-service",
    }
    extra = [p for p in props if p["name"] in inv_names]
    assert extra == [], f"Unexpected inventory props on minimal component: {extra}"
