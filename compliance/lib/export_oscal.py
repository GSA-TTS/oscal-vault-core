"""Transform canonical JSON sidecars into strict NIST OSCAL JSON/YAML exports.

For notes marked as `exportable: true` with a recognized `oscal_model`, this
module converts the canonical JSON sidecar into a standards-conformant OSCAL
document following the NIST OSCAL 1.0 XML-derived JSON format.

Supported export mappings:
  - oscal.control → (embedded in SSP context, not standalone)
  - oscal.ssp → system-security-plan
  - oscal.component_definition → component-definition
  - oscal.poam → plan-of-action-and-milestones
  - oscal.assessment_results → assessment-results
  - oscal.assessment_plan → assessment-plan
"""

from __future__ import annotations

import copy
import json
import re
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default OSCAL version emitted by this module (ADR-019, phase R17). Kept as a
# single module constant so the version-agility follow-up (#13) has one seam to
# parameterize; the NIST schema the Layer 1 validator loads is keyed to it.
DEFAULT_OSCAL_VERSION = "1.1.3"

# Typed exporter adapters claim exact source properties and target paths. Tests
# execute every claim with sentinels so this registry cannot mask a dropped field.
EXPORTER_MAPPING_ADAPTERS = {
    "party": {
        "oscal_party.uuid": ("system-security-plan.metadata.parties[].uuid",),
        "oscal_party.type": ("system-security-plan.metadata.parties[].type",),
        "oscal_party.name": ("system-security-plan.metadata.parties[].name",),
        "oscal_party.role_ids": (
            "system-security-plan.metadata.responsible-parties[].role-id",
        ),
        "oscal_party.email_address": (
            "system-security-plan.metadata.parties[].email-addresses[0]",
        ),
        "oscal_party.phone": (
            "system-security-plan.metadata.parties[].telephone-numbers[0].number",
        ),
        "oscal_party_ao.role_ids": (
            "system-security-plan.metadata.responsible-parties[].role-id",
        ),
        "oscal_party_ao.email_address": (
            "system-security-plan.metadata.parties[].email-addresses[0]",
        ),
        "oscal_party_ao.phone": (
            "system-security-plan.metadata.parties[].telephone-numbers[0].number",
        ),
        "oscal_party_security.role_ids": (
            "system-security-plan.metadata.responsible-parties[].role-id",
        ),
        "oscal_party_security.email_address": (
            "system-security-plan.metadata.parties[].email-addresses[0]",
        ),
        "oscal_party_security.phone": (
            "system-security-plan.metadata.parties[].telephone-numbers[0].number",
        ),
        "oscal_party_contact.role_ids": (
            "system-security-plan.metadata.responsible-parties[].role-id",
        ),
        "oscal_party_contact.email_address": (
            "system-security-plan.metadata.parties[].email-addresses[0]",
        ),
        "oscal_party_contact.phone": (
            "system-security-plan.metadata.parties[].telephone-numbers[0].number",
        ),
        "oscal_party_preparer.role_ids": (
            "system-security-plan.metadata.responsible-parties[].role-id",
        ),
    },
    "component-definition": {
        "oscal_component_definition.uuid": ("component-definition.uuid",),
        "oscal_component_definition.title": (
            "component-definition.components[0].title",
        ),
        "oscal_component_definition.inventory_items": (
            "component-definition.components[0].props",
        ),
        "oscal_component_definition.software_inventory": (
            "component-definition.components[0].props",
        ),
        "oscal_component_definition.external_services": (
            "component-definition.components[0].props",
        ),
        "oscal_component_definition.aws_services_in_use": (
            "component-definition.components[0].props",
        ),
    },
    "boundary-bridge": {
        "oscal_x_boundary.description": (
            "system-characteristics.network-architecture.description",
        ),
        "oscal_x_boundary.types_of_users": (
            "system-implementation.users[].authorized-privileges",
        ),
    },
    "relationship-bridge": {
        "oscal_x_relationship.interconnections": (
            "system-characteristics.data-flow.description",
        ),
        "oscal_x_relationship.ports_protocols_services": (
            "system-implementation.components[].protocols",
        ),
    },
    "inheritance-bridge": {
        "oscal_x_inheritance.provided_by": (
            "system-implementation.leveraged-authorizations",
        ),
    },
    "crm-control-sidecar": {
        "oscal_control.crm": (
            "control-implementation.implemented-requirements[].by-components[].export.provided",
            "control-implementation.implemented-requirements[].by-components[].export.responsibilities",
        ),
    },
}

# Deterministic UUIDv5 namespace so re-exporting the same logical entity yields
# a stable, schema-valid UUID (the NIST schemas require the RFC-4122 v4/v5
# shape, which the old "generated-<x>-uuid" placeholders violated). Using v5
# keeps exports reproducible for diffing without persisting a UUID map.
_OSCAL_NS = _uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # RFC-4122 DNS ns

# Namespace URI for this project's OSCAL extension props (okf.* / oscal.x.*).
FEDRAMP_PARSER_NS = "https://github.com/JJediny/oscal-vault/ns/oscal"

_UUID_RE_V4V5 = (
    "^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[45][0-9A-Fa-f]{3}-"
    "[89ABab][0-9A-Fa-f]{3}-[0-9A-Fa-f]{12}$"
)


class ExportError(ValueError):
    """Base class for fail-closed export errors."""


class ControlConversionError(ExportError):
    """A control sidecar could not be converted into an implemented-requirement."""


class MissingControlsError(ExportError):
    """No real controls were converted and synthetic fallback is not permitted."""


class ControlParityError(ExportError):
    """The converted control count disagrees with the plan's declared count."""


def _coerce_uuid(value: Optional[str], *, seed: str) -> str:
    """Return ``value`` if it is already a valid RFC-4122 v4/v5 UUID, else a
    deterministic UUIDv5 derived from ``seed``.

    Vault notes sometimes carry a real UUID (e.g. a system's ``system_id``) and
    sometimes carry nothing or a non-UUID id. Either way the OSCAL output must
    be a schema-valid UUID, and re-exports of the same entity must be stable —
    UUIDv5 over a stable seed gives both.
    """
    import re

    if value and re.match(_UUID_RE_V4V5, str(value)):
        return str(value)
    return str(_uuid.uuid5(_OSCAL_NS, seed))


def _prop(name: str, value: Any, *, ns: str = FEDRAMP_PARSER_NS) -> Dict[str, Any]:
    """Build an OSCAL ``property`` object (required: name, value).

    OSCAL constrains ``prop.value`` to a single non-empty line with no leading
    or trailing whitespace (pattern ``^\\S(.*\\S)?$``, no DOTALL). Multi-line or
    padded values — common in recovered SSP prose — are collapsed to a single
    space-joined line so they stay schema-valid rather than failing Layer 1.
    """
    if value is None:
        text = ""
    else:
        text = " ".join(str(value).split())
    return {"name": name, "ns": ns, "value": text}


