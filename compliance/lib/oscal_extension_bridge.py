"""Map ``oscal.x.*`` extension notes onto native OSCAL assemblies.

The vault captures FedRAMP SSP Chapters 1-12 content that the primary control
parser drops into typed extension notes (``oscal.x.boundary``,
``oscal.x.relationship``, ``oscal.x.diagram``, ``oscal.x.inheritance``, ...).
Historically those notes had no first-class OSCAL home, so the exported SSP
omitted them. Per the project decision to prefer OSCAL core fields over custom
extensions for interoperability, this module converts each extension note into
the native OSCAL assembly that models the same concept:

    oscal.x.boundary       -> system-characteristics.network-architecture
                              + system-implementation.users (types of users)
    oscal.x.relationship   -> system-characteristics.data-flow
                              + this-system component ``protocols[]``
    oscal.x.diagram        -> authorization-boundary.diagrams[]
                              + back-matter.resources[] (the media)
    oscal.x.inheritance    -> system-implementation.leveraged-authorizations[]

Each converter is defensive: a note that lacks the relevant section simply
yields ``None``/empty, so a sparse vault never produces an invalid document.
The functions are pure (no I/O) and return plain dicts ready to splice into the
``canonical_to_oscal_ssp`` output.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from compliance.lib.transforms import (
    AdapterResult,
    TransformEvent,
    filter_generated_recovery_callout,
    parse_gfm_table,
    parse_markdown_list,
    parse_port_expression,
)


def _sections(note: Dict[str, Any]) -> Dict[str, str]:
    """Return ``{heading_lowercased: content}`` for a note's body sections."""
    out: Dict[str, str] = {}
    for section in note.get("body_sections", []) or []:
        heading = (section.get("heading") or "").strip()
        if heading:
            out[heading.lower()] = section.get("content") or ""
    return out


def _find_section(note: Dict[str, Any], *needles: str) -> Optional[str]:
    """Return the first section whose heading contains any needle."""
    sections = _sections(note)
    for heading, content in sections.items():
        if any(n.lower() in heading for n in needles):
            return content
    return None


def _strip_callout(text: str) -> str:
    """Compatibility wrapper for generated provenance-callout filtering."""
    return filter_generated_recovery_callout(text).value


def _parse_markdown_table(text: str) -> List[Dict[str, str]]:
    """Parse a GitHub-flavored markdown table into a list of row dicts.

    Returns ``[]`` if no pipe-table is present. The header row supplies keys;
    the separator row (``| --- |``) is skipped.
    """
    return parse_gfm_table(text).value


def _is_table_like(text: str) -> bool:
    """Return whether text contains a possible Markdown table block."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return sum("|" in line for line in lines) >= 2


# ---------------------------------------------------------------------------
# oscal.x.boundary -> network-architecture + users
# ---------------------------------------------------------------------------


def network_architecture_from_boundary(
    note: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Build ``system-characteristics.network-architecture`` from a boundary note."""
    return network_architecture_from_boundary_traced(note).value


def network_architecture_from_boundary_traced(
    note: Dict[str, Any],
) -> AdapterResult[Optional[Dict[str, Any]]]:
    content = _find_section(note, "network architecture")
    if content is None:
        return AdapterResult(None)
    result = filter_generated_recovery_callout(content)
    return AdapterResult(
        {"description": result.value} if result.value else None,
        result.events,
    )


