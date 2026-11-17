"""The canonical GSA workbook JSON the core ships must stay valid, public-safe and in sync."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from compliance.lib import gsa_workbooks as g
from compliance.lib.redaction import EMAIL_RE, URL_RE

DATA = g.DATA_DIR
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
FILES = (g.TAILORING_FILENAME, g.TEST_CASES_FILENAME)


def test_bundled_files_exist_with_provenance() -> None:
    for name in FILES:
        assert (DATA / name).is_file()
    prov = (DATA / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "Release determination" in prov and "Do not add" in prov


@pytest.mark.parametrize("name", FILES)
def test_bundled_json_validates(name: str) -> None:
    g.load_bundled(name)


def test_bundled_counts_match_documented_provenance() -> None:
    ctw = g.load_bundled(g.TAILORING_FILENAME)
    tc = g.load_bundled(g.TEST_CASES_FILENAME)
    assert ctw["total_controls"] == 1194 and len(ctw["hva_overlay"]) == 131
    assert tc["total_controls_in_scope"] == 34 and tc["total_test_cases"] == 191
    prov = (DATA / "PROVENANCE.md").read_text(encoding="utf-8")
    for needle in (ctw["version"], tc["version"], "1,194", "131", "191"):
        assert needle in prov


@pytest.mark.parametrize("name", FILES)
def test_bundled_json_contains_no_unredacted_or_system_content(name: str) -> None:
    text = (DATA / name).read_text(encoding="utf-8")
    assert not URL_RE.search(text) and not EMAIL_RE.search(text)
    assert not UUID.search(text) and not IPV4.search(text)
    assert "CUI//" not in text
    assert not re.search(r"(?i)fpdf|databricks|mcaas|gsa-pages|/Users/", text)


def test_test_case_workbook_ships_blank_of_assessment_content() -> None:
    tc = g.load_bundled(g.TEST_CASES_FILENAME)
    for case in tc["test_cases"]:
        assert not case["observations_and_evidence"]
        assert not case["assessment_result"]
        assert not case["recommendation_for_mitigation"]
    assert {s["assessment_result"] for s in tc["summary"]} == {"Incomplete"}
    ctw = g.load_bundled(g.TAILORING_FILENAME)
    assert not any(
        c["vendor_contractor_values"] or c["gsa_approval"] for c in ctw["controls"]
    )


def test_engine_runs_on_bundled_data_with_no_workspace_catalog(tmp_path: Path) -> None:
    from compliance.lib.gsa_assessment import build_assessment

    system = {
        "schema_version": 1,
        "system": {"slug": "demo", "name": "Demo", "baseline": "Low"},
        "controls": [],
    }
    a = build_assessment(
        system,
        g.load_bundled(g.TAILORING_FILENAME),
        g.load_bundled(g.TEST_CASES_FILENAME),
    )
    assert a["scope"]["objectives"] == 191
    assert a["attestation"]["ao_decision"] is None
