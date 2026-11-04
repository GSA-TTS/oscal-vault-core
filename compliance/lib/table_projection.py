"""Project simple DOCX source-table rows into queryable Obsidian notes."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

from compliance.lib.docx_ir import DocxTable, OPCPackageWalker

_ROW_GENERATOR = "compliance.lib.table_projection:rows"
_TABLE_GENERATOR = "compliance.lib.table_projection:tables"
_GENERATOR_VERSION = 1

_EXACT_TABLE_FAMILIES = {
    ("organization name", "general services administration"): "organization_fact_sheet",
    ("information system name", "fpdf databricks pipeline"): "system_fact_sheet",
    ("assurance levels", "implemented assurance level"): "assurance_levels",
    ("system operational status", "status description"): "operational_status",
    ("date", "comments", "version", "author"): "revision_history",
    (
        "information type",
        "confidentiality",
        "integrity",
        "availability",
    ): "information_types",
    ("security objective", "impact level"): "security_impact",
    ("asset type", "description of function or service provided"): "asset_inventory",
    (
        "system component",
        "application url",
        "internal external or both",
        "mfa authentication method",
    ): "system_components",
    (
        "component name",
        "os make",
        "operational environment",
        "location physical or virtual",
    ): "component_inventory",
    ("service element", "response"): "external_service",
    (
        "software component name",
        "function",
        "version",
        "patch level",
        "virtual yes no",
    ): "software_inventory",
    (
        "direction inbound outbound or bi directional",
        "boundary crossings y n",
        "source",
        "destination",
        "ports t or u",
        "protocols",
        "services",
        "purpose",
        "encrypted y n",
        "data sensitivity",
    ): "protocol",
    (
        "aws service name",
        "approval status fedramp and or ociso approved",
        "brief description of use s",
    ): "aws_services",
    (
        "system name",
        "organization",
        "point of contact and phone number",
        "connection security ipsec vpn ssl certificates secure file transfer etc",
        "data direction incoming outgoing or both",
        "information being transmitted",
        "ports or circuit",
    ): "interconnections",
    ("", "showstopper description", "control reference"): "showstoppers",
    ("acronym", "full name"): "acronyms",
    ("subsystem name", "subsystem purpose description", "system owner"): "subsystems",
}


def _source_sha256(docx_path: Path) -> str:
    return hashlib.sha256(docx_path.read_bytes()).hexdigest()


def _provenance(generator: str, source_sha256: str) -> Dict[str, Any]:
    return {
        "ownership": "generated",
        "generator": generator,
        "generator_version": _GENERATOR_VERSION,
        "source_sha256": source_sha256,
    }


def _cleanup_stale(directory: Path, expected: set[Path], generator: str) -> List[Path]:
    removed: List[Path] = []
    if not directory.is_dir():
        return removed
    for path in directory.glob("*.md"):
        if path in expected:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            frontmatter = yaml.safe_load(text.split("---", 2)[1])
        except (OSError, IndexError, yaml.YAMLError):
            continue
        provenance = (
            frontmatter.get("provenance") if isinstance(frontmatter, dict) else None
        )
        if (
            isinstance(provenance, dict)
            and provenance.get("ownership") == "generated"
            and provenance.get("generator") == generator
        ):
            path.unlink()
            removed.append(path)
    return removed


def _field_name(header: str, index: int) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")
    return value or f"column_{index + 1}"


def _header_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.strip().lower()).strip()


def classify_source_table(table: DocxTable) -> str | None:
    """Classify only exact, reviewable source-table header signatures."""
    if not table.cells or not table.cells[0]:
        return None
    headers = tuple(_header_key(cell.text) for cell in table.cells[0])
    if (
        len(headers) >= 2
        and re.fullmatch(r"[a-z]{2} \d{2}(?: \d{2,3})?", headers[0])
        and headers[1]
        in {
            "control summary information",
            "control enhancement summary information",
        }
    ):
        return "control_summary"
    if len(headers) == 4 and headers[0].startswith("providing system name"):
        return "inherited_control_provider"
    if len(headers) == 4 and headers[0].startswith("receiving system name"):
        return "inherited_control_receiver"
    if headers == (
        "role",
        "internal or external",
        "privileged p non privileged np or no logical access nla",
        "sensitivity level",
        "authorized privileges",
        "functions performed",
        "mfa authentication method",
    ):
        return "user_roles"
    if len(headers) == 8 and headers[:2] == ("system name", "organization"):
        return "system_inventory"
    if (
        len(headers) == 2
        and headers[0] == "control id"
        and re.fullmatch(r"[a-z]{2} \d{1,2}(?: \d{1,3})?", headers[1])
    ):
        return "control_reference"
    if (
        len(headers) == 2
        and headers[0] == "name"
        and headers[1] != "pending authoritative source"
    ):
        return "personnel_contact"
    return _EXACT_TABLE_FAMILIES.get(headers)


def projectable_rows(table: DocxTable) -> List[Dict[str, Any]]:
    """Return row projections only for reviewable rectangular source tables.

    ``user_roles`` tables in the GSA SSP starter use horizontal spans for the
    sensitivity/privilege layout but retain one physical cell per semantic
    column and no vertical carry-forward merges. Those spans are safe to
    preserve as the cell's value; all other merged shapes remain preserved-only.
    """
    if len(table.cells) < 2 or not table.cells[0]:
        return []
    cells = [cell for row in table.cells for cell in row]
    if any(cell.v_merge is not None for cell in cells):
        return []
    if (
        any(cell.grid_span != 1 for cell in cells)
        and classify_source_table(table) != "user_roles"
    ):
        return []
    width = len(table.cells[0])
    if width == 0 or any(len(row) != width for row in table.cells):
        return []
    headers = [
        _field_name(cell.text, index) for index, cell in enumerate(table.cells[0])
    ]
    if len(set(headers)) != len(headers):
        return []
    rows: List[Dict[str, Any]] = []
    for row_index, row in enumerate(table.cells[1:], start=1):
        if not any(cell.text.strip() for cell in row):
            continue
        row_id = hashlib.sha256(
            f"{table.node_id}\0{row_index}".encode("utf-8")
        ).hexdigest()[:20]
        rows.append(
            {
                "table_row_id": f"row-{row_id}",
                "source_table_id": table.node_id,
                "source_row_index": row_index,
                "source_cell_ids": [cell.cell_locator for cell in row],
                "fields": {header: cell.text for header, cell in zip(headers, row)},
            }
        )
    return rows


def write_table_row_notes(docx_path: Path, vault_dir: Path) -> List[Path]:
    """Write deterministic ``okf.table_row`` notes for projectable source rows."""
    document = OPCPackageWalker(docx_path).parse()
    source_sha256 = _source_sha256(docx_path)
    written: List[Path] = []
    for table_index, table in enumerate(document.tables):
        for row in projectable_rows(table):
            path = (
                vault_dir
                / "_source"
                / "table-rows"
                / f"table-{table_index + 1:04d}-row-{row['source_row_index']:04d}.md"
            )
            frontmatter = {
                "type": "okf.table_row",
                "title": f"Source table {table_index + 1} row {row['source_row_index']}",
                "status": "active",
                "owner": None,
                "provenance": _provenance(_ROW_GENERATOR, source_sha256),
                **row,
            }
            body = [f"# {frontmatter['title']}", ""]
            for key, value in row["fields"].items():
                inline_value = str(value).replace("\n", " ").strip()
                body.append(f"{key}:: {inline_value}")
            body.extend(
                [
                    "",
                    f"source_table_id:: {row['source_table_id']}",
                    f"source_row_index:: {row['source_row_index']}",
                    "",
                ]
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "---\n"
                + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
                + "---\n\n"
                + "\n".join(body),
                encoding="utf-8",
            )
            written.append(path)
    _cleanup_stale(vault_dir / "_source" / "table-rows", set(written), _ROW_GENERATOR)
    return written


def write_source_table_notes(docx_path: Path, vault_dir: Path) -> List[Path]:
    """Write one review note for every source table, including complex tables."""
    document = OPCPackageWalker(docx_path).parse()
    source_sha256 = _source_sha256(docx_path)
    written: List[Path] = []
    for table_index, table in enumerate(document.tables, start=1):
        merged_cells = sum(
            cell.grid_span > 1 or cell.v_merge is not None
            for row in table.cells
            for cell in row
        )
        paragraph_count = sum(
            len(cell.paragraphs) for row in table.cells for cell in row
        )
        projected_rows = projectable_rows(table)
        projection_status = "fully_projected" if projected_rows else "preserved_only"
        semantic_family = classify_source_table(table)
        frontmatter = {
            "type": "okf.source_table",
            "title": f"Source table {table_index}",
            "status": "active",
            "owner": None,
            "provenance": _provenance(_TABLE_GENERATOR, source_sha256),
            "source_table_id": table.node_id,
            "source_table_index": table_index,
            "row_count": table.num_rows,
            "column_count": table.num_cols,
            "cell_count": sum(len(row) for row in table.cells),
            "merged_cell_count": merged_cells,
            "paragraph_count": paragraph_count,
            "projected_row_count": len(projected_rows),
            "projection_status": projection_status,
            "classification_status": "classified"
            if semantic_family
            else "unclassified",
            "semantic_family": semantic_family,
            "preservation_manifest": "_preservation/docx-source-manifest.json",
        }
        body = (
            f"# Source table {table_index}\n\n"
            f"source_table_id:: {table.node_id}\n"
            f"projection_status:: {projection_status}\n"
            f"classification_status:: {'classified' if semantic_family else 'unclassified'}\n"
            f"semantic_family:: {semantic_family or ''}\n"
            f"row_count:: {table.num_rows}\n"
            f"column_count:: {table.num_cols}\n"
            f"merged_cell_count:: {merged_cells}\n"
            f"projected_row_count:: {len(projected_rows)}\n\n"
            "Full table structure is retained in "
            "`_preservation/docx-source-manifest.json`.\n"
        )
        path = vault_dir / "_source" / "tables" / f"table-{table_index:04d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
            + "---\n\n"
            + body,
            encoding="utf-8",
        )
        written.append(path)
    _cleanup_stale(vault_dir / "_source" / "tables", set(written), _TABLE_GENERATOR)
    return written


def write_source_table_base(vault_dir: Path) -> Path:
    """Write a Base dashboard over source table review notes."""
    path = vault_dir / "_meta" / "views" / "source-tables.base"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "filters": {"and": ['file.inFolder("_source/tables")']},
        "properties": {
            "file.name": {"displayName": "Table"},
            "projection_status": {"displayName": "Projection"},
            "row_count": {"displayName": "Rows"},
            "column_count": {"displayName": "Columns"},
            "merged_cell_count": {"displayName": "Merged cells"},
            "projected_row_count": {"displayName": "Projected rows"},
            "classification_status": {"displayName": "Classification"},
            "semantic_family": {"displayName": "Family"},
        },
        "views": [
            {
                "type": "table",
                "name": "All source tables",
                "order": [
                    "file.name",
                    "projection_status",
                    "row_count",
                    "column_count",
                    "merged_cell_count",
                    "projected_row_count",
                    "classification_status",
                    "semantic_family",
                ],
            },
            {
                "type": "table",
                "name": "Fully projected",
                "filters": {"and": ['projection_status == "fully_projected"']},
                "order": ["file.name", "row_count", "projected_row_count"],
            },
            {
                "type": "table",
                "name": "Complex preserved-only",
                "filters": {"and": ['projection_status == "preserved_only"']},
                "order": [
                    "file.name",
                    "row_count",
                    "column_count",
                    "merged_cell_count",
                ],
            },
            {
                "type": "table",
                "name": "Unclassified",
                "filters": {"and": ['classification_status == "unclassified"']},
                "order": [
                    "file.name",
                    "row_count",
                    "column_count",
                    "merged_cell_count",
                ],
            },
        ],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path
