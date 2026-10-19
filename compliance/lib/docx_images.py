"""Extract embedded image binary data from a FedRAMP SSP `.docx`.

The primary extraction path (``docx_catchall_dump`` -> ``heading_parser``) is a
*text* pipeline: it records image *references* (rId + ``/word/media/imageN.png``
part name) but never the binary bytes, so diagrams were represented in the vault
only as a stringified metadata dict and no image files were written.

This module reads the raw image bytes straight from the ``.docx`` package
relationship parts (``python-docx`` exposes them via
``document.part.related_parts[rId].blob``) so the vault builder can write real
``Diagrams/media/<slug>-imageN.png`` attachment files and embed them with
Obsidian's ``![[...]]`` syntax.

The result maps ``rId -> ExtractedImage`` and is consumed by
``compliance.lib.vault.build_vault`` (via ``export_obsidian_vault``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Union

# DrawingML namespaces used to walk inline/anchor image references in body XML.
_NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# Image content-types we treat as embeddable vault attachments.
_IMAGE_CONTENT_TYPES = (
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/bmp",
    "image/tiff",
    "image/svg+xml",
    "image/webp",
    "image/x-emf",
    "image/x-wmf",
    "image/emf",
    "image/wmf",
)

# Map a content-type to a file extension when the part name has none/an odd one.
_CONTENT_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
    "image/x-emf": ".emf",
    "image/emf": ".emf",
    "image/x-wmf": ".wmf",
    "image/wmf": ".wmf",
}


@dataclass
class ExtractedImage:
    """One embedded image recovered from a `.docx`.

    ``rid``          — the relationship id (e.g. ``rId89``) used by body XML.
    ``part_name``    — the package part name (e.g. ``/word/media/image3.png``).
    ``content_type`` — MIME type reported by the package (e.g. ``image/png``).
    ``ext``          — file extension including the dot (e.g. ``.png``).
    ``data``         — the raw image bytes.
    ``media_stem``   — the media base name without extension (e.g. ``image3``).
    """

    rid: str
    part_name: str
    content_type: str
    ext: str
    data: bytes
    media_stem: str


def _ensure_docx_document(source: Union[str, Path, "object"]):
    """Return a python-docx Document for a path or pass through a Document."""
    from docx import Document  # local import; python-docx is a runtime dep

    if isinstance(source, (str, Path)):
        return Document(str(source))
    return source  # assume it is already a Document


def extract_images(source: Union[str, Path, "object"]) -> Dict[str, ExtractedImage]:
    """Extract every embedded image from a `.docx`, keyed by relationship id.

    ``source`` may be a path to a ``.docx`` or an already-open python-docx
    ``Document``. Returns ``{rId: ExtractedImage}``. Parts that are not images,
    or whose bytes cannot be read, are skipped (fail-open: a broken image never
    aborts a vault build).
    """
    doc = _ensure_docx_document(source)
    out: Dict[str, ExtractedImage] = {}

    try:
        related = doc.part.related_parts
    except Exception:
        return out

    for rid, part in related.items():
        part_name = str(getattr(part, "partname", "") or "")
        content_type = str(getattr(part, "content_type", "") or "").lower()

        # A part is an image if the package says so, or the part name lives
        # under /word/media/ with an image-ish extension.
        is_image = content_type in _IMAGE_CONTENT_TYPES or (
            "/media/" in part_name.lower()
            and Path(part_name).suffix.lower()
            in {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".bmp",
                ".tiff",
                ".svg",
                ".webp",
                ".emf",
                ".wmf",
            }
        )
        if not is_image:
            continue

        try:
            blob = part.blob
        except Exception:
            continue
        if not blob:
            continue

        ext = Path(part_name).suffix.lower()
        if not ext or ext == ".":
            ext = _CONTENT_TYPE_EXT.get(content_type, ".bin")
        media_stem = Path(part_name).stem or rid

        out[rid] = ExtractedImage(
            rid=rid,
            part_name=part_name,
            content_type=content_type or "application/octet-stream",
            ext=ext,
            data=blob,
            media_stem=media_stem,
        )

    return out


def image_rids_in_document_order(source: Union[str, Path, "object"]) -> List[str]:
    """Return image relationship ids in the order they appear in the body.

    Useful for numbering/embedding images the way a reader encounters them.
    rIds referenced multiple times appear once (first occurrence wins). rIds
    that resolve to a header/footer or are otherwise not in the main body are
    appended afterward (in relationship-map order) so nothing is lost.
    """
    doc = _ensure_docx_document(source)
    seen: List[str] = []
    seen_set = set()

    try:
        body = doc.element.body
    except Exception:
        body = None

    if body is not None:
        for blip in body.iter(f"{{{_NS_A}}}blip"):
            rid = blip.get(f"{{{_NS_R}}}embed") or blip.get(f"{{{_NS_R}}}link")
            if rid and rid not in seen_set:
                seen.append(rid)
                seen_set.add(rid)

    # Append any remaining image rIds not referenced inline in the body.
    for rid in extract_images(doc):
        if rid not in seen_set:
            seen.append(rid)
            seen_set.add(rid)

    return seen
