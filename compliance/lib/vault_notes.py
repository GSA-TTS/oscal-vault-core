"""Read typed vault notes (controls, inheritance, organizations) into plain records.

Parses control frontmatter and per-part narrative, normalizes implementation status and
origination through mapping tables, and extracts the inheritance table. Missing optional
files are skipped, never invented. Ports/Protocols/Services tables are read by ``topology``.
"""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Iterable

import yaml

from .control_identity import canonical_control_id

INLINE_META_RE = re.compile(r"^\s*([a-z_][a-z0-9_]*)::\s*(.*)$", re.IGNORECASE)
PART_HEADING_RE = re.compile(r"^##\s+Part\s+([A-Za-z0-9]+)\s*$")
SECTION_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$")
INHERITANCE_ROW_RE = re.compile(
    r"^\|\s*(?P<provider>[^|]+?)\s*\|\s*(?P<owner>[^|]*?)\s*\|\s*(?P<control>[^|]+?)\s*\|\s*(?P<mode>[^|]+?)\s*\|\s*$"
)


def normalize_control_id(raw: str) -> str:
    """Return the shared canonical representation of a control ID."""
    return canonical_control_id(raw) if raw and raw.strip() else ""


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    frontmatter = text[3:end].lstrip("\n")
    body = text[end + 4 :].lstrip("\n")
    try:
        data = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data, body


@dataclasses.dataclass
class StatusMap:
    mapping: dict[str, dict]
    default: dict

    @classmethod
    def from_yaml(cls, path: str) -> StatusMap:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(
            mapping=data.get("mapping", {}) or {}, default=data.get("default", {}) or {}
        )

    def lookup(self, value: str | None) -> tuple[str, str | None]:
        if value and value in self.mapping:
            entry = self.mapping[value] or {}
            return entry.get("target", value), entry.get("remark")
        return self.default.get("target", "not-implemented"), self.default.get("remark")


@dataclasses.dataclass
class OriginationMap:
    mapping: dict[str, dict]
    precedence: list[str]
    default: dict

    @classmethod
    def from_yaml(cls, path: str) -> OriginationMap:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(
            mapping=data.get("mapping", {}) or {},
            precedence=data.get("precedence", []) or [],
            default=data.get("default", {}) or {},
        )

    def lookup(self, values: Iterable[str] | None) -> tuple[str, str | None, list[str]]:
        raw = [v for v in (values or []) if v]
        if not raw:
            return (
                self.default.get("target", "system-specific"),
                self.default.get("remark"),
                [],
            )
        for candidate in self.precedence:
            if candidate in raw:
                entry = self.mapping.get(candidate, {}) or {}
                return entry.get("target", candidate), entry.get("remark"), raw
        entry = self.mapping.get(raw[0], {}) or {}
        return entry.get("target", raw[0]), entry.get("remark"), raw


@dataclasses.dataclass
class PartRecord:
    part: str
    inheritance_mode: str | None
    implementation_status: str | None
    prose: str


@dataclasses.dataclass
class ControlRecord:
    control_id: str
    title: str
    family: str
    implementation_status: str
    origination: list[str]
    normalized_status: str
    normalized_origination: str
    parameters: list[dict]
    statements: list[dict]
    parts: list[PartRecord]
    body_sections: dict[str, str]
    source_path: str

    @property
    def combined_prose(self) -> str:
        return "\n\n".join(
            part.prose.strip() for part in self.parts if part.prose.strip()
        )


