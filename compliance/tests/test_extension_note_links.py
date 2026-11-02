"""Extension notes are part of the graph: system/owner links and Index navigation (ADR-030)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from compliance.lib import vault_graph as vg
from compliance.lib.vault import (
    VaultIndex,
    build_narrative_notes,
    build_vault,
    extension_link_fields,
)
from compliance.scripts.export_obsidian_vault import write_vault

ROOT = Path(__file__).resolve().parents[2]
SSP = ROOT / "compliance/tests/fixtures/oscal/ssp-source.example.json"


_FIRST_SECTION = {
    "oscal_x_boundary": "description",
    "oscal_x_relationship": "interconnections",
    "oscal_x_inheritance": "direction",
    "oscal_x_runbook": "discipline",
    "oscal_x_decision": "decision_kind",
    "oscal_x_privacy": "pii_processing",
    "oscal_x_crm": "crm_narrative",
}


def _objects(*names: str) -> dict:
    table = {"headers": ["A", "B"], "rows": [["x", "y"]]}
    return {
        "objects": [
            {"object_name": n, "properties": {_FIRST_SECTION[n]: table}} for n in names
        ],
        "summary": {},
    }


def _index() -> VaultIndex:
    index = VaultIndex()
    index.systems["Example System"] = "[[Systems/example-system]]"
    index.plans["p"] = "[[Plans/example-system-ssp]]"
    return index


def test_extension_link_fields_keeps_the_owner_key_even_when_unknown():
    assert extension_link_fields("[[Systems/s]]", None) == {
        "system": "[[Systems/s]]",
        "owner": None,
    }
    assert extension_link_fields(None, None) == {"owner": None}
    both = extension_link_fields("[[Systems/s]]", "[[Organizations/o]]")
    assert both == {"system": "[[Systems/s]]", "owner": "[[Organizations/o]]"}


def test_narrative_notes_link_to_their_system_and_owner():
    notes = build_narrative_notes(
        _objects(
            "oscal_x_boundary",
            "oscal_x_relationship",
            "oscal_x_inheritance",
            "oscal_x_runbook",
            "oscal_x_decision",
            "oscal_x_privacy",
            "oscal_x_crm",
        ),
        index=_index(),
        system_id="sys-1",
        owner_link="[[Organizations/example-owner]]",
    )
    assert {n.relpath.split("/")[0] for n in notes} == {
        "Boundaries",
        "Relationships",
        "Inheritance",
        "Runbooks",
        "Decisions",
        "Privacy",
        "CRM",
    }
    for note in notes:
        assert note.frontmatter["system"] == "[[Systems/example-system]]"
        assert note.frontmatter["owner"] == "[[Organizations/example-owner]]"
        assert (
            note.frontmatter["system_id"] == "sys-1"
        )  # the UUID is kept, not replaced


def test_unknown_owner_stays_null_and_missing_system_omits_the_key():
    notes = build_narrative_notes(
        _objects("oscal_x_boundary"), index=_index(), system_id="s"
    )
    assert notes[0].frontmatter["owner"] is None and "system" in notes[0].frontmatter
    empty = build_narrative_notes(
        _objects("oscal_x_boundary"), index=VaultIndex(), system_id="s"
    )
    assert (
        "system" not in empty[0].frontmatter and empty[0].frontmatter["owner"] is None
    )


@pytest.fixture(scope="module")
def vault(tmp_path_factory):
    from compliance.tests.test_export_obsidian_vault import _fake_narrative

    ssp = json.loads(SSP.read_text(encoding="utf-8"))
    notes = build_vault(
        ssp, plan_id="sample-system-ssp-2024", narrative=_fake_narrative()
    )
    out = tmp_path_factory.mktemp("v") / "sample"
    write_vault(notes, out)
    return out


def test_generated_extension_notes_are_no_longer_orphans(vault):
    graph = vg.build_graph(vault)
    extension = [
        n for n in graph.notes if graph.types.get(n, "").startswith("oscal.x.")
    ]
    assert extension, "fixture should produce extension notes"
    assert [n for n in vg.orphans(graph) if n in extension] == []
    for rel in extension:
        assert "Systems/sample-system.md" in graph.out[rel], rel
        assert graph.inn[rel], f"{rel} has no inbound link"


def test_index_navigation_lists_system_plan_and_every_extension_note(vault):
    text = (vault / "Index.md").read_text(encoding="utf-8")
    assert "<!-- navigate:begin -->" in text and "<!-- navigate:end -->" in text
    front = yaml.safe_load(text.split("---\n", 2)[1])
    assert front["type"] == "okf.index"  # still exempt-typed; the links are additive
    graph = vg.build_graph(vault)
    for rel in graph.notes:
        if graph.types.get(rel, "").startswith("oscal.x."):
            assert rel in graph.out["Index.md"], f"Index does not link {rel}"
    assert "Systems/sample-system.md" in graph.out["Index.md"]


def test_the_orphan_count_drops_to_zero_for_content_notes(vault):
    metrics = vg.metrics(vg.build_graph(vault))
    assert metrics["orphans_content"] == 0, metrics


def test_existing_fields_and_dataview_queries_are_untouched(vault):
    text = (vault / "Index.md").read_text(encoding="utf-8")
    assert "```dataview" in text and "## Dashboards" in text and "- Controls:" in text
    ssp = json.loads(SSP.read_text(encoding="utf-8"))
    from compliance.tests.test_export_obsidian_vault import _fake_narrative

    again = build_vault(
        ssp, plan_id="sample-system-ssp-2024", narrative=_fake_narrative()
    )
    boundary = next(n for n in again if n.relpath.startswith("Boundaries/"))
    assert boundary.frontmatter["type"] == "oscal.x.boundary"
    assert boundary.frontmatter["status"] == "active"
    assert list(boundary.frontmatter).count("owner") == 1


def test_generation_is_deterministic(vault, tmp_path):
    from compliance.tests.test_export_obsidian_vault import _fake_narrative

    ssp = json.loads(SSP.read_text(encoding="utf-8"))
    write_vault(
        build_vault(ssp, plan_id="sample-system-ssp-2024", narrative=_fake_narrative()),
        tmp_path / "again",
    )
    a = {p.relative_to(vault).as_posix(): p.read_bytes() for p in vault.rglob("*.md")}
    b = {
        p.relative_to(tmp_path / "again").as_posix(): p.read_bytes()
        for p in (tmp_path / "again").rglob("*.md")
    }
    assert a == b


def test_extension_frontmatter_still_validates_against_the_schemas(vault):
    import subprocess
    import sys

    res = subprocess.run(
        [
            sys.executable,
            str(ROOT / "compliance/scripts/validate_frontmatter.py"),
            "--vault",
            str(vault),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_owner_flows_from_the_system_note_into_every_extension_note():
    """The synthetic SSP names no system owner, so exercise the real wiring: give the SSP one and
    check the owner link reaches the extension notes through build_vault, not just the helper."""
    from compliance.tests.test_export_obsidian_vault import _fake_narrative

    ssp = json.loads(SSP.read_text(encoding="utf-8"))
    meta = ssp["system-security-plan"]["metadata"]
    # The generator indexes owners by party name via ``role-ids`` on the party itself.
    meta["parties"][0]["role-ids"] = ["system-owner"]
    notes = build_vault(
        ssp, plan_id="sample-system-ssp-2024", narrative=_fake_narrative()
    )
    system = next(n for n in notes if n.relpath.startswith("Systems/"))
    owner = system.frontmatter.get("owner")
    assert owner, "fixture change should give the system an owner"
    extension = [
        n
        for n in notes
        if str(n.frontmatter.get("type", "")).startswith("oscal.x.")
        and not n.relpath.startswith(
            "_meta/"
        )  # skeleton templates, not generated content
    ]
    assert extension
    assert all(n.frontmatter["owner"] == owner for n in extension)
