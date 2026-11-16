"""Source-free regression shapes for the PR #79 generic-preservation audit."""

import docx
import pytest
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from compliance.lib.canonical_model import CanonicalControl
from compliance.lib.control_checkbox_vocab import STATUS_KEY_PATTERNS
from compliance.lib.control_table_fill import _set_checkbox_lines, fill_control_tables
from compliance.tests.test_control_table_fill import _add_cis_table


@pytest.mark.parametrize(
    "old,new",
    [("TLS 1.2", "TLS 1,2"), ("Role A", "role a"), ("café access", "caf access")],
)
def test_changed_prose_is_written_even_when_lossy_tokens_match(tmp_path, old, new):
    document = docx.Document()
    table = _add_cis_table(document, "AC-1", with_parts=True)
    table.rows[4].cells[1].text = old
    control = CanonicalControl.from_raw(
        "AC-1",
        raw_status="implemented",
        raw_origination=["system-specific"],
        statement_ids=["ac-1_smt.a"],
    )
    output = tmp_path / "filled.docx"
    fill_control_tables(
        document,
        [control],
        output,
        statement_text_by_control={"AC-1": {"ac-1_smt.a": new}},
    )
    assert docx.Document(output).tables[0].rows[4].cells[1].text == new


def wrap_runs(paragraph, semantic=False):
    wrapper = OxmlElement("w:sdt")
    if semantic:
        props = OxmlElement("w:sdtPr")
        binding = OxmlElement("w:dataBinding")
        props.append(binding)
        wrapper.append(props)
    content = OxmlElement("w:sdtContent")
    for child in list(paragraph._p):
        if child.tag == qn("w:r"):
            content.append(child)
    wrapper.append(content)
    paragraph._p.append(wrapper)
    return wrapper


@pytest.mark.parametrize(
    "checked,targets,expected",
    [(False, ["implemented"], "☒"), (True, [], "☐"), (True, ["implemented"], "☒")],
)
def test_wrapped_checkbox_label_and_state_are_read_from_xml(
    tmp_path, checked, targets, expected
):
    document = docx.Document()
    paragraph = document.add_paragraph(("☒" if checked else "☐") + " Implemented")
    wrapper = wrap_runs(paragraph)
    assert paragraph.text == ""
    _set_checkbox_lines([paragraph], targets, STATUS_KEY_PATTERNS)
    assert "".join(paragraph._p.itertext()).find(expected) >= 0
    if checked and targets:
        assert wrapper.getparent() is not None
    output = tmp_path / "checkbox.docx"
    document.save(output)
    reloaded = docx.Document(output).paragraphs[0]
    assert expected in "".join(t.text or "" for t in reloaded._p.iter(qn("w:t")))


def test_checkbox_toggle_preserves_unrelated_and_semantic_wrappers(tmp_path):
    document = docx.Document()
    paragraph = document.add_paragraph("☐ Implemented")
    semantic = wrap_runs(paragraph, semantic=True)
    unrelated = OxmlElement("w:sdt")
    content = OxmlElement("w:sdtContent")
    unrelated.append(content)
    paragraph._p.append(unrelated)
    _set_checkbox_lines([paragraph], ["implemented"], STATUS_KEY_PATTERNS)
    assert semantic.getparent() is not None
    assert unrelated.getparent() is not None


@pytest.mark.parametrize("part", ["a", "b", "c", "1"])
def test_merged_standard_named_parts_keep_identity(tmp_path, part):
    from fismatic.parser import extract_implementation_details

    document = docx.Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "AC-1"
    table.cell(0, 1).text = "Control Summary Information"
    narrative = f"Part {part}: Synthetic merged narrative with sufficient length to exercise named routing."
    table.cell(1, 0).merge(table.cell(1, 1)).text = narrative
    result = extract_implementation_details(
        {"table": table, "is_control_table": True, "control_id": "AC-1"}
    )
    assert result == {"implementation": {part: narrative}}


def test_equivalent_whitespace_keeps_existing_numbered_paragraph_xml(tmp_path):
    document = docx.Document()
    table = _add_cis_table(document, "AC-1", with_parts=True)
    cell = table.rows[4].cells[1]
    cell.text = "Exact Unicode café statement."
    properties = cell.paragraphs[0]._p.get_or_add_pPr()
    properties.append(OxmlElement("w:numPr"))
    before = cell._tc.xml
    control = CanonicalControl.from_raw(
        "AC-1",
        raw_status="implemented",
        raw_origination=["system-specific"],
        statement_ids=["ac-1_smt.a"],
    )
    fill_control_tables(
        document,
        [control],
        tmp_path / "filled.docx",
        statement_text_by_control={
            "AC-1": {"ac-1_smt.a": "Exact Unicode   café statement."}
        },
    )
    assert cell._tc.xml == before


def test_normalized_collision_does_not_overwrite_first_observed_value():
    from compliance.lib.control_table_fill import _normalize_statement_map

    values, collision = _normalize_statement_map(
        "AC-2(1)", {"ac-02.01_stmt.a": "first", "AC-2(1)_smt.a": "second"}
    )
    assert collision
    assert values == {"AC-2(1)_smt.a": "first"}
