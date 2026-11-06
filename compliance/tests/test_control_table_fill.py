"""Tests for the control-summary (CIS) table structural fill (Phase R17).

Two tiers, matching the convention in ``test_docx_fill_roundtrip.py`` /
``test_instruction_anchors.py``:

1. **Synthetic** (always run): build a small ``.docx`` in memory with a
   hand-crafted CIS table matching the official templates' row layout, and
   assert detection + fill + fismatic-read-back all agree.

2. **Fixture-gated** (skip when the ``.docx`` is absent): run detection and a
   full synthesized fill against the real committed jinja templates (which
   ship in the repo, unlike the blanks) under ``compliance/templates/``, and
   assert a live round-trip through ``fismatic.parser`` — the exact same
   acceptance criterion ``test_docx_generator.py`` uses for the from-scratch
   generator, applied here to the in-place structural filler.
"""

from __future__ import annotations

import sys
from pathlib import Path

import docx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FISMATIC_SRC = _REPO_ROOT / "fismatic" / "src" / "phase_1"
_TEMPLATES = _REPO_ROOT / "compliance" / "templates"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_FISMATIC_SRC) not in sys.path:
    sys.path.insert(0, str(_FISMATIC_SRC))

from compliance.lib.canonical_model import CanonicalControl  # noqa: E402
from compliance.lib.control_checkbox_vocab import (  # noqa: E402
    CHECKED_GLYPH,
    ORIGINATION_KEY_PATTERNS,
    STATUS_KEY_PATTERNS,
    UNCHECKED_GLYPH,
)
from compliance.lib.control_table_anchors import (  # noqa: E402
    find_control_tables,
    summarize_anchors,
)
from compliance.lib.control_table_fill import (  # noqa: E402
    _match_key,
    _set_checkbox_lines,
    fill_control_tables,
)

_LOW_JINJA = _TEMPLATES / "low-sspp.jinja.docx"
_MOD_JINJA = _TEMPLATES / "moderate-sspp.jinja.docx"
_HIGH_JINJA = _TEMPLATES / "high-sspp.jinja.docx"

# fismatic's own read path -- imported lazily per-test since it requires the
# sys.path insert above to have already run.


def _fismatic_parser():
    from fismatic import parser as fp

    return fp


# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------


def _add_cis_table(doc, control_id: str, *, with_parts: bool = True):
    """Build a minimal CIS table matching the official templates' row layout.

    Merges each row's cells (matching the real templates -- confirmed via
    direct inspection that rows 1-3 are always a single merged cell spanning
    every column) since ``fismatic.parser.parse_control_table`` reads the
    *value* from ``row.cells[-1]`` while this module writes to
    ``row.cells[0]``; unmerged cells would silently desync the two.
    """
    ncols = 2
    nrows = 7 if with_parts else 5
    table = doc.add_table(rows=nrows, cols=ncols)
    table.rows[0].cells[0].text = control_id
    table.rows[0].cells[1].text = "Control Summary Information"

    status_row = table.rows[1]
    status_cell = status_row.cells[0].merge(status_row.cells[-1])
    status_cell.text = "Implementation Status:"
    for label in (
        "Implemented",
        "Partially Implemented",
        "Planned",
        "Alternative implementation",
        "Not applicable",
    ):
        status_cell.add_paragraph(f"{UNCHECKED_GLYPH} {label}")

    origin_row = table.rows[2]
    origin_cell = origin_row.cells[0].merge(origin_row.cells[-1])
    origin_cell.text = "Control Origination:"
    for label in (
        "Inherited from: [Enter System's Name]",
        "Hybrid Control with [list systems]",
        "System Specific Control",
    ):
        origin_cell.add_paragraph(f"{UNCHECKED_GLYPH} {label}")

    describe_row = table.rows[3]
    describe_cell = describe_row.cells[0].merge(describe_row.cells[-1])
    describe_cell.text = f"{control_id} Describe how the control is implemented."

    if with_parts:
        for ri, part in zip((4, 5, 6), ("a", "b", "c"), strict=True):
            row = table.rows[ri]
            row.cells[0].text = f"Part {part}"
    else:
        answer_row = table.rows[4]
        answer_row.cells[0].merge(answer_row.cells[-1])

    return table


