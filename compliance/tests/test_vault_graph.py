"""Link resolution and graph metrics (ADR-029): path-aware, ambiguity-reporting, regression-safe."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from compliance.lib import vault_graph as vg
from compliance.scripts import validate_vault

ROOT = Path(__file__).resolve().parents[2]


def note(vault: Path, rel: str, body: str = "", front: str = "") -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (f"---\n{front}\n---\n" if front else "") + body
    path.write_text(text, encoding="utf-8")
    return path


# --- resolution -----------------------------------------------------------------------------


def test_same_name_in_two_folders_stays_two_notes(tmp_path):
    """The defect: stem matching made Components/x look linked because Systems/x was."""
    note(tmp_path, "Systems/data.gov.md", "[[Components/data.gov]]")
    note(tmp_path, "Components/data.gov.md", "")
    note(tmp_path, "Plans/p.md", "[[Systems/data.gov]]")
    graph = vg.build_graph(tmp_path)
    assert "Components/data.gov.md" in graph.inn and graph.inn[
        "Components/data.gov.md"
    ] == {"Systems/data.gov.md"}
    assert graph.inn["Systems/data.gov.md"] == {"Plans/p.md"}


def test_orphaned_component_is_no_longer_hidden_by_a_same_named_system(tmp_path):
    note(tmp_path, "Systems/app.md", "")
    note(tmp_path, "Components/app.md", "")
    note(tmp_path, "Plans/p.md", "[[Systems/app]]")
    issues = validate_vault.check_orphan_notes(tmp_path)
    assert issues == ["Components/app.md: orphan (no inbound or outbound links)"]


def test_bare_ambiguous_link_is_reported_and_resolves_to_nothing(tmp_path):
    note(tmp_path, "Systems/foo.md", "")
    note(tmp_path, "Components/foo.md", "")
    note(tmp_path, "ref.md", "see [[foo]]")
    graph = vg.build_graph(tmp_path)
    assert graph.out.get("ref.md", set()) == set()
    assert vg.ambiguous_bare_links(graph) == {
        "foo": ["Components/foo.md", "Systems/foo.md"]
    }
    assert len(validate_vault.check_duplicate_note_names(tmp_path)) == 1


def test_path_suffix_and_case_and_alias_and_heading_resolve(tmp_path):
    note(tmp_path, "Controls/AC-02.md", "")
    note(
        tmp_path,
        "a.md",
        "[[controls/ac-02]] [[Controls/AC-02|AC-2]] [[Controls/AC-02#Part a]]",
    )
    note(tmp_path, "b.md", "[[AC-02.md]] [[AC-02^blockid]]")
    graph = vg.build_graph(tmp_path)
    assert graph.inn["Controls/AC-02.md"] == {"a.md", "b.md"}
    assert not graph.unresolved and not graph.ambiguous


def test_unresolved_links_are_recorded_with_their_source(tmp_path):
    note(tmp_path, "a.md", "[[Controls/Missing]] and [[nowhere]]")
    graph = vg.build_graph(tmp_path)
    assert sorted(graph.unresolved) == [
        ("a.md", "Controls/Missing"),
        ("a.md", "nowhere"),
    ]


def test_self_links_headings_and_external_do_not_create_edges_or_errors(tmp_path):
    note(tmp_path, "a.md", "[[a]] [[#Heading]] [[]] [[https://example.invalid/x]]")
    graph = vg.build_graph(tmp_path)
    assert graph.edge_count == 0 and not graph.unresolved


def test_links_in_fenced_code_blocks_are_ignored(tmp_path):
    note(tmp_path, "a.md", "```\n[[ghost]]\n```\n~~~\n[[ghost2]]\n~~~\n")
    note(tmp_path, "b.md", "")
    graph = vg.build_graph(tmp_path)
    assert not graph.unresolved and graph.edge_count == 0


def test_frontmatter_links_count_as_edges(tmp_path):
    note(tmp_path, "Controls/AC-01.md", "", front="system: '[[Systems/s]]'")
    note(tmp_path, "Systems/s.md", "")
    graph = vg.build_graph(tmp_path)
    assert graph.out["Controls/AC-01.md"] == {"Systems/s.md"}


def test_embeds_resolve_to_attachments_and_to_notes(tmp_path):
    (tmp_path / "Diagrams" / "media").mkdir(parents=True)
    (tmp_path / "Diagrams" / "media" / "fig1.png").write_bytes(b"x")
    note(
        tmp_path,
        "Diagrams/d.md",
        "![[fig1.png]] ![[Diagrams/media/fig1.png]] ![[missing.png]]",
    )
    graph = vg.build_graph(tmp_path)
    assert graph.unresolved == [("Diagrams/d.md", "missing.png")]


def test_template_notes_and_dot_directories_are_not_part_of_the_graph(tmp_path):
    note(tmp_path, "_meta/templates/Control.md", "[[Controls/{{ id }}]]")
    note(tmp_path, ".snapshots/current/x.md", "[[ghost]]")
    note(tmp_path, "a.md", "")
    graph = vg.build_graph(tmp_path)
    assert graph.notes == ["a.md"] and not graph.unresolved


def test_okf_infrastructure_and_ignore_flag_are_exempt_from_orphans(tmp_path):
    note(tmp_path, "Index.md", "# i", front="type: okf.index")
    note(tmp_path, "m.md", "x", front="gardener-ignore: true")
    note(tmp_path, "lonely.md", "x", front="type: oscal.x.diagram")
    graph = vg.build_graph(tmp_path)
    assert vg.orphans(graph) == ["lonely.md"]
    assert len(vg.orphans(graph, honour_exemptions=False)) == 3


# --- metrics and baseline ---------------------------------------------------------------------


def _vault(tmp_path):
    note(tmp_path, "Systems/s.md", "[[Plans/p]]")
    note(tmp_path, "Plans/p.md", "")
    note(tmp_path, "Diagrams/d.md", "x", front="type: oscal.x.diagram")
    note(tmp_path, "Boundaries/b.md", "[[ghost]]", front="type: oscal.x.boundary")
    note(tmp_path, "Index.md", "x", front="type: okf.index")
    return tmp_path


def test_metrics_report_is_stable_and_complete(tmp_path):
    report = vg.metrics(vg.build_graph(_vault(tmp_path)))
    assert report["notes"] == 5 and report["edges"] == 1
    # Diagrams/d.md has no links at all; Boundaries/b.md's only link is dangling, so it is not
    # connected to anything either (it is also counted under unresolved_links).
    assert report["orphans_content"] == 2
    assert report["orphans_by_folder"] == {"Boundaries": 1, "Diagrams": 1}
    assert report["orphans_exempt"] == 1  # Index.md
    assert report["unresolved_links"] == 1 and report["unresolved_targets"] == 1
    assert list(report) == list(
        vg.metrics(vg.build_graph(tmp_path))
    )  # deterministic order


def test_regressions_flag_only_worse_numbers():
    base = {"orphans_content": 5, "unresolved_links": 2, "ambiguous_links": 0}
    assert vg.regressions(dict(base), base) == []
    better = {"orphans_content": 1, "unresolved_links": 0, "ambiguous_links": 0}
    assert vg.regressions(better, base) == []
    worse = {"orphans_content": 6, "unresolved_links": 2, "ambiguous_links": 1}
    assert vg.regressions(worse, base) == [
        "orphans_content: 5 -> 6",
        "ambiguous_links: 0 -> 1",
    ]


# --- CLI ---------------------------------------------------------------------------------------

AUDIT = ROOT / "compliance" / "scripts" / "audit_vault_graph.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(AUDIT), *args], capture_output=True, text=True
    )


def test_audit_cli_writes_json_and_compares_to_a_baseline(tmp_path):
    vault = _vault(tmp_path / "v")
    out = tmp_path / "metrics.json"
    res = _run("--vault", str(vault), "--out", str(out))
    assert res.returncode == 0
    data = json.loads(out.read_text())
    assert list(data["vaults"]) == ["v"] and data["vaults"]["v"]["orphans_content"] == 2

    assert _run("--vault", str(vault), "--baseline", str(out)).returncode == 0
    note(vault, "Decisions/new.md", "x", front="type: oscal.x.decision")  # a new orphan
    res = _run("--vault", str(vault), "--baseline", str(out))
    assert res.returncode == 1 and "orphans_content: 2 -> 3" in res.stdout + res.stderr


def test_audit_cli_fails_closed_on_missing_vault_or_baseline(tmp_path):
    assert _run("--vault", str(tmp_path / "nope")).returncode == 2
    vault = _vault(tmp_path / "v")
    assert (
        _run("--vault", str(vault), "--baseline", str(tmp_path / "no.json")).returncode
        == 2
    )


def test_audit_cli_lists_findings_with_verbose(tmp_path):
    vault = _vault(tmp_path / "v")
    res = _run("--vault", str(vault), "--list")
    assert "Diagrams/d.md" in res.stdout and "ghost" in res.stdout


@pytest.mark.parametrize("flag", ["--out", "--baseline"])
def test_audit_cli_requires_a_vault_or_workspace_vaults(tmp_path, flag):
    res = subprocess.run(
        [sys.executable, str(AUDIT), flag, str(tmp_path / "x.json")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert res.returncode == 2


def test_names_containing_dots_resolve_as_notes_not_attachments(tmp_path):
    note(tmp_path, "Systems/data.gov.md", "")
    note(tmp_path, "Components/data.gov.md", "")
    note(
        tmp_path,
        "Plans/p.md",
        "[[Systems/data.gov]] [[Components/data.gov]] [[data.gov]]",
    )
    graph = vg.build_graph(tmp_path)
    assert graph.inn["Systems/data.gov.md"] == {"Plans/p.md"}
    assert graph.inn["Components/data.gov.md"] == {"Plans/p.md"}
    assert [t for _s, t, _c in graph.ambiguous] == ["data.gov"]
    assert not graph.unresolved


def test_a_dotted_target_with_no_note_falls_back_to_an_attachment(tmp_path):
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "challenge.gov-image1.png").write_bytes(b"x")
    note(tmp_path, "a.md", "![[challenge.gov-image1.png]] ![[challenge.gov-gone.png]]")
    graph = vg.build_graph(tmp_path)
    assert graph.unresolved == [("a.md", "challenge.gov-gone.png")]


def test_a_note_that_only_embeds_images_is_an_orphan_but_is_labelled_as_such(tmp_path):
    """The old stem check counted ``![[fig.png]]`` as an outbound link and hid these notes. The
    graph view hides attachments by default, so they look disconnected; report them, separately."""
    (tmp_path / "Diagrams" / "media").mkdir(parents=True)
    (tmp_path / "Diagrams" / "media" / "fig1.png").write_bytes(b"x")
    note(tmp_path, "Diagrams/d.md", "![[fig1.png]]", front="type: oscal.x.diagram")
    note(tmp_path, "Diagrams/bare.md", "no links", front="type: oscal.x.diagram")
    graph = vg.build_graph(tmp_path)
    assert vg.orphans(graph) == ["Diagrams/bare.md", "Diagrams/d.md"]
    assert vg.attachment_only(graph) == ["Diagrams/d.md"]
    report = vg.metrics(graph)
    assert report["orphans_content"] == 2 and report["orphans_attachment_only"] == 1


def test_portfolio_graph_scopes_intra_system_paths_and_warns_on_root_obsidian(tmp_path):
    """R22 PR 4: opening examples/vault/ as one vault resolves intra-system paths and warns (never fails) on cross-system bare ambiguity and root .obsidian/."""
    (tmp_path / ".obsidian").mkdir()
    note(tmp_path, "sys-a/Controls/AC-02.md", "")
    note(tmp_path, "sys-b/Controls/AC-02.md", "")
    note(tmp_path, "sys-a/Systems/sys-a.md", "[[Controls/AC-02]] [[AC-02]]")
    graph, warnings = vg.build_portfolio_graph(tmp_path)
    assert graph.inn["sys-a/Controls/AC-02.md"] == {"sys-a/Systems/sys-a.md"}
    assert any("root-level .obsidian/" in w for w in warnings)
    assert any("cross-system ambiguity" in w and "AC-02" in w for w in warnings)

    res = _run("--portfolio", str(tmp_path))
    assert res.returncode == 0
    assert "WARN:" in res.stdout

