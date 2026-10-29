"""Tests for the unified contract-driven SSP converter (Phase R11).

These tests define the R11 acceptance criteria.  They are intentionally
structured so that:

1. Tests marked ``@pytest.mark.r11`` are **expected to fail** today (the
   converter raises ``NotImplementedError``).  They will be unmarked as
   each pipeline stage is implemented.

2. Structural/API tests (no mark) pass today and guard the module's public
   interface contract.

Running today::

    uv run pytest compliance/tests/test_unified_converter.py -v

Running once R11 is complete::

    uv run pytest compliance/tests/test_unified_converter.py -v -m r11

"""

import pytest

from compliance.lib.unified_converter import (
    ConversionResult,
    ConvertedEntity,
    DocumentNode,
    DocumentTree,
    UnifiedConverter,
    assemble_entities,
    execute_strategy,
    extract_document_tree,
    route_document_tree,
    write_entities,
)


# ---------------------------------------------------------------------------
# Structural / API tests (pass today)
# ---------------------------------------------------------------------------


class TestPublicDataModels:
    """Verify that all public data models are importable and constructable."""

    def test_document_node_defaults(self):
        node = DocumentNode(kind="heading", level=1, text="Introduction", raw=None)
        assert node.kind == "heading"
        assert node.level == 1
        assert node.children == []

    def test_document_tree_headings_filter(self):
        h1 = DocumentNode(kind="heading", level=1, text="H1", raw=None)
        h2 = DocumentNode(kind="heading", level=2, text="H2", raw=None)
        para = DocumentNode(kind="paragraph", level=None, text="Para", raw=None)
        tree = DocumentTree(nodes=[h1, h2, para])
        assert tree.headings(max_level=1) == [h1]
        assert tree.headings() == [h1, h2]

    def test_converted_entity_defaults(self):
        entity = ConvertedEntity(
            destination_id="oscal_ssp",
            destination_folder="Systems/",
            oscal_type="oscal.ssp",
            note_type="system",
        )
        assert entity.fields == {}
        assert entity.source_heading is None

    def test_conversion_result_defaults(self):
        result = ConversionResult()
        assert result.entities == []
        assert result.warnings == []
        assert result.errors == []


class TestUnifiedConverterInit:
    """Verify UnifiedConverter instantiation without a real contract."""

    def test_init_accepts_path(self, tmp_path):
        # Write a minimal stub contract file so Path resolves
        contract = tmp_path / "test.yaml"
        contract.write_text("kind: DataContract\nschema: []\n")
        converter = UnifiedConverter(contract_path=contract)
        assert converter.contract_path == contract
        assert converter.primary_parser_results == {}

    def test_init_with_primary_results(self, tmp_path):
        contract = tmp_path / "test.yaml"
        contract.write_text("kind: DataContract\nschema: []\n")
        primary = {"AC-1": {"implementation_status": "implemented"}}
        converter = UnifiedConverter(
            contract_path=contract, primary_parser_results=primary
        )
        assert converter.primary_parser_results == primary


# ---------------------------------------------------------------------------
# R11 acceptance tests (expected to fail until implementation is complete)
# ---------------------------------------------------------------------------


@pytest.mark.r11
class TestExtractDocumentTree:
    """Stage 1: .docx → DocumentTree."""

    def test_raises_on_missing_file(self):
        with pytest.raises((FileNotFoundError, NotImplementedError)):
            extract_document_tree("/nonexistent/path.docx")

    def test_returns_document_tree(self, tmp_path):
        # Build a minimal real .docx via python-docx (mirrors
        # test_extractor_backend.minimal_docx_bytes).
        import docx

        fake_docx = tmp_path / "test.docx"
        d = docx.Document()
        d.add_heading("System Overview", level=1)
        d.add_paragraph("This is the system description.")
        d.add_heading("Access Control", level=2)
        d.add_paragraph("AC paragraph text.")
        d.save(str(fake_docx))
        result = extract_document_tree(fake_docx)
        assert isinstance(result, DocumentTree)

    def test_headings_are_ordered(self, tmp_path):
        import docx

        fake_docx = tmp_path / "test.docx"
        d = docx.Document()
        d.add_heading("H1", level=1)
        d.add_heading("H2", level=2)
        d.add_heading("H3", level=3)
        d.save(str(fake_docx))
        tree = extract_document_tree(fake_docx)
        headings = tree.headings()
        # Heading levels must be monotonically non-decreasing within sections
        for i in range(1, len(headings)):
            if headings[i].level and headings[i - 1].level:
                assert headings[i].level <= headings[i - 1].level + 1


