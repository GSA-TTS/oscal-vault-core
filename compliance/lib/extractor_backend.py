"""Pluggable extractor backend abstraction (Phase R12 benchmark harness).

This module defines:

1. ``ExtractorBackend`` — a ``Protocol`` that every extraction backend must
   implement, enabling drop-in comparison between the legacy fismatic parser
   and three alternative document-intelligence libraries.

2. ``FismaticBackend`` — wraps the existing python-docx path (baseline).

3. ``DoclingBackend`` — wraps IBM's Docling ``DocumentConverter``.

4. ``MinerUBackend`` — wraps OpenDataLab's MinerU (``mineru.api.MinerU``).

5. ``UnstructuredBackend`` — wraps Unstructured-IO's ``partition_docx``.

6. ``ExtractedDoc`` — normalised intermediate representation shared by all
   backends (headings, tables, paragraphs, images).

All optional backends (Docling, MinerU, Unstructured) live in the shared
``[dependency-groups.benchmark]`` uv group — one environment, no separate
venvs.  Install with::

    uv sync --group benchmark

Backend selection
-----------------
::

    backend = get_backend("docling")   # or "fismatic" | "mineru" | "unstructured"
    doc     = backend.extract(docx_path)
    routed  = route_document_tree(doc.to_document_tree(), contract)

References
----------
- https://docling-project.github.io/docling/reference/document_converter/
- https://opendatalab.github.io/MinerU/
- https://github.com/Unstructured-IO/unstructured
- ``docs/ADR/ADR-015-extractor-benchmark-harness.md``
- ``compliance/scripts/benchmark_extractors.py``
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from compliance.lib.unified_converter import DocumentNode, DocumentTree


# ---------------------------------------------------------------------------
# Normalised extraction output
# ---------------------------------------------------------------------------


@dataclass
class ExtractedTable:
    """A table extracted from the source document.

    Attributes
    ----------
    heading:
        The nearest ancestor heading text (used for contract routing).
    rows:
        List of rows; each row is a list of cell text strings.
    raw:
        Backend-native table object (optional, for debugging).
    """

    heading: str
    rows: List[List[str]]
    raw: Any = None


@dataclass
class ExtractedImage:
    """An image extracted from the source document.

    Attributes
    ----------
    heading:
        The nearest ancestor heading text.
    alt_text:
        Alt text or caption (may be empty).
    data:
        Raw image bytes (``None`` if not captured by the backend).
    """

    heading: str
    alt_text: str
    data: Optional[bytes] = None


@dataclass
class ExtractedDoc:
    """Normalised document representation produced by any ``ExtractorBackend``.

    All backends produce this shape so that downstream contract routing and
    entity assembly are backend-agnostic.

    Attributes
    ----------
    source_path:
        Absolute path to the source ``.docx`` file.
    backend_name:
        Name of the backend that produced this document.
    headings:
        Ordered list of ``(level, text)`` heading tuples.
    paragraphs:
        List of ``(heading_text, paragraph_text)`` tuples.
    tables:
        List of :class:`ExtractedTable`.
    images:
        List of :class:`ExtractedImage`.
    elapsed_s:
        Wall-clock seconds taken by the backend to extract the document.
    metadata:
        Backend-specific metadata (confidence scores, page count, etc.).
    """

    source_path: str
    backend_name: str
    headings: List[tuple[int, str]]  # (level, text)
    paragraphs: List[tuple[str, str]]  # (heading, text)
    tables: List[ExtractedTable] = field(default_factory=list)
    images: List[ExtractedImage] = field(default_factory=list)
    elapsed_s: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    def to_document_tree(self) -> DocumentTree:
        """Convert this ``ExtractedDoc`` to a ``DocumentTree``.

        Enables the unified converter's routing stage to operate on
        ``ExtractedDoc`` regardless of which backend produced it.
        """
        nodes: List[DocumentNode] = []
        heading_children: Dict[str, List[DocumentNode]] = {}

        for level, heading_text in self.headings:
            heading_children.setdefault(heading_text, [])
            nodes.append(
                DocumentNode(
                    kind="heading",
                    level=level,
                    text=heading_text,
                    raw=None,
                    children=heading_children[heading_text],
                )
            )

        for heading_text, para_text in self.paragraphs:
            child = DocumentNode(kind="paragraph", level=None, text=para_text, raw=None)
            if heading_text in heading_children:
                heading_children[heading_text].append(child)
            else:
                nodes.append(child)

        for table in self.tables:
            child = DocumentNode(kind="table", level=None, text="", raw=table.rows)
            if table.heading in heading_children:
                heading_children[table.heading].append(child)
            else:
                nodes.append(child)

        for image in self.images:
            child = DocumentNode(
                kind="image", level=None, text=image.alt_text, raw=image.data
            )
            if image.heading in heading_children:
                heading_children[image.heading].append(child)
            else:
                nodes.append(child)

        return DocumentTree(nodes=nodes)

    def heading_count(self) -> int:
        return len(self.headings)

    def table_count(self) -> int:
        return len(self.tables)

    def paragraph_count(self) -> int:
        return len(self.paragraphs)

    def image_count(self) -> int:
        return len(self.images)

    # ------------------------------------------------------------------
    # Cross-backend comparison helpers (R14 equal-footing)
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_heading_text(text: str) -> str:
        """Normalize a heading text for cross-backend comparison.

        Strips:
        - Leading chapter numbering prefixes (``"10.4 "``, ``"13. "``).
        - Trailing/embedded markdown bold markers (``"**...**"``).
        - Markdown escape backslashes (``"\\("``, ``"\\."``, ``"\\-"``)
          that mammoth emits around punctuation.
        - Surrounding whitespace.

        Examples:
        - ``"10 **System Environment**"`` -> ``"System Environment"``
        - ``"  4.2 Information Types  "`` -> ``"Information Types"``
        - ``"Access Control \\\\(AC\\\\)"`` -> ``"Access Control (AC)"``
        - ``"**Approva ls**"`` -> ``"Approva ls"``

        The function is intentionally lossy: the goal is cross-backend
        recall, not exact reproduction. Backend-specific context (e.g.
        ``fismatic``'s raw text vs ``mineru``'s bold-marked text vs
        ``mammoth``'s escape-marked text) is normalized so the same
        contract ``sourceHeading`` pattern can match any of them.
        """
        import re

        t = text.strip()
        # Strip leading chapter-number prefix: "1 ", "10.4 ", "13.1.2 "
        t = re.sub(r"^\d+(?:\.\d+)*\s+", "", t)
        # Strip markdown escape backslashes (mammoth escapes punctuation:
        # "(", ")", ".", "-", "_", etc.) BEFORE the bold-marker pass so
        # they don't pollute the result.
        t = re.sub(r"\\(.)", r"\1", t)
        # Strip bold markers (and any remaining leading/trailing whitespace)
        t = re.sub(r"\*+", "", t).strip()
        return t

    def normalized_headings(self, level: Optional[int] = None) -> List[tuple[int, str]]:
        """Return ``(level, normalized_text)`` tuples, optionally filtered to one level."""
        if level is None:
            return [(lvl, self.normalize_heading_text(t)) for lvl, t in self.headings]
        return [
            (lvl, self.normalize_heading_text(t))
            for lvl, t in self.headings
            if lvl == level
        ]

    def heading_text_set(self, level: Optional[int] = None) -> set:
        """Return the set of normalized heading texts (optionally one level)."""
        return {t for _, t in self.normalized_headings(level=level)}

    def heading_recall(self, other: "ExtractedDoc") -> float:
        """Return recall of this doc's headings vs ``other``'s.

        ``recall = |intersection| / |other|`` — what fraction of
        ``other``'s headings does this doc find. 0 if ``other`` has no
        headings. Used by the benchmark harness to compare backends on
        equal footing after normalization.
        """
        other_set = other.heading_text_set()
        if not other_set:
            return 0.0
        this_set = self.heading_text_set()
        return len(this_set & other_set) / len(other_set)


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ExtractorBackend(Protocol):
    """Protocol that every extraction backend must implement.

    Implementing a new backend requires exactly two methods: ``extract`` and
    ``is_available``, plus a ``name`` class attribute.
    """

    #: Human-readable name for CLI and reporting.
    name: str

    def extract(self, docx_path: str | Path) -> ExtractedDoc:
        """Extract document content from a ``.docx`` file.

        Parameters
        ----------
        docx_path:
            Path to the source ``.docx`` file.

        Returns
        -------
        ExtractedDoc

        Raises
        ------
        FileNotFoundError
            If ``docx_path`` does not exist.
        ImportError
            If the backend's optional dependencies are not installed.
        RuntimeError
            If the backend encounters a fatal extraction error.
        """
        ...

    def is_available(self) -> bool:
        """Return ``True`` if the backend's optional dependencies are installed."""
        ...


# ---------------------------------------------------------------------------
# FismaticBackend  (baseline — always available)
# ---------------------------------------------------------------------------


# DrawingML namespaces (mirrored from scripts/docx_catchall_dump.py so
# backends detect inline/anchor images in the same way).
_NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def detect_image_rids_in_paragraph(para) -> List[str]:
    """Return rIds of inline/anchor images in this python-docx paragraph.

    Shared by FismaticBackend and UnstructuredBackend so both backends
    report the same inline-image count for a given docx (unstructured's
    default ``partition_docx`` call does not enumerate inline images).
    """
    rids: List[str] = []
    el = para._element
    for tag_local in ("inline", "anchor"):
        for node in el.iter(f"{{{_NS_WP}}}{tag_local}"):
            blip = node.find(f".//{{{_NS_A}}}blip")
            if blip is not None:
                rid = blip.get(f"{{{_NS_R}}}embed") or blip.get(f"{{{_NS_R}}}link")
                if rid:
                    rids.append(rid)
    return rids


def iter_docx_blocks_with_style(doc):
    """Yield ``(block_kind, paragraph_or_table, style_name)`` for every block.

    Same walk as FismaticBackend.extract() but yields style names so
    UnstructuredBackend (which doesn't read paragraph styles) can promote
    Heading-styled paragraphs to ``(level, text)`` headings.
    """
    import docx as python_docx

    for block in doc.element.body:
        tag = block.tag.split("}")[-1] if "}" in block.tag else block.tag
        if tag == "p":
            para = python_docx.text.paragraph.Paragraph(block, doc)
            style_name = para.style.name if para.style else ""
            yield ("p", para, style_name)
        elif tag == "tbl":
            tbl = python_docx.table.Table(block, doc)
            yield ("tbl", tbl, "")


def heading_level_from_style(style_name: str) -> Optional[int]:
    """Return the H1/H2/... level from a Word paragraph style name.

    Returns ``None`` for non-heading styles. Handles:
    - ``"Heading 1"`` -> 1
    - ``"Heading 2"`` -> 2
    - ``"Heading 3"`` -> 3
    - ``"Heading 4"`` -> 4
    - ``"Title"`` -> 1  (used by UnstructuredBackend to promote cover titles)
    - Anything else -> None
    """
    s = style_name.strip()
    if s.startswith("Heading"):
        try:
            return int(s.split()[-1])
        except (ValueError, IndexError):
            return 1
    if s == "Title":
        return 1
    return None


def _is_likely_sentence(text: str) -> bool:
    """Heuristic: does this text look like a full sentence (not a heading)?

    Used by MinerUBackend to filter mammoth false-positive headings.
    A heading is typically a short noun phrase ending in punctuation-free
    or a known terminator (`:`, `:`-prefixed label, etc.). A sentence
    is a longer phrase that ends in `;` or `.` and has a verb-like
    structure.

    Heuristics, in priority order:
    - Ends in `;*` (italicized phrase from a list item) — likely a sentence.
    - Contains `; and` (a list connector) — likely a sentence.
    - Contains 8+ words AND ends in `;` or `.` — likely a sentence.
    """
    t = text.strip()
    if t.endswith(";*") or t.endswith(".*"):
        return True
    if "; and" in t or "; or" in t:
        return True
    # Strip ** markdown markers
    t_clean = t.replace("**", "").strip()
    word_count = len(t_clean.split())
    if word_count >= 8 and (t_clean.endswith(";") or t_clean.endswith(".")):
        return True
    return False


class FismaticBackend:
    """Extraction backend wrapping the legacy fismatic + python-docx path.

    Uses ``python-docx`` directly — the same underlying library as
    ``fismatic.SSPConverter`` and ``compliance.lib.heading_parser``.
    This is the **baseline** against which all other backends are compared.
    """

    name: str = "fismatic"

    def is_available(self) -> bool:
        try:
            import docx  # noqa: F401

            return True
        except ImportError:
            return False

    @classmethod
    def _detect_image_rids(cls, para) -> List[str]:
        """Compatibility shim: delegate to the module-level helper."""
        return detect_image_rids_in_paragraph(para)

    def extract(self, docx_path: str | Path) -> ExtractedDoc:
        """Extract using python-docx (fismatic-compatible path)."""
        import docx as python_docx

        path = Path(docx_path)
        if not path.exists():
            raise FileNotFoundError(f"docx not found: {path}")

        t0 = time.perf_counter()
        doc = python_docx.Document(str(path))

        headings: List[tuple[int, str]] = []
        paragraphs: List[tuple[str, str]] = []
        tables: List[ExtractedTable] = []
        images: List[ExtractedImage] = []
        current_heading = ""

        for kind, block, style_name in iter_docx_blocks_with_style(doc):
            if kind == "p":
                para = block
                text = para.text.strip()
                if not text:
                    # Even empty paragraphs may carry inline images (e.g.
                    # data-flow diagrams). Still scan them for image rIds.
                    for rid in detect_image_rids_in_paragraph(para):
                        images.append(
                            ExtractedImage(heading=current_heading, alt_text="")
                        )
                    continue
                level = heading_level_from_style(style_name)
                if text.strip().lower() in (
                    "document prepared by",
                    "document revision history",
                    "revision history",
                ):
                    level = 1
                if level is not None:
                    headings.append((level, text))
                    current_heading = text
                else:
                    paragraphs.append((current_heading, text))
                    # Image detection happens for every paragraph (images are
                    # common inside data-flow / architecture sections).
                    for rid in detect_image_rids_in_paragraph(para):
                        images.append(
                            ExtractedImage(heading=current_heading, alt_text="")
                        )

            elif kind == "tbl":
                tbl = block
                rows = [[cell.text.strip() for cell in row.cells] for row in tbl.rows]
                if rows:
                    tables.append(ExtractedTable(heading=current_heading, rows=rows))

        elapsed = time.perf_counter() - t0

        return ExtractedDoc(
            source_path=str(path.resolve()),
            backend_name=self.name,
            headings=headings,
            paragraphs=paragraphs,
            tables=tables,
            images=images,
            elapsed_s=elapsed,
            metadata={"page_count": None},
        )


# ---------------------------------------------------------------------------
# DoclingBackend  (IBM Research)
# ---------------------------------------------------------------------------


class DoclingBackend:
    """Extraction backend wrapping IBM's Docling.

    Converts ``.docx`` via ``docling.backend.msword_backend.MsWordDocumentBackend``
    (the docx native backend) called directly, bypassing
    ``docling.document_converter.DocumentConverter``.

    **R15 rationale for bypassing DocumentConverter**: as of docling-slim
    2.113.0, the high-level ``DocumentConverter`` transitively imports
    ``docling.models.stages.chart_extraction.granite_vision`` (which
    requires torch+transformers) and ``docling_ibm_models.reading_order``
    (which requires the full ``docling-ibm-models`` ML bundle). Calling
    ``MsWordDocumentBackend`` directly avoids the import chain entirely
    and lets the benchmark run on a lean CPU-only install (no torch,
    no transformers, no model downloads).

    The backend returns a ``DoclingDocument`` with per-element type
    labels (``SectionHeaderItem``, ``TextItem``, ``TableItem``,
    ``PictureItem``) that this class walks to produce an ``ExtractedDoc``
    with the same shape as the other backends.

    Install::

        uv sync --group benchmark

    References
    ----------
    - https://docling-project.github.io/docling/
    - https://github.com/docling-project/docling
    """

    name: str = "docling"

    def is_available(self) -> bool:
        # R15: check the lightweight backend import, not DocumentConverter
        # (which transitively requires torch + transformers).
        try:
            from docling.backend.msword_backend import MsWordDocumentBackend  # noqa: F401
            from docling.datamodel.base_models import InputFormat  # noqa: F401

            return True
        except ImportError:
            return False

    def extract(self, docx_path: str | Path) -> ExtractedDoc:
        """Extract using MsWordDocumentBackend directly (R15 lean path)."""
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.document import InputDocument
            from docling.backend.msword_backend import MsWordDocumentBackend
        except ImportError as exc:
            raise ImportError(
                "Docling is not installed. Run: uv sync --group benchmark"
            ) from exc

        path = Path(docx_path)
        if not path.exists():
            raise FileNotFoundError(f"docx not found: {path}")

        t0 = time.perf_counter()
        # Build the InputDocument wrapper with a real Path (required for
        # internal hash computation + size checks).
        in_doc = InputDocument(
            path_or_stream=path,
            format=InputFormat.DOCX,
            filename=str(path),
            backend=MsWordDocumentBackend,
        )
        backend = MsWordDocumentBackend(in_doc=in_doc, path_or_stream=path)
        if not backend.is_valid():
            raise RuntimeError(f"Docling backend failed to initialise for {path.name}")
        docling_doc = backend.convert()
        elapsed = time.perf_counter() - t0

        headings: List[tuple[int, str]] = []
        paragraphs: List[tuple[str, str]] = []
        tables: List[ExtractedTable] = []
        images: List[ExtractedImage] = []
        current_heading = ""

        for item, _ in docling_doc.iterate_items():
            item_type = type(item).__name__

            if item_type in ("SectionHeaderItem", "HeadingItem"):
                level = getattr(item, "level", 1) or 1
                text = item.text.strip() if hasattr(item, "text") else str(item)
                headings.append((level, text))
                current_heading = text

            elif item_type == "TextItem":
                text = item.text.strip() if hasattr(item, "text") else ""
                if text:
                    if text.strip().lower() in (
                        "document prepared by",
                        "document revision history",
                        "revision history",
                    ):
                        level = 1
                        headings.append((level, text))
                        current_heading = text
                    else:
                        paragraphs.append((current_heading, text))

            elif item_type == "TableItem":
                try:
                    df = item.export_to_dataframe()
                    rows = [
                        [str(c) for c in row]
                        for row in [list(df.columns)] + df.values.tolist()
                    ]
                    tables.append(ExtractedTable(heading=current_heading, rows=rows))
                except Exception:
                    raw_rows: List[List[str]] = []
                    for row in getattr(item, "data", {}).get("table_cells", []):
                        raw_rows.append([cell.get("text", "") for cell in row])
                    if raw_rows:
                        tables.append(
                            ExtractedTable(heading=current_heading, rows=raw_rows)
                        )

            elif item_type == "PictureItem":
                alt = getattr(item, "caption", "") or ""
                images.append(
                    ExtractedImage(heading=current_heading, alt_text=str(alt))
                )

        return ExtractedDoc(
            source_path=str(path.resolve()),
            backend_name=self.name,
            headings=headings,
            paragraphs=paragraphs,
            tables=tables,
            images=images,
            elapsed_s=elapsed,
            metadata={
                "page_count": None,
                "docling_version": getattr(__import__("docling"), "__version__", None),
                "backend": "MsWordDocumentBackend (direct, R15 lean path)",
                "status": "ok",
            },
        )


# ---------------------------------------------------------------------------
# MammothBackend  (mammoth docx→html/markdown)
# ---------------------------------------------------------------------------


class MammothBackend:
    """Extraction backend wrapping the ``mammoth`` library.

    ``mammoth`` converts ``.docx`` to clean HTML/Markdown by reading the
    underlying WordprocessingML directly. It is the same library mineru
    uses internally for its docx path (via the mammoth back-end), so
    adding it as a separate backend gives us a clean baseline of just
    the docx→html conversion without the vlm/pdf side.

    Why include mammoth as its own backend (and not just rely on mineru)?
    - ``mammoth`` is **~10 MB** vs mineru's ~80 MB.
    - ``mammoth`` does not promote bold list items to headings (the bug
      we had to filter out of mineru's R14 output).
    - ``mammoth`` is **fast** — it is a pure-Python docx XML walker with
      no ML inference.
    - It is the same library mineru is built on, so cross-checking the
      two helps validate the docx-parsing primitive.

    Outputs:
    - Headings: from ``convert_to_markdown()`` — preserves the Word
      ``Heading 1/2/3/4`` style mapping to ``#``/``##``/``###``/``####``.
    - Tables: from ``convert_to_html()`` — emits ``<table>`` blocks; the
      shared ``UnstructuredBackend._parse_html_table`` parses them.
    - Images: extracted from the data: URIs in the HTML output and
      surfaced as ``ExtractedImage`` (heading = nearest preceding
      heading at the time of encounter).
    - Paragraphs: paragraphs from the markdown walk; the nearest
      preceding heading sets the parent.

    Install::

        uv sync --group benchmark

    References
    ----------
    - https://github.com/mwilliamson/python-mammoth
    """

    name: str = "mammoth"

    def is_available(self) -> bool:
        try:
            import mammoth  # noqa: F401

            return True
        except ImportError:
            return False

    def extract(self, docx_path: str | Path) -> ExtractedDoc:
        """Extract using ``mammoth`` (markdown for headings, HTML for tables)."""
        try:
            import mammoth
            from html.parser import HTMLParser
            import base64
            import re
        except ImportError as exc:
            raise ImportError(
                "mammoth is not installed. Run: uv sync --group benchmark"
            ) from exc

        path = Path(docx_path)
        if not path.exists():
            raise FileNotFoundError(f"docx not found: {path}")

        t0 = time.perf_counter()
        with open(path, "rb") as f:
            md_result = mammoth.convert_to_markdown(f)
            f.seek(0)
            html_result = mammoth.convert_to_html(f)
        md_text = md_result.value
        html_text = html_result.value
        elapsed = time.perf_counter() - t0

        # --- Pass 1: walk the markdown for headings + paragraphs ---
        headings: List[tuple[int, str]] = []
        paragraphs: List[tuple[str, str]] = []
        current_heading = ""
        in_md_table = False
        md_table_buffer: List[str] = []

        for line in md_text.splitlines():
            stripped = line.strip()

            # Heading: #, ##, ###, etc.
            hm = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if hm:
                if in_md_table and md_table_buffer:
                    rows = self._parse_md_table(md_table_buffer)
                    if rows:
                        # Will be appended after HTML pass; track in a side list
                        pass
                    md_table_buffer = []
                    in_md_table = False
                level = len(hm.group(1))
                # Strip Word's <a id="..."> bookmark anchors
                text = re.sub(r'<a id="[^"]*"></a>', "", hm.group(2)).strip()
                # R14 helper: normalize bold + chapter-number prefixes
                text = ExtractedDoc.normalize_heading_text(text)
                # Skip empty-heading artifacts (e.g. mammoth's "## " with
                # only a stripped bookmark)
                if text:
                    headings.append((level, text))
                    current_heading = text
                continue

            # Pipe-table row (rare for mammoth default, but support it)
            if stripped.startswith("|"):
                in_md_table = True
                md_table_buffer.append(line)
                continue
            if in_md_table and not stripped.startswith("|"):
                if md_table_buffer:
                    md_table_buffer = []
                in_md_table = False

            if stripped and not stripped.startswith("---"):
                if stripped.strip().lower() in (
                    "document prepared by",
                    "document revision history",
                    "revision history",
                ):
                    level = 1
                    headings.append((level, stripped))
                    current_heading = stripped
                else:
                    paragraphs.append((current_heading, stripped))

        # --- Pass 2: walk the HTML for tables + images ---
        # We use a single HTMLParser that emits (tag, attrs) events; the
        # caller processes them in document order.
        tables: List[ExtractedTable] = []
        images: List[ExtractedImage] = []

        class _DocParser(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self._row: List[str] = []
                self._rows: List[List[str]] = []
                self._in_cell = False
                self._cell_text = ""
                self._in_table = False
                self._in_img = False
                self._img_alt = ""
                self._img_src = ""

            def handle_starttag(self, tag: str, attrs) -> None:
                a = dict(attrs)
                if tag == "table":
                    self._in_table = True
                elif tag in ("td", "th") and self._in_table:
                    self._in_cell = True
                    self._cell_text = ""
                elif tag == "tr" and self._in_table:
                    self._row = []
                elif tag == "img" and not self._in_table:
                    self._in_img = True
                    self._img_alt = a.get("alt", "")
                    self._img_src = a.get("src", "")

            def handle_endtag(self, tag: str) -> None:
                if tag in ("td", "th") and self._in_table:
                    self._row.append(self._cell_text.strip())
                    self._in_cell = False
                elif tag == "tr" and self._in_table:
                    if self._row:
                        self._rows.append(self._row)
                elif tag == "table" and self._in_table:
                    if self._rows:
                        tables.append(
                            ExtractedTable(heading=current_heading, rows=self._rows)
                        )
                    self._rows = []
                    self._in_table = False
                elif tag == "img" and self._in_img:
                    if self._img_src.startswith("data:"):
                        try:
                            header, b64 = self._img_src.split(",", 1)
                            data = base64.b64decode(b64)
                        except Exception:
                            data = None
                    else:
                        data = None
                    images.append(
                        ExtractedImage(
                            heading=current_heading,
                            alt_text=self._img_alt or "(image)",
                            data=data,
                        )
                    )
                    self._in_img = False

            def handle_data(self, data: str) -> None:
                if self._in_cell:
                    self._cell_text += data

        parser = _DocParser()
        # Strip the mammoth <a> bookmark anchors for clean parsing
        clean_html = re.sub(r'<a id="[^"]*"></a>', "", html_text)
        parser.feed(clean_html)

        return ExtractedDoc(
            source_path=str(path.resolve()),
            backend_name=self.name,
            headings=headings,
            paragraphs=paragraphs,
            tables=tables,
            images=images,
            elapsed_s=elapsed,
            metadata={
                "mammoth_messages": [
                    {"type": str(m.type), "message": m.message}
                    for m in md_result.messages[:10]
                ],
                "mammoth_html_chars": len(html_text),
                "mammoth_md_chars": len(md_text),
            },
        )

    @staticmethod
    def _parse_md_table(lines: List[str]) -> List[List[str]]:
        """Parse Markdown pipe-table lines (mirrors MinerUBackend._parse_md_table)."""
        rows: List[List[str]] = []
        for line in lines:
            stripped = line.strip()
            if (
                set(stripped.replace("|", "").replace("-", "").replace(" ", ""))
                == set()
            ):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells:
                rows.append(cells)
        return rows


# ---------------------------------------------------------------------------
# MinerUBackend  (OpenDataLab)
# ---------------------------------------------------------------------------


class MinerUBackend:
    """Extraction backend wrapping OpenDataLab's MinerU.

    Uses ``mineru.cli.common.do_parse`` (the modern v3+ entry point -- the
    older ``mineru.api.MinerU`` class was removed in v3.x) to convert the
    ``.docx`` file to Markdown + a structured content-list JSON, then
    normalises them into ``ExtractedDoc``.

    MinerU is designed for LLM/RAG-ready extraction, with:
    - Native DOCX/PPTX/XLSX parsing (no PDF conversion step for .docx)
    - Table-to-Markdown/HTML conversion
    - Formula detection (LaTeX output)
    - CPU (pipeline) and GPU (vlm) backend selection

    Install::

        uv sync --group benchmark

    References
    ----------
    - https://opendatalab.github.io/MinerU/
    - https://github.com/opendatalab/MinerU
    """

    name: str = "mineru"

    def is_available(self) -> bool:
        try:
            from mineru.cli.common import do_parse  # noqa: F401

            return True
        except ImportError:
            return False

    def extract(self, docx_path: str | Path) -> ExtractedDoc:
        """Extract using MinerU's ``pipeline`` backend (CPU-safe)."""
        try:
            from mineru.cli.common import do_parse
        except ImportError as exc:
            raise ImportError(
                "MinerU is not installed. Run: uv sync --group benchmark"
            ) from exc

        path = Path(docx_path)
        if not path.exists():
            raise FileNotFoundError(f"docx not found: {path}")

        import json
        import tempfile

        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_bytes = path.read_bytes()
            try:
                do_parse(
                    output_dir=tmp_dir,
                    pdf_file_names=[path.name],
                    pdf_bytes_list=[file_bytes],
                    p_lang_list=["en"],
                    backend="pipeline",
                    parse_method="auto",
                    formula_enable=False,
                    table_enable=True,
                    f_draw_layout_bbox=False,
                    f_draw_span_bbox=False,
                    f_dump_md=True,
                    f_dump_middle_json=True,
                    f_dump_model_output=False,
                    f_dump_orig_pdf=False,
                    f_dump_content_list=True,
                    image_analysis=False,
                )
            except Exception as exc:
                # mineru prints 'No valid PDF or image files' for office
                # docs but still writes the markdown + content list. Don't
                # let that warning propagate as an error.
                if "No valid PDF or image" not in str(exc):
                    raise

            # mineru writes to <tmp>/<docname>/office/<docname>.md
            md_path = next(Path(tmp_dir).rglob(f"{path.stem}*.md"), None)
            json_path = next(
                Path(tmp_dir).rglob(f"{path.stem}*content_list*.json"), None
            )
            images_dir = next(Path(tmp_dir).rglob("images"), None)
            md_text = md_path.read_text(encoding="utf-8") if md_path else ""
            json_data: Any = []
            if json_path:
                try:
                    json_data = json.loads(json_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            images: List[ExtractedImage] = []
            if images_dir and images_dir.is_dir():
                # Walk the per-image file list, look up their caption/heading
                # from the content_list json (each item with type=='image'
                # has a 'text' caption and the nearest preceding heading).
                img_lookup: Dict[str, str] = {}
                if isinstance(json_data, list):
                    last_heading = ""
                    for item in json_data:
                        if not isinstance(item, dict):
                            continue
                        itype = item.get("type", "")
                        if itype in ("title", "heading"):
                            last_heading = item.get("text", "").strip()
                        if itype in ("image", "figure"):
                            cap = item.get("text", "").strip()
                            img_path = item.get("img_path", "")
                            if img_path:
                                img_lookup[img_path] = (
                                    f"{last_heading}: {cap}" if cap else last_heading
                                )
                for img_file in sorted(images_dir.iterdir()):
                    if not img_file.is_file():
                        continue
                    try:
                        data = img_file.read_bytes()
                    except Exception:
                        continue
                    # Use the first known image heading; fall back to "".
                    heading = next(iter(img_lookup.values()), "")
                    if img_file.name in img_lookup:
                        heading = img_lookup[img_file.name]
                    images.append(
                        ExtractedImage(
                            heading=heading, alt_text=img_file.name, data=data
                        )
                    )

        elapsed = time.perf_counter() - t0

        headings, paragraphs, tables = self._parse_markdown(md_text)

        # R14: mineru emits headings with bold markers and chapter-number
        # prefixes (e.g. ``"10.4 **Data Flow**"``). Normalize via the
        # shared helper so the contract `sourceHeading` patterns match.
        headings = [
            (lvl, ExtractedDoc.normalize_heading_text(txt)) for lvl, txt in headings
        ]

        if isinstance(json_data, dict):
            json_keys: List[str] = list(json_data.keys())
        elif isinstance(json_data, list):
            json_keys = [f"list[{len(json_data)}]"]
        else:
            json_keys = []
        return ExtractedDoc(
            source_path=str(path.resolve()),
            backend_name=self.name,
            headings=headings,
            paragraphs=paragraphs,
            tables=tables,
            images=images,
            elapsed_s=elapsed,
            metadata={
                "json_keys": json_keys,
                "md_chars": len(md_text),
            },
        )

    @staticmethod
    def _parse_markdown(
        md_text: str,
    ) -> tuple[List[tuple[int, str]], List[tuple[str, str]], List[ExtractedTable]]:
        """Parse MinerU Markdown output into heading/paragraph/table lists.

        MinerU emits docx tables as either:
        - GitHub-flavored Markdown pipe tables (``| col | col |``)
        - HTML ``<table>...</table>`` blocks (default for docx)

        Both forms are recognized and converted to ``ExtractedTable`` rows.
        """
        import re

        headings: List[tuple[int, str]] = []
        paragraphs: List[tuple[str, str]] = []
        tables: List[ExtractedTable] = []
        current_heading = ""
        table_buffer: List[str] = []
        in_table = False
        html_table_buffer: List[str] = []
        in_html_table = False

        for line in md_text.splitlines():
            stripped = line.strip()

            # Heading
            hm = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if hm:
                # Flush any open pipe-table
                if in_table and table_buffer:
                    tables.append(
                        ExtractedTable(
                            heading=current_heading,
                            rows=MinerUBackend._parse_md_table(table_buffer),
                        )
                    )
                    table_buffer = []
                    in_table = False
                # Flush any open HTML-table
                if in_html_table and html_table_buffer:
                    html_text = " ".join(html_table_buffer)
                    rows = UnstructuredBackend._parse_html_table(html_text)
                    if rows:
                        tables.append(
                            ExtractedTable(heading=current_heading, rows=rows)
                        )
                    html_table_buffer = []
                    in_html_table = False
                level = len(hm.group(1))
                text = hm.group(2).strip()
                # R14: filter mineru false-positive headings. The mammoth
                # back-end sometimes promotes bold list items to H3/H5
                # when they look like titles. Real FedRAMP headings are
                # bold (wrapped in **...**) and have a noun phrase, not a
                # full sentence ending in `;*` or `; and`.
                if _is_likely_sentence(text):
                    # Don't promote to a heading; treat as a paragraph.
                    paragraphs.append((current_heading, text))
                    continue
                headings.append((level, text))
                current_heading = text
                continue

            # HTML table open
            if not in_html_table and stripped.startswith("<table"):
                in_html_table = True
                html_table_buffer = [line]
                # Handle the case where </table> is on the same line
                if "</table>" in line:
                    rows = UnstructuredBackend._parse_html_table(
                        " ".join(html_table_buffer)
                    )
                    if rows:
                        tables.append(
                            ExtractedTable(heading=current_heading, rows=rows)
                        )
                    html_table_buffer = []
                    in_html_table = False
                continue
            if in_html_table:
                html_table_buffer.append(line)
                if "</table>" in line:
                    rows = UnstructuredBackend._parse_html_table(
                        " ".join(html_table_buffer)
                    )
                    if rows:
                        tables.append(
                            ExtractedTable(heading=current_heading, rows=rows)
                        )
                    html_table_buffer = []
                    in_html_table = False
                continue

            # Pipe-table row
            if stripped.startswith("|"):
                in_table = True
                table_buffer.append(line)
                continue

            # End of pipe-table block
            if in_table and not stripped.startswith("|"):
                if table_buffer:
                    tables.append(
                        ExtractedTable(
                            heading=current_heading,
                            rows=MinerUBackend._parse_md_table(table_buffer),
                        )
                    )
                    table_buffer = []
                in_table = False

            if stripped and not stripped.startswith("---"):
                if stripped.strip().lower() in (
                    "document prepared by",
                    "document revision history",
                    "revision history",
                ):
                    level = 1
                    headings.append((level, stripped))
                    current_heading = stripped
                else:
                    paragraphs.append((current_heading, stripped))

        # Flush any trailing pipe-table
        if in_table and table_buffer:
            tables.append(
                ExtractedTable(
                    heading=current_heading,
                    rows=MinerUBackend._parse_md_table(table_buffer),
                )
            )
        # Flush any trailing HTML-table
        if in_html_table and html_table_buffer:
            rows = UnstructuredBackend._parse_html_table(" ".join(html_table_buffer))
            if rows:
                tables.append(ExtractedTable(heading=current_heading, rows=rows))

        return headings, paragraphs, tables

    @staticmethod
    def _parse_md_table(lines: List[str]) -> List[List[str]]:
        """Parse Markdown pipe-table lines into a list of cell-string rows."""
        rows = []
        for line in lines:
            stripped = line.strip()
            # Skip separator rows (---|---|---)
            if (
                set(stripped.replace("|", "").replace("-", "").replace(" ", ""))
                == set()
            ):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells:
                rows.append(cells)
        return rows


# ---------------------------------------------------------------------------
# UnstructuredBackend  (Unstructured-IO)
# ---------------------------------------------------------------------------


class UnstructuredBackend:
    """Extraction backend wrapping Unstructured-IO.

    Uses ``unstructured.partition.docx.partition_docx`` to parse the ``.docx``
    file into a list of typed ``Element`` objects (``Title``, ``NarrativeText``,
    ``Table``, ``Image``, etc.), then normalises them into ``ExtractedDoc``.

    Unstructured provides:
    - Pre-trained layout models for heading/paragraph classification
    - Table extraction to HTML (``include_metadata=True``)
    - Image captions (when OCR is enabled)
    - Document-level and element-level metadata

    **R14 equal-footing customization**: the default ``partition_docx`` call
    only promotes ``Title`` style to a heading, which misses all
    ``Heading 1/2/3/4`` paragraph styles used by the FedRAMP SSP template.
    To produce a fair comparison with fismatic/mineru, this backend
    supplements the partition_docx output with:

    - python-docx reads of paragraph styles to add missing H1–H4 headings.
    - XML walk via ``detect_image_rids_in_paragraph`` to enumerate inline
      images (partition_docx with default options reports zero images).

    Install::

        uv sync --group benchmark

    References
    ----------
    - https://github.com/Unstructured-IO/unstructured
    - https://docs.unstructured.io/api-reference/partition/partition-docx
    """

    name: str = "unstructured"

    def is_available(self) -> bool:
        try:
            from unstructured.partition.docx import partition_docx  # noqa: F401
            import docx  # noqa: F401

            return True
        except ImportError:
            return False

    def extract(self, docx_path: str | Path) -> ExtractedDoc:
        """Extract using ``unstructured.partition.docx.partition_docx``.

        Augments the default partition_docx output with python-docx-based
        heading detection and inline image enumeration. See class docstring.
        """
        try:
            from unstructured.partition.docx import partition_docx
            import docx as python_docx
        except ImportError as exc:
            raise ImportError(
                "unstructured is not installed. Run: uv sync --group benchmark"
            ) from exc

        path = Path(docx_path)
        if not path.exists():
            raise FileNotFoundError(f"docx not found: {path}")

        t0 = time.perf_counter()
        elements = partition_docx(filename=str(path), include_metadata=True)
        # Also open via python-docx for heading-style + image detection.
        docx_doc = python_docx.Document(str(path))
        elapsed = time.perf_counter() - t0

        # --- Pass 1: collect python-docx headings (merge with unstructured later) ---
        pydocx_headings: List[tuple[int, str]] = []
        pydocx_images: List[ExtractedImage] = []
        current_heading = ""

        for kind, block, style_name in iter_docx_blocks_with_style(docx_doc):
            if kind == "p":
                para = block
                text = para.text.strip()
                if not text:
                    for _ in detect_image_rids_in_paragraph(para):
                        pydocx_images.append(
                            ExtractedImage(heading=current_heading, alt_text="")
                        )
                    continue
                level = heading_level_from_style(style_name)
                if text.strip().lower() in (
                    "document prepared by",
                    "document revision history",
                    "revision history",
                ):
                    level = 1
                if level is not None:
                    pydocx_headings.append((level, text))
                    current_heading = text
                else:
                    for _ in detect_image_rids_in_paragraph(para):
                        pydocx_images.append(
                            ExtractedImage(heading=current_heading, alt_text="")
                        )
            elif kind == "tbl":
                pass  # tables handled from unstructured stream below

        # --- Pass 2: walk unstructured elements, merging with python-docx headings ---
        headings: List[tuple[int, str]] = []
        # Index pydocx_headings by normalized text for fast lookup
        from collections import defaultdict

        pydocx_by_text: Dict[str, List[tuple[int, str]]] = defaultdict(list)
        for lvl, txt in pydocx_headings:
            pydocx_by_text[ExtractedDoc.normalize_heading_text(txt)].append((lvl, txt))

        paragraphs: List[tuple[str, str]] = []
        tables: List[ExtractedTable] = []
        images: List[ExtractedImage] = list(pydocx_images)
        cur_heading = ""

        # Element type names in unstructured:
        #   Title, NarrativeText, ListItem, Table, Image, Header, Footer,
        #   FigureCaption, PageBreak, Formula, CodeSnippet, ...
        for element in elements:
            etype = type(element).__name__
            text = str(element).strip()

            if etype == "Title":
                # Unstructured-detected Title — may overlap with a python-docx
                # heading. Use the python-docx level if available (more
                # accurate), else default to 1.
                depth = getattr(
                    getattr(element, "metadata", None), "category_depth", None
                )
                level = int(depth) + 1 if depth is not None else 1
                if text:
                    norm = ExtractedDoc.normalize_heading_text(text)
                    if norm in pydocx_by_text:
                        level = pydocx_by_text[norm][0][0]
                    headings.append((level, text))
                    cur_heading = text

            elif etype in ("NarrativeText", "ListItem", "Header", "Footer", "Text"):
                if text:
                    if text.strip().lower() in (
                        "document prepared by",
                        "document revision history",
                        "revision history",
                    ):
                        level = 1
                        headings.append((level, text))
                        cur_heading = text
                    else:
                        paragraphs.append((cur_heading, text))

            elif etype == "Table":
                # Table text is the full cell content joined by whitespace.
                # Build a single-row representation; real HTML is in metadata.
                html = getattr(getattr(element, "metadata", None), "text_as_html", None)
                rows = self._parse_html_table(html) if html else [[text]]
                if rows:
                    tables.append(ExtractedTable(heading=cur_heading, rows=rows))

            elif etype in ("Image", "FigureCaption"):
                if text:
                    images.append(ExtractedImage(heading=cur_heading, alt_text=text))

        # Append python-docx-only headings that unstructured did not emit
        # (e.g. Heading 1/2/3 paragraphs that partition_docx classified as Text).
        existing_norm = {ExtractedDoc.normalize_heading_text(t) for _, t in headings}
        for lvl, txt in pydocx_headings:
            if ExtractedDoc.normalize_heading_text(txt) not in existing_norm:
                headings.append((lvl, txt))
                existing_norm.add(ExtractedDoc.normalize_heading_text(txt))

        # Re-sort headings by document order (python-docx walk order is the
        # canonical order since we walk the body sequentially).
        # The simplest proxy: keep the original order in which they appear
        # across both passes. Both passes walk the docx top-to-bottom, so
        # concatenating them in walk order is already correct.

        return ExtractedDoc(
            source_path=str(path.resolve()),
            backend_name=self.name,
            headings=headings,
            paragraphs=paragraphs,
            tables=tables,
            images=images,
            elapsed_s=elapsed,
            metadata={
                "element_count": len(elements),
                "element_types": list(
                    dict.fromkeys(type(e).__name__ for e in elements)
                ),
                "pydocx_heading_count": len(pydocx_headings),
                "pydocx_image_count": len(pydocx_images),
            },
        )

    @staticmethod
    def _parse_html_table(html: str) -> List[List[str]]:
        """Parse an HTML table string into a list of cell-string rows.

        Falls back to an empty list if ``html.parser`` cannot parse it.
        """
        try:
            from html.parser import HTMLParser

            class _TableParser(HTMLParser):
                def __init__(self) -> None:
                    super().__init__()
                    self.rows: List[List[str]] = []
                    self._row: List[str] = []
                    self._cell = ""
                    self._in_cell = False

                def handle_starttag(self, tag: str, attrs: Any) -> None:
                    if tag in ("tr",):
                        self._row = []
                    elif tag in ("td", "th"):
                        self._in_cell = True
                        self._cell = ""

                def handle_endtag(self, tag: str) -> None:
                    if tag in ("td", "th"):
                        self._row.append(self._cell.strip())
                        self._in_cell = False
                    elif tag == "tr":
                        if self._row:
                            self.rows.append(self._row)

                def handle_data(self, data: str) -> None:
                    if self._in_cell:
                        self._cell += data

            parser = _TableParser()
            parser.feed(html)
            return parser.rows
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, type] = {
    "fismatic": FismaticBackend,
    "docling": DoclingBackend,
    "mineru": MinerUBackend,
    "unstructured": UnstructuredBackend,
    "mammoth": MammothBackend,
}


def get_backend(name: str) -> ExtractorBackend:
    """Return a backend instance by name.

    Parameters
    ----------
    name:
        One of ``"fismatic"``, ``"docling"``, ``"mineru"``, or
        ``"unstructured"``.

    Raises
    ------
    ValueError
        If ``name`` is not a registered backend.
    """
    if name not in _REGISTRY:
        raise ValueError(f"Unknown backend {name!r}. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def list_backends() -> List[str]:
    """Return all registered backend names."""
    return sorted(_REGISTRY)