# Vault frontmatter keys that are bookkeeping / wikilink noise, never exported
# as OSCAL props.
_NON_PROP_KEYS = frozenset(
    {
        "type",
        "oscal_model",
        "extension_model",
        "kind",
        "note_type",
        "exportable",
        "parsed_by",
        "parser",
        "last_updated",
        "reviewed",
        "date",
        "tags",
        "owner",
        "system",
        "source_profile",
        "source_ssp",
        "uuid",
        "title",
        "status",
        "version",
        "oscasl_version",
        "oscal_version",
        # already mapped to first-class OSCAL fields on the relevant model:
        "control_id",
        "implementation_status",
        "origination",
        "system_name",
        "name",
        "fips_199",
        "plan_id",
        "system_id",
        "profile_ref",
        "import_profile",
        "component_type",
    }
)


def _extension_props(fm: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map leftover vault-specific metadata (okf.* / oscal.x.* and misc kinds)
    onto standard OSCAL ``props[]`` so nothing is lost and the NIST schema (and
    CCF's relational mapper) preserve it. Scalar/str/bool/number values only;
    wikilink lists are flattened to comma-joined strings.
    """
    props: List[Dict[str, Any]] = []
    for key, value in sorted(fm.items()):
        if key in _NON_PROP_KEYS or key.startswith("_"):
            continue
        # Structural canonical-JSON containers are never props.
        if key in {
            "raw_body",
            "body_sections",
            "inline_fields",
            "frontmatter",
            "table_records",
            "table_record_metrics",
            "table_record_count",
            "table_kinds",
            "table_classifications",
            "table_structured_count",
            "table_opaque_count",
            "table_unmapped_count",
        }:
            continue
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple)):
            flattened = ", ".join(str(v) for v in value if v is not None)
            if flattened.strip():
                props.append(_prop(key, flattened))
        elif isinstance(value, (str, int, float, bool)):
            if str(value).strip():
                props.append(_prop(key, value))
        # dict/complex values are skipped (would need bespoke mapping)
    return props


def _strip_note_type(fm: Dict[str, Any]) -> Dict[str, Any]:
    """Remove non-OSCAL fields from frontmatter for clean export."""
    oscal_fields = {}
    non_oscal = {
        "type",
        "oscal_model",
        "kind",
        "note_type",
        "extension_model",
        "exportable",
        "parsed_by",
        "last_updated",
        "tags",
        "owner",
        "system",
        "source_profile",
        "source_ssp",
    }
    for k, v in fm.items():
        if k not in non_oscal:
            oscal_fields[k] = v
    return oscal_fields


def control_to_oscal_implemented_requirement(
    canonical_data: Dict[str, Any],
    *,
    component_uuid: Optional[str] = None,
    role_titles: Optional[Dict[str, str]] = None,
    lineage_events: Optional[List[Dict[str, Any]]] = None,
    lineage_derivations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Convert a canonical control to an OSCAL `implemented-requirement`.

    The control note does not become a standalone OSCAL document; it maps into
    the `control-implementation.implemented-requirements[]` array of an SSP.

    Args:
        canonical_data: the control's canonical JSON (frontmatter + body).
        component_uuid: the SSP's ``this-system`` component UUID that this
            requirement's ``by-component`` points at. Passing the plan's real
            component UUID keeps the by-component/component graph internally
            consistent; if omitted a deterministic synthetic one is derived.
        role_titles: collector for ``role-id -> title``. The caller declares
            these in ``metadata.roles[]`` so the emitted ``responsible-roles``
            references resolve.
    """
    fm = canonical_data.get("frontmatter", canonical_data)
    body_sections = canonical_data.get("body_sections", [])
    inline_fields = canonical_data.get("inline_fields", {})

    control_id = fm.get("control_id", "")
    if not isinstance(control_id, str) or not control_id.strip():
        raise ValueError("control_id is required for OSCAL export")
    ir_uuid = _coerce_uuid(fm.get("uuid"), seed=f"ir:{control_id}")

    result: Dict[str, Any] = {
        "uuid": ir_uuid,
        "control-id": _oscal_control_id(control_id),
    }

    # implementation-status + control-origination are NOT first-class fields on
    # an OSCAL implemented-requirement; per FedRAMP convention they are carried
    # as props on the requirement. This also keeps the raw vault vocabulary
    # (richer than OSBAPI's enums) intact for round-trip.
    props: List[Dict[str, Any]] = []
    impl_status = fm.get("implementation_status") or inline_fields.get(
        "implementation_status"
    )
    if impl_status:
        props.append(_prop("implementation-status", impl_status))

    origination = fm.get("origination")
    if origination:
        values = [origination] if isinstance(origination, str) else list(origination)
        for val in values:
            props.append(_prop("control-origination", val))
    if props:
        result["props"] = props

    # set-parameters: recovered organization-defined parameter (ODP) values.
    # Frontmatter carries them as [{"param_id": "ac-02_odp.01", "value": "..."}]
    # or [{"param_id": "ac-01_prm_1", "values": ["..."]}].
    set_params = _oscal_set_parameters(
        fm.get("set_parameters") or fm.get("set-parameters") or fm.get("parameters")
    )
    if set_params:
        result["set-parameters"] = set_params

    # responsible-roles: link the control to the party/role that owns it
    # (D-1 / #38). Emitted as first-class OSCAL `responsible-roles[]` so the
    # per-control ownership survives export, not just as vault frontmatter.
    resp_roles = _oscal_responsible_roles(
        fm.get("responsible_roles")
        or fm.get("responsible-roles")
        or fm.get("responsible_role"),
        role_titles=role_titles,
    )
    if resp_roles:
        result["responsible-roles"] = resp_roles

    # statements from body sections (schema requires statement-id + uuid).
    # The OSCAL SSP `statement` object does not permit a `description` field;
    # statement-level prose is carried as `remarks`. The implementation
    # narrative that a consumer reads lives on `by-components[].description`
    # instead (see below), which is where the fismatic path's statement text is
    # also surfaced — keeping both directions field-symmetric (D-7 / #42).
    statements: List[Dict[str, Any]] = []
    statement_prose: List[str] = []
    for section in body_sections:
        heading = section.get("heading", "") or ""
        content = section.get("content", "")
        if heading.lower().startswith("part "):
            part_id = heading.split(" ", 1)[1].strip()
            stmt_id = _oscal_statement_id(control_id, part_id)
            entry: Dict[str, Any] = {
                "statement-id": stmt_id,
                "uuid": _coerce_uuid(None, seed=f"stmt:{stmt_id}"),
            }
            if content:
                entry["remarks"] = content
                statement_prose.append(f"{heading.strip()}: {content}")
            statements.append(entry)
    if statements:
        result["statements"] = statements
    # Concatenated statement narrative, reused as the by-component description
    # so the implementation prose is not lost to a generic placeholder.
    combined_prose = "\n\n".join(statement_prose).strip()

    # by-components: each requirement must reference system component(s) that
    # implement it (schema requires component-uuid, uuid, description).
    by_comps_fm = fm.get("by_components") or fm.get("by-components")
    if by_comps_fm and isinstance(by_comps_fm, list):
        by_comps: List[Dict[str, Any]] = []
        for idx, bc in enumerate(by_comps_fm):
            if not isinstance(bc, dict):
                continue
            bc_uuid = (
                bc.get("component_uuid") or bc.get("component-uuid") or component_uuid
            )
            entry: Dict[str, Any] = {
                "component-uuid": _coerce_uuid(
                    bc_uuid, seed=f"comp:{idx}:{control_id}"
                ),
                "uuid": _coerce_uuid(bc.get("uuid"), seed=f"bycomp:{control_id}:{idx}"),
                "description": bc.get("description")
                or f"Implementation of {control_id or 'control'}.",
            }
            bc_status = bc.get("implementation_status") or bc.get(
                "implementation-status"
            )
            if bc_status:
                state = (
                    bc_status.get("state")
                    if isinstance(bc_status, dict)
                    else str(bc_status)
                )
                entry["implementation-status"] = {"state": state}
            bc_params = _oscal_set_parameters(
                bc.get("set_parameters") or bc.get("set-parameters")
            )
            if bc_params:
                entry["set-parameters"] = bc_params
            crm_events: List[Dict[str, Any]] = []
            crm_export = _oscal_crm_export(
                fm,
                control_id=_oscal_control_id(control_id),
                by_component_uuid=entry["component-uuid"],
                lineage_events=crm_events,
            )
            if lineage_events is not None:
                lineage_events.extend(crm_events)
            if crm_export:
                entry["export"] = crm_export
                if lineage_derivations is not None:
                    from compliance.lib.oscal_lineage import runtime_derivation

                    lineage_derivations.append(
                        runtime_derivation(
                            mapping_source="oscal_control.crm",
                            source_ids=[str(fm.get("source_locator") or control_id)],
                            canonical_pointers=[
                                "/crm",
                                "/inherited_from",
                                "/customer_responsibilities",
                            ],
                            oscal_pointers=["/by-components/export"],
                            events=crm_events,
                        )
                    )
            by_comps.append(entry)
        if by_comps:
            result["by-components"] = by_comps

    if "by-components" not in result:
        comp_uuid = component_uuid or _coerce_uuid(
            fm.get("component_id"), seed="component:this-system"
        )
        by_comp: Dict[str, Any] = {
            "component-uuid": comp_uuid,
            "uuid": _coerce_uuid(None, seed=f"bycomp:{control_id}"),
            # Prefer the real statement narrative; fall back to a placeholder
            # only when the control captured no statement prose (D-7 / #42).
            "description": combined_prose
            or f"Implementation of {control_id or 'control'}.",
        }
        if impl_status:
            by_comp["implementation-status"] = {"state": impl_status}
        crm_events = []
        crm_export = _oscal_crm_export(
            fm,
            control_id=_oscal_control_id(control_id),
            by_component_uuid=by_comp["component-uuid"],
            lineage_events=crm_events,
        )
        if lineage_events is not None:
            lineage_events.extend(crm_events)
        if crm_export:
            by_comp["export"] = crm_export
            if lineage_derivations is not None:
                from compliance.lib.oscal_lineage import runtime_derivation

                lineage_derivations.append(
                    runtime_derivation(
                        mapping_source="oscal_control.crm",
                        source_ids=[str(fm.get("source_locator") or control_id)],
                        canonical_pointers=[
                            "/crm",
                            "/inherited_from",
                            "/customer_responsibilities",
                        ],
                        oscal_pointers=["/by-components/0/export"],
                        events=crm_events,
                    )
                )
        result["by-components"] = [by_comp]

    return result


def _oscal_crm_export(
    fm: Dict[str, Any],
    *,
    control_id: str,
    by_component_uuid: str,
    lineage_events: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Build native SSP ``by-component.export`` from CRM/inheritance fields.

    ``provided[]`` is emitted only when the vault has an actual provider-side
    statement (``inherited_from``, ``crm.inheritance``, or a CRM evidence/
    assessment statement). ``responsibilities[]`` is emitted from explicit
    customer-responsibility prose, CRM assessment/evidence fields, or CRM extra
    columns. This deliberately avoids fabricating a shared-responsibility split
    for ordinary local controls.
    """
    crm = fm.get("crm") if isinstance(fm.get("crm"), dict) else {}
    inherited_from = str(fm.get("inherited_from") or "").strip()
    inheritance = str(crm.get("inheritance") or "").strip()
    customer = fm.get("customer_responsibilities") or fm.get("customer-responsibility")
    if isinstance(customer, list):
        customer_text = "\n".join(str(item) for item in customer if item)
    else:
        customer_text = str(customer or "").strip()

    provider_parts = [
        part
        for part in (
            inherited_from,
            f"CRM inheritance: {inheritance}" if inheritance else "",
            str(crm.get("evidence") or "").strip(),
        )
        if part
    ]
    responsibility_parts = [
        part
        for part in (
            customer_text,
            str(crm.get("assessment_objective") or "").strip(),
            str(crm.get("assessment_method") or "").strip(),
            str(crm.get("impacted") or "").strip(),
            *(
                f"{key}: {value}"
                for key, value in (crm.get("extra") or {}).items()
                if value
            ),
        )
        if part
    ]
    if not provider_parts and not responsibility_parts:
        return None
    if lineage_events is not None:
        from compliance.lib.transforms import TransformEvent, digest_value

        for label, parts in (
            ("provided", provider_parts),
            ("responsibilities", responsibility_parts),
        ):
            if len(parts) > 1:
                joined = "\n".join(parts)
                lineage_events.append(
                    TransformEvent(
                        operator="record_aggregation",
                        input_sha256=digest_value(parts),
                        output_sha256=digest_value(joined),
                        changed=True,
                        lossiness="none",
                        consumed=tuple(parts),
                        warnings=(f"crm {label} values aggregated",),
                    ).to_dict()
                )

    export: Dict[str, Any] = {
        "description": (
            f"CRM/shared-responsibility statement for {control_id} "
            f"at component {by_component_uuid}."
        )
    }
    provided_uuid = _coerce_uuid(
        None, seed=f"crm-provided:{control_id}:{by_component_uuid}"
    )
    if provider_parts:
        export["provided"] = [
            {
                "uuid": provided_uuid,
                "description": "\n".join(provider_parts),
            }
        ]
    if responsibility_parts:
        responsibility: Dict[str, Any] = {
            "uuid": _coerce_uuid(
                None, seed=f"crm-responsibility:{control_id}:{by_component_uuid}"
            ),
            "description": "\n".join(responsibility_parts),
        }
        if provider_parts:
            responsibility["provided-uuid"] = provided_uuid
        export["responsibilities"] = [responsibility]
    return export


def _oscal_control_id(control_id: str) -> str:
    """Normalize a vault control id to OSCAL's token form.

    OSCAL control-ids are lowercase NCName-style tokens and encode control
    *enhancements* with a dot, not parentheses:

        ``SC-7(3)``  -> ``sc-7.3``
        ``AT-2(2)``  -> ``at-2.2``
        ``AC-2``     -> ``ac-2``

    Parentheses are not permitted by the OSCAL token pattern, so a raw
    ``at-2(2)`` fails schema validation.
    """
    import re

    text = (control_id or "").strip().lower()
    # (n) or (n)(m) enhancement suffixes -> .n(.m)
    text = re.sub(r"\((\w+)\)", r".\1", text)
    return text


def _oscal_statement_id(control_id: str, part_id: str) -> str:
    """Build an OSCAL statement-id token: ``<oscal-control-id>_smt.<part>``."""
    return f"{_oscal_control_id(control_id)}_smt.{part_id.strip().lower()}"


def _oscal_role_id(raw: str) -> str:
    """Normalize a role name/id to an OSCAL role-id token (lowercase, kebab).

    Strips Obsidian wikilink syntax (``[[Organizations/slug|Name]]`` ->
    ``Name``) since vault control notes carry ``responsible_role`` as
    wikilinks.
    """
    import re

    text = str(raw or "").strip()
    m = re.search(r"\[\[[^\]|]*\|([^\]]+)\]\]", text)
    if m:
        text = m.group(1)
    else:
        m = re.search(r"\[\[[^\]/]*/([^\]|]+)\]\]", text)
        if m:
            text = m.group(1)
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "responsible-role"


def _oscal_responsible_roles(
    raw: Any, *, role_titles: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    """Build OSCAL ``responsible-roles[]`` from a control's responsible role(s).

    Accepts a scalar role name, a list of role names, or a list of dicts
    carrying ``role_id`` / ``role-id`` (and optional ``party_uuids``). Each
    entry emits ``{"role-id": token, ...}``; blank/None entries are skipped.

    ``role_titles`` collects ``token -> human label`` for every role emitted. A
    ``role-id`` is a reference to ``metadata.roles[]``; without this the caller
    cannot declare the role and the reference dangles (schema-valid, but
    semantically broken).
    """
    if not raw:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in items:
        if isinstance(item, dict):
            rid = item.get("role_id") or item.get("role-id") or item.get("role")
            party_uuids = item.get("party_uuids") or item.get("party-uuids")
        else:
            rid = item
            party_uuids = None
        rid_str = str(rid or "").strip()
        if not rid_str:
            continue
        role_id = _oscal_role_id(rid_str)
        if role_titles is not None:
            role_titles.setdefault(role_id, _role_title(rid_str))
        if role_id in seen:
            continue
        seen.add(role_id)
        entry: Dict[str, Any] = {"role-id": role_id}
        if party_uuids:
            uuids = party_uuids if isinstance(party_uuids, list) else [party_uuids]
            coerced = [_coerce_uuid(str(u), seed=f"party:{u}") for u in uuids if u]
            if coerced:
                entry["party-uuids"] = coerced
        out.append(entry)
    return out


def _role_title(raw: str) -> str:
    """Human-readable title for a role, recovered from its source label."""
    import re

    text = str(raw or "").strip()
    m = re.search(r"\[\[[^\]|]*\|([^\]]+)\]\]", text)
    if m:
        text = m.group(1)
    else:
        m = re.search(r"\[\[[^\]/]*/([^\]|]+)\]\]", text)
        if m:
            text = m.group(1).replace("-", " ")
    text = " ".join(text.split())
    # OSCAL role titles are single-line markup-multiline strings; keep them
    # short enough to be usable in a reviewer UI.
    if len(text) > 120:
        text = text[:117].rstrip() + "..."
    return text or "Responsible role"


def _oscal_set_parameters(raw: Any) -> List[Dict[str, Any]]:
    """Build OSCAL ``set-parameters[]`` from a control note's ``parameters`` or ``set_parameters``.

    Accepts frontmatter shapes:
      - ``[{"param_id": "ac-02_odp.01", "value": "x"}]``
      - ``[{"param_id": "ac-02_odp.01", "values": ["x", "y"], "remarks": "..."}]``
    and emits ``[{"param-id": "ac-02_odp.01", "values": ["x", ...], ...}]``.
    """
    if not raw or not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("param_id") or item.get("param-id") or "").strip()
        vals = item.get("values")
        if vals is None and "value" in item:
            val = item.get("value")
            vals = [val] if val is not None else []
        elif isinstance(vals, (str, int, float, bool)):
            vals = [vals]
        if not pid or not vals:
            continue
        str_vals = [str(v) for v in vals if v is not None and str(v).strip() != ""]
        if not str_vals:
            continue
        entry: Dict[str, Any] = {"param-id": pid, "values": str_vals}
        if item.get("remarks"):
            entry["remarks"] = str(item["remarks"])
        out.append(entry)
    return out


def _information_type(
    system_name: str,
    fips: str,
    oscal_version: str,
    *,
    information_types_data: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Build a schema-valid ``information-types[]`` list for the target OSCAL version.

    When *information_types_data* is provided and non-empty, those dicts are
    returned directly (they already carry the correct OSCAL shape as produced by
    :func:`compliance.lib.fips199.fips199_to_oscal_information_types`).

    Synthetic fallback: if *information_types_data* is absent or empty a single
    placeholder entry is generated so the export always yields valid OSCAL.

    Version delta (the one that matters for our export): OSCAL **1.0.x**
    requires ``confidentiality-impact`` / ``integrity-impact`` /
    ``availability-impact`` on each information-type; **1.1.x** dropped those
    from the required set. We always emit a well-formed information-type and add
    the impact triad when the target version needs it, so a single exporter
    satisfies both schema generations.

    Note (D-2 / #39): ``oscalPath`` values in the data contract are *descriptive
    metadata* that document where each field maps in the OSCAL schema — they are
    not directives that drive this exporter. The exporter owns the canonical
    output shape. See ``compliance/tests/test_contract_export_consistency.py``
    (``test_ssp_export_covers_all_oscal_path_roots``) for the structural guard
    that ensures the exporter output stays consistent with the declared paths.
    """
    if information_types_data:
        return list(information_types_data)

    info: Dict[str, Any] = {
        "uuid": _coerce_uuid(None, seed=f"infotype:{system_name}"),
        "title": f"{system_name} information",
        "description": "Information types not itemized in source SSP.",
    }
    if oscal_version.startswith("1.0"):
        impact = {"base": fips}
        info["confidentiality-impact"] = impact
        info["integrity-impact"] = impact
        info["availability-impact"] = impact
    return [info]


def _information_types(
    system_name: str,
    fips: str,
    oscal_version: str,
    *,
    sys_fm: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Resolve the ``information-types[]`` list for an SSP export.

    Reads ``sys_fm["information_types"]`` when present; otherwise delegates to
    the synthetic-fallback path in :func:`_information_type`.
    """
    real_data: Optional[List[Dict[str, Any]]] = None
    if sys_fm:
        candidate = sys_fm.get("information_types")
        if isinstance(candidate, list) and candidate:
            real_data = candidate
    return _information_type(
        system_name, fips, oscal_version, information_types_data=real_data
    )


# Titles for the three canonical FedRAMP metadata roles the exporter wires into
# ``responsible-parties``. A ``role-id`` is a reference, so each one must also be
# declared in ``metadata.roles[]``.
_FEDRAMP_ROLE_TITLES = {
    "system-owner": "System Owner",
    "authorizing-official": "Authorizing Official",
    "information-system-security-officer": ("Information System Security Officer"),
}


def _oscal_parties_and_responsible(
    party_sidecars: List[Dict[str, Any]],
    sys_fm: Dict[str, Any],
    plan_seed: str,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build OSCAL ``metadata.parties[]`` + ``metadata.responsible-parties[]``
    from the vault's ``oscal.party`` notes (D-1 / #38).

    Each party note becomes an OSCAL party (type organization/person). The
    system note's ``system_owner`` / ``authorizing_official`` /
    ``isso`` wikilink fields are matched back to a party by name so the three
    canonical FedRAMP roles are wired into ``responsible-parties``.

    Returns ``(parties, responsible_parties)`` — either may be empty.
    """
    parties: List[Dict[str, Any]] = []
    responsible: List[Dict[str, Any]] = []
    seen_roles: set = set()
    # name (lowercased) -> party-uuid, for responsible-party resolution.
    name_to_uuid: Dict[str, str] = {}

    for sidecar in party_sidecars:
        pfm = sidecar.get("frontmatter", sidecar)
        name = str(pfm.get("name") or pfm.get("title") or "").strip()
        if not name:
            continue
        raw_type = str(pfm.get("org_type") or pfm.get("party_type") or "").lower()
        party_type = (
            "person" if raw_type in {"person", "individual"} else "organization"
        )
        puuid = _coerce_uuid(pfm.get("uuid"), seed=f"party:{name}")
        entry: Dict[str, Any] = {
            "uuid": puuid,
            "type": party_type,
            "name": name,
        }
        acronym = pfm.get("acronym")
        if acronym:
            entry["short-name"] = str(acronym)
        raw_emails = pfm.get("email_addresses") or pfm.get("email_address")
        if raw_emails:
            emails = raw_emails if isinstance(raw_emails, list) else [raw_emails]
            normalized_emails = [
                str(email).strip() for email in emails if str(email).strip()
            ]
            if normalized_emails:
                entry["email-addresses"] = normalized_emails
        raw_phones = pfm.get("telephone_numbers") or pfm.get("phone")
        if raw_phones:
            phones = raw_phones if isinstance(raw_phones, list) else [raw_phones]
            normalized_phones = []
            for phone in phones:
                if isinstance(phone, dict):
                    number = str(phone.get("number") or "").strip()
                    phone_type = str(phone.get("type") or "").strip()
                else:
                    number = str(phone).strip()
                    phone_type = ""
                if not number:
                    continue
                telephone = {"number": number}
                if phone_type:
                    telephone["type"] = phone_type
                normalized_phones.append(telephone)
            if normalized_phones:
                entry["telephone-numbers"] = normalized_phones
        parties.append(entry)
        name_to_uuid[name.lower()] = puuid
        raw_role_ids = pfm.get("role_ids") or []
        if isinstance(raw_role_ids, str):
            raw_role_ids = [raw_role_ids]
        for raw_role_id in raw_role_ids:
            role_id = _oscal_role_id(str(raw_role_id))
            if not role_id or role_id in seen_roles:
                continue
            seen_roles.add(role_id)
            responsible.append({"role-id": role_id, "party-uuids": [puuid]})

    def _wikilink_name(value: Any) -> Optional[str]:
        """Extract the display name from an Organizations wikilink or plain str."""
        if not value:
            return None
        text = str(value).strip()
        # [[Organizations/slug|Display Name]] or [[Organizations/slug]]
        import re

        m = re.search(r"\[\[[^\]|]*\|([^\]]+)\]\]", text)
        if m:
            return m.group(1).strip()
        m = re.search(r"\[\[[^\]/]*/([^\]|]+)\]\]", text)
        if m:
            return m.group(1).replace("-", " ").strip()
        return text

    for role_id, fm_key in (
        ("system-owner", "system_owner"),
        ("authorizing-official", "authorizing_official"),
        ("information-system-security-officer", "isso"),
    ):
        display = _wikilink_name(sys_fm.get(fm_key))
        if not display:
            continue
        party_uuid = name_to_uuid.get(display.lower())
        if not party_uuid:
            # Match by substring against known parties (wikilink slug vs name).
            for pname, puuid in name_to_uuid.items():
                if display.lower() in pname or pname in display.lower():
                    party_uuid = puuid
                    break
        if not party_uuid or role_id in seen_roles:
            continue
        seen_roles.add(role_id)
        responsible.append({"role-id": role_id, "party-uuids": [party_uuid]})

    return parties, responsible


def _extensions_of(
    extensions: List[Dict[str, Any]], note_type: str
) -> List[Dict[str, Any]]:
    """Return extension sidecars whose canonical ``type`` matches ``note_type``."""
    out: List[Dict[str, Any]] = []
    for note in extensions:
        data = note if isinstance(note, dict) else {}
        if (data.get("type") or data.get("note_type")) == note_type:
            out.append(data)
    return out


def _capability_control_implementation(
    implementation: Dict[str, Any], capability_name: str
) -> Dict[str, Any]:
    """Copy a component implementation into a capability UUID namespace.

    OSCAL permits the same control-id to be declared under both a concrete
    component and an offering capability, but every UUID remains document-wide
    unique. Deep-copy and deterministically reseed the control-implementation,
    requirements, and statements rather than reusing the component objects.
    """
    copied = copy.deepcopy(implementation)
    copied["uuid"] = _coerce_uuid(None, seed=f"capability-ci:{capability_name}")
    for ridx, requirement in enumerate(copied.get("implemented-requirements", [])):
        control_id = requirement.get("control-id", ridx)
        requirement["uuid"] = _coerce_uuid(
            None, seed=f"capability-ir:{capability_name}:{control_id}:{ridx}"
        )
        for sidx, statement in enumerate(requirement.get("statements", [])):
            statement_id = statement.get("statement-id", sidx)
            statement["uuid"] = _coerce_uuid(
                None,
                seed=(f"capability-stmt:{capability_name}:{statement_id}:{sidx}"),
            )
    return copied


def canonical_to_oscal_ssp(
    canonical_data: Dict[str, Any],
    plan_id: str = "",
    *,
    control_sidecars: Optional[List[Dict[str, Any]]] = None,
    system_data: Optional[Dict[str, Any]] = None,
    party_sidecars: Optional[List[Dict[str, Any]]] = None,
    extension_sidecars: Optional[List[Dict[str, Any]]] = None,
    lineage_events: Optional[List[Dict[str, Any]]] = None,
    preserved_extensions: Optional[List[Dict[str, Any]]] = None,
    lineage_derivations: Optional[List[Dict[str, Any]]] = None,
    oscal_version: str = DEFAULT_OSCAL_VERSION,
    allow_synthetic: bool = False,
) -> Dict[str, Any]:
    """Convert a vault SSP plan note into a full OSCAL ``system-security-plan``.

    The output is a complete, NIST-1.1.3-schema-valid document: metadata
    (including parties/roles/responsible-parties recovered from the vault's
    ``oscal.party`` notes), import-profile, system-characteristics (with
    system-information, security-impact-level, status, authorization-boundary,
    and -- when extension notes are supplied -- network-architecture and
    data-flow), system-implementation (users + a ``this-system`` component with
    protocols, plus leveraged-authorizations), a control-implementation whose
    ``implemented-requirements[]`` are built from the supplied control sidecars,
    and a back-matter of diagram resources.

    Args:
        canonical_data: the plan note's canonical JSON.
        plan_id: plan identifier (used for stable UUID seeds + system-id).
        control_sidecars: canonical JSON dicts for the plan's ``oscal.control``
            notes; each becomes an implemented-requirement.
        system_data: optional canonical JSON for the matching ``oscal.ssp``
            system note (supplies impact levels, boundary, name).
        party_sidecars: canonical JSON dicts for the plan's ``oscal.party``
            notes; each becomes an OSCAL ``metadata.parties[]`` entry, with
            system-owner / AO / security roles wired into
            ``metadata.responsible-parties[]`` (D-1 / #38).
        extension_sidecars: canonical JSON dicts for the plan's ``oscal.x.*``
            notes. These are mapped onto NATIVE OSCAL assemblies
            (network-architecture, data-flow, protocols, diagrams/back-matter,
            leveraged-authorizations) rather than carried as custom extensions,
            so the export maximizes OSCAL-core interoperability.
        oscal_version: OSCAL version to stamp (default 1.1.3).
        allow_synthetic: permit a documented placeholder requirement when the
            plan converted zero controls. Defaults to ``False`` so a production
            export fails closed instead of emitting a fabricated ``ac-1``.

    Raises:
        ControlConversionError: a control sidecar could not be converted.
        ControlParityError: the converted requirement count disagrees with the
            plan note's ``controls_parsed``.
        MissingControlsError: zero controls converted and ``allow_synthetic`` is
            ``False``.
    """
    fm = canonical_data.get("frontmatter", canonical_data)
    sys_fm = (system_data or {}).get("frontmatter", system_data or {})
    controls = control_sidecars or []
    extensions = extension_sidecars or []

    plan_seed = plan_id or fm.get("plan_id") or fm.get("title") or "ssp"
    ssp_uuid = _coerce_uuid(
        fm.get("uuid") or sys_fm.get("system_id"), seed=f"ssp:{plan_seed}"
    )

    # ---- this-system component (referenced by every by-component) ----------
    component_uuid = _coerce_uuid(None, seed=f"component:this-system:{plan_seed}")
    system_name = (
        sys_fm.get("name") or fm.get("system_name") or fm.get("title") or "System"
    )
    fips = sys_fm.get("fips_199") or fm.get("fips_199") or "low"
    status_state = _oscal_system_state(sys_fm.get("status") or fm.get("status"))

    # ---- native OSCAL assemblies recovered from oscal.x.* extension notes ---
    # Prefer OSCAL core fields over custom extensions for interoperability:
    # each extension note is mapped onto the assembly that models the same
    # concept (ADR / #46 follow-up).
    from compliance.lib import oscal_extension_bridge as _bridge
    from compliance.lib.oscal_lineage import runtime_derivation

    boundary_notes = _extensions_of(extensions, "oscal.x.boundary")
    relationship_notes = _extensions_of(extensions, "oscal.x.relationship")
    diagram_notes = _extensions_of(extensions, "oscal.x.diagram")
    inheritance_notes = _extensions_of(extensions, "oscal.x.inheritance")

    trace_sink = lineage_events if lineage_events is not None else []
    preserved_sink = preserved_extensions if preserved_extensions is not None else []
    network_architecture = None
    for note_index, note in enumerate(boundary_notes):
        result = _bridge.network_architecture_from_boundary_traced(note)
        trace_sink.extend(event.to_dict() for event in result.events)
        if result.value and network_architecture is None:
            network_architecture = result.value
            if lineage_derivations is not None:
                lineage_derivations.append(
                    runtime_derivation(
                        mapping_source="oscal_x_boundary.description",
                        source_ids=[f"boundary-note:{note_index}:network-architecture"],
                        canonical_pointers=["/body_sections"],
                        oscal_pointers=[
                            "/system-security-plan/system-characteristics/network-architecture/description"
                        ],
                        events=[event.to_dict() for event in result.events],
                    )
                )
    data_flow = None
    for note_index, note in enumerate(relationship_notes):
        result = _bridge.data_flow_from_relationship_traced(note)
        trace_sink.extend(event.to_dict() for event in result.events)
        if result.value and data_flow is None:
            data_flow = result.value
            if lineage_derivations is not None:
                lineage_derivations.append(
                    runtime_derivation(
                        mapping_source="oscal_x_relationship.interconnections",
                        source_ids=[f"relationship-note:{note_index}:interconnections"],
                        canonical_pointers=["/body_sections"],
                        oscal_pointers=[
                            "/system-security-plan/system-characteristics/data-flow/description"
                        ],
                        events=[event.to_dict() for event in result.events],
                    )
                )
    recovered_users: List[Dict[str, Any]] = []
    for note_index, note in enumerate(boundary_notes):
        result = _bridge.users_from_boundary_traced(note)
        recovered_users.extend(result.value)
        trace_sink.extend(event.to_dict() for event in result.events)
        if result.value and lineage_derivations is not None:
            lineage_derivations.append(
                runtime_derivation(
                    mapping_source="oscal_x_boundary.types_of_users",
                    source_ids=[f"boundary-note:{note_index}:types-of-users"],
                    canonical_pointers=["/body_sections"],
                    oscal_pointers=[
                        "/system-security-plan/system-implementation/users"
                    ],
                    events=[event.to_dict() for event in result.events],
                )
            )
    protocols: List[Dict[str, Any]] = []
    for note_index, note in enumerate(relationship_notes):
        result = _bridge.protocols_from_relationship_traced(note)
        protocols.extend(result.value)
        trace_sink.extend(event.to_dict() for event in result.events)
        preserved_sink.extend(result.preserved_extensions)
        if result.value and lineage_derivations is not None:
            lineage_derivations.append(
                runtime_derivation(
                    mapping_source="oscal_x_relationship.ports_protocols_services",
                    source_ids=[
                        f"relationship-note:{note_index}:ports-protocols-services"
                    ],
                    canonical_pointers=["/body_sections"],
                    oscal_pointers=[
                        "/system-security-plan/system-implementation/components/0/protocols"
                    ],
                    events=[event.to_dict() for event in result.events],
                    preserved_extensions=list(result.preserved_extensions),
                )
            )
    diagrams: List[Dict[str, Any]] = []
    resources: List[Dict[str, Any]] = []
    for note in diagram_notes:
        d, r = _bridge.diagrams_and_resources_from_diagram(note)
        diagrams.extend(d)
        resources.extend(r)
    inheritance_result = _bridge.leveraged_authorizations_from_inheritance_traced(
        inheritance_notes
    )
    leveraged_auths, leveraged_parties = inheritance_result.value
    trace_sink.extend(event.to_dict() for event in inheritance_result.events)
    if leveraged_auths and lineage_derivations is not None:
        lineage_derivations.append(
            runtime_derivation(
                mapping_source="oscal_x_inheritance.provided_by",
                source_ids=[
                    f"inheritance-note:{index}:provided-by"
                    for index in range(len(inheritance_notes))
                ],
                canonical_pointers=["/body_sections"],
                oscal_pointers=[
                    "/system-security-plan/system-implementation/leveraged-authorizations"
                ],
                events=[event.to_dict() for event in inheritance_result.events],
            )
        )

    this_system_component = {
        "uuid": component_uuid,
        "type": "this-system",
        "title": system_name,
        "description": (
            sys_fm.get("authorization_boundary") or f"The {system_name} system."
        ),
        "status": {"state": status_state},
    }
    if protocols:
        this_system_component["protocols"] = protocols

    # ---- implemented-requirements ------------------------------------------
    implemented_reqs: List[Dict[str, Any]] = []
    # role-id -> title for every role a control references. These must be
    # declared in metadata.roles[] or the emitted responsible-roles references
    # resolve to nothing.
    role_titles: Dict[str, str] = {}
    for index, cdata in enumerate(controls):
        try:
            implemented_reqs.append(
                control_to_oscal_implemented_requirement(
                    cdata,
                    component_uuid=component_uuid,
                    role_titles=role_titles,
                    lineage_events=trace_sink,
                    lineage_derivations=lineage_derivations,
                )
            )
        except Exception as exc:
            control_id = (cdata.get("frontmatter", cdata) or {}).get(
                "control_id", index
            )
            raise ControlConversionError(
                f"cannot export control {control_id!r}: {exc}"
            ) from exc

    # Parity gate: the plan note records how many controls the source document
    # yielded. A conversion that produces a different number means a control was
    # dropped (or duplicated) between the vault and the export, which schema
    # validation cannot see. Fail rather than emit a quietly short document.
    declared = fm.get("controls_parsed", sys_fm.get("controls_parsed"))
    if isinstance(declared, bool):
        declared = None
    if isinstance(declared, int) and declared != len(implemented_reqs):
        raise ControlParityError(
            f"plan declares controls_parsed={declared} but "
            f"{len(implemented_reqs)} implemented-requirement(s) were converted"
        )

    if not implemented_reqs:
        if not allow_synthetic:
            raise MissingControlsError(
                "no real controls were captured; synthetic fallback is disabled "
                "(pass allow_synthetic=True / --allow-synthetic for a "
                "development-only placeholder export)"
            )
        # control-implementation requires a non-empty implemented-requirements
        # array; emit a documented placeholder rather than an invalid empty one.
        # Marked with an explicit `synthetic` prop (D-11 / #46) so a downstream
        # OSCAL consumer can distinguish it from a control the source SSP
        # actually contained.
        implemented_reqs.append(
            {
                "uuid": _coerce_uuid(None, seed=f"ir:placeholder:{plan_seed}"),
                "control-id": "ac-1",
                "props": [
                    _prop("implementation-status", "planned"),
                    _prop("synthetic", "true"),
                ],
                "remarks": (
                    "SYNTHETIC placeholder: no controls were captured from the "
                    "source SSP. This requirement was fabricated only to satisfy "
                    "the OSCAL schema's non-empty implemented-requirements[] "
                    "constraint and does not represent a real control."
                ),
                "by-components": [
                    {
                        "component-uuid": component_uuid,
                        "uuid": _coerce_uuid(
                            None, seed=f"bycomp:placeholder:{plan_seed}"
                        ),
                        "description": "No controls captured from source SSP.",
                    }
                ],
            }
        )

    # ---- assemble ----------------------------------------------------------
    parties, responsible_parties = _oscal_parties_and_responsible(
        party_sidecars or [], sys_fm, plan_seed
    )
    # Parties synthesized for leveraged authorizations must appear in
    # metadata.parties[] so the leveraged-authorization party-uuid resolves.
    if leveraged_parties:
        parties = list(parties) + leveraged_parties
    # Every role referenced anywhere in the document must be declared here:
    # the three FedRAMP metadata roles plus whatever per-control
    # responsible-roles the vault carried.
    for responsible_party in responsible_parties:
        role_id = responsible_party.get("role-id")
        if role_id:
            role_titles.setdefault(
                role_id, _FEDRAMP_ROLE_TITLES.get(role_id) or role_id.replace("-", " ")
            )
    roles = [
        {"id": role_id, "title": title}
        for role_id, title in sorted(role_titles.items())
    ]

    metadata: Dict[str, Any] = {
        "title": fm.get("title", "System Security Plan"),
        "last-modified": _oscal_timestamp(fm.get("reviewed") or fm.get("date")),
        "version": str(fm.get("version", "1.0")),
        "oscal-version": oscal_version,
    }
    if roles:
        metadata["roles"] = roles
    if parties:
        metadata["parties"] = parties
    if responsible_parties:
        metadata["responsible-parties"] = responsible_parties

    plan: Dict[str, Any] = {
        "uuid": ssp_uuid,
        "metadata": metadata,
        "import-profile": {
            "href": fm.get("profile_ref")
            or fm.get("import_profile")
            or "#profile-not-specified"
        },
        "system-characteristics": {
            "system-ids": [{"identifier-type": "https://fedramp.gov", "id": plan_seed}],
            "system-name": system_name,
            "description": _plan_description(canonical_data, system_name),
            "security-sensitivity-level": fips,
            "system-information": {
                "information-types": _information_types(
                    system_name, fips, oscal_version, sys_fm=sys_fm
                )
            },
            "security-impact-level": {
                "security-objective-confidentiality": sys_fm.get(
                    "security_objective_confidentiality", fips
                ),
                "security-objective-integrity": sys_fm.get(
                    "security_objective_integrity", fips
                ),
                "security-objective-availability": sys_fm.get(
                    "security_objective_availability", fips
                ),
            },
            "status": {"state": status_state},
            "authorization-boundary": {
                "description": (
                    sys_fm.get("authorization_boundary")
                    or "Authorization boundary not itemized in source SSP."
                )
            },
        },
        "system-implementation": {
            "users": recovered_users
            or [
                {
                    "uuid": _coerce_uuid(None, seed=f"user:generic:{plan_seed}"),
                    "title": "System users",
                    "remarks": "Users not itemized in source SSP.",
                }
            ],
            "components": [this_system_component],
        },
        "control-implementation": {
            "description": "Control implementation generated from vault.",
            "implemented-requirements": implemented_reqs,
        },
    }

    # Native OSCAL assemblies recovered from extension notes (interoperability
    # over custom extensions). Each is attached only when non-empty.
    sc = plan["system-characteristics"]
    if network_architecture:
        sc["network-architecture"] = network_architecture
    if data_flow:
        sc["data-flow"] = data_flow
    if diagrams:
        sc["authorization-boundary"]["diagrams"] = diagrams
    if leveraged_auths:
        plan["system-implementation"]["leveraged-authorizations"] = leveraged_auths
    if resources:
        plan["back-matter"] = {"resources": resources}

    # Preserve leftover plan/system metadata as OSCAL props on metadata,
    # de-duplicated by (name, value) since plan and system notes overlap.
    ext_props = _extension_props(fm) + _extension_props(sys_fm)
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for p in ext_props:
        key = (p["name"], p["value"])
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    if deduped:
        plan["metadata"]["props"] = deduped

    return {"system-security-plan": plan}


def _oscal_system_state(raw: Optional[str]) -> str:
    """Map a vault status onto OSCAL system status states (operational,
    under-development, under-major-modification, disposition, other)."""
    value = (raw or "").strip().lower()
    allowed = {
        "operational",
        "under-development",
        "under-major-modification",
        "disposition",
        "other",
    }
    if value in allowed:
        return value
    if value in {"active", "authorized"}:
        return "operational"
    return "operational"


def _oscal_timestamp(raw: Optional[str]) -> str:
    """Return an OSCAL date-time. Falls back to a fixed epoch marker when the
    vault has no reviewed/date value, so the field is always populated (the
    schema requires metadata.last-modified as a date-time-with-timezone).
    """
    if raw:
        text = str(raw)
        # bare date -> midnight UTC
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return f"{text}T00:00:00Z"
        return text
    return "1970-01-01T00:00:00Z"


def _plan_description(canonical_data: Dict[str, Any], system_name: str) -> str:
    body_secs = canonical_data.get("body_sections") or []
    for sec in body_secs:
        content = (sec.get("content") or "").strip()
        if content:
            return content
    return f"System Security Plan for {system_name}."


def canonical_to_oscal_component_definition(
    canonical_data: Dict[str, Any],
    *,
    control_sidecars: Optional[List[Dict[str, Any]]] = None,
    oscal_version: str = DEFAULT_OSCAL_VERSION,
) -> Dict[str, Any]:
    """Convert a vault component note into an OSCAL `component-definition`.

    Produces a NIST-1.1.3-schema-valid document. Note: OSCAL's
    ``defined-component`` does NOT carry a ``status`` field (unlike an SSP
    system-component); the vault status is preserved as a prop instead.

    When ``control_sidecars`` are supplied, the component gains a native
    ``control-implementations[]`` built from the same per-control conversion the
    SSP path uses, so the control implementation detail is expressed in OSCAL
    core rather than left only in the SSP (interoperability over duplication).
    """
    fm = canonical_data.get("frontmatter", canonical_data)
    body_sections = canonical_data.get("body_sections", [])

    component_name = fm.get("name", fm.get("title", "Component"))
    component_type = fm.get("component_type", "service")
    description = ""
    for sec in body_sections:
        if (sec.get("content") or "").strip():
            description = sec["content"].strip()
            break
    if not description:
        description = f"{component_name} component."

    cd_uuid = _coerce_uuid(fm.get("uuid"), seed=f"cd:{component_name}")
    comp_uuid = _coerce_uuid(None, seed=f"defined-component:{component_name}")

    component: Dict[str, Any] = {
        "uuid": comp_uuid,
        "type": component_type,
        "title": component_name,
        "description": description,
    }
    # defined-component has no status field; carry vault status + leftovers as props.
    props: List[Dict[str, Any]] = []
    if fm.get("status"):
        props.append(_prop("status", fm.get("status")))

    # Wire real component inventory (D-5 / #26): when the heading_parser has
    # enriched the component note frontmatter, surface inventory fields as
    # discrete OSCAL props so they are not silently dropped on export.
    _INVENTORY_PROP_MAP = [
        ("inventory_items", "inventory-item"),
        ("software_inventory", "software-inventory"),
        ("external_services", "external-service"),
        ("aws_services_in_use", "aws-service"),
    ]
    for fm_key, prop_name in _INVENTORY_PROP_MAP:
        items = fm.get(fm_key)
        if not items:
            continue
        if isinstance(items, list):
            for item in items:
                if item is not None and str(item).strip():
                    props.append(_prop(prop_name, item))
        else:
            if str(items).strip():
                props.append(_prop(prop_name, items))

    # Fact-sheet table values have a natural OSCAL representation as discrete
    # props. Never stringify the record or row containers into one prop value.
    for record in canonical_data.get("table_records", []) or []:
        if (
            record.get("kind") != "external_service"
            or record.get("disposition") != "mapped"
        ):
            continue
        headers = record.get("headers") or []
        rows = record.get("rows") or []
        if headers != ["Service Element", "Response"]:
            continue
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                continue
            label, value = (str(cell).strip() for cell in row)
            prop_name = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
            if prop_name and value:
                props.append(_prop(f"external-service-{prop_name}", value))

    props.extend(_extension_props(fm))
    if props:
        component["props"] = props

    # Native control-implementations (interoperability over leaving control
    # detail only in the SSP). The component-definition requirement/statement
    # schema requires a `description`, unlike the SSP variant, so build a
    # component-flavored requirement here reusing the shared field helpers.
    controls = control_sidecars or []
    capabilities: List[Dict[str, Any]] = []
    if controls:
        requirements: List[Dict[str, Any]] = []
        for index, cdata in enumerate(controls):
            cfm = cdata.get("frontmatter", cdata)
            raw_id = cfm.get("control_id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                continue
            control_id = _oscal_control_id(raw_id)
            req: Dict[str, Any] = {
                "uuid": _coerce_uuid(
                    cfm.get("uuid"), seed=f"cd-ir:{component_name}:{control_id}"
                ),
                "control-id": control_id,
                "description": (
                    cfm.get("implementation_narrative")
                    or f"{component_name} implementation of {control_id}."
                ),
            }
            set_params = _oscal_set_parameters(
                cfm.get("parameters") or cfm.get("set_parameters")
            )
            if set_params:
                req["set-parameters"] = set_params
            statements = []
            for sidx, sid in enumerate(cfm.get("statement_ids") or []):
                statements.append(
                    {
                        "statement-id": str(sid),
                        "uuid": _coerce_uuid(
                            None, seed=f"cd-stmt:{component_name}:{sid}:{sidx}"
                        ),
                        "description": f"See {component_name} narrative for {sid}.",
                    }
                )
            if statements:
                req["statements"] = statements
            requirements.append(req)
        if requirements:
            control_implementation = {
                "uuid": _coerce_uuid(None, seed=f"cd-ci:{component_name}"),
                "source": fm.get("profile_ref")
                or fm.get("import_profile")
                or "#profile-not-specified",
                "description": (
                    f"Controls implemented by the {component_name} component."
                ),
                "implemented-requirements": requirements,
            }
            component["control-implementations"] = [control_implementation]

            # An OSCAL capability is the native equivalent of an OSBAPI service
            # offering/plan: it incorporates the concrete component and
            # declares the controls that offering can provide. Keep the same
            # implementation requirements under the capability so a catalog
            # consumer can discover the offering without interpreting custom
            # vault extensions.
            capability = {
                "uuid": _coerce_uuid(None, seed=f"capability:{component_name}"),
                "name": fm.get("capability_name")
                or f"{component_name} service capability",
                "description": fm.get("capability_description")
                or f"Controls provided by the {component_name} component.",
                "incorporates-components": [
                    {
                        "component-uuid": comp_uuid,
                        "description": f"Incorporates {component_name}.",
                    }
                ],
                "control-implementations": [
                    _capability_control_implementation(
                        control_implementation, component_name
                    )
                ],
            }
            capabilities.append(capability)

    component_definition: Dict[str, Any] = {
        "component-definition": {
            "uuid": cd_uuid,
            "metadata": {
                "title": f"{component_name} Component Definition",
                "last-modified": _oscal_timestamp(fm.get("reviewed")),
                "version": str(fm.get("version", "1.0")),
                "oscal-version": oscal_version,
            },
            "components": [component],
        }
    }
    if capabilities:
        component_definition["component-definition"]["capabilities"] = capabilities
    return component_definition


def load_canonical_json(path: Path) -> Dict[str, Any]:
    """Load a canonical JSON sidecar file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_exportable_types() -> List[str]:
    """Return the list of OSCAL types that can be exported as standalone documents.

    Controls (`oscal.control`) map into SSP context, not standalone exports.
    """
    return [
        "oscal.ssp",
        "oscal.component_definition",
        "oscal.poam",
        "oscal.assessment_plan",
        "oscal.assessment_results",
        "oscal.profile",
        "oscal.catalog",
    ]
