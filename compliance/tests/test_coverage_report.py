"""Unit tests for Feature R25: Coverage Reconciliation Report.

All tests are self-contained — no vault directory or .ods file needed.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.vault import build_coverage_note  # noqa: E402
from compliance.scripts.coverage_report import (  # noqa: E402
    compute_coverage,
    get_baseline,
    render_json,
    render_markdown,
)


# ---------------------------------------------------------------------------
# build_coverage_note
# ---------------------------------------------------------------------------


class TestBuildCoverageNote:
    def _minimal_controls(self) -> dict:
        return {
            "AC-01": {"implementation_status": "implemented"},
            "AC-02": {"implementation_status": "partially-implemented"},
            "AC-03": {"implementation_status": "not-implemented"},
        }

    def test_note_type_and_type_fields(self):
        note = build_coverage_note(self._minimal_controls())
        assert note.frontmatter["type"] == "okf.index"
        assert note.frontmatter["note_type"] == "coverage"

    def test_relpath_is_coverage_md(self):
        note = build_coverage_note(self._minimal_controls())
        assert note.relpath == "Coverage.md"

    def test_fips199_in_frontmatter(self):
        note = build_coverage_note(self._minimal_controls(), fips_199="high")
        assert note.frontmatter["fips_199"] == "high"

    def test_body_contains_status_table(self):
        note = build_coverage_note(self._minimal_controls())
        assert "| implemented | 1 |" in note.body
        assert "| partially-implemented | 1 |" in note.body
        assert "| not-implemented | 1 |" in note.body

    def test_body_contains_total_controls(self):
        note = build_coverage_note(self._minimal_controls())
        assert "Total controls: 3" in note.body

    def test_baseline_gaps_section_when_provided(self):
        gaps = ["ac-5", "ac-6"]
        note = build_coverage_note(self._minimal_controls(), baseline_gaps=gaps)
        assert "Baseline Gaps" in note.body
        assert "ac-5" in note.body
        assert "ac-6" in note.body

    def test_no_baseline_gaps_section_when_none(self):
        note = build_coverage_note(self._minimal_controls(), baseline_gaps=None)
        assert "Baseline Gaps" not in note.body

    def test_full_baseline_coverage_message(self):
        note = build_coverage_note(self._minimal_controls(), baseline_gaps=[])
        assert "full baseline coverage" in note.body

    def test_crm_only_section_when_provided(self):
        note = build_coverage_note(
            self._minimal_controls(), crm_only=["au-99", "cm-99"]
        )
        assert "CRM-Only Controls" in note.body
        assert "au-99" in note.body

    def test_no_crm_section_when_none(self):
        note = build_coverage_note(self._minimal_controls(), crm_only=None)
        assert "CRM-Only Controls" not in note.body

    def test_dataview_query_present(self):
        note = build_coverage_note(self._minimal_controls())
        assert "```dataview" in note.body


# ---------------------------------------------------------------------------
# compute_coverage
# ---------------------------------------------------------------------------


class TestComputeCoverage:
    def _ssp(self) -> dict:
        return {
            "ac-1": {"implementation_status": "implemented"},
            "ac-2": {"implementation_status": "implemented"},
            "ac-3": {"implementation_status": "not-implemented"},
            "si-99": {"implementation_status": "implemented"},  # beyond baseline
        }

    def _baseline(self) -> set:
        return {"ac-1", "ac-2", "ac-3", "ac-4", "ac-5"}

    def test_ssp_only_identifies_extra_controls(self):
        metrics = compute_coverage(self._ssp(), self._baseline())
        assert "si-99" in metrics["ssp_only"]
        assert "ac-1" not in metrics["ssp_only"]

    def test_baseline_gaps_identifies_missing_controls(self):
        metrics = compute_coverage(self._ssp(), self._baseline())
        assert "ac-4" in metrics["baseline_gaps"]
        assert "ac-5" in metrics["baseline_gaps"]
        assert "ac-1" not in metrics["baseline_gaps"]

    def test_status_distribution_counts(self):
        metrics = compute_coverage(self._ssp(), self._baseline())
        dist = metrics["status_dist"]
        assert dist["implemented"] == 3
        assert dist["not-implemented"] == 1

    def test_crm_only_when_provided(self):
        crm = {"ac-1", "crm-99", "crm-100"}
        metrics = compute_coverage(self._ssp(), self._baseline(), crm_controls=crm)
        assert "crm-99" in metrics["crm_only"]
        assert "crm-100" in metrics["crm_only"]
        assert "ac-1" not in metrics["crm_only"]  # already in SSP

    def test_crm_only_empty_when_no_crm(self):
        metrics = compute_coverage(self._ssp(), self._baseline())
        assert metrics["crm_only"] == set()

    def test_origination_distribution(self):
        ssp = {
            "ac-1": {"origination": ["system-specific"]},
            "ac-2": {"origination": ["inherited", "system-specific"]},
        }
        metrics = compute_coverage(ssp, {"ac-1", "ac-2"})
        dist = metrics["origination_dist"]
        assert dist["system-specific"] == 2
        assert dist["inherited"] == 1


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def _metrics(self) -> dict:
        return {
            "ssp_ids": {"ac-1", "ac-2", "si-99"},
            "baseline_ids": {"ac-1", "ac-2", "ac-3"},
            "ssp_only": {"si-99"},
            "baseline_gaps": {"ac-3"},
            "crm_only": set(),
            "status_dist": Counter({"implemented": 2, "not-implemented": 1}),
            "origination_dist": Counter({"system-specific": 3}),
        }

    def test_title_includes_system_and_level(self):
        md = render_markdown(
            self._metrics(), system_name="My System", fips_level="moderate"
        )
        assert "# Coverage Report: My System (moderate)" in md

    def test_generated_date_present(self):
        md = render_markdown(self._metrics())
        assert "Generated:" in md

    def test_summary_section(self):
        md = render_markdown(self._metrics(), fips_level="moderate")
        assert "SSP controls:" in md
        assert "Baseline controls (FedRAMP moderate):" in md
        assert "Baseline coverage:" in md

    def test_baseline_gap_listed(self):
        md = render_markdown(self._metrics())
        assert "ac-3" in md

    def test_ssp_only_listed(self):
        md = render_markdown(self._metrics())
        assert "si-99" in md

    def test_no_crm_section_when_empty(self):
        md = render_markdown(self._metrics())
        assert "CRM-Only Controls" not in md

    def test_baseline_provenance_is_visible(self):
        md = render_markdown(
            self._metrics(),
            fips_level="low",
            baseline_provenance={
                "semantics": "GSA Low assessment baseline",
                "source": {"source_workbook_sha256": "a" * 64},
            },
        )
        assert "Baseline semantics: GSA Low assessment baseline" in md
        assert f"Baseline source workbook SHA-256: `{'a' * 64}`" in md


# ---------------------------------------------------------------------------
# render_json
# ---------------------------------------------------------------------------


class TestRenderJson:
    def _metrics(self) -> dict:
        return {
            "ssp_ids": {"ac-1", "ac-2"},
            "baseline_ids": {"ac-1", "ac-2", "ac-3"},
            "ssp_only": set(),
            "baseline_gaps": {"ac-3"},
            "crm_only": set(),
            "status_dist": Counter({"implemented": 2}),
            "origination_dist": Counter({"system-specific": 2}),
        }

    def test_json_is_serializable(self):
        out = render_json(self._metrics())
        # Must not raise.
        data = json.dumps(out)
        assert data is not None

    def test_summary_keys_present(self):
        out = render_json(self._metrics())
        assert "summary" in out
        assert "baseline_gaps" in out
        assert "ssp_only" in out

    def test_coverage_percentage_computed(self):
        out = render_json(self._metrics())
        # 2 of 3 covered → 66.7%
        assert out["summary"]["baseline_covered"] == 2
        assert out["summary"]["baseline_coverage_pct"] == pytest.approx(66.7, abs=0.1)

    def test_json_carries_baseline_provenance(self):
        receipt = {"verification_status": "unverified-source-provenance"}
        out = render_json(self._metrics(), baseline_provenance=receipt)
        assert out["baseline_provenance"] == receipt


# ---------------------------------------------------------------------------
# get_baseline
# ---------------------------------------------------------------------------


class TestGetBaseline:
    def test_low_returns_nonempty_set(self):
        bl = get_baseline("low")
        assert len(bl) == 158
        assert "ac-1" in bl

    def test_moderate_superset_of_low(self):
        low = get_baseline("low")
        mod = get_baseline("moderate")
        assert low.issubset(mod)

    def test_high_superset_of_moderate(self):
        mod = get_baseline("moderate")
        high = get_baseline("high")
        assert mod.issubset(high)

    def test_unknown_level_defaults_to_moderate(self):
        bl = get_baseline("unknown")
        mod = get_baseline("moderate")
        assert bl == mod


# ---------------------------------------------------------------------------
# coverage_report imports cleanly
# ---------------------------------------------------------------------------


def test_coverage_report_imports():
    import compliance.scripts.coverage_report as cr

    assert hasattr(cr, "main")
    assert hasattr(cr, "compute_coverage")
    assert hasattr(cr, "render_markdown")
    assert hasattr(cr, "render_json")
    assert hasattr(cr, "get_baseline")
