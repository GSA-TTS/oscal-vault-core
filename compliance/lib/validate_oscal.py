"""Layer 1 OSCAL conformance validation: validate exported OSCAL documents
against the official NIST OSCAL JSON Schema (ADR-019, phase R17).

This is the *primary*, fast, offline conformance gate. It runs entirely from
vendored NIST schemas under ``compliance/schemas/oscal-<version>/`` with zero
network / Docker / database dependency. The (optional, heavier) CCF relational
round-trip oracle is Layer 2 and lives elsewhere.

The NIST OSCAL schemas are JSON Schema **draft-07** and are keyed by a
top-level wrapper object (``{"system-security-plan": {...}}``,
``{"component-definition": {...}}``, ...), which is exactly the shape
``compliance.lib.export_oscal`` emits and the shape the compliance-framework
API ingests. So the wrapper key doubles as the document-type discriminator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from jsonschema import Draft7Validator

# Default OSCAL version this project targets (ADR-019). Kept as a module
# constant rather than hardcoded at call sites so the R17 version-agility
# follow-up (issue #13) has a single seam to parameterize.
DEFAULT_OSCAL_VERSION = "1.1.3"

# OSCAL schema versions this project has vendored under compliance/schemas/.
# Adding a version = drop its schema set in compliance/schemas/oscal-<v>/ and
# list it here (the loader resolves files by this string).
SUPPORTED_OSCAL_VERSIONS = ("1.0.4", "1.1.3")

_SCHEMAS_ROOT = Path(__file__).resolve().parents[1] / "schemas"

# Map the top-level OSCAL wrapper key -> vendored NIST schema filename.
# Only the models this project actually exports are wired; unknown wrappers
# fail closed with a clear error rather than silently passing.
_WRAPPER_TO_SCHEMA: Dict[str, str] = {
    "system-security-plan": "oscal_ssp_schema.json",
    "component-definition": "oscal_component_schema.json",
    "plan-of-action-and-milestones": "oscal_poam_schema.json",
    "assessment-results": "oscal_assessment-results_schema.json",
}

# Document types the post-schema semantic checks understand. The reference
# graph walk is specific to SSP/component-definition layouts, so the other
# wired types are schema-validated only and fail closed under ``semantic=True``.
_SEMANTIC_DOCUMENT_TYPES = frozenset({"system-security-plan", "component-definition"})


class SchemaNotAvailableError(FileNotFoundError):
    """A vendored NIST schema for the requested model/version is missing."""


@dataclass
class ValidationResult:
    """Outcome of validating one OSCAL document against its NIST schema."""

    valid: bool
    document_type: str  # the top-level wrapper key, e.g. "system-security-plan"
    oscal_version: str
    errors: List[str] = field(default_factory=list)
    source: Optional[str] = None  # path/label of the validated document

    def __bool__(self) -> bool:  # allow `if result:`
        return self.valid


def available_document_types() -> List[str]:
    """OSCAL wrapper keys this validator can check (i.e. have a vendored schema)."""
    return sorted(_WRAPPER_TO_SCHEMA)


def _schema_path(document_type: str, oscal_version: str) -> Path:
    filename = _WRAPPER_TO_SCHEMA.get(document_type)
    if filename is None:
        raise SchemaNotAvailableError(
            f"No NIST schema wired for OSCAL document type {document_type!r}. "
            f"Known: {available_document_types()}"
        )
    path = _SCHEMAS_ROOT / f"oscal-{oscal_version}" / filename
    if not path.is_file():
        raise SchemaNotAvailableError(
            f"Vendored NIST schema not found: {path}. "
            f"(document_type={document_type}, oscal_version={oscal_version})"
        )
    return path


@lru_cache(maxsize=None)
def _load_validator(document_type: str, oscal_version: str) -> Draft7Validator:
    """Load and cache a Draft7Validator for a given model + OSCAL version.

    The NIST schemas are self-contained (all ``$ref``s resolve within a single
    file's ``definitions``), so no external ``referencing.Registry`` wiring is
    needed — unlike this repo's frontmatter validator, whose sub-schemas share
    one root.

    Note: we intentionally do NOT call ``Draft7Validator.check_schema`` here.
    The NIST OSCAL schemas embed ECMA-262 ``pattern`` values that use Unicode
    property escapes (e.g. ``\\p{L}``), which Python's ``re`` engine cannot
    compile. ``check_schema`` runs a ``format: regex`` check over the schema's
    own patterns and would raise on those. We also rewrite those patterns to
    Python-compatible equivalents on load (see ``_sanitize_patterns``) so a
    document validation does not blow up mid-walk when jsonschema tries to
    ``re.search`` them. We do not attach a FormatChecker, so ``format``
    constraints (uuid, date-time, ...) are not enforced at Layer 1 — an
    acceptable, documented limitation (Layer 2 / CCF's go-oscal catches what a
    pure JSON-Schema pass cannot).
    """
    with open(_schema_path(document_type, oscal_version), encoding="utf-8") as f:
        schema = json.load(f)
    _sanitize_patterns(schema)
    return Draft7Validator(schema)


# ECMA-262 Unicode property escapes used by the NIST OSCAL schemas that
# Python's ``re`` cannot compile, mapped to equivalent Python character classes.
# ``\p{L}`` (any letter) and ``\p{N}`` (any number) → Unicode-aware classes.
_ECMA_UNICODE_CLASS_REWRITES = {
    r"\p{L}": r"[^\W\d_]",  # letters (Unicode): word chars minus digits/underscore
    r"\p{N}": r"\d",  # numbers
}


def _sanitize_patterns(node: Any) -> None:
    """Rewrite ECMA-262 Unicode-property ``pattern`` escapes in place.

    NIST OSCAL schemas use ``\\p{L}`` / ``\\p{N}`` in a handful of ``pattern``
    constraints (e.g. the NCName/Token datatypes). Python's ``re`` raises
    ``PatternError: bad escape \\p`` on these, which would abort validation of
    an otherwise-valid document. We translate them to Python-compatible
    equivalents so the pattern still constrains meaningfully. This is a
    documented Layer-1 fidelity trade-off, not a correctness gap for the
    structural checks we rely on.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pattern" and isinstance(value, str):
                for ecma, py in _ECMA_UNICODE_CLASS_REWRITES.items():
                    value = value.replace(ecma, py)
                node[key] = value
            else:
                _sanitize_patterns(value)
    elif isinstance(node, list):
        for item in node:
            _sanitize_patterns(item)


def detect_document_type(document: Dict[str, Any]) -> Optional[str]:
    """Return the OSCAL wrapper key present in ``document``, or None.

    A well-formed OSCAL document has exactly one top-level wrapper key
    (``system-security-plan``, ``component-definition``, ...). ``$schema`` is
    permitted alongside it and ignored for discrimination.
    """
    keys = [k for k in document if k != "$schema"]
    if len(keys) == 1 and keys[0] in _WRAPPER_TO_SCHEMA:
        return keys[0]
    # Fall back to any recognized wrapper if multiple keys are present.
    for k in keys:
        if k in _WRAPPER_TO_SCHEMA:
            return k
    return None


def validate_oscal_document(
    document: Dict[str, Any],
    *,
    document_type: Optional[str] = None,
    oscal_version: str = DEFAULT_OSCAL_VERSION,
    source: Optional[str] = None,
    semantic: bool = False,
    allow_synthetic: bool = False,
) -> ValidationResult:
    """Validate an in-memory OSCAL document against its vendored NIST schema.

    Args:
        document: the parsed OSCAL JSON (wrapper-keyed, as emitted by
            ``export_oscal`` / ingested by CCF).
        document_type: force a wrapper key; if None it is auto-detected.
        oscal_version: which vendored schema set to validate against.
        source: optional label (e.g. file path) echoed back in the result.
        semantic: also run the post-schema reference/uniqueness checks. Schema
            validation remains a prerequisite, not a substitute: semantics are
            only evaluated once the document is structurally valid.
        allow_synthetic: permit documented synthetic placeholder objects. A
            production document must not contain them.

    Returns a ``ValidationResult``; it never raises on *schema-invalid input*
    (that is reported via ``result.errors``). It does raise
    ``SchemaNotAvailableError`` if the requested schema is not vendored, and
    ``ValueError`` if the document type cannot be determined — both are
    fail-closed conditions, not validation failures to swallow.
    """
    dtype = document_type or detect_document_type(document)
    if dtype is None:
        raise ValueError(
            "Could not determine OSCAL document type: expected exactly one "
            f"recognized top-level wrapper key from {available_document_types()}, "
            f"got keys {sorted(k for k in document if k != '$schema')}"
        )

    validator = _load_validator(dtype, oscal_version)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    messages = [_format_error(e) for e in errors]
    if semantic and not messages:
        messages.extend(
            validate_oscal_semantics(
                document, document_type=dtype, allow_synthetic=allow_synthetic
            )
        )
    return ValidationResult(
        valid=not messages,
        document_type=dtype,
        oscal_version=oscal_version,
        errors=messages,
        source=source,
    )


def validate_oscal_file(
    path: Path,
    *,
    oscal_version: str = DEFAULT_OSCAL_VERSION,
    semantic: bool = False,
    allow_synthetic: bool = False,
) -> ValidationResult:
    """Load a ``.json`` OSCAL export from disk and validate it."""
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        document = json.load(f)
    return validate_oscal_document(
        document,
        oscal_version=oscal_version,
        source=str(path),
        semantic=semantic,
        allow_synthetic=allow_synthetic,
    )


# ---------------------------------------------------------------------------
# Post-schema semantic validation
#
# JSON Schema proves document *shape*: that a ``component-uuid`` is a UUID, not
# that it names a component that exists. Everything below checks the reference
# graph and identifier uniqueness that the schema cannot express.
# ---------------------------------------------------------------------------


def _iter_uuids(node: Any, pointer: str = "") -> List[tuple[str, str]]:
    """Yield every ``(json_pointer, uuid)`` pair in the document.

    Walking the whole tree — rather than a hand-picked list of entities — is
    what makes the uniqueness check exhaustive: a UUID added to a nested
    assembly (information types, inventory items, locations, ...) is covered
    without the validator being updated.
    """
    found: List[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{pointer}/{key}"
            if key == "uuid" and isinstance(value, str):
                found.append((pointer or "/", value))
            else:
                found.extend(_iter_uuids(value, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_iter_uuids(item, f"{pointer}/{index}"))
    return found


def _iter_props(node: Any, pointer: str = "") -> List[tuple[str, Dict[str, Any]]]:
    """Yield every ``(json_pointer, prop)`` in the document.

    Each property is yielded separately rather than collapsed into a mapping,
    because OSCAL permits repeated property names: folding them into a dict
    lets a later ``synthetic: false`` mask an earlier ``synthetic: true``.
    """
    found: List[tuple[str, Dict[str, Any]]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{pointer}/{key}"
            if key == "props" and isinstance(value, list):
                for index, prop in enumerate(value):
                    if isinstance(prop, dict):
                        found.append((f"{child}/{index}", prop))
            else:
                found.extend(_iter_props(value, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_iter_props(item, f"{pointer}/{index}"))
    return found


def _iter_keyed_references(
    node: Any, keys: tuple[str, ...], pointer: str = ""
) -> List[tuple[str, str, str]]:
    """Yield ``(json_pointer, key, value)`` for every occurrence of ``keys``.

    Reference keys such as ``component-uuid``, ``party-uuids`` and ``role-id``
    appear at many depths (requirement, statement, by-component, inventory
    item, ...). Collecting them structurally means a reference nested somewhere
    new is still checked.
    """
    found: List[tuple[str, str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{pointer}/{key}"
            if key in keys:
                if isinstance(value, str):
                    found.append((child, key, value))
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        if isinstance(item, str):
                            found.append((f"{child}/{index}", key, item))
            else:
                found.extend(_iter_keyed_references(value, keys, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_iter_keyed_references(item, keys, f"{pointer}/{index}"))
    return found


def validate_oscal_semantics(
    document: Dict[str, Any],
    *,
    document_type: Optional[str] = None,
    allow_synthetic: bool = False,
) -> List[str]:
    """Validate internal OSCAL references and identifier uniqueness.

    Checks, for both ``system-security-plan`` and ``component-definition``:

    * every UUID in the document is unique, wherever it appears;
    * every ``component-uuid`` resolves to a declared component;
    * every ``party-uuid`` resolves to ``metadata.parties[]``;
    * every ``role-id`` resolves to ``metadata.roles[]``;
    * ``metadata.roles[].id`` and ``metadata.parties[].uuid`` are unique;
    * ``control-id`` values are unique and ``statement-id`` /
      ``set-parameters[].param-id`` values are unique and scoped to their
      control;
    * no object is marked ``synthetic`` unless ``allow_synthetic`` is set.

    Scope: this validates the graph *within* one emitted document. Resolving
    ``control-id`` against an external catalog/profile requires fetching that
    profile and is out of scope here; cross-stage field parity is covered by
    :func:`verify_oscal_parity`.
    """
    dtype = document_type or detect_document_type(document)
    if dtype not in _SEMANTIC_DOCUMENT_TYPES:
        return [f"semantic: unsupported document type {dtype!r}"]
    root = document[dtype]
    errors: List[str] = []

    # --- UUID uniqueness, document-wide ------------------------------------
    seen_uuid: Dict[str, str] = {}
    for pointer, value in _iter_uuids(root, f"/{dtype}"):
        if value in seen_uuid:
            errors.append(
                f"semantic: duplicate uuid {value!r} at {pointer} "
                f"(first seen at {seen_uuid[value]})"
            )
        else:
            seen_uuid[value] = pointer

    metadata = root.get("metadata") or {}

    # --- declared identifier sets ------------------------------------------
    party_uuids: set[str] = set()
    for index, party in enumerate(metadata.get("parties") or []):
        uuid_value = party.get("uuid")
        if isinstance(uuid_value, str):
            party_uuids.add(uuid_value)
        else:
            errors.append(f"semantic: metadata.parties[{index}] has no uuid")

    role_ids: set[str] = set()
    for index, role in enumerate(metadata.get("roles") or []):
        role_id = role.get("id")
        if not isinstance(role_id, str) or not role_id:
            errors.append(f"semantic: metadata.roles[{index}] has no id")
            continue
        if role_id in role_ids:
            errors.append(f"semantic: duplicate metadata role id {role_id!r}")
        role_ids.add(role_id)

    component_uuids: set[str] = set()
    if dtype == "system-security-plan":
        components = (root.get("system-implementation") or {}).get("components") or []
    else:
        components = root.get("components") or []
    for index, component in enumerate(components):
        uuid_value = component.get("uuid")
        if isinstance(uuid_value, str):
            component_uuids.add(uuid_value)
        else:
            errors.append(f"semantic: components[{index}] has no uuid")
    # A component-definition may also declare capabilities that are referenced
    # the same way.
    for capability in root.get("capabilities") or []:
        uuid_value = capability.get("uuid")
        if isinstance(uuid_value, str):
            component_uuids.add(uuid_value)

    # --- reference resolution ----------------------------------------------
    for pointer, key, value in _iter_keyed_references(
        root,
        (
            "component-uuid",
            "party-uuids",
            "role-id",
            "location-uuid",
            "member-of-organizations",
        ),
        f"/{dtype}",
    ):
        if key == "component-uuid":
            if value not in component_uuids:
                errors.append(
                    f"semantic: unresolved component-uuid {value!r} at {pointer}"
                )
        elif key in {"party-uuids", "member-of-organizations"}:
            if value not in party_uuids:
                errors.append(f"semantic: unresolved party-uuid {value!r} at {pointer}")
        elif key == "role-id":
            if value not in role_ids:
                errors.append(f"semantic: unresolved role-id {value!r} at {pointer}")
        elif key == "location-uuid":
            location_uuids = {
                loc.get("uuid")
                for loc in (metadata.get("locations") or [])
                if isinstance(loc.get("uuid"), str)
            }
            if value not in location_uuids:
                errors.append(
                    f"semantic: unresolved location-uuid {value!r} at {pointer}"
                )

    # --- control / statement / parameter identifiers ------------------------
    errors.extend(_validate_control_identifiers(root, dtype))

    # --- synthetic production objects --------------------------------------
    if not allow_synthetic:
        for pointer, prop in _iter_props(root, f"/{dtype}"):
            if prop.get("name") != "synthetic":
                continue
            if str(prop.get("value", "")).strip().lower() in {"true", "yes", "1"}:
                errors.append(f"semantic: synthetic object marked at {pointer}")

    return errors


def _control_implementations(
    root: Dict[str, Any], document_type: str
) -> List[tuple[str, Dict[str, Any]]]:
    """Return ``(pointer, control-implementation)`` pairs for a document.

    An SSP has exactly one; a component-definition has one per component and
    per capability, each with its own requirement namespace.
    """
    if document_type == "system-security-plan":
        implementation = root.get("control-implementation")
        return [("/control-implementation", implementation)] if implementation else []
    pairs: List[tuple[str, Dict[str, Any]]] = []
    for collection in ("components", "capabilities"):
        for index, item in enumerate(root.get(collection) or []):
            for ci_index, implementation in enumerate(
                item.get("control-implementations") or []
            ):
                pairs.append(
                    (
                        f"/{collection}/{index}/control-implementations/{ci_index}",
                        implementation,
                    )
                )
    return pairs


def _validate_control_identifiers(
    root: Dict[str, Any], document_type: str
) -> List[str]:
    """Check control, statement, and parameter identifier coherence."""
    errors: List[str] = []
    for base, implementation in _control_implementations(root, document_type):
        seen_controls: Dict[str, str] = {}
        for index, requirement in enumerate(
            implementation.get("implemented-requirements") or []
        ):
            pointer = f"{base}/implemented-requirements/{index}"
            control_id = requirement.get("control-id")
            if not isinstance(control_id, str) or not control_id:
                errors.append(f"semantic: {pointer} has no control-id")
                continue
            if control_id in seen_controls:
                errors.append(
                    f"semantic: duplicate control-id {control_id!r} at {pointer} "
                    f"(first seen at {seen_controls[control_id]})"
                )
            else:
                seen_controls[control_id] = pointer

            seen_statements: set[str] = set()
            for stmt_index, statement in enumerate(requirement.get("statements") or []):
                statement_id = statement.get("statement-id")
                stmt_pointer = f"{pointer}/statements/{stmt_index}"
                if not isinstance(statement_id, str) or not statement_id:
                    errors.append(f"semantic: {stmt_pointer} has no statement-id")
                    continue
                if statement_id in seen_statements:
                    errors.append(
                        f"semantic: duplicate statement-id {statement_id!r} "
                        f"at {stmt_pointer}"
                    )
                seen_statements.add(statement_id)
                # An OSCAL statement-id is namespaced by its control; a
                # mismatch means the statement was attached to the wrong
                # requirement, which no schema check can detect.
                if not statement_id.startswith(f"{control_id}_"):
                    errors.append(
                        f"semantic: statement-id {statement_id!r} at {stmt_pointer} "
                        f"is not scoped to control-id {control_id!r}"
                    )

            errors.extend(
                _validate_set_parameters(
                    requirement.get("set-parameters"), pointer, control_id
                )
            )
            for bc_index, by_component in enumerate(
                requirement.get("by-components") or []
            ):
                errors.extend(
                    _validate_set_parameters(
                        by_component.get("set-parameters"),
                        f"{pointer}/by-components/{bc_index}",
                        control_id,
                    )
                )
    return errors


def _validate_set_parameters(
    set_parameters: Any, pointer: str, control_id: str
) -> List[str]:
    """Check ``set-parameters[].param-id`` uniqueness and control scoping."""
    errors: List[str] = []
    if not isinstance(set_parameters, list):
        return errors
    seen: set[str] = set()
    # A control enhancement's parameters are namespaced by the base control
    # (``ac-2.3_odp.01`` belongs to ``ac-2.3``, but ``ac-2_odp.01`` may legally
    # appear on ``ac-2.3`` when the enhancement sets a base-control parameter).
    base_control = control_id.split(".", 1)[0]
    for index, parameter in enumerate(set_parameters):
        if not isinstance(parameter, dict):
            continue
        param_id = parameter.get("param-id")
        param_pointer = f"{pointer}/set-parameters/{index}"
        if not isinstance(param_id, str) or not param_id:
            errors.append(f"semantic: {param_pointer} has no param-id")
            continue
        if param_id in seen:
            errors.append(
                f"semantic: duplicate param-id {param_id!r} at {param_pointer}"
            )
        seen.add(param_id)
        if not (
            param_id.startswith(f"{control_id}_")
            or param_id.startswith(f"{base_control}_")
        ):
            errors.append(
                f"semantic: param-id {param_id!r} at {param_pointer} is not scoped "
                f"to control-id {control_id!r}"
            )
    return errors


# ---------------------------------------------------------------------------
# Cross-stage parity
# ---------------------------------------------------------------------------


@dataclass
class StageSummary:
    """Deterministic identifier + value inventory for one pipeline stage.

    The id lists catch dropped/renamed entities. The value maps catch *content*
    drift at a stable id -- a changed ODP parameter value or a mismatched
    statement/control count -- which an id-set diff alone cannot see.
    """

    stage: str
    control_ids: List[str] = field(default_factory=list)
    statement_ids: List[str] = field(default_factory=list)
    parameter_ids: List[str] = field(default_factory=list)
    party_names: List[str] = field(default_factory=list)
    component_titles: List[str] = field(default_factory=list)
    # param-id -> normalized value string, for value-level drift detection.
    parameter_values: Dict[str, str] = field(default_factory=dict)
    # control-id -> number of statements, to catch statement loss within a
    # control even when the control itself is present.
    statements_per_control: Dict[str, int] = field(default_factory=dict)

    def counts(self) -> Dict[str, int]:
        return {
            "controls": len(self.control_ids),
            "statements": len(self.statement_ids),
            "parameters": len(self.parameter_ids),
            "parties": len(self.party_names),
            "components": len(self.component_titles),
        }


def _normalize_control_id(control_id: str) -> str:
    """Normalize a vault control id to the OSCAL token form.

    Mirrors ``export_oscal._oscal_control_id`` so the two stages are compared in
    one vocabulary. Kept local to avoid a validate -> export import cycle.
    """
    import re

    text = (control_id or "").strip().lower()
    return re.sub(r"\((\w+)\)", r".\1", text)


def _normalize_param_value(raw: Any) -> str:
    """Render a set-parameter value to a stable comparable string.

    Accepts the canonical shape (``value``/``values``) and the OSCAL shape
    (``values: [...]`` / ``value``) and normalizes whitespace so the two stages
    compare in one vocabulary.
    """
    if isinstance(raw, dict):
        value = raw.get("value")
        values = raw.get("values")
        if value is None and values is None:
            value = raw.get("select")
    else:
        value = raw
        values = None
    if values is not None:
        if isinstance(values, (list, tuple)):
            parts = [" ".join(str(v).split()) for v in values if v is not None]
            return "|".join(parts)
        return " ".join(str(values).split())
    if value is None:
        return ""
    return " ".join(str(value).split())


def summarize_canonical_ssp(
    plan_data: Dict[str, Any],
    control_sidecars: List[Dict[str, Any]],
    party_sidecars: Optional[List[Dict[str, Any]]] = None,
    system_data: Optional[Dict[str, Any]] = None,
    extension_sidecars: Optional[List[Dict[str, Any]]] = None,
) -> StageSummary:
    """Summarize the canonical (vault) stage of an SSP export.

    Statement ids are derived the same way the exporter derives them, from
    ``Part <x>`` body headings, so a dropped or renamed statement shows up as a
    parity difference rather than a silent omission. Parameter *values* and the
    per-control statement count are also captured so a changed ODP value or a
    gutted control is caught, not just a missing id.

    ``system_data`` is the matching ``oscal.ssp`` *system* note (``note_type:
    system``); the exporter titles the this-system component from its ``name``
    first, so it must be supplied here for component-title parity to be exact.

    ``extension_sidecars`` are the plan's ``oscal.x.*`` notes; the exporter
    synthesizes a leveraged-authorization party per ``oscal.x.inheritance``
    note, so those party names are counted here too or parity would report a
    spurious extra party.
    """
    summary = StageSummary(stage="canonical")
    for sidecar in control_sidecars:
        fm = sidecar.get("frontmatter", sidecar)
        raw_id = fm.get("control_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        control_id = _normalize_control_id(raw_id)
        summary.control_ids.append(control_id)
        statement_count = 0
        for section in sidecar.get("body_sections") or []:
            heading = (section.get("heading") or "").strip()
            if heading.lower().startswith("part "):
                part = heading.split(" ", 1)[1].strip().lower()
                summary.statement_ids.append(f"{control_id}_smt.{part}")
                statement_count += 1
        summary.statements_per_control[control_id] = statement_count
        raw_params = (
            fm.get("set_parameters") or fm.get("set-parameters") or fm.get("parameters")
        )
        for parameter in raw_params or []:
            if isinstance(parameter, dict):
                param_id = parameter.get("param_id") or parameter.get("param-id")
                if param_id:
                    summary.parameter_ids.append(str(param_id))
                    summary.parameter_values[str(param_id)] = _normalize_param_value(
                        parameter
                    )
    for sidecar in party_sidecars or []:
        pfm = sidecar.get("frontmatter", sidecar)
        name = str(pfm.get("name") or pfm.get("title") or "").strip()
        if name:
            summary.party_names.append(name)
    # Use the typed adapter for synthesized inheritance parties so parity checks
    # the exact mapped Provided By value rather than independently guessing it.
    inheritance_sidecars = [
        sidecar
        for sidecar in extension_sidecars or []
        if (sidecar.get("type") or sidecar.get("note_type")) == "oscal.x.inheritance"
    ]
    if inheritance_sidecars:
        from compliance.lib.oscal_extension_bridge import (
            leveraged_authorizations_from_inheritance,
        )

        _, leveraged_parties = leveraged_authorizations_from_inheritance(
            inheritance_sidecars
        )
        summary.party_names.extend(
            str(party.get("name") or "").strip()
            for party in leveraged_parties
            if str(party.get("name") or "").strip()
        )
    plan_fm = plan_data.get("frontmatter", plan_data)
    sys_fm = (system_data or {}).get("frontmatter", system_data or {})
    # Mirror the exporter's this-system component title precedence exactly
    # (sys_fm.name -> plan.system_name -> plan.title) or every vault reports a
    # spurious component-title mismatch.
    system_name = (
        sys_fm.get("name")
        or plan_fm.get("system_name")
        or plan_fm.get("title")
        or "System"
    )
    summary.component_titles.append(str(system_name))
    summary.control_ids.sort()
    summary.statement_ids.sort()
    summary.parameter_ids.sort()
    summary.party_names.sort()
    return summary


def summarize_oscal_ssp(document: Dict[str, Any]) -> StageSummary:
    """Summarize the exported OSCAL stage of an SSP."""
    summary = StageSummary(stage="oscal")
    ssp = document.get("system-security-plan", document)
    requirements = (ssp.get("control-implementation") or {}).get(
        "implemented-requirements"
    ) or []
    for requirement in requirements:
        control_id = requirement.get("control-id")
        statement_count = 0
        for statement in requirement.get("statements") or []:
            statement_id = statement.get("statement-id")
            if statement_id:
                summary.statement_ids.append(str(statement_id))
                statement_count += 1
        if isinstance(control_id, str) and control_id:
            summary.control_ids.append(control_id)
            summary.statements_per_control[control_id] = statement_count
        for parameter in requirement.get("set-parameters") or []:
            param_id = parameter.get("param-id")
            if param_id:
                summary.parameter_ids.append(str(param_id))
                summary.parameter_values[str(param_id)] = _normalize_param_value(
                    parameter
                )
    for party in (ssp.get("metadata") or {}).get("parties") or []:
        name = party.get("name")
        if name:
            summary.party_names.append(str(name))
    for component in (ssp.get("system-implementation") or {}).get("components") or []:
        title = component.get("title")
        if title:
            summary.component_titles.append(str(title))
    summary.control_ids.sort()
    summary.statement_ids.sort()
    summary.parameter_ids.sort()
    summary.party_names.sort()
    return summary


def verify_oscal_parity(
    canonical: StageSummary,
    oscal: StageSummary,
    *,
    allow_synthetic: bool = False,
) -> List[str]:
    """Compare two stage summaries and report every identifier difference.

    This is the check that catches loss between stages: schema validation and
    reference validation both pass on a document that is internally coherent but
    missing half the source controls.
    """
    errors: List[str] = []
    if allow_synthetic and not canonical.control_ids:
        # A documented placeholder export has no canonical controls to compare.
        return errors
    for label, left, right in (
        ("control", canonical.control_ids, oscal.control_ids),
        ("statement", canonical.statement_ids, oscal.statement_ids),
        ("parameter", canonical.parameter_ids, oscal.parameter_ids),
        ("party", canonical.party_names, oscal.party_names),
        ("component", canonical.component_titles, oscal.component_titles),
    ):
        missing = sorted(set(left) - set(right))
        extra = sorted(set(right) - set(left))
        if missing:
            errors.append(
                f"parity: {len(missing)} {label}(s) present in canonical but "
                f"absent from OSCAL: {missing[:10]}"
            )
        if extra:
            errors.append(
                f"parity: {len(extra)} {label}(s) present in OSCAL but absent "
                f"from canonical: {extra[:10]}"
            )
        if len(left) != len(right):
            errors.append(
                f"parity: {label} count mismatch: canonical={len(left)} "
                f"oscal={len(right)}"
            )

    # Value-level drift: a parameter whose id is present in both stages but
    # whose value changed. An id-set diff cannot see this.
    for param_id in sorted(
        set(canonical.parameter_values) & set(oscal.parameter_values)
    ):
        left_value = canonical.parameter_values[param_id]
        right_value = oscal.parameter_values[param_id]
        if left_value != right_value:
            errors.append(
                f"parity: parameter value drift for {param_id!r}: "
                f"canonical={left_value[:60]!r} oscal={right_value[:60]!r}"
            )

    # Statement loss within a control that is itself present in both stages.
    for control_id in sorted(
        set(canonical.statements_per_control) & set(oscal.statements_per_control)
    ):
        left_count = canonical.statements_per_control[control_id]
        right_count = oscal.statements_per_control[control_id]
        if left_count != right_count:
            errors.append(
                f"parity: statement count drift for control {control_id!r}: "
                f"canonical={left_count} oscal={right_count}"
            )

    return errors


def _format_error(error: Any) -> str:
    """Render a jsonschema ValidationError into a concise, path-prefixed line."""
    location = "/".join(str(p) for p in error.absolute_path) or "<root>"
    return f"{location}: {error.message}"
