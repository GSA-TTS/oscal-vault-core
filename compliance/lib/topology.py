"""Neutral system topology parsed from an SSP's Ports, Protocols, and Services table.

One parsed model feeds every diagram generator (CycloneDX blueprint, C4 Mermaid, ...),
so the outputs can be compared against each other and against hand-drawn references.
Classification is by name only and deliberately conservative: an endpoint that matches
nothing is a process inside the application boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

HUMAN = "human"
EXTERNAL = "external"
DATASTORE = "datastore"
PLATFORM = "platform-process"
PROCESS = "process"

ZONE_USERS = "users"
ZONE_EXTERNAL = "external"
ZONE_PLATFORM = "platform"
ZONE_APP = "app"

ZONE_LABELS = {
    ZONE_USERS: "Users and operators",
    ZONE_EXTERNAL: "External services",
    ZONE_PLATFORM: "Platform-managed services (cloud.gov / AWS)",
    ZONE_APP: "{display} application boundary",
}
ZONE_TRUST = {ZONE_USERS: 10, ZONE_EXTERNAL: 30, ZONE_PLATFORM: 60, ZONE_APP: 75}

_APP_WORDS = (
    r"(application|broker|worker|scheduler|proxy|service|portal|interface|"
    r"container|server|site|boundary)"
)
_HUMAN = re.compile(
    r"\b(users?|developers?|team members?|operators?|administrators?|workstations?|"
    r"devices?|browsers?|clients?|customers?|public|internet|team)\b",
    re.I,
)
_DATASTORE = re.compile(
    r"(mysql|postgres|redis|elasticache|\brds\b|\bs3\b|bucket|database|\bdb\b|"
    r"dynamo|solr\b|file store|documentdb|elasticsearch|opensearch|"
    r"elastic file system|\befs\b)",
    re.I,
)
_EXTERNAL = re.compile(
    r"(new ?relic|login\.gov|github|google|snyk|circle ?ci|dependabot|docker|looker|"
    r"big ?query|\bdap\b|gsaauth|okta|prisma|sam\.gov|govdelivery|figma|nessus|"
    r"bigfix|fireeye|elastic agent|smtp relay|\baws (ses|sns|api)\b|"
    r"cloud\.gov cli|client boundary|\bexternal\b|^agency\b|\bagencies\b)",
    re.I,
)
_PLATFORM = re.compile(
    r"(cloud\.gov|\baws\b|kubernetes|\beks\b|fargate|cloudfront)", re.I
)
_NOT_A_COMPONENT = re.compile(
    r"^(reference figure|list of target urls|n/?a|none|-+)\b", re.I
)
_NULL = re.compile(r"^(n/a|-+|none)$", re.I)

_STORE_KINDS = (
    (re.compile(r"mysql|postgres|\brds\b", re.I), "relational"),
    (re.compile(r"redis|elasticache", re.I), "key-value"),
    (re.compile(r"\bs3\b|bucket|file store", re.I), "object"),
    (re.compile(r"solr", re.I), "search"),
)
_TABLE_RE = re.compile(r"\|[^\n]+\|\n\|[-: |]+\|\n(?:\|[^\n]+\|\n)+", re.MULTILINE)


@dataclass(frozen=True)
class PpsRow:
    """One row of a Ports, Protocols, and Services table."""

    source: str
    destination: str
    direction: str
    boundary_crossing: bool
    port: str
    protocol: str
    service: str
    purpose: str
    encrypted: bool
    sensitivity: str


@dataclass(frozen=True)
class Component:
    key: str
    name: str
    kind: str
    zone: str
    store_type: str | None = None


@dataclass(frozen=True)
class Flow:
    ref: str
    source: str
    destination: str
    label: str
    purpose: str
    protocol: str
    port: int | None
    encrypted: bool
    crosses_boundary: bool
    sensitivity: str


@dataclass
class Topology:
    system_id: str
    display: str
    components: list[Component] = field(default_factory=list)
    flows: list[Flow] = field(default_factory=list)

    def component(self, key: str) -> Component:
        return next(c for c in self.components if c.key == key)

    @property
    def zones(self) -> list[str]:
        used = {c.zone for c in self.components}
        return [z for z in ZONE_LABELS if z in used]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]


def classify(name: str) -> str:
    """Return the kind of an endpoint from its name alone.

    Order matters: a software name that merely contains a person-like word stays
    software, and a data store beats a vendor match ("Cloud.gov RDS").
    """
    if _HUMAN.search(name) and not re.search(_APP_WORDS, name, re.I):
        return HUMAN
    if _DATASTORE.search(name):
        return DATASTORE
    if _EXTERNAL.search(name):
        return EXTERNAL
    if _PLATFORM.search(name):
        return PLATFORM
    return PROCESS


def zone_for(name: str, kind: str) -> str:
    if kind == HUMAN:
        return ZONE_USERS
    if kind == EXTERNAL:
        return ZONE_EXTERNAL
    if kind in (PLATFORM, DATASTORE) and _PLATFORM.search(name):
        return ZONE_PLATFORM
    return ZONE_APP


def store_type(name: str) -> str:
    for pattern, kind in _STORE_KINDS:
        if pattern.search(name):
            return kind
    return "legacy-unspecified"


def parse_port(value: str) -> int | None:
    match = re.search(r"\d{1,5}", value or "")
    if not match:
        return None
    port = int(match.group())
    return port if 0 < port <= 65535 else None


def _truthy(value: str) -> bool:
    token = re.split(r"[\s(]", value.strip(), maxsplit=1)[0].upper()
    return token in ("Y", "YES", "TRUE", "1")


def _direction(value: str) -> str:
    v = value.strip().lower()
    if "inbound" in v and "outbound" in v:
        return "Bi-Directional"
    if "inbound" in v:
        return "Inbound"
    if "outbound" in v:
        return "Outbound"
    return value.strip().title() or "Bi-Directional"


def _split_table(text: str) -> tuple[list[str], list[list[str]]]:
    rows = []
    for line in text.strip().split("\n"):
        if re.match(r"\|[-: |]+\|", line):
            continue
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return (rows[0], rows[1:]) if len(rows) >= 2 else ([], [])


def parse_pps_rows(markdown: str) -> list[PpsRow]:
    """Return every PPS row in ``markdown``. Columns are located by header keyword."""
    out: list[PpsRow] = []
    for match in _TABLE_RE.finditer(markdown):
        headers, rows = _split_table(match.group(0))
        head = [h.lower() for h in headers]

        def col(test) -> int | None:
            return next((i for i, h in enumerate(head) if test(h)), None)

        src = col(lambda h: "source" in h)
        dst = col(lambda h: "dest" in h)
        if src is None or dst is None:
            continue
        cols = {
            "dir": col(lambda h: "direction" in h),
            "bc": col(lambda h: "boundary" in h),
            "port": col(lambda h: "port" in h),
            "proto": col(lambda h: "protocol" in h),
            "svc": col(lambda h: "service" in h and "external" not in h),
            "purpose": col(lambda h: "purpose" in h),
            "enc": col(lambda h: "encrypt" in h),
            "sens": col(lambda h: "sensitiv" in h or "data" in h),
        }

        def cell(row: list[str], index: int | None) -> str:
            return row[index].strip() if index is not None and index < len(row) else ""

        for row in rows:
            if len(row) <= max(src, dst):
                continue
            a, b = cell(row, src), cell(row, dst)
            if (_NULL.match(a) and _NULL.match(b)) or (not a and not b):
                continue
            out.append(
                PpsRow(
                    a,
                    b,
                    _direction(cell(row, cols["dir"])),
                    _truthy(cell(row, cols["bc"]) or "N"),
                    cell(row, cols["port"]),
                    cell(row, cols["proto"]),
                    cell(row, cols["svc"]),
                    cell(row, cols["purpose"]),
                    _truthy(cell(row, cols["enc"]) or "Y"),
                    cell(row, cols["sens"]) or "Low",
                )
            )
    return out


def load_pps_rows(system_dir: Path) -> list[PpsRow]:
    """Rows from every ``Relationships/*.md`` note of one vault system."""
    rows: list[PpsRow] = []
    for note in sorted((system_dir / "Relationships").glob("*.md")):
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        body = text.split("\n---\n", 1)[1] if text.startswith("---\n") else text
        rows.extend(parse_pps_rows(body))
    return rows


def build_topology(system_id: str, display: str, rows: list[PpsRow]) -> Topology:
    """Typed, zoned components and flows from PPS rows."""
    rows = [
        r
        for r in rows
        if not any(_NOT_A_COMPONENT.match(x.strip()) for x in (r.source, r.destination))
    ]
    names: dict[str, str] = {}
    for row in rows:
        for label in (row.source, row.destination):
            names.setdefault(slug(label), label.strip())
    names.pop("", None)

    topo = Topology(system_id, display)
    for key in sorted(names):
        name = names[key]
        kind = classify(name)
        topo.components.append(
            Component(
                key,
                name,
                kind,
                zone_for(name, kind),
                store_type(name) if kind == DATASTORE else None,
            )
        )

    seen: set[str] = set()
    for row in rows:
        src, dst = slug(row.source), slug(row.destination)
        if src not in names or dst not in names:
            continue
        port = parse_port(row.port)
        base = f"flow-{system_id}-{src}-to-{dst}" + (f"-{port}" if port else "")
        ref, n = base, 2
        while ref in seen:
            ref, n = f"{base}-{n}", n + 1
        seen.add(ref)
        topo.flows.append(
            Flow(
                ref,
                src,
                dst,
                row.service or row.purpose or row.protocol or "connection",
                row.purpose,
                row.protocol,
                port,
                row.encrypted,
                row.boundary_crossing,
                row.sensitivity,
            )
        )
    return topo
