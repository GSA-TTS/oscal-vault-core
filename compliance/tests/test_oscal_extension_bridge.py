"""Tests for the oscal.x.* -> native OSCAL assembly bridge (#46 follow-up).

The project prefers OSCAL core fields over custom extensions for
interoperability. These tests pin that each extension note is mapped onto the
correct native assembly and that the resulting SSP remains schema-valid and
reference-coherent.
"""

from __future__ import annotations

from compliance.lib import oscal_extension_bridge as bridge
from compliance.lib.export_oscal import canonical_to_oscal_ssp
from compliance.lib.validate_oscal import validate_oscal_document


def _boundary_note() -> dict:
    return {
        "type": "oscal.x.boundary",
        "frontmatter": {"type": "oscal.x.boundary", "title": "Sys — Boundary"},
        "body_sections": [
            {
                "heading": "Network Architecture",
                "content": "Three-tier VPC in GovCloud.",
            },
            {
                "heading": "Types of Users",
                "content": (
                    "| Role | Privilege |\n| --- | --- |\n"
                    "| Administrator | full |\n| Auditor | read-only |"
                ),
            },
        ],
    }


def _relationship_note() -> dict:
    return {
        "type": "oscal.x.relationship",
        "frontmatter": {
            "type": "oscal.x.relationship",
            "title": "Sys — Interconnections",
        },
        "body_sections": [
            {"heading": "System Interconnections", "content": "Connects to login.gov."},
            {
                "heading": "Ports, Protocols, and Services",
                "content": (
                    "| Ports (T or U) | Protocols | Services |\n"
                    "| --- | --- | --- |\n"
                    "| 443 (T) | HTTPS | API |\n| 53 (U) | DNS | Resolution |"
                ),
            },
        ],
    }


def _diagram_note() -> dict:
    return {
        "type": "oscal.x.diagram",
        "frontmatter": {
            "type": "oscal.x.diagram",
            "title": "Sys — Diagrams",
            "media_files": ["arch.png", "dataflow.png"],
        },
        "body_sections": [{"heading": "Diagrams", "content": "![[arch.png]]"}],
    }


def _inheritance_note() -> dict:
    return {
        "type": "oscal.x.inheritance",
        "frontmatter": {
            "type": "oscal.x.inheritance",
            "title": "Sys — Leveraged / cloud.gov",
        },
        "body_sections": [
            {"heading": "Direction", "content": "provided-by"},
            {"heading": "Provided By", "content": "INHERITANCE-SENTINEL"},
        ],
    }


# ---------------------------------------------------------------------------
# Unit: each converter maps to its native assembly
# ---------------------------------------------------------------------------


def test_network_architecture_from_boundary():
    na = bridge.network_architecture_from_boundary(_boundary_note())
    assert na == {"description": "Three-tier VPC in GovCloud."}


def test_users_from_boundary_table():
    users = bridge.users_from_boundary(_boundary_note())
    titles = {u["title"] for u in users}
    assert {"Administrator", "Auditor"} <= titles
    assert all("uuid" in u for u in users)


def test_data_flow_from_relationship():
    df = bridge.data_flow_from_relationship(_relationship_note())
    assert df and "login.gov" in df["description"]


def test_protocols_from_relationship_parses_ports_and_transport():
    protocols = bridge.protocols_from_relationship(_relationship_note())
    by_name = {p["name"]: p for p in protocols}
    assert "HTTPS" in by_name
    https = by_name["HTTPS"]
    assert https["port-ranges"][0] == {"start": 443, "end": 443, "transport": "TCP"}
    dns = by_name["DNS"]
    assert dns["port-ranges"][0]["transport"] == "UDP"


def test_protocol_runtime_trace_preserves_unparsed_port_residual():
    note = _relationship_note()
    note["body_sections"][1]["content"] = (
        "Ports | Protocols | Services\n--- | --- | ---\n"
        "443 TCP and Vendor-managed | HTTPS | API"
    )

    result = bridge.protocols_from_relationship_traced(note)

    assert result.value[0]["port-ranges"][0]["start"] == 443
    assert result.preserved_extensions[0]["residual"] == ["Vendor-managed"]
    assert {event.operator for event in result.events} >= {
        "gfm_table_parse",
        "port_expression_parse",
    }


