"""Tests for allowlisted DOCX template token replacement."""

import zipfile

from compliance.scripts.replace_docx_tokens import replace_tokens


def test_replace_tokens_updates_word_xml_only(tmp_path):
    path = tmp_path / "document.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<w:t>[ACRONYM]</w:t>")
        archive.writestr("custom.xml", "[ACRONYM]")

    result = replace_tokens(path, {"[ACRONYM]": "SYN"})

    assert result["total_replacements"] == 1
    assert result["remaining_tokens"] == 0
    with zipfile.ZipFile(path) as archive:
        assert b"SYN" in archive.read("word/document.xml")
        assert b"[ACRONYM]" in archive.read("custom.xml")
