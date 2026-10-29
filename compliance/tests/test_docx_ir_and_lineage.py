"""Tests for Issue #52 (Package Inventory), Issue #48 (Typed DOCX IR), and Issue #45 (Field Lineage)."""

import tempfile
from pathlib import Path

import pytest

from compliance.lib.docx_ir import (
    DISPOSITION_OPAQUE_PRESERVED,
    DISPOSITION_STRUCTURED,
    DISPOSITION_UNMAPPED_ERROR,
    LineageTracker,
    OPCPackageWalker,
)
from compliance.lib.synthetic_golden_docx import create_synthetic_golden_docx


@pytest.fixture(scope="module")
def golden_docx_path():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "golden.docx"
        create_synthetic_golden_docx(p)
        yield p


def test_opc_package_inventory(golden_docx_path):
    """OPC package inventory discovers all parts, relationships, and content nodes."""
    walker = OPCPackageWalker(golden_docx_path)
    doc = walker.parse()
    inv = doc.inventory
    summary = inv.summary()

    assert summary["total_parts"] >= 15
    assert summary["total_relationships"] >= 10
    assert summary["total_nodes"] >= 15

    # Check presence of specific parts
    part_names = {p.uri for p in inv.parts}
    assert "word/document.xml" in part_names
    assert "word/footnotes.xml" in part_names
    assert "word/endnotes.xml" in part_names
    assert "word/comments.xml" in part_names
    assert any(p.startswith("word/media/") for p in part_names)


def test_typed_docx_ir_nodes(golden_docx_path):
    """Typed IR nodes preserve part URI, XML locator, kind, order, and payload."""
    walker = OPCPackageWalker(golden_docx_path)
    doc = walker.parse()

    kinds = {n.kind for n in doc.nodes}
    assert "heading" in kinds
    assert "paragraph" in kinds
    assert "table" in kinds
    assert "header" in kinds
    assert "footer" in kinds
    assert "footnote" in kinds
    assert "endnote" in kinds
    assert "comment" in kinds
    assert "revision_ins" in kinds

    # Verify every node has a stable locator
    for n in doc.nodes:
        assert n.locator.part_uri
        assert n.locator.xml_locator
        assert n.locator.order > 0
        assert n.locator.stable_id == f"{n.locator.part_uri}#{n.locator.xml_locator}"


def test_structured_table_model(golden_docx_path):
    """Tables in IR preserve grid spans, vertical merges, and cell-level locators."""
    walker = OPCPackageWalker(golden_docx_path)
    doc = walker.parse()

    assert len(doc.tables) == 3
    # Check the merged matrix table (3rd table)
    merged_tbl = doc.tables[2]
    assert merged_tbl.num_rows == 3
    assert merged_tbl.num_cols == 3

    # Cell 0,0 has grid_span == 2
    c00 = merged_tbl.cells[0][0]
    assert c00.grid_span == 2
    assert "Merged Header" in c00.text

    # Cell 1,0 has v_merge
    c10 = merged_tbl.cells[1][0]
    assert c10.v_merge is not None
    assert "Merged Vertical" in c10.text

    # Compatibility projection produces 2D string grid
    grid = merged_tbl.to_text_grid()
    assert len(grid) == 3
    assert len(grid[0]) == 2  # row 0 has 2 cells because of col merge


def test_all_content_nodes_have_explicit_disposition(golden_docx_path):
    """Strict mode verifies that every content-bearing node has an explicit disposition."""
    walker = OPCPackageWalker(golden_docx_path)
    doc = walker.parse()

    ok, errors = doc.verify_all_classified(strict=True)
    assert ok
    assert len(errors) == 0

    dispositions = {n.disposition for n in doc.nodes}
    assert DISPOSITION_UNMAPPED_ERROR not in dispositions
    assert DISPOSITION_STRUCTURED in dispositions
    assert DISPOSITION_OPAQUE_PRESERVED in dispositions


