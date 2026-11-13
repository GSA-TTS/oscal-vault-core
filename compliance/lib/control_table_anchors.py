"""Structural detector for FedRAMP SSP control-summary (CIS) tables (Phase R17).

The official GSA rev5 blank templates (Low/Moderate/High/Leveraged-SaaS-CRM)
embed **one CIS table per control**, already fully styled, immediately after
each control's narrative/instruction paragraphs. Unlike the roundtrip demo's
instruction-anchored narrative slots (``instruction_anchors.py``), these
tables need no Jinja tag injection and no cloning: the write path
(``control_table_fill.py``) mutates each table's existing checkbox runs and
narrative cells **in place**, by table-shape signature, exactly mirroring
the row layout ``fismatic.parser.parse_control_table`` /
``extract_implementation_details`` already reads back (proven identical via
a live round-trip against the committed jinja templates -- see
``compliance/tests/test_control_table_fill.py``).

Row layout (verified structurally against all four committed templates):

    row 0: control_id | ... | "Control Summary Information"  (row0.cells[0])
    row 1: "Implementation Status:" + 5-6 checkbox lines       (merged cell)
    row 2: "Control Origination:" + 3-7 checkbox lines         (merged cell)
    row 3: "<CID> Describe how the control is implemented."   (merged cell)
    row 4..N-1: EITHER
        - "Part <key>" | <narrative text>   (one row per OSCAL statement), OR
        - a single blank fully-merged answer row (row 4 only, when the
          control has no Part breakdown -- row count is exactly 5)

Detection key: row 1's first cell starts with ``"Implementation Status:"``
(after ``.strip()``) -- this is the one substring stable across every
official template and column-count variant (2-col Low/Moderate/High CIS
tables, 3-col legacy-merged-heading variant, 2-col CRM Responsibility
tables). Column count (2 vs 3) and total row count (5-16) vary per control
and are handled generically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.table import Table, _Cell

_STATUS_ROW_MARKER = "Implementation Status:"
_STATUS_ROW_RE = re.compile(
    r"^Implementation\s+(?:Status|Type)(?:\s*\([^)]*\))?\s*:", re.IGNORECASE
)
_ORIGIN_ROW_MARKER = "Control Origination:"
_ORIGIN_ROW_RE = re.compile(
    r"^Control\s+(?:Origination|Applicability)(?:\s*\([^)]*\))?\s*:", re.IGNORECASE
)
_DESCRIBE_ROW_MARKER = "Describe how"
_CONTINUATION_HEADER_RE = re.compile(
    r"^(?:[a-z]{2}[\s-]*0*\d+(?:\s*\(0*(?:\d+|[a-z]+)\))?\s+)?(?:describe\s+how\b|what\s+is\s+the\s+solution\b)",
    re.IGNORECASE,
)
_PART_ROW_RE = re.compile(r"^(?:Part|Req\.?)\s+([a-z0-9]+)\s*$", re.IGNORECASE)


@dataclass
class ControlTableAnchor:
    """One detected CIS table + the cell handles needed to fill it.

    Attributes
    ----------
    control_id:
        Raw text of row 0's first cell (NOT yet normalized -- callers should
        run this through ``fismatic.parser.normalize_control_id`` before
        matching against a ``CanonicalControl.control_id``).
    table:
        The underlying ``python-docx`` ``Table``.
    status_cell:
        Row 1's first cell (the merged "Implementation Status:" block).
    origination_cell:
        Row 2's first cell (the merged "Control Origination:" block).
    part_rows:
        ``{part_key: value_cell}`` for each "Part <key>" row found (row 4+).
        Empty when the control has no Part breakdown.
    blank_answer_cell:
        The single fully-merged row-4 answer cell, present only when
        ``part_rows`` is empty (a control with no Part breakdown still has
        exactly one blank narrative slot to fill).
    continuation_table:
        Optional paired narrative table immediately following a split CIS
        summary table (e.g. Rev 4 / early Rev 5 templates where status and
        narrative live in adjacent tables).
    """

    control_id: str
    table_index: int
    table: Table
    status_cell: _Cell
    origination_cell: _Cell
    part_rows: dict[str, _Cell] = field(default_factory=dict)
    blank_answer_cell: _Cell | None = None
    continuation_table: Table | None = None


def _status_row_index(table: Table) -> int | None:
    if len(table.rows) < 3:
        return None
    for idx in range(1, min(len(table.rows), 10)):
        try:
            text = table.rows[idx].cells[0].text.strip()
            if text.startswith(_STATUS_ROW_MARKER) or _STATUS_ROW_RE.match(text):
                return idx
        except Exception:  # pragma: no cover - defensive (malformed table)
            continue
    return None


def _origination_row_index(table: Table, status_idx: int) -> int:
    for idx in range(status_idx + 1, min(len(table.rows), status_idx + 5)):
        try:
            text = table.rows[idx].cells[0].text.strip()
            if text.startswith(_ORIGIN_ROW_MARKER) or _ORIGIN_ROW_RE.match(text):
                return idx
        except Exception:  # pragma: no cover
            continue
    return min(status_idx + 1, len(table.rows) - 1)


def _is_continuation_table(table: Table) -> bool:
    if len(table.rows) < 2 or _status_row_index(table) is not None:
        return False
    try:
        header = " ".join(table.rows[0].cells[0].text.split())
        return bool(_CONTINUATION_HEADER_RE.match(header))
    except Exception:  # pragma: no cover
        return False


def _looks_like_status_row(table: Table) -> bool:
    return _status_row_index(table) is not None


def find_control_tables(
    source: str | Path | DocxDocument,
) -> list[ControlTableAnchor]:
    """Detect every CIS table in ``source``, in document order.

    Parameters
    ----------
    source:
        Path to a ``.docx``, or an already-open ``python-docx`` ``Document``.
    """
    doc = source if isinstance(source, DocxDocument) else Document(str(source))

    anchors: list[ControlTableAnchor] = []
    tables = list(doc.tables)
    for table_index, table in enumerate(tables):
        status_idx = _status_row_index(table)
        if status_idx is None:
            continue

        control_id = table.rows[0].cells[0].text.strip()
        status_cell = table.rows[status_idx].cells[0]
        origin_idx = _origination_row_index(table, status_idx)
        origination_cell = table.rows[origin_idx].cells[0]

        part_rows: dict[str, _Cell] = {}
        blank_answer_cell: _Cell | None = None
        continuation_table: Table | None = None
        for row_index in range(origin_idx + 1, len(table.rows)):
            row = table.rows[row_index]
            label = row.cells[0].text.strip()
            m = _PART_ROW_RE.match(label)
            if m:
                part_rows[m.group(1).lower()] = row.cells[-1]
            elif (
                blank_answer_cell is None
                and len(table.rows) == status_idx + 4
                and row_index == status_idx + 3
            ):
                # Only the single row after Describe-how in a no-Part CIS table is
                # the blank answer slot; guard against matching later padding.
                # It may be empty or carry a label, such as "Cloud.gov Customer
                # Responsibility:", that the narrative is written after.
                blank_answer_cell = row.cells[0]

        if (
            not part_rows
            and table_index + 1 < len(tables)
            and _is_continuation_table(tables[table_index + 1])
        ):
            continuation_table = tables[table_index + 1]
            for row in continuation_table.rows[1:]:
                label = row.cells[0].text.strip()
                m = _PART_ROW_RE.match(label)
                if m:
                    part_rows[m.group(1).lower()] = row.cells[-1]
            if (
                not part_rows
                and blank_answer_cell is None
                and len(continuation_table.rows) == 2
            ):
                blank_answer_cell = continuation_table.rows[1].cells[0]

        anchors.append(
            ControlTableAnchor(
                control_id=control_id,
                table_index=table_index,
                table=table,
                status_cell=status_cell,
                origination_cell=origination_cell,
                part_rows=part_rows,
                blank_answer_cell=blank_answer_cell,
                continuation_table=continuation_table,
            )
        )

    return anchors


def summarize_anchors(anchors: list[ControlTableAnchor]) -> dict:
    """Counts by row-shape, for CLI/test reporting."""
    return {
        "total": len(anchors),
        "with_part_rows": sum(1 for a in anchors if a.part_rows),
        "with_blank_answer_only": sum(
            1 for a in anchors if not a.part_rows and a.blank_answer_cell is not None
        ),
        "max_parts": max((len(a.part_rows) for a in anchors), default=0),
    }
