"""Render-time Markdown formatting for DOCX narrative cells (synthetic fixtures only)."""

from __future__ import annotations

import re

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from compliance.lib import docx_narrative_format as nf

HEADERS = re.compile(r"(?:Alpha Team|Beta Team):")


def _cell(text: str):
    doc = Document()
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = text
    return doc, cell


def _fmt(run):
    return bool(run.bold), bool(run.underline)


def test_headers_are_bold_underlined_own_paragraphs_with_spacer_before_later_ones():
    _, cell = _cell("Alpha Team: First body. Beta Team: Second body.")
    assert nf.format_cell(cell, HEADERS)
    assert [p.text for p in cell.paragraphs] == [
        "Alpha Team:",
        "First body.",
        "Beta Team:",
        "Second body.",
    ]
    assert _fmt(cell.paragraphs[0].runs[0]) == (True, True)
    assert _fmt(cell.paragraphs[1].runs[0]) == (False, False)
    assert cell.paragraphs[2].paragraph_format.space_before is not None
    assert cell.paragraphs[0].paragraph_format.space_before is None
    assert cell.paragraphs[0].paragraph_format.keep_with_next


def test_repeated_header_is_dropped():
    _, cell = _cell("Alpha Team: Alpha Team: Same body.")
    nf.format_cell(cell, HEADERS)
    assert [p.text for p in cell.paragraphs] == ["Alpha Team:", "Same body."]


def test_repeated_header_with_body_is_kept_because_it_carries_prose():
    _, cell = _cell("Alpha Team: One. Alpha Team: One.")
    nf.format_cell(cell, HEADERS)
    assert [p.text for p in cell.paragraphs] == [
        "Alpha Team:",
        "One.",
        "Alpha Team:",
        "One.",
    ]


def test_ordered_and_bullet_lists_become_real_word_lists_and_restart():
    _, cell = _cell("Intro.\n1. one\n2. two\n\nMiddle.\n1. again\n- dot\n  - nested")
    nf.format_cell(cell, None)
    paras = cell.paragraphs

    def num(p):
        pr = p._p.pPr
        if pr is None or pr.numPr is None:
            return None
        return pr.numPr.numId.val, pr.numPr.ilvl.val

    assert [p.text for p in paras if num(p)] == ["one", "two", "again", "dot", "nested"]
    assert (
        num(paras[1]) == num(paras[2])[0:1] + (0,)
        or num(paras[1])[0] == num(paras[2])[0]
    )
    again = next(p for p in paras if p.text == "again")
    assert num(again)[0] != num(paras[1])[0]
    dot = next(p for p in paras if p.text == "dot")
    nested = next(p for p in paras if p.text == "nested")
    assert num(nested) == (num(dot)[0], 1)
    assert not any(re.match(r"^\s*(\d+\.|-)\s", p.text) for p in paras)


def test_numbering_definitions_are_created_in_the_document():
    doc, cell = _cell("1. a\n2. b")
    nf.format_cell(cell, None)
    root = doc.part.numbering_part.element
    num_id = cell.paragraphs[0]._p.pPr.numPr.numId.val
    assert any(int(n.get(qn("w:numId"))) == num_id for n in root.findall(qn("w:num")))


def test_bold_and_http_links_render_and_other_markup_stays_literal():
    _, cell = _cell(
        "Use **bold** and [site](https://example.com/x). Not a link [x](javascript:alert(1)) "
        "and **unclosed."
    )
    nf.format_cell(cell, None)
    para = cell.paragraphs[0]
    assert any(r.bold and r.text == "bold" for r in para.runs)
    links = para._p.findall(qn("w:hyperlink"))
    assert len(links) == 1
    rel = para.part.rels[links[0].get(qn("r:id"))]
    assert rel.target_ref == "https://example.com/x" and rel.is_external
    assert "[x](javascript:alert(1))" in para.text and "**unclosed." in para.text


def test_plain_prose_is_untouched_and_unmatched_cells_report_false():
    _, cell = _cell("Plain implementation prose.")
    assert not nf.format_cell(cell, HEADERS)
    assert cell.text == "Plain implementation prose."


def test_inline_hyphen_and_number_text_is_not_a_list():
    _, cell = _cell("Rotate keys every 90 days - not 30. See item 2. for detail.")
    assert not nf.format_cell(cell, None)


