from pathlib import Path

import yaml

from compliance.lib.canonical_json import generate_canonical_data
from compliance.lib.docx_ir import DocxLocator, DocxTable, DocxTableCell
from compliance.lib.synthetic_golden_docx import create_synthetic_golden_docx
from compliance.lib.table_projection import (
    classify_source_table,
    projectable_rows,
    write_source_table_base,
    write_source_table_notes,
    write_table_row_notes,
)


def _table(*, merged: bool = False) -> DocxTable:
    locator = DocxLocator("word/document.xml", "body/tbl[0]", 1, "table")
    return DocxTable(
        node_id=locator.stable_id,
        locator=locator,
        num_rows=2,
        num_cols=2,
        cells=[
            [
                DocxTableCell(0, 0, "System Name", grid_span=2 if merged else 1),
                DocxTableCell(0, 1, "Protocol"),
            ],
            [
                DocxTableCell(
                    1,
                    0,
                    "Example",
                    cell_locator="word/document.xml#body/tbl[0]/tr[1]/tc[0]",
                ),
                DocxTableCell(
                    1,
                    1,
                    "TLS 1.2+",
                    cell_locator="word/document.xml#body/tbl[0]/tr[1]/tc[1]",
                ),
            ],
        ],
    )


def test_simple_rows_have_stable_source_ids_and_normalized_fields():
    rows = projectable_rows(_table())

    assert len(rows) == 1
    assert rows[0]["table_row_id"].startswith("row-")
    assert rows[0]["source_table_id"] == "word/document.xml#body/tbl[0]"
    assert rows[0]["fields"] == {
        "system_name": "Example",
        "protocol": "TLS 1.2+",
    }
    assert len(rows[0]["source_cell_ids"]) == 2


def test_merged_table_is_not_flattened_into_inline_properties():
    assert projectable_rows(_table(merged=True)) == []


def test_exact_control_summary_family_is_classified_without_flattening():
    table = _table(merged=True)
    table.cells[0][0].text = "AC-02(01)"
    table.cells[0][1].text = "Control Enhancement Summary Information"

    assert classify_source_table(table) == "control_summary"
    assert projectable_rows(table) == []


def test_unknown_header_shape_remains_unclassified():
    table = _table()
    table.cells[0][0].text = "Similar but unknown"

    assert classify_source_table(table) is None


def test_placeholder_personnel_table_remains_unclassified():
    table = _table()
    table.cells[0][0].text = "Name"
    table.cells[0][1].text = "Pending authoritative source"

    assert classify_source_table(table) is None


def test_exact_operational_families_are_classified():
    table = _table()
    table.cells[0][0].text = "Acronym"
    table.cells[0][1].text = "Full Name"
    assert classify_source_table(table) == "acronyms"

    table.cells[0][0].text = "Control ID"
    table.cells[0][1].text = "AC-4(21)"
    assert classify_source_table(table) == "control_reference"


def test_golden_docx_writes_valid_frontmatter_and_inline_properties(tmp_path: Path):
    docx = tmp_path / "golden.docx"
    vault = tmp_path / "vault"
    create_synthetic_golden_docx(docx)

    paths = write_table_row_notes(docx, vault)

    assert paths
    text = paths[0].read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["type"] == "okf.table_row"
    assert frontmatter["source_table_id"].startswith("word/document.xml#")
    assert "source_table_id:: word/document.xml#" in text
    for key, value in frontmatter["fields"].items():
        assert f"{key}:: {value}" in text

    canonical = generate_canonical_data(paths[0], vault)
    assert canonical["source_lineage"][0] == {
        "source_id": frontmatter["source_table_id"],
        "canonical_pointer": "/source_table_id",
    }
    assert len(canonical["source_lineage"]) == 1 + len(frontmatter["source_cell_ids"])


def test_golden_docx_writes_review_note_for_every_table(tmp_path: Path):
    docx = tmp_path / "golden.docx"
    vault = tmp_path / "vault"
    create_synthetic_golden_docx(docx)

    paths = write_source_table_notes(docx, vault)
    base = write_source_table_base(vault)

    assert len(paths) == 3
    records = [yaml.safe_load(path.read_text().split("---", 2)[1]) for path in paths]
    assert {record["projection_status"] for record in records} == {
        "fully_projected",
        "preserved_only",
    }
    assert any(record["merged_cell_count"] > 0 for record in records)
    assert any(record["projected_row_count"] > 0 for record in records)
    payload = yaml.safe_load(base.read_text(encoding="utf-8"))
    assert [view["name"] for view in payload["views"]] == [
        "All source tables",
        "Fully projected",
        "Complex preserved-only",
        "Unclassified",
    ]
    assert payload["filters"] == {"and": ['file.inFolder("_source/tables")']}


def test_projection_cleanup_only_removes_owned_stale_files(tmp_path: Path):
    docx = tmp_path / "golden.docx"
    vault = tmp_path / "vault"
    create_synthetic_golden_docx(docx)
    paths = write_table_row_notes(docx, vault)
    assert paths
    directory = vault / "_source" / "table-rows"
    stale = directory / "table-9999-row-9999.md"
    stale.write_text(
        "---\ntype: okf.table_row\nprovenance:\n"
        "  ownership: generated\n"
        "  generator: compliance.lib.table_projection:rows\n"
        "  generator_version: 1\n"
        f"  source_sha256: {'0' * 64}\n---\n",
        encoding="utf-8",
    )
    authored = directory / "reviewer-notes.md"
    authored.write_text("# Keep this authored note\n", encoding="utf-8")
    other = directory / "other-generator.md"
    other.write_text(
        "---\nprovenance:\n  ownership: generated\n"
        "  generator: another.generator\n---\n",
        encoding="utf-8",
    )

    write_table_row_notes(docx, vault)

    assert not stale.exists()
    assert authored.exists()
    assert other.exists()
    frontmatter = yaml.safe_load(paths[0].read_text().split("---", 2)[1])
    assert frontmatter["provenance"]["ownership"] == "generated"
    assert frontmatter["provenance"]["source_sha256"]
