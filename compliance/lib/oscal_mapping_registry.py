"""Validate declarative OSCAL export mappings against schemas and adapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

MAPPING_CLASSES = frozenset(
    {"native_core", "authoring_extension", "true_extension", "unimplemented"}
)
MAPPING_STATUSES = frozenset({"active", "documented"})
ACTIVE_MAPPING_CLASSES = frozenset({"native_core", "authoring_extension"})
_INDEX_RE = re.compile(r"\[[^\]]*\]")
_SCHEMA_FILES = {
    "component-definition": "oscal_component_schema.json",
    "system-security-plan": "oscal_ssp_schema.json",
    "system-characteristics": "oscal_ssp_schema.json",
    "system-implementation": "oscal_ssp_schema.json",
    "control-implementation": "oscal_ssp_schema.json",
    "back-matter": "oscal_ssp_schema.json",
}


class MappingValidationError(ValueError):
    """The contract contains an invalid or unclaimed active mapping."""


@dataclass(frozen=True)
class Mapping:
    """One contract property and its intended OSCAL targets."""

    source: str
    classification: str
    status: str
    native_paths: tuple[str, ...]
    adapter: str | None = None
    legacy_extension_path: str | None = None
    authoring_extension_type: str | None = None


def normalize_oscal_path(path: str) -> str:
    """Normalize concrete and symbolic array selectors to schema field paths."""
    return _INDEX_RE.sub("", path.strip())


def _properties(values: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {
        item["property"]: item.get("value")
        for item in values
        if isinstance(item, dict) and item.get("property")
    }


def load_mappings(contract_path: str | Path) -> list[Mapping]:
    """Load and fail closed on incomplete mapping metadata in an ODCS contract."""
    raw = yaml.safe_load(Path(contract_path).read_text(encoding="utf-8"))
    mappings: list[Mapping] = []
    errors: list[str] = []
    for obj in raw.get("schema", []):
        object_props = _properties(obj.get("customProperties", []))
        object_class = object_props.get("mappingClass")
        object_status = object_props.get("mappingStatus")
        object_adapter = object_props.get("exportAdapter")
        authoring_type = object_props.get("authoringExtensionType")
        object_active = (
            object_class in ACTIVE_MAPPING_CLASSES or object_status == "active"
        )
        for prop in obj.get("properties", []):
            prop_props = _properties(prop.get("customProperties", []))
            classification = prop_props.get("mappingClass", object_class)
            status = prop_props.get("mappingStatus", object_status)
            paths = prop_props.get("nativeOscalPaths", prop_props.get("oscalPath"))
            adapter = prop_props.get("exportAdapter", object_adapter)
            source = f"{obj.get('id', obj.get('name'))}.{prop.get('name')}"
            has_mapping_metadata = any(
                key in prop_props
                for key in (
                    "mappingClass",
                    "mappingStatus",
                    "nativeOscalPaths",
                    "exportAdapter",
                    "legacyExtensionPath",
                )
            )
            if object_class is None and not has_mapping_metadata:
                continue
            if not has_mapping_metadata and not object_active and paths is None:
                continue
            if classification is None:
                errors.append(f"{source}: missing mappingClass")
                continue
            if status is None:
                errors.append(f"{source}: missing mappingStatus")
                continue
            if isinstance(paths, str):
                paths = [paths]
            if paths is None:
                paths = []
            if not isinstance(paths, list) or any(
                not isinstance(path, str) for path in paths
            ):
                errors.append(
                    f"{source}: native OSCAL path must be a string or list of strings"
                )
                continue
            mapping = Mapping(
                source=source,
                classification=str(classification),
                status=str(status),
                native_paths=tuple(path.strip() for path in paths if path.strip()),
                adapter=str(adapter) if adapter else None,
                legacy_extension_path=prop_props.get("legacyExtensionPath"),
                authoring_extension_type=prop_props.get(
                    "authoringExtensionType", authoring_type
                ),
            )
            if mapping.classification not in MAPPING_CLASSES:
                errors.append(
                    f"{source}: unknown mappingClass {mapping.classification!r}"
                )
            if mapping.status not in MAPPING_STATUSES:
                errors.append(f"{source}: unknown mappingStatus {mapping.status!r}")
            if (
                mapping.status == "active"
                and mapping.classification not in ACTIVE_MAPPING_CLASSES
            ):
                errors.append(
                    f"{source}: active status requires native_core or authoring_extension"
                )
            if (
                mapping.classification in ACTIVE_MAPPING_CLASSES
                and mapping.status == "active"
            ):
                if not mapping.native_paths:
                    errors.append(
                        f"{source}: active native mapping has no native OSCAL path"
                    )
                if not mapping.adapter:
                    errors.append(
                        f"{source}: active native mapping has no exportAdapter"
                    )
            mappings.append(mapping)
    if errors:
        raise MappingValidationError("; ".join(errors))
    return mappings


def _resolve_ref(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise MappingValidationError(
                f"unsupported external OSCAL schema reference: {ref}"
            )
        resolved: Any = schema
        for token in ref[2:].split("/"):
            resolved = resolved[token.replace("~1", "/").replace("~0", "~")]
        node = resolved
    return node


def validate_schema_path(path: str, schema_dir: str | Path) -> None:
    """Validate a complete normalized mapping path against OSCAL 1.1.3 schemas."""
    normalized = normalize_oscal_path(path)
    parts = normalized.split(".")
    if not normalized or any(not part for part in parts):
        raise MappingValidationError(f"malformed OSCAL path: {path!r}")
    schema_file = _SCHEMA_FILES.get(parts[0])
    if schema_file is None:
        raise MappingValidationError(f"unknown OSCAL path root: {path}")
    schema = json.loads((Path(schema_dir) / schema_file).read_text(encoding="utf-8"))
    if parts[0] not in {"system-security-plan", "component-definition"}:
        parts.insert(0, "system-security-plan")
    node: dict[str, Any] = schema
    for part in parts:
        node = _resolve_ref(schema, node)
        if node.get("type") == "array":
            node = _resolve_ref(schema, node["items"])
        properties = node.get("properties", {})
        if part not in properties:
            consumed = ".".join(parts[: parts.index(part)]) or "<root>"
            raise MappingValidationError(
                f"unknown OSCAL path {path!r}: {part!r} is not valid after {consumed}"
            )
        node = properties[part]


def validate_mapping_claims(
    contract_path: str | Path,
    adapter_claims: dict[str, dict[str, Iterable[str]]],
    *,
    schema_dir: str | Path | None = None,
) -> list[Mapping]:
    """Validate active paths against exact adapter/property claims and schemas."""
    mappings = load_mappings(contract_path)
    if schema_dir is None:
        schema_dir = Path(__file__).resolve().parents[1] / "schemas/oscal-1.1.3"
    errors: list[str] = []
    for mapping in mappings:
        if (
            mapping.classification not in ACTIVE_MAPPING_CLASSES
            or mapping.status != "active"
        ):
            continue
        for path in mapping.native_paths:
            try:
                validate_schema_path(path, schema_dir)
            except MappingValidationError as exc:
                errors.append(f"{mapping.source}: {exc}")
        claims = adapter_claims.get(mapping.adapter or "")
        claimed_paths = tuple(claims.get(mapping.source, ())) if claims else ()
        if tuple(map(normalize_oscal_path, mapping.native_paths)) != tuple(
            map(normalize_oscal_path, claimed_paths)
        ):
            errors.append(
                f"{mapping.source}: adapter {mapping.adapter!r} claims "
                f"{claimed_paths or '<nothing>'}, contract declares "
                f"{mapping.native_paths or '<nothing>'}"
            )
    if errors:
        raise MappingValidationError("; ".join(errors))
    return mappings