# ---------------------------------------------------------------------------
# Synthetic tests (always run)
# ---------------------------------------------------------------------------


class TestSyntheticDetection:
    def test_finds_cis_table_with_parts(self):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=True)
        anchors = find_control_tables(doc)
        assert len(anchors) == 1
        assert anchors[0].control_id == "AC-1"
        assert set(anchors[0].part_rows.keys()) == {"a", "b", "c"}
        assert anchors[0].blank_answer_cell is None

    def test_finds_cis_table_without_parts(self):
        doc = docx.Document()
        _add_cis_table(doc, "AC-3", with_parts=False)
        anchors = find_control_tables(doc)
        assert len(anchors) == 1
        assert anchors[0].part_rows == {}
        assert anchors[0].blank_answer_cell is not None

    def test_ignores_non_cis_tables(self):
        doc = docx.Document()
        plain = doc.add_table(rows=2, cols=2)
        plain.rows[0].cells[0].text = "Header"
        plain.rows[1].cells[0].text = "Data"
        assert find_control_tables(doc) == []

    def test_summarize_anchors(self):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=True)
        _add_cis_table(doc, "AC-3", with_parts=False)
        anchors = find_control_tables(doc)
        summary = summarize_anchors(anchors)
        assert summary["total"] == 2
        assert summary["with_part_rows"] == 1
        assert summary["with_blank_answer_only"] == 1
        assert summary["max_parts"] == 3


class TestSyntheticCheckboxToggle:
    def test_toggles_matching_status_line_only(self):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=False)
        anchor = find_control_tables(doc)[0]
        _set_checkbox_lines(
            anchor.status_cell.paragraphs, {"planned"}, STATUS_KEY_PATTERNS
        )
        texts = [p.text for p in anchor.status_cell.paragraphs]
        assert texts[1].startswith(UNCHECKED_GLYPH)  # Implemented
        assert texts[3].startswith(CHECKED_GLYPH)  # Planned
        assert texts[2].startswith(UNCHECKED_GLYPH)  # Partially Implemented
        assert texts[4].startswith(UNCHECKED_GLYPH)  # Alternative implementation

    def test_toggles_multiple_origination_lines(self):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=False)
        anchor = find_control_tables(doc)[0]
        _set_checkbox_lines(
            anchor.origination_cell.paragraphs,
            {"inherited", "system_specific"},
            ORIGINATION_KEY_PATTERNS,
        )
        texts = [p.text for p in anchor.origination_cell.paragraphs]
        assert texts[1].startswith(CHECKED_GLYPH)  # Inherited
        assert texts[2].startswith(UNCHECKED_GLYPH)  # Hybrid
        assert texts[3].startswith(CHECKED_GLYPH)  # System Specific

    def test_toggling_off_when_no_targets(self):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=False)
        anchor = find_control_tables(doc)[0]
        _set_checkbox_lines(
            anchor.status_cell.paragraphs, {"implemented"}, STATUS_KEY_PATTERNS
        )
        _set_checkbox_lines(anchor.status_cell.paragraphs, set(), STATUS_KEY_PATTERNS)
        assert all(
            p.text.startswith(UNCHECKED_GLYPH)
            for p in anchor.status_cell.paragraphs[1:]
        )


class TestMatchKey:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("AC-2(1)", "AC-02(01)"),
            ("ac-2", "AC-02"),
            ("AC-2", "AC-2"),
            ("SC-7(4)", "SC-07(04)"),
        ],
    )
    def test_zero_padding_insensitive_equal(self, a, b):
        assert _match_key(a) == _match_key(b)

    def test_different_controls_not_equal(self):
        assert _match_key("AC-2") != _match_key("AC-3")

    def test_none_input(self):
        assert _match_key(None) is None
        assert _match_key("") is None


