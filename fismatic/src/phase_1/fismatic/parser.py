"""
Text- and table-based parsing utilities for FedRAMP SSP control implementation content.

This module turns the free-form checkbox/narrative text found in FedRAMP SSP
"Implementation Status" / "Control Origination" blocks (and the surrounding
narrative) into structured data, and turns docx control-summary tables into
the same structured shape so `SSPConverter`/`DocxParser` can build valid
OSCAL SSP content.
"""

import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .text_matcher import extract_control_id as _tm_extract_control_id
from .text_matcher import extract_related_controls as _tm_extract_related_controls

# ---------------------------------------------------------------------------
# Control ID handling
# ---------------------------------------------------------------------------

_CONTROL_ID_PREFIXES = ("security control", "control", "requirement", "req.", "part")
_CONTROL_ID_RE = re.compile(
    r"([a-z]{2})[\s-]*0*(\d+)(?:(?:\s*\(0*(\d+|[a-z]+)\))|(?:\.0*(\d+|[a-z]+)))?"
)


def normalize_control_id(control_id: Optional[str]) -> Optional[str]:
    """Normalize a control ID to standard 'AC-1' / 'AC-1(1)' form."""
    if not control_id:
        return None

    text = control_id.strip().lower()

    for prefix in _CONTROL_ID_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()

    match = _CONTROL_ID_RE.fullmatch(text)
    if not match:
        return None

    family, number, parenthesized, dotted = match.groups()
    enhancement = parenthesized or dotted
    base = f"{family.upper()}-{number}"
    return f"{base}({enhancement})" if enhancement else base


def parse_control_id(
    text: Optional[str], config: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """Extract and normalize a control ID from arbitrary cell/heading text."""
    if not text:
        return None
    pat = config.get("controlIdRegex") if config else None
    if pat:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) >= 2:
                family = groups[0]
                number = groups[1]
                enhancement = groups[2] if len(groups) >= 3 else None
                base = f"{family.upper()}-{number}"
                return f"{base}({enhancement})" if enhancement else base
    found = _tm_extract_control_id(text)
    if found:
        return normalize_control_id(found.replace(" ", ""))
    return normalize_control_id(text)


def is_control_heading(text: str) -> bool:
    """True if `text` looks like a control-summary/table heading."""
    if not text:
        return False
    indicators = [
        r"control\s+summary",
        r"implementation\s+status",
        r"control\s+origination",
        r"describe\s+how\s+the\s+control",
        r"control\s+enhancement\s+summary",
    ]
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in indicators)


def generate_statement_id(
    control_id: str, part: Optional[str] = None, index: Optional[int] = None
) -> str:
    """Generate an OSCAL-ish statement id such as 'ac-1_stmt.a' or 'ac-1_stmt.1'."""
    base = re.sub(r"\s+", "", (control_id or "control").strip().lower())
    suffix = (
        str(part) if part not in (None, "") else str(index if index is not None else 1)
    )
    return f"{base}_stmt.{suffix}"


def is_control_table(table: Any) -> Tuple[bool, Optional[str]]:
    """Best-effort check for whether a docx table is a control-summary table."""
    if not table or not getattr(table, "rows", None) or len(table.rows) < 3:
        return False, None

    try:
        header_text = " ".join(
            cell.text.strip()
            for row in table.rows[: min(3, len(table.rows))]
            for cell in row.cells
            if cell.text and cell.text.strip()
        )
    except Exception:
        return False, None

    control_id = parse_control_id(header_text)
    if not control_id:
        return False, None

    if (
        not is_control_heading(header_text)
        and "implementation status" not in header_text.lower()
        and "control origination" not in header_text.lower()
    ):
        return False, None

    return True, control_id


# ---------------------------------------------------------------------------
# Implementation status / control origination text parsing
# ---------------------------------------------------------------------------

