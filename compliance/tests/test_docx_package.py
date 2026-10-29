from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from compliance.lib.docx_package import (
    PackageIntegrityError,
    PackageLimitError,
    inspect_package,
    strict_findings,
    unresolved_nodes,
    unresolved_parts,
)
from compliance.scripts.inspect_docx_package import main

CONTENT_TYPES = """<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://purl.org/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://purl.org/metadata" Target="docProps/core.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId10" Type="http://purl.org/header" Target="header1.xml"/>
  <Relationship Id="rId11" Type="http://purl.org/footnotes" Target="footnotes.xml"/>
  <Relationship Id="rId12" Type="http://purl.org/comments" Target="comments.xml"/>
  <Relationship Id="rId13" Type="http://purl.org/image" Target="media/image1.png"/>
  <Relationship Id="rId14" Type="http://purl.org/hyperlink" Target="https://example.gov/policy" TargetMode="External"/>
  <Relationship Id="rId15" Type="http://purl.org/customXml" Target="../customXml/item1.xml"/>
</Relationships>"""

DOCUMENT = """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Access control policy prose.</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>AC-01</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
    <w:p>
      <w:hyperlink r:id="rId14" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <w:r><w:t>Policy link</w:t></w:r>
      </w:hyperlink>
    </w:p>
    <w:ins w:id="7" w:author="Reviewer">
      <w:p><w:r><w:t>Inserted revision text.</w:t></w:r></w:p>
    </w:ins>
    <w:bookmarkStart w:id="1" w:name="ac-1"/>
    <w:bookmarkEnd w:id="1"/>
    <w:sectPr/>
  </w:body>
</w:document>"""

DOCUMENT_WITH_HIDDEN_SDT = """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Visible prose.</w:t></w:r></w:p>
    <w:sdt>
      <w:sdtContent>
        <w:p><w:r><w:t>Content-control prose that must not vanish.</w:t></w:r></w:p>
      </w:sdtContent>
    </w:sdt>
  </w:body>
</w:document>"""

DOCUMENT_WITH_UNKNOWN_NODE = """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Visible prose.</w:t></w:r></w:p>
    <w:unsupportedBlock><w:r><w:t>Unroutable content.</w:t></w:r></w:unsupportedBlock>
  </w:body>
</w:document>"""

HEADER = """<?xml version="1.0"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p><w:r><w:t>CONTROLLED UNCLASSIFIED INFORMATION</w:t></w:r></w:p>
</w:hdr>"""

FOOTNOTES = """<?xml version="1.0"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:footnote w:id="1"><w:p><w:r><w:t>Footnote text.</w:t></w:r></w:p></w:footnote>
</w:footnotes>"""

COMMENTS = """<?xml version="1.0"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:comment w:id="1" w:author="Reviewer"><w:p><w:r><w:t>Comment text.</w:t></w:r></w:p></w:comment>
</w:comments>"""


def _write_package(
    path: Path,
    *,
    document: str = DOCUMENT,
    extra: dict[str, bytes | str] | None = None,
    doc_rels: str = DOC_RELS,
    root_rels: str = ROOT_RELS,
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/header1.xml", HEADER)
        archive.writestr("word/footnotes.xml", FOOTNOTES)
        archive.writestr("word/comments.xml", COMMENTS)
        archive.writestr("word/media/image1.png", b"synthetic-png")
        archive.writestr("customXml/item1.xml", "<root/>")
        archive.writestr("docProps/core.xml", "<cp:coreProperties/>")
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)


def test_inventory_classifies_every_package_part(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx)

    report = inspect_package(docx)
    parts = {part["path"]: part for part in report["parts"]}

    assert parts["word/document.xml"]["disposition"] == "structured"
    assert parts["word/header1.xml"]["disposition"] == "opaque-preserved"
    assert parts["word/footnotes.xml"]["disposition"] == "opaque-preserved"
    assert parts["word/comments.xml"]["disposition"] == "opaque-preserved"
    assert parts["word/media/image1.png"]["disposition"] == "opaque-preserved"
    assert parts["customXml/item1.xml"]["disposition"] == "opaque-preserved"
    assert parts["docProps/core.xml"]["disposition"] == "intentionally-excluded"
    assert strict_findings(report) == []


def test_inventory_records_content_type_from_default_extension(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx)

    parts = {part["path"]: part for part in inspect_package(docx)["parts"]}

    assert parts["word/media/image1.png"]["content_type"] == "image/png"