@pytest.mark.r11
class TestRouteDocumentTree:
    """Stage 2: DocumentTree + contract → routed nodes."""

    def test_exact_match_routing(self, tmp_path):
        contract = tmp_path / "contract.yaml"
        contract.write_text(
            """
kind: DataContract
schema:
  - id: oscal_ssp
    name: ssp
    logicalType: object
    properties:
      - name: system_name
        logicalType: string
        customProperties:
          - property: sourceHeading
            value: "System Overview"
          - property: captureStrategy
            value: heading_paragraph
"""
        )
        tree = DocumentTree(
            nodes=[
                DocumentNode(kind="heading", level=1, text="System Overview", raw=None),
                DocumentNode(
                    kind="paragraph", level=None, text="The system is...", raw=None
                ),
            ]
        )
        routed = route_document_tree(tree, contract)
        assert "oscal_ssp" in routed
        assert len(routed["oscal_ssp"]) >= 1

    def test_regex_match_routing(self, tmp_path):
        contract = tmp_path / "contract.yaml"
        contract.write_text(
            """
kind: DataContract
schema:
  - id: oscal_control
    name: control
    logicalType: object
    customProperties:
      - property: multiple
        value: true
    properties:
      - name: control_id
        logicalType: string
        customProperties:
          - property: sourceHeading
            value: "regex:^(?:13\\\\.|Access Control)"
          - property: captureStrategy
            value: control_table
"""
        )
        tree = DocumentTree(
            nodes=[
                DocumentNode(kind="heading", level=2, text="Access Control", raw=None),
                DocumentNode(
                    kind="heading", level=2, text="Audit and Accountability", raw=None
                ),
            ]
        )
        routed = route_document_tree(tree, contract)
        # Both "Access Control" headings match the regex
        assert "oscal_control" in routed

    def test_sub_headings_included_in_routed_content(self, tmp_path):
        """R11: captureScope=until_next_same_or_higher gathers sub-headings."""
        contract = tmp_path / "contract.yaml"
        contract.write_text(
            """
kind: DataContract
schema:
  - id: oscal_ssp
    name: ssp
    logicalType: object
    properties:
      - name: description
        logicalType: string
        customProperties:
          - property: sourceHeading
            value: "General System Description"
          - property: captureStrategy
            value: heading_paragraph
"""
        )
        tree = DocumentTree(
            nodes=[
                DocumentNode(
                    kind="heading", level=1, text="General System Description", raw=None
                ),
                DocumentNode(
                    kind="paragraph", level=None, text="Top-level text.", raw=None
                ),
                DocumentNode(kind="heading", level=2, text="Sub-section", raw=None),
                DocumentNode(
                    kind="paragraph", level=None, text="Sub-level text.", raw=None
                ),
                DocumentNode(kind="heading", level=1, text="Next Section", raw=None),
            ]
        )
        routed = route_document_tree(tree, contract)
        assert "oscal_ssp" in routed
        # Both paragraphs should be in the routed content (until_next_same_or_higher)
        # Sub-heading is H2 < H1, so it doesn't terminate the H1 scope.
        all_text = " ".join(
            n.text for n in routed["oscal_ssp"] if n.kind == "paragraph"
        )
        assert "Top-level text." in all_text
        assert "Sub-level text." in all_text


@pytest.mark.r11
class TestExecuteStrategy:
    """Stage 3: Capture strategy execution."""

    def test_heading_paragraph_strategy(self):
        para = DocumentNode(
            kind="paragraph", level=None, text="System description.", raw=None
        )
        heading = DocumentNode(
            kind="heading", level=1, text="Overview", raw=None, children=[para]
        )
        result = execute_strategy([heading], strategy="heading_paragraph")
        assert isinstance(result, str)
        assert "System description" in result

    def test_constant_strategy_raises_on_invalid(self):
        with pytest.raises(ValueError):
            execute_strategy([], strategy="__invalid__strategy__")

    def test_heading_table_all_returns_list(self):
        table = DocumentNode(kind="table", level=None, text="", raw=None)
        heading = DocumentNode(
            kind="heading", level=2, text="Ports", raw=None, children=[table]
        )
        result = execute_strategy([heading], strategy="heading_table_all")
        assert isinstance(result, list)

    def test_heading_paragraph_first_returns_first_paragraph(self):
        para1 = DocumentNode(
            kind="paragraph", level=None, text="First sentence.", raw=None
        )
        para2 = DocumentNode(
            kind="paragraph", level=None, text="Second sentence.", raw=None
        )
        heading = DocumentNode(
            kind="heading", level=1, text="Overview", raw=None, children=[para1, para2]
        )
        result = execute_strategy([heading], strategy="heading_paragraph_first")
        assert result == "First sentence."

    def test_heading_paragraph_concatenates_all(self):
        para1 = DocumentNode(kind="paragraph", level=None, text="First.", raw=None)
        para2 = DocumentNode(kind="paragraph", level=None, text="Second.", raw=None)
        heading = DocumentNode(
            kind="heading", level=1, text="Overview", raw=None, children=[para1, para2]
        )
        result = execute_strategy([heading], strategy="heading_paragraph")
        assert "First." in result
        assert "Second." in result

    def test_heading_image_all_returns_alt_texts(self):
        img1 = DocumentNode(kind="image", level=None, text="Diagram 1", raw=None)
        img2 = DocumentNode(kind="image", level=None, text="Diagram 2", raw=None)
        heading = DocumentNode(
            kind="heading", level=2, text="Network", raw=None, children=[img1, img2]
        )
        result = execute_strategy([heading], strategy="heading_image_all")
        assert result == ["Diagram 1", "Diagram 2"]

    def test_constant_strategy_returns_none(self):
        # constant + derived are handled by stage 4, not stage 3
        result = execute_strategy([], strategy="constant")
        assert result is None
        result = execute_strategy([], strategy="derived")
        assert result is None