def parse_control(
    path: str,
    validate_schema: bool = False,
    *,
    status_map: StatusMap | None = None,
    origination_map: OriginationMap | None = None,
) -> ControlRecord | None:
    """Parse one control, deriving omitted normalized fields from mapping tables."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    front, body = split_frontmatter(text)
    if front.get("note_type") != "control":
        return None
    control_id = normalize_control_id(front.get("control_id", ""))
    if not control_id:
        return None

    # ADR-006 issue #21: fail-fast if required frontmatter fields are absent
    if validate_schema:
        missing = [
            f
            for f in ("note_type", "control_id", "implementation_status")
            if not front.get(f)
        ]
        if missing:
            raise ValueError(
                f"vault schema validation failed for {path}: missing fields {missing}"
            )

    implementation_status = (front.get("implementation_status") or "").strip()
    origination = [str(v).strip() for v in (front.get("origination") or []) if v]
    normalized_status = (front.get("normalized_status") or "").strip()
    normalized_origination = (front.get("normalized_origination") or "").strip()
    if not normalized_status and status_map is not None:
        normalized_status, _ = status_map.lookup(implementation_status)
    if not normalized_origination and origination_map is not None:
        normalized_origination, _, _ = origination_map.lookup(origination)

    parts = _parse_body_parts(body)
    sections = _parse_body_sections(body)
    return ControlRecord(
        control_id=control_id,
        title=(front.get("title") or "").strip(),
        family=(front.get("family") or control_id.split("-", 1)[0]).upper(),
        implementation_status=implementation_status,
        origination=origination,
        normalized_status=normalized_status,
        normalized_origination=normalized_origination,
        parameters=list(front.get("parameters") or []),
        statements=list(front.get("statements") or []),
        parts=parts,
        body_sections=sections,
        source_path=path,
    )


def _parse_body_parts(body: str) -> list[PartRecord]:
    parts: list[PartRecord] = []
    current: PartRecord | None = None
    inline_meta: dict[str, str] = {}
    prose_lines: list[str] = []
    for line in body.splitlines():
        heading = PART_HEADING_RE.match(line)
        if heading:
            if current is not None:
                current.prose = "\n".join(prose_lines).strip()
                current.inheritance_mode = inline_meta.get("inheritance_mode")
                current.implementation_status = inline_meta.get("implementation_status")
                parts.append(current)
            current = PartRecord(
                part=heading.group(1).strip(),
                inheritance_mode=None,
                implementation_status=None,
                prose="",
            )
            inline_meta = {}
            prose_lines = []
            continue
        if current is None:
            continue
        inline = INLINE_META_RE.match(line)
        if inline:
            inline_meta[inline.group(1).lower()] = inline.group(2).strip()
            continue
        if line.startswith("# "):
            break
        prose_lines.append(line)
    if current is not None:
        current.prose = "\n".join(prose_lines).strip()
        current.inheritance_mode = inline_meta.get("inheritance_mode")
        current.implementation_status = inline_meta.get("implementation_status")
        parts.append(current)
    return parts


def _parse_body_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        header = SECTION_HEADING_RE.match(line)
        if header:
            current = header.group(1).strip()
            sections[current] = []
            continue
        if current is None:
            continue
        sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


@dataclasses.dataclass
class InheritanceEntry:
    control_id: str
    provider: str
    owner: str
    mode: str
    direction: str
    raw_control: str


def parse_inheritance(path: str) -> list[InheritanceEntry]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    _, body = split_frontmatter(text)
    entries: list[InheritanceEntry] = []
    direction: str | None = None
    section: str | None = None
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        header = SECTION_HEADING_RE.match(line) if line.startswith("# ") else None
        subheader = re.match(r"^##\s+(.+?)\s*$", line)
        if header:
            section = header.group(1).lower()
            continue
        if subheader:
            section = subheader.group(1).lower()
            continue
        if section == "direction" and line.strip():
            direction = line.strip()
            continue
        if section not in {"provided by", "receives"}:
            continue
        row = INHERITANCE_ROW_RE.match(line)
        if not row:
            continue
        provider = row.group("provider").strip()
        owner = row.group("owner").strip()
        control_field = row.group("control").strip()
        mode = row.group("mode").strip().lower()
        if provider.startswith(("[", "---")) or not provider:
            continue
        control_id = _first_control_id(control_field)
        if not control_id:
            continue
        entries.append(
            InheritanceEntry(
                control_id=control_id,
                provider=provider,
                owner=owner,
                mode=mode,
                direction=direction or section,
                raw_control=control_field,
            )
        )
    return entries


def _first_control_id(text: str) -> str:
    match = re.search(
        r"([A-Za-z]{2})[-\s]?0*(\d+)"
        r"(?:\s*(?:\(\s*0*(\d+)\s*\)|\.\s*0*(\d+)))?",
        text,
    )
    if not match:
        return ""
    raw = f"{match.group(1)}-{match.group(2)}"
    enhancement = match.group(3) or match.group(4)
    if enhancement:
        raw += f"({enhancement})"
    return normalize_control_id(raw)


CORE_MAPPINGS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mappings"
)


def load_status_and_origination(vault_root: str) -> tuple[StatusMap, OriginationMap]:
    """Status and origination mapping tables.

    A repository may carry its own ``<vault_root>/compliance/mappings`` (an override); otherwise the
    core's own tables are used, so a system repository that only holds vaults needs no copy.
    """
    local = os.path.join(vault_root, "compliance", "mappings")
    mappings = (
        local
        if os.path.isfile(os.path.join(local, "implementation-status-map.yaml"))
        else CORE_MAPPINGS
    )
    return (
        StatusMap.from_yaml(os.path.join(mappings, "implementation-status-map.yaml")),
        OriginationMap.from_yaml(
            os.path.join(mappings, "control-origination-map.yaml")
        ),
    )


# Maps org frontmatter role values to CPO contactType strings.
_ROLE_TO_CONTACT_TYPE: dict[str, str] = {
    "information system security officer": "Security",
    "isso": "Security",
    "authorizing official": "Security",
    "ao": "Security",
    "cloud service provider": "Sales",
    "cloud-service-provider": "Sales",
}


@dataclasses.dataclass
class OrgParty:
    """Parsed party record from Organizations/<slug>.md frontmatter."""

    name: str
    acronym: str
    role: str
    contact_type: str  # CPO contactType: "Security" or "Sales"
    org_type: str


def parse_org_parties(vault_system_dir: str) -> list[OrgParty]:
    """Return OrgParty records from every Organizations/*.md in vault_system_dir.

    Silently skips missing directory or unreadable files.
    """
    org_dir = os.path.join(vault_system_dir, "Organizations")
    if not os.path.isdir(org_dir):
        return []
    parties: list[OrgParty] = []
    for fname in sorted(os.listdir(org_dir)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(org_dir, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        front, _ = split_frontmatter(text)
        if front.get("note_type") != "organization":
            continue
        name = (front.get("name") or "").strip()
        if not name:
            continue
        role = (front.get("role") or "").strip()
        contact_type = _ROLE_TO_CONTACT_TYPE.get(role.lower(), "Security")
        parties.append(
            OrgParty(
                name=name,
                acronym=(front.get("acronym") or "").strip(),
                role=role,
                contact_type=contact_type,
                org_type=(front.get("org_type") or "").strip(),
            )
        )
    return parties
