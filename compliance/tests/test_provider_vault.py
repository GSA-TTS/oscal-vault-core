"""Unit tests for compliance.lib.provider_vault (#52)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from compliance.lib import provider_vault as pv


def test_statement_label_supports_smt_and_stmt() -> None:
    assert pv.statement_label("ac-1_smt.a") == "a"
    assert pv.statement_label("ac-2.1_stmt.b") == "b"
    with pytest.raises(ValueError, match="unrecognised statement id"):
        pv.statement_label("ac-1")


def test_vault_control_id_and_note_id() -> None:
    assert pv.vault_control_id("ac-2.5") == "AC-2(5)"
    assert pv.note_id("ac-2.5") == "AC-02(05)"
    assert pv.note_id("AC-2(5)") == "AC-02(05)"


def test_render_and_parse_statement_parts_roundtrip() -> None:
    sections = [
        (None, [("Platform Provider", "Whole-control narrative.")]),
        (
            "a",
            [
                ("Platform Provider", "Provider part a narrative."),
                ("This System", "Customer part a narrative."),
            ],
        ),
    ]
    rendered = pv.render_statement_parts(sections)
    parsed = pv.parse_statement_parts(rendered)
    assert parsed == {
        (None, "Platform Provider"): "Whole-control narrative.",
        ("a", "Platform Provider"): "Provider part a narrative.",
        ("a", "This System"): "Customer part a narrative.",
    }


def test_render_empty_statement_parts() -> None:
    rendered = pv.render_statement_parts([(None, [("Provider", "")])])
    assert pv.EMPTY in rendered
    assert pv.parse_statement_parts(rendered) == {}


def test_check_prose_rejects_headings_and_whitespace() -> None:
    with pytest.raises(ValueError, match="whitespace"):
        pv.render_statement_parts([("a", [("Provider", " padded ")])])
    with pytest.raises(ValueError, match="heading"):
        pv.render_statement_parts([("a", [("Provider", "# injected heading")])])


def test_ssp_and_vault_facts_roundtrip(tmp_path: Path) -> None:
    comp_uuid = "11111111-1111-4111-8111-111111111111"
    ev_uuid = "22222222-2222-4222-8222-222222222222"
    req_uuid = "33333333-3333-4333-8333-333333333333"
    aliases = {
        comp_uuid: {
            "id": "this-system",
            "title": "This System",
            "origination": "system-specific",
        }
    }
    ssp_doc = {
        "system-security-plan": {
            "metadata": {
                "title": "Synthetic Plan",
                "last-modified": "2026-01-01T00:00:00Z",
            },
            "system-characteristics": {"description": "Old description"},
            "system-implementation": {"components": []},
            "control-implementation": {
                "implemented-requirements": [
                    {
                        "uuid": req_uuid,
                        "control-id": "ac-1",
                        "props": [
                            {"name": "implementation-status", "value": "implemented"},
                            {"name": "control-origination", "value": "system-specific"},
                            {"name": "fy26-evidence-resource-uuid", "value": ev_uuid},
                        ],
                        "set-parameters": [
                            {"param-id": "ac-1_prm_1", "values": ["annually"]}
                        ],
                        "links": [{"href": f"#{ev_uuid}", "rel": "evidence"}],
                        "by-components": [],
                        "statements": [
                            {
                                "statement-id": "ac-1_smt.a",
                                "uuid": "44444444-4444-4444-8444-444444444444",
                                "by-components": [
                                    {
                                        "component-uuid": comp_uuid,
                                        "uuid": "55555555-5555-4555-8555-555555555555",
                                        "description": "Access control policy prose.",
                                        "implementation-status": {
                                            "state": "implemented"
                                        },
                                        "props": [{"name": "Rule_Id", "value": "R-1"}],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
            "back-matter": {
                "resources": [
                    {
                        "uuid": ev_uuid,
                        "title": "Evidence Doc",
                        "props": [
                            {"name": "sha256", "value": "abcd"},
                            {"name": "media-type", "value": "text/markdown"},
                        ],
                        "rlinks": [{"href": "docs/evidence.md"}],
                    }
                ]
            },
        }
    }

    # Build a synthetic vault directory matching the SSP
    vault_dir = tmp_path / "vault"
    (vault_dir / "Evidence").mkdir(parents=True)
    (vault_dir / "Controls").mkdir(parents=True)
    (vault_dir / "Components").mkdir(parents=True)
    (vault_dir / "Systems").mkdir(parents=True)
    (vault_dir / "Plans").mkdir(parents=True)

    (vault_dir / "Evidence/evidence-doc.md").write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "evidence_id": ev_uuid,
                "title": "Evidence Doc",
                "sha256": "abcd",
                "media_type": "text/markdown",
                "source_uri": "docs/evidence.md",
            }
        )
        + "---\n# Evidence Doc\n",
        encoding="utf-8",
    )
    (vault_dir / "Components/this-system.md").write_text(
        "---\nstatus: operational\n---\n# This System\n\n## Description\n\nComponent desc.\n\n## Rules\n\n- `Rule_Id` (rule remark): R-1\n\n## Protocols\n\n- https (HTTPS): 443/TCP\n",
        encoding="utf-8",
    )
    (vault_dir / "Systems/system.md").write_text(
        "---\nname: Synthetic System\n---\n# System\n\n## Description\n\nUpdated system description.\n",
        encoding="utf-8",
    )
    (vault_dir / "Plans/plan.md").write_text(
        "---\nplan_id: synthetic-plan-id\n---\n# Plan\n",
        encoding="utf-8",
    )
    ctrl_front = {
        "oscal_control_id": "ac-1",
        "oscal_uuid": req_uuid,
        "implementation_status": "implemented",
        "origination": ["system-specific"],
        "parameters": [{"param_id": "ac-1_prm_1", "value": "annually"}],
        "evidence": ["[[Evidence/evidence-doc]]"],
        "evidence_resource_props": [ev_uuid],
        "statement_labels": ["a"],
        "provider_responses": [
            {
                "part": "a",
                "provider": "This System",
                "status": "implemented",
                "rule_ids": ["R-1"],
            }
        ],
    }
    ctrl_body = pv.render_statement_parts(
        [("a", [("This System", "Access control policy prose.")])]
    )
    (vault_dir / "Controls/AC-01.md").write_text(
        "---\n" + yaml.safe_dump(ctrl_front) + "---\n\n" + ctrl_body,
        encoding="utf-8",
    )

    ssp_facts = pv.facts_from_ssp(ssp_doc, aliases)
    vault_facts = pv.facts_from_vault(vault_dir)
    assert ssp_facts == vault_facts

    enriched = pv.enrich_ssp_from_vault(ssp_doc, vault_dir, aliases)
    assert pv.facts_from_ssp(enriched, aliases) == vault_facts
    assert (
        enriched["system-security-plan"]["system-characteristics"]["description"]
        == "Updated system description."
    )
    assert enriched["system-security-plan"]["system-characteristics"]["system-ids"] == [
        {"id": "synthetic-plan-id"}
    ]
    projected = pv.project_baseline_ssp(enriched, ["AC-1"], expected_count=1)
    assert (
        len(
            projected["system-security-plan"]["control-implementation"][
                "implemented-requirements"
            ]
        )
        == 1
    )
