"""Parse hand-written C4 Mermaid and score generated topology diagrams against it.

The reference side is a Mermaid ``C4Context``/``C4Container`` block as drawn by a person
(or another agent). The generated side is a ``Topology`` rendered by this package. The
scorer reads its bands from the rubric YAML (docs/rubrics/topology-generator-rubric.yaml)
so thresholds are changed in one reviewed place, not in code.

Matching is by normalised name tokens. A generated element matches a reference element
when the smaller token set is contained in the larger one, so "Cloud.gov Redis" matches
both "API Redis" and "Admin Redis" (reported as an ambiguous match, not hidden).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .topology import DATASTORE, EXTERNAL, HUMAN, Topology
from .topology_c4 import render_container, render_context

_ELEMENT_KINDS = {
    "person": "person",
    "person_ext": "person",
    "system": "system",
    "system_ext": "external",
    "systemdb": "datastore",
    "systemdb_ext": "datastore",
    "container": "container",
    "container_ext": "external",
    "containerdb": "datastore",
    "containerdb_ext": "datastore",
    "component": "container",
    "componentdb": "datastore",
}
_BOUNDARIES = {
    "boundary",
    "enterprise_boundary",
    "system_boundary",
    "container_boundary",
}
_RELS = {"rel", "birel", "rel_u", "rel_d", "rel_l", "rel_r", "rel_back"}
_STOP = {
    "application",
    "app",
    "us",
    "the",
    "service",
    "services",
    "cloud",
    "gov",
    "aws",
    "amazon",
    "managed",
}
_CALL = re.compile(r"^\s*([A-Za-z_]+)\s*\((.*)\)\s*(\{)?\s*$")


@dataclass
class RefElement:
    alias: str
    label: str
    kind: str
    tech: str = ""
    boundary: str = ""


@dataclass
class RefRel:
    source: str
    destination: str
    label: str
    tech: str


@dataclass
class RefDiagram:
    level: str
    title: str
    elements: dict[str, RefElement] = field(default_factory=dict)
    rels: list[RefRel] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def split_args(text: str) -> list[str]:
    """Split a C4 call's argument list on commas outside double quotes."""
    out, cur, quoted = [], [], False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur).strip())
    return [a.strip().strip('"') for a in out]


def mermaid_blocks(markdown: str) -> list[str]:
    return re.findall(r"```mermaid\n(.*?)```", markdown, flags=re.S)


def parse_c4(block: str) -> RefDiagram:
    lines = [
        ln
        for ln in block.splitlines()
        if ln.strip() and not ln.strip().startswith("%%")
    ]
    header = lines[0].strip() if lines else ""
    diagram = RefDiagram(level=header, title="")
    stack: list[str] = []
    for line in lines[1:]:
        text = line.strip()
        if text.startswith("title "):
            diagram.title = text[6:].strip()
            continue
        if text == "}":
            if stack:
                stack.pop()
            else:
                diagram.errors.append("unbalanced closing brace")
            continue
        match = _CALL.match(text)
        if not match:
            continue
        name, args, opens = (
            match.group(1).lower(),
            split_args(match.group(2)),
            match.group(3),
        )
        if name in _BOUNDARIES:
            diagram.boundaries.append(args[1] if len(args) > 1 else args[0])
            if opens:
                stack.append(args[1] if len(args) > 1 else args[0])
        elif name in _ELEMENT_KINDS and len(args) >= 2:
            if args[0] in diagram.elements:
                diagram.errors.append(f"duplicate alias {args[0]}")
            diagram.elements[args[0]] = RefElement(
                args[0],
                args[1],
                _ELEMENT_KINDS[name],
                args[2] if len(args) > 3 else "",
                stack[-1] if stack else "",
            )
        elif name in _RELS and len(args) >= 3:
            diagram.rels.append(
                RefRel(args[0], args[1], args[2], args[3] if len(args) > 3 else "")
            )
    for rel in diagram.rels:
        for end in (rel.source, rel.destination):
            if end not in diagram.elements:
                diagram.errors.append(f"relationship references unknown alias {end}")
    if stack:
        diagram.errors.append("unclosed boundary")
    return diagram


