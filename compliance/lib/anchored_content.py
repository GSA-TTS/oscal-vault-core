"""Stable bookmark-backed paragraph and image content for DOCX templates."""

from __future__ import annotations

import hashlib
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches


def _bookmark_id(name: str) -> str:
    return str(int(hashlib.sha256(name.encode()).hexdigest()[:7], 16))


def _find_paragraph(doc: DocxDocument, anchor: str):
    bookmark_name = f"ttse_{anchor}"
    for paragraph in doc.paragraphs:
        if paragraph._p.xpath(f'.//w:bookmarkStart[@w:name="{bookmark_name}"]'):
            return paragraph
    return None


def _anchor_paragraph(paragraph, anchor: str) -> None:
    bookmark_name = f"ttse_{anchor}"
    bookmark_id = _bookmark_id(bookmark_name)
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), bookmark_id)
    start.set(qn("w:name"), bookmark_name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), bookmark_id)
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def find_or_anchor_paragraph(doc: DocxDocument, anchor: str, containing_text: str):
    paragraph = _find_paragraph(doc, anchor)
    if paragraph is not None:
        return paragraph
    matches = [p for p in doc.paragraphs if containing_text in p.text]
    if len(matches) != 1:
        raise ValueError(
            f"{anchor}: expected one paragraph containing {containing_text!r}, found {len(matches)}"
        )
    _anchor_paragraph(matches[0], anchor)
    return matches[0]


def fill_anchored_text(
    source: str | Path | DocxDocument,
    *,
    anchor: str,
    containing_text: str,
    value: str,
    output_path: str | Path,
) -> dict:
    doc = source if isinstance(source, DocxDocument) else Document(str(source))
    paragraph = find_or_anchor_paragraph(doc, anchor, containing_text)
    paragraph.text = value
    _anchor_paragraph(paragraph, anchor) if _find_paragraph(
        doc, anchor
    ) is None else None
    output = Path(output_path)
    doc.save(output)
    observed = _find_paragraph(Document(output), anchor)
    observed_text = observed.text if observed is not None else ""
    return {
        "anchor": anchor,
        "expected": value,
        "observed": observed_text,
        "matched": observed_text == value,
    }


def fill_anchored_image(
    source: str | Path | DocxDocument,
    *,
    anchor: str,
    containing_text: str,
    image_path: str | Path,
    caption: str,
    alt_text: str,
    output_path: str | Path,
    width_inches: float = 6.5,
) -> dict:
    doc = source if isinstance(source, DocxDocument) else Document(str(source))
    paragraph = find_or_anchor_paragraph(doc, anchor, containing_text)
    paragraph.clear()
    _anchor_paragraph(paragraph, anchor)
    run = paragraph.add_run()
    inline = run.add_picture(str(image_path), width=Inches(width_inches))._inline
    inline.docPr.set("descr", alt_text)
    caption_paragraph = paragraph.insert_paragraph_before(caption)
    caption_paragraph.style = (
        "Caption" if "Caption" in [s.name for s in doc.styles] else None
    )
    output = Path(output_path)
    doc.save(output)
    reloaded = Document(output)
    observed = _find_paragraph(reloaded, anchor)
    descriptions = (
        observed._p.xpath(".//wp:docPr/@descr") if observed is not None else []
    )
    return {
        "anchor": anchor,
        "image": str(image_path),
        "caption": caption,
        "alt_text": alt_text,
        "matched": alt_text in descriptions,
    }
