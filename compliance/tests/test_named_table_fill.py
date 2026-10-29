"""Tests for high-fidelity named table row filling."""

from __future__ import annotations

from pathlib import Path

import docx
import pytest
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from compliance.lib.named_table_fill import (
    NamedTableSpec,
    fill_named_table,
    read_named_table,
)


def _document(path: Path, *, duplicate: bool = False) -> None:
    document = docx.Document()
    for _ in range(2 if duplicate else 1):
        table = document.add_table(rows=2, cols=2)
        table.rows[0].cells[0].text = "Name"
        table.rows[0].cells[1].text = "Description"
        table.rows[1].cells[0].text = "[Example]"
        table.rows[1].cells[1].text = "Placeholder"
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), "D9EAF7")
        table.rows[1].cells[0]._tc.get_or_add_tcPr().append(shading)
    document.save(path)


SPEC = NamedTableSpec(
    name="assets",
    headers=("Name", "Description"),
    columns=("name", "description"),
)


def test_fill_named_table_clones_style_and_roundtrips_rows(tmp_path):
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _document(source)

    result = fill_named_table(
        source,
        SPEC,
        [
            {"name": "One", "description": "First"},
            {"name": "Two", "description": "Second"},
        ],
        output,
    )

    assert result.table_index == 0
    assert result.rows_removed == 1
    assert result.rows_written == 2
    assert result.mismatches == []
    assert read_named_table(output, SPEC) == [
        {"name": "One", "description": "First"},
        {"name": "Two", "description": "Second"},
    ]
    document = docx.Document(output)
    assert (
        document.tables[0].rows[1].cells[0]._tc.tcPr.find(qn("w:shd")).get(qn("w:fill"))
        == "D9EAF7"
    )
    assert (
        document.tables[0]._tbl.tblPr.find(qn("w:tblCaption")).get(qn("w:val"))
        == "ttse:assets"
    )


def test_semantic_anchor_survives_header_changes(tmp_path):
    source = tmp_path / "source.docx"
    first = tmp_path / "first.docx"
    second = tmp_path / "second.docx"
    _document(source)
    fill_named_table(source, SPEC, [{"name": "One", "description": "First"}], first)
    document = docx.Document(first)
    document.tables[0].rows[0].cells[0].text = "Renamed Display Header"
    document.save(first)

    result = fill_named_table(
        first, SPEC, [{"name": "Two", "description": "Second"}], second
    )

    assert result.mismatches == []


def test_fill_named_table_rejects_ambiguous_signature(tmp_path):
    source = tmp_path / "source.docx"
    _document(source, duplicate=True)

    with pytest.raises(ValueError, match="expected one table"):
        fill_named_table(source, SPEC, [], tmp_path / "output.docx")


def test_fill_named_table_rejects_vertical_merge_prototype(tmp_path):
    source = tmp_path / "source.docx"
    _document(source)
    document = docx.Document(source)
    merge = OxmlElement("w:vMerge")
    merge.set(qn("w:val"), "restart")
    document.tables[0].rows[1].cells[0]._tc.get_or_add_tcPr().append(merge)
    document.save(source)

    with pytest.raises(ValueError, match="vertical-merge"):
        fill_named_table(source, SPEC, [], tmp_path / "output.docx")
