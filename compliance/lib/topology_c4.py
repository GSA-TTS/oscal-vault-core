"""C4 (Mermaid) renderers for a parsed ``Topology``.

Level 1 (System Context): the system as one box, the people and external systems around
it, and the flows between them aggregated per counterpart.

Level 2 (Container): the application and platform boundaries with their processes and
data stores, and every PPS flow as a relationship.

Levels 3 (Component) and 4 (Code) are not derivable from a Ports, Protocols, and
Services table and are intentionally not generated.

Output is deterministic: elements and relationships are sorted, aliases are stable.
"""

from __future__ import annotations

import re

from .topology import (
    DATASTORE,
    HUMAN,
    PROCESS,
    ZONE_APP,
    ZONE_PLATFORM,
    Component,
    Flow,
    Topology,
)

SYSTEM_ALIAS = "system"


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace('"', "'")).strip()


def _alias(key: str) -> str:
    alias = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_") or "x"
    return alias if not alias[0].isdigit() else f"n_{alias}"


def _tech(flow: Flow) -> str:
    parts = [flow.protocol.upper() if flow.protocol else ""]
    if flow.port:
        parts.append(str(flow.port))
    tech = " / ".join(p for p in parts if p)
    return f"{tech} (unencrypted)" if not flow.encrypted else tech


def _rel(source: str, destination: str, label: str, tech: str) -> str:
    return f'Rel({source}, {destination}, "{_text(label)}", "{_text(tech)}")'


def _element(c: Component, indent: str) -> str:
    alias, name = _alias(c.key), _text(c.name)
    if c.kind == DATASTORE:
        tech = {
            "relational": "Relational DB",
            "key-value": "Key-value store",
            "object": "Object store",
        }.get(c.store_type or "", "Data store")
        return f'{indent}ContainerDb({alias}, "{name}", "{tech}")'
    return f'{indent}Container({alias}, "{name}", "{"Platform service" if c.zone == ZONE_PLATFORM else "Application process"}")'


def render_context(topo: Topology) -> str:
    """C4 Level 1: system, people, external systems, aggregated relationships."""
    inside = {
        c.key
        for c in topo.components
        if c.zone in (ZONE_APP, ZONE_PLATFORM)
        and c.kind in (PROCESS, DATASTORE, "platform-process")
    }
    connected = {
        k
        for f in topo.flows
        if (f.source in inside) != (f.destination in inside)
        for k in (f.source, f.destination)
    }
    outside = [c for c in topo.components if c.key not in inside and c.key in connected]
    lines = ["C4Context", f"    title {_text(topo.display)} - System Context", ""]
    for c in sorted(outside, key=lambda c: (c.kind != HUMAN, c.name.lower())):
        if c.kind == HUMAN:
            lines.append(f'    Person({_alias(c.key)}, "{_text(c.name)}")')
        else:
            lines.append(
                f'    System_Ext({_alias(c.key)}, "{_text(c.name)}", "External system")'
            )
    lines.append(
        f'    System({SYSTEM_ALIAS}, "{_text(topo.display)}", "System under authorization")'
    )
    lines.append("")

    grouped: dict[tuple[str, str], list[Flow]] = {}
    for f in topo.flows:
        a, b = f.source in inside, f.destination in inside
        if a == b:
            continue
        grouped.setdefault((f.source, f.destination), []).append(f)
    rels = []
    for (src, dst), flows in grouped.items():
        s = SYSTEM_ALIAS if src in inside else _alias(src)
        d = SYSTEM_ALIAS if dst in inside else _alias(dst)
        purposes = sorted({_text(f.purpose or f.label) for f in flows})
        techs = sorted({_tech(f) for f in flows if _tech(f)})
        rels.append(_rel(s, d, "; ".join(purposes[:2]), "; ".join(techs)))
    lines.extend(f"    {r}" for r in sorted(set(rels)))
    lines += ["", '    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")']
    return "\n".join(lines) + "\n"


def render_container(topo: Topology) -> str:
    """C4 Level 2: boundaries, processes, data stores and every flow."""
    lines = ["C4Container", f"    title {_text(topo.display)} - Containers", ""]
    by_zone: dict[str, list[Component]] = {}
    for c in topo.components:
        by_zone.setdefault(c.zone, []).append(c)

    for c in sorted(by_zone.get("users", []), key=lambda c: c.name.lower()):
        lines.append(f'    Person({_alias(c.key)}, "{_text(c.name)}")')
    for c in sorted(by_zone.get("external", []), key=lambda c: c.name.lower()):
        lines.append(
            f'    System_Ext({_alias(c.key)}, "{_text(c.name)}", "External system")'
        )
    lines.append("")
    for zone, label, boundary in (
        (ZONE_PLATFORM, "cloud.gov / AWS platform services", "System_Boundary"),
        (ZONE_APP, f"{_text(topo.display)} application boundary", "System_Boundary"),
    ):
        members = sorted(by_zone.get(zone, []), key=lambda c: c.name.lower())
        if not members:
            continue
        lines.append(f'    {boundary}({zone}_boundary, "{label}") {{')
        lines.extend(_element(c, "        ") for c in members)
        lines.append("    }")
        lines.append("")

    rels = [
        _rel(_alias(f.source), _alias(f.destination), f.purpose or f.label, _tech(f))
        for f in topo.flows
    ]
    lines.extend(f"    {r}" for r in sorted(set(rels)))
    lines += ["", '    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="2")']
    return "\n".join(lines) + "\n"