class TestSyntheticFillAndFismaticReadback:
    """End-to-end: fill a synthetic CIS table, then read it back with
    fismatic's *unmodified* read path (parse_control_table +
    extract_implementation_details) and assert exact agreement. This is the
    same acceptance shape as test_docx_generator.py's round-trip test, scoped
    to the structural (in-place) filler instead of the from-scratch one.
    """

    def test_status_and_origination_roundtrip(self, tmp_path):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=False)
        control = CanonicalControl.from_raw(
            "AC-1",
            raw_status="partially-implemented",
            raw_origination=["hybrid"],
            statement_ids=[],
        )
        out = tmp_path / "filled.docx"
        result = fill_control_tables(doc, [control], out)
        assert result.filled_control_ids == ["AC-1"]
        assert result.unmatched_control_ids == []

        fp = _fismatic_parser()
        reloaded = docx.Document(str(out))
        ok, cid = fp.is_control_table(reloaded.tables[0])
        assert ok and cid == "AC-1"
        data = fp.parse_control_table(reloaded.tables[0])
        assert data["implementation_status"] == "partially-implemented"
        assert data["control_origination"] == ["hybrid"]

    @pytest.mark.parametrize("marker", ["_smt.", "_stmt."])
    def test_narrative_parts_roundtrip(self, tmp_path, marker):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=True)
        control = CanonicalControl.from_raw(
            "AC-1",
            raw_status="implemented",
            raw_origination=[],
            statement_ids=[f"ac-1{marker}a", f"ac-1{marker}b", f"ac-1{marker}c"],
        )
        statement_text = {
            "AC-1": {
                f"ac-1{marker}a": "Policy text.",
                f"ac-1{marker}b": "Procedure text.",
                f"ac-1{marker}c": "Review text.",
            }
        }
        out = tmp_path / "filled.docx"
        fill_control_tables(
            doc, [control], out, statement_text_by_control=statement_text
        )

        fp = _fismatic_parser()
        reloaded = docx.Document(str(out))
        data = fp.parse_control_table(reloaded.tables[0])
        details = fp.extract_implementation_details(data)
        assert details["implementation"] == {
            "a": "Policy text.",
            "b": "Procedure text.",
            "c": "Review text.",
        }

    def test_no_part_rows_uses_blank_answer_cell(self, tmp_path):
        doc = docx.Document()
        _add_cis_table(doc, "AC-3", with_parts=False)
        control = CanonicalControl.from_raw(
            "AC-3",
            raw_status="implemented",
            raw_origination=[],
            statement_ids=["ac-3_stmt.a"],
        )
        statement_text = {"AC-3": {"ac-3_stmt.a": "Single blob of narrative text."}}
        out = tmp_path / "filled.docx"
        fill_control_tables(
            doc, [control], out, statement_text_by_control=statement_text
        )
        reloaded = docx.Document(str(out))
        answer_row = reloaded.tables[0].rows[4]
        assert "Single blob of narrative text." in answer_row.cells[0].text

    def test_unmatched_control_reported(self, tmp_path):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=False)
        control = CanonicalControl.from_raw(
            "ZZ-99", raw_status="implemented", raw_origination=[], statement_ids=[]
        )
        out = tmp_path / "filled.docx"
        result = fill_control_tables(doc, [control], out)
        assert result.unmatched_control_ids == ["ZZ-99"]
        assert result.unmatched_table_ids == ["AC-1"]

    def test_dotted_enhancement_matches_only_enhancement_table(self, tmp_path):
        doc = docx.Document()
        _add_cis_table(doc, "AC-02", with_parts=False)
        _add_cis_table(doc, "AC-02(01)", with_parts=False)
        control = CanonicalControl.from_raw(
            "ac-2.1",
            raw_status="implemented",
            raw_origination=["system-specific"],
            statement_ids=["ac-2.1_stmt.a"],
        )
        out = tmp_path / "filled.docx"

        result = fill_control_tables(
            doc,
            [control],
            out,
            statement_text_by_control={
                "ac-2.1": {"ac-2.1_stmt.a": "Enhancement narrative."}
            },
        )

        assert result.filled_table_count == 1
        assert result.control_results[0]["normalized_control_id"] == "AC-2(1)"
        assert result.control_results[0]["target_tables"][0]["raw_control_id"] == (
            "AC-02(01)"
        )
        reloaded = docx.Document(str(out))

        def table_text(table):
            return "\n".join(cell.text for row in table.rows for cell in row.cells)

        assert "Enhancement narrative." not in table_text(reloaded.tables[0])
        assert "Enhancement narrative." in table_text(reloaded.tables[1])

    def test_normalization_collision_fails_before_output(self, tmp_path):
        doc = docx.Document()
        _add_cis_table(doc, "AC-02(01)", with_parts=False)
        controls = [
            CanonicalControl.from_raw(
                value, raw_status="implemented", raw_origination=[]
            )
            for value in ("AC-2(1)", "ac-2.1")
        ]
        out = tmp_path / "filled.docx"

        with pytest.raises(ValueError, match="normalization collisions"):
            fill_control_tables(doc, controls, out)

        assert not out.exists()

    def test_duplicate_target_anchors_are_all_filled_and_reported(self, tmp_path):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=False)
        _add_cis_table(doc, "AC-1", with_parts=False)
        control = CanonicalControl.from_raw(
            "AC-1", raw_status="implemented", raw_origination=["inherited"]
        )
        out = tmp_path / "filled.docx"
        result = fill_control_tables(doc, [control], out)

        assert result.filled_control_ids == ["AC-1"]
        assert result.filled_table_count == 2
        assert result.duplicate_table_ids == ["AC-1"]
        assert result.unmatched_table_ids == []

        reloaded = docx.Document(str(out))
        for table in reloaded.tables:
            assert "☒ Implemented" in table.rows[1].cells[0].text
            assert "☒ Inherited" in table.rows[2].cells[0].text