def test_protocols_without_leading_pipes_have_native_oscal_parity():
    note = _relationship_note()
    note["body_sections"][1]["content"] = (
        "Ports | Protocols | Services\n--- | --- | ---\n443 (T) | HTTPS | API"
    )

    protocols = bridge.protocols_from_relationship(note)

    assert protocols == [
        {
            "name": "HTTPS",
            "title": "API",
            "port-ranges": [{"start": 443, "end": 443, "transport": "TCP"}],
        }
    ]


def test_malformed_protocol_table_is_not_stringified_into_oscal():
    note = _relationship_note()
    note["body_sections"][1]["content"] = (
        "Ports | Protocols | Services\n--- | ---\n443 (T) | HTTPS | API"
    )

    assert bridge.protocols_from_relationship(note) == []


def test_malformed_user_table_is_not_stringified_into_oscal():
    note = _boundary_note()
    note["body_sections"][1]["content"] = "Role | Privilege\n---\nAdministrator | full"

    assert bridge.users_from_boundary(note) == []


def test_diagrams_and_resources_cross_reference():
    diagrams, resources = bridge.diagrams_and_resources_from_diagram(_diagram_note())
    assert len(diagrams) == 2
    assert len(resources) == 2
    resource_uuids = {r["uuid"] for r in resources}
    # Each diagram links to a real resource uuid.
    for diagram in diagrams:
        href = diagram["links"][0]["href"].lstrip("#")
        assert href in resource_uuids


def test_leveraged_authorizations_emit_party_and_authorization():
    leveraged, parties = bridge.leveraged_authorizations_from_inheritance(
        [_inheritance_note()]
    )
    assert len(leveraged) == 1
    assert len(parties) == 1
    assert leveraged[0]["party-uuid"] == parties[0]["uuid"]
    assert leveraged[0]["title"] == "INHERITANCE-SENTINEL"


def test_inheritance_provider_table_exports_value_not_markdown():
    note = _inheritance_note()
    note["body_sections"] = [
        {
            "heading": "Provided By",
            "content": "Provider | Scope\n--- | ---\ncloud.gov | platform",
        }
    ]

    leveraged, parties = bridge.leveraged_authorizations_from_inheritance([note])

    assert leveraged[0]["title"] == "cloud.gov"
    assert parties[0]["name"] == "cloud.gov"
    assert all(
        "|" not in value for value in (leveraged[0]["title"], parties[0]["name"])
    )


def test_malformed_inheritance_table_does_not_leak_into_oscal():
    note = _inheritance_note()
    note["body_sections"] = [
        {
            "heading": "Provided By",
            "content": "Provider | Scope\n---\ncloud.gov | platform",
        }
    ]

    assert bridge.leveraged_authorizations_from_inheritance([note]) == ([], [])


# ---------------------------------------------------------------------------
# Integration: wired SSP is schema-valid AND reference-coherent
# ---------------------------------------------------------------------------


def _plan() -> dict:
    return {"frontmatter": {"plan_id": "sys", "title": "Sys SSP", "controls_parsed": 1}}


def _control() -> dict:
    return {"frontmatter": {"control_id": "AC-1"}}