def users_from_boundary(note: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build ``system-implementation.users[]`` from the Types of Users section.

    Each table row (or bullet) becomes a ``system-user`` with a stable UUID and
    the row text carried as ``authorized-privileges[].title`` when present.
    """
    return users_from_boundary_traced(note).value


def users_from_boundary_traced(
    note: Dict[str, Any],
) -> AdapterResult[List[Dict[str, Any]]]:
    from compliance.lib.export_oscal import _coerce_uuid

    content = _find_section(note, "types of users")
    if content is None:
        return AdapterResult([])
    events: List[TransformEvent] = []
    table_result = parse_gfm_table(content)
    events.extend(table_result.events)
    users: List[Dict[str, Any]] = []
    if table_result.value:
        for index, row in enumerate(table_result.value):
            title = (
                row.get("Role")
                or row.get("Type")
                or row.get("User Type")
                or row.get("Type of User")
                or next(iter(row.values()), "")
            ).strip()
            if not title:
                continue
            privileges = [
                {
                    "title": f"{key}: {value}".strip(": ").strip(),
                    "functions-performed": [value.strip()],
                }
                for key, value in row.items()
                if value.strip() and key.lower() not in {"role", "type"}
            ]
            user: Dict[str, Any] = {
                "uuid": _coerce_uuid(None, seed=f"user:{title}:{index}"),
                "title": title,
            }
            if privileges:
                user["authorized-privileges"] = privileges
            users.append(user)
        if len(users) > 1:
            events.append(
                TransformEvent(
                    operator="record_fan_out",
                    input_sha256=table_result.events[0].input_sha256,
                    output_sha256=table_result.events[0].output_sha256,
                    changed=True,
                    lossiness="none",
                    consumed=tuple(user["title"] for user in users),
                )
            )
    elif _is_table_like(content):
        return AdapterResult([])
    else:
        filtered = filter_generated_recovery_callout(content)
        events.extend(filtered.events)
        list_result = parse_markdown_list(filtered.value)
        events.extend(list_result.events)
        for index, line in enumerate(list_result.value):
            if line:
                users.append(
                    {
                        "uuid": _coerce_uuid(None, seed=f"user:{line}:{index}"),
                        "title": line[:200],
                    }
                )
    return AdapterResult(users, tuple(events))


# ---------------------------------------------------------------------------
# oscal.x.relationship -> data-flow + protocols
# ---------------------------------------------------------------------------


def data_flow_from_relationship(
    note: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Build ``system-characteristics.data-flow`` from a relationship note."""
    return data_flow_from_relationship_traced(note).value


def data_flow_from_relationship_traced(
    note: Dict[str, Any],
) -> AdapterResult[Optional[Dict[str, Any]]]:
    content = _find_section(
        note, "data flow", "interconnection", "system interconnection"
    )
    if content is None:
        return AdapterResult(None)
    result = filter_generated_recovery_callout(content)
    return AdapterResult(
        {"description": result.value} if result.value else None,
        result.events,
    )


def protocols_from_relationship(note: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build ``protocols[]`` from the Ports, Protocols, and Services table."""
    return protocols_from_relationship_traced(note).value


def protocols_from_relationship_traced(
    note: Dict[str, Any],
) -> AdapterResult[List[Dict[str, Any]]]:
    content = _find_section(note, "ports, protocols", "ports protocols")
    if content is None:
        return AdapterResult([])
    table_result = parse_gfm_table(content)
    events: List[TransformEvent] = list(table_result.events)
    protocols: List[Dict[str, Any]] = []
    preserved: List[dict] = []
    for row_index, row in enumerate(table_result.value):
        protocol_name = (row.get("Protocols") or row.get("Protocol") or "").strip()
        ports_raw = (row.get("Ports (T or U)") or row.get("Ports") or "").strip()
        if not protocol_name and not ports_raw:
            continue
        port_result = parse_port_expression(ports_raw)
        events.extend(port_result.events)
        residual = port_result.events[0].residual
        if residual:
            preserved.append(
                {
                    "name": "unparsed-port-expression",
                    "row_index": row_index,
                    "value": ports_raw,
                    "residual": list(residual),
                }
            )
        protocol: Dict[str, Any] = {"name": protocol_name or ports_raw or "service"}
        service = (row.get("Services") or row.get("Service") or "").strip()
        if service:
            protocol["title"] = service[:200]
        ranges = [item.to_oscal() for item in port_result.value]
        if ranges:
            protocol["port-ranges"] = ranges
        protocols.append(protocol)
    if len(protocols) > 1 and table_result.events:
        events.append(
            TransformEvent(
                operator="record_fan_out",
                input_sha256=table_result.events[0].input_sha256,
                output_sha256=table_result.events[0].output_sha256,
                changed=True,
                lossiness="none",
                consumed=tuple(protocol["name"] for protocol in protocols),
            )
        )
    return AdapterResult(protocols, tuple(events), tuple(preserved))


# ---------------------------------------------------------------------------
# oscal.x.diagram -> authorization-boundary.diagrams + back-matter.resources
# ---------------------------------------------------------------------------


def diagrams_and_resources_from_diagram(
    note: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build ``(diagrams[], resources[])`` from a diagram note.

    Each referenced media file becomes a ``back-matter`` resource (with an
    ``rlink`` to the vault attachment) and an authorization-boundary diagram
    that links to that resource by UUID, so the two are cross-referenced the
    way OSCAL expects.
    """
    from compliance.lib.export_oscal import _coerce_uuid

    fm = note.get("frontmatter", note)
    media = fm.get("media_files") or []
    if not isinstance(media, list):
        media = [media]
    # Fall back to parsing ![[embed]] references from the Diagrams section.
    if not media:
        content = _find_section(note, "diagram") or ""
        media = re.findall(r"!\[\[([^\]]+)\]\]", content)

    diagrams: List[Dict[str, Any]] = []
    resources: List[Dict[str, Any]] = []
    for index, filename in enumerate(media):
        filename = str(filename).strip()
        if not filename:
            continue
        resource_uuid = _coerce_uuid(None, seed=f"resource:{filename}:{index}")
        resources.append(
            {
                "uuid": resource_uuid,
                "title": filename,
                "rlinks": [{"href": f"./diagrams/media/{filename}"}],
            }
        )
        diagrams.append(
            {
                "uuid": _coerce_uuid(None, seed=f"diagram:{filename}:{index}"),
                "description": f"Diagram recovered from source SSP: {filename}",
                "links": [
                    {"href": f"#{resource_uuid}", "rel": "diagram"},
                ],
            }
        )
    return diagrams, resources


# ---------------------------------------------------------------------------
# oscal.x.inheritance -> leveraged-authorizations
# ---------------------------------------------------------------------------


def leveraged_authorizations_from_inheritance(
    notes: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build ``(leveraged-authorizations[], parties[])`` from inheritance notes.

    Each inheritance note that names a provider yields a leveraged-authorization
    plus a party for the leveraged system's owner (OSCAL requires
    ``party-uuid`` on a leveraged-authorization). Returns the parties so the
    caller can merge them into ``metadata.parties[]``.
    """
    return leveraged_authorizations_from_inheritance_traced(notes).value


def leveraged_authorizations_from_inheritance_traced(
    notes: List[Dict[str, Any]],
) -> AdapterResult[tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    from compliance.lib.export_oscal import _coerce_uuid

    leveraged: List[Dict[str, Any]] = []
    parties: List[Dict[str, Any]] = []
    events: List[TransformEvent] = []
    for index, note in enumerate(notes):
        fm = note.get("frontmatter", note)
        title = str(fm.get("title") or "").strip()
        provided_by = _find_section(note, "provided by", "leveraged") or ""
        table_result = parse_gfm_table(provided_by)
        events.extend(table_result.events)
        if table_result.value:
            first_row = table_result.value[0]
            name = str(
                first_row.get("Provided By")
                or first_row.get("Provider")
                or next(iter(first_row.values()), "")
            ).strip()
        elif _is_table_like(provided_by):
            continue
        else:
            filtered = filter_generated_recovery_callout(provided_by)
            events.extend(filtered.events)
            name = filtered.value or title
        if not name:
            continue
        party_uuid = _coerce_uuid(None, seed=f"party:leveraged:{name}:{index}")
        parties.append({"uuid": party_uuid, "type": "organization", "name": name[:200]})
        leveraged.append(
            {
                "uuid": _coerce_uuid(None, seed=f"leveraged:{name}:{index}"),
                "title": name[:200],
                "party-uuid": party_uuid,
                "date-authorized": "2020-01-01",
            }
        )
    return AdapterResult((leveraged, parties), tuple(events))
