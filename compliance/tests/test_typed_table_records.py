"""Regression coverage for lossless typed table records (#63)."""

from __future__ import annotations

from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from compliance.lib.canonical_json import (
    generate_canonical_data,
    parse_table_records,
    validate_canonical_sidecar,
)
from compliance.lib.table_records import table_record_metrics
from compliance.lib.vault import (
    VaultIndex,
    build_bases_views,
    build_narrative_notes,
    build_synthetic_component_note,
    enrich_component_note_from_narrative,
)


def test_two_column_fact_sheet_is_a_typed_external_service_record(
    tmp_path: Path,
) -> None:
    note = tmp_path / "service.md"
    body = (
        "# External Service\n\n"
        "| Service Element | Response |\n"
        "| --- | --- |\n"
        "| Name | login.gov |\n"
    )
    note.write_text(f"---\ntype: oscal.x.relationship\n---\n{body}", encoding="utf-8")

    record = generate_canonical_data(note, tmp_path)["table_records"][0]

    assert record["schema_version"] == 1
    assert record["source_heading"] == "External Service"
    assert record["classification"] == "fact_sheet"
    assert record["kind"] == "external_service"
    assert record["disposition"] == "mapped"
    assert record["rows"] == [["Name", "login.gov"]]
    assert record["raw_markdown"] in body


def test_table_without_leading_pipes_is_valid_and_schema_conformant(
    tmp_path: Path,
) -> None:
    note = tmp_path / "service.md"
    note.write_text(
        "---\ntype: oscal.x.relationship\n---\n"
        "# External Service\n\n"
        "Service Element | Response\n"
        "--- | ---\n"
        "Name | login.gov\n",
        encoding="utf-8",
    )

    canonical = generate_canonical_data(note, tmp_path)

    assert canonical["table_records"][0]["kind"] == "external_service"
    assert canonical["table_records"][0]["valid"] is True
    assert validate_canonical_sidecar(canonical) == []


def test_separator_width_mismatch_is_rejected() -> None:
    record = parse_table_records(
        "Service Element | Response\n--- | --- | ---\nName | login.gov\n"
    )[0]

    assert record["valid"] is False
    assert record["classification"] == "opaque"
    assert record["disposition"] == "unmapped"


def test_tables_inside_fenced_code_are_not_canonical_records() -> None:
    records = parse_table_records(
        "```markdown\n| Service Element | Response |\n| --- | --- |\n"
        "| Name | example |\n```\n\n"
        "| Service Element | Response |\n| --- | --- |\n| Name | login.gov |\n"
    )

    assert len(records) == 1
    assert records[0]["rows"] == [["Name", "login.gov"]]


def test_multi_row_inventory_is_preserved_not_flattened_into_frontmatter() -> None:
    inventory = {
        "headers": ["Software", "Version", "Purpose"],
        "rows": [["nginx", "1.25", "proxy"], ["postgres", "16", "database"]],
    }
    narrative = {
        "objects": [
            {
                "object_name": "oscal_x_relationship",
                "properties": {"interconnections": inventory},
            }
        ]
    }
    index = VaultIndex(systems={"Example": "[[Systems/example]]"})

    note = build_narrative_notes(narrative, index=index)[0]

    assert "| Software | Version | Purpose |" in note.body
    assert "postgres" in note.body
    assert note.frontmatter["table_record_count"] == 1
    assert note.frontmatter["table_kinds"] == ["opaque"]
    assert note.frontmatter["table_classifications"] == ["opaque"]
    assert note.frontmatter["table_opaque_count"] == 1
    assert note.frontmatter["table_unmapped_count"] == 1
    assert "software" not in note.frontmatter
    assert "rows" not in note.frontmatter


