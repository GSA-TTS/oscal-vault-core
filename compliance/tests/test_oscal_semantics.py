"""#46: post-schema semantic reference validation and cross-stage parity.

Schema validation proves document shape. Each test here encodes one defect that
is schema-valid but semantically broken, or one way a stage-to-stage transform
could drop data while every schema check still passes.
"""

from __future__ import annotations

from compliance.lib.validate_oscal import (
    summarize_canonical_ssp,
    summarize_oscal_ssp,
    validate_oscal_document,
    validate_oscal_semantics,
    verify_oscal_parity,
)
from compliance.tests.test_validate_oscal import (
    _minimal_valid_component_definition,
    _minimal_valid_ssp,
)

_MISSING_UUID = "00000000-0000-4000-8000-000000000099"


def _semantic_document() -> dict:
    """A coherent SSP: declared roles, parties, components, and resolved refs."""
    document = _minimal_valid_ssp()
    ssp = document["system-security-plan"]
    component_uuid = ssp["system-implementation"]["components"][0]["uuid"]
    party_uuid = "00000000-0000-4000-8000-00000000000a"
    ssp["metadata"]["roles"] = [{"id": "system-owner", "title": "System Owner"}]
    ssp["metadata"]["parties"] = [
        {"uuid": party_uuid, "type": "organization", "name": "Example Agency"}
    ]
    ssp["metadata"]["responsible-parties"] = [
        {"role-id": "system-owner", "party-uuids": [party_uuid]}
    ]
    requirement = ssp["control-implementation"]["implemented-requirements"][0]
    requirement["responsible-roles"] = [
        {"role-id": "system-owner", "party-uuids": [party_uuid]}
    ]
    requirement["statements"] = [
        {
            "statement-id": "ac-1_smt.a",
            "uuid": "00000000-0000-4000-8000-00000000000b",
        }
    ]
    requirement["set-parameters"] = [{"param-id": "ac-1_prm_1", "values": ["x"]}]
    requirement["by-components"] = [
        {
            "uuid": "00000000-0000-4000-8000-000000000001",
            "component-uuid": component_uuid,
            "description": "Test implementation.",
        }
    ]
    return document