def test_wired_ssp_contains_native_assemblies_and_is_valid():
    ssp = canonical_to_oscal_ssp(
        _plan(),
        "sys",
        control_sidecars=[_control()],
        extension_sidecars=[
            _boundary_note(),
            _relationship_note(),
            _diagram_note(),
            _inheritance_note(),
        ],
    )
    doc = ssp["system-security-plan"]
    sc = doc["system-characteristics"]
    si = doc["system-implementation"]

    assert sc["network-architecture"]["description"] == "Three-tier VPC in GovCloud."
    assert "login.gov" in sc["data-flow"]["description"]
    assert len(sc["authorization-boundary"]["diagrams"]) == 2
    assert si["components"][0]["protocols"]
    assert len(si["leveraged-authorizations"]) == 1
    assert len(doc["back-matter"]["resources"]) == 2
    # Recovered users replace the generic placeholder.
    assert {u["title"] for u in si["users"]} >= {"Administrator", "Auditor"}

    # Schema-valid and all synthesized references resolve (leveraged party uuid).
    result = validate_oscal_document(ssp, semantic=True)
    assert result.valid, result.errors


def test_sparse_vault_without_extensions_still_valid():
    ssp = canonical_to_oscal_ssp(_plan(), "sys", control_sidecars=[_control()])
    doc = ssp["system-security-plan"]
    sc = doc["system-characteristics"]
    assert "network-architecture" not in sc
    assert "data-flow" not in sc
    assert "back-matter" not in doc
    result = validate_oscal_document(ssp, semantic=True)
    assert result.valid, result.errors


def test_runtime_trace_contains_only_executed_operators():
    events = []
    derivations = []
    canonical_to_oscal_ssp(
        _plan(),
        "sys",
        control_sidecars=[_control()],
        extension_sidecars=[_boundary_note(), _relationship_note()],
        lineage_events=events,
        lineage_derivations=derivations,
    )

    operators = {event["operator"] for event in events}
    assert "gfm_table_parse" in operators
    assert "port_expression_parse" in operators
    assert "markdown_list_parse" not in operators
    assert "generated_callout_filter" not in operators


def test_runtime_trace_preserves_port_residual_through_ssp_export():
    relationship = _relationship_note()
    relationship["body_sections"][1]["content"] = (
        "Ports | Protocols | Services\n--- | --- | ---\n"
        "443 TCP and Vendor-managed | HTTPS | API"
    )
    preserved = []
    derivations = []
    canonical_to_oscal_ssp(
        _plan(),
        "sys",
        control_sidecars=[_control()],
        extension_sidecars=[relationship],
        preserved_extensions=preserved,
        lineage_derivations=derivations,
    )

    assert preserved[0]["residual"] == ["Vendor-managed"]
    protocol_derivation = next(
        item
        for item in derivations
        if item["mapping_source"] == "oscal_x_relationship.ports_protocols_services"
    )
    assert protocol_derivation["preserved_extensions"] == preserved


def test_component_capability_declares_component_scoped_controls():
    """An OSBAPI-style offering is a native capability, not an oscal.x note."""
    component = {
        "frontmatter": {
            "name": "Managed database",
            "component_type": "service",
        }
    }
    controls = [
        {
            "frontmatter": {
                "control_id": "AC-2",
                "statement_ids": ["ac-2_smt.a"],
            }
        }
    ]
    from compliance.lib.export_oscal import canonical_to_oscal_component_definition

    doc = canonical_to_oscal_component_definition(component, control_sidecars=controls)
    cd = doc["component-definition"]
    capability = cd["capabilities"][0]
    assert (
        capability["incorporates-components"][0]["component-uuid"]
        == cd["components"][0]["uuid"]
    )
    requirement = capability["control-implementations"][0]["implemented-requirements"][
        0
    ]
    assert requirement["control-id"] == "ac-2"
    result = validate_oscal_document(doc, semantic=True)
    assert result.valid, result.errors


