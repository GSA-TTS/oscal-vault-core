"""CUI-safe synthetic golden DOCX generator (#52, #45).

Generates a fully synthetic DOCX fixture covering:
  - Body paragraphs and headings (H1, H2, H3)
  - Hyperlinks with relationship targets
  - FedRAMP CIS control tables (AC-01, AC-02)
  - Tables with merged cells (gridSpan and vMerge)
  - Headers and footers
  - Footnotes and endnotes
  - Comments
  - Tracked revisions (w:ins and w:del)
  - Embedded image / drawing
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
import struct
import zlib
from pathlib import Path
from typing import Union

import docx
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches


def make_tiny_png() -> bytes:
    """Generate a valid 1x1 PNG byte string in pure Python without PIL."""
    width, height = 1, 1
    raw_data = b"\x00\xff\x00\x00"  # filter byte 0 + red RGB
    idat_data = zlib.compress(raw_data)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat_data)
        + chunk(b"IEND", b"")
    )


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def add_hyperlink(paragraph, url: str, text: str):
    """Add a hyperlink run to a python-docx paragraph."""
    part = paragraph.part
    r_id = part.relate_to(
        url,
        docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK,
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0000FF")
    rPr.append(color)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rPr.append(u)
    new_run.append(rPr)

    t = OxmlElement("w:t")
    t.text = text
    new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


def create_synthetic_golden_docx(output_path: Union[str, Path]) -> Path:
    """Generate a rich, 100% CUI-safe synthetic DOCX file with all target features."""
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()

    # 1. Header & Footer
    section = doc.sections[0]
    section.header.paragraphs[
        0
    ].text = "FEDRAMP SYNTHETIC GOLDEN - CUI SAFE TEST FIXTURE"
    section.footer.paragraphs[0].text = "Page 1 - UNCLASSIFIED / SYNTHETIC"

    # 2. Title & Headings
    doc.add_heading("FedRAMP Synthetic System Security Plan", level=1)
    doc.add_heading("1. System Identification", level=2)

    doc.add_paragraph(
        "This system is a synthetic reference workload designed for lossless IR validation."
    )
    doc.add_heading("2. Architecture & External References", level=2)
    p2 = doc.add_paragraph("Documentation and architecture overview is available at: ")
    add_hyperlink(p2, "https://fedramp.gov/synthetic-spec", "FedRAMP Synthetic Portal")

    # 3. Control Table 1: AC-01
    doc.add_heading("3. Access Control Policy (AC-01)", level=2)
    tbl1 = doc.add_table(rows=5, cols=2)
    tbl1.cell(0, 0).text = "Control Identifier"
    tbl1.cell(0, 1).text = "AC-01"
    tbl1.cell(1, 0).text = "Control Name"
    tbl1.cell(1, 1).text = "Access Control Policy and Procedures"
    tbl1.cell(2, 0).text = "Implementation Status"
    tbl1.cell(2, 1).text = "Implemented"
    tbl1.cell(3, 0).text = "Control Origination"
    tbl1.cell(3, 1).text = "Service Provider Corporate"
    tbl1.cell(4, 0).text = "What is the solution and how is it implemented?"
    tbl1.cell(4, 1).text = (
        "Part a: The organization develops and maintains an access control policy.\n"
        "Part b: Procedures are reviewed annually and published internally."
    )

    # 4. Control Table 2: AC-02
    doc.add_heading("4. Account Management (AC-02)", level=2)
    tbl2 = doc.add_table(rows=5, cols=2)
    tbl2.cell(0, 0).text = "Control Identifier"
    tbl2.cell(0, 1).text = "AC-02"
    tbl2.cell(1, 0).text = "Control Name"
    tbl2.cell(1, 1).text = "Account Management"
    tbl2.cell(2, 0).text = "Implementation Status"
    tbl2.cell(2, 1).text = "Partially Implemented"
    tbl2.cell(3, 0).text = "Control Origination"
    tbl2.cell(3, 1).text = "Customer Configured"
    tbl2.cell(4, 0).text = "What is the solution and how is it implemented?"
    tbl2.cell(4, 1).text = (
        "Part a: Automated provisioning handles standard user lifecycle.\n"
        "Part b: Inactive accounts are deactivated after 90 days of non-use."
    )

    # 5. Table with Merged Cells (gridSpan and vMerge)
    doc.add_heading("5. Component Matrix (Merged Table)", level=2)
    tbl_merge = doc.add_table(rows=3, cols=3)
    # Header row with col span (0,0 merged with 0,1 -> gridSpan=2)
    c00 = tbl_merge.cell(0, 0)
    c01 = tbl_merge.cell(0, 1)
    c00.merge(c01)
    c00.text = "Merged Header (Col 1-2)"
    tbl_merge.cell(0, 2).text = "Header Col 3"

    # Row 1 and 2 with vertical merge in col 0 (vMerge)
    c10 = tbl_merge.cell(1, 0)
    c20 = tbl_merge.cell(2, 0)
    c10.merge(c20)
    c10.text = "Merged Vertical (Row 1-2)"
    tbl_merge.cell(1, 1).text = "Data R1C2"
    tbl_merge.cell(1, 2).text = "Data R1C3"
    tbl_merge.cell(2, 1).text = "Data R2C2"
    tbl_merge.cell(2, 2).text = "Data R2C3"

    # 6. Embedded image
    doc.add_heading("6. System Architecture Diagram", level=2)
    doc.add_paragraph("Network topology diagram:")
    doc.add_picture(io.BytesIO(make_tiny_png()), width=Inches(1.0))

    # Save to intermediate in-memory bytes
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    # 7. Augment DOCX package with footnotes, endnotes, comments, and revisions
    footnotes_xml = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        b'<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:footnote w:type="separator" w:id="-1"/>'
        b'<w:footnote w:type="continuationSeparator" w:id="0"/>'
        b'<w:footnote w:id="1"><w:p><w:r><w:t>Synthetic footnote reference text for AC-01.</w:t></w:r></w:p></w:footnote>'
        b"</w:footnotes>"
    )
    endnotes_xml = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        b'<w:endnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:endnote w:type="separator" w:id="-1"/>'
        b'<w:endnote w:type="continuationSeparator" w:id="0"/>'
        b'<w:endnote w:id="1"><w:p><w:r><w:t>Synthetic endnote reference citation.</w:t></w:r></w:p></w:endnote>'
        b"</w:endnotes>"
    )
    comments_xml = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        b'<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:comment w:id="1" w:author="Compliance Auditor" w:date="2026-10-01T12:00:00Z">'
        b"<w:p><w:r><w:t>Review comment: Verify MFA enforcement mechanism.</w:t></w:r></w:p>"
        b"</w:comment>"
        b"</w:comments>"
    )

    with zipfile.ZipFile(buf, "r") as src_zip:
        with zipfile.ZipFile(out_p, "w", compression=zipfile.ZIP_DEFLATED) as dst_zip:
            for item in src_zip.infolist():
                content = src_zip.read(item.filename)

                if item.filename == "[Content_Types].xml":
                    # Add overrides for footnotes, endnotes, comments
                    ct_root = ET.fromstring(content)
                    ET.SubElement(
                        ct_root,
                        "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
                        {
                            "PartName": "/word/footnotes.xml",
                            "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
                        },
                    )
                    ET.SubElement(
                        ct_root,
                        "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
                        {
                            "PartName": "/word/endnotes.xml",
                            "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml",
                        },
                    )
                    ET.SubElement(
                        ct_root,
                        "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
                        {
                            "PartName": "/word/comments.xml",
                            "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
                        },
                    )
                    content = ET.tostring(
                        ct_root, encoding="utf-8", xml_declaration=True
                    )

                elif item.filename == "word/_rels/document.xml.rels":
                    # Add relationships to footnotes, endnotes, comments
                    rel_root = ET.fromstring(content)
                    ET.SubElement(
                        rel_root,
                        "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
                        {
                            "Id": "rIdFn1",
                            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
                            "Target": "footnotes.xml",
                        },
                    )
                    ET.SubElement(
                        rel_root,
                        "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
                        {
                            "Id": "rIdEn1",
                            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes",
                            "Target": "endnotes.xml",
                        },
                    )
                    ET.SubElement(
                        rel_root,
                        "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
                        {
                            "Id": "rIdCm1",
                            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
                            "Target": "comments.xml",
                        },
                    )
                    content = ET.tostring(
                        rel_root, encoding="utf-8", xml_declaration=True
                    )

                elif item.filename == "word/document.xml":
                    # Inject tracked revisions (w:ins and w:del), footnote reference, and comment reference
                    doc_root = ET.fromstring(content)
                    body = doc_root.find(f"{{{W_NS}}}body")
                    if body is not None:
                        # Append a paragraph containing tracked insertion and deletion
                        rev_p = ET.Element(f"{{{W_NS}}}p")
                        ins_elem = ET.SubElement(
                            rev_p,
                            f"{{{W_NS}}}ins",
                            {
                                f"{{{W_NS}}}id": "100",
                                f"{{{W_NS}}}author": "Policy Reviewer",
                                f"{{{W_NS}}}date": "2026-10-02T10:00:00Z",
                            },
                        )
                        ins_r = ET.SubElement(ins_elem, f"{{{W_NS}}}r")
                        ins_t = ET.SubElement(ins_r, f"{{{W_NS}}}t")
                        ins_t.text = (
                            "Tracked revision insertion: policy reviewed and approved."
                        )

                        del_elem = ET.SubElement(
                            rev_p,
                            f"{{{W_NS}}}del",
                            {
                                f"{{{W_NS}}}id": "101",
                                f"{{{W_NS}}}author": "Policy Reviewer",
                                f"{{{W_NS}}}date": "2026-10-02T10:01:00Z",
                            },
                        )
                        del_r = ET.SubElement(del_elem, f"{{{W_NS}}}r")
                        del_t = ET.SubElement(del_r, f"{{{W_NS}}}delText")
                        del_t.text = "Deprecated legacy clause deleted."

                        # Add footnote, endnote, and comment references in paragraph
                        fn_r = ET.SubElement(rev_p, f"{{{W_NS}}}r")
                        ET.SubElement(
                            fn_r, f"{{{W_NS}}}footnoteReference", {f"{{{W_NS}}}id": "1"}
                        )

                        en_r = ET.SubElement(rev_p, f"{{{W_NS}}}r")
                        ET.SubElement(
                            en_r, f"{{{W_NS}}}endnoteReference", {f"{{{W_NS}}}id": "1"}
                        )

                        ET.SubElement(
                            rev_p,
                            f"{{{W_NS}}}commentRangeStart",
                            {f"{{{W_NS}}}id": "1"},
                        )
                        ET.SubElement(
                            rev_p, f"{{{W_NS}}}commentRangeEnd", {f"{{{W_NS}}}id": "1"}
                        )
                        cm_r = ET.SubElement(rev_p, f"{{{W_NS}}}r")
                        ET.SubElement(
                            cm_r, f"{{{W_NS}}}commentReference", {f"{{{W_NS}}}id": "1"}
                        )

                        body.append(rev_p)

                    content = ET.tostring(
                        doc_root, encoding="utf-8", xml_declaration=True
                    )

                dst_zip.writestr(item, content)

            # Add the new parts
            dst_zip.writestr("word/footnotes.xml", footnotes_xml)
            dst_zip.writestr("word/endnotes.xml", endnotes_xml)
            dst_zip.writestr("word/comments.xml", comments_xml)

    return out_p
