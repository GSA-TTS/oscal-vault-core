"""Tests for Layer 1 OSCAL conformance validation (ADR-019, phase R17).

Validates that ``compliance.lib.validate_oscal`` correctly accepts
schema-conformant OSCAL documents and rejects non-conformant ones against the
vendored NIST OSCAL 1.1.3 JSON Schema — offline, no Docker/DB.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from compliance.lib.validate_oscal import (
    DEFAULT_OSCAL_VERSION,
    SUPPORTED_OSCAL_VERSIONS,
    SchemaNotAvailableError,
    available_document_types,
    detect_document_type,
    validate_oscal_document,
    validate_oscal_file,
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _minimal_valid_ssp() -> dict:
    """A minimal SSP that satisfies the NIST 1.1.3 ssp schema's required graph."""
    return {
        "system-security-plan": {
            "uuid": _uuid(),
            "metadata": {
                "title": "Test SSP",
                "last-modified": "2026-07-20T00:00:00Z",
                "version": "1.0",
                "oscal-version": "1.1.3",
            },
            "import-profile": {"href": "#profile"},
            "system-characteristics": {
                "system-ids": [{"id": "sys-1"}],
                "system-name": "Test System",
                "description": "d",
                "system-information": {
                    "information-types": [{"title": "x", "description": "d"}]
                },
                "security-sensitivity-level": "low",
                "security-impact-level": {
                    "security-objective-confidentiality": "low",
                    "security-objective-integrity": "low",
                    "security-objective-availability": "low",
                },
                "status": {"state": "operational"},
                "authorization-boundary": {"description": "d"},
            },
            "system-implementation": {
                "users": [{"uuid": _uuid()}],
                "components": [
                    {
                        "uuid": _uuid(),
                        "type": "this-system",
                        "title": "t",
                        "description": "d",
                        "status": {"state": "operational"},
                    }
                ],
            },
            "control-implementation": {
                "description": "d",
                "implemented-requirements": [{"uuid": _uuid(), "control-id": "ac-1"}],
            },
        }
    }


def _minimal_valid_component_definition() -> dict:
    return {
        "component-definition": {
            "uuid": _uuid(),
            "metadata": {
                "title": "Test CD",
                "last-modified": "2026-07-20T00:00:00Z",
                "version": "1.0",
                "oscal-version": "1.1.3",
            },
            "components": [
                {
                    "uuid": _uuid(),
                    "type": "service",
                    "title": "Comp",
                    "description": "d",
                }
            ],
        }
    }


class TestDocumentTypeDetection:
    def test_detects_ssp_wrapper(self):
        assert detect_document_type(_minimal_valid_ssp()) == "system-security-plan"

    def test_detects_component_definition_wrapper(self):
        doc = _minimal_valid_component_definition()
        assert detect_document_type(doc) == "component-definition"

    def test_ignores_schema_key(self):
        doc = dict(_minimal_valid_ssp())
        doc["$schema"] = "https://example/schema.json"
        assert detect_document_type(doc) == "system-security-plan"

    def test_unknown_wrapper_returns_none(self):
        assert detect_document_type({"catalog": {}}) is None

    def test_available_types_include_ssp_and_component(self):
        types = available_document_types()
        assert "system-security-plan" in types
        assert "component-definition" in types


class TestValidateValidDocuments:
    def test_minimal_ssp_is_valid(self):
        result = validate_oscal_document(_minimal_valid_ssp())
        assert result.valid, result.errors
        assert result.document_type == "system-security-plan"
        assert result.oscal_version == DEFAULT_OSCAL_VERSION

    def test_minimal_component_definition_is_valid(self):
        result = validate_oscal_document(_minimal_valid_component_definition())
        assert result.valid, result.errors
        assert result.document_type == "component-definition"

    def test_result_is_truthy_when_valid(self):
        assert validate_oscal_document(_minimal_valid_ssp())


class TestValidateInvalidDocuments:
    def test_missing_required_fields_reported(self):
        doc = {
            "system-security-plan": {
                "uuid": _uuid(),
                "metadata": {
                    "title": "T",
                    "last-modified": "2026-07-20T00:00:00Z",
                    "version": "1.0",
                    "oscal-version": "1.1.3",
                },
            }
        }
        result = validate_oscal_document(doc)
        assert not result.valid
        # import-profile / system-characteristics / system-implementation /
        # control-implementation are all required by the ssp schema.
        joined = " ".join(result.errors)
        assert "import-profile" in joined
        assert "control-implementation" in joined

    def test_result_is_falsy_when_invalid(self):
        assert not validate_oscal_document({"component-definition": {}})

    def test_errors_are_path_prefixed(self):
        result = validate_oscal_document({"component-definition": {}})
        assert not result.valid
        # every error line is "<location>: <message>"
        assert all(":" in e for e in result.errors)


class TestFailClosedBehavior:
    def test_unrecognized_document_type_raises(self):
        # A catalog wrapper has no vendored schema wired -> fail closed.
        with pytest.raises(ValueError):
            validate_oscal_document({"totally-not-oscal": {}})

    def test_missing_schema_version_raises(self):
        with pytest.raises(SchemaNotAvailableError):
            validate_oscal_document(_minimal_valid_ssp(), oscal_version="9.9.9")