def test_component_inventory_has_aggregate_projection_without_rows() -> None:
    inventory = {
        "headers": ["Service Element", "Response"],
        "rows": [["Name", "login.gov"], ["Authorization", "FedRAMP"]],
    }
    narrative = {
        "objects": [
            {
                "object_name": "oscal_component_definition",
                "properties": {"external_services": inventory},
            }
        ]
    }
    index = VaultIndex(systems={"Example": "[[Systems/example]]"})
    component = build_synthetic_component_note(
        {"system-characteristics": {"system-name": "Example"}}, index=index
    )

    note = enrich_component_note_from_narrative(component, narrative)

    assert note is not None
    assert note.frontmatter["table_record_count"] == 1
    assert note.frontmatter["table_kinds"] == ["external_service"]
    assert note.frontmatter["table_structured_count"] == 1
    assert "external_services" not in note.frontmatter
    assert "login.gov" in note.body


def test_protocol_and_inheritance_provider_table_classification() -> None:
    records = parse_table_records(
        "# Ports, Protocols, and Services\n\n"
        "| Ports (T or U) | Protocols | Services |\n"
        "| --- | --- | --- |\n"
        "| 443 (T) | HTTPS | API |\n\n"
        "# Provided By\n\n"
        "| Provided By | Authorization |\n"
        "| --- | --- |\n"
        "| cloud.gov | FedRAMP |\n"
    )

    assert [record["kind"] for record in records] == [
        "protocol",
        "inheritance_provider",
    ]
    assert all(record["disposition"] == "mapped" for record in records)
    assert [record["classification"] for record in records] == [
        "record_set",
        "record_set",
    ]


def test_inheritance_projection_uses_the_provided_by_section_heading() -> None:
    narrative = {
        "objects": [
            {
                "object_name": "oscal_x_inheritance",
                "properties": {
                    "provided_by": {
                        "headers": ["Provider", "Scope"],
                        "rows": [["cloud.gov", "platform"]],
                    }
                },
            }
        ]
    }
    index = VaultIndex(systems={"Example": "[[Systems/example]]"})

    note = build_narrative_notes(narrative, index=index)[0]

    assert note.frontmatter["table_kinds"] == ["inheritance_provider"]


def test_malformed_and_duplicate_heading_tables_remain_explicitly_unmapped() -> None:
    records = parse_table_records(
        "# Service Details\n\n"
        "| Service Element | Service Element |\n"
        "| --- | --- |\n"
        "| Name | login.gov |\n\n"
        "# Service Details\n\n"
        "| Key | Value |\n"
        "| Name | api.data.gov |\n"
    )

    assert len(records) == 2
    assert [record["source_heading"] for record in records] == [
        "Service Details",
        "Service Details",
    ]
    assert all(record["kind"] == "opaque" for record in records)
    assert all(record["disposition"] == "unmapped" for record in records)
    assert records[1]["rows"] == [["Name", "api.data.gov"]]


def test_known_and_unknown_table_classification_coverage() -> None:
    records = parse_table_records(
        "| Service Element | Response |\n| --- | --- |\n| Name | login.gov |\n\n"
        "| Ports | Protocols | Services |\n| --- | --- | --- |\n| 443 | HTTPS | API |\n\n"
        "# Provided By\n\n| Provider | Scope |\n| --- | --- |\n| cloud.gov | platform |\n\n"
        "# Asset Inventory\n\n| Asset | Owner |\n| --- | --- |\n| database | team |\n"
    )

    assert {record["kind"] for record in records} == {
        "external_service",
        "protocol",
        "inheritance_provider",
        "asset_inventory",
    }
    assert table_record_metrics(records) == {
        "tables_total": 4,
        "tables_classified": 4,
        "tables_structured": 4,
        "tables_opaque": 0,
        "tables_unmapped": 0,
    }


