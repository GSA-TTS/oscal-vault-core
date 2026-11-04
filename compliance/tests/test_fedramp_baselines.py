"""Provenance and template-alignment gates for coverage baseline data."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from compliance.lib.control_identity import canonical_control_id
from compliance.lib.control_table_anchors import find_control_tables

ROOT = Path(__file__).resolve().parents[2]
BASELINES = ROOT / "compliance" / "data" / "fedramp_baselines.json"
PROVENANCE = ROOT / "compliance" / "data" / "fedramp_baselines_provenance.json"
LOW_TEMPLATE = ROOT / "compliance" / "templates" / "low-sspp.jinja.docx"


def _load() -> tuple[dict, dict]:
    return (
        json.loads(BASELINES.read_text(encoding="utf-8")),
        json.loads(PROVENANCE.read_text(encoding="utf-8")),
    )


def _digest(control_ids: list[str]) -> str:
    return hashlib.sha256(("\n".join(control_ids) + "\n").encode()).hexdigest()


def test_every_baseline_has_a_count_and_content_receipt():
    baselines, provenance = _load()

    assert set(baselines) == {"low", "moderate", "high"}
    assert set(provenance) == {"schema_version", *baselines}
    for level, control_ids in baselines.items():
        receipt = provenance[level]
        assert receipt["control_count"] == len(control_ids)
        assert receipt["control_ids_sha256"] == _digest(control_ids)
        assert len(control_ids) == len(set(control_ids))
        assert all(canonical_control_id(control_id) for control_id in control_ids)


def test_low_is_the_gsa_assessment_reference_not_nist_low():
    baselines, provenance = _load()
    low = provenance["low"]

    assert len(baselines["low"]) == 158
    assert low["semantics"] == (
        "GSA Low assessment baseline used for SSP coverage reconciliation"
    )
    assert low["source"] == {
        "assessment_reference_sha256": (
            "f4f53710ed5160ccfde868b7e331e73509ed168e75b513240a5ce7e7679e2658"
        ),
        "kind": "assessment-reference-derived",
        "reference_id": "gsa-pages-low-rev5",
        "reference_schema_version": 1,
        "source_blob": "b1ac3859bd8579c50ae826824954a817ccba1246",
        "source_commit": "aa6edfe7227ca45a42243b334ec98afacf0eb14c",
        "source_workbook_sha256": (
            "28aadb12e16db22e28a9c092c19560ef0d58ab898434476ab0f71e1517a50fa8"
        ),
    }


def test_low_template_matches_the_gsa_assessment_reference():
    baselines, provenance = _load()
    expected = {canonical_control_id(control_id) for control_id in baselines["low"]}
    template_ids = [
        canonical_control_id(anchor.control_id)
        for anchor in find_control_tables(LOW_TEMPLATE)
    ]
    actual = set(template_ids)
    duplicates = sorted(
        control_id for control_id, count in Counter(template_ids).items() if count > 1
    )

    observed = {
        "reference_without_template_table": sorted(expected - actual),
        "template_table_without_reference": sorted(actual - expected),
        "duplicate_template_control_ids": duplicates,
    } == {
        "reference_without_template_table": [],
        "template_table_without_reference": [],
        "duplicate_template_control_ids": [],
    }
    assert observed
    assert provenance["low"]["known_template_alignment"]["issues"] == {
        "reference_without_template_table": None,
        "template_table_without_reference": None,
    }


def test_unverified_lists_do_not_inherit_low_provenance():
    _, provenance = _load()

    for level in ("moderate", "high"):
        assert provenance[level]["verification_status"] == (
            "unverified-source-provenance"
        )
        assert "source" not in provenance[level]
