"""Fill ordinary SSP tables by unique normalized header signature."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table, _Cell


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _headers(table: Table) -> tuple[str, ...]:
    return tuple(_normalize(cell.text) for cell in table.rows[0].cells)


def _table_anchor(table: Table) -> str | None:
    caption = table._tbl.tblPr.find(qn("w:tblCaption"))
    return caption.get(qn("w:val")) if caption is not None else None


@dataclass(frozen=True)
class NamedTableSpec:
    name: str
    headers: tuple[str, ...]
    columns: tuple[str, ...]
    template_row: int = 1
    data_start_row: int = 1


@dataclass
class NamedTableFillResult:
    output_path: Path
    table_name: str
    table_index: int
    input_row_count: int
    rows_written: int
    rows_removed: int
    readback_rows: list[dict[str, str]] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)


def _find_table(doc: DocxDocument, spec: NamedTableSpec) -> tuple[int, Table]:
    anchored = [
        (index, table)
        for index, table in enumerate(doc.tables)
        if _table_anchor(table) == f"ttse:{spec.name}"
    ]
    if len(anchored) == 1:
        return anchored[0]
    if len(anchored) > 1:
        raise ValueError(f"{spec.name}: duplicate semantic table anchors")
    expected = tuple(_normalize(value) for value in spec.headers)
    matches = [
        (index, table)
        for index, table in enumerate(doc.tables)
        if table.rows and _headers(table) == expected
    ]
    if len(matches) != 1:
        raise ValueError(
            f"{spec.name}: expected one table with header signature, found {len(matches)}"
        )
    return matches[0]


def _has_vertical_merge(row) -> bool:
    return any(cell._tc.find(f".//{qn('w:vMerge')}") is not None for cell in row.cells)


def _clear_cell(cell: _Cell) -> None:
    for paragraph in cell.paragraphs:
        for run in paragraph.runs:
            run.text = ""
        paragraph.text = ""
    if not cell.paragraphs:
        cell.add_paragraph()


def _set_cell_text(cell: _Cell, value: Any) -> None:
    text = "" if value is None else str(value)
    _clear_cell(cell)
    paragraph = cell.paragraphs[0]
    if paragraph.runs:
        paragraph.runs[0].text = text
    else:
        paragraph.add_run(text)


def _replace_rows(
    table: Table, spec: NamedTableSpec, rows: Sequence[Mapping[str, Any]]
) -> int:
    if spec.template_row >= len(table.rows):
        raise ValueError(
            f"{spec.name}: template row {spec.template_row} does not exist"
        )
    prototype = table.rows[spec.template_row]
    if _has_vertical_merge(prototype):
        raise ValueError(f"{spec.name}: vertical-merge prototype rows are unsupported")
    if len(prototype.cells) != len(spec.columns):
        raise ValueError(
            f"{spec.name}: prototype has {len(prototype.cells)} cells, but {len(spec.columns)} columns were declared"
        )

    table_element = table._tbl
    removable = list(table.rows[spec.data_start_row :])
    for row in removable:
        table_element.remove(row._tr)

    for record in rows:
        row_element = copy.deepcopy(prototype._tr)
        table_element.append(row_element)
        row = table.rows[-1]
        for index, key in enumerate(spec.columns):
            _set_cell_text(row.cells[index], record.get(key, ""))
    return len(removable)


def _set_table_anchor(table: Table, name: str) -> None:
    properties = table._tbl.tblPr
    caption = properties.find(qn("w:tblCaption"))
    if caption is None:
        caption = properties.makeelement(qn("w:tblCaption"))
        properties.append(caption)
    caption.set(qn("w:val"), f"ttse:{name}")


def read_named_table(
    source: str | Path | DocxDocument, spec: NamedTableSpec
) -> list[dict[str, str]]:
    doc = source if isinstance(source, DocxDocument) else Document(str(source))
    _, table = _find_table(doc, spec)
    return [
        {key: row.cells[index].text.strip() for index, key in enumerate(spec.columns)}
        for row in table.rows[spec.data_start_row :]
    ]


def fill_named_table(
    source: str | Path | DocxDocument,
    spec: NamedTableSpec,
    rows: Sequence[Mapping[str, Any]],
    output_path: str | Path,
) -> NamedTableFillResult:
    doc = source if isinstance(source, DocxDocument) else Document(str(source))
    table_index, table = _find_table(doc, spec)
    _set_table_anchor(table, spec.name)
    removed = _replace_rows(table, spec, rows)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)

    expected = [
        {
            key: "" if row.get(key) is None else str(row.get(key, ""))
            for key in spec.columns
        }
        for row in rows
    ]
    observed = read_named_table(output, spec)
    mismatches = [
        {"row": index, "expected": expected_row, "observed": observed_row}
        for index, (expected_row, observed_row) in enumerate(
            zip(expected, observed, strict=False)
        )
        if expected_row != observed_row
    ]
    if len(expected) != len(observed):
        mismatches.append(
            {"row_count": {"expected": len(expected), "observed": len(observed)}}
        )
    return NamedTableFillResult(
        output_path=output,
        table_name=spec.name,
        table_index=table_index,
        input_row_count=len(rows),
        rows_written=len(rows),
        rows_removed=removed,
        readback_rows=observed,
        mismatches=mismatches,
    )