# ---------------------------------------------------------------------------
# Fixture-gated: real committed jinja templates
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _LOW_JINJA.exists() or not _MOD_JINJA.exists(),
    reason="committed Low/Moderate jinja templates absent",
)
def test_gsa_pages_template_part_anchors_are_unambiguous():
    """Prevent reintroducing the GSA Pages RA-03 / IR-04 mapping defects."""
    moderate = find_control_tables(_MOD_JINJA)
    ra_03 = [anchor for anchor in moderate if anchor.control_id == "RA-03"]
    ra_03_01 = [anchor for anchor in moderate if anchor.control_id == "RA-03(01)"]
    assert len(ra_03) == 1
    assert len(ra_03_01) == 1
    assert set(ra_03[0].part_rows) == {"a", "b", "c", "d", "e", "f"}
    assert set(ra_03_01[0].part_rows) == {"a", "b"}

    low = find_control_tables(_LOW_JINJA)
    ir_04 = next(anchor for anchor in low if anchor.control_id == "IR-04")
    assert set(ir_04.part_rows) == {"a", "b", "c", "d"}


@pytest.mark.slow
@pytest.mark.skipif(
    not _LOW_JINJA.exists(), reason="committed Low jinja template absent"
)
def test_low_template_full_control_table_roundtrip(tmp_path):
    """Fill every CIS table in the committed Low template with synthesized
    per-control status/origination combos (cycling through checkbox options
    that are actually present in this template — see
    ``control_checkbox_vocab.py``'s module docstring on why the label *set*
    varies per official template) and assert every one round-trips exactly
    through fismatic's unmodified read path.
    """
    fp = _fismatic_parser()
    anchors = find_control_tables(_LOW_JINJA)
    assert len(anchors) >= 150  # corpus baseline per docs/PARSING_ACCURACY_NOTES.md

    statuses = [
        "implemented",
        "partially-implemented",
        "planned",
        "alternative-implementation",
        "not-applicable",
    ]
    originations = [
        ["inherited"],
        ["system-specific"],
        ["hybrid"],
        ["inherited", "system-specific"],
        [],
    ]
    controls = []
    expected = {}
    dupe_ids = set()
    seen_norm = set()
    for i, a in enumerate(anchors):
        norm = fp.normalize_control_id(a.control_id)
        if norm in seen_norm:
            dupe_ids.add(norm)
        seen_norm.add(norm)
        status = statuses[i % len(statuses)]
        origination = originations[i % len(originations)]
        controls.append(
            CanonicalControl.from_raw(
                a.control_id,
                raw_status=status,
                raw_origination=origination,
                statement_ids=[],
            )
        )
        expected[norm] = (status, sorted(origination))

    out = tmp_path / "low.filled.docx"
    result = fill_control_tables(_LOW_JINJA, controls, out)
    assert result.unmatched_control_ids == []
    assert result.unmatched_table_ids == []

    doc = docx.Document(str(out))
    seen = set()
    mismatches = []
    for t in doc.tables:
        ok, cid = fp.is_control_table(t)
        if not ok or cid not in expected:
            continue
        seen.add(cid)
        if cid in dupe_ids:
            # A control_id shared by two distinct CIS tables (e.g. a base
            # control + its own differently-keyed duplicate heading) can't
            # be disambiguated by control_id alone; skip strict comparison
            # for these known corpus duplicates (see
            # docs/PARSING_ACCURACY_NOTES.md and the fidelity evaluation).
            continue
        data = fp.parse_control_table(t)
        exp_status, exp_origin = expected[cid]
        got_status = data["implementation_status"]
        got_origin = sorted(data["control_origination"])
        # "service-provider-corporate" is a known pre-existing false-positive
        # in fismatic's OWN read regex (unrelated to this fill code): its
        # origination pattern is a bare substring search for "common control"
        # anywhere in the line, and the Low template's own placeholder text
        # for "inherited" is "Inherited from the Common Control Catalog
        # (CCC)", which contains that substring regardless of what this
        # module writes. Tolerate it here; it's a fismatic.parser
        # follow-up, not a control_table_fill regression.
        got_origin_without_corp_fp = [
            v for v in got_origin if v != "service-provider-corporate"
        ]
        if got_status != exp_status:
            mismatches.append((cid, "status", exp_status, got_status))
        elif got_origin_without_corp_fp != exp_origin:
            mismatches.append((cid, "origination", exp_origin, got_origin))

    assert seen == set(expected), f"missing from readback: {set(expected) - seen}"
    assert not mismatches, f"{len(mismatches)} mismatches: {mismatches[:10]}"


