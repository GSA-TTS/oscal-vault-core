"""Consistency guards between the ODCS datacontract and the code that consumes
it — closing the drift gaps identified in docs/INTEROPERABILITY.md §9.

Covers:
  - D-2 (#39): every property ``oscalPath`` declared in the contract points at
    an OSCAL model the exporter actually knows how to produce, so a declared
    path cannot silently reference a model with no exporter.
  - D-17 (#45): every object's ``destinationFolder`` matches the folder the
    vault builders actually write to (casing included), and the
    ``physicalName`` prefix agrees with ``destinationFolder``.
  - D-2 / #39 (strengthened): the exported SSP structure contains all
    top-level OSCAL keys that ``system-security-plan`` oscalPath roots imply.
    See also ``_information_type()`` in compliance/lib/export_oscal.py for
    the matching comment clarifying that oscalPath is descriptive-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.heading_parser import load_contract  # noqa: E402
from compliance.lib.vault import _NARRATIVE_NOTE_SPECS  # noqa: E402
from compliance.lib.export_oscal import (  # noqa: E402
    EXPORTER_MAPPING_ADAPTERS,
    canonical_to_oscal_ssp,
)
from compliance.lib.oscal_mapping_registry import (  # noqa: E402
    MappingValidationError,
    load_mappings,
    normalize_oscal_path,
    validate_mapping_claims,
    validate_schema_path,
)
from compliance.lib.oscal_lineage import mapping_lineage_report, validate_lineage_report  # noqa: E402

_CONTRACT = _REPO_ROOT / "compliance" / "datacontracts" / "fedramp-ssp-v1.yaml"

# OSCAL model roots the exporter can produce (canonical_to_oscal_ssp /
# _component_definition). oscalPath values name one of these top-level models
# (or the project's `extension.*` namespace for oscal.x.* notes).
_KNOWN_OSCAL_ROOTS = {
    "system-security-plan",
    "control-implementation",
    "component-definition",
    "party",
    "extension",  # oscal.x.* extension namespace
    "system-characteristics",
    "system-implementation",
    "back-matter",
}


def test_every_oscal_path_names_a_known_model_root():
    """D-2 / #39: no property may declare an oscalPath rooted at a model the
    exporter has no way to emit."""
    contract = load_contract(_CONTRACT)
    unknown: list[str] = []
    for obj in contract.destinations():
        for prop in obj.properties:
            path = prop.oscal_path
            if not path:
                continue
            root = path.split(".", 1)[0].split("[", 1)[0]
            if root not in _KNOWN_OSCAL_ROOTS:
                unknown.append(f"{obj.object_id}.{prop.name} -> {path}")
    assert not unknown, (
        f"Contract oscalPath values reference OSCAL roots the exporter cannot produce (D-2 / #39): {unknown}"
    )


def test_active_native_contract_mappings_are_claimed_by_exporter_adapters():
    """#65: active mapping declarations cannot outrun typed exporter adapters."""
    validate_mapping_claims(_CONTRACT, EXPORTER_MAPPING_ADAPTERS)


def test_active_native_contract_mappings_have_complete_lineage_claims():
    report = mapping_lineage_report(_CONTRACT)
    assert validate_lineage_report(report) == []
    assert (
        report["counters"]["active_mappings"]
        == report["counters"]["covered_active_mappings"]
    )


def test_extension_authoring_mappings_keep_native_targets_and_legacy_aliases():
    """Native export targets do not erase the existing oscal.x authoring links."""
    mappings = {mapping.source: mapping for mapping in load_mappings(_CONTRACT)}
    boundary = mappings["oscal_x_boundary.description"]
    relationship = mappings["oscal_x_relationship.ports_protocols_services"]
    inheritance = mappings["oscal_x_inheritance.provided_by"]

    assert boundary.classification == "authoring_extension"
    assert boundary.native_paths == (
        "system-characteristics.network-architecture.description",
    )
    assert boundary.legacy_extension_path == "extension.boundary.description"
    assert relationship.native_paths == (
        "system-implementation.components[].protocols",
    )
    assert inheritance.legacy_extension_path == "extension.inheritance.provided-by"


def test_external_services_and_crm_targets_match_consumed_sources():
    mappings = {mapping.source: mapping for mapping in load_mappings(_CONTRACT)}
    capability = mappings["oscal_component_definition.external_services"]
    crm = mappings["oscal_control.crm"]

    assert capability.native_paths == ("component-definition.components[0].props",)
    assert crm.native_paths == (
        "control-implementation.implemented-requirements[].by-components[].export.provided",
        "control-implementation.implemented-requirements[].by-components[].export.responsibilities",
    )
    assert mappings["oscal_x_crm.crm_narrative"].classification == "unimplemented"


def test_party_contact_and_role_targets_match_native_metadata_model():
    mappings = {mapping.source: mapping for mapping in load_mappings(_CONTRACT)}

    assert mappings["oscal_party.role_ids"].native_paths == (
        "system-security-plan.metadata.responsible-parties[].role-id",
    )
    assert mappings["oscal_party.email_address"].native_paths == (
        "system-security-plan.metadata.parties[].email-addresses[0]",
    )
    assert mappings["oscal_party.phone"].native_paths == (
        "system-security-plan.metadata.parties[].telephone-numbers[0].number",
    )
    assert {
        mappings[source].adapter
        for source in (
            "oscal_party.role_ids",
            "oscal_party.email_address",
            "oscal_party.phone",
        )
    } == {"party"}