def test_crm_and_customer_responsibility_emit_native_export():
    """Provider/consumer CRM text becomes by-component.export, not an extension."""
    control = {
        "frontmatter": {
            "control_id": "AC-2",
            "origination": ["inherited"],
            "inherited_from": "[[Components/managed-idp|Managed IdP]]",
            "customer_responsibilities": [
                "Customer must review application accounts annually."
            ],
            "crm": {
                "inheritance": "Hybrid",
                "assessment_objective": "Verify tenant account review evidence.",
            },
        }
    }
    doc = canonical_to_oscal_ssp(_plan(), "sys", control_sidecars=[control])
    by_component = doc["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ][0]["by-components"][0]
    export = by_component["export"]
    assert export["provided"]
    assert export["responsibilities"]
    assert (
        export["responsibilities"][0]["provided-uuid"] == export["provided"][0]["uuid"]
    )
    result = validate_oscal_document(doc, semantic=True)
    assert result.valid, result.errors


def test_active_adapter_claims_consume_unique_source_sentinels():
    """Every active bridge claim visibly consumes its declared source property."""
    boundary = _boundary_note()
    boundary["body_sections"][0]["content"] = "BOUNDARY-SENTINEL"
    boundary["body_sections"][1]["content"] = (
        "| Role | Privilege |\n| --- | --- |\n| USER-SENTINEL | full |"
    )
    relationship = _relationship_note()
    relationship["body_sections"][0]["content"] = "FLOW-SENTINEL"
    relationship["body_sections"][1]["content"] = (
        "| Ports (T or U) | Protocols | Services |\n"
        "| --- | --- | --- |\n| 443 (T) | PROTOCOL-SENTINEL | API |"
    )
    doc = canonical_to_oscal_ssp(
        _plan(),
        "sys",
        control_sidecars=[_control()],
        extension_sidecars=[boundary, relationship, _inheritance_note()],
    )["system-security-plan"]
    assert (
        doc["system-characteristics"]["network-architecture"]["description"]
        == "BOUNDARY-SENTINEL"
    )
    assert doc["system-characteristics"]["data-flow"]["description"] == "FLOW-SENTINEL"
    assert doc["system-implementation"]["users"][0]["title"] == "USER-SENTINEL"
    assert (
        doc["system-implementation"]["components"][0]["protocols"][0]["name"]
        == "PROTOCOL-SENTINEL"
    )
    assert (
        doc["system-implementation"]["leveraged-authorizations"][0]["title"]
        == "INHERITANCE-SENTINEL"
    )


def test_component_control_selection_uses_existing_component_link():
    """Capability controls derive from control.component, not a duplicate index."""
    from compliance.scripts.export_oscal import _controls_for_component

    component = {"frontmatter": {"name": "Managed database"}}
    controls = [
        (
            None,
            {
                "frontmatter": {
                    "control_id": "AC-2",
                    "component": "[[Components/managed-database|Managed database]]",
                }
            },
        ),
        (None, {"frontmatter": {"control_id": "AU-2", "component": "Other"}}),
    ]
    selected = _controls_for_component(component, controls)
    assert [c["frontmatter"]["control_id"] for c in selected] == ["AC-2"]


def test_component_export_has_capability_when_controls_are_linked():
    """The executable CLI projection exposes real offering capabilities: only controls whose
    ``component`` link names the component are projected, and a control for another component is not."""
    from compliance.lib.export_oscal import canonical_to_oscal_component_definition
    from compliance.scripts.export_oscal import _controls_for_component

    component = {
        "frontmatter": {
            "type": "oscal.component_definition",
            "note_type": "component",
            "name": "Example Service",
            "title": "Example Service",
            "component_type": "service",
        }
    }

    def control(cid: str, owner: str) -> tuple:
        return (
            f"{cid}.json",
            {
                "type": "oscal.control",
                "control_id": cid,
                "component": f"[[Components/{owner}]]",
                "implementation_status": "implemented",
                "statements": [],
                "origination": ["system-specific"],
            },
        )

    controls = [
        control("AC-01", "example-service"),
        control("AC-02", "example-service"),
        control("AU-01", "other-component"),
    ]
    selected = _controls_for_component(component, controls)
    assert sorted(c["control_id"] for c in selected) == ["AC-01", "AC-02"]

    definition = canonical_to_oscal_component_definition(
        component, control_sidecars=selected
    )["component-definition"]
    capabilities = definition.get("capabilities") or []
    assert capabilities
    requirements = capabilities[0]["control-implementations"][0][
        "implemented-requirements"
    ]
    assert len(requirements) == 2
