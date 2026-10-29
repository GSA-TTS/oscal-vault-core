from __future__ import annotations

from pathlib import Path

import pytest

from compliance.scripts.docx_coverage_metrics import (
    IntegrityMetrics,
    SemanticCoverage,
    StructuralCoverage,
    collect,
    main,
    render_markdown,
    to_jsonable,
)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
LOW = "Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx"
MODERATE = "Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx"


def _template(name: str) -> Path:
    path = TEMPLATES / name
    if not path.is_file():
        pytest.skip(f"template not available: {path}")
    return path


def test_collect_returns_all_three_metric_families():
    result = collect(_template(MODERATE))
    assert isinstance(result["structural"], StructuralCoverage)
    assert isinstance(result["semantic"], SemanticCoverage)
    assert isinstance(result["integrity"], IntegrityMetrics)


def test_moderate_structural_coverage_shape():
    struct: StructuralCoverage = collect(_template(MODERATE))["structural"]
    assert struct.total_blocks > 2_000
    assert struct.routed_tables > 300
    assert struct.dropped_tables > 0
    assert struct.instruction_paragraphs > 500
    assert 0.45 <= struct.routable_block_coverage <= 0.60


def test_cis_readback_is_lossless_on_blank_templates():
    for name in (LOW, MODERATE):
        integ: IntegrityMetrics = collect(_template(name))["integrity"]
        assert integ.cis_tables_detected > 100
        assert integ.cis_tables_readback_fail == 0
        assert integ.readback_fraction == 1.0


def test_part_narrative_survival_lossless_where_present():
    for name in (LOW, MODERATE):
        integ: IntegrityMetrics = collect(_template(name))["integrity"]
        assert integ.total_part_narrative_slots > 0
        assert integ.part_narrative_fraction == 1.0


def test_semantic_control_and_narrative_elements_are_routed():
    sem: SemanticCoverage = collect(_template(MODERATE))["semantic"]
    elems = {e["element"]: e for e in sem.elements}
    assert elems["control_responses"]["routed"] is True
    assert elems["system_description"]["routed"] is True
    assert elems["network_architecture"]["routed"] is True
    assert elems["asset_inventory"]["present"] is True
    assert elems["asset_inventory"]["routed"] is False


def test_render_markdown_and_json_have_all_sections():
    result = collect(_template(MODERATE))
    md = render_markdown(result)
    assert "Structural coverage" in md
    assert "Semantic coverage" in md
    assert "Integrity" in md
    assert "Top residual sections" in md
    jsonable = to_jsonable(result)
    assert jsonable["structural"]["routable_block_coverage"] > 0
    assert jsonable["integrity"]["readback_fraction"] == 1.0


def test_cli_writes_json(tmp_path):
    out = tmp_path / "coverage.json"
    rc = main(
        [
            "--docx",
            str(_template(MODERATE)),
            "--json",
            str(out),
            "--min-readback",
            "1.0",
        ]
    )
    assert rc == 0
    assert out.is_file()
    assert '"readback_fraction": 1.0' in out.read_text(encoding="utf-8")
