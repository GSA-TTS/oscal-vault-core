"""Tests for the extractor backend abstraction (Phase R12).

Tests are divided into two groups:

1. **Protocol & API tests** (no mark) — verify the backend protocol,
   ``ExtractedDoc`` helpers, and registry.  These pass without any .docx
   fixtures or optional deps.

2. **Integration tests** (``@pytest.mark.r12``) — exercise real extraction
   with a minimal synthetic docx.  Deselected by default; run explicitly:

::

    uv run pytest compliance/tests/test_extractor_backend.py -m r12 -v

"""

from __future__ import annotations

import os

import io
from pathlib import Path

import pytest

from compliance.lib.extractor_backend import (
    DoclingBackend,
    ExtractedDoc,
    ExtractedImage,
    ExtractedTable,
    FismaticBackend,
    MammothBackend,
    MinerUBackend,
    UnstructuredBackend,
    get_backend,
    list_backends,
)
from compliance.lib.unified_converter import DocumentTree


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def minimal_docx_bytes() -> bytes:
    """Return bytes of a minimal valid .docx with a heading, paragraph, table."""
    try:
        import docx

        doc = docx.Document()
        doc.add_heading("System Overview", level=1)
        doc.add_paragraph("This is the system description.")
        doc.add_heading("Access Control", level=2)
        doc.add_paragraph("AC paragraph text.")
        table = doc.add_table(rows=2, cols=3)
        table.cell(0, 0).text = "Port"
        table.cell(0, 1).text = "Protocol"
        table.cell(0, 2).text = "Purpose"
        table.cell(1, 0).text = "443"
        table.cell(1, 1).text = "TCP"
        table.cell(1, 2).text = "HTTPS"
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    except ImportError:
        pytest.skip("python-docx not available")


# ---------------------------------------------------------------------------
# ExtractedDoc data model tests
# ---------------------------------------------------------------------------


class TestExtractedDoc:
    def test_heading_count(self):
        doc = ExtractedDoc(
            source_path="/fake.docx",
            backend_name="test",
            headings=[(1, "H1"), (2, "H2")],
            paragraphs=[("H1", "Para")],
        )
        assert doc.heading_count() == 2

    def test_table_count(self):
        doc = ExtractedDoc(
            source_path="/fake.docx",
            backend_name="test",
            headings=[],
            paragraphs=[],
            tables=[ExtractedTable(heading="H1", rows=[["a", "b"]])],
        )
        assert doc.table_count() == 1

    def test_paragraph_count(self):
        doc = ExtractedDoc(
            source_path="/fake.docx",
            backend_name="test",
            headings=[(1, "H1")],
            paragraphs=[("H1", "Para 1"), ("H1", "Para 2")],
        )
        assert doc.paragraph_count() == 2

    def test_image_count(self):
        doc = ExtractedDoc(
            source_path="/fake.docx",
            backend_name="test",
            headings=[],
            paragraphs=[],
            images=[ExtractedImage(heading="H1", alt_text="diagram")],
        )
        assert doc.image_count() == 1

    def test_to_document_tree_returns_tree(self):
        doc = ExtractedDoc(
            source_path="/fake.docx",
            backend_name="test",
            headings=[(1, "Overview"), (2, "Access Control")],
            paragraphs=[("Overview", "System description.")],
        )
        tree = doc.to_document_tree()
        assert isinstance(tree, DocumentTree)
        headings = tree.headings()
        assert len(headings) == 2
        assert headings[0].text == "Overview"
        assert headings[0].level == 1

    def test_to_document_tree_attaches_paragraphs(self):
        doc = ExtractedDoc(
            source_path="/fake.docx",
            backend_name="test",
            headings=[(1, "Overview")],
            paragraphs=[("Overview", "Para 1"), ("Overview", "Para 2")],
        )
        tree = doc.to_document_tree()
        overview = tree.headings()[0]
        paras = [c for c in overview.children if c.kind == "paragraph"]
        assert len(paras) == 2

    def test_to_document_tree_attaches_tables(self):
        doc = ExtractedDoc(
            source_path="/fake.docx",
            backend_name="test",
            headings=[(2, "Ports")],
            paragraphs=[],
            tables=[ExtractedTable(heading="Ports", rows=[["443", "TCP"]])],
        )
        tree = doc.to_document_tree()
        ports = tree.headings()[0]
        tbl_children = [c for c in ports.children if c.kind == "table"]
        assert len(tbl_children) == 1
        assert tbl_children[0].raw == [["443", "TCP"]]

    def test_defaults(self):
        doc = ExtractedDoc(
            source_path="/f.docx",
            backend_name="test",
            headings=[],
            paragraphs=[],
        )
        assert doc.tables == []
        assert doc.images == []
        assert doc.elapsed_s == 0.0
        assert doc.metadata == {}


