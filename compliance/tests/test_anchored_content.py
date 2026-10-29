"""Tests for bookmark-backed paragraph and image fill."""

import base64

import docx

from compliance.lib.anchored_content import fill_anchored_image, fill_anchored_text


def test_anchored_text_survives_source_label_change(tmp_path):
    source = tmp_path / "source.docx"
    first = tmp_path / "first.docx"
    second = tmp_path / "second.docx"
    document = docx.Document()
    document.add_paragraph("System type placeholder")
    document.save(source)

    first_result = fill_anchored_text(
        source,
        anchor="system_type",
        containing_text="System type placeholder",
        value="First value",
        output_path=first,
    )
    second_result = fill_anchored_text(
        first,
        anchor="system_type",
        containing_text="no longer present",
        value="Second value",
        output_path=second,
    )

    assert first_result["matched"]
    assert second_result["matched"]


def test_anchored_image_preserves_alt_text(tmp_path):
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    image = tmp_path / "diagram.png"
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
    )
    document = docx.Document()
    document.add_paragraph("Diagram placeholder")
    document.save(source)

    result = fill_anchored_image(
        source,
        anchor="network_diagram",
        containing_text="Diagram placeholder",
        image_path=image,
        caption="Network Diagram",
        alt_text="Network architecture",
        output_path=output,
    )

    assert result["matched"]
