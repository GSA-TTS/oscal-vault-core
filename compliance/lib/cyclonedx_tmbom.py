"""Normalize legacy threat-model fields into CycloneDX 2.0 TM-BOM shapes."""

from __future__ import annotations

import json
import re
from typing import Any


def _property(item: dict[str, Any], name: str, value: Any) -> None:
    properties = item.setdefault("properties", [])
    encoded = (
        json.dumps(value, separators=(",", ":"))
        if isinstance(value, (dict, list, bool, int, float))
        else str(value)
    )
    existing = next((prop for prop in properties if prop.get("name") == name), None)
    if existing is None:
        properties.append({"name": name, "value": encoded})
    else:
        existing["value"] = encoded


def _label(ref: str, fallback: str) -> str:
    words = re.sub(r"^(flow|boundary)-", "", ref).replace("-", " ").strip()
    return words.title() or fallback


def normalize_scenario_threats(document: dict[str, Any]) -> int:
    """Rewrite the legacy singular scenario ``threat`` into the canonical ``threats`` array.

    The TM-BOM importer reads only ``threats``; a scenario with just ``threat`` is stored as a
    custom threat with no link to its abstract threat. Mutates ``document``; returns the count.
    """
    changed = 0
    threats = document.get("threats")
    scenarios = threats.get("scenarios") if isinstance(threats, dict) else None
    for scenario in scenarios or []:
        singular = scenario.pop("threat", None)
        if singular is None:
            continue
        refs = list(scenario.get("threats") or [])
        if singular and singular not in refs:
            refs.insert(0, singular)
        scenario["threats"] = refs
        changed += 1
    return changed


def project_tmbom_topology(document: dict[str, Any]) -> None:
    """Convert removed editor extensions while preserving their semantics."""

    for blueprint in document.get("blueprints") or []:
        entities = {
            item.get("bom-ref"): item.get("name") or item.get("bom-ref")
            for section in ("zones", "assets", "dataStores")
            for item in blueprint.get(section) or []
            if item.get("bom-ref")
        }
        for zone in blueprint.get("zones") or []:
            trust_level = zone.pop("trustLevel", None)
            if trust_level is not None:
                _property(zone, "precogly:trust-level", trust_level)
            parent = zone.pop("parentZone", None)
            if parent:
                zone["parent"] = parent

        for index, boundary in enumerate(blueprint.get("boundaries") or [], start=1):
            boundary.setdefault("bom-ref", f"boundary-{index}")
            zone_names = [entities.get(ref, ref) for ref in boundary.get("zones") or []]
            boundary.setdefault("name", " / ".join(zone_names) + " Boundary")
            boundary.setdefault("type", "trust")

        for store in blueprint.get("dataStores") or []:
            if not store.get("type"):
                store["type"] = {
                    "name": "legacy-unspecified",
                    "description": "The source DFD does not classify this data store technology.",
                }

        for index, flow in enumerate(blueprint.get("flows") or [], start=1):
            source = entities.get(flow.get("source"), flow.get("source"))
            destination = entities.get(flow.get("destination"), flow.get("destination"))
            flow.setdefault("name", f"{source} to {destination}")
            for field, property_name in (
                ("authMechanism", "owasp:auth_mechanism"),
                ("crosses_trust_zone", "resp:crosses-trust-zone"),
                ("data_classification", "resp:data-classification"),
            ):
                value = flow.pop(field, None)
                if value is not None:
                    _property(flow, property_name, value)

    for control in document.get("controls") or []:
        # CycloneDX 2.0 carries this relation on threat.mitigations. The source
        # already emits that inverse link, so the legacy control-side duplicate
        # can be removed without losing the relationship.
        control.pop("mitigations", None)

    threats = document.get("threats") or {}
    for threat in threats.get("threats") or []:
        threat.pop("affectedFlows", None)
    threat_names = {
        threat.get("bom-ref"): threat.get("name")
        for threat in threats.get("threats") or []
        if threat.get("bom-ref")
    }
    for index, scenario in enumerate(threats.get("scenarios") or [], start=1):
        threat_ref = scenario.pop("threat", None)
        if threat_ref:
            scenario["threats"] = [threat_ref]
        else:
            threat_ref = next(iter(scenario.get("threats") or []), None)
        scenario.setdefault(
            "name",
            threat_names.get(threat_ref)
            or _label(scenario.get("bom-ref", ""), f"Scenario {index}"),
        )
        scenario.pop("affectedFlows", None)
        for field, property_name in (
            ("inherentSeverity", "resp:inherent-severity"),
            ("residualSeverity", "resp:residual-severity"),
        ):
            value = scenario.pop(field, None)
            if value is not None:
                _property(scenario, property_name, value)