_STATUS_HEADER_RE = re.compile(r"^implementation\s+status\s*:?\s*$", re.IGNORECASE)
_ORIGIN_HEADER_RE = re.compile(r"^control\s+origination\s*:?\s*$", re.IGNORECASE)
_SECTION_HEADER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .,'&()/-]{0,80}:$")

_STATUS_KEYWORDS = [
    ("implemented", r"^implemented\b"),
    ("partially_implemented", r"^partially\s+implemented\b"),
    ("planned", r"^planned\b"),
    ("alternative_implementation", r"^alternative\s+implementation\b"),
    ("configured_by_customer", r"^configured\s+by\s+customer\b"),
    ("not_applicable", r"^not\s+applicable\b"),
]

_ORIGINATION_KEYWORDS = [
    ("inherited", r"^inherited\s+from"),
    ("hybrid", r"^(?:hybrid\s+control|service\s+provider\s+hybrid)"),
    (
        "system_specific",
        r"^(?:system\s+specific\s+control|service\s+provider\s+system\s+specific)",
    ),
    (
        "service_provider_corporate",
        r"(?:common\s+control|service\s+provider\s+corporate)",
    ),
    ("configured_by_customer", r"^configured\s+by\s+customer"),
    ("provided_by_customer", r"^provided\s+by\s+customer"),
    ("shared", r"^shared\b"),
]


_CHECKED_LINE_RE = re.compile(
    r"^[☒☑✓⊠]",  # U+2612 ballot-box-with-X, U+2611 ballot-box-with-check, U+2713 check-mark, U+22A0 squared-check
)
_UNCHECKED_LINE_RE = re.compile(r"^[☐]")
_BRACKET_CHECKED_RE = re.compile(r"^\[[ xX]\]")
# Plain 'x' or 'X' at start of line (after whitespace-strip), but only if
# the line does NOT start with a known-unchecked character.
_PLAIN_X_RE = re.compile(r"^[xX]\b")