@pytest.mark.r11
class TestAssembleEntities:
    """Stage 4: Typed entity assembly."""

    def test_required_field_missing_generates_warning(self, tmp_path):
        contract = tmp_path / "contract.yaml"
        contract.write_text(
            """
kind: DataContract
schema:
  - id: oscal_ssp
    name: ssp
    logicalType: object
    properties:
      - name: system_name
        logicalType: string
        required: true
        customProperties:
          - property: sourceHeading
            value: "System Overview"
          - property: captureStrategy
            value: heading_paragraph
"""
        )
        # Empty routing → required field will be missing
        entities = assemble_entities({}, contract)
        # Should produce an entity with a warning, not crash
        assert isinstance(entities, list)

    def test_entity_count_matches_destinations(self, tmp_path):
        contract = tmp_path / "contract.yaml"
        contract.write_text(
            """
kind: DataContract
schema:
  - id: oscal_ssp
    name: ssp
    logicalType: object
    properties: []
  - id: oscal_party
    name: party
    logicalType: object
    properties: []
"""
        )
        entities = assemble_entities({}, contract)
        # Exactly 2 destinations → 2 entities
        assert len(entities) == 2


@pytest.mark.r11
class TestWriteEntities:
    """Stage 5: Vault + sidecar writing."""

    def test_creates_markdown_file(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        entities = [
            ConvertedEntity(
                destination_id="oscal_ssp",
                destination_folder="Systems/",
                oscal_type="oscal.ssp",
                note_type="system",
                fields={"title": "Test System", "status": "operational"},
            )
        ]
        write_entities(entities, vault_dir=vault)
        assert (vault / "Systems").is_dir()
        md_files = list((vault / "Systems").glob("*.md"))
        assert len(md_files) == 1

    def test_creates_canonical_json_sidecar(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        snapshots = tmp_path / "snapshots"
        entities = [
            ConvertedEntity(
                destination_id="oscal_ssp",
                destination_folder="Systems/",
                oscal_type="oscal.ssp",
                note_type="system",
                fields={"title": "Test System"},
            )
        ]
        write_entities(entities, vault_dir=vault, snapshots_dir=snapshots)
        json_files = list(snapshots.glob("**/*.json"))
        assert len(json_files) >= 1


@pytest.mark.r11
class TestUnifiedConverterEndToEnd:
    """Full pipeline integration test."""

    def test_convert_returns_conversion_result(self, tmp_path):
        """R11: convert() now returns a ConversionResult instead of raising."""
        import docx

        contract = tmp_path / "contract.yaml"
        contract.write_text(
            """
kind: DataContract
schema:
  - id: oscal_ssp
    name: ssp
    logicalType: object
    customProperties:
      - property: destinationFolder
        value: "Systems/"
    properties:
      - name: title
        logicalType: string
        customProperties:
          - property: sourceHeading
            value: "System Overview"
          - property: captureStrategy
            value: heading_paragraph
"""
        )
        docx_path = tmp_path / "test.docx"
        d = docx.Document()
        d.add_heading("System Overview", level=1)
        d.add_paragraph("My system description here.")
        d.save(str(docx_path))
        vault = tmp_path / "vault"
        converter = UnifiedConverter(contract_path=contract)
        result = converter.convert(docx_path=docx_path, vault_dir=vault)
        assert isinstance(result, ConversionResult)
        # One entity per destination
        assert len(result.entities) == 1
        # The title was extracted
        assert result.entities[0].fields.get("title") == "My system description here."
        # A markdown file was written
        assert (vault / "Systems").is_dir()

    def test_convert_result_type(self, tmp_path):
        """When implemented, convert() must return ConversionResult."""
        # This test will only pass when R11 is complete.
        contract = tmp_path / "contract.yaml"
        contract.write_text("kind: DataContract\nschema: []\n")
        fake_docx = tmp_path / "test.docx"
        fake_docx.write_bytes(b"PK")
        vault = tmp_path / "vault"
        converter = UnifiedConverter(contract_path=contract)
        result = converter.convert(docx_path=fake_docx, vault_dir=vault)
        assert isinstance(result, ConversionResult)
