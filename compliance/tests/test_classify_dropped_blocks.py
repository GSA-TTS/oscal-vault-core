"""Disposition classification of unrouted SSP blocks.

The coverage metric counts *how much* is unrouted. These cover the classifier
that decides *what* each unrouted block is, so a reviewer can adopt or decline
each class rather than treating the loss budget as undifferentiated.

Fixtures are synthetic: no FedRAMP SSP source document is required.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "compliance" / "scripts" / "classify_dropped_blocks.py"


def _load():
    spec = importlib.util.spec_from_file_location("classify_dropped_blocks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["classify_dropped_blocks"] = module
    spec.loader.exec_module(module)
    return module


classifier = _load()


def _block(
    text: str, *, kind: str = "prose", heading: str = "AC-02: Account Management"
):
    return classifier.Block(kind, heading, text, len(text))


def _classify(text: str, **kwargs) -> str:
    exact, statements = classifier._catalog_statements()
    return classifier._classify_one(
        _block(text, **kwargs), exact, statements, fuzzy=False
    )


class TestClassification:
    def test_verbatim_catalog_statement_is_not_system_content(self):
        """Text the vendored NIST catalog already owns must not read as narrative."""
        assert _classify("Assign account managers;") == "catalog-statement"

    def test_control_title_styled_as_body_text_is_structure(self):
        """Low/High style control headings as plain paragraphs; they are not prose."""
        for title in (
            "AC-02(03): Account Management | Disable Accounts",
            "SI-04: System Monitoring",
            "PT-07(01): Specific Categories of Personally Identifiable Information",
        ):
            assert _classify(title) == "control-title", title

    def test_agency_narrative_survives_as_template_narrative(self):
        """The one class an adoption decision is actually about."""
        text = (
            "The components of the [ACRONYM] environment can be broken down into "
            "the following groups of assets, each described in the table below."
        )
        assert _classify(text) == "template-narrative"

    def test_glossary_definition_needs_the_dash_and_the_length(self):
        glossary = (
            "Audit Log - A chronological record of information system activities, "
            "including records of system accesses and operations performed."
        )
        assert _classify(glossary) == "glossary-definition"
        # The same shape, too short to be a definition, is not one.
        assert _classify("Audit Log - short") != "glossary-definition"

    @pytest.mark.parametrize(
        "citation",
        [
            '5 U.S.C. 552a, "Privacy Act of 1974"',
            'FIPS 140-2, "Security Requirements for Cryptographic Modules"',
            "CISA Cybersecurity Directives",
            'OMB Memo 21-07, "Completing the Transition to IPv6"',
        ],
    )
    def test_authority_citations_are_recognized(self, citation):
        assert _classify(citation) == "authority-citation"

    def test_bracketed_odp_fragment_is_not_narrative(self):
        assert _classify("[Annually];") == "odp-placeholder"
        assert _classify("[Security-relevant information].") == "odp-placeholder"

    def test_signature_rules_and_named_furniture(self):
        assert _classify("_" * 32) == "document-furniture"
        for line in ("Approvals", "Table of Contents", "Document Revision History"):
            assert _classify(line) == "document-furniture", line

    def test_gsa_note_is_kept_distinct_from_furniture(self):
        """Agency asides carry intent; they must not be lumped with scaffolding."""
        text = (
            "GSA Note: For databases, encryption of the whole database, table, "
            "column, or field levels is acceptable."
        )
        assert _classify(text) == "gsa-annotation"

    def test_tables_and_images_classify_by_kind_not_text(self):
        exact, statements = classifier._catalog_statements()
        table = classifier.Block("table", "Asset Inventory", "Asset Type | Desc", 0)
        image = classifier.Block("image", "Network Architecture", "", 0)
        assert (
            classifier._classify_one(table, exact, statements, fuzzy=False)
            == "structured-table"
        )
        assert (
            classifier._classify_one(image, exact, statements, fuzzy=False)
            == "diagram-image"
        )

    def test_catalog_match_wins_over_every_other_rule(self):
        """Precedence matters: a catalog sentence must never be adopted as prose."""
        assert _classify("Monitor the use of accounts;") == "catalog-statement"


class TestNormalization:
    def test_odp_inserts_and_brackets_are_removed_before_comparison(self):
        """A parameter-substituted statement is still the catalog's sentence."""
        catalog = "Require {{ insert: param, ac-02_odp.01 }} for group membership;"
        filled = "Require [annual review] for group membership;"
        assert classifier.normalize(catalog) == classifier.normalize(filled)

    def test_normalization_is_case_and_punctuation_insensitive(self):
        assert classifier.normalize("Assign Account Managers;") == classifier.normalize(
            "assign  account   managers"
        )


class TestDisposition:
    def test_every_class_has_exactly_one_disposition(self):
        """A class with no disposition would silently vanish from the summary."""
        assigned = [
            cls for members in classifier.DISPOSITION.values() for cls in members
        ]
        assert sorted(assigned) == sorted(classifier.CLASSES)
        assert len(assigned) == len(set(assigned)), "a class appears twice"

    def test_catalog_and_reference_material_is_never_adopted(self):
        """The core decision: do not re-store text that is authoritative elsewhere."""
        adopt = classifier.DISPOSITION["adopt"]
        for cls in ("catalog-statement", "glossary-definition", "authority-citation"):
            assert cls not in adopt

    def test_agency_narrative_and_tables_are_the_adopt_set(self):
        assert set(classifier.DISPOSITION["adopt"]) == {
            "structured-table",
            "template-narrative",
        }


class TestReportShape:
    def test_report_aggregates_blocks_and_chars_per_class(self):
        report = classifier.Report(docx="synthetic.docx")
        report.blocks = [
            classifier.Block("prose", "H", "x" * 10, 10, cls="template-narrative"),
            classifier.Block("prose", "H", "y" * 5, 5, cls="template-narrative"),
            classifier.Block(
                "table", "H", "hdr", 0, cells=6, cell_chars=24, cls="structured-table"
            ),
        ]
        agg = report.by_class()
        assert agg["template-narrative"] == {"blocks": 2, "chars": 15, "cells": 0}
        # A table's content is in its cells, so cell text is its character count.
        assert agg["structured-table"] == {"blocks": 1, "chars": 24, "cells": 6}

    def test_by_heading_orders_by_characters_descending(self):
        report = classifier.Report(docx="synthetic.docx")
        report.blocks = [
            classifier.Block("prose", "Small", "x" * 10, 10, cls="template-narrative"),
            classifier.Block("prose", "Large", "y" * 90, 90, cls="template-narrative"),
        ]
        assert [row[0] for row in report.by_heading("template-narrative")] == [
            "Large",
            "Small",
        ]

    def test_render_reports_every_disposition_group(self):
        report = classifier.Report(docx="synthetic.docx")
        report.blocks = [
            classifier.Block("prose", "H", "x" * 10, 10, cls="template-narrative"),
            classifier.Block("prose", "H", "y" * 10, 10, cls="catalog-statement"),
            classifier.Block("prose", "H", "_" * 10, 10, cls="document-furniture"),
        ]
        out = classifier.render(report, detail=0)
        for group in ("adopt", "reference", "decline"):
            assert f"**{group}**" in out
        assert "synthetic.docx" in out