def _is_checked(line: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """True if this line looks like a "checked" checkbox."""
    checked_pat = config.get("checkboxCheckedRegex") if config else None
    if checked_pat:
        return bool(re.search(checked_pat, line))
    return bool(
        _CHECKED_LINE_RE.match(line)
        or _BRACKET_CHECKED_RE.match(line)
        or (_PLAIN_X_RE.match(line) and not _UNCHECKED_LINE_RE.match(line))
    )


def _is_checkbox_line(line: str) -> bool:
    return bool(
        _CHECKED_LINE_RE.match(line)
        or _UNCHECKED_LINE_RE.match(line)
        or _BRACKET_CHECKED_RE.match(line)
        or _PLAIN_X_RE.match(line)
    )


def _strip_checkbox(line: str) -> str:
    content = line.lstrip("☒☑☐✓⊠").strip()
    content = re.sub(r"^\[[ xX]\]\s*", "", content).strip()
    content = re.sub(r"^[xX]\s+", "", content).strip()
    return content


def parse_implementation_status(
    text: Optional[str], config: Optional[Dict[str, Any]] = None
) -> Dict[str, bool]:
    """Parse a FedRAMP 'Implementation Status:' checkbox block into flags."""
    status = {key: False for key, _ in _STATUS_KEYWORDS}
    if not text:
        return status

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if config and "checkboxCheckedRegex" in config:
            if not _is_checked(line, config):
                continue
        else:
            if not _is_checkbox_line(line):
                continue
            if not _is_checked(line):
                continue

        content = _strip_checkbox(line).lower()
        for key, pattern in _STATUS_KEYWORDS:
            if re.match(pattern, content):
                status[key] = True

    return status


def parse_control_origination(
    text: Optional[str], config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Parse a FedRAMP 'Control Origination:' checkbox block into flags."""
    origination: Dict[str, Any] = {
        "inherited": False,
        "inherited_from": None,
        "hybrid": False,
        "hybrid_systems": [],
        "system_specific": False,
        "service_provider_corporate": False,
        "configured_by_customer": False,
        "provided_by_customer": False,
        "shared": False,
    }
    if not text:
        return origination

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if config and "checkboxCheckedRegex" in config:
            if not _is_checked(line, config):
                continue
        else:
            if not _is_checkbox_line(line):
                continue
            if not _is_checked(line):
                continue

        content = _strip_checkbox(line)
        content_lower = content.lower()
        without_parens = re.sub(r"\([^)]*\)", "", content_lower).strip()

        for key, pattern in _ORIGINATION_KEYWORDS:
            if re.search(pattern, without_parens):
                origination[key] = True

        if origination["inherited"]:
            inherited_pat = (
                config.get("inheritedFromRegex", r"inherited\s+from:?\s*(.+)")
                if config
                else r"inherited\s+from:?\s*(.+)"
            )
            match = re.search(inherited_pat, content, flags=re.IGNORECASE)
            if match:
                origination["inherited_from"] = (
                    match.group(1).strip().rstrip(".") or None
                )

        if origination["hybrid"]:
            hybrid_pat = (
                config.get("hybridSystemsRegex", r"shared\s+between\s+(.+?)(?:;|\)|$)")
                if config
                else r"shared\s+between\s+(.+?)(?:;|\)|$)"
            )
            between = re.search(hybrid_pat, content, flags=re.IGNORECASE)
            if between:
                systems = re.split(r"\s+and\s+", between.group(1))
                origination["hybrid_systems"] = [
                    s.strip().strip(",") for s in systems if s.strip()
                ]

    return origination


def _status_to_oscal_state(status: Dict[str, bool]) -> str:
    """Collapse a status-flags dict into a single OSCAL implementation-status state."""
    if status.get("implemented"):
        return "implemented"
    if status.get("partially_implemented"):
        return "partially-implemented"
    if status.get("alternative_implementation"):
        return "alternative-implementation"
    if status.get("planned"):
        return "planned"
    if status.get("configured_by_customer"):
        return "customer-configured"
    if status.get("not_applicable"):
        return "not-applicable"
    return "not-implemented"


def _origination_to_oscal_list(origination: Dict[str, Any]) -> List[str]:
    """Collapse an origination-flags dict into a list of OSCAL control-origination values."""
    mapping = [
        ("inherited", "inherited"),
        ("hybrid", "hybrid"),
        ("system_specific", "system-specific"),
        ("service_provider_corporate", "service-provider-corporate"),
        ("configured_by_customer", "customer-configured"),
        ("provided_by_customer", "customer-provided"),
        ("shared", "shared"),
    ]
    return [oscal_value for key, oscal_value in mapping if origination.get(key)]


# ---------------------------------------------------------------------------
# Free-text narrative parsing (statements, customer responsibilities, etc.)
# ---------------------------------------------------------------------------

_OBLIGATION_RE = re.compile(
    r"\b(must|shall|required\s+to|responsible\s+for|have\s+to|has\s+to|need(?:s)?\s+to|should)\b",
    re.IGNORECASE,
)
_REQUIREMENT_ACTOR_RE = re.compile(
    r"\b(the\s+system|the\s+service\s+provider|the\s+organization|provider[s]?)\b.*\b(must|shall|implements?|provides?)\b",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r"(?:see\s+also|see|reference[s]?:?)\s+([^\n.;]+)", re.IGNORECASE
)


def _split_status_and_origination_blocks(text: str) -> Tuple[str, str, str]:
    """
    Split raw statement/description text into (status_block, origination_block, narrative).

    Lines are attributed to whichever checkbox block is currently active (based on
    the most recently seen 'Implementation Status:'/'Control Origination:' header);
    any non-checkbox line ends the active block and is treated as narrative text.
    """
    status_lines: List[str] = []
    origination_lines: List[str] = []
    narrative_lines: List[str] = []
    mode: Optional[str] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            narrative_lines.append("")
            continue

        if _STATUS_HEADER_RE.match(line):
            mode = "status"
            continue

        if _ORIGIN_HEADER_RE.match(line):
            mode = "origination"
            continue

        if _is_checkbox_line(line) and mode in ("status", "origination"):
            (status_lines if mode == "status" else origination_lines).append(line)
            continue

        mode = None
        narrative_lines.append(raw_line)

    return (
        "\n".join(status_lines),
        "\n".join(origination_lines),
        "\n".join(narrative_lines),
    )


def _split_sentences(line: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip()]


def extract_customer_responsibilities(text: Optional[str]) -> List[str]:
    """Extract sentences describing an obligation (customer, site owner, etc.)."""
    if not text:
        return []

    responsibilities = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _SECTION_HEADER_RE.match(line):
            continue
        for sentence in _split_sentences(line):
            if _OBLIGATION_RE.search(sentence):
                responsibilities.append(sentence)

    return responsibilities


def _extract_implementation_requirements(narrative: str) -> List[str]:
    """Extract sentences describing what the system/service provider must implement."""
    if not narrative:
        return []

    requirements = []
    for raw_line in narrative.splitlines():
        line = raw_line.strip()
        if not line or _SECTION_HEADER_RE.match(line):
            continue
        for sentence in _split_sentences(line):
            if _REQUIREMENT_ACTOR_RE.search(sentence):
                requirements.append(sentence)

    return requirements


def _extract_references(text: str) -> List[str]:
    if not text:
        return []
    return [
        match.group(1).strip()
        for match in _REFERENCE_RE.finditer(text)
        if match.group(1).strip()
    ]


def _extract_sections(narrative: str) -> Dict[str, str]:
    """Group narrative text under any 'Party Name:' style headers it contains."""
    sections: Dict[str, List[str]] = {"unstructured_content": []}
    current_key = "unstructured_content"

    for raw_line in narrative.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _SECTION_HEADER_RE.match(line):
            current_key = line[:-1].strip()
            sections.setdefault(current_key, [])
            continue
        sections.setdefault(current_key, []).append(line)

    return {key: "\n".join(lines) for key, lines in sections.items() if lines}


def parse_implementation_details(text: Optional[str]) -> Dict[str, Any]:
    """
    Parse a control statement/description into structured implementation details.

    Returns a dict with keys: implementation_status, control_origination,
    related_controls, implementation_requirements, customer_responsibilities,
    references, sections.
    """
    text = text or ""

    status_block, origination_block, narrative = _split_status_and_origination_blocks(
        text
    )

    return {
        "implementation_status": parse_implementation_status(status_block),
        "control_origination": parse_control_origination(origination_block),
        "related_controls": sorted(set(_tm_extract_related_controls(text))),
        "implementation_requirements": _extract_implementation_requirements(narrative),
        "customer_responsibilities": extract_customer_responsibilities(narrative),
        "references": _extract_references(text),
        "sections": _extract_sections(narrative),
    }


# ---------------------------------------------------------------------------
# docx table -> structured control data
# ---------------------------------------------------------------------------


def sanitize_responsible_role(text: Optional[str]) -> Optional[str]:
    """Sanitize raw 'Responsible Role' cell content from FedRAMP tables.

    FedRAMP table cells often contain paragraphs of narrative prose. This helper
    extracts clean, concise role titles suitable for OSCAL role-ids and party mappings.
    """
    if not text or not text.strip():
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    first = lines[0]
    if ":" in first:
        head, _ = first.split(":", 1)
        if len(head.strip()) <= 60 and head.strip():
            first = head.strip()
    if len(first) > 80:
        first = first[:77].strip() + "..."
    return first or None


def parse_control_table(
    table: Any, config: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Parse a docx control-summary (CIS) table into a flat dict for `Control()`."""
    if not table or not getattr(table, "rows", None):
        return None

    try:
        header_cell_text = table.cell(0, 0).text
    except Exception:
        return None

    control_id = parse_control_id(header_cell_text, config=config)
    if not control_id:
        return None

    responsible_role = None
    status_text = ""
    origination_text = ""

    # Load label pattern config
    status_label = (
        config.get("implStatusRowLabel", "implementation status")
        if config
        else "implementation status"
    )
    origin_label = (
        config.get("ctrlOriginationRowLabel", "control origination")
        if config
        else "control origination"
    )
    role_label = (
        config.get("responsibleRoleRowLabel", "responsible + role")
        if config
        else "responsible + role"
    )

    for row in table.rows:
        try:
            label = row.cells[0].text.strip().lower()
            value_text = row.cells[-1].text
        except Exception:
            continue

        if "+" in role_label:
            is_role = all(part.strip() in label for part in role_label.split("+"))
        else:
            is_role = bool(re.search(role_label, label))

        if is_role:
            responsible_role = sanitize_responsible_role(value_text)
            if responsible_role:
                responsible_role = responsible_role.strip()
        elif status_label in label:
            status_text = value_text
        elif origin_label in label:
            origination_text = value_text

    status = parse_implementation_status(status_text, config=config)
    origination = parse_control_origination(origination_text, config=config)

    return {
        "control_id": control_id,
        "responsible_role": responsible_role,
        "implementation_status": _status_to_oscal_state(status),
        "control_origination": _origination_to_oscal_list(origination),
        "inherited_from": origination.get("inherited_from"),
        "hybrid_systems": origination.get("hybrid_systems") or [],
        "table": table,
        "is_control_table": True,
    }


def extract_implementation_details(
    control_data: Any, config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Extract additional Control fields from either:
      - a raw docx continuation table, passed as {'table': <docx Table>}, or
      - the dict already produced by `parse_control_table` (in which case,
        if narrative/part rows exist inside the summary table, they are extracted).
    """
    if not isinstance(control_data, dict):
        return {}

    table = control_data.get("table")
    if table is None:
        return {}

    is_ctrl = control_data.get("is_control_table", False)

    part_pat = (
        config.get("partRowRegex", r"(?:part|req\.?)\s*([a-z0-9]+)")
        if config
        else r"(?:part|req\.?)\s*([a-z0-9]+)"
    )
    part_re = (
        re.compile(part_pat, re.IGNORECASE) if isinstance(part_pat, str) else part_pat
    )

    implementation: Dict[str, str] = {}

    status_label = (
        config.get("implStatusRowLabel", "implementation status")
        if config
        else "implementation status"
    ).lower()
    origin_label = (
        config.get("ctrlOriginationRowLabel", "control origination")
        if config
        else "control origination"
    ).lower()
    role_label = (
        config.get("responsibleRoleRowLabel", "responsible + role")
        if config
        else "responsible + role"
    ).lower()

    for row in getattr(table, "rows", []):
        try:
            label = row.cells[0].text.strip()
            value = row.cells[-1].text.strip()
        except Exception:
            continue
        if not label or not value:
            continue

        label_lower = label.lower()

        # Skip headers, prompts, and CIS rows if this is a control table
        if is_ctrl:
            control_id = control_data.get("control_id")
            if (
                control_id
                and len(label) < 20
                and (
                    control_id.lower() in label_lower
                    or normalize_control_id(parse_control_id(label, config=config))
                    == normalize_control_id(control_id)
                )
            ):
                continue
            if status_label in label_lower:
                continue
            if origin_label in label_lower:
                continue
            is_merged_narrative = len(label) > 40 and label == value
            if not is_merged_narrative:
                if "+" in role_label:
                    if all(p.strip() in label_lower for p in role_label.split("+")):
                        continue
                elif bool(re.search(role_label, label_lower)):
                    continue
            if "describe how" in label_lower or "control summary" in label_lower:
                continue

        part_match = part_re.match(label)
        if part_match:
            part_key = part_match.group(1)
        else:
            # If label is long or equal to value (merged cell), it is narrative text, not a label column.
            # In that case, use 'a' as a fallback part key (only inside a confirmed control table).
            if len(label) > 20 or label == value:
                if not is_ctrl:
                    continue
                part_key = "a"
            else:
                part_match_general = re.match(
                    r"(?:part|req\.?)\s*([a-z0-9]+)", label, re.IGNORECASE
                )
                if part_match_general:
                    part_key = part_match_general.group(1)
                elif not is_ctrl:
                    # Non-control tables (e.g. Acronym/Glossary or Appendix tables after SR-12)
                    # must not turn arbitrary column-0 labels into fake statement parts.
                    continue
                else:
                    part_key = re.sub(r"[^A-Za-z0-9_-]", "_", label.strip())
                    if not part_key:
                        part_key = "a"
        existing = implementation.get(part_key, "")
        if existing and value not in existing:
            implementation[part_key] = f"{existing}\n\n{value}"
        elif not existing:
            implementation[part_key] = value

    return {"implementation": implementation} if implementation else {}


def extract_control_content(table: Any, control_id: str) -> Optional[Dict[str, Any]]:
    """
    Build an OSCAL-shaped `implemented-requirement` dict directly from a single
    docx table containing both the checkbox summary and narrative text.
    """
    if not table:
        return None

    try:
        rows_text = [
            "\n".join(cell.text for cell in row.cells if cell.text)
            for row in table.rows
        ]
    except Exception as exc:
        print(f"Warning: Error extracting control content - {exc}")
        return None

    full_text = "\n".join(rows_text)
    parsed = parse_implementation_details(full_text)

    statements = []
    for index, (section, content) in enumerate(parsed["sections"].items(), start=1):
        if section == "unstructured_content" or not content.strip():
            continue
        statements.append(
            {
                "statement-id": generate_statement_id(control_id, index=index),
                "uuid": str(uuid.uuid4()),
                "description": content.strip(),
            }
        )

    return {
        "control-id": control_id,
        "implementation-status": {
            "state": _status_to_oscal_state(parsed["implementation_status"])
        },
        "control-origination": _origination_to_oscal_list(
            parsed["control_origination"]
        ),
        "statements": statements,
    }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_control_data(control: Any) -> Tuple[bool, List[str]]:
    """Validate a `fismatic.control.Control` instance before OSCAL export."""
    errors: List[str] = []

    control_id = getattr(control, "name", None) or getattr(control, "number", None)
    if not control_id:
        errors.append("missing control id")

    if getattr(control, "implementation_status", None) is None:
        errors.append("missing implementation status")

    return (len(errors) == 0, errors)


def load_contract_config(contract_path: Optional[str]) -> Dict[str, Any]:
    """Load configuration variables from a YAML contract file."""
    import yaml

    config = {
        "controlIdRegex": r"([a-z]{2})[\s-]*(\d+)(?:\s*\((\d+|[a-z]+)\))?",
        "checkboxCheckedRegex": r"^[☒☑✓⊠]|^\[[ xX]\]|^[xX]\b",
        "implStatusRowLabel": "implementation status",
        "ctrlOriginationRowLabel": "control origination",
        "responsibleRoleRowLabel": "responsible + role",
        "partRowRegex": r"(?:part|req\.?)\s*([a-z0-9]+)",
        "parameterRowRegex": r"^[Pp]arameter\s+(\S+):?\s*$",
    }
    if not contract_path:
        return config
    try:
        with open(contract_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            for obj in data.get("schema", []):
                if (
                    obj.get("id") == "oscal_control"
                    or obj.get("name") == "oscal_control"
                ):
                    for prop in obj.get("customProperties", []):
                        prop_name = prop.get("property")
                        prop_val = prop.get("value")
                        if prop_name in config:
                            config[prop_name] = prop_val
                    # Check customProperties inside properties of the schema object
                    for prop_obj in obj.get("properties", []):
                        for cp in prop_obj.get("customProperties", []):
                            prop_name = cp.get("property")
                            prop_val = cp.get("value")
                            if prop_name in config:
                                config[prop_name] = prop_val
    except Exception as e:
        print(f"Warning: Failed to load contract config from {contract_path}: {e}")
    return config
