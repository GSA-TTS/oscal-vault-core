"""Extract a Customer Responsibility / Control Implementation Matrix from a
`.ods` (OpenDocument Spreadsheet) file to augment an SSP-derived vault.

A "secondary CRM" spreadsheet (e.g. the Databricks Tenant CRM for the FPDS-UI /
FCS Data Databricks tenant) describes, per control, the **inheritance** model
and the tenant's **implementation / assessment** responsibilities that layer on
top of what the primary SSP `.docx` already states. This module reads that
spreadsheet with no third-party dependency (pure stdlib zip + ElementTree — an
`.ods` is a zipped OpenDocument package) and returns a normalized per-control
record keyed by canonical control id, ready to merge into the vault.

The column layout is discovered from the header row rather than hard-coded, so
minor column reordering does not break extraction. Recognized header concepts
(matched case-insensitively, substring):

    control id            -> control_id (canonical)
    control name          -> control_name
    ...status (CCG/CRM)   -> implementation_status
    ...inheritance        -> inheritance
    impacted              -> impacted
    assessment objective  -> assessment_objective
    assessment method     -> assessment_method
    why / impact / who / what / how / when / where -> assessment_* fields
    evidence for...       -> evidence
    evidence/artifact link-> evidence_link
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union
from xml.etree import ElementTree as ET

_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


# Header concept -> field name. Order matters only for first-match wins.
_HEADER_MAP = [
    ("control id", "control_id"),
    ("control name", "control_name"),
    ("implementation status", "implementation_status"),
    ("status (ccg", "implementation_status"),
    ("inheritance", "inheritance"),
    ("impacted", "impacted"),
    ("assessment objective", "assessment_objective"),
    ("assessment method", "assessment_method"),
    ("evidence for", "evidence"),
    ("evidence/artifact", "evidence_link"),
    ("artifact link", "evidence_link"),
    ("why", "why"),
    ("impact", "impact"),
]

_CONTROL_ID_RE = re.compile(r"^[A-Za-z]{2}-\d{1,3}(?:\s*\(\d+\))?")


@dataclass
class CRMEntry:
    """One control's CRM row, normalized."""

    control_id: str  # canonical, e.g. "AC-02(01)"
    control_name: str = ""
    implementation_status: str = ""
    inheritance: str = ""
    impacted: str = ""
    assessment_objective: str = ""
    assessment_method: str = ""
    evidence: str = ""
    evidence_link: str = ""
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class CRMExtractionResult:
    source: str
    sheet: str
    by_control: Dict[str, CRMEntry] = field(default_factory=dict)

    def total(self) -> int:
        return len(self.by_control)


def _cell_text(cell: ET.Element) -> str:
    """Concatenated text of an ODS cell (joins multiple <text:p> paragraphs)."""
    parts = ["".join(p.itertext()) for p in cell.iter(_q(_TEXT_NS, "p"))]
    return " ".join(t for t in parts if t).strip()


def _row_cells(row: ET.Element) -> List[str]:
    """Expand a table-row into a flat list of cell texts, honoring
    ``number-columns-repeated`` and trimming trailing empties."""
    cells: List[str] = []
    for cell in row.findall(_q(_TABLE_NS, "table-cell")):
        rep = int(cell.get(_q(_TABLE_NS, "number-columns-repeated"), "1"))
        # Cap runaway repeats (ODS pads to 1024 cols); we only need the data span.
        rep = min(rep, 64)
        cells.extend([_cell_text(cell)] * rep)
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def _canonical(control_id: str) -> str:
    from compliance.lib.vault import canonical_control_id

    return canonical_control_id(control_id)


def _map_headers(header_row: List[str]) -> Dict[int, str]:
    """Map column index -> field name.

    Recognized header concepts map to their canonical ``CRMEntry`` field.
    Unrecognized but non-empty columns map to ``x:<header-label>`` so their
    values are carried into ``CRMEntry.extra`` rather than silently dropped
    (lossless pipeline, EPIC #43).
    """
    mapping: Dict[int, str] = {}
    for idx, raw in enumerate(header_row):
        label = raw.strip().lower()
        if not label:
            continue
        matched = False
        for needle, field_name in _HEADER_MAP:
            if needle in label:
                # Do not overwrite an earlier, more specific match for the field.
                if field_name not in mapping.values():
                    mapping[idx] = field_name
                matched = True
                break
        if not matched:
            mapping[idx] = f"x:{raw.strip()}"
    return mapping


def extract_crm(ods_path: Union[str, Path]) -> CRMExtractionResult:
    """Parse the first data sheet of a CRM `.ods` and return per-control entries.

    Rows whose first column is not a control id (blank rows, section headers)
    are skipped. The header row is the first row containing a "control id"-like
    cell.
    """
    path = Path(ods_path)
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))

    for table in root.iter(_q(_TABLE_NS, "table")):
        sheet = table.get(_q(_TABLE_NS, "name")) or ""
        rows = [_row_cells(r) for r in table.findall(_q(_TABLE_NS, "table-row"))]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue

        # Find the header row (first row with a "control id" cell).
        header_idx = None
        for i, r in enumerate(rows):
            if any("control id" in (c or "").strip().lower() for c in r):
                header_idx = i
                break
        if header_idx is None:
            continue

        col_map = _map_headers(rows[header_idx])
        if "control_id" not in col_map.values():
            continue

        result = CRMExtractionResult(source=str(path), sheet=sheet)
        cid_col = next(i for i, f in col_map.items() if f == "control_id")

        for r in rows[header_idx + 1 :]:
            if cid_col >= len(r):
                continue
            raw_id = (r[cid_col] or "").strip()
            if not _CONTROL_ID_RE.match(raw_id):
                continue
            cid = _canonical(raw_id)
            entry = CRMEntry(control_id=cid)
            for idx, field_name in col_map.items():
                if field_name == "control_id" or idx >= len(r):
                    continue
                value = (r[idx] or "").strip()
                if not value:
                    continue
                if field_name.startswith("x:"):
                    # Unrecognized column: preserve under its original header.
                    entry.extra[field_name[2:]] = value
                elif hasattr(entry, field_name):
                    setattr(entry, field_name, value)
                else:
                    entry.extra[field_name] = value
            result.by_control[cid] = entry
        return result

    return CRMExtractionResult(source=str(path), sheet="")


def crm_to_props(entry: CRMEntry) -> List[Dict[str, str]]:
    """Render a CRM entry as OSCAL-style prop dicts (name/value) for a control
    note's frontmatter / by-component augmentation.

    Unrecognized columns captured in ``entry.extra`` (e.g. ``why``, ``impact``,
    ``control_name``) are carried forward as ``crm-x-<column>`` props rather than
    silently dropped, so no CRM column is lost between the source matrix and the
    control note (lossless pipeline, EPIC #43).
    """
    fields = [
        ("crm-inheritance", entry.inheritance),
        ("crm-implementation-status", entry.implementation_status),
        ("crm-impacted", entry.impacted),
    ]
    props = [{"name": name, "value": value} for name, value in fields if value]
    for column, value in sorted(entry.extra.items()):
        if value and str(value).strip():
            slug = re.sub(r"[^a-z0-9]+", "-", str(column).strip().lower()).strip("-")
            if slug:
                props.append({"name": f"crm-x-{slug}", "value": str(value).strip()})
    return props


def summarize_inheritance(entry: CRMEntry) -> Optional[str]:
    """A one-line inheritance summary suitable for a note callout."""
    if not entry.inheritance:
        return None
    bits = [entry.inheritance]
    if entry.implementation_status:
        bits.append(f"({entry.implementation_status})")
    return " ".join(bits)
