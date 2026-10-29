"""Tests for the secondary CRM `.ods` extractor (compliance/lib/crm_ods.py)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from compliance.lib.crm_ods import (
    CRMEntry,
    crm_to_props,
    extract_crm,
    summarize_inheritance,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_ODS = _REPO_ROOT / "fismatic" / "Databricks_Tenant_CRM.ods"

_CONTENT_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
 <office:body><office:spreadsheet>
  <table:table table:name="Sheet1">
   {rows}
  </table:table>
 </office:spreadsheet></office:body>
</office:document-content>"""


def _row(cells):
    tc = "".join(
        f"<table:table-cell><text:p>{c}</text:p></table:table-cell>" for c in cells
    )
    return f"<table:table-row>{tc}</table:table-row>"


def _make_ods(tmp_path: Path, rows) -> Path:
    content = _CONTENT_TMPL.format(rows="".join(_row(r) for r in rows))
    p = tmp_path / "crm.ods"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", content)
    return p


class TestSyntheticOds:
    def _rows(self):
        return [
            [
                "Control ID",
                "Control Name",
                "Control Implementation Status (CCG/CRM)",
                "New Service Area Inheritance",
                "Impacted/Not Impacted",
                "Assessment Objective",
            ],
            [
                "AC-2",
                "Account Management",
                "Partially Implemented",
                "Hybrid (Tenant)",
                "Impacted",
                "obj-1",
            ],
            [
                "AC-2 (1)",
                "Automated",
                "Fully Implemented",
                "System Specific",
                "Impacted",
                "obj-2",
            ],
            ["", "", "", "", "", ""],  # blank row skipped
            ["Not a control", "x", "y", "z", "", ""],  # non-control row skipped
        ]

    def test_extracts_and_canonicalizes(self, tmp_path):
        ods = _make_ods(tmp_path, self._rows())
        result = extract_crm(ods)
        assert result.total() == 2
        assert set(result.by_control) == {"AC-02", "AC-02(01)"}

    def test_fields_mapped(self, tmp_path):
        ods = _make_ods(tmp_path, self._rows())
        e = extract_crm(ods).by_control["AC-02"]
        assert e.control_name == "Account Management"
        assert e.implementation_status == "Partially Implemented"
        assert e.inheritance == "Hybrid (Tenant)"
        assert e.impacted == "Impacted"
        assert e.assessment_objective == "obj-1"

    def test_non_control_rows_skipped(self, tmp_path):
        ods = _make_ods(tmp_path, self._rows())
        result = extract_crm(ods)
        assert "Not a control" not in str(result.by_control)

    def test_unrecognized_columns_preserved_in_extra(self, tmp_path):
        """#43: a column with no mapped concept lands in extra, not dropped."""
        rows = [
            ["Control ID", "Tenant Owner", "Shared Responsibility Notes"],
            ["AC-2", "tenant owns IdP", "see runbook 4"],
        ]
        ods = _make_ods(tmp_path, rows)
        entry = extract_crm(ods).by_control["AC-02"]
        assert entry.extra.get("Tenant Owner") == "tenant owns IdP"
        assert entry.extra.get("Shared Responsibility Notes") == "see runbook 4"


class TestHelpers:
    def test_crm_to_props(self):
        e = CRMEntry(
            control_id="AC-02",
            inheritance="Hybrid",
            implementation_status="Partial",
            impacted="Impacted",
        )
        props = crm_to_props(e)
        names = {p["name"]: p["value"] for p in props}
        assert names["crm-inheritance"] == "Hybrid"
        assert names["crm-implementation-status"] == "Partial"

    def test_summarize_inheritance(self):
        e = CRMEntry(
            control_id="AC-02", inheritance="Hybrid", implementation_status="Partial"
        )
        assert summarize_inheritance(e) == "Hybrid (Partial)"

    def test_crm_to_props_carries_unrecognized_columns(self):
        """#43: unrecognized CRM columns are carried as crm-x-* props, not dropped."""
        e = CRMEntry(
            control_id="AC-02",
            inheritance="Hybrid",
            extra={"why": "tenant owns IdP", "Customer Impact": "high"},
        )
        names = {p["name"]: p["value"] for p in crm_to_props(e)}
        assert names["crm-inheritance"] == "Hybrid"
        assert names["crm-x-why"] == "tenant owns IdP"
        assert names["crm-x-customer-impact"] == "high"

    def test_crm_to_props_skips_blank_extra_columns(self):
        e = CRMEntry(control_id="AC-02", extra={"why": "", "note": "  "})
        assert all(not p["name"].startswith("crm-x-") for p in crm_to_props(e))

    def test_summarize_none_when_no_inheritance(self):
        assert summarize_inheritance(CRMEntry(control_id="AC-02")) is None


@pytest.mark.skipif(not _SAMPLE_ODS.exists(), reason="sample CRM .ods not present")
class TestRealOds:
    def test_databricks_crm(self):
        result = extract_crm(_SAMPLE_ODS)
        assert result.total() >= 15
        ac2 = result.by_control.get("AC-02")
        assert ac2 is not None
        assert "Hybrid" in ac2.inheritance
        # enhancement rows are canonicalized
        assert "AC-02(01)" in result.by_control
        assert "AC-04(21)" in result.by_control


class TestVaultAugmentation:
    def test_crm_entry_flows_into_control_note(self):
        import json

        from compliance.lib.crm_ods import CRMEntry
        from compliance.lib.vault import build_vault

        ssp_path = (
            _REPO_ROOT
            / "compliance"
            / "tests"
            / "fixtures"
            / "oscal"
            / "ssp-source.example.json"
        )
        if not ssp_path.exists():
            pytest.skip("example SSP source missing")
        ssp = json.loads(ssp_path.read_text())
        crm = {
            "AC-02": CRMEntry(
                control_id="AC-02",
                inheritance="Hybrid (Tenant)",
                implementation_status="Partially Implemented",
                impacted="Impacted",
                assessment_objective="verify provisioning",
            )
        }
        notes = build_vault(ssp, plan_id="s", parser_name="fismatic", crm_entries=crm)
        ac2 = next(n for n in notes if n.relpath == "Controls/AC-02.md")
        # frontmatter carries the crm block
        assert ac2.frontmatter.get("crm", {}).get("inheritance") == "Hybrid (Tenant)"
        # body renders the inheritance callout
        assert "## Inheritance (secondary CRM)" in ac2.body
        assert "crm_inheritance:: Hybrid (Tenant)" in ac2.body

    def test_no_crm_leaves_note_unaugmented(self):
        import json

        from compliance.lib.vault import build_vault

        ssp_path = (
            _REPO_ROOT
            / "compliance"
            / "tests"
            / "fixtures"
            / "oscal"
            / "ssp-source.example.json"
        )
        if not ssp_path.exists():
            pytest.skip("example SSP source missing")
        ssp = json.loads(ssp_path.read_text())
        notes = build_vault(ssp, plan_id="s", parser_name="fismatic")
        ac2 = next(n for n in notes if n.relpath == "Controls/AC-02.md")
        assert "crm" not in ac2.frontmatter
        assert "Inheritance (secondary CRM)" not in ac2.body