class TestValidateFromFile:
    def test_validate_ssp_file_roundtrip(self, tmp_path: Path):
        path = tmp_path / "ssp.json"
        path.write_text(json.dumps(_minimal_valid_ssp()), encoding="utf-8")
        result = validate_oscal_file(path)
        assert result.valid, result.errors
        assert result.source == str(path)


class TestExportIntegration:
    """The hardened export (#12) must emit schema-valid OSCAL. These assert the
    full IR graph and extension-props mapping pass Layer 1.
    """

    def test_full_ssp_export_is_schema_valid(self):
        from compliance.lib.export_oscal import canonical_to_oscal_ssp

        plan = {
            "frontmatter": {
                "uuid": _uuid(),
                "title": "Full SSP",
                "plan_id": "plan-full",
                "profile_ref": "#profile",
                "version": "1.0",
                "reviewed": "2026-07-20",
                # a leftover extension field that must land as a prop:
                "template_version": "08282018",
            }
        }
        system = {
            "frontmatter": {
                "name": "Full System",
                "fips_199": "moderate",
                "status": "operational",
                "authorization_boundary": "The boundary.",
                "security_objective_confidentiality": "moderate",
                "security_objective_integrity": "moderate",
                "security_objective_availability": "low",
            }
        }
        controls = [
            {
                "frontmatter": {
                    "control_id": "AC-2(1)",
                    "uuid": _uuid(),
                    "implementation_status": "implemented",
                    "origination": ["inherited"],
                },
                "body_sections": [
                    {"heading": "Part a", "content": "narrative a"},
                ],
                "inline_fields": {},
            }
        ]
        ssp = canonical_to_oscal_ssp(
            plan, "plan-full", control_sidecars=controls, system_data=system
        )
        result = validate_oscal_document(ssp)
        assert result.valid, result.errors

        plan_obj = ssp["system-security-plan"]
        irs = plan_obj["control-implementation"]["implemented-requirements"]
        assert irs[0]["control-id"] == "ac-2.1"  # enhancement normalized
        # extension prop preserved on metadata
        prop_names = {p["name"] for p in plan_obj["metadata"].get("props", [])}
        assert "template_version" in prop_names

    def test_ssp_export_with_parties_is_schema_valid(self):
        """Parties/roles recovered from oscal.party notes survive export and
        stay NIST-schema-valid (D-1 / #38)."""
        from compliance.lib.export_oscal import canonical_to_oscal_ssp

        plan = {"frontmatter": {"title": "Party SSP", "plan_id": "plan-party"}}
        system = {
            "frontmatter": {
                "name": "Party System",
                "fips_199": "low",
                "status": "operational",
                # wikilink form, as vault system notes carry it
                "system_owner": "[[Organizations/acme-cloud|Acme Cloud]]",
                "authorizing_official": "[[Organizations/gsa-ao|GSA AO]]",
            }
        }
        parties = [
            {
                "frontmatter": {
                    "name": "Acme Cloud",
                    "org_type": "agency",
                    "acronym": "ACME",
                    "role_ids": ["system-owner", "custom-reviewer"],
                    "email_addresses": ["owner@example.com"],
                    "telephone_numbers": [
                        {"number": "+1-202-555-0100", "type": "work"}
                    ],
                }
            },
            {"frontmatter": {"name": "GSA AO", "org_type": "agency"}},
        ]
        controls = [
            {
                "frontmatter": {
                    "control_id": "AC-1",
                    "uuid": _uuid(),
                    "implementation_status": "implemented",
                    "responsible_role": "[[Organizations/acme-cloud|Acme Cloud]]",
                },
                "body_sections": [{"heading": "Part a", "content": "narrative a"}],
                "inline_fields": {},
            }
        ]
        ssp = canonical_to_oscal_ssp(
            plan,
            "plan-party",
            control_sidecars=controls,
            system_data=system,
            party_sidecars=parties,
        )
        result = validate_oscal_document(ssp)
        assert result.valid, result.errors

        md = ssp["system-security-plan"]["metadata"]
        assert len(md["parties"]) == 2
        party_names = {p["name"] for p in md["parties"]}
        assert party_names == {"Acme Cloud", "GSA AO"}
        acme = next(p for p in md["parties"] if p["name"] == "Acme Cloud")
        assert acme["email-addresses"] == ["owner@example.com"]
        assert acme["telephone-numbers"] == [
            {"number": "+1-202-555-0100", "type": "work"}
        ]
        responsible = {
            item["role-id"]: item["party-uuids"] for item in md["responsible-parties"]
        }
        assert "custom-reviewer" in responsible
        # responsible-parties wired from the system note's role fields
        rp_roles = {rp["role-id"] for rp in md["responsible-parties"]}
        assert "system-owner" in rp_roles
        assert "authorizing-official" in rp_roles
        # per-control responsible-roles present
        ir = ssp["system-security-plan"]["control-implementation"][
            "implemented-requirements"
        ][0]
        assert ir["responsible-roles"][0]["role-id"] == "acme-cloud"
        # statement prose surfaced on by-component description (D-7 / #42)
        assert "narrative a" in ir["by-components"][0]["description"]

    def test_empty_ssp_placeholder_is_marked_synthetic(self):
        """An opted-in controls-less SSP marks its placeholder synthetic (D-11 / #46)."""
        from compliance.lib.export_oscal import canonical_to_oscal_ssp

        plan = {"frontmatter": {"title": "Empty SSP", "plan_id": "plan-empty"}}
        ssp = canonical_to_oscal_ssp(
            plan, "plan-empty", control_sidecars=[], allow_synthetic=True
        )
        result = validate_oscal_document(ssp)
        assert result.valid, result.errors
        ir = ssp["system-security-plan"]["control-implementation"][
            "implemented-requirements"
        ][0]
        prop_names = {p["name"]: p["value"] for p in ir.get("props", [])}
        assert prop_names.get("synthetic") == "true"
        assert "SYNTHETIC" in ir["remarks"]

    def test_empty_ssp_fails_closed_without_opt_in(self):
        """#51: the production default refuses to fabricate a control."""
        from compliance.lib.export_oscal import (
            MissingControlsError,
            canonical_to_oscal_ssp,
        )

        plan = {"frontmatter": {"title": "Empty SSP", "plan_id": "plan-empty"}}
        with pytest.raises(MissingControlsError):
            canonical_to_oscal_ssp(plan, "plan-empty", control_sidecars=[])

    def test_full_component_definition_export_is_schema_valid(self):
        from compliance.lib.export_oscal import (
            canonical_to_oscal_component_definition,
        )

        cd = canonical_to_oscal_component_definition(
            {
                "frontmatter": {
                    "uuid": _uuid(),
                    "name": "IAM",
                    "component_type": "service",
                    "status": "operational",
                    "version": "1.0",
                    "reviewed": "2026-07-20",
                },
                "body_sections": [{"content": "IAM service."}],
            }
        )
        result = validate_oscal_document(cd)
        assert result.valid, result.errors

    def test_empty_controls_still_valid(self):
        """A plan with no controls still emits a schema-valid SSP (placeholder IR) when allowed."""
        from compliance.lib.export_oscal import canonical_to_oscal_ssp

        ssp = canonical_to_oscal_ssp(
            {"frontmatter": {"uuid": _uuid(), "title": "Empty", "plan_id": "e"}},
            "e",
            control_sidecars=[],
            allow_synthetic=True,
        )
        assert validate_oscal_document(ssp).valid


