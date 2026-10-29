"""Unit tests for compliance.lib.fips199 (Feature R24).

All tests are self-contained: no .docx fixture files are required. python-docx
is used in-memory to construct synthetic documents for parse_fips199().
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.fips199 import (  # noqa: E402
    Fips199Result,
    InformationType,
    _normalize_impact,
    fips199_to_oscal_information_types,
    parse_fips199,
)

# ---------------------------------------------------------------------------
# Impact normalizer
# ---------------------------------------------------------------------------


class TestNormalizeImpact:
    def test_l_to_low(self):
        assert _normalize_impact("L") == "low"

    def test_m_to_moderate(self):
        assert _normalize_impact("M") == "moderate"

    def test_h_to_high(self):
        assert _normalize_impact("H") == "high"

    def test_full_word_low(self):
        assert _normalize_impact("Low") == "low"

    def test_full_word_moderate(self):
        assert _normalize_impact("Moderate") == "moderate"

    def test_full_word_high(self):
        assert _normalize_impact("High") == "high"

    def test_na_maps_to_low(self):
        assert _normalize_impact("N/A") == "low"

    def test_unknown_passthrough(self):
        # Unknown values are passed through unchanged.
        assert _normalize_impact("foobar") == "foobar"


# ---------------------------------------------------------------------------
# parse_fips199 with mock docx
# ---------------------------------------------------------------------------


def _make_mock_doc(
    paragraphs: list[str],
    tables: list[dict],
) -> MagicMock:
    """Build a minimal mock python-docx Document.

    *tables* is a list of dicts::

        {
          "headers": ["Information Type", "Confidentiality", "Integrity", "Availability"],
          "rows": [["Type A", "Moderate", "Low", "High"]],
        }
    """
    doc = MagicMock()
    doc.paragraphs = [_para(p) for p in paragraphs]

    mock_tables = []
    for t in tables:
        tbl = MagicMock()
        header_cells = [_cell(h) for h in t["headers"]]
        header_row = MagicMock()
        header_row.cells = header_cells
        data_rows = []
        for row_vals in t["rows"]:
            row = MagicMock()
            row.cells = [_cell(v) for v in row_vals]
            data_rows.append(row)
        tbl.rows = [header_row] + data_rows
        mock_tables.append(tbl)

    doc.tables = mock_tables
    return doc


def _para(text: str) -> MagicMock:
    p = MagicMock()
    p.text = text
    return p


def _cell(text: str) -> MagicMock:
    c = MagicMock()
    c.text = text
    return c


class TestParseFips199WithMock:
    """patch docx.Document so no real .docx file is needed."""

    def _run(self, doc: MagicMock) -> Fips199Result | None:
        with patch("compliance.lib.fips199.Document", return_value=doc):
            return parse_fips199("fake.docx")

    def test_returns_none_on_file_error(self):
        with patch("compliance.lib.fips199.Document", side_effect=FileNotFoundError):
            result = parse_fips199("missing.docx")
        assert result is None

    def test_basic_parse_returns_result(self):
        doc = _make_mock_doc(
            paragraphs=[
                "The system has been determined to be a Moderate impact system."
            ],
            tables=[
                {
                    "headers": [
                        "Information Type",
                        "Confidentiality",
                        "Integrity",
                        "Availability",
                    ],
                    "rows": [["Mission Support", "Moderate", "Moderate", "Low"]],
                }
            ],
        )
        result = self._run(doc)
        assert result is not None
        assert result.overall_level == "moderate"
        assert len(result.information_types) == 1
        it = result.information_types[0]
        assert it.title == "Mission Support"
        assert it.confidentiality == "moderate"
        assert it.integrity == "moderate"
        assert it.availability == "low"

    def test_high_level_detected(self):
        doc = _make_mock_doc(
            paragraphs=["The system has been determined to be a High impact level."],
            tables=[
                {
                    "headers": [
                        "Information Type",
                        "Confidentiality",
                        "Integrity",
                        "Availability",
                    ],
                    "rows": [["Agency Data", "High", "High", "Moderate"]],
                }
            ],
        )
        result = self._run(doc)
        assert result is not None
        assert result.overall_level == "high"

    def test_level_fallback_from_info_types(self):
        """When no explicit level phrase is in paragraphs, derive from max impact."""
        doc = _make_mock_doc(
            paragraphs=["This document describes the system categorization."],
            tables=[
                {
                    "headers": [
                        "Information Type",
                        "Confidentiality",
                        "Integrity",
                        "Availability",
                    ],
                    "rows": [["Financial Data", "High", "Moderate", "Low"]],
                }
            ],
        )
        result = self._run(doc)
        assert result is not None
        assert result.overall_level == "high"

    def test_sp800_60_id_extracted_from_cell(self):
        """SP 800-60 identifiers like C.2.6.1 should be captured."""
        doc = _make_mock_doc(
            paragraphs=[],
            tables=[
                {
                    "headers": [
                        "Information Type",
                        "Confidentiality",
                        "Integrity",
                        "Availability",
                    ],
                    "rows": [["C.2.6.1 Tax Data", "Moderate", "Low", "Low"]],
                }
            ],
        )
        result = self._run(doc)
        assert result is not None
        assert len(result.information_types) == 1
        assert result.information_types[0].sp800_60_id == "C.2.6.1"

    def test_empty_document_returns_result_with_defaults(self):
        """Empty doc should return a result with default level (no crash)."""
        doc = _make_mock_doc(paragraphs=[], tables=[])
        result = self._run(doc)
        assert result is not None
        assert result.overall_level == "moderate"
        assert result.information_types == []

    def test_short_impact_values(self):
        """L/M/H short form should normalize correctly."""
        doc = _make_mock_doc(
            paragraphs=["determined to be a low"],
            tables=[
                {
                    "headers": [
                        "Information Type",
                        "Confidentiality",
                        "Integrity",
                        "Availability",
                    ],
                    "rows": [["Support Data", "L", "M", "H"]],
                }
            ],
        )
        result = self._run(doc)
        assert result is not None
        it = result.information_types[0]
        assert it.confidentiality == "low"
        assert it.integrity == "moderate"
        assert it.availability == "high"

    def test_table_without_cia_headers_skipped(self):
        """Tables that don't have recognizable C/I/A headers should be skipped."""
        doc = _make_mock_doc(
            paragraphs=["determined to be a moderate"],
            tables=[
                {
                    "headers": ["Section", "Description", "Notes"],
                    "rows": [["1", "Some text", "note"]],
                }
            ],
        )
        result = self._run(doc)
        assert result is not None
        assert result.information_types == []


