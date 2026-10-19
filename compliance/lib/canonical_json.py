"""Utility for generating normalized, schema-stable JSON sidecars from vault markdown notes.

This module parses a markdown note (YAML frontmatter + body sections + Dataview
inline fields) and converts it into a deterministic JSON representation.
The canonical JSON is stored with a content hash in its metadata (`_meta`).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml
from jsonschema import Draft7Validator

from compliance.lib.table_records import parse_table_records, table_record_metrics
from compliance.lib.oscal_lineage import canonical_lineage_summary

_INSTRUCTION_METADATA_RE = re.compile(
    r"^<!-- oscal-vault-instruction (\{.*\}) -->$", re.MULTILINE
)


def parse_frontmatter(path: Path) -> Tuple[Dict[str, Any], str]:
    """Extract frontmatter and body from a markdown file.

    Returns:
        Tuple of (frontmatter dict, body text string).

    Raises:
        ValueError if the file cannot be read, lacks frontmatter, or has invalid YAML.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        raise ValueError(f"Cannot read file: {e}")

    if not text.startswith("---\n"):
        raise ValueError("File does not start with frontmatter delimiter '---'")

    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        raise ValueError("Unclosed frontmatter block")

    fm_text = text[4:end]
    body = text[end + 5 :]

    try:
        parsed = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML parse error in frontmatter: {e}")

    if not isinstance(parsed, dict):
        raise ValueError("Frontmatter is not a valid YAML mapping")

    return parsed, body


def parse_markdown_sections(body_text: str) -> List[Dict[str, Any]]:
    """Parse flat markdown body into a list of heading sections.

    Each section has:
      - heading: The heading text (without '#' prefixes), or None for content before the first heading.
      - level: The heading depth (1-6), or None for pre-heading content.
      - content: The raw body text of the section, stripped.

    ``level`` is retained because an H1 and an H3 with the same text are
    distinct source structures. Collapsing them to the same section hid a
    real document hierarchy and made loss across the export boundary invisible
    (lossless-pipeline gap, EPIC #43).
    """
    sections: List[Dict[str, Any]] = []
    current_heading: Optional[str] = None
    current_level: Optional[int] = None
    current_lines: List[str] = []

    for line in body_text.splitlines():
        # Match headings from H1 to H6
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            # Save previous section if there's any content or we had a heading
            if current_heading is not None or current_lines:
                sections.append(
                    {
                        "heading": current_heading,
                        "level": current_level,
                        "content": "\n".join(current_lines).strip(),
                    }
                )
            current_heading = m.group(2).strip()
            current_level = len(m.group(1))
            current_lines = []
        else:
            current_lines.append(line)

    # Save final section
    if current_heading is not None or current_lines:
        sections.append(
            {
                "heading": current_heading,
                "level": current_level,
                "content": "\n".join(current_lines).strip(),
            }
        )

    return sections