@pytest.mark.slow
@pytest.mark.skipif(
    not _MOD_JINJA.exists(), reason="committed Moderate jinja template absent"
)
def test_moderate_template_control_tables_detected(tmp_path):
    """Lighter-weight check for Moderate/High (full per-table readback is
    covered by the Low test above; this asserts detection coverage matches
    the corpus baseline and that a fill+render_check-style save succeeds).
    """
    anchors = find_control_tables(_MOD_JINJA)
    assert len(anchors) >= 320  # corpus baseline per docs/PARSING_ACCURACY_NOTES.md

    controls = [
        CanonicalControl.from_raw(
            a.control_id,
            raw_status="implemented",
            raw_origination=["inherited"],
            statement_ids=[],
        )
        for a in anchors
    ]
    out = tmp_path / "moderate.filled.docx"
    result = fill_control_tables(_MOD_JINJA, controls, out)
    assert len(result.filled_control_ids) == len(anchors)
    assert out.exists()


@pytest.mark.slow
@pytest.mark.skipif(
    not _HIGH_JINJA.exists(), reason="committed High jinja template absent"
)
def test_high_template_control_tables_detected(tmp_path):
    anchors = find_control_tables(_HIGH_JINJA)
    assert len(anchors) >= 400  # corpus baseline per docs/PARSING_ACCURACY_NOTES.md

    controls = [
        CanonicalControl.from_raw(
            a.control_id,
            raw_status="implemented",
            raw_origination=["inherited"],
            statement_ids=[],
        )
        for a in anchors
    ]
    out = tmp_path / "high.filled.docx"
    result = fill_control_tables(_HIGH_JINJA, controls, out)
    assert len(result.filled_control_ids) == len(anchors)
    assert out.exists()