def _requirement(document: dict) -> dict:
    return document["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ][0]


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def test_semantic_validation_accepts_resolved_export_graph() -> None:
    result = validate_oscal_document(_semantic_document(), semantic=True)

    assert result.valid, result.errors


def test_semantic_validation_accepts_component_definition() -> None:
    result = validate_oscal_document(
        _minimal_valid_component_definition(), semantic=True
    )

    assert result.valid, result.errors


def test_schema_validation_remains_a_prerequisite() -> None:
    """Semantics are not evaluated for a schema-invalid document."""
    document = _semantic_document()
    del document["system-security-plan"]["metadata"]["title"]

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert not any(error.startswith("semantic:") for error in result.errors)


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------


def test_rejects_unresolved_component_reference() -> None:
    document = _semantic_document()
    _requirement(document)["by-components"][0]["component-uuid"] = _MISSING_UUID

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("unresolved component-uuid" in error for error in result.errors)


def test_rejects_unresolved_responsible_party_reference() -> None:
    document = _semantic_document()
    document["system-security-plan"]["metadata"]["responsible-parties"][0][
        "party-uuids"
    ] = [_MISSING_UUID]

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("unresolved party-uuid" in error for error in result.errors)


def test_rejects_unresolved_metadata_role_reference() -> None:
    document = _semantic_document()
    document["system-security-plan"]["metadata"]["responsible-parties"][0][
        "role-id"
    ] = "not-a-declared-role"

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("unresolved role-id" in error for error in result.errors)


def test_rejects_unresolved_control_level_role_reference() -> None:
    """A per-control responsible-role must resolve to metadata.roles[]."""
    document = _semantic_document()
    _requirement(document)["responsible-roles"][0]["role-id"] = "undeclared-role"

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("unresolved role-id" in error for error in result.errors)


def test_rejects_unresolved_control_level_party_reference() -> None:
    document = _semantic_document()
    _requirement(document)["responsible-roles"][0]["party-uuids"] = [_MISSING_UUID]

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("unresolved party-uuid" in error for error in result.errors)


def test_rejects_unresolved_component_definition_reference() -> None:
    """A capability's ``incorporates-components`` must name a real component."""
    document = _minimal_valid_component_definition()
    document["component-definition"]["capabilities"] = [
        {
            "uuid": "00000000-0000-4000-8000-000000000031",
            "name": "Example capability",
            "description": "d",
            "incorporates-components": [
                {"component-uuid": _MISSING_UUID, "description": "d"}
            ],
        }
    ]

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("unresolved component-uuid" in error for error in result.errors)


# ---------------------------------------------------------------------------
# Identifier uniqueness
# ---------------------------------------------------------------------------


def test_rejects_duplicate_uuid_between_top_level_entities() -> None:
    document = _semantic_document()
    ssp = document["system-security-plan"]
    ssp["system-implementation"]["components"][0]["uuid"] = ssp["uuid"]

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("duplicate uuid" in error for error in result.errors)


def test_rejects_duplicate_uuid_in_a_nested_assembly() -> None:
    """Uniqueness is document-wide, not limited to a hand-picked entity list."""
    document = _semantic_document()
    ssp = document["system-security-plan"]
    ssp["system-characteristics"]["system-information"]["information-types"][0][
        "uuid"
    ] = ssp["uuid"]

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("duplicate uuid" in error for error in result.errors)


def test_rejects_duplicate_uuid_in_a_component_definition() -> None:
    document = _minimal_valid_component_definition()
    cd = document["component-definition"]
    cd["components"][0]["uuid"] = cd["uuid"]

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("duplicate uuid" in error for error in result.errors)


def test_rejects_duplicate_metadata_role_id() -> None:
    document = _semantic_document()
    document["system-security-plan"]["metadata"]["roles"].append(
        {"id": "system-owner", "title": "Duplicate"}
    )

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("duplicate metadata role id" in error for error in result.errors)


def test_rejects_duplicate_control_id() -> None:
    document = _semantic_document()
    requirements = document["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ]
    clone = dict(requirements[0])
    clone["uuid"] = "00000000-0000-4000-8000-0000000000ff"
    requirements.append(clone)

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("duplicate control-id" in error for error in result.errors)


def test_rejects_duplicate_statement_id() -> None:
    document = _semantic_document()
    statements = _requirement(document)["statements"]
    statements.append(
        {
            "statement-id": statements[0]["statement-id"],
            "uuid": "00000000-0000-4000-8000-0000000000fe",
        }
    )

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("duplicate statement-id" in error for error in result.errors)


def test_rejects_duplicate_param_id() -> None:
    document = _semantic_document()
    _requirement(document)["set-parameters"].append(
        {"param-id": "ac-1_prm_1", "values": ["y"]}
    )

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("duplicate param-id" in error for error in result.errors)


# ---------------------------------------------------------------------------
# Identifier scoping
# ---------------------------------------------------------------------------


def test_rejects_statement_attached_to_the_wrong_control() -> None:
    document = _semantic_document()
    _requirement(document)["statements"][0]["statement-id"] = "au-2_smt.a"

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("not scoped to control-id" in error for error in result.errors)


def test_rejects_parameter_attached_to_the_wrong_control() -> None:
    document = _semantic_document()
    _requirement(document)["set-parameters"][0]["param-id"] = "au-2_prm_1"

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("is not scoped to control-id" in error for error in result.errors)


def test_accepts_base_control_parameter_on_an_enhancement() -> None:
    """``ac-2_odp.01`` may legitimately be set by requirement ``ac-2.3``."""
    document = _semantic_document()
    requirement = _requirement(document)
    requirement["control-id"] = "ac-2.3"
    requirement["statements"][0]["statement-id"] = "ac-2.3_smt.a"
    requirement["set-parameters"][0]["param-id"] = "ac-2_odp.01"

    result = validate_oscal_document(document, semantic=True)

    assert result.valid, result.errors


def test_rejects_requirement_without_a_control_id() -> None:
    document = _semantic_document()
    requirement = _requirement(document)
    del requirement["control-id"]

    errors = validate_oscal_semantics(document)

    assert any("no control-id" in error for error in errors)


# ---------------------------------------------------------------------------
# Synthetic production objects
# ---------------------------------------------------------------------------


def test_rejects_synthetic_requirement_by_default() -> None:
    document = _semantic_document()
    _requirement(document)["props"] = [{"name": "synthetic", "value": "true"}]

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("synthetic object" in error for error in result.errors)


def test_allows_synthetic_only_when_explicitly_permitted() -> None:
    document = _semantic_document()
    _requirement(document)["props"] = [{"name": "synthetic", "value": "true"}]

    result = validate_oscal_document(document, semantic=True, allow_synthetic=True)

    assert result.valid, result.errors


def test_repeated_synthetic_prop_cannot_mask_an_earlier_true() -> None:
    """OSCAL permits repeated props; each is evaluated independently."""
    document = _semantic_document()
    _requirement(document)["props"] = [
        {"name": "synthetic", "value": "true"},
        {"name": "synthetic", "value": "false"},
    ]

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("synthetic object" in error for error in result.errors)


def test_rejects_synthetic_marked_anywhere_not_just_on_requirements() -> None:
    document = _semantic_document()
    document["system-security-plan"]["system-implementation"]["components"][0][
        "props"
    ] = [{"name": "synthetic", "value": "true"}]

    result = validate_oscal_document(document, semantic=True)

    assert not result.valid
    assert any("synthetic object" in error for error in result.errors)


# ---------------------------------------------------------------------------
# Cross-stage parity
# ---------------------------------------------------------------------------


def _canonical_control(control_id: str, parts: tuple[str, ...] = ("a",)) -> dict:
    return {
        "frontmatter": {
            "control_id": control_id,
            "parameters": [{"param_id": f"{control_id.lower()}_prm_1", "value": "x"}],
        },
        "body_sections": [
            {"heading": f"Part {part}", "content": "narrative"} for part in parts
        ],
    }


def _canonical_plan() -> dict:
    return {"frontmatter": {"plan_id": "p", "title": "T", "system_name": "Test System"}}


def _exported_ssp(control_ids: tuple[str, ...]) -> dict:
    return {
        "system-security-plan": {
            "metadata": {
                "parties": [{"name": "Example Agency"}],
            },
            "system-implementation": {"components": [{"title": "Test System"}]},
            "control-implementation": {
                "implemented-requirements": [
                    {
                        "control-id": cid,
                        "statements": [{"statement-id": f"{cid}_smt.a"}],
                        "set-parameters": [
                            {"param-id": f"{cid}_prm_1", "values": ["x"]}
                        ],
                    }
                    for cid in control_ids
                ]
            },
        }
    }


def test_parity_reports_counts_and_ids_for_each_stage() -> None:
    canonical = summarize_canonical_ssp(
        _canonical_plan(),
        [_canonical_control("AC-1"), _canonical_control("AU-2", ("a", "b"))],
        [{"frontmatter": {"name": "Example Agency"}}],
    )

    assert canonical.control_ids == ["ac-1", "au-2"]
    assert canonical.statement_ids == ["ac-1_smt.a", "au-2_smt.a", "au-2_smt.b"]
    assert canonical.parameter_ids == ["ac-1_prm_1", "au-2_prm_1"]
    assert canonical.party_names == ["Example Agency"]
    assert canonical.counts()["controls"] == 2


def test_canonical_parity_uses_mapped_inheritance_provider_name() -> None:
    canonical = summarize_canonical_ssp(
        _canonical_plan(),
        [],
        [],
        extension_sidecars=[
            {
                "type": "oscal.x.inheritance",
                "frontmatter": {
                    "title": "Example System — Leveraged / Inherited Controls"
                },
                "body_sections": [
                    {"heading": "Provided By", "content": "INHERITANCE-SENTINEL"}
                ],
            }
        ],
    )

    assert canonical.party_names == ["INHERITANCE-SENTINEL"]


def test_parity_accepts_a_lossless_export() -> None:
    canonical = summarize_canonical_ssp(
        _canonical_plan(),
        [_canonical_control("AC-1")],
        [{"frontmatter": {"name": "Example Agency"}}],
    )
    exported = summarize_oscal_ssp(_exported_ssp(("ac-1",)))

    assert verify_oscal_parity(canonical, exported) == []


def test_parity_fails_when_a_control_is_dropped() -> None:
    canonical = summarize_canonical_ssp(
        _canonical_plan(),
        [_canonical_control("AC-1"), _canonical_control("AU-2")],
        [{"frontmatter": {"name": "Example Agency"}}],
    )
    exported = summarize_oscal_ssp(_exported_ssp(("ac-1",)))

    errors = verify_oscal_parity(canonical, exported)

    assert any("control(s) present in canonical but absent" in e for e in errors)
    assert any("control count mismatch" in e for e in errors)


def test_parity_fails_when_a_statement_is_dropped() -> None:
    canonical = summarize_canonical_ssp(
        _canonical_plan(),
        [_canonical_control("AC-1", ("a", "b"))],
        [{"frontmatter": {"name": "Example Agency"}}],
    )
    exported = summarize_oscal_ssp(_exported_ssp(("ac-1",)))

    errors = verify_oscal_parity(canonical, exported)

    assert any("statement(s) present in canonical but absent" in e for e in errors)


def test_parity_fails_when_a_party_is_dropped() -> None:
    canonical = summarize_canonical_ssp(
        _canonical_plan(),
        [_canonical_control("AC-1")],
        [
            {"frontmatter": {"name": "Example Agency"}},
            {"frontmatter": {"name": "Second Org"}},
        ],
    )
    exported = summarize_oscal_ssp(_exported_ssp(("ac-1",)))

    errors = verify_oscal_parity(canonical, exported)

    assert any("party(s) present in canonical but absent" in e for e in errors)


def test_parity_fails_when_the_export_invents_a_control() -> None:
    canonical = summarize_canonical_ssp(
        _canonical_plan(),
        [_canonical_control("AC-1")],
        [{"frontmatter": {"name": "Example Agency"}}],
    )
    exported = summarize_oscal_ssp(_exported_ssp(("ac-1", "ac-2")))

    errors = verify_oscal_parity(canonical, exported)

    assert any("present in OSCAL but absent from canonical" in e for e in errors)


def test_parity_skips_comparison_for_a_documented_placeholder_export() -> None:
    canonical = summarize_canonical_ssp(_canonical_plan(), [], [])
    exported = summarize_oscal_ssp(_exported_ssp(("ac-1",)))

    assert verify_oscal_parity(canonical, exported, allow_synthetic=True) == []
    assert verify_oscal_parity(canonical, exported) != []


def test_parity_detects_parameter_value_drift_at_a_stable_id() -> None:
    """A changed ODP value with an unchanged param-id must be flagged (#43)."""
    canonical = summarize_canonical_ssp(
        _canonical_plan(),
        [_canonical_control("AC-1")],
        [{"frontmatter": {"name": "Example Agency"}}],
    )
    exported = _exported_ssp(("ac-1",))
    # Same param-id, different value than canonical's "x".
    exported["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ][0]["set-parameters"][0]["values"] = ["TAMPERED"]
    summarized = summarize_oscal_ssp(exported)

    errors = verify_oscal_parity(canonical, summarized)

    assert any("parameter value drift" in e for e in errors)


def test_parity_detects_statement_count_drift_within_a_present_control() -> None:
    """A control present in both stages but with fewer statements is flagged."""
    canonical = summarize_canonical_ssp(
        _canonical_plan(),
        [_canonical_control("AC-1", ("a", "b", "c"))],
        [{"frontmatter": {"name": "Example Agency"}}],
    )
    # Exported has the control but only one statement.
    exported = summarize_oscal_ssp(_exported_ssp(("ac-1",)))

    errors = verify_oscal_parity(canonical, exported)

    assert any("statement count drift for control" in e for e in errors)


def test_parity_detects_component_title_drift() -> None:
    canonical = summarize_canonical_ssp(
        _canonical_plan(),
        [_canonical_control("AC-1")],
        [{"frontmatter": {"name": "Example Agency"}}],
    )
    exported = _exported_ssp(("ac-1",))
    exported["system-security-plan"]["system-implementation"]["components"][0][
        "title"
    ] = "Renamed System"
    summarized = summarize_oscal_ssp(exported)

    errors = verify_oscal_parity(canonical, summarized)

    assert any("component" in e for e in errors)


def test_parity_matches_component_title_from_system_note() -> None:
    """When a system note supplies the name, parity uses it (no false mismatch)."""
    canonical = summarize_canonical_ssp(
        {"frontmatter": {"plan_id": "p", "title": "Plan Title"}},
        [_canonical_control("AC-1")],
        [{"frontmatter": {"name": "Example Agency"}}],
        system_data={"frontmatter": {"name": "Real System", "note_type": "system"}},
    )
    exported = _exported_ssp(("ac-1",))
    exported["system-security-plan"]["system-implementation"]["components"][0][
        "title"
    ] = "Real System"
    summarized = summarize_oscal_ssp(exported)

    assert verify_oscal_parity(canonical, summarized) == []