def parse_inline_fields(body_text: str) -> Dict[str, Any]:
    """Parse Dataview-style inline fields from the markdown body.

    Supports:
      - Line-level: `key:: value`
      - Bracketed: `[key:: value]`
      - Parenthesized: `(key:: value)`

    If a key occurs once, its value is a string. If it occurs multiple times,
    values are accumulated into a list.
    """
    fields: Dict[str, List[str]] = {}

    for line in body_text.splitlines():
        line = line.strip()
        # 1. Check for line-level field (key:: value)
        m = re.match(r"^([a-zA-Z0-9_-]+)::\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            fields.setdefault(key, []).append(val)
            continue

        # 2. Check for bracketed or parenthesized fields anywhere on the line
        matches = re.findall(
            r"(?:\[|\()([a-zA-Z0-9_-]+)::\s*([^\]\)]+?)(?:\]|\))", line
        )
        for key, val in matches:
            fields.setdefault(key, []).append(val.strip())

    # Simplify single-value lists to scalar strings
    result: Dict[str, Any] = {}
    for k, v in fields.items():
        if len(v) == 1:
            result[k] = v[0]
        else:
            result[k] = v
    return result


def generate_canonical_data(
    markdown_path: Path,
    vault_root: Path,
    generated_at_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate the deterministic canonical JSON structure for a markdown note.

    Args:
        markdown_path: Path to the note's markdown file.
        vault_root: Root directory of the vault.
        generated_at_override: Optional ISO timestamp to make metadata deterministic in tests.

    Returns:
        A dict containing the note's structured data and `_meta` block.
    """
    frontmatter, body = parse_frontmatter(markdown_path)
    body_sections = parse_markdown_sections(body)
    inline_fields = parse_inline_fields(body)
    instruction_sources: List[Dict[str, str]] = []
    for match in _INSTRUCTION_METADATA_RE.finditer(body):
        try:
            metadata = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if (
            isinstance(metadata, dict)
            and isinstance(metadata.get("source_locator"), str)
            and isinstance(metadata.get("source_hash"), str)
        ):
            instruction_sources.append(
                {
                    "source_locator": metadata["source_locator"],
                    "source_hash": metadata["source_hash"],
                }
            )
    table_records = parse_table_records(body)
    table_metrics = table_record_metrics(table_records)
    stable_id = next(
        (
            str(value)
            for key, value in frontmatter.items()
            if key.endswith("_id") and isinstance(value, str) and value.strip()
        ),
        None,
    )
    typed_references: List[Dict[str, str]] = []
    system_id = frontmatter.get("system_id")
    if isinstance(system_id, str) and system_id.strip():
        typed_references.append(
            {"relation": "scoped_to", "id": system_id, "type": "oscal.ssp"}
        )
    source_lineage: List[Dict[str, str]] = []
    source_table_id = frontmatter.get("source_table_id")
    if isinstance(source_table_id, str) and source_table_id:
        source_lineage.append(
            {"source_id": source_table_id, "canonical_pointer": "/source_table_id"}
        )
    for index, source_cell_id in enumerate(frontmatter.get("source_cell_ids") or []):
        if isinstance(source_cell_id, str) and source_cell_id:
            source_lineage.append(
                {
                    "source_id": source_cell_id,
                    "canonical_pointer": f"/source_cell_ids/{index}",
                }
            )

    # Build the main data mapping (excluding volatile metadata).
    #
    # ``raw_body`` preserves the exact markdown body verbatim. ``body_sections``
    # is a lossy structural projection (headings split out, content stripped);
    # keeping the raw body alongside it means the canonical sidecar can prove
    # byte-level fidelity of the note body and nothing in the section-splitter
    # can silently drop content without the hash changing (lossless-pipeline
    # gap, EPIC #43).
    data: Dict[str, Any] = {
        **frontmatter,
        "raw_body": body,
        "body_sections": body_sections,
        "inline_fields": inline_fields,
        "table_records": table_records,
        "table_record_metrics": table_metrics,
    }
    if instruction_sources:
        data["instruction_sources"] = instruction_sources
    if stable_id:
        data["stable_id"] = stable_id
    if typed_references:
        data["typed_references"] = typed_references
    if source_lineage:
        data["source_lineage"] = source_lineage
    mapping_capabilities = canonical_lineage_summary(
        data,
        Path(__file__).resolve().parents[1] / "datacontracts/fedramp-ssp-v1.yaml",
    )
    if mapping_capabilities:
        data["oscal_mapping_capabilities"] = mapping_capabilities

    # Calculate content hash over the stable data keys
    # Separators without spaces ensure platform/formatting independence
    serialized_for_hash = json.dumps(data, sort_keys=True, separators=(",", ":"))
    content_hash = (
        "sha256:" + hashlib.sha256(serialized_for_hash.encode("utf-8")).hexdigest()
    )

    # Relative path within the vault
    try:
        rel_path = markdown_path.relative_to(vault_root)
    except ValueError:
        rel_path = markdown_path

    # Build metadata block
    generated_at = (
        generated_at_override
        or datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    meta = {
        "schema_version": 1,
        "generated_from": str(rel_path),
        "generated_at": generated_at,
        "content_hash": content_hash,
        "exportable": frontmatter.get("exportable", True),
    }
    if instruction_sources:
        meta["instruction_sources"] = instruction_sources

    # Merge the meta block
    canonical_dict = {
        **data,
        "_meta": meta,
    }
    return canonical_dict


_CANONICAL_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "canonical"
    / "canonical_sidecar_schema_v1.json"
)
_CANONICAL_VALIDATOR: Optional[Draft7Validator] = None


def get_canonical_validator(
    schema_path: Optional[Path] = None,
) -> Draft7Validator:
    """Load and cache Draft7Validator for the versioned canonical sidecar schema (#49)."""
    global _CANONICAL_VALIDATOR
    p = schema_path or _CANONICAL_SCHEMA_PATH
    if _CANONICAL_VALIDATOR is None or schema_path is not None:
        with open(p, encoding="utf-8") as f:
            schema = json.load(f)
        validator = Draft7Validator(schema)
        if schema_path is None:
            _CANONICAL_VALIDATOR = validator
        return validator
    return _CANONICAL_VALIDATOR


def validate_canonical_sidecar(
    sidecar: Dict[str, Any],
    schema_path: Optional[Path] = None,
) -> List[str]:
    """Validate a canonical JSON sidecar against the versioned canonical schema (#49).

    Returns a list of error strings; empty if valid.
    """
    validator = get_canonical_validator(schema_path)
    errors = []
    for err in sorted(validator.iter_errors(sidecar), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{loc}: {err.message}")
    return errors


def normalize_canonical_record(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize date values, relationships, statement parts, and attachments (#49)."""
    out = dict(data)

    # 1. Normalize dates
    for d_key in ("date", "reviewed", "last_updated"):
        val = out.get(d_key)
        if isinstance(val, (datetime.date, datetime.datetime)):
            out[d_key] = val.isoformat()
        elif isinstance(val, str) and val.strip():
            # If date string is YYYY-MM-DD or standard ISO, ensure clean format
            out[d_key] = val.strip()

    # 2. Normalize statement parts (preserving provenance and custom fields)
    raw_stmts = out.get("statement_parts")
    if isinstance(raw_stmts, list):
        norm_stmts = []
        for s in raw_stmts:
            if isinstance(s, dict) and "id" in s:
                item = dict(s)
                item["id"] = str(s["id"]).strip()
                if "uuid" in s and s["uuid"]:
                    item["uuid"] = str(s["uuid"]).strip()
                if "text" in s and s["text"] is not None:
                    item["text"] = str(s["text"]).strip()
                norm_stmts.append(item)
            elif isinstance(s, str):
                norm_stmts.append({"id": s.strip()})
        out["statement_parts"] = norm_stmts

    # 3. Normalize related controls (preserving all attributes)
    raw_rel = out.get("related_controls")
    if isinstance(raw_rel, list):
        norm_rel = []
        for r in raw_rel:
            if isinstance(r, dict) and "control_id" in r:
                rel_item = dict(r)
                rel_item["control_id"] = str(r["control_id"]).strip().upper()
                rel_item["relationship"] = str(
                    r.get("relationship", "depends_on")
                ).strip()
                norm_rel.append(rel_item)
        out["related_controls"] = norm_rel

    # 4. Normalize attachments (preserving source_part, sha256, etc.)
    raw_att = out.get("attachments")
    if isinstance(raw_att, list):
        norm_att = []
        for a in raw_att:
            if isinstance(a, dict) and "id" in a and "path" in a:
                att_item = dict(a)
                att_item["id"] = str(a["id"]).strip()
                att_item["path"] = str(a["path"]).strip()
                norm_att.append(att_item)
        out["attachments"] = norm_att

    return out