class TestWholeControlNarrative:
    """A control with no statements keeps its narrative in requirement-level
    ``by-components``; it must still reach the DOCX control table."""

    def _ssp(self, *requirements: dict) -> dict:
        return {
            "system-security-plan": {
                "control-implementation": {
                    "implemented-requirements": list(requirements)
                }
            }
        }

    def _req(self, control_id: str, text: str, statements: list | None = None) -> dict:
        req = {
            "control-id": control_id,
            "by-components": [{"description": text}],
            "props": [{"name": "implementation-status", "value": "implemented"}],
        }
        if statements is not None:
            req["statements"] = statements
        return req

    def test_map_includes_only_statementless_requirements(self):
        from compliance.lib.oscal_source import whole_control_text_map_from_oscal_ssp

        ssp = self._ssp(
            self._req("ia-6", "Not applicable."),
            self._req(
                "ac-1",
                "Requirement-level text.",
                [{"statement-id": "ac-1_smt.a", "description": "Part text."}],
            ),
            self._req("ia-7", "   "),
        )
        assert whole_control_text_map_from_oscal_ssp(ssp) == {"ia-6": "Not applicable."}

    def test_text_is_written_to_the_blank_answer_cell(self, tmp_path):
        doc = docx.Document()
        _add_cis_table(doc, "IA-06", with_parts=False)
        control = CanonicalControl.from_raw(
            "ia-6", raw_status="implemented", raw_origination=[], statement_ids=[]
        )
        out = tmp_path / "filled.docx"
        result = fill_control_tables(
            doc,
            [control],
            out,
            whole_control_text_by_control={"ia-6": "Not applicable here."},
        )
        assert (
            "Not applicable here."
            in docx.Document(str(out)).tables[0].rows[4].cells[0].text
        )
        assert result.control_results[0]["whole_control"] == {
            "placement": "blank-answer",
            "written": True,
        }

    def test_part_row_table_is_reported_not_guessed(self, tmp_path):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=True)
        control = CanonicalControl.from_raw(
            "ac-1", raw_status="implemented", raw_origination=[], statement_ids=[]
        )
        out = tmp_path / "filled.docx"
        result = fill_control_tables(
            doc, [control], out, whole_control_text_by_control={"ac-1": "Orphan text."}
        )
        assert result.control_results[0]["whole_control"] == {
            "placement": "unmatched",
            "written": False,
        }
        assert "Orphan text." not in " ".join(
            cell.text
            for row in docx.Document(str(out)).tables[0].rows
            for cell in row.cells
        )

    def test_controls_with_statements_ignore_the_whole_control_map(self, tmp_path):
        doc = docx.Document()
        _add_cis_table(doc, "AC-1", with_parts=True)
        control = CanonicalControl.from_raw(
            "ac-1",
            raw_status="implemented",
            raw_origination=[],
            statement_ids=["ac-1_smt.a"],
        )
        out = tmp_path / "filled.docx"
        result = fill_control_tables(
            doc,
            [control],
            out,
            statement_text_by_control={"ac-1": {"ac-1_smt.a": "Part text."}},
            whole_control_text_by_control={"ac-1": "Should not be used."},
        )
        assert result.control_results[0]["whole_control"] is None

    def test_default_behaviour_is_unchanged(self, tmp_path):
        doc = docx.Document()
        _add_cis_table(doc, "IA-06", with_parts=False)
        control = CanonicalControl.from_raw(
            "ia-6", raw_status="implemented", raw_origination=[], statement_ids=[]
        )
        out = tmp_path / "filled.docx"
        result = fill_control_tables(doc, [control], out)
        assert result.control_results[0]["whole_control"] is None
        assert docx.Document(str(out)).tables[0].rows[4].cells[0].text.strip() == ""

    def test_end_to_end_from_oscal(self, tmp_path):
        import json

        from compliance.scripts.fill_ssp_from_oscal import build_from_oscal

        template = tmp_path / "t.docx"
        doc = docx.Document()
        _add_cis_table(doc, "IA-06", with_parts=False)
        doc.save(str(template))
        oscal = tmp_path / "ssp.json"
        oscal.write_text(json.dumps(self._ssp(self._req("ia-6", "Whole narrative."))))
        out, manifest = tmp_path / "o.docx", tmp_path / "m.json"
        result = build_from_oscal(oscal, template, out, manifest)
        assert (
            "Whole narrative."
            in docx.Document(str(out)).tables[0].rows[4].cells[0].text
        )
        assert result["controls"][0]["whole_control"]["written"] is True