def test_table_page_breaks_removed_but_body_breaks_kept():
    doc = Document()
    body_p = doc.add_paragraph("Heading")
    body_p.paragraph_format.page_break_before = True
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell.paragraphs[0].paragraph_format.page_break_before = True
    assert nf.strip_table_page_breaks(doc) == 1
    assert body_p.paragraph_format.page_break_before is True
    assert not cell.paragraphs[0].paragraph_format.page_break_before


def test_second_pass_leaves_formatted_lists_alone():
    _, cell = _cell("Alpha Team: Body\n1. one\n2. two")
    nf.format_cell(cell, HEADERS)
    before = [p.text for p in cell.paragraphs]
    assert not nf.format_cell(cell, HEADERS)
    assert [p.text for p in cell.paragraphs] == before


def test_hard_wrapped_lines_join_into_one_paragraph_and_blank_line_splits():
    _, cell = _cell(
        "First sentence wraps\nonto a second line.\n\nNew paragraph.\n1. item wraps\nacross lines"
    )
    nf.format_cell(cell, None)
    assert [p.text for p in cell.paragraphs] == [
        "First sentence wraps onto a second line.",
        "New paragraph.",
        "item wraps across lines",
    ]
    assert not any(not p.text for p in cell.paragraphs)


def _control_cell(doc, *paragraphs):
    table = doc.add_table(rows=2, cols=1)
    table.cell(0, 0).text = "AC-02"
    cell = table.cell(1, 0)
    cell.paragraphs[0].text = paragraphs[0]
    for text in paragraphs[1:]:
        cell.add_paragraph(text)
    return cell


def test_audit_flags_unrendered_markdown_and_page_breaks_then_passes_after_formatting():
    doc = Document()
    cell = _control_cell(
        doc,
        "Alpha Team: Use **bold** and [x](https://example.com).",
        "1. one",
        "2. two",
    )
    cell.paragraphs[0].paragraph_format.page_break_before = True
    dirty = nf.audit_document(doc, HEADERS)
    assert (
        dirty["literal_bold"] and dirty["literal_links"] and dirty["table_page_breaks"]
    )
    assert dirty["literal_list_lines"]
    nf.format_document(doc, HEADERS)
    nf.strip_table_page_breaks(doc)
    assert not any(nf.audit_document(doc, HEADERS).values())


def test_audit_ignores_non_control_tables():
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Reference **text**"
    assert not any(nf.audit_document(doc, HEADERS).values())


def test_audit_flags_plain_header():
    doc = Document()
    _control_cell(doc, "Alpha Team:")
    assert nf.audit_document(doc, HEADERS)["headers_not_bold_underlined"] == 1


def test_audit_table_pattern_is_caller_supplied():
    doc = Document()
    table = doc.add_table(rows=2, cols=1)
    table.cell(0, 0).text = "Section 9"
    table.cell(1, 0).text = "Use **bold**."
    assert not any(nf.audit_document(doc, None).values())
    custom = re.compile(r"^Section \d+")
    assert nf.audit_document(doc, None, table_re=custom)["literal_bold"] == 1


def test_format_document_renders_bold_in_cell_with_native_hyperlink() -> None:
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.text = "Native link stays; **bold** is rendered."
    hyperlink = OxmlElement("w:hyperlink")
    cell.paragraphs[0]._p.append(hyperlink)

    assert nf.format_document(doc, None) == 1
    assert "**" not in cell.text
    assert any(run.bold for run in cell.paragraphs[0].runs if run.text == "bold")
    assert cell._tc.findall(".//" + qn("w:hyperlink"))


def test_format_document_renders_bold_in_nested_table() -> None:
    doc = Document()
    outer = doc.add_table(rows=1, cols=1)
    nested = outer.cell(0, 0).add_table(rows=1, cols=1)
    cell = nested.cell(0, 0)
    cell.text = "Nested **bold** prose."

    assert nf.format_document(doc, None) == 1
    assert "**" not in cell.text
    assert any(run.bold for run in cell.paragraphs[0].runs if run.text == "bold")


def test_no_system_vocabulary_in_module():
    source = nf.__file__
    text = open(source, encoding="utf-8").read()
    assert "GSA Pages" not in text and "Tenant" not in text