def test_inventory_records_digest_for_opaque_parts(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx)

    parts = {part["path"]: part for part in inspect_package(docx)["parts"]}
    media = parts["word/media/image1.png"]

    assert len(media["sha256"]) == 64
    assert media["size"] == len(b"synthetic-png")


def test_inventory_enumerates_relationship_graph(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx)

    report = inspect_package(docx)
    relationships = {
        (rel["rels_part"], rel["id"]): rel for rel in report["relationships"]
    }

    hyperlink = relationships[("word/_rels/document.xml.rels", "rId14")]
    assert hyperlink["target_mode"] == "External"
    assert hyperlink["target"] == "https://example.gov/policy"
    assert hyperlink["resolved_target"] is None

    image = relationships[("word/_rels/document.xml.rels", "rId13")]
    assert image["source_part"] == "word/document.xml"
    assert image["resolved_target"] == "word/media/image1.png"

    custom = relationships[("word/_rels/document.xml.rels", "rId15")]
    assert custom["resolved_target"] == "customXml/item1.xml"

    assert report["summary"]["relationship_count"] == 8
    assert report["summary"]["external_relationship_count"] == 1

    parts = {part["path"]: part for part in report["parts"]}
    assert parts["word/media/image1.png"]["referenced_by"] == [
        "word/_rels/document.xml.rels#rId13"
    ]


def test_unreachable_word_part_is_unmapped(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx, extra={"word/unsupported.xml": "<w:unsupported/>"})

    report = inspect_package(docx)

    assert unresolved_parts(report) == ["word/unsupported.xml"]
    assert main(["--docx", str(docx), "--strict"]) == 1


def test_orphaned_non_word_part_is_unmapped_not_excluded(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx, extra={"orphan/payload.xml": "<data/>"})

    report = inspect_package(docx)
    parts = {part["path"]: part for part in report["parts"]}

    assert parts["orphan/payload.xml"]["reachable"] is False
    assert parts["orphan/payload.xml"]["disposition"] == "unmapped-error"
    assert main(["--docx", str(docx), "--strict"]) == 1


def test_undeclared_header_lookalike_does_not_inherit_disposition(
    tmp_path: Path,
) -> None:
    docx = tmp_path / "fixture.docx"
    doc_rels = DOC_RELS.replace(
        '<Relationship Id="rId10" Type="http://purl.org/header" Target="header1.xml"/>',
        '<Relationship Id="rId10" Type="http://purl.org/header" Target="header1.xml"/>'
        '<Relationship Id="rId16" Type="http://purl.org/header" Target="headerUnsupported.xml"/>',
    )
    _write_package(
        docx,
        doc_rels=doc_rels,
        extra={"word/headerUnsupported.xml": "<w:hdr/>"},
    )

    report = inspect_package(docx)

    assert unresolved_parts(report) == ["word/headerUnsupported.xml"]