def tokens(name: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", name.lower().replace(".", " "))
    kept = {w for w in words if w not in _STOP}
    return frozenset(kept or words)


def names_match(a: str, b: str) -> bool:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def _kind_of(component_kind: str) -> str:
    return {HUMAN: "person", EXTERNAL: "external", DATASTORE: "datastore"}.get(
        component_kind, "container"
    )


def load_rubric(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def band(value: float, bands: list[dict[str, Any]]) -> int:
    """Score for ``value``: the highest band whose ``min`` it reaches."""
    score = 0
    for entry in sorted(bands, key=lambda b: b["min"]):
        if value >= entry["min"]:
            score = entry["score"]
    return score


def _ratio(num: float, den: float) -> float:
    return 1.0 if den == 0 else num / den


def compare_c4(
    topo: Topology, ref: RefDiagram, level: str = "container"
) -> dict[str, Any]:
    """Metrics for one generated C4 level against one reference diagram."""
    people_or_ext = {"person", "external", "system"}
    ref_elements = {
        a: e
        for a, e in ref.elements.items()
        if level == "container" or e.kind in people_or_ext
    }
    gen = {c.key: c for c in topo.components}

    ref_match: dict[str, list[str]] = {a: [] for a in ref_elements}
    gen_match: dict[str, list[str]] = {k: [] for k in gen}
    for a, e in ref_elements.items():
        for k, c in gen.items():
            if (e.kind == "datastore") == (c.kind == DATASTORE) and names_match(
                e.label, c.name
            ):
                ref_match[a].append(k)
                gen_match[k].append(a)

    recalled = [a for a, m in ref_match.items() if m]
    precise = [k for k, m in gen_match.items() if m]
    typed_ok = sum(
        1
        for a in recalled
        if any(
            _kind_of(gen[k].kind) == ref_elements[a].kind
            or (
                ref_elements[a].kind in ("system", "container")
                and _kind_of(gen[k].kind) in ("container", "external")
            )
            for k in ref_match[a]
        )
    )

    gen_pairs = {(f.source, f.destination) for f in topo.flows}

    def linked(a: str, b: str) -> bool:
        return any(
            (x, y) in gen_pairs or (y, x) in gen_pairs
            for x in ref_match.get(a, [])
            for y in ref_match.get(b, [])
        )

    both = [
        r for r in ref.rels if ref_match.get(r.source) and ref_match.get(r.destination)
    ]
    ref_pairs = [(r.source, r.destination) for r in ref.rels]
    rel_hit = sum(1 for r in both if linked(r.source, r.destination))
    gen_supported = sum(
        1
        for f in topo.flows
        if any(
            (a, b) in ref_pairs or (b, a) in ref_pairs
            for a in gen_match.get(f.source, [])
            for b in gen_match.get(f.destination, [])
        )
    )

    with_tech = sum(1 for f in topo.flows if f.protocol)
    with_port = sum(1 for f in topo.flows if f.port)
    ambiguous = sorted(k for k, m in gen_match.items() if len(m) > 1)
    return {
        "reference": {
            "title": ref.title,
            "elements": len(ref_elements),
            "relationships": len(ref.rels),
            "syntax_errors": ref.errors,
        },
        "generated": {"elements": len(gen), "relationships": len(topo.flows)},
        "metrics": {
            "element_recall": _ratio(len(recalled), len(ref_elements)),
            "element_precision": _ratio(len(precise), len(gen)),
            "type_agreement": _ratio(typed_ok, len(recalled)),
            "relationship_recall": _ratio(rel_hit, len(both)),
            "relationship_recall_overall": _ratio(rel_hit, len(ref.rels)),
            "relationship_precision": _ratio(gen_supported, len(topo.flows)),
            "technology_coverage": _ratio(with_tech, len(topo.flows)),
            "port_coverage": _ratio(with_port, len(topo.flows)),
        },
        "findings": {
            "missing_from_generated": sorted(
                ref_elements[a].label for a in ref_elements if not ref_match[a]
            ),
            "extra_in_generated": sorted(gen[k].name for k in gen if not gen_match[k]),
            "ambiguous_matches": [gen[k].name for k in ambiguous],
            "reference_relationships_between_unmatched": len(ref.rels) - len(both),
        },
    }


def validate_generated_c4(text: str) -> dict[str, Any]:
    """Structural checks that apply to any C4 text, generated or hand-written."""
    diagram = parse_c4(text)
    used = {r.source for r in diagram.rels} | {r.destination for r in diagram.rels}
    orphans = sorted(a for a in diagram.elements if a not in used)
    return {
        "has_title": bool(diagram.title),
        "syntax_errors": diagram.errors,
        "orphan_elements": orphans,
        "elements": len(diagram.elements),
        "relationships": len(diagram.rels),
    }


def readability(topo: Topology, rendered: str) -> dict[str, float]:
    diagram = parse_c4(rendered)
    longest = max((len(r.label) for r in diagram.rels), default=0)
    degree: dict[str, int] = {}
    for r in diagram.rels:
        degree[r.source] = degree.get(r.source, 0) + 1
        degree[r.destination] = degree.get(r.destination, 0) + 1
    return {
        "elements": len(diagram.elements),
        "relationships": len(diagram.rels),
        "longest_label": longest,
        "max_degree": max(degree.values(), default=0),
    }


def consistency(
    topo: Topology, blueprint: dict[str, list[dict[str, Any]]]
) -> dict[str, float]:
    """The CycloneDX blueprint and the C4 diagrams come from one model; check they agree."""
    container = parse_c4(render_container(topo))
    cdx_names = {a["name"] for a in blueprint["assets"]} | {
        s["name"] for s in blueprint["dataStores"]
    }
    c4_names = {e.label for e in container.elements.values()}
    c4_pairs = {
        (container.elements[r.source].label, container.elements[r.destination].label)
        for r in container.rels
        if r.source in container.elements and r.destination in container.elements
    }
    by_ref = {a["bom-ref"]: a["name"] for a in blueprint["assets"]}
    by_ref.update({s["bom-ref"]: s["name"] for s in blueprint["dataStores"]})
    cdx_pairs = {
        (by_ref[f["source"]], by_ref[f["destination"]]) for f in blueprint["flows"]
    }
    return {
        "component_agreement": _ratio(
            len(cdx_names & c4_names), len(cdx_names | c4_names)
        ),
        "flow_agreement": _ratio(len(cdx_pairs & c4_pairs), len(cdx_pairs | c4_pairs)),
    }


def cdx_checks(blueprint: dict[str, list[dict[str, Any]]]) -> dict[str, float]:
    refs = {
        x["bom-ref"]
        for k in ("zones", "assets", "dataStores", "flows")
        for x in blueprint[k]
    }
    nodes = {x["bom-ref"] for k in ("assets", "dataStores") for x in blueprint[k]}
    zones = {z["bom-ref"] for z in blueprint["zones"]}
    flows = blueprint["flows"]
    resolves = all(
        f["source"] in nodes and f["destination"] in nodes for f in flows
    ) and all(
        n["zone"] in zones for k in ("assets", "dataStores") for n in blueprint[k]
    )
    props = lambda f: {p["name"] for p in f["properties"]}  # noqa: E731
    return {
        "refs_unique": float(
            len(refs)
            == sum(
                len(blueprint[k]) for k in ("zones", "assets", "dataStores", "flows")
            )
        ),
        "refs_resolve": float(resolves),
        "flow_security_attributes": _ratio(
            sum(
                1
                for f in flows
                if "encrypted" in f
                and {"resp:crosses-trust-zone", "resp:data-classification"} <= props(f)
            ),
            len(flows),
        ),
        "flow_protocol_coverage": _ratio(
            sum(1 for f in flows if f["protocols"]), len(flows)
        ),
        "typed_nodes": _ratio(
            sum(
                1
                for a in blueprint["assets"]
                if a["type"] in ("actor", "system", "process")
            )
            + len(blueprint["dataStores"]),
            len(blueprint["assets"]) + len(blueprint["dataStores"]),
        ),
    }


def render_levels(topo: Topology) -> dict[str, str]:
    return {"context": render_context(topo), "container": render_container(topo)}