class TestVersionAgility:
    """#13: the exporter emits, and the validator checks, multiple OSCAL
    versions. 1.0.x has a stricter information-type (requires the C/I/A impact
    triad) that 1.1.x relaxed; a single exporter must satisfy both.
    """

    def test_supported_versions_include_both(self):
        assert "1.1.3" in SUPPORTED_OSCAL_VERSIONS
        assert "1.0.4" in SUPPORTED_OSCAL_VERSIONS

    def _build_ssp(self, version: str) -> dict:
        from compliance.lib.export_oscal import canonical_to_oscal_ssp

        return canonical_to_oscal_ssp(
            {"frontmatter": {"uuid": _uuid(), "title": "V", "plan_id": "v"}},
            "v",
            control_sidecars=[
                {
                    "frontmatter": {
                        "control_id": "AC-1",
                        "uuid": _uuid(),
                        "implementation_status": "implemented",
                    },
                    "body_sections": [],
                    "inline_fields": {},
                }
            ],
            system_data={"frontmatter": {"name": "Sys", "fips_199": "low"}},
            oscal_version=version,
        )

    def test_ssp_valid_against_1_1_3(self):
        ssp = self._build_ssp("1.1.3")
        assert ssp["system-security-plan"]["metadata"]["oscal-version"] == "1.1.3"
        assert validate_oscal_document(ssp, oscal_version="1.1.3").valid

    def test_ssp_valid_against_1_0_4(self):
        ssp = self._build_ssp("1.0.4")
        assert ssp["system-security-plan"]["metadata"]["oscal-version"] == "1.0.4"
        # 1.0.4 requires the C/I/A impact triad on each information-type.
        info = ssp["system-security-plan"]["system-characteristics"][
            "system-information"
        ]["information-types"][0]
        assert "confidentiality-impact" in info
        result = validate_oscal_document(ssp, oscal_version="1.0.4")
        assert result.valid, result.errors

    def test_component_definition_valid_against_1_0_4(self):
        from compliance.lib.export_oscal import (
            canonical_to_oscal_component_definition,
        )

        cd = canonical_to_oscal_component_definition(
            {
                "frontmatter": {
                    "uuid": _uuid(),
                    "name": "C",
                    "component_type": "service",
                    "version": "1.0",
                },
                "body_sections": [{"content": "desc"}],
            },
            oscal_version="1.0.4",
        )
        assert cd["component-definition"]["metadata"]["oscal-version"] == "1.0.4"
        assert validate_oscal_document(cd, oscal_version="1.0.4").valid
