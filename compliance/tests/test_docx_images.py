"""Tests for compliance.lib.docx_images (embedded image extraction).

These tests build a minimal in-memory .docx with an embedded PNG so they run
without any committed .docx fixture (per the repo's no-.docx policy).
"""

from __future__ import annotations

import io
import struct
import sys
import zlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.docx_images import (  # noqa: E402
    extract_images,
    image_rids_in_document_order,
)


def _valid_png_1x1() -> bytes:
    """A minimal but structurally valid 1x1 RGB PNG that python-docx parses."""

    def _chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit, RGB
    raw = b"\x00\xff\x00\x00"  # filter byte + one RGB pixel
    idat = zlib.compress(raw)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


_PNG_BYTES = _valid_png_1x1()


def _docx_with_image():
    """Build an in-memory python-docx Document with one embedded PNG."""
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("Network Architecture")
    doc.add_picture(io.BytesIO(_PNG_BYTES))
    return doc


def test_extract_images_returns_bytes():
    doc = _docx_with_image()
    images = extract_images(doc)
    assert images, "expected at least one extracted image"
    img = next(iter(images.values()))
    assert img.data == _PNG_BYTES
    assert img.ext == ".png"
    assert img.content_type.startswith("image/")
    assert img.media_stem  # non-empty stem, e.g. "image1"
    assert img.part_name.lower().endswith(".png")


def test_extract_images_from_path(tmp_path):
    doc = _docx_with_image()
    p = tmp_path / "sample.docx"
    doc.save(str(p))
    images = extract_images(str(p))
    assert images
    assert any(i.data == _PNG_BYTES for i in images.values())


def test_extract_images_no_images_returns_empty():
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("No pictures here.")
    assert extract_images(doc) == {}


def test_image_rids_in_document_order_includes_embedded():
    doc = _docx_with_image()
    order = image_rids_in_document_order(doc)
    rids = set(extract_images(doc))
    # Every extracted image rId must appear in the ordering.
    assert rids <= set(order)


def test_extract_images_fail_open_on_bad_source():
    # A source with no related_parts / not a Document must not raise.
    class _Bogus:
        pass

    assert extract_images(_Bogus()) == {}


# --- Lossless catchall dump (no row/cell/paragraph truncation) -------------


def _docx_with_big_table(nrows: int = 30):
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("Types of Users")
    tbl = doc.add_table(rows=nrows + 1, cols=2)
    tbl.rows[0].cells[0].text = "Role"
    tbl.rows[0].cells[1].text = "Notes"
    for i in range(1, nrows + 1):
        tbl.rows[i].cells[0].text = f"role-{i}"
        tbl.rows[i].cells[1].text = "x" * 800  # exceed the 400-char cell cap
    return doc


def test_dump_docx_full_mode_omits_nothing(tmp_path):
    import sys as _sys

    _sys.path.insert(0, str(_REPO_ROOT / "compliance" / "scripts"))
    import docx_catchall_dump as dcd  # type: ignore

    doc = _docx_with_big_table(nrows=30)
    src = tmp_path / "big.docx"
    doc.save(str(src))

    # Default (capped) dump: rows are omitted and cells truncated.
    capped = tmp_path / "capped.md"
    dcd.dump_docx(src, capped, full=False)
    capped_md = capped.read_text(encoding="utf-8")
    assert "rows omitted" in capped_md
    assert "…" in capped_md  # cell truncation ellipsis

    # Lossless dump: every row present, no omission marker, full cell text.
    full = tmp_path / "full.md"
    dcd.dump_docx(src, full, full=True)
    full_md = full.read_text(encoding="utf-8")
    assert "rows omitted" not in full_md
    for i in range(1, 31):
        assert f"role-{i}" in full_md, f"row role-{i} missing from lossless dump"
    assert ("x" * 800) in full_md, "cell content was truncated in lossless dump"


def test_catchall_requires_a_docx_or_all(capsys):
    import pytest

    from compliance.scripts import docx_catchall_dump as dcd

    with pytest.raises(SystemExit) as exc:
        dcd.main([])
    assert exc.value.code == 2
    assert "--docx" in capsys.readouterr().err


def test_catchall_all_reports_an_empty_directory(tmp_path, capsys):
    from compliance.scripts import docx_catchall_dump as dcd

    assert dcd.main(["--all", "--docx-dir", str(tmp_path)]) == 1
    assert "No .docx files found" in capsys.readouterr().err