class TestExtractedDocNormalize:
    """Tests for ExtractedDoc heading-normalization helpers (R14)."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("10.4 Data Flow", "Data Flow"),
            ("13 Minimum Security Controls", "Minimum Security Controls"),
            ("  4.2 Information Types  ", "Information Types"),
            ("10 **System Environment**", "System Environment"),
            ("**AWS Management**", "AWS Management"),
            ("System Data Nature", "System Data Nature"),
            ("", ""),
        ],
    )
    def test_normalize_heading_text(self, raw, expected):
        assert ExtractedDoc.normalize_heading_text(raw) == expected

    def test_normalized_headings_filters_by_level(self):
        doc = ExtractedDoc(
            source_path="/f.docx",
            backend_name="test",
            headings=[(1, "10 H1"), (2, "10.1 H2"), (1, "11 Next H1")],
            paragraphs=[],
        )
        h1 = doc.normalized_headings(level=1)
        h2 = doc.normalized_headings(level=2)
        assert h1 == [(1, "H1"), (1, "Next H1")]
        assert h2 == [(2, "H2")]

    def test_heading_text_set(self):
        doc = ExtractedDoc(
            source_path="/f.docx",
            backend_name="test",
            headings=[(1, "10 H1"), (1, "10 H1"), (2, "10.1 H2")],
            paragraphs=[],
        )
        # Deduplicates, normalizes
        assert doc.heading_text_set() == {"H1", "H2"}

    def test_heading_recall_full_overlap(self):
        a = ExtractedDoc(
            source_path="/a.docx",
            backend_name="a",
            headings=[(1, "H1"), (1, "H2")],
            paragraphs=[],
        )
        b = ExtractedDoc(
            source_path="/b.docx",
            backend_name="b",
            headings=[(1, "H1"), (1, "H2")],
            paragraphs=[],
        )
        assert a.heading_recall(b) == 1.0

    def test_heading_recall_partial(self):
        a = ExtractedDoc(
            source_path="/a.docx",
            backend_name="a",
            headings=[(1, "H1")],
            paragraphs=[],
        )
        b = ExtractedDoc(
            source_path="/b.docx",
            backend_name="b",
            headings=[(1, "H1"), (1, "H2")],
            paragraphs=[],
        )
        # a has 1 of b's 2 headings = 0.5
        assert a.heading_recall(b) == 0.5

    def test_heading_recall_normalizes_first(self):
        # mineru emits "10 **H1**", fismatic emits "H1" — after normalize both
        # match, so recall = 1.0
        a = ExtractedDoc(
            source_path="/a.docx",
            backend_name="mineru",
            headings=[(1, "10 **H1**")],
            paragraphs=[],
        )
        b = ExtractedDoc(
            source_path="/b.docx",
            backend_name="fismatic",
            headings=[(1, "H1")],
            paragraphs=[],
        )
        assert a.heading_recall(b) == 1.0

    def test_heading_recall_empty_other(self):
        a = ExtractedDoc(
            source_path="/a.docx",
            backend_name="a",
            headings=[(1, "H1")],
            paragraphs=[],
        )
        b = ExtractedDoc(
            source_path="/b.docx",
            backend_name="b",
            headings=[],
            paragraphs=[],
        )
        assert a.heading_recall(b) == 0.0


class TestEqualFooting:
    """R14: verify all 3 backends produce parity on a real SSP.

    These tests are skipped if a backend is unavailable.
    """

    REAL_SSP = os.environ.get(
        "OSCAL_VAULT_SAMPLE_LOW_SSP", "/nonexistent/sample-low-ssp.docx"
    )

    @pytest.fixture(autouse=True)
    def _require_real_ssp(self):
        # The real SSP .docx is gitignored (ADR-009); skip the whole class
        # gracefully on a bare checkout rather than erroring in each fixture.
        if not Path(self.REAL_SSP).exists():
            pytest.skip(f"real SSP fixture not present: {self.REAL_SSP}")

    @pytest.fixture
    def fismatic_doc(self):
        if not FismaticBackend().is_available():
            pytest.skip("fismatic not available")
        return FismaticBackend().extract(self.REAL_SSP)

    @pytest.fixture
    def mineru_doc(self):
        if not MinerUBackend().is_available():
            pytest.skip("mineru not available")
        return MinerUBackend().extract(self.REAL_SSP)

    @pytest.fixture
    def unstructured_doc(self):
        if not UnstructuredBackend().is_available():
            pytest.skip("unstructured not available")
        return UnstructuredBackend().extract(self.REAL_SSP)

    def test_fismatic_finds_at_least_30_headings(self, fismatic_doc):
        assert fismatic_doc.heading_count() >= 30

    def test_mineru_finds_at_least_30_headings(self, mineru_doc):
        assert mineru_doc.heading_count() >= 30

    def test_unstructured_finds_at_least_30_headings(self, unstructured_doc):
        # R14: was 2 before adding python-docx heading-style reads.
        assert unstructured_doc.heading_count() >= 30

    def test_unstructured_finds_at_least_one_image(self, unstructured_doc):
        # R14: was 0 before adding detect_image_rids_in_paragraph walk.
        assert unstructured_doc.image_count() >= 1

    def test_all_three_backends_recall_each_other_above_70_percent(
        self, fismatic_doc, mineru_doc, unstructured_doc
    ):
        # Pairwise recall after normalization should be at least 70%.
        for src, tgt in [
            (fismatic_doc, mineru_doc),
            (mineru_doc, fismatic_doc),
            (fismatic_doc, unstructured_doc),
            (unstructured_doc, fismatic_doc),
            (mineru_doc, unstructured_doc),
            (unstructured_doc, mineru_doc),
        ]:
            r = src.heading_recall(tgt)
            assert r >= 0.70, (
                f"Recall {src.backend_name}->{tgt.backend_name} is only {r:.2f}"
            )

    def test_no_sentence_promoted_to_heading_mineru(self, mineru_doc):
        # mineru's mammoth back-end sometimes promotes bold list items.
        # The _is_likely_sentence filter should remove them.
        for _lvl, txt in mineru_doc.headings:
            assert not txt.endswith(";*"), f"Bold-italic sentence in headings: {txt!r}"
            assert "; and" not in txt, f"List connector in headings: {txt!r}"

    def test_image_counts_agree(self, fismatic_doc, mineru_doc, unstructured_doc):
        # All 3 should find a similar number of inline images (the SSP has 4).
        # Allow ±1 variance because mineru also captures captions separately.
        counts = [
            fismatic_doc.image_count(),
            mineru_doc.image_count(),
            unstructured_doc.image_count(),
        ]
        assert min(counts) >= 1, f"All backends should find >= 1 image, got {counts}"


class TestExtractedTable:
    def test_basic(self):
        t = ExtractedTable(heading="Ports", rows=[["443", "TCP", "HTTPS"]])
        assert t.heading == "Ports"
        assert t.rows[0][2] == "HTTPS"

    def test_raw_optional(self):
        t = ExtractedTable(heading="H", rows=[])
        assert t.raw is None


class TestExtractedImage:
    def test_basic(self):
        img = ExtractedImage(heading="Diagrams", alt_text="Architecture")
        assert img.heading == "Diagrams"
        assert img.data is None


# ---------------------------------------------------------------------------
# Backend registry tests  (no fixtures needed)
# ---------------------------------------------------------------------------


class TestBackendRegistry:
    def test_list_backends_contains_all_four(self):
        backends = list_backends()
        assert "fismatic" in backends
        assert "docling" in backends
        assert "mineru" in backends
        assert "unstructured" in backends

    def test_get_fismatic(self):
        b = get_backend("fismatic")
        assert b.name == "fismatic"

    def test_get_docling(self):
        b = get_backend("docling")
        assert b.name == "docling"

    def test_get_mineru(self):
        b = get_backend("mineru")
        assert b.name == "mineru"

    def test_get_unstructured(self):
        b = get_backend("unstructured")
        assert b.name == "unstructured"

    def test_get_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            get_backend("__bad__")

    def test_fismatic_always_available(self):
        assert FismaticBackend().is_available() is True

    def test_optional_availability_does_not_raise(self):
        # is_available() must never raise, even when deps are absent
        for cls in (DoclingBackend, MinerUBackend, UnstructuredBackend, MammothBackend):
            result = cls().is_available()
            assert isinstance(result, bool)

    def test_all_satisfy_protocol(self):
        from compliance.lib.extractor_backend import ExtractorBackend

        for cls in (
            FismaticBackend,
            DoclingBackend,
            MinerUBackend,
            UnstructuredBackend,
            MammothBackend,
        ):
            assert isinstance(cls(), ExtractorBackend)


# ---------------------------------------------------------------------------
# MinerU Markdown parser unit tests  (no deps needed)
# ---------------------------------------------------------------------------


class TestMinerUMarkdownParser:
    def test_parses_headings(self):
        md = "# System Overview\n\nSome text.\n\n## Access Control\n"
        headings, paragraphs, tables = MinerUBackend._parse_markdown(md)
        assert headings[0] == (1, "System Overview")
        assert headings[1] == (2, "Access Control")

    def test_parses_paragraphs(self):
        md = "# H1\n\nParagraph text here.\n"
        _, paragraphs, _ = MinerUBackend._parse_markdown(md)
        assert any("Paragraph text" in p for _, p in paragraphs)

    def test_parses_table(self):
        md = "# Ports\n\n| Port | Protocol |\n|------|----------|\n| 443 | TCP |\n"
        _, _, tables = MinerUBackend._parse_markdown(md)
        assert len(tables) == 1
        # separator row excluded; should have header + data row
        assert any("443" in " ".join(row) for row in tables[0].rows)

    def test_empty_markdown(self):
        headings, paragraphs, tables = MinerUBackend._parse_markdown("")
        assert headings == []
        assert paragraphs == []
        assert tables == []


# ---------------------------------------------------------------------------
# Unstructured HTML table parser unit tests  (no deps needed)
# ---------------------------------------------------------------------------


class TestUnstructuredHTMLParser:
    def test_parses_simple_table(self):
        html = (
            "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        )
        rows = UnstructuredBackend._parse_html_table(html)
        assert rows[0] == ["A", "B"]
        assert rows[1] == ["1", "2"]

    def test_empty_html(self):
        rows = UnstructuredBackend._parse_html_table("")
        assert rows == []

    def test_malformed_html_returns_empty(self):
        rows = UnstructuredBackend._parse_html_table("not html at all <<<")
        assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# FismaticBackend integration tests  (@pytest.mark.r12)
# ---------------------------------------------------------------------------


@pytest.mark.r12
class TestFismaticBackendIntegration:
    def test_extract_returns_extracted_doc(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = FismaticBackend().extract(docx_path)
        assert isinstance(doc, ExtractedDoc)
        assert doc.backend_name == "fismatic"
        assert doc.elapsed_s > 0

    def test_extract_finds_h1_heading(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = FismaticBackend().extract(docx_path)
        texts = [t for _, t in doc.headings]
        assert any("System Overview" in t for t in texts)

    def test_extract_finds_table(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = FismaticBackend().extract(docx_path)
        assert doc.table_count() >= 1
        assert any("443" in " ".join(r) for tbl in doc.tables for r in tbl.rows)

    def test_extract_finds_paragraph(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = FismaticBackend().extract(docx_path)
        assert doc.paragraph_count() >= 1

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            FismaticBackend().extract(tmp_path / "missing.docx")

    def test_to_document_tree(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = FismaticBackend().extract(docx_path)
        tree = doc.to_document_tree()
        assert isinstance(tree, DocumentTree)
        assert len(tree.headings()) >= 1


# ---------------------------------------------------------------------------
# DoclingBackend integration tests  (@pytest.mark.r12)
# ---------------------------------------------------------------------------


@pytest.mark.r12
class TestDoclingBackendIntegration:
    @pytest.fixture(autouse=True)
    def skip_if_unavailable(self):
        if not DoclingBackend().is_available():
            pytest.skip("docling not installed (uv sync --group benchmark)")

    def test_extract_returns_extracted_doc(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = DoclingBackend().extract(docx_path)
        assert isinstance(doc, ExtractedDoc)
        assert doc.backend_name == "docling"

    def test_metadata_present(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = DoclingBackend().extract(docx_path)
        assert "status" in doc.metadata

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            DoclingBackend().extract(tmp_path / "missing.docx")

    def test_document_tree_compatible(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = DoclingBackend().extract(docx_path)
        tree = doc.to_document_tree()
        assert isinstance(tree, DocumentTree)


# ---------------------------------------------------------------------------
# MinerUBackend integration tests  (@pytest.mark.r12)
# ---------------------------------------------------------------------------


@pytest.mark.r12
class TestMinerUBackendIntegration:
    @pytest.fixture(autouse=True)
    def skip_if_unavailable(self):
        if not MinerUBackend().is_available():
            pytest.skip("mineru not installed (uv sync --group benchmark)")

    def test_extract_returns_extracted_doc(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = MinerUBackend().extract(docx_path)
        assert isinstance(doc, ExtractedDoc)
        assert doc.backend_name == "mineru"

    def test_metadata_has_md_chars(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = MinerUBackend().extract(docx_path)
        assert "md_chars" in doc.metadata

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            MinerUBackend().extract(tmp_path / "missing.docx")

    def test_document_tree_compatible(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = MinerUBackend().extract(docx_path)
        tree = doc.to_document_tree()
        assert isinstance(tree, DocumentTree)


# ---------------------------------------------------------------------------
# UnstructuredBackend integration tests  (@pytest.mark.r12)
# ---------------------------------------------------------------------------


@pytest.mark.r12
class TestUnstructuredBackendIntegration:
    @pytest.fixture(autouse=True)
    def skip_if_unavailable(self):
        if not UnstructuredBackend().is_available():
            pytest.skip("unstructured not installed (uv sync --group benchmark)")

    def test_extract_returns_extracted_doc(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = UnstructuredBackend().extract(docx_path)
        assert isinstance(doc, ExtractedDoc)
        assert doc.backend_name == "unstructured"

    def test_metadata_has_element_count(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = UnstructuredBackend().extract(docx_path)
        assert "element_count" in doc.metadata
        assert doc.metadata["element_count"] > 0

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            UnstructuredBackend().extract(tmp_path / "missing.docx")

    def test_document_tree_compatible(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = UnstructuredBackend().extract(docx_path)
        tree = doc.to_document_tree()
        assert isinstance(tree, DocumentTree)


# ---------------------------------------------------------------------------
# MammothBackend integration tests  (@pytest.mark.r12)
# ---------------------------------------------------------------------------


@pytest.mark.r12
class TestMammothBackendIntegration:
    @pytest.fixture(autouse=True)
    def skip_if_unavailable(self):
        if not MammothBackend().is_available():
            pytest.skip("mammoth not installed")

    def test_extract_returns_extracted_doc(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = MammothBackend().extract(docx_path)
        assert isinstance(doc, ExtractedDoc)
        assert doc.backend_name == "mammoth"

    def test_metadata_present(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = MammothBackend().extract(docx_path)
        assert "mammoth_messages" in doc.metadata
        assert "mammoth_html_chars" in doc.metadata

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            MammothBackend().extract(tmp_path / "missing.docx")

    def test_document_tree_compatible(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        doc = MammothBackend().extract(docx_path)
        tree = doc.to_document_tree()
        assert isinstance(tree, DocumentTree)


class TestMammothHeadingNormalization:
    """Unit tests for mammoth-specific normalization (markdown escapes)."""

    def test_normalize_strips_backslash_escapes(self):
        # mammoth escapes parens, dots, hyphens in markdown output.
        assert (
            ExtractedDoc.normalize_heading_text("Access Control \\(AC\\)")
            == "Access Control (AC)"
        )
        assert (
            ExtractedDoc.normalize_heading_text("N/A\\.  System is administered by GSA")
            == "N/A.  System is administered by GSA"
        )
        assert (
            ExtractedDoc.normalize_heading_text("Container\\-Based Workload Management")
            == "Container-Based Workload Management"
        )

    def test_normalize_strips_chapter_prefix_and_bold(self):
        # mineru's "10.4 **Data Flow**" form
        assert ExtractedDoc.normalize_heading_text("10.4 **Data Flow**") == "Data Flow"
        # mammoth's "Access Control \\(AC\\)" form
        assert (
            ExtractedDoc.normalize_heading_text("10 **Access Control \\(AC\\)**")
            == "Access Control (AC)"
        )


# ---------------------------------------------------------------------------
# Benchmark runner tests  (@pytest.mark.r12)
# ---------------------------------------------------------------------------


@pytest.mark.r12
class TestBenchmarkRunner:
    def test_fismatic_only_report(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())

        from compliance.scripts.benchmark_extractors import run_benchmark

        report = run_benchmark(
            docx_paths=[docx_path],
            backend_names=["fismatic"],
            baseline_name="fismatic",
        )
        assert report["document_count"] == 1
        agg = report["aggregates"]["fismatic"]
        assert agg["documents_ok"] == 1
        assert agg["mean_elapsed_s"] > 0
        assert agg["total_headings"] >= 1
        assert agg["total_tables"] >= 1

    def test_markdown_report_renders(self, tmp_path):
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())

        from compliance.scripts.benchmark_extractors import (
            render_markdown_report,
            run_benchmark,
        )

        report = run_benchmark(
            docx_paths=[docx_path],
            backend_names=["fismatic"],
            baseline_name="fismatic",
        )
        md = render_markdown_report(report)
        assert "# Extractor Benchmark Report" in md
        assert "fismatic" in md
        assert "Aggregate Metrics" in md
        assert "Interpretation Guide" in md

    def test_unavailable_backend_status_skip(self, tmp_path):
        """Unavailable backends are recorded as 'unavailable', not errors."""
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())

        from compliance.scripts.benchmark_extractors import run_benchmark

        # Run all backends; optional ones will be 'unavailable' if not installed
        report = run_benchmark(
            docx_paths=[docx_path],
            backend_names=["fismatic", "docling", "mineru", "unstructured"],
            baseline_name="fismatic",
        )
        doc_result = report["results"][0]
        for backend_name, r in doc_result["backends"].items():
            assert r["status"] in ("ok", "unavailable", "error"), (
                f"Unexpected status {r['status']!r} for backend {backend_name}"
            )

    def test_comparison_metrics_populated(self, tmp_path):
        """When two backends run, comparison metrics are computed."""
        docling_avail = DoclingBackend().is_available()
        if not docling_avail:
            pytest.skip("docling not installed")

        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())

        from compliance.scripts.benchmark_extractors import run_benchmark

        report = run_benchmark(
            docx_paths=[docx_path],
            backend_names=["fismatic", "docling"],
            baseline_name="fismatic",
        )
        doc_result = report["results"][0]
        cmp = doc_result.get("comparisons", {}).get("fismatic_vs_docling", {})
        # If both ran OK, speedup must be present
        if (
            doc_result["backends"]["fismatic"]["status"] == "ok"
            and doc_result["backends"]["docling"]["status"] == "ok"
        ):
            assert "speedup" in cmp
            assert "heading_recall" in cmp

    def test_json_output_serialisable(self, tmp_path):
        import json

        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())

        from compliance.scripts.benchmark_extractors import run_benchmark

        report = run_benchmark(
            docx_paths=[docx_path],
            backend_names=["fismatic"],
            baseline_name="fismatic",
        )
        # Must not raise
        json_str = json.dumps(report, default=str)
        assert len(json_str) > 100


# ---------------------------------------------------------------------------
# Image detection tests  (FismaticBackend previously returned images=[])
# ---------------------------------------------------------------------------


class TestFismaticBackendImageDetection:
    """Verify FismaticBackend surfaces inline/anchor images from docx XML.

    Previously the backend returned an empty list; the inaugural benchmark
    reported 0 images across 10 real SSPs even though many contain data-flow
    diagrams.  These tests pin the fix.
    """

    def _build_docx_with_image(self, tmp_path) -> "Path":
        """Build a docx that contains one inline image in a paragraph."""
        import docx
        from docx.shared import Inches

        path = tmp_path / "with_image.docx"
        doc = docx.Document()
        doc.add_heading("System Architecture", level=1)
        doc.add_paragraph("The data flow is shown below:")
        # Inject a tiny PNG into the package
        import io
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (4, 4), color="red").save(buf, format="PNG")
        buf.seek(0)
        doc.add_picture(buf, width=Inches(2))
        doc.add_paragraph("End of architecture section.")
        doc.save(str(path))
        return path

    def test_detects_inline_image(self, tmp_path):
        # Skip if Pillow not available (we use it to build a real PNG)
        pytest.importorskip("PIL")
        path = self._build_docx_with_image(tmp_path)
        doc = FismaticBackend().extract(path)
        assert doc.image_count() >= 1, (
            f"Expected >=1 image, got {doc.image_count()}; "
            f"the Fismatic backend was returning images=[] before the fix."
        )
        img = doc.images[0]
        assert img.heading == "System Architecture", (
            f"Image should be attributed to its nearest heading; got {img.heading!r}"
        )

    def test_image_count_appears_in_extract_result(self, tmp_path):
        """The image_count() helper returns the new length-based value."""
        pytest.importorskip("PIL")
        path = self._build_docx_with_image(tmp_path)
        doc = FismaticBackend().extract(path)
        assert doc.image_count() == len(doc.images)

    def test_no_images_in_pure_text_docx(self, tmp_path):
        """A docx with no images should return images=[]."""
        path = tmp_path / "text_only.docx"
        path.write_bytes(minimal_docx_bytes())
        doc = FismaticBackend().extract(path)
        assert doc.image_count() == 0


# ---------------------------------------------------------------------------
# Contract coverage tests  (per-document routing metrics)
# ---------------------------------------------------------------------------


class TestContractCoverageAggregation:
    """Unit tests for the contract_aggregate summarizer (no docx needed)."""

    def test_empty_input_returns_none(self):
        from compliance.scripts.benchmark_extractors import (
            _summarize_contract_coverage,
        )

        assert _summarize_contract_coverage([]) is None

    def test_single_document(self):
        from compliance.scripts.benchmark_extractors import (
            _summarize_contract_coverage,
        )

        cov = {
            "fill_rate": 0.5,
            "control_tables_found": 10,
            "unrouted_heading_count": 3,
            "unfilled_properties": [{}, {}, {}],
        }
        agg = _summarize_contract_coverage([cov])
        assert agg["documents_with_coverage"] == 1
        assert agg["mean_fill_rate"] == 0.5
        assert agg["median_fill_rate"] == 0.5
        assert agg["min_fill_rate"] == 0.5
        assert agg["max_fill_rate"] == 0.5
        assert agg["total_control_tables"] == 10
        assert agg["total_unrouted_headings"] == 3
        assert agg["total_unfilled_properties"] == 3
        assert agg["documents_full_coverage"] == 0
        assert agg["documents_zero_coverage"] == 0

    def test_mixed_fill_rates(self):
        from compliance.scripts.benchmark_extractors import (
            _summarize_contract_coverage,
        )

        covs = [
            {
                "fill_rate": 1.0,
                "control_tables_found": 0,
                "unrouted_heading_count": 0,
                "unfilled_properties": [],
            },
            {
                "fill_rate": 0.0,
                "control_tables_found": 0,
                "unrouted_heading_count": 5,
                "unfilled_properties": [{}, {}],
            },
            {
                "fill_rate": 0.6,
                "control_tables_found": 3,
                "unrouted_heading_count": 1,
                "unfilled_properties": [{}],
            },
        ]
        agg = _summarize_contract_coverage(covs)
        assert agg["documents_with_coverage"] == 3
        assert agg["mean_fill_rate"] == round((1.0 + 0.0 + 0.6) / 3, 3)
        assert agg["min_fill_rate"] == 0.0
        assert agg["max_fill_rate"] == 1.0
        # Median of [0.0, 0.6, 1.0] is 0.6
        assert agg["median_fill_rate"] == 0.6
        assert agg["documents_full_coverage"] == 1
        assert agg["documents_zero_coverage"] == 1
        assert agg["total_control_tables"] == 3
        assert agg["total_unrouted_headings"] == 6
        assert agg["total_unfilled_properties"] == 3


@pytest.mark.r12
class TestContractCoverageIntegration:
    """Integration tests for compute_contract_coverage on a real docx."""

    def test_contract_coverage_reports_routing_metrics(self, tmp_path):
        from compliance.scripts.benchmark_extractors import (
            compute_contract_coverage,
        )

        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        # Locate the contract shipped with the repo
        contract_path = (
            Path(__file__).resolve().parents[1]
            / "datacontracts"
            / "fedramp-ssp-v1.yaml"
        )
        if not contract_path.exists():
            pytest.skip(f"Contract not found at {contract_path}")
        cov = compute_contract_coverage(docx_path, contract_path)
        assert "objects_total" in cov
        assert "objects_routed" in cov
        assert "properties_total" in cov
        assert "properties_filled" in cov
        assert "fill_rate" in cov
        assert "unrouted_headings" in cov
        assert "unfilled_properties" in cov
        # totals from the contract (>= 14 objects, >= 90 properties)
        assert cov["objects_total"] >= 14
        assert cov["properties_total"] >= 90
        # fill_rate is a float in [0, 1]
        assert 0.0 <= cov["fill_rate"] <= 1.0

    def test_run_benchmark_attaches_contract_coverage(self, tmp_path):
        from compliance.scripts.benchmark_extractors import run_benchmark

        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        contract_path = (
            Path(__file__).resolve().parents[1]
            / "datacontracts"
            / "fedramp-ssp-v1.yaml"
        )
        if not contract_path.exists():
            pytest.skip(f"Contract not found at {contract_path}")

        report = run_benchmark(
            docx_paths=[docx_path],
            backend_names=["fismatic"],
            baseline_name="fismatic",
            contract_path=contract_path,
        )
        assert report.get("contract_path") == str(contract_path)
        assert report.get("contract_aggregate") is not None
        cov = report["results"][0].get("contract_coverage")
        assert cov is not None
        assert "fill_rate" in cov
        # Synthetic dumper headings must be filtered out
        for h in cov["unrouted_headings"]:
            assert h["title"] != "Document Metadata (core properties)"
            assert h["title"] != "Document Inventory"
            assert h["title"] != "Headings Outline (in document order)"
            assert (
                h["title"]
                != "Body Content (in document order, grouped under each heading)"
            )

    def test_run_benchmark_without_contract_skips_coverage(self, tmp_path):
        from compliance.scripts.benchmark_extractors import run_benchmark

        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        report = run_benchmark(
            docx_paths=[docx_path],
            backend_names=["fismatic"],
            baseline_name="fismatic",
        )
        assert report.get("contract_path") is None
        assert report.get("contract_aggregate") is None
        assert "contract_coverage" not in report["results"][0]

    def test_markdown_report_includes_contract_section(self, tmp_path):
        from compliance.scripts.benchmark_extractors import (
            render_markdown_report,
            run_benchmark,
        )

        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(minimal_docx_bytes())
        contract_path = (
            Path(__file__).resolve().parents[1]
            / "datacontracts"
            / "fedramp-ssp-v1.yaml"
        )
        if not contract_path.exists():
            pytest.skip(f"Contract not found at {contract_path}")

        report = run_benchmark(
            docx_paths=[docx_path],
            backend_names=["fismatic"],
            baseline_name="fismatic",
            contract_path=contract_path,
        )
        md = render_markdown_report(report)
        assert "## Contract Coverage" in md
        assert "Corpus-wide routing summary" in md
        assert "Per-document coverage" in md
        assert "Mean fill rate" in md
        assert "Unrouted headings" in md
        assert str(contract_path) in md