def test_unclaimed_active_mapping_fails_validation(tmp_path: Path):
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        "kind: DataContract\nschema:\n"
        "  - id: example\n    customProperties:\n"
        "      - property: mappingClass\n        value: native_core\n"
        "      - property: mappingStatus\n        value: active\n"
        "      - property: exportAdapter\n        value: missing-adapter\n"
        "    properties:\n      - name: field\n        customProperties:\n"
        "          - property: oscalPath\n            value: missing.assembly\n",
        encoding="utf-8",
    )
    try:
        validate_mapping_claims(contract, EXPORTER_MAPPING_ADAPTERS)
    except MappingValidationError as exc:
        assert "unknown OSCAL path root" in str(exc)
    else:
        raise AssertionError("unclaimed active mapping unexpectedly passed")


def test_active_object_property_missing_path_fails_closed(tmp_path: Path):
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        "schema:\n  - id: example\n    customProperties:\n"
        "      - property: mappingClass\n        value: native_core\n"
        "      - property: mappingStatus\n        value: active\n"
        "      - property: exportAdapter\n        value: adapter\n"
        "    properties:\n      - name: field\n        customProperties: []\n",
        encoding="utf-8",
    )
    with pytest.raises(MappingValidationError, match="has no native OSCAL path"):
        validate_mapping_claims(contract, {"adapter": {}})


@pytest.mark.parametrize(
    "key,value", [("mappingClass", "native-ish"), ("mappingStatus", "enabled")]
)
def test_malformed_mapping_enums_fail_closed(tmp_path: Path, key: str, value: str):
    contract = tmp_path / "contract.yaml"
    contract.write_text(
        "schema:\n  - id: example\n    customProperties:\n"
        "      - property: mappingClass\n        value: native_core\n"
        "      - property: mappingStatus\n        value: active\n"
        "      - property: exportAdapter\n        value: adapter\n"
        "    properties:\n      - name: field\n        customProperties:\n"
        "          - property: oscalPath\n            value: system-characteristics.description\n"
        f"          - property: {key}\n            value: {value}\n",
        encoding="utf-8",
    )
    with pytest.raises(MappingValidationError, match=f"unknown {key}"):
        validate_mapping_claims(contract, {"adapter": {}})


def test_schema_path_validation_normalizes_array_selectors():
    schema_dir = _REPO_ROOT / "compliance/schemas/oscal-1.1.3"
    path = "system-implementation.components[0].protocols"
    validate_schema_path(path, schema_dir)
    assert normalize_oscal_path(path) == "system-implementation.components.protocols"
    with pytest.raises(MappingValidationError, match="unknown OSCAL path"):
        validate_schema_path(
            "system-implementation.components[].not-a-field", schema_dir
        )


def test_destination_folder_matches_physical_name():
    """D-17 / #45: destinationFolder and physicalName must agree (casing)."""
    contract = load_contract(_CONTRACT)
    mismatches: list[str] = []
    for obj in contract.destinations():
        folder = obj.destination_folder
        physical = obj.physical_name
        if folder and physical and not physical.startswith(folder):
            mismatches.append(
                f"{obj.object_id}: folder={folder!r} physical={physical!r}"
            )
    assert not mismatches, (
        f"destinationFolder/physicalName casing divergence (D-17 / #45): {mismatches}"
    )


def test_narrative_note_folders_match_contract_destination_folders():
    """D-17 / #45: the folder each oscal.x.* narrative note is written to must
    equal the contract's declared destinationFolder for that object."""
    contract = load_contract(_CONTRACT)
    obj_by_id = {o.object_id: o for o in contract.destinations()}
    mismatches: list[str] = []
    for spec in _NARRATIVE_NOTE_SPECS:
        obj = obj_by_id.get(spec["object"])
        if obj is None or not obj.destination_folder:
            continue
        # spec["folder"] is written without a trailing slash; contract carries one.
        declared = obj.destination_folder.rstrip("/")
        emitted = spec["folder"].rstrip("/")
        if declared != emitted:
            mismatches.append(
                f"{spec['object']}: contract={declared!r} vault={emitted!r}"
            )
    assert not mismatches, (
        f"vault folder vs contract destinationFolder divergence (D-17 / #45): {mismatches}"
    )


def test_ssp_export_covers_all_oscal_path_roots():
    """D-2 / #39 (strengthened): canonical_to_oscal_ssp() always emits the
    four top-level OSCAL keys that every ``system-security-plan`` oscalPath
    root implies.

    This is a structural guard — it checks key presence only, not values.
    oscalPath is descriptive metadata in the contract (see comment in
    _information_type() in compliance/lib/export_oscal.py); the contract does
    not drive the exporter. This test ensures the exporter output stays
    structurally consistent with that declared shape.
    """
    # Minimal canonical data sufficient to produce a valid SSP skeleton.
    minimal_canonical = {
        "frontmatter": {
            "type": "oscal.ssp",
            "plan_id": "test-ssp",
            "system_name": "Test System",
            "fips_199": "moderate",
            "status": "operational",
            "version": "1.0",
        }
    }
    # This is a structural shape test with no controls, so it opts into the
    # documented synthetic placeholder explicitly; the production default is
    # fail-closed (#51).
    result = canonical_to_oscal_ssp(
        minimal_canonical, plan_id="test-ssp", allow_synthetic=True
    )
    assert "system-security-plan" in result, (
        "Top-level 'system-security-plan' key missing"
    )
    ssp = result["system-security-plan"]
    required_keys = {
        "metadata",
        "system-characteristics",
        "system-implementation",
        "control-implementation",
    }
    missing = required_keys - set(ssp.keys())
    assert not missing, (
        f"canonical_to_oscal_ssp() output is missing required top-level SSP keys (D-2 / #39): {missing}"
    )