def test_unclassified_node_fails_strict_mode(golden_docx_path):
    """If a content-bearing node is unclassified, strict validation fails."""
    walker = OPCPackageWalker(golden_docx_path)
    doc = walker.parse()

    # Artificially unclassify a node
    doc.nodes[0].disposition = DISPOSITION_UNMAPPED_ERROR
    with pytest.raises(ValueError) as exc:
        doc.verify_all_classified(strict=True)
    assert "unclassified source node(s) found in strict mode" in str(exc.value)


def test_lineage_tracking_from_docx_to_oscal(golden_docx_path):
    """LineageTracker links source locators to canonical pointers and OSCAL pointers."""
    walker = OPCPackageWalker(golden_docx_path)
    doc = walker.parse()

    tracker = LineageTracker()

    # Track AC-01 from source table to canonical to OSCAL
    tbl_ac01 = doc.tables[0]
    rec1 = tracker.record(
        source_locator=tbl_ac01.locator.stable_id,
        value="AC-01 Access Control Policy",
        canonical_pointer="/controls/AC-01",
        oscal_pointer="/system-security-plan/control-implementation/implemented-requirements/0",
        disposition=DISPOSITION_STRUCTURED,
    )
    assert rec1.normalized_sha256

    # Track AC-02
    tbl_ac02 = doc.tables[1]
    tracker.record(
        source_locator=tbl_ac02.locator.stable_id,
        value="AC-02 Account Management",
        canonical_pointer="/controls/AC-02",
        oscal_pointer="/system-security-plan/control-implementation/implemented-requirements/1",
        disposition=DISPOSITION_STRUCTURED,
    )

    # Track footnote (opaque preserved)
    fn_node = next(n for n in doc.nodes if n.kind == "footnote")
    tracker.record(
        source_locator=fn_node.locator.stable_id,
        value=fn_node.text,
        disposition=DISPOSITION_OPAQUE_PRESERVED,
    )

    required = [
        tbl_ac01.locator.stable_id,
        tbl_ac02.locator.stable_id,
        fn_node.locator.stable_id,
    ]
    lossless, missing = tracker.verify_lossless(required)
    assert lossless
    assert len(missing) == 0


def test_lineage_tracker_negative_detects_loss():
    """LineageTracker detects when a required source locator is missing from export."""
    tracker = LineageTracker()
    tracker.record(
        source_locator="word/document.xml#body/tbl[0]",
        value="AC-01",
        canonical_pointer="/controls/AC-01",
        disposition=DISPOSITION_STRUCTURED,
    )

    required = [
        "word/document.xml#body/tbl[0]",
        "word/document.xml#body/tbl[1]",  # not tracked -> loss!
    ]
    lossless, missing = tracker.verify_lossless(required)
    assert not lossless
    assert len(missing) == 1
    assert "word/document.xml#body/tbl[1]" in missing[0]


def test_lineage_tracker_verifies_generated_artifact_values():
    tracker = LineageTracker()
    tracker.record(
        source_locator="word/document.xml#body/p[0]",
        value="Account Management",
        canonical_pointer="/title",
        oscal_pointer="/system-security-plan/metadata/title",
    )
    canonical = {"title": "Account   Management"}
    oscal = {"system-security-plan": {"metadata": {"title": "Account Management"}}}

    assert tracker.verify_artifacts(canonical, oscal) == (True, [])


def test_lineage_tracker_reports_pointer_and_value_loss():
    tracker = LineageTracker()
    tracker.record(
        source_locator="word/document.xml#body/p[0]",
        value="Expected",
        canonical_pointer="/title",
        oscal_pointer="/system-security-plan/metadata/title",
    )

    valid, findings = tracker.verify_artifacts(
        {"title": "Changed"}, {"system-security-plan": {"metadata": {}}}
    )
    assert not valid
    assert any("canonical value hash differs" in finding for finding in findings)
    assert any("OSCAL pointer missing" in finding for finding in findings)
