"""Tests for Feature R29: Content Coverage & Integrity Report.

Complements ``compliance/tests/test_coverage_report.py`` (which tests
control-*id* set reconciliation against a FedRAMP baseline). This file tests
per-control *content-completeness* auditing: whether a control note still
carries a stale/placeholder marker instead of real recovered content.

All ``analyze_control_note`` / ``analyze_vault_controls`` / ``summarize`` /
``render_markdown`` / ``render_json`` tests are self-contained (write a
synthetic note to ``tmp_path``). The section-guidance-reachability test is
fixture-gated on the committed Moderate blank template, matching the
convention in ``test_control_guidance.py``.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.scripts.content_integrity_report import (  # noqa: E402
    ControlIntegrity,
    VaultIntegritySummary,
    analyze_control_note,
    analyze_vault_controls,
    render_json,
    render_markdown,
    section_guidance_reachability,
    summarize,
)

_TEMPLATES = _REPO_ROOT / "templates"
_MOD = _TEMPLATES / "Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx"


def _write_note(tmp_path: Path, name: str, frontmatter: str, body: str) -> Path:
    controls_dir = tmp_path / "Controls"
    controls_dir.mkdir(exist_ok=True)
    p = controls_dir / f"{name}.md"
    p.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# analyze_control_note: marker detection
# ---------------------------------------------------------------------------


class TestAnalyzeControlNote:
    def test_stale_upstream_detected(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\nparameters: []\n",
            "# AC-01\n\n# Upstream\n\n"
            "> Original upstream control language for `AC-01` "
            "from the catalog or imported baseline.\n",
        )
        result = analyze_control_note(p)
        assert result.stale_upstream is True
        assert "stale_upstream" in result.defect_flags()

    def test_current_upstream_not_flagged_stale(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\n"
            "parameters:\n- param_id: ac-01_odp.01\n  value: x\n",
            "# AC-01\n\n# Upstream\n\n- Develop, document, and disseminate...\n",
        )
        result = analyze_control_note(p)
        assert result.stale_upstream is False
        assert "stale_upstream" not in result.defect_flags()

    def test_no_catalog_match_detected_but_not_a_defect(self, tmp_path):
        p = _write_note(
            tmp_path,
            "ZZ-99",
            "control_id: ZZ-99\nimplementation_status: implemented\nparameters: []\n",
            "# ZZ-99\n\n# Upstream\n\n"
            "> No NIST SP 800-53 rev5 catalog statement is vendored for "
            "`ZZ-99` (custom or non-catalog control).\n",
        )
        result = analyze_control_note(p)
        assert result.no_catalog_match is True
        # Informational only -- not counted as a regression/defect.
        assert "no_catalog_match" not in result.defect_flags()

    def test_raw_odp_placeholder_detected(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\nparameters: []\n",
            "# Upstream\n\n"
            "- Develop {{ insert: param, ac-01_odp.01 }} and disseminate.\n",
        )
        result = analyze_control_note(p)
        assert result.raw_odp_placeholder is True
        assert "raw_odp_placeholder" in result.defect_flags()

    def test_bare_param_fallback_detected(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\nparameters: []\n",
            "# Upstream\n\n- Develop [Assignment: ac-01_odp.01] and disseminate.\n",
        )
        result = analyze_control_note(p)
        assert result.bare_param_fallback is True
        assert "bare_param_fallback" in result.defect_flags()

    def test_descriptive_assignment_label_not_flagged(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\nparameters: []\n",
            "# Upstream\n\n- Develop [Assignment: personnel or roles] and disseminate.\n",
        )
        result = analyze_control_note(p)
        assert result.bare_param_fallback is False

    def test_no_part_narrative_detected(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-99",
            "control_id: AC-99\nimplementation_status: not-implemented\n"
            "parameters: []\n",
            "# Statement Parts\n\n"
            "_(No part-level narrative was captured by the parser.)_\n",
        )
        result = analyze_control_note(p)
        assert result.no_part_narrative is True
        assert "no_part_narrative" in result.defect_flags()

    def test_empty_part_text_detected(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-02",
            "control_id: AC-02\nimplementation_status: not-implemented\n"
            "parameters: []\n",
            "## Part a\n\n_(No narrative recorded in source SSP.)_\n",
        )
        result = analyze_control_note(p)
        assert result.empty_part_text is True
        assert "empty_part_text" in result.defect_flags()

    def test_no_evidence_detected(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\nparameters: []\n",
            "# Evidence\n\n_(No evidence references recorded in source SSP.)_\n",
        )
        result = analyze_control_note(p)
        assert result.no_evidence is True
        assert "no_evidence" in result.defect_flags()

    def test_no_related_controls_detected(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\nparameters: []\n",
            "## Related Controls\n\n_(Not recorded in source SSP.)_\n",
        )
        result = analyze_control_note(p)
        assert result.no_related_controls is True
        assert "no_related_controls" in result.defect_flags()

    def test_omitted_evidence_and_related_sections_detected(self, tmp_path):
        # ADR-025: empty Evidence / Related Controls sections are omitted
        # entirely; their absence must still count as no_evidence /
        # no_related_controls (not a false "present").
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\nparameters: []\n",
            "# Statement Parts\n\n## Part a\n\nnarrative here\n",
        )
        result = analyze_control_note(p)
        assert result.no_evidence is True
        assert result.no_related_controls is True

    def test_no_parameters_when_frontmatter_key_absent(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\n",
            "# AC-01\n",
        )
        result = analyze_control_note(p)
        assert result.no_parameters is True
        assert "no_parameters" in result.defect_flags()

    def test_no_parameters_false_when_present(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\n"
            "parameters:\n- param_id: ac-01_odp.01\n  value: x\n",
            "# AC-01\n",
        )
        result = analyze_control_note(p)
        assert result.no_parameters is False

    def test_guidance_present_detected(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: implemented\nparameters: []\n",
            "> [!note]- Template guidance\n> Some instruction.\n",
        )
        result = analyze_control_note(p)
        assert result.guidance_present is True

    def test_implementation_status_captured(self, tmp_path):
        p = _write_note(
            tmp_path,
            "AC-01",
            "control_id: AC-01\nimplementation_status: partially-implemented\n"
            "parameters: []\n",
            "# AC-01\n",
        )
        result = analyze_control_note(p)
        assert result.implementation_status == "partially-implemented"

    def test_control_id_falls_back_to_filename_stem(self, tmp_path):
        controls_dir = tmp_path / "Controls"
        controls_dir.mkdir()
        p = controls_dir / "SC-07.md"
        p.write_text("no frontmatter here", encoding="utf-8")
        result = analyze_control_note(p)
        assert result.control_id == "SC-07"

    def test_missing_file_does_not_raise(self, tmp_path):
        result = analyze_control_note(tmp_path / "Controls" / "nonexistent.md")
        assert isinstance(result, ControlIntegrity)


# ---------------------------------------------------------------------------
# analyze_vault_controls
# ---------------------------------------------------------------------------


class TestAnalyzeVaultControls:
    def test_walks_all_control_notes(self, tmp_path):
        _write_note(
            tmp_path, "AC-01", "control_id: AC-01\nparameters: []\n", "# AC-01\n"
        )
        _write_note(
            tmp_path, "AC-02", "control_id: AC-02\nparameters: []\n", "# AC-02\n"
        )
        results = analyze_vault_controls(tmp_path)
        assert len(results) == 2
        assert {r.control_id for r in results} == {"AC-01", "AC-02"}

    def test_empty_when_no_controls_dir(self, tmp_path):
        assert analyze_vault_controls(tmp_path) == []


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


class TestSummarize:
    def test_counts_defects_across_controls(self):
        results = [
            ControlIntegrity(
                control_id="AC-01",
                relpath="Controls/AC-01.md",
                implementation_status="implemented",
                stale_upstream=True,
                no_evidence=True,
            ),
            ControlIntegrity(
                control_id="AC-02",
                relpath="Controls/AC-02.md",
                implementation_status="implemented",
                stale_upstream=True,
            ),
        ]
        summary = summarize("test-vault", results)
        assert summary.total_controls == 2
        assert summary.defect_counts["stale_upstream"] == 2
        assert summary.defect_counts["no_evidence"] == 1

    def test_implemented_missing_evidence_ratio(self):
        results = [
            ControlIntegrity(
                control_id="AC-01",
                relpath="x",
                implementation_status="implemented",
                no_evidence=True,
            ),
            ControlIntegrity(
                control_id="AC-02",
                relpath="x",
                implementation_status="implemented",
                no_evidence=False,
            ),
            ControlIntegrity(
                control_id="AC-03",
                relpath="x",
                implementation_status="not-implemented",
                no_evidence=True,
            ),
        ]
        summary = summarize("test-vault", results)
        assert summary.implemented_total == 2
        assert summary.implemented_no_evidence == 1

    def test_empty_results(self):
        summary = summarize("empty-vault", [])
        assert summary.total_controls == 0
        assert summary.implemented_total == 0


# ---------------------------------------------------------------------------
# render_markdown / render_json
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def _summary(self) -> VaultIntegritySummary:
        s = VaultIntegritySummary(vault_name="demo-vault", total_controls=10)
        s.defect_counts = Counter({"stale_upstream": 3, "no_evidence": 8})
        s.implemented_total = 5
        s.implemented_no_evidence = 4
        return s

    def test_title_present(self):
        md = render_markdown([self._summary()])
        assert "# Content Coverage & Integrity Report" in md

    def test_vault_row_present(self):
        md = render_markdown([self._summary()])
        assert "demo-vault" in md
        assert "| demo-vault | 10 | 3 |" in md

    def test_missing_evidence_section_present(self):
        md = render_markdown([self._summary()])
        assert "Implemented Controls Missing Evidence" in md
        assert "80%" in md

    def test_aggregate_section_present(self):
        md = render_markdown([self._summary()])
        assert "## Aggregate" in md
        assert "Total control notes scanned: 10" in md

    def test_empty_summaries_does_not_raise(self):
        md = render_markdown([])
        assert "# Content Coverage & Integrity Report" in md


class TestRenderJson:
    def _summary(self) -> VaultIntegritySummary:
        s = VaultIntegritySummary(vault_name="demo-vault", total_controls=10)
        s.defect_counts = Counter({"stale_upstream": 3})
        s.implemented_total = 5
        s.implemented_no_evidence = 4
        return s

    def test_json_serializable(self):
        import json

        out = render_json([self._summary()])
        data = json.dumps(out)
        assert data is not None

    def test_vault_entry_shape(self):
        out = render_json([self._summary()])
        assert out["vaults"][0]["vault_name"] == "demo-vault"
        assert out["vaults"][0]["defect_counts"]["stale_upstream"] == 3

    def test_section_guidance_included_when_provided(self):
        out = render_json([self._summary()], section_guidance={"total_headings": 5})
        assert out["section_guidance_reachability"]["total_headings"] == 5

    def test_section_guidance_omitted_when_none(self):
        out = render_json([self._summary()])
        assert "section_guidance_reachability" not in out


# ---------------------------------------------------------------------------
# section_guidance_reachability (fixture-gated on committed Moderate blank)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MOD.exists(), reason="blank Moderate template not present")
def test_section_guidance_reachability_moderate_template():
    result = section_guidance_reachability(_MOD)
    # ADR-023 measured 143 front-matter instructions across ~52 headings in
    # the Moderate template, of which only a handful map onto a rendered
    # narrative-note heading (_NARRATIVE_NOTE_SPECS in compliance/lib/vault.py).
    assert result["total_headings"] >= 40
    assert result["total_instructions"] >= 100
    assert result["reachable_headings"] < result["total_headings"]
    assert result["reachable_instructions"] < result["total_instructions"]
    assert isinstance(result["unreachable_headings"], list)
    assert len(result["unreachable_headings"]) > 0


# ---------------------------------------------------------------------------
# content_integrity_report imports cleanly
# ---------------------------------------------------------------------------


def test_content_integrity_report_imports():
    import compliance.scripts.content_integrity_report as cir

    assert hasattr(cir, "main")
    assert hasattr(cir, "analyze_control_note")
    assert hasattr(cir, "analyze_vault_controls")
    assert hasattr(cir, "summarize")
    assert hasattr(cir, "render_markdown")
    assert hasattr(cir, "render_json")
    assert hasattr(cir, "section_guidance_reachability")
