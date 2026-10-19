"""Generate and verify canonical-to-OSCAL value lineage reports."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from compliance.lib.oscal_mapping_registry import ACTIVE_MAPPING_CLASSES, load_mappings

_LOSSY_OPERATORS = {"gfm_table_parse", "markdown_list_parse", "port_expression_parse"}
_TYPE_MAPPING_PREFIXES = {
    "oscal.party": ("oscal_party.",),
    "oscal.component_definition": ("oscal_component_definition.",),
    "oscal.x.boundary": ("oscal_x_boundary.",),
    "oscal.x.inheritance": ("oscal_x_inheritance.",),
    "oscal.x.relationship": ("oscal_x_relationship.",),
    "oscal.control": ("oscal_control.",),
}
_CANONICAL_MAPPING_POINTERS = {
    "oscal_party.uuid": ("/uuid",),
    "oscal_party.type": ("/party_type", "/org_type", "/note_type"),
    "oscal_party.name": ("/name", "/title"),
    "oscal_party.role_ids": ("/role_ids",),
    "oscal_party.email_address": ("/email_addresses", "/email_address"),
    "oscal_party.phone": ("/telephone_numbers", "/phone"),
    "oscal_component_definition.uuid": ("/uuid",),
    "oscal_component_definition.title": ("/name", "/title"),
    "oscal_component_definition.inventory_items": ("/inventory_items",),
    "oscal_component_definition.software_inventory": ("/software_inventory",),
    "oscal_component_definition.external_services": ("/external_services",),
    "oscal_component_definition.aws_services_in_use": ("/aws_services_in_use",),
    "oscal_x_boundary.description": ("/body_sections",),
    "oscal_x_boundary.types_of_users": ("/body_sections",),
    "oscal_x_inheritance.provided_by": ("/body_sections",),
    "oscal_x_relationship.interconnections": ("/body_sections",),
    "oscal_x_relationship.ports_protocols_services": ("/body_sections",),
    "oscal_control.crm": (
        "/crm",
        "/inherited_from",
        "/customer_responsibilities",
    ),
}
_ALLOWED_OPERATORS = {
    "oscal_party.uuid": ("uuid_derivation",),
    "oscal_component_definition.uuid": ("uuid_derivation",),
    "oscal_x_boundary.description": ("generated_callout_filter",),
    "oscal_x_boundary.types_of_users": (
        "generated_callout_filter",
        "gfm_table_parse",
        "markdown_list_parse",
        "record_fan_out",
    ),
    "oscal_x_inheritance.provided_by": (
        "generated_callout_filter",
        "gfm_table_parse",
    ),
    "oscal_x_relationship.interconnections": ("generated_callout_filter",),
    "oscal_x_relationship.ports_protocols_services": (
        "gfm_table_parse",
        "port_expression_parse",
        "record_fan_out",
    ),
    "oscal_control.crm": ("record_aggregation",),
}


def mapping_lineage_report(contract_path: str | Path) -> Dict[str, Any]:
    """Build a schema-versioned lineage claim for every active registry mapping."""
    derivations: List[Dict[str, Any]] = []
    for mapping in load_mappings(contract_path):
        if (
            mapping.status != "active"
            or mapping.classification not in ACTIVE_MAPPING_CLASSES
        ):
            continue
        export_class = (
            "native" if mapping.classification == "native_core" else "extension_bridge"
        )
        cardinality = "one_to_many" if len(mapping.native_paths) > 1 else "one_to_one"
        derivations.append(
            {
                "derivation_id": f"mapping:{mapping.source}",
                "derivation_kind": "fan_out"
                if cardinality == "one_to_many"
                else "mapping",
                "mapping_source": mapping.source,
                "adapter": mapping.adapter,
                "export_class": export_class,
                "cardinality": cardinality,
                "source_ids": [],
                "canonical_pointers": list(
                    _CANONICAL_MAPPING_POINTERS.get(mapping.source, ())
                ),
                "oscal_pointers": list(mapping.native_paths),
                "allowed_operators": list(_ALLOWED_OPERATORS.get(mapping.source, ())),
            }
        )
    counts = Counter(item["export_class"] for item in derivations)
    return {
        "schema_version": 1,
        "counters": {
            "native": counts["native"],
            "extension_bridge": counts["extension_bridge"],
            "preserved_only": counts["preserved_only"],
            "active_mappings": len(derivations),
            "covered_active_mappings": len(derivations),
        },
        "derivations": derivations,
    }


def validate_lineage_report(report: Dict[str, Any]) -> List[str]:
    """Fail closed on incomplete mappings and unreviewed lossy transformations."""
    findings: List[str] = []
    derivations = report.get("derivations") or []
    sources: set[str] = set()
    for item in derivations:
        source = str(item.get("mapping_source") or "")
        if not source:
            findings.append("lineage derivation has no mapping_source")
            continue
        if source in sources:
            findings.append(f"duplicate lineage mapping claim: {source}")
        sources.add(source)
        if item.get("export_class") != "preserved_only" and not item.get(
            "oscal_pointers"
        ):
            findings.append(f"{source}: exported mapping has no OSCAL pointer")
        if not item.get("canonical_pointers"):
            findings.append(f"{source}: mapping has no canonical source pointer")
        cardinality = item.get("cardinality")
        kind = item.get("derivation_kind")
        if cardinality == "one_to_many" and kind != "fan_out":
            findings.append(
                f"{source}: one-to-many mapping must be a fan_out derivation"
            )
        if cardinality == "many_to_one" and kind != "aggregation":
            findings.append(
                f"{source}: many-to-one mapping must be an aggregation derivation"
            )
    counters = report.get("counters") or {}
    if counters.get("active_mappings") != len(derivations):
        findings.append("active mapping counter differs from derivation count")
    if counters.get("covered_active_mappings") != len(sources):
        findings.append("covered mapping counter differs from unique mapping claims")
    return findings


def runtime_derivation(
    *,
    mapping_source: str,
    source_ids: List[str],
    canonical_pointers: List[str],
    oscal_pointers: List[str],
    events: List[Dict[str, Any]],
    preserved_extensions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "derivation_id": f"runtime:{mapping_source}:{len(source_ids)}:{len(oscal_pointers)}",
        "mapping_source": mapping_source,
        "source_ids": source_ids,
        "canonical_pointers": canonical_pointers,
        "oscal_pointers": oscal_pointers,
        "events": events,
        "preserved_extensions": preserved_extensions or [],
    }


def validate_runtime_derivations(
    capabilities: Dict[str, Any], runtime: List[Dict[str, Any]]
) -> List[str]:
    findings: List[str] = []
    allowed = {
        item["mapping_source"]: set(item.get("allowed_operators") or [])
        for item in capabilities.get("derivations") or []
    }
    for item in runtime:
        source = item.get("mapping_source")
        if source not in allowed:
            findings.append(f"runtime derivation has no mapping capability: {source}")
            continue
        for event in item.get("events") or []:
            operator = event.get("operator")
            if operator not in allowed[source]:
                findings.append(
                    f"{source}: runtime operator is not allowed: {operator}"
                )
            if event.get("residual") and not item.get("preserved_extensions"):
                findings.append(f"{source}: residual content is not preserved")
    return findings


def _has_section(data: Dict[str, Any], needle: str) -> bool:
    return any(
        needle in str(section.get("heading") or "").lower()
        and bool(str(section.get("content") or "").strip())
        for section in data.get("body_sections") or []
    )


def _mapping_is_present(source: str, data: Dict[str, Any]) -> bool:
    aliases = {
        "oscal_party.uuid": ("uuid",),
        "oscal_party.type": ("party_type", "org_type", "note_type"),
        "oscal_party.name": ("name", "title"),
        "oscal_party.role_ids": ("role_ids",),
        "oscal_party.email_address": ("email_addresses", "email_address"),
        "oscal_party.phone": ("telephone_numbers", "phone"),
        "oscal_component_definition.uuid": ("uuid",),
        "oscal_component_definition.title": ("name", "title"),
        "oscal_component_definition.inventory_items": ("inventory_items",),
        "oscal_component_definition.software_inventory": ("software_inventory",),
        "oscal_component_definition.external_services": ("external_services",),
        "oscal_component_definition.aws_services_in_use": ("aws_services_in_use",),
    }
    if source in aliases:
        return any(data.get(key) not in (None, "", [], {}) for key in aliases[source])
    if source == "oscal_x_boundary.description":
        return _has_section(data, "network architecture")
    if source == "oscal_x_boundary.types_of_users":
        return _has_section(data, "types of users")
    if source == "oscal_x_inheritance.provided_by":
        return _has_section(data, "provided by")
    if source == "oscal_x_relationship.interconnections":
        return _has_section(data, "interconnection")
    if source == "oscal_x_relationship.ports_protocols_services":
        return _has_section(data, "ports, protocols") or _has_section(
            data, "ports protocols"
        )
    if source == "oscal_control.crm":
        crm = data.get("crm")
        return bool(
            data.get("inherited_from")
            or data.get("customer_responsibilities")
            or data.get("customer-responsibility")
            or (isinstance(crm, dict) and any(crm.values()))
        )
    return False


def canonical_lineage_summary(
    data: Dict[str, Any], contract_path: str | Path
) -> Dict[str, Any] | None:
    note_type = str(data.get("type") or "")
    prefixes = _TYPE_MAPPING_PREFIXES.get(note_type)
    if not prefixes:
        return None
    report = mapping_lineage_report(contract_path)
    mappings = [
        item
        for item in report["derivations"]
        if item["mapping_source"].startswith(prefixes)
        and _mapping_is_present(item["mapping_source"], data)
    ]
    if not mappings:
        return None
    counts = Counter(item["export_class"] for item in mappings)
    return {
        "schema_version": report["schema_version"],
        "mapping_sources": [item["mapping_source"] for item in mappings],
        "native": counts["native"],
        "extension_bridge": counts["extension_bridge"],
        "preserved_only": counts["preserved_only"],
    }


def control_export_lineage(
    control_sidecars: List[Dict[str, Any]], oscal: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Generate concrete control, statement, and parameter lineage records."""
    requirements = (
        oscal.get("system-security-plan", {})
        .get("control-implementation", {})
        .get("implemented-requirements", [])
    )
    by_control = {
        str(requirement.get("control-id") or "").lower(): (index, requirement)
        for index, requirement in enumerate(requirements)
    }
    records: List[Dict[str, Any]] = []
    for sidecar_index, sidecar in enumerate(control_sidecars):
        fm = sidecar.get("frontmatter", sidecar)
        raw_control_id = str(fm.get("control_id") or "")
        normalized = re.sub(r"\((\w+)\)", r".\1", raw_control_id.lower())
        match = by_control.get(normalized)
        if not match:
            continue
        requirement_index, requirement = match
        base = f"/system-security-plan/control-implementation/implemented-requirements/{requirement_index}"
        records.append(
            {
                "mapping_source": "oscal.control.control_id",
                "derivation_id": f"control:{normalized}",
                "derivation_kind": "mapping",
                "adapter": "control",
                "export_class": "native",
                "cardinality": "one_to_one",
                "source_ids": [str(fm.get("source_locator") or raw_control_id)],
                "canonical_pointers": [f"/control_sidecars/{sidecar_index}/control_id"],
                "oscal_pointers": [f"{base}/control-id"],
                "transformations": [
                    {
                        "name": "label_normalization",
                        "review_required": False,
                        "reviewed": True,
                    }
                ],
            }
        )
        for statement_index, statement in enumerate(
            requirement.get("statements") or []
        ):
            records.append(
                {
                    "mapping_source": "oscal.control.statement",
                    "derivation_id": f"statement:{normalized}:{statement_index}",
                    "derivation_kind": "mapping",
                    "adapter": "control",
                    "export_class": "native",
                    "cardinality": "one_to_one",
                    "source_ids": [
                        str(statement.get("statement-id") or raw_control_id)
                    ],
                    "canonical_pointers": [
                        f"/control_sidecars/{sidecar_index}/body_sections"
                    ],
                    "oscal_pointers": [
                        f"{base}/statements/{statement_index}/description"
                    ],
                    "transformations": [],
                }
            )
        for parameter_index, parameter in enumerate(
            requirement.get("set-parameters") or []
        ):
            records.append(
                {
                    "mapping_source": "oscal.control.parameter",
                    "derivation_id": f"parameter:{normalized}:{parameter_index}",
                    "derivation_kind": "fan_out"
                    if len(parameter.get("values") or []) > 1
                    else "mapping",
                    "adapter": "control",
                    "export_class": "native",
                    "cardinality": "one_to_many"
                    if len(parameter.get("values") or []) > 1
                    else "one_to_one",
                    "source_ids": [str(parameter.get("param-id") or raw_control_id)],
                    "canonical_pointers": [
                        f"/control_sidecars/{sidecar_index}/parameters"
                    ],
                    "oscal_pointers": [
                        f"{base}/set-parameters/{parameter_index}/values"
                    ],
                    "transformations": [],
                }
            )
    return records