def test_classification_requires_an_exact_unambiguous_shape() -> None:
    records = parse_table_records(
        "| Service Element | Response | Extra |\n"
        "| --- | --- | --- |\n"
        "| Name | login.gov | ignored |\n\n"
        "| Ports | Protocols | Services | Notes |\n"
        "| --- | --- | --- | --- |\n"
        "| 443 | HTTPS | API | public |\n\n"
        "# Not Provided By This Heading\n\n"
        "| Provider | Scope |\n| --- | --- |\n| cloud.gov | platform |\n"
    )

    assert [record["kind"] for record in records] == ["opaque", "opaque", "opaque"]


def test_presentation_table_is_classified_but_not_structured() -> None:
    record = parse_table_records(
        "# Coverage\n\n| Kind | Count |\n| --- | --- |\n| mapped | 3 |\n"
    )[0]

    assert record["classification"] == "presentation_only"
    assert record["kind"] == "opaque"
    assert record["disposition"] == "unmapped"
    assert table_record_metrics([record])["tables_classified"] == 1


def test_asset_and_software_inventory_shapes_are_typed_with_source_locators() -> None:
    records = parse_table_records(
        "# Asset Inventory\n\n"
        "| Asset Name | Asset Type | Owner |\n"
        "| --- | --- | --- |\n"
        "| db-1 | database | platform |\n\n"
        "# Software Inventory\n\n"
        "| Software | Version | Purpose |\n"
        "| --- | --- | --- |\n"
        "| postgres | 16 | database |\n"
    )

    assert [record["kind"] for record in records] == [
        "asset_inventory",
        "software_inventory",
    ]
    assert all(
        record["source_locator"].startswith("markdown:line[") for record in records
    )
    assert all(record["row_locators"] for record in records)
    assert records[0]["source_locator"] == "markdown:line[3]"
    assert records[0]["row_locators"] == ["markdown:line[5]"]


def test_components_base_exposes_flat_typed_table_projection() -> None:
    component_view = next(
        note for note in build_bases_views() if note.relpath.endswith("components.base")
    ).render()

    assert "Typed table coverage" in component_view
    assert "table_record_count" in component_view
    assert "table_structured_count" in component_view
    assert "table_unmapped_count" in component_view


def test_all_initial_table_kinds_have_queryable_base_views() -> None:
    views = {
        Path(note.relpath).name: yaml.safe_load(note.render())
        for note in build_bases_views()
    }

    assert views["components.base"]["filters"] == {
        "and": ['file.inFolder("Components")']
    }
    assert views["relationships.base"]["filters"] == {
        "and": ['file.inFolder("Relationships")']
    }
    assert views["inheritance.base"]["filters"] == {
        "and": ['file.inFolder("Inheritance")']
    }
    for name in ("components.base", "relationships.base", "inheritance.base"):
        assert "table_kinds" in views[name]["properties"]
        assert "table_structured_count" in views[name]["properties"]


def test_typed_summary_frontmatter_conforms_to_component_and_extension_schemas() -> (
    None
):
    schema = yaml.safe_load(Path("vault-schemas.json").read_text(encoding="utf-8"))
    component = build_synthetic_component_note(
        {"system-characteristics": {"system-name": "Example"}},
        index=VaultIndex(systems={"Example": "[[Systems/example]]"}),
    )
    component.frontmatter.update(
        {
            "table_record_count": 1,
            "table_kinds": ["external_service"],
            "table_classifications": ["fact_sheet"],
            "table_structured_count": 1,
            "table_opaque_count": 0,
            "table_unmapped_count": 0,
        }
    )
    extension = {
        "type": "oscal.x.relationship",
        "extension_model": "relationship",
        "kind": "extension",
        "title": "Example relationships",
        "table_record_count": 1,
        "table_kinds": ["protocol"],
        "table_classifications": ["record_set"],
        "table_structured_count": 1,
        "table_opaque_count": 0,
        "table_unmapped_count": 0,
    }

    Draft202012Validator(schema["$defs"]["VaultComponentFrontmatter"]).validate(
        component.frontmatter
    )
    Draft202012Validator(
        schema["$defs"]["VaultExtensionNarrativeFrontmatter"]
    ).validate(extension)