def test_dangling_relationship_fails_strict_mode(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    doc_rels = DOC_RELS.replace(
        '<Relationship Id="rId13" Type="http://purl.org/image" Target="media/image1.png"/>',
        '<Relationship Id="rId13" Type="http://purl.org/image" Target="media/image1.png"/>'
        '<Relationship Id="rId17" Type="http://purl.org/image" Target="media/missing.png"/>',
    )
    _write_package(docx, doc_rels=doc_rels)

    report = inspect_package(docx)

    assert report["summary"]["dangling_relationships"] == [
        "word/_rels/document.xml.rels#rId17 targets missing part "
        "'word/media/missing.png'"
    ]
    assert main(["--docx", str(docx), "--strict"]) == 1


def test_body_nodes_are_classified_individually(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx)

    report = inspect_package(docx)
    nodes = report["nodes"]
    kinds = [node["kind"] for node in nodes if node["part_uri"] == "word/document.xml"]

    # body prose, hyperlink paragraph, and the tracked-revision paragraph
    assert kinds.count("paragraph") == 3
    assert kinds.count("table") == 1
    assert {"bookmarkStart", "bookmarkEnd", "sectPr"} <= set(kinds)
    assert any(node["part_uri"] == "word/header1.xml" for node in nodes)
    assert any(node["part_uri"] == "word/footnotes.xml" for node in nodes)
    assert any(node["part_uri"] == "word/comments.xml" for node in nodes)
    assert unresolved_nodes(report) == []


def test_content_control_content_is_not_hidden_by_its_wrapper(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx, document=DOCUMENT_WITH_HIDDEN_SDT)

    report = inspect_package(docx)
    locators = [node["xml_locator"] for node in report["nodes"]]

    assert any("sdt[1]/sdtContent[1]/p[1]" in locator for locator in locators)
    assert unresolved_nodes(report) == []


def test_unknown_content_bearing_node_fails_strict_mode(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx, document=DOCUMENT_WITH_UNKNOWN_NODE)

    report = inspect_package(docx)

    assert unresolved_nodes(report) == [
        "word/document.xml#document/body[1]/unsupportedBlock[1]"
    ]
    assert main(["--docx", str(docx), "--strict"]) == 1
    assert main(["--docx", str(docx)]) == 0


def test_report_is_independent_of_invocation_path(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx)

    direct = inspect_package(docx)
    relative = inspect_package(Path(tmp_path / "." / "fixture.docx"))

    assert json.dumps(direct, sort_keys=True) == json.dumps(relative, sort_keys=True)


def test_duplicate_member_names_are_rejected(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx)
    with zipfile.ZipFile(docx, "a") as archive:
        archive.writestr("word/document.xml", "<w:document/>")

    with pytest.raises(PackageIntegrityError):
        inspect_package(docx)
    assert main(["--docx", str(docx), "--strict"]) == 1


def test_oversized_declared_part_is_rejected_before_read(tmp_path: Path) -> None:
    docx = tmp_path / "bomb.docx"
    _write_package(docx, extra={"word/bomb.bin": b"0" * 4096})

    from compliance.lib import docx_package

    original = docx_package.MAX_PART_BYTES
    docx_package.MAX_PART_BYTES = 1024
    try:
        with pytest.raises(PackageLimitError):
            inspect_package(docx)
        assert main(["--docx", str(docx), "--strict"]) == 1
    finally:
        docx_package.MAX_PART_BYTES = original


def test_malformed_xml_part_is_rejected(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_package(docx, document="<w:document>")

    with pytest.raises(PackageIntegrityError):
        inspect_package(docx)


def test_content_type_classifies_parts_with_nonstandard_paths(tmp_path: Path) -> None:
    """Real templates ship ``customXML/`` and ``word/fonts/`` layouts.

    Part identity comes from the declared OPC content type, so a known part kind
    in an unexpected directory must not be reported as unmapped.
    """
    docx = tmp_path / "fixture.docx"
    content_types = CONTENT_TYPES.replace(
        "</Types>",
        '  <Default Extension="ttf" ContentType="application/x-font-ttf"/>\n'
        '  <Override PartName="/customXML/itemProps1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.customXmlProperties+xml"/>\n'
        "</Types>",
    )
    doc_rels = DOC_RELS.replace(
        "</Relationships>",
        '  <Relationship Id="rId20" Type="http://purl.org/customXmlProps" Target="../customXML/itemProps1.xml"/>\n'
        '  <Relationship Id="rId21" Type="http://purl.org/font" Target="fonts/Example-regular.ttf"/>\n'
        "</Relationships>",
    )
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
        archive.writestr("word/document.xml", DOCUMENT)
        archive.writestr("word/header1.xml", HEADER)
        archive.writestr("word/footnotes.xml", FOOTNOTES)
        archive.writestr("word/comments.xml", COMMENTS)
        archive.writestr("word/media/image1.png", b"synthetic-png")
        archive.writestr("customXml/item1.xml", "<root/>")
        archive.writestr("customXML/itemProps1.xml", "<ds:datastoreItem/>")
        archive.writestr("word/fonts/Example-regular.ttf", b"synthetic-font")
        archive.writestr("docProps/core.xml", "<cp:coreProperties/>")

    report = inspect_package(docx)
    parts = {part["path"]: part for part in report["parts"]}

    assert parts["customXML/itemProps1.xml"]["disposition"] == "opaque-preserved"
    assert parts["word/fonts/Example-regular.ttf"]["disposition"] == (
        "intentionally-excluded"
    )
    assert strict_findings(report) == []


@pytest.mark.parametrize(
    "template",
    [
        "High-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
        "Leveraged-SaaS-CRM-SSPP-Template-[Contains-CUI]-02-01-2024.docx",
        "Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
        "MiSaaS-SSPP-Template-[Contains-CUI]-03-19-2025.docx",
        "Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
    ],
)
def test_committed_templates_are_fully_classified(template: str) -> None:
    """Every part and body node of the committed blank templates is accounted for.

    Templates are ``.gitignore``d, so this skips when they are absent (the
    repository never commits ``.docx`` sources).
    """
    path = Path(__file__).resolve().parents[2] / "templates" / template
    if not path.is_file():
        pytest.skip(f"template not available: {path}")

    report = inspect_package(path)

    assert strict_findings(report) == []
    assert report["summary"]["node_count"] > 100
    assert report["summary"]["relationship_count"] > 10