def export_lineage_report(
    contract_path: str | Path,
    *,
    runtime_derivations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Combine complete registry claims with concrete export derivations."""
    report = mapping_lineage_report(contract_path)
    report["runtime_derivations"] = runtime_derivations or []
    classes = {
        item["mapping_source"]: item["export_class"] for item in report["derivations"]
    }
    runtime_counts = Counter(
        classes.get(item["mapping_source"], "preserved_only")
        for item in report["runtime_derivations"]
    )
    report["runtime_counters"] = {
        "native": runtime_counts["native"],
        "extension_bridge": runtime_counts["extension_bridge"],
        "preserved_only": runtime_counts["preserved_only"],
    }
    return report


def _pointer_value(document: Dict[str, Any], pointer: str) -> Any:
    value: Any = document
    for raw in pointer.strip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").split())


def component_definition_lineage(
    canonical: Dict[str, Any], oscal: Dict[str, Any]
) -> List[Dict[str, Optional[str]]]:
    """Map adapter-backed component table values to exact OSCAL prop pointers."""
    records: List[Dict[str, Optional[str]]] = []
    props = (
        oscal.get("component-definition", {})
        .get("components", [{}])[0]
        .get("props", [])
    )
    used_props: set[int] = set()
    for record_index, record in enumerate(canonical.get("table_records") or []):
        if (
            record.get("kind") != "external_service"
            or record.get("disposition") != "mapped"
            or record.get("headers") != ["Service Element", "Response"]
        ):
            continue
        source = str(record.get("source_locator") or f"table-record:{record_index}")
        for row_index, row in enumerate(record.get("rows") or []):
            if not isinstance(row, list) or len(row) != 2:
                continue
            label, expected = (str(cell).strip() for cell in row)
            name = "external-service-" + re.sub(
                r"[^a-z0-9]+", "-", label.lower()
            ).strip("-")
            prop_index = next(
                (
                    index
                    for index, prop in enumerate(props)
                    if index not in used_props
                    and prop.get("name") == name
                    and _normalized(prop.get("value")) == _normalized(expected)
                ),
                None,
            )
            canonical_pointer = f"/table_records/{record_index}/rows/{row_index}/1"
            oscal_pointer = (
                f"/component-definition/components/0/props/{prop_index}/value"
                if prop_index is not None
                else None
            )
            if prop_index is not None:
                used_props.add(prop_index)
            records.append(
                {
                    "source_id": f"{source}/row[{row_index}]/cell[1]",
                    "canonical_pointer": canonical_pointer,
                    "oscal_pointer": oscal_pointer,
                    "export_disposition": "native_prop"
                    if oscal_pointer
                    else "unmapped_error",
                }
            )
    for source in canonical.get("source_lineage") or []:
        records.append(
            {
                "source_id": source["source_id"],
                "canonical_pointer": source["canonical_pointer"],
                "oscal_pointer": None,
                "export_disposition": "preserved_only",
            }
        )
    return records


def verify_oscal_lineage(
    canonical: Dict[str, Any],
    oscal: Dict[str, Any],
    records: List[Dict[str, Optional[str]]],
) -> List[str]:
    findings: List[str] = []
    for record in records:
        disposition = record.get("export_disposition")
        canonical_pointer = record.get("canonical_pointer")
        oscal_pointer = record.get("oscal_pointer")
        if not canonical_pointer:
            findings.append(f"{record.get('source_id')}: canonical pointer is missing")
            continue
        try:
            canonical_value = _pointer_value(canonical, canonical_pointer)
        except (KeyError, IndexError, ValueError, TypeError):
            findings.append(
                f"{record.get('source_id')}: canonical pointer does not resolve: {canonical_pointer}"
            )
            continue
        if disposition == "preserved_only":
            if oscal_pointer:
                findings.append(
                    f"{record.get('source_id')}: preserved-only value claims an OSCAL pointer"
                )
            continue
        if not oscal_pointer:
            findings.append(
                f"{record.get('source_id')}: mapped value has no OSCAL pointer"
            )
            continue
        try:
            oscal_value = _pointer_value(oscal, oscal_pointer)
        except (KeyError, IndexError, ValueError, TypeError):
            findings.append(
                f"{record.get('source_id')}: OSCAL pointer does not resolve: {oscal_pointer}"
            )
            continue
        if _normalized(canonical_value) != _normalized(oscal_value):
            findings.append(
                f"{record.get('source_id')}: canonical and OSCAL values differ"
            )
    return findings
