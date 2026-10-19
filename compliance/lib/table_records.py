"""Shared parsing, classification, and summary helpers for Markdown tables."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


TABLE_RECORD_SCHEMA_VERSION = 1

_SEPARATOR_CELL = re.compile(r":?-{3,}:?")
_PRESENTATION_HEADINGS = {
    "coverage",
    "mapping",
    "mappings",
    "crosswalk",
    "type registry",
}
_ASSET_INVENTORY_HEADINGS = {"asset inventory", "hardware inventory"}
_SOFTWARE_INVENTORY_HEADINGS = {"software inventory", "software components"}


def normalize_table_label(value: Any) -> str:
    """Normalize a heading or header for exact table-shape matching."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def split_markdown_table_row(line: str) -> List[str]:
    """Split a Markdown table row, with or without leading/trailing pipes."""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith(r"\|"):
        text = text[:-1]
    return [cell.replace(r"\|", "|").strip() for cell in re.split(r"(?<!\\)\|", text)]


def _is_separator(cells: List[str]) -> bool:
    return bool(cells) and all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def classify_table_record(
    heading: Optional[str], headers: List[str], *, malformed: bool = False
) -> Dict[str, str]:
    """Classify one complete table shape without partial/subset matches."""
    normalized = tuple(normalize_table_label(header) for header in headers)
    normalized_heading = normalize_table_label(heading)
    duplicate_headers = len(set(normalized)) != len(normalized)
    if (
        malformed
        or not normalized
        or any(not header for header in normalized)
        or duplicate_headers
    ):
        return {"classification": "opaque", "kind": "opaque", "disposition": "unmapped"}
    if normalized == ("service element", "response"):
        return {
            "classification": "fact_sheet",
            "kind": "external_service",
            "disposition": "mapped",
        }
    if normalized in {
        ("ports t or u", "protocols", "services"),
        ("ports", "protocols", "services"),
        ("ports", "protocol", "service"),
    }:
        return {
            "classification": "record_set",
            "kind": "protocol",
            "disposition": "mapped",
        }
    if normalized_heading == "provided by":
        return {
            "classification": "record_set",
            "kind": "inheritance_provider",
            "disposition": "mapped",
        }
    if normalized_heading in _ASSET_INVENTORY_HEADINGS and normalized in {
        ("asset", "owner"),
        ("asset", "type", "owner"),
        ("asset name", "asset type", "owner"),
    }:
        return {
            "classification": "record_set",
            "kind": "asset_inventory",
            "disposition": "mapped",
        }
    if normalized_heading in _SOFTWARE_INVENTORY_HEADINGS and normalized in {
        ("software", "version", "purpose"),
        ("software name", "version", "purpose"),
        ("product", "version", "function"),
    }:
        return {
            "classification": "record_set",
            "kind": "software_inventory",
            "disposition": "mapped",
        }
    if normalized_heading in _PRESENTATION_HEADINGS:
        return {
            "classification": "presentation_only",
            "kind": "opaque",
            "disposition": "unmapped",
        }
    return {"classification": "opaque", "kind": "opaque", "disposition": "unmapped"}


def parse_table_records(body_text: str) -> List[Dict[str, Any]]:
    """Parse all table-like Markdown blocks while preserving malformed blocks."""
    records: List[Dict[str, Any]] = []
    lines = body_text.splitlines()
    current_heading: Optional[str] = None
    fence: Optional[str] = None
    index = 0
    while index < len(lines):
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", lines[index])
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            index += 1
            continue
        if fence is not None:
            index += 1
            continue
        heading_match = re.match(r"^#{1,6}\s+(.*)$", lines[index])
        if heading_match:
            current_heading = heading_match.group(1).strip()
            index += 1
            continue
        if (
            "|" not in lines[index]
            or index + 1 >= len(lines)
            or "|" not in lines[index + 1]
        ):
            index += 1
            continue

        raw_lines: List[str] = []
        while index < len(lines) and "|" in lines[index] and lines[index].strip():
            raw_lines.append(lines[index])
            index += 1
        parsed_rows = [split_markdown_table_row(line) for line in raw_lines]
        headers = parsed_rows[0]
        separator = parsed_rows[1] if len(parsed_rows) > 1 else []
        has_separator = _is_separator(separator)
        rows = parsed_rows[2:] if has_separator else parsed_rows[1:]
        expected_width = len(headers)
        malformed = (
            not has_separator
            or len(separator) != expected_width
            or not rows
            or any(len(row) != expected_width for row in rows)
        )
        classification = classify_table_record(
            current_heading, headers, malformed=malformed
        )
        records.append(
            {
                "schema_version": TABLE_RECORD_SCHEMA_VERSION,
                "source_heading": current_heading,
                "source_locator": f"markdown:line[{index - len(raw_lines) + 1}]",
                "headers": headers,
                "rows": rows,
                "row_locators": [
                    f"markdown:line[{index - len(rows) + row_index + 1}]"
                    for row_index in range(len(rows))
                ],
                "valid": not malformed,
                **classification,
                "raw_markdown": "\n".join(raw_lines),
            }
        )
    return records


def table_record_metrics(records: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Return classification/structure/opacity/mapping coverage counts."""
    items = list(records)
    return {
        "tables_total": len(items),
        "tables_classified": sum(r.get("classification") != "opaque" for r in items),
        "tables_structured": sum(r.get("disposition") == "mapped" for r in items),
        "tables_opaque": sum(r.get("classification") == "opaque" for r in items),
        "tables_unmapped": sum(r.get("disposition") == "unmapped" for r in items),
    }


def table_projection(
    values: Any, headings: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Build flat Bases/Dataview fields from extracted table dictionaries."""
    tables = values if isinstance(values, list) else [values]
    records: List[Dict[str, Any]] = []
    for index, table in enumerate(tables):
        heading = headings[index] if headings and index < len(headings) else None
        if isinstance(table, list):
            nested = table_projection(table, [heading or ""] * len(table))
            records.extend(nested.pop("_records"))
            continue
        if not isinstance(table, dict):
            continue
        headers = [str(header) for header in table.get("headers") or []]
        rows = table.get("rows") or []
        malformed = (
            not headers
            or not rows
            or any(
                not isinstance(row, list) or len(row) != len(headers) for row in rows
            )
        )
        records.append(
            {
                **classify_table_record(heading, headers, malformed=malformed),
                "headers": headers,
                "rows": rows,
            }
        )
    metrics = table_record_metrics(records)
    return {
        "table_record_count": metrics["tables_total"],
        "table_kinds": list(dict.fromkeys(record["kind"] for record in records)),
        "table_classifications": list(
            dict.fromkeys(record["classification"] for record in records)
        ),
        "table_structured_count": metrics["tables_structured"],
        "table_opaque_count": metrics["tables_opaque"],
        "table_unmapped_count": metrics["tables_unmapped"],
        "_records": records,
    }


def table_rows(record: Dict[str, Any]) -> List[Dict[str, str]]:
    """Convert a valid record to row dictionaries without truncating cells."""
    headers = record.get("headers") or []
    rows = record.get("rows") or []
    if record.get("valid") is False:
        return []
    if any(len(row) != len(headers) for row in rows):
        return []
    return [
        dict(zip(headers, row))
        for row in rows
        if any(str(cell).strip() for cell in row)
    ]