# ---------------------------------------------------------------------------
# fips199_to_oscal_information_types
# ---------------------------------------------------------------------------


class TestFips199ToOscal:
    def _sample_result(self) -> Fips199Result:
        return Fips199Result(
            overall_level="moderate",
            information_types=[
                InformationType(
                    title="Financial Management",
                    sp800_60_id="C.2.4.1",
                    confidentiality="moderate",
                    integrity="moderate",
                    availability="low",
                ),
                InformationType(
                    title="General Support",
                    sp800_60_id=None,
                    confidentiality="low",
                    integrity="low",
                    availability="low",
                ),
            ],
        )

    def test_returns_list_length_matches(self):
        result = self._sample_result()
        out = fips199_to_oscal_information_types(result)
        assert len(out) == 2

    def test_uuid_is_deterministic(self):
        result = self._sample_result()
        out1 = fips199_to_oscal_information_types(result)
        out2 = fips199_to_oscal_information_types(result)
        assert out1[0]["uuid"] == out2[0]["uuid"]

    def test_title_and_description_present(self):
        result = self._sample_result()
        out = fips199_to_oscal_information_types(result)
        assert out[0]["title"] == "Financial Management"
        assert "information type" in out[0]["description"].lower()

    def test_categorizations_with_sp800_60_id(self):
        result = self._sample_result()
        out = fips199_to_oscal_information_types(result)
        cats = out[0].get("categorizations")
        assert cats is not None
        assert len(cats) == 1
        assert cats[0]["system"] == "http://doi.org/10.6028/NIST.SP.800-60v2r1"
        assert "C.2.4.1" in cats[0]["information-type-ids"]

    def test_no_categorizations_when_no_sp800_60_id(self):
        result = self._sample_result()
        out = fips199_to_oscal_information_types(result)
        assert "categorizations" not in out[1]

    def test_oscal_10x_adds_impact_triad(self):
        result = self._sample_result()
        out = fips199_to_oscal_information_types(result, oscal_version="1.0.4")
        assert out[0]["confidentiality-impact"] == {"base": "moderate"}
        assert out[0]["integrity-impact"] == {"base": "moderate"}
        assert out[0]["availability-impact"] == {"base": "low"}

    def test_oscal_11x_no_impact_triad(self):
        result = self._sample_result()
        out = fips199_to_oscal_information_types(result, oscal_version="1.1.3")
        assert "confidentiality-impact" not in out[0]
        assert "integrity-impact" not in out[0]
        assert "availability-impact" not in out[0]

    def test_empty_information_types_returns_empty_list(self):
        result = Fips199Result(overall_level="low", information_types=[])
        out = fips199_to_oscal_information_types(result)
        assert out == []


# ---------------------------------------------------------------------------
# export_oscal synthetic fallback vs real information_types
# ---------------------------------------------------------------------------


class TestExportOscalInformationTypes:
    """Verify that export_oscal uses real data when present in sys_fm."""

    def test_synthetic_fallback_when_no_fips199_data(self):
        from compliance.lib.export_oscal import _information_types

        result = _information_types("My System", "moderate", "1.1.3")
        assert len(result) == 1
        assert result[0]["title"] == "My System information"

    def test_real_data_used_when_present(self):
        from compliance.lib.export_oscal import _information_types

        real = [
            {
                "uuid": "aaaaaaaa-0000-5000-8000-000000000001",
                "title": "Real Type",
                "description": "Real Type information type",
            }
        ]
        result = _information_types(
            "My System", "moderate", "1.1.3", sys_fm={"information_types": real}
        )
        assert len(result) == 1
        assert result[0]["title"] == "Real Type"

    def test_empty_list_in_sys_fm_uses_fallback(self):
        from compliance.lib.export_oscal import _information_types

        result = _information_types(
            "My System", "moderate", "1.1.3", sys_fm={"information_types": []}
        )
        assert result[0]["title"] == "My System information"
