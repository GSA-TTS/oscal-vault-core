"""Contract-driven secondary parser for FedRAMP SSP content.

Walks a markdown blob (produced by `scripts/docx_catchall_dump.py`) and routes
content to destination note types based on an ODCS v3.1.0 data contract
(`compliance/datacontracts/fedramp-ssp-v1.yaml`).

The contract is the single source of truth for the to/from mapping: each
destination property carries `customProperties.sourceHeading` +
`customProperties.captureStrategy` describing where to find the source
content and how to extract it. This parser is intentionally simple and
generic — adding a new mapping is a contract edit, not a code change.

Companion to the primary `fismatic` parser, which only extracts Chapter 13
control implementation tables. This parser handles everything else:
system identity, parties, architecture, data flow, inheritance,
interconnections, references, etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# ---------------------------------------------------------------------------
# Contract loading
# ---------------------------------------------------------------------------

# Custom-property keys we understand on schema properties.
CP_SOURCE_HEADING = "sourceHeading"
CP_ALIAS_SOURCE_HEADING = "aliasSourceHeading"
CP_SOURCE_KIND = "sourceKind"
CP_CAPTURE_STRATEGY = "captureStrategy"
CP_CAPTURE_SCOPE = "captureScope"
CP_OSCAL_PATH = "oscalPath"
CP_NOTE = "note"
CP_PROVENANCE = "provenance"

# R16 write-back custom-property keys (fill-back / roundtrip). These are the
# inverse of the read keys above and are used by docx_jinjafier / docx_fill.
CP_TEMPLATE_TAG = "templateTag"
CP_WRITE_STRATEGY = "writeStrategy"
CP_INSTRUCTION_ANCHOR = "instructionAnchor"
CP_WRITE_SCOPE = "writeScope"

# Custom-property keys we understand on schema objects.
CP_OSCAL_TYPE = "oscalType"
CP_OSCAL_MODEL = "oscalModel"
CP_NOTE_TYPE = "noteType"
CP_DESTINATION_FOLDER = "destinationFolder"
CP_REQUIRED = "required"
# When true, this object's content is produced by the primary parser (fismatic),
# so the contract-driven secondary parser must NOT attempt to re-extract it
# (D-9 / #43). Its properties are otherwise inert here.
CP_HANDLED_BY_PRIMARY = "handledByPrimaryParser"

# Valid capture strategies.
CAPTURE_STRATEGIES = {
    "heading_paragraph",  # concat all paragraph text under the heading
    "heading_paragraph_first",  # first non-empty paragraph only
    "heading_table_first",  # first table under the heading
    "heading_table_all",  # all tables under the heading (list)
    "heading_image_all",  # all images under the heading (list)
    "heading_image_first",  # first image under the heading
    "heading_all",  # full raw markdown of the heading section
    "constant",  # use the value from `examples[0]` or `customProperties.value`
    "derived",  # computed from other extracted fields (parser hook)
    "control_table",  # parse CONTROL TABLE (CAPTURED) tags from catchall blob;
    # extracts control_id, impl-status, origination, role,
    # statement_parts from the markdown table grid.
}


@dataclass
class PropertyMapping:
    """One destination property + how to fill it from the source."""

    object_id: str
    name: str
    logical_type: str
    source_heading: Optional[str]
    source_kind: Optional[str]
    capture_strategy: Optional[str]
    capture_scope: Optional[str]
    oscal_path: Optional[str]
    note: Optional[str]
    provenance: Optional[str]
    constant_value: Optional[Any] = None
    required: bool = False
    primary_key: bool = False
    tags: List[str] = field(default_factory=list)
    # R14: optional list of alias source_headings (newline-separated) so the
    # same property can match e.g. "Data Flow" OR "10.4 Data Flow" from
    # different SSP templates.
    alias_source_heading: Optional[str] = None
    # R16: fill-back / roundtrip write metadata (inverse of the read fields).
    template_tag: Optional[str] = None
    write_strategy: Optional[str] = None
    instruction_anchor: Optional[str] = None
    write_scope: Optional[str] = None


@dataclass
class ObjectMapping:
    """One destination note type (e.g. `oscal.party`)."""

    object_id: str
    name: str
    physical_name: Optional[str]
    description: Optional[str]
    oscal_type: Optional[str]
    oscal_model: Optional[str]
    note_type: Optional[str]
    destination_folder: Optional[str]
    required: bool
    tags: List[str]
    properties: List[PropertyMapping]
    relationships: List[Dict[str, Any]]
    multiple: bool = False  # True if a single source yields many records
    # D-9 / #43: object is filled by the primary parser (fismatic); the
    # contract-driven `extract()` skips it so its (currently unimplemented)
    # properties are not mislabeled as "unfilled".
    handled_by_primary_parser: bool = False


@dataclass
class Contract:
    """Parsed ODCS data contract, ready for the parser to consume."""

    raw: Dict[str, Any]
    objects_by_id: Dict[str, ObjectMapping]
    objects_by_name: Dict[str, ObjectMapping]
    source_object_ids: List[str]  # objects flagged as source (customProp isSource=true)

    def destinations(self) -> List[ObjectMapping]:
        """Destination objects in contract order (excludes source objects)."""
        return [
            obj
            for obj in (self.objects_by_id[k] for k in self._object_order)
            if obj.object_id not in self.source_object_ids
        ]

    # _object_order is set by load_contract()
    _object_order: List[str] = field(default_factory=list)


def _coerce_bool(value: Any) -> bool:
    """Interpret a contract customProperty value as a boolean.

    ODCS customProperty values arrive as native YAML types or strings; accept
    ``true``/``"true"``/``1`` etc. so ``handledByPrimaryParser`` is honored
    regardless of how it was serialized.
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _custom_props_map(custom_props: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Convert ODCS customProperties array to a flat dict."""
    out: Dict[str, Any] = {}
    if not custom_props:
        return out
    for cp in custom_props:
        key = cp.get("property")
        if key:
            out[key] = cp.get("value")
    return out


def load_contract(path: str | Path) -> Contract:
    """Load and parse an ODCS data contract YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Contract at {path} is not a YAML mapping")
    if raw.get("kind") != "DataContract":
        raise ValueError(
            f"Contract at {path} has kind={raw.get('kind')!r}; expected 'DataContract'"
        )

    objects_by_id: Dict[str, ObjectMapping] = {}
    objects_by_name: Dict[str, ObjectMapping] = {}
    source_object_ids: List[str] = []
    order: List[str] = []

    for schema_obj in raw.get("schema") or []:
        obj_id = schema_obj.get("id")
        obj_name = schema_obj.get("name")
        if not obj_name:
            raise ValueError(f"Schema object missing `name`: {schema_obj!r}")
        if obj_id:
            order.append(obj_id)
        elif obj_name:
            order.append(obj_name)
        obj_cp = _custom_props_map(schema_obj.get("customProperties") or [])

        props: List[PropertyMapping] = []
        for prop in schema_obj.get("properties") or []:
            prop_cp = _custom_props_map(prop.get("customProperties") or [])
            constant_val = None
            if prop_cp.get("captureStrategy") == "constant":
                examples = prop.get("examples") or []
                if examples:
                    constant_val = examples[0]
                elif "value" in prop_cp:
                    constant_val = prop_cp["value"]
            props.append(
                PropertyMapping(
                    object_id=obj_id or obj_name,
                    name=prop.get("name", ""),
                    logical_type=prop.get("logicalType", "string"),
                    source_heading=prop_cp.get(CP_SOURCE_HEADING),
                    source_kind=prop_cp.get(CP_SOURCE_KIND),
                    capture_strategy=prop_cp.get(CP_CAPTURE_STRATEGY),
                    capture_scope=prop_cp.get(CP_CAPTURE_SCOPE),
                    oscal_path=prop_cp.get(CP_OSCAL_PATH),
                    note=prop_cp.get(CP_NOTE),
                    provenance=prop_cp.get(CP_PROVENANCE),
                    alias_source_heading=prop_cp.get(CP_ALIAS_SOURCE_HEADING),
                    constant_value=constant_val,
                    required=bool(prop.get("required", False)),
                    primary_key=bool(prop.get("primaryKey", False)),
                    tags=prop.get("tags") or [],
                    template_tag=prop_cp.get(CP_TEMPLATE_TAG),
                    write_strategy=prop_cp.get(CP_WRITE_STRATEGY),
                    instruction_anchor=prop_cp.get(CP_INSTRUCTION_ANCHOR),
                    write_scope=prop_cp.get(CP_WRITE_SCOPE),
                )
            )

        obj = ObjectMapping(
            object_id=obj_id or obj_name,
            name=obj_name,
            physical_name=schema_obj.get("physicalName"),
            description=schema_obj.get("description"),
            oscal_type=obj_cp.get(CP_OSCAL_TYPE),
            oscal_model=obj_cp.get(CP_OSCAL_MODEL),
            note_type=obj_cp.get(CP_NOTE_TYPE),
            destination_folder=obj_cp.get(CP_DESTINATION_FOLDER),
            required=bool(obj_cp.get(CP_REQUIRED, False)),
            tags=schema_obj.get("tags") or [],
            properties=props,
            relationships=schema_obj.get("relationships") or [],
            multiple=bool(obj_cp.get("multiple", False)),
            handled_by_primary_parser=_coerce_bool(
                obj_cp.get(CP_HANDLED_BY_PRIMARY, False)
            ),
        )
        objects_by_id[obj.object_id] = obj
        objects_by_name[obj.name] = obj
        if obj_cp.get("isSource"):
            source_object_ids.append(obj.object_id)

    c = Contract(
        raw=raw,
        objects_by_id=objects_by_id,
        objects_by_name=objects_by_name,
        source_object_ids=source_object_ids,
    )
    c._object_order = order
    return c


# ---------------------------------------------------------------------------
# Markdown blob parsing
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# The catchall dumper emits `_[PARA @ idx=N style=... — DROPPED]_  \n<text>`
_PARA_TAG_RE = re.compile(r"^_\[PARA @ idx=(\d+) style=([^\s]+)(?:[^\]]*)\]_\s*$")
# `_[TABLE #N @ heading='...' rows=R cols=C class=...]_`
# Control tables have class=CONTROL TABLE (CAPTURED) and carry control_id='AC-1'
# Tolerant of single OR double quotes around heading and control_id.
_TABLE_TAG_RE = re.compile(
    r"^_\[TABLE #(\d+) @ heading=['\"]([^'\"]*)['\"] rows=(\d+) cols=(\d+) "
    r"class=([A-Z _()]+)(?: control_id=['\"]([^'\"]*)['\"])?_\s*$"
)
# `_[IMAGE #N @ paragraph_idx=... style=... rId=... part=... — DROPPED]_`
# Note: image tags end with `]_` (unlike table tags which end with `_`).
_IMAGE_TAG_RE = re.compile(
    r"^_\[IMAGE #(\d+) @ paragraph_idx=(\d+) style=(\S+) rId=(\S+) "
    r"part=(\S+)(?: [^\]]*)?\]_\s*$"
)
_STYLE_LINE_RE = re.compile(r"^_style=([^,]+), paragraph_idx=(\d+)_\s*$")

# ---------------------------------------------------------------------------
# Control table parsing (Chapter 13)
# ---------------------------------------------------------------------------
#
# The catchall dumper marks Chapter 13 tables as CONTROL TABLE (CAPTURED) and
# records the extracted control_id in the TABLE tag. ParsedTable.control_id is
# set for these tables; ParsedTable.is_control_table is True.
#
# These regexes mirror what fismatic/parser.py uses — document them here so
# the contract's `customProperties` descriptions stay in sync with the code.

# Checkbox detection — mirrors fismatic.parser._is_checked() / _strip_checkbox()
_CHECKED_RE = re.compile(r"^[\u2612\u2713\u22A0]|^\[[ xX]\]|^[xX]\b")
_UNCHECKED_RE = re.compile(r"^[\u2610]")


def _is_checkbox_checked(line: str) -> bool:
    """True if line starts with a checked checkbox (stripped).

    Matches: ☒ (U+2612), ✓ (U+2713), ⊠ (U+22A0), [x], [X], bare x/X.
    Unchecked: ☐ (U+2610).
    Pattern (from fismatic/parser.py): r'^[☒✓⊠]' | r'^\\[[ xX]\\]' | r'^[xX]\\b'
    """
    stripped = line.strip()
    if _UNCHECKED_RE.match(stripped):
        return False
    return bool(_CHECKED_RE.match(stripped))


def _strip_checkbox_prefix(line: str) -> str:
    """Remove the checkbox prefix from a checked/unchecked line.

    Mirrors fismatic.parser._strip_checkbox():
      - lstrip Unicode ballot-box chars (☒☐✓⊠)
      - strip [x]/[X]/[ ] bracket form
      - strip bare 'x '/'X ' prefix
    """
    content = line.lstrip("\u2612\u2610\u2713\u22a0").strip()
    content = re.sub(r"^\[[ xX]\]\s*", "", content).strip()
    content = re.sub(r"^[xX]\s+", "", content).strip()
    return content


def _parse_impl_status_from_cell(cell_text: str) -> Optional[str]:
    """Parse the Implementation Status checkbox block into an OSCAL state string.

    Scans each line of `cell_text` (the value column of the Implementation
    Status row). Returns the OSCAL state for the first checked box, or
    'not-implemented' if nothing matched.

    Keyword patterns and OSCAL mapping (fismatic/parser.py:_STATUS_KEYWORDS +
    _status_to_oscal_state):
      implemented            <- re.match(r'^implemented\\b',           label_lower)
      partially_implemented  <- re.match(r'^partially\\s+implemented\\b', label_lower)
      planned                <- re.match(r'^planned\\b',               label_lower)
      alternative_impl       <- re.match(r'^alternative\\s+implementation\\b', label_lower)
      configured_by_customer <- re.match(r'^configured\\s+by\\s+customer\\b', label_lower)
      not_applicable         <- re.match(r'^not\\s+applicable\\b',      label_lower)
    """
    _status_kw: List[Tuple[str, str]] = [
        ("implemented", r"^implemented\b"),
        ("partially_implemented", r"^partially\s+implemented\b"),
        ("planned", r"^planned\b"),
        ("alternative_implementation", r"^alternative\s+implementation\b"),
        ("configured_by_customer", r"^configured\s+by\s+customer\b"),
        ("not_applicable", r"^not\s+applicable\b"),
    ]
    _oscal_map = {
        "implemented": "implemented",
        "partially_implemented": "partially-implemented",
        "alternative_implementation": "alternative-implementation",
        "planned": "planned",
        "configured_by_customer": "customer-configured",
        "not_applicable": "not-applicable",
    }
    flags: dict = {k: False for k, _ in _status_kw}
    for raw_line in cell_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not _is_checkbox_checked(line):
            continue
        label = _strip_checkbox_prefix(line).lower()
        for key, pattern in _status_kw:
            if re.match(pattern, label):
                flags[key] = True
    # Precedence mirrors _status_to_oscal_state()
    for key in [
        "implemented",
        "partially_implemented",
        "alternative_implementation",
        "planned",
        "configured_by_customer",
        "not_applicable",
    ]:
        if flags[key]:
            return _oscal_map[key]
    return "not-implemented"


def _parse_origination_from_cell(cell_text: str) -> Dict[str, Any]:
    """Parse the Control Origination checkbox block.

    Returns a dict with keys: origination_list (List[str] of OSCAL values),
    inherited_from (Optional[str]), hybrid_systems (List[str]).

    Origination keyword patterns (fismatic/parser.py:_ORIGINATION_KEYWORDS +
    _origination_to_oscal_list):
      inherited              <- re.search(r'^inherited\\s+from',      without_parens)
      hybrid                 <- re.search(r'^(?:hybrid\\s+control|service\\s+provider\\s+hybrid)', without_parens)
      system_specific        <- re.search(r'^(?:system\\s+specific\\s+control|service\\s+provider\\s+system\\s+specific)', without_parens)
      service_provider_corp  <- re.search(r'(?:common\\s+control|service\\s+provider\\s+corporate)', without_parens)
      configured_by_customer <- re.search(r'^configured\\s+by\\s+customer', without_parens)
      provided_by_customer   <- re.search(r'^provided\\s+by\\s+customer', without_parens)
      shared                 <- re.search(r'^shared\\b', without_parens)

    Inherited-from: re.search(r'inherited\\s+from:?\\s*(.+)', line, IGNORECASE).group(1)
    Hybrid systems: re.search(r'shared\\s+between\\s+(.+?)(?:;|\\)|$)', line, IGNORECASE),
                    split group(1) on r'\\s+and\\s+'.
    """
    _origin_kw: List[Tuple[str, str]] = [
        ("inherited", r"^inherited\s+from"),
        ("hybrid", r"^(?:hybrid\s+control|service\s+provider\s+hybrid)"),
        (
            "system_specific",
            r"^(?:system\s+specific\s+control|service\s+provider\s+system\s+specific)",
        ),
        (
            "service_provider_corp",
            r"(?:common\s+control|service\s+provider\s+corporate)",
        ),
        ("configured_by_customer", r"^configured\s+by\s+customer"),
        ("provided_by_customer", r"^provided\s+by\s+customer"),
        ("shared", r"^shared\b"),
    ]
    _oscal_map = {
        "inherited": "inherited",
        "hybrid": "hybrid",
        "system_specific": "system-specific",
        "service_provider_corp": "service-provider-corporate",
        "configured_by_customer": "customer-configured",
        "provided_by_customer": "customer-provided",
        "shared": "shared",
    }
    flags: dict = {k: False for k, _ in _origin_kw}
    inherited_from: Optional[str] = None
    hybrid_systems: List[str] = []

    for raw_line in cell_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not _is_checkbox_checked(line):
            continue
        content = _strip_checkbox_prefix(line)
        content_lower = content.lower()
        without_parens = re.sub(r"\([^)]*\)", "", content_lower).strip()
        for key, pattern in _origin_kw:
            if re.search(pattern, without_parens):
                flags[key] = True
        if flags["inherited"] and inherited_from is None:
            m = re.search(r"inherited\s+from:?\s*(.+)", content, re.IGNORECASE)
            if m:
                inherited_from = m.group(1).strip().rstrip(".") or None
        if flags["hybrid"] and not hybrid_systems:
            m2 = re.search(
                r"shared\s+between\s+(.+?)(?:;|\)|$)", content, re.IGNORECASE
            )
            if m2:
                hybrid_systems = [
                    s.strip().strip(",")
                    for s in re.split(r"\s+and\s+", m2.group(1))
                    if s.strip()
                ]

    origination_list = [_oscal_map[k] for k, _ in _origin_kw if flags[k]]
    return {
        "origination_list": origination_list,
        "inherited_from": inherited_from,
        "hybrid_systems": hybrid_systems,
    }


def parse_control_table_record(tbl: "ParsedTable") -> Dict[str, Any]:
    r"""Extract structured control data from a ParsedTable (CONTROL TABLE tag).

    Returns a dict with keys that map to the `oscal_control` contract properties:
      control_id          — from TABLE tag or row-0 header cell
      implementation_status — OSCAL state string
      control_origination — list of OSCAL origination values
      inherited_from      — provider name (when origination includes 'inherited')
      hybrid_systems      — list of systems (when origination includes 'hybrid')
      responsible_role    — raw text from the Responsible Role cell
      statement_parts     — list of {statement_id, part_key, description} dicts
      parameters          — list of {param_id, value} dicts (if any rows match)
      raw_headers         — the table header row as captured in the blob
      raw_rows            — all data rows as lists of cell strings

    The contract's `customProperties` on each oscal_control property describe
    the docx row layout, label patterns, and regexes in enough detail to tune
    them without reading parser.py.  Specifically:

    Row detection (cells[0].strip().lower() contains label):
      'responsible' + 'role' -> responsible_role
      'implementation status' -> implementation_status (checkbox parse)
      'control origination'   -> control_origination  (checkbox parse)
      re.match(r'(?:part|req\.?)\s*([a-z0-9]+)', IGNORECASE) -> statement_parts
      re.match(r'^[Pp]arameter\s+(\S+):?\s*$')              -> parameters
    """
    _IMPL_STATUS_LABEL = "implementation status"
    _CTRL_ORIGIN_LABEL = "control origination"
    _RESPONSIBLE_LABEL1 = "responsible"
    _RESPONSIBLE_LABEL2 = "role"
    _PART_ROW_RE = re.compile(r"^(?:part|req\.?)\s*([a-z0-9]+)", re.IGNORECASE)
    _PARAM_ROW_RE = re.compile(r"^[Pp]arameter\s+([^\s:]+):?\s*$")

    control_id: Optional[str] = tbl.control_id
    impl_status: Optional[str] = None
    origination_data: Dict[str, Any] = {}
    responsible_role: Optional[str] = None
    statement_parts: List[Dict[str, Any]] = []
    parameters: List[Dict[str, str]] = []

    # Process header row (row 0) if control_id not in tag
    if not control_id and tbl.headers:
        header_text = " ".join(tbl.headers)
        m = re.search(
            r"([a-z]{2})[\s-]*(\d+)(?:\s*\((\d+|[a-z]+)\))?",
            header_text,
            re.IGNORECASE,
        )
        if m:
            family, number, enhancement = m.group(1).upper(), m.group(2), m.group(3)
            control_id = f"{family}-{number}" + (
                f"({enhancement})" if enhancement else ""
            )

    # Process all data rows
    all_rows = tbl.data_rows
    for row in all_rows:
        if len(row) < 2:
            continue
        label = row[0].strip().lower()
        value = row[-1]  # last column is the value

        if _IMPL_STATUS_LABEL in label:
            impl_status = _parse_impl_status_from_cell(value)
            continue

        if _CTRL_ORIGIN_LABEL in label:
            origination_data = _parse_origination_from_cell(value)
            continue

        if _RESPONSIBLE_LABEL1 in label and _RESPONSIBLE_LABEL2 in label:
            responsible_role = row[-1].strip() or None
            continue

        m_part = _PART_ROW_RE.match(row[0].strip())
        if m_part:
            part_key = m_part.group(1)
            cid_lower = re.sub(r"\s+", "", (control_id or "ctrl").strip().lower())
            stmt_id = f"{cid_lower}_stmt.{part_key}"
            statement_parts.append(
                {
                    "statement_id": stmt_id,
                    "part_key": part_key,
                    "description": row[-1].strip(),
                }
            )
            continue

        m_param = _PARAM_ROW_RE.match(row[0].strip())
        if m_param:
            parameters.append(
                {
                    "param_id": m_param.group(1),
                    "value": row[-1].strip(),
                }
            )
            continue

    return {
        "control_id": control_id,
        "implementation_status": impl_status or "not-implemented",
        "control_origination": origination_data.get("origination_list") or [],
        "inherited_from": origination_data.get("inherited_from"),
        "hybrid_systems": origination_data.get("hybrid_systems") or [],
        "responsible_role": responsible_role,
        "statement_parts": statement_parts,
        "parameters": parameters,
        "raw_headers": tbl.headers,
        "raw_rows": tbl.data_rows,
    }


@dataclass
class ParsedTable:
    table_index: int
    heading: str
    rows: int
    cols: int
    control_id: Optional[str]
    headers: List[str]
    data_rows: List[List[str]]  # cells as strings


@dataclass
class ParsedImage:
    image_index: int
    paragraph_index: int
    style: str
    rid: str
    part: str


@dataclass
class HeadingSection:
    level: int  # 1-6
    title: str
    style: str
    paragraph_index: int
    paragraphs: List[str]  # text content (in order)
    tables: List[ParsedTable]
    images: List[ParsedImage]
    raw_lines: List[str]  # full raw markdown of this section (excluding heading)


def parse_catchall_blob(markdown: str) -> List[HeadingSection]:
    """Split a catchall dump markdown into per-heading sections.

    Returns a list of HeadingSection in document order. Content before the
    first heading (preamble) is attached to a synthetic section with
    title="" and level=0.
    """
    lines = markdown.splitlines()
    sections: List[HeadingSection] = []
    current: Optional[HeadingSection] = None
    # Track section nesting so we know when a heading at level <= current ends
    # the current section. We split on ANY heading — content under sub-headings
    # is also captured by the parent section's raw_lines, but the
    # paragraphs/tables/images lists only get direct (non-nested) content.
    # For simplicity and to match the contract's `captureScope` semantics,
    # we collect content into the *deepest* current heading and let
    # higher-level sections see only their direct content via raw_lines.

    # Stack of (level, section) for nesting
    stack: List[HeadingSection] = []

    def pop_until(level: int) -> None:
        while stack and stack[-1].level >= level:
            stack.pop()

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _HEADING_RE.match(line)
        if m:
            hashes, title = m.group(1), m.group(2).strip()
            level = len(hashes)
            # Skip the `# Catchall Dump:` title (level 1) and the top-level
            # structural `## ...` sections produced by the dumper (Document
            # Metadata, Document Inventory, Headings Outline, Body Content,
            # Images and Embedded Objects, Headers and Footers, Currently
            # captured vs. dropped). Real source headings start under
            # `## Body Content` and use H1..H4 (Title=H1, Heading N=H(N+1)).
            # We don't strictly need to filter here; the contract's
            # `sourceHeading` will only match real SSP headings. But we DO
            # need to skip the dumper's own H1 title so it doesn't shadow
            # the SSP's first heading.
            # Style line follows: `_style=..., paragraph_idx=N_`
            style = "normal"
            para_idx = -1
            if i + 1 < len(lines):
                sm = _STYLE_LINE_RE.match(lines[i + 1])
                if sm:
                    style = sm.group(1)
                    para_idx = int(sm.group(2))
                    i += 1  # consume style line
            section = HeadingSection(
                level=level,
                title=title,
                style=style,
                paragraph_index=para_idx,
                paragraphs=[],
                tables=[],
                images=[],
                raw_lines=[],
            )
            sections.append(section)
            pop_until(level)
            stack.append(section)
            current = section
            i += 1
            continue

        if current is None:
            # Preamble (before any heading) — drop
            i += 1
            continue

        # Paragraph tag
        pm = _PARA_TAG_RE.match(line)
        if pm:
            # The text is on the NEXT line(s), until a blank line or another tag
            i += 1
            text_lines: List[str] = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() == "":
                    break
                if (
                    _HEADING_RE.match(nxt)
                    or _PARA_TAG_RE.match(nxt)
                    or _TABLE_TAG_RE.match(nxt)
                    or _IMAGE_TAG_RE.match(nxt)
                    or _STYLE_LINE_RE.match(nxt)
                ):
                    break
                text_lines.append(nxt)
                i += 1
            text = "\n".join(text_lines).strip()
            # Attach to the deepest section only (sub-heading's section)
            stack[-1].paragraphs.append(text)
            stack[-1].raw_lines.append(f"[PARA] {text}")
            continue

        # Table tag
        tm = _TABLE_TAG_RE.match(line)
        if tm:
            tindex = int(tm.group(1))
            theading = tm.group(2)
            trows = int(tm.group(3))
            tcols = int(tm.group(4))
            tclass = tm.group(5).strip()
            tcontrol = tm.group(6)
            is_control = "CONTROL TABLE" in tclass
            i += 1
            # Read the markdown table grid that follows
            md_rows: List[List[str]] = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip().startswith("|") and nxt.count("|") >= 2:
                    # Strip leading/trailing pipes, split, un-escape
                    inner = nxt.strip()
                    if inner.startswith("|"):
                        inner = inner[1:]
                    if inner.endswith("|"):
                        inner = inner[:-1]
                    cells = [
                        c.replace("\\|", "|").strip() for c in _split_table_row(inner)
                    ]
                    md_rows.append(cells)
                    i += 1
                elif nxt.strip() == "":
                    # Blank line ends the markdown table
                    break
                else:
                    break
            # First row = headers, second row = separator (---|---|...), rest = data
            headers = md_rows[0] if md_rows else []
            # Filter out separator row (all cells match ^:?-+:?$)
            data_rows = [
                r
                for r in md_rows[1:]
                if not all(re.match(r"^:?-{2,}:?$", c.strip() or "-") for c in r)
            ]
            tbl = ParsedTable(
                table_index=tindex,
                heading=theading,
                rows=trows,
                cols=tcols,
                control_id=tcontrol if is_control else None,
                headers=headers,
                data_rows=data_rows,
            )
            stack[-1].tables.append(tbl)
            stack[-1].raw_lines.append(f"[TABLE #{tindex}] {tclass}")
            continue

        # Image tag
        im = _IMAGE_TAG_RE.match(line)
        if im:
            img = ParsedImage(
                image_index=int(im.group(1)),
                paragraph_index=int(im.group(2)),
                style=im.group(3),
                rid=im.group(4),
                part=im.group(5),
            )
            stack[-1].images.append(img)
            stack[-1].raw_lines.append(
                f"[IMAGE #{img.image_index}] rId={img.rid} part={img.part}"
            )
            i += 1
            continue

        # Other line — keep in raw_lines but don't attach to any list
        stack[-1].raw_lines.append(line)
        i += 1

    return sections


def _split_table_row(inner: str) -> List[str]:
    """Split a markdown table row body (pipes already stripped at ends) on
    un-escaped `|`, respecting `\\|` escapes."""
    out: List[str] = []
    buf: List[str] = []
    j = 0
    while j < len(inner):
        ch = inner[j]
        if ch == "\\" and j + 1 < len(inner) and inner[j + 1] == "|":
            buf.append("|")
            j += 2
            continue
        if ch == "|":
            out.append("".join(buf))
            buf = []
            j += 1
            continue
        buf.append(ch)
        j += 1
    out.append("".join(buf))
    return out


# ---------------------------------------------------------------------------
# Heading matching
# ---------------------------------------------------------------------------


def _strip_section_number(title: str) -> str:
    """Strip a leading section-number prefix from a heading.

    FedRAMP SSP templates are inconsistent: the same section appears as
    ``DevOps/DevSecOps Management`` in one document and ``10.8 DevOps/DevSecOps
    Management`` in another. The contract declares the unnumbered form, so we
    normalize by removing a leading dotted/appendix number so both match without
    needing a per-property ``aliasSourceHeading`` for every numbered variant.

    Examples stripped: ``10 ``, ``10.8 ``, ``13.11 ``, ``14.2 ``, ``A.1 ``,
    ``Appendix A – `` is NOT stripped here (handled by regex headings).
    """
    # Leading multi-level number like "10", "10.8", "13.11.2" followed by space.
    return re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s+", "", title or "").strip()


def _heading_matches(section_title: str, source_heading: str) -> bool:
    """Match a section title against a contract sourceHeading.

    Supports:
      - exact match (case-insensitive, whitespace-normalized)
      - exact match after stripping a leading section number ("10.8 X" == "X")
      - prefix match if source_heading ends with `*`
      - regex match if source_heading starts with `regex:`
    """
    s = re.sub(r"\s+", " ", section_title).strip().lower()
    h = source_heading.strip()
    if h.startswith("regex:"):
        pat = h[len("regex:") :]
        try:
            return re.search(pat, section_title, re.IGNORECASE) is not None
        except re.error:
            return False
    if h.endswith("*"):
        prefix = h[:-1].strip().lower()
        prefix = re.sub(r"\s+", " ", prefix)
        s_nonum = re.sub(r"\s+", " ", _strip_section_number(section_title)).lower()
        return s.startswith(prefix) or s_nonum.startswith(prefix)
    h_norm = re.sub(r"\s+", " ", h).lower()
    if s == h_norm:
        return True
    # Numbered/unnumbered reconciliation: "10.8 DevOps..." matches "DevOps...".
    s_nonum = re.sub(r"\s+", " ", _strip_section_number(section_title)).lower()
    return s_nonum == h_norm


def _heading_matches_any(
    section_title: str,
    source_heading: Optional[str],
    alias_source_heading: Optional[str] = None,
) -> bool:
    """Match a section title against the primary sourceHeading OR any alias.

    R14: a property may declare an ``aliasSourceHeading`` (in addition to
    its primary ``sourceHeading``) so the same routing rule can match
    either the unnumbered ("Data Flow") or numbered ("10.4 Data Flow")
    form found in different SSP templates.
    """
    if source_heading and _heading_matches(section_title, source_heading):
        return True
    if alias_source_heading:
        for alias in alias_source_heading.split("\n"):
            alias = alias.strip()
            if alias and _heading_matches(section_title, alias):
                return True
    return False


# ---------------------------------------------------------------------------
# Capture strategies
# ---------------------------------------------------------------------------


def _capture(pm: PropertyMapping, sections: List[HeadingSection]) -> Optional[Any]:
    """Apply the capture strategy to extract a value for one property."""
    strat = pm.capture_strategy
    if strat is None:
        return None
    if strat == "constant":
        return pm.constant_value
    if strat not in CAPTURE_STRATEGIES:
        raise ValueError(
            f"Unknown captureStrategy {strat!r} on {pm.object_id}.{pm.name}"
        )

    if not pm.source_heading:
        return None

    # Find matching sections (could be multiple if heading repeats or regex).
    # R14: also honor the alias_source_heading list so the same property
    # can match e.g. "Data Flow" OR "10.4 Data Flow" from different SSP
    # templates.
    matches = [
        s
        for s in sections
        if _heading_matches_any(s.title, pm.source_heading, pm.alias_source_heading)
    ]
    if not matches and strat != "control_table":
        return None

    # Default scope: content directly under this heading (not sub-headings).
    # Since parse_catchall_blob only attaches direct content to each section,
    # using the matched section's own lists gives us the right scope.
    scope = pm.capture_scope or "immediate_only"

    if strat == "heading_paragraph":
        texts: List[str] = []
        for s in matches:
            if scope == "until_next_same_or_higher":
                # Include this section + all descendant sections
                texts.extend(p for p in s.paragraphs if p)
                # Walk descendants in document order
                idx = sections.index(s)
                for desc in sections[idx + 1 :]:
                    if desc.level <= s.level:
                        break
                    texts.extend(p for p in desc.paragraphs if p)
            else:
                texts.extend(p for p in s.paragraphs if p)
        return "\n\n".join(t for t in texts if t) or None

    if strat == "heading_paragraph_first":
        for s in matches:
            for p in s.paragraphs:
                if p.strip():
                    return p.strip()
        return None

    if strat == "heading_table_first":
        for s in matches:
            if s.tables:
                t = s.tables[0]
                return {
                    "table_index": t.table_index,
                    "headers": t.headers,
                    "rows": t.data_rows,
                }
        return None

    if strat == "heading_table_all":
        out: List[Dict[str, Any]] = []
        for s in matches:
            target_sections = [s]
            if scope == "until_next_same_or_higher":
                idx = sections.index(s)
                for desc in sections[idx + 1 :]:
                    if desc.level <= s.level:
                        break
                    target_sections.append(desc)
            for ts in target_sections:
                for t in ts.tables:
                    out.append(
                        {
                            "table_index": t.table_index,
                            "headers": t.headers,
                            "rows": t.data_rows,
                            "control_id": t.control_id,
                        }
                    )
        return out or None

    if strat == "heading_image_first":
        for s in matches:
            if s.images:
                img = s.images[0]
                return {
                    "image_index": img.image_index,
                    "rId": img.rid,
                    "part": img.part,
                }
        return None

    if strat == "heading_image_all":
        out_imgs: List[Dict[str, Any]] = []
        for s in matches:
            for img in s.images:
                out_imgs.append(
                    {
                        "image_index": img.image_index,
                        "rId": img.rid,
                        "part": img.part,
                    }
                )
        return out_imgs or None

    if strat == "heading_all":
        out_lines: List[str] = []
        for s in matches:
            out_lines.append(f"# {s.title}")
            out_lines.extend(s.raw_lines)
        return "\n".join(out_lines) or None

    if strat == "control_table":
        # Walk all sections matching the sourceHeading and collect every
        # ParsedTable flagged as a control table (CONTROL TABLE (CAPTURED)).
        # Applies parse_control_table_record() to each and returns the list.
        # This strategy is the contract-driven equivalent of fismatic's primary
        # Chapter 13 parser and enables full control extraction via the YAML
        # contract without touching parser.py.
        records: List[Dict[str, Any]] = []
        for s in matches:
            for tbl in s.tables:
                if tbl.control_id is not None:
                    # ParsedTable.control_id is set only for CONTROL TABLE (CAPTURED)
                    records.append(parse_control_table_record(tbl))
        # For broad regex headings (e.g. regex:^13\.) also scan all sections
        # since the Chapter 13 heading structure varies across SSP templates.
        if not records:
            for s in sections:
                for tbl in s.tables:
                    if tbl.control_id is not None:
                        records.append(parse_control_table_record(tbl))
        return records or None

    if strat == "derived":
        # `derived` is a hook for future expansion — parser doesn't compute
        # anything by default; downstream consumers can re-derive.
        return None

    return None


# ---------------------------------------------------------------------------
# Top-level extraction
# ---------------------------------------------------------------------------


@dataclass
class ExtractedRecord:
    object_id: str
    object_name: str
    oscal_type: Optional[str]
    oscal_model: Optional[str]
    note_type: Optional[str]
    destination_folder: Optional[str]
    multiple: bool
    properties: Dict[str, Any] = field(default_factory=dict)
    unfilled: List[str] = field(default_factory=list)  # property names with no value


def extract(markdown: str, contract: Contract) -> Dict[str, Any]:
    """Extract routed records from a markdown blob per a contract.

    Returns a dict shaped as:
        {
          "objects": [
            {
              "object_id": "...",
              "object_name": "...",
              "oscal_type": "...",
              "oscal_model": "...",
              "note_type": "...",
              "destination_folder": "...",
              "multiple": bool,
              "properties": {name: value, ...},
              "unfilled": [name, ...],
            },
            ...
          ],
          "summary": {
            "objects_total": N,
            "objects_with_data": N,
            "properties_total": N,
            "properties_filled": N,
            "properties_capturable": N,  # total minus `derived`/UUID props
            "unfilled": [{object_id, name, sourceHeading}, ...],
          },
        }
    """
    sections = parse_catchall_blob(markdown)
    out_records: List[ExtractedRecord] = []
    for obj in contract.destinations():
        # D-9 / #43: objects filled by the primary parser (fismatic) are not
        # re-extracted here; skipping them keeps their properties out of the
        # "unfilled" tally (which otherwise depressed the coverage metric with
        # fields this parser was never meant to fill).
        if obj.handled_by_primary_parser:
            continue
        rec = ExtractedRecord(
            object_id=obj.object_id,
            object_name=obj.name,
            oscal_type=obj.oscal_type,
            oscal_model=obj.oscal_model,
            note_type=obj.note_type,
            destination_folder=obj.destination_folder,
            multiple=obj.multiple,
        )
        for pm in obj.properties:
            val = _capture(pm, sections)
            if val is not None and val != "":
                rec.properties[pm.name] = val
            else:
                rec.unfilled.append(pm.name)
        out_records.append(rec)

    # Build summary
    # Total properties comes from the CONTRACT (not the extracted records,
    # whose .properties dict only holds filled values). We also compute a
    # "capturable" denominator (D-12 / #47) that excludes `derived` properties
    # (UUIDs and other computed fields the parser never fills from headings),
    # so the fill rate reflects content actually recoverable from the source.
    total_props = sum(
        len(contract.objects_by_id[r.object_id].properties) for r in out_records
    )
    filled_props = sum(len(r.properties) for r in out_records)
    capturable_props = sum(
        sum(
            1
            for p in contract.objects_by_id[r.object_id].properties
            if p.capture_strategy != "derived"
        )
        for r in out_records
    )
    unfilled_list = [
        {
            "object_id": r.object_id,
            "name": name,
            "sourceHeading": next(
                (p.source_heading for p in o.properties if p.name == name),
                None,
            ),
        }
        for r in out_records
        for o in [contract.objects_by_id[r.object_id]]
        for name in r.unfilled
    ]

    return {
        "objects": [
            {
                "object_id": r.object_id,
                "object_name": r.object_name,
                "oscal_type": r.oscal_type,
                "oscal_model": r.oscal_model,
                "note_type": r.note_type,
                "destination_folder": r.destination_folder,
                "multiple": r.multiple,
                "properties": r.properties,
                "unfilled": r.unfilled,
            }
            for r in out_records
        ],
        "summary": {
            "objects_total": len(out_records),
            "objects_with_data": sum(1 for r in out_records if r.properties),
            "properties_total": total_props,
            "properties_filled": filled_props,
            "properties_capturable": capturable_props,
            "unfilled": unfilled_list,
        },
    }


# ---------------------------------------------------------------------------
# Convenience: docx → markdown → routed records
# ---------------------------------------------------------------------------


def extract_from_docx(docx_path: str | Path, contract: Contract) -> Dict[str, Any]:
    """Run the catchall dumper on a .docx, then extract via the contract.

    The dumper is imported from the core's ``compliance.scripts`` package so
    this path works in isolation and does not depend on another test mutating
    ``sys.path`` first.
    """
    import contextlib
    import io
    import os as _os

    from compliance.scripts import docx_catchall_dump as dcd

    out_path = (
        Path(_os.environ.get("TMPDIR", "/tmp")) / f"_catchall_{Path(docx_path).stem}.md"
    )
    with contextlib.redirect_stdout(io.StringIO()):
        # Lossless: the extraction path must see every row/cell/paragraph, not
        # the truncated human-review blob (else large tables lose rows, e.g.
        # a "Types of Users" table with "… 8 rows omitted …").
        stats = dcd.dump_docx(Path(docx_path), out_path, full=True)  # type: ignore[attr-defined]
    md = out_path.read_text(encoding="utf-8")
    result = extract(md, contract)
    result["source"] = {"docx": str(docx_path), "stats": stats}
    return result
