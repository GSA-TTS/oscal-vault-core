"""Render Markdown-style narrative text in filled DOCX table cells as Word formatting.

Narrative stays plain Markdown in the vault and OSCAL. This module only runs at
render time, after the template has been filled. It is deliberately generic: the
caller supplies the pattern that recognises block headers, so no system-specific
label lives here.

Public API: ``format_document``, ``format_cell``, ``strip_table_page_breaks`` and
``audit_document``. ``audit_document`` returns counts a consumer can fail CI on.

Supported syntax, anything else is left as literal text:
* headers recognised by the caller's pattern: bold and underlined, own paragraph
* ``**bold**`` spans
* ``[text](http(s)://url)`` links, as real hyperlinks
* ``1.`` / ``1)`` ordered and ``-`` / ``*`` bullet items (two or more leading
  spaces nests one level), as real Word lists restarted for each list
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Pt
from docx.table import Table, _Cell

_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_LIST_ITEM_RE = re.compile(r"^(\s*)(\d+[.)]|[-*\u2022])\s+(\S.*)$")
_SPACE_BEFORE = Pt(6)
_HANGING_TWIPS = 360


@dataclass(frozen=True)
class Line:
    kind: str  # header | body | ordered | bullet | blank (parse boundary only)
    text: str
    level: int = 0


def _classify(raw: str) -> Line:
    if not raw.strip():
        return Line("blank", "")
    match = _LIST_ITEM_RE.match(raw)
    if not match:
        return Line("body", raw.strip())
    indent, marker, body = match.groups()
    kind = "ordered" if marker[0].isdigit() else "bullet"
    return Line(kind, body.strip(), 1 if len(indent.replace("\t", "  ")) >= 2 else 0)


def _header_blocks(
    text: str, header_re: re.Pattern[str] | None
) -> list[tuple[bool, str]]:
    matches = list(header_re.finditer(text)) if header_re else []
    if not matches:
        return [(False, text)]
    blocks: list[tuple[bool, str]] = []
    prefix = text[: matches[0].start()].strip()
    if prefix:
        blocks.append((False, prefix))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((True, match.group(0).strip()))
        body = text[match.end() : end].strip()
        if body:
            blocks.append((False, body))
    return blocks


def _drop_repeated_headers(blocks: list[tuple[bool, str]]) -> list[tuple[bool, str]]:
    """Drop a header that is immediately followed by the same header (no body between).

    Repeated header-and-body pairs carry prose and are left for the vault owner to resolve.
    """
    kept: list[tuple[bool, str]] = []
    for index, block in enumerate(blocks):
        nxt = blocks[index + 1] if index + 1 < len(blocks) else None
        if block[0] and nxt and nxt[0] and nxt[1].casefold() == block[1].casefold():
            continue
        kept.append(block)
    return kept


def _is_hard_break(raw: str) -> bool:
    """A line that starts a new paragraph: indented code-like text is not treated as a soft wrap."""
    return raw.startswith(("\t", "    "))


def parse_narrative(text: str, header_re: re.Pattern[str] | None) -> list[Line]:
    lines: list[Line] = []
    boundary = True
    for is_header, block in _drop_repeated_headers(_header_blocks(text, header_re)):
        if is_header:
            lines.append(Line("header", block))
            boundary = True
            continue
        for raw in block.splitlines() or [""]:
            line = _classify(raw)
            if line.kind == "blank":
                boundary = True
                continue
            if (
                line.kind == "body"
                and lines
                and not boundary
                and lines[-1].kind in ("body", "ordered", "bullet")
                and not _is_hard_break(raw)
            ):
                prev = lines[-1]
                lines[-1] = Line(prev.kind, f"{prev.text} {line.text}", prev.level)
                continue
            boundary = False
            lines.append(line)
    return lines


def _needs_formatting(text: str, header_re: re.Pattern[str] | None) -> bool:
    if header_re and header_re.search(text):
        return True
    if "**" in text or _INLINE_RE.search(text):
        return True
    return any(_LIST_ITEM_RE.match(row) for row in text.splitlines())


def _add_run(paragraph: Any, text: str, bold: bool, underline: bool) -> None:
    run = paragraph.add_run(text)
    run.bold = bold
    if underline:
        run.underline = True


def _add_hyperlink(paragraph: Any, text: str, url: str, bold: bool) -> None:
    rel_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    if bold:
        props.append(OxmlElement("w:b"))
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    props.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(underline)
    run.append(props)
    node = OxmlElement("w:t")
    node.text = text
    node.set(qn("xml:space"), "preserve")
    run.append(node)
    link.append(run)
    paragraph._p.append(link)


def add_inline(paragraph: Any, text: str, header: bool = False) -> None:
    cursor = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > cursor:
            _add_run(paragraph, text[cursor : match.start()], header, header)
        if match.group(1) is not None:
            _add_run(paragraph, match.group(1), True, header)
        else:
            _add_hyperlink(paragraph, match.group(2), match.group(3), header)
        cursor = match.end()
    if cursor < len(text) or not paragraph.runs:
        _add_run(paragraph, text[cursor:], header, header)


_LEVELS = {
    "ordered": (("decimal", "%1."), ("lowerLetter", "%2.")),
    "bullet": (("bullet", "\u2022"), ("bullet", "\u2013")),
}


def _new_list(numbering_part: Any, kind: str) -> int:
    """Append a fresh abstract numbering + instance so each list restarts at 1."""
    root = numbering_part.element
    abstract_ids = [
        int(n.get(qn("w:abstractNumId"))) for n in root.findall(qn("w:abstractNum"))
    ]
    num_ids = [int(n.get(qn("w:numId"))) for n in root.findall(qn("w:num"))]
    abstract_id, num_id = max(abstract_ids, default=-1) + 1, max(num_ids, default=0) + 1
    levels = "".join(
        f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
        f'<w:pPr><w:ind w:left="{_HANGING_TWIPS * (i + 1)}" w:hanging="{_HANGING_TWIPS}"/></w:pPr>'
        "</w:lvl>"
        for i, (fmt, text) in enumerate(_LEVELS[kind])
    )
    abstract = parse_xml(
        f'<w:abstractNum {nsdecls("w")} w:abstractNumId="{abstract_id}">'
        f'<w:multiLevelType w:val="hybridMultilevel"/>{levels}</w:abstractNum>'
    )
    first_num = root.find(qn("w:num"))
    if first_num is not None:
        first_num.addprevious(abstract)
    else:
        root.append(abstract)
    root.append(
        parse_xml(
            f'<w:num {nsdecls("w")} w:numId="{num_id}">'
            f'<w:abstractNumId w:val="{abstract_id}"/></w:num>'
        )
    )
    return num_id


def _apply_list(paragraph: Any, num_id: int, level: int) -> None:
    num_pr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    num_pr.get_or_add_ilvl().val = level
    num_pr.get_or_add_numId().val = num_id


def _cell_text(cell: Any) -> str:
    """Join paragraphs with a blank line so wrapped lines and paragraph breaks stay distinct."""
    return "\n\n".join(p.text for p in cell.paragraphs)


def _already_formatted(cell: Any) -> bool:
    tc = cell._tc
    return bool(
        tc.findall(".//" + qn("w:numPr")) or tc.findall(".//" + qn("w:hyperlink"))
    )


def format_cell(cell: Any, header_re: re.Pattern[str] | None) -> bool:
    text = _cell_text(cell)
    if (
        not text.strip()
        or _already_formatted(cell)
        or not _needs_formatting(text, header_re)
    ):
        return False
    lines = parse_narrative(text, header_re)
    numbering = cell.part.numbering_part
    cell.text = ""
    cell.paragraphs[0].clear()
    paragraph, first, active_kind, num_id = cell.paragraphs[0], True, None, 0
    for line in lines:
        if not first:
            paragraph = cell.add_paragraph()
        first = False
        if line.kind in ("ordered", "bullet"):
            if active_kind != line.kind:
                num_id, active_kind = _new_list(numbering, line.kind), line.kind
            _apply_list(paragraph, num_id, line.level)
        else:
            active_kind = None
        if line.kind == "header":
            paragraph.paragraph_format.keep_with_next = True
            if paragraph._p is not cell.paragraphs[0]._p:
                paragraph.paragraph_format.space_before = _SPACE_BEFORE
        add_inline(paragraph, line.text, header=line.kind == "header")
    return True


def format_document(doc: Any, header_re: re.Pattern[str] | None) -> int:
    formatted = 0
    for tbl in list(doc.element.body.iter(qn("w:tbl"))):
        table = Table(tbl, doc)
        for tc in list(tbl.iterchildren(qn("w:tr"))):
            for cell_el in tc.iterchildren(qn("w:tc")):
                formatted += format_cell(_Cell(cell_el, table), header_re)
    return formatted


def strip_table_page_breaks(doc: Any) -> int:
    """Remove ``pageBreakBefore`` from paragraphs inside tables; body paragraphs are kept."""
    removed = 0
    for tbl in list(doc.element.body.iter(qn("w:tbl"))):
        for node in list(tbl.iter(qn("w:pageBreakBefore"))):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
                removed += 1
    return removed


DEFAULT_AUDIT_TABLE_RE = re.compile(r"^[A-Z]{2}-\d+")
_LITERAL_LIST_RE = re.compile(r"^\s*(\d+[.)]|[-*\u2022])\s+\S")


def audit_document(
    doc: Any,
    header_re: re.Pattern[str] | None,
    table_re: re.Pattern[str] = DEFAULT_AUDIT_TABLE_RE,
) -> dict[str, int]:
    """Count table paragraphs that still carry unrendered Markdown or inconsistent styling.

    Only tables whose first cell matches ``table_re`` (by default a control id such as
    ``AC-02``) are audited: other tables carry template reference text the pipeline does
    not write.
    Every count must be zero for the document to be consistently formatted.
    """
    counts = {
        "literal_bold": 0,
        "literal_links": 0,
        "literal_list_lines": 0,
        "headers_not_bold_underlined": 0,
        "table_page_breaks": 0,
    }
    for tbl in doc.element.body.iter(qn("w:tbl")):
        first = "".join(
            t.text or "" for t in next(tbl.iter(qn("w:tc")), tbl).iter(qn("w:t"))
        )
        if not table_re.match(first.strip()):
            continue
        counts["table_page_breaks"] += len(list(tbl.iter(qn("w:pageBreakBefore"))))
        for para in tbl.iter(qn("w:p")):
            text = "".join(t.text or "" for t in para.iter(qn("w:t")))
            if not text.strip():
                continue
            props = para.find(qn("w:pPr"))
            in_list = props is not None and props.find(qn("w:numPr")) is not None
            if "**" in text:
                counts["literal_bold"] += 1
            if re.search(r"\]\(https?://", text):
                counts["literal_links"] += 1
            if not in_list and _LITERAL_LIST_RE.match(text):
                counts["literal_list_lines"] += 1
            if header_re is not None and header_re.fullmatch(text.strip()):
                runs = [
                    r for r in para.iter(qn("w:r")) if r.find(qn("w:t")) is not None
                ]
                if not all(_bold_underlined(r) for r in runs):
                    counts["headers_not_bold_underlined"] += 1
    return counts


def _bold_underlined(run: Any) -> bool:
    props = run.find(qn("w:rPr"))
    if props is None:
        return False
    bold, underline = props.find(qn("w:b")), props.find(qn("w:u"))
    return (
        bold is not None
        and bold.get(qn("w:val")) not in ("0", "false")
        and underline is not None
        and underline.get(qn("w:val")) not in (None, "none")
    )
