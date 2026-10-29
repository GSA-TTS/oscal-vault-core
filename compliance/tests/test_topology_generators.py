"""Topology parsing, CycloneDX + C4 generation, vault write-back and the rubric scorer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.topology import (  # noqa: E402
    DATASTORE,
    EXTERNAL,
    HUMAN,
    PROCESS,
    build_topology,
    classify,
    parse_pps_rows,
)
from compliance.lib.topology_c4 import render_container, render_context  # noqa: E402
from compliance.lib.topology_cyclonedx import to_cyclonedx_blueprint  # noqa: E402
from compliance.lib.topology_eval import (  # noqa: E402
    band,
    cdx_checks,
    compare_c4,
    consistency,
    names_match,
    parse_c4,
    split_args,
    validate_generated_c4,
)
from compliance.lib.topology_vault import apply_blocks  # noqa: E402

TABLE = """
| External Service (Y/N) | Direction | Boundary Crossings (Y/N) | Source | Destination | Ports (T or U) | Protocols | Services | Purpose | Encrypted (Y/N) | Data Sensitivity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Y | Inbound | Y | Web Browser | Admin application | tcp/443 | HTTPS | Demo UI | Use the app | Y | Moderate |
| N | Outbound | N | Admin application | Cloud.gov Redis | T/6379 | Redis | Key/Value | Cache | Yes (TLS) | Low |
| Y | Outbound | Y | API Worker | AWS SES | T/443 | https | AWS SES | Send email | Y | Moderate |
| Y | Outbound | N | Reference Figure 9-1 | Admin application | | | | | | |
|  |  |  |  |  |  |  |  |  |  |  |
"""


@pytest.fixture
def topo():
    return build_topology("demo", "Demo", parse_pps_rows(TABLE))


@pytest.mark.parametrize(
    "name,kind",
    [
        ("Web Browser", HUMAN),
        ("API Worker", PROCESS),
        ("Cloud.gov RDS", DATASTORE),
        ("AWS SES", EXTERNAL),
        ("GitHub", EXTERNAL),
    ],
)
def test_classify(name, kind):
    assert classify(name) == kind


def test_service_column_skips_external_service_flag():
    row = parse_pps_rows(TABLE)[0]
    assert row.service == "Demo UI"
    assert row.encrypted and row.boundary_crossing


def test_captions_and_blank_rows_are_dropped(topo):
    assert "Reference Figure 9-1" not in {c.name for c in topo.components}
    assert len(topo.flows) == 3


def test_flows_carry_security_attributes(topo):
    cache = next(f for f in topo.flows if "redis" in f.destination)
    assert (cache.port, cache.protocol, cache.encrypted) == (6379, "Redis", True)


def test_c4_output_is_deterministic_and_parses(topo):
    for render in (render_context, render_container):
        text = render(topo)
        assert text == render(build_topology("demo", "Demo", parse_pps_rows(TABLE)))
        report = validate_generated_c4(text)
        assert report["syntax_errors"] == [] and report["has_title"]


def test_context_has_no_internal_containers(topo):
    ctx = parse_c4(render_context(topo))
    labels = {e.label for e in ctx.elements.values()}
    assert "Demo" in labels and "Admin application" not in labels
    assert "AWS SES" in labels and "Web Browser" in labels


def test_container_places_store_in_platform_boundary(topo):
    ref = parse_c4(render_container(topo))
    store = next(e for e in ref.elements.values() if e.label == "Cloud.gov Redis")
    assert store.kind == "datastore" and "platform" in store.boundary.lower()


def test_cyclonedx_and_c4_agree(topo):
    blueprint = to_cyclonedx_blueprint(topo)
    assert consistency(topo, blueprint) == {
        "component_agreement": 1.0,
        "flow_agreement": 1.0,
    }
    checks = cdx_checks(blueprint)
    assert all(v == 1.0 for v in checks.values())


def test_split_args_respects_quoted_commas():
    assert split_args('a, "b, c", "d"') == ["a", "b, c", "d"]


def test_parse_c4_reports_unknown_alias_and_unbalanced_boundary():
    bad = 'C4Container\n  title T\n  Boundary(b, "B") {\n  Container(a, "A", "t")\n  Rel(a, ghost, "x")\n'
    errors = parse_c4(bad).errors
    assert any("ghost" in e for e in errors) and "unclosed boundary" in errors


def test_names_match_is_token_containment():
    assert names_match("Cloud.gov Redis", "API Redis")
    assert not names_match("Cloud.gov MySQL", "API Redis")


def test_reference_comparison_finds_missing_and_ambiguous(topo):
    ref = parse_c4(
        "C4Container\n  title R\n"
        '  Container(admin, "Admin application", "Flask")\n'
        '  ContainerDb(redis_a, "Admin Redis", "Redis")\n'
        '  ContainerDb(redis_b, "API Redis", "Redis")\n'
        '  Container(proxy, "Egress Proxy", "Caddy")\n'
        '  Rel(admin, redis_a, "cache", "Redis / 6379")\n'
        '  Rel(admin, proxy, "calls", "HTTPS / 443")\n'
    )
    result = compare_c4(topo, ref, "container")
    assert "Egress Proxy" in result["findings"]["missing_from_generated"]
    assert "Cloud.gov Redis" in result["findings"]["ambiguous_matches"]
    assert 0 < result["metrics"]["element_recall"] < 1
    assert result["metrics"]["relationship_recall"] == 1.0


def test_band_picks_highest_reached_threshold():
    bands = [{"min": 0, "score": 0}, {"min": 0.5, "score": 2}, {"min": 0.9, "score": 4}]
    assert (band(0.1, bands), band(0.5, bands), band(0.95, bands)) == (0, 2, 4)


def test_apply_blocks_replaces_only_generated_region_and_is_idempotent():
    note = "# T\n\n![[img.png]]\n\n```mermaid\nC4Container\n```\n"
    once = apply_blocks(
        note,
        {
            "c4-context": "C4Context\n  title A",
            "c4-container": "C4Container\n  title B",
        },
    )
    assert once.startswith(note.rstrip("\n"))
    twice = apply_blocks(
        once,
        {
            "c4-context": "C4Context\n  title A",
            "c4-container": "C4Container\n  title B",
        },
    )
    assert twice == once
    changed = apply_blocks(once, {"c4-context": "C4Context\n  title NEW"})
    assert "title NEW" in changed and "title A" not in changed and "title B" in changed
    assert changed.count("<!-- generated:c4-context begin -->") == 1


def test_evaluator_scores_a_synthetic_system_and_writes_idempotently(tmp_path):
    from compliance.scripts.evaluate_topology_diagrams import main

    system = tmp_path / "demo"
    (system / "Relationships").mkdir(parents=True)
    (system / "Diagrams").mkdir()
    (system / "Relationships" / "r.md").write_text(TABLE, encoding="utf-8")
    note = system / "Diagrams" / "demo-diagram.md"
    note.write_text("# Demo\n\n![[img.png]]\n", encoding="utf-8")
    report = tmp_path / "report.json"

    assert main(["--vault", str(tmp_path), "--all", "--out", str(report)]) == 0
    assert main(["--vault", str(tmp_path), "--all", "--baseline", str(report)]) == 0
    assert main(["--vault", str(tmp_path), "--all", "--write-vault"]) == 0
    first = note.read_text(encoding="utf-8")
    assert "![[img.png]]" in first and "C4Container" in first
    main(["--vault", str(tmp_path), "--all", "--write-vault"])
    assert note.read_text(encoding="utf-8") == first


def test_container_view_never_guesses_technology(topo):
    text = render_container(topo)
    assert 'Container(admin_application, "Admin application", "")' in text
    assert 'ContainerDb(cloud_gov_redis, "Cloud.gov Redis", "Redis")' in text


def test_long_edge_labels_are_shortened_on_a_word_boundary():
    from compliance.lib.topology_c4 import MAX_LABEL, _short

    long = "Outbound via Caddy Proxy Sending application metrics and logs to the vendor"
    out = _short(long)
    assert len(out) <= MAX_LABEL and out.endswith("\u2026")
    assert not out[:-1].endswith(" ") and long.startswith(out[:-1])
    assert _short("short") == "short"


def test_container_view_lists_connected_components_together(topo):
    ref = parse_c4(render_container(topo))
    order = list(ref.elements)
    # The Redis store is used by the admin app; it must not be listed after unrelated ones.
    assert abs(order.index("admin_application") - order.index("cloud_gov_redis")) <= 3


def test_structure_check_flags_duplicate_alias_and_unknown_alias():
    text = (
        "C4Container\n  title T\n"
        '  Container(a, "A", "x")\n  Container(a, "A2", "x")\n'
        '  Rel(a, ghost, "uses")\n'
    )
    errors = validate_generated_c4(text)["syntax_errors"]
    assert "duplicate alias a" in errors
    assert any("ghost" in e for e in errors)


VIA_TABLE = """
| Direction | Boundary Crossings (Y/N) | Source | Destination | Ports (T or U) | Protocols | Services | Purpose | Encrypted (Y/N) | Data Sensitivity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Outbound | Y | Admin application | Login.gov | T/443 | https | Login.gov | Outbound via Caddy Proxy Sending authentication requests | Y | Moderate |
| Outbound | Y | API Worker | New Relic | T/443 | https | New Relic | Outbound via Caddy Proxy Sending metrics | Y | Moderate |
| Outbound | N | API Worker | AWS SES | T/443 | https | AWS SES | Sending email | Y | Moderate |
"""


def test_via_names_an_intermediary_only_when_the_row_states_one():
    from compliance.lib.topology import via_intermediary

    rows = parse_pps_rows(VIA_TABLE)
    assert [via_intermediary(r) for r in rows] == ["Caddy Proxy", "Caddy Proxy", None]


def test_via_splits_the_flow_through_one_shared_proxy():
    topo = build_topology("demo", "Demo", parse_pps_rows(VIA_TABLE), resolve_via=True)
    assert [c.name for c in topo.components].count("Caddy Proxy") == 1
    pairs = {(f.source, f.destination) for f in topo.flows}
    assert ("admin-application", "caddy-proxy") in pairs
    assert ("caddy-proxy", "login-gov") in pairs
    assert ("admin-application", "login-gov") not in pairs
    first, second = (
        next(f for f in topo.flows if f.source == "admin-application"),
        next(f for f in topo.flows if f.destination == "login-gov"),
    )
    assert (first.crosses_boundary, second.crosses_boundary) == (False, True)
    assert first.port == second.port == 443 and first.protocol == second.protocol


def test_via_is_off_by_default_and_never_invents_components():
    topo = build_topology("demo", "Demo", parse_pps_rows(VIA_TABLE))
    assert "Caddy Proxy" not in {c.name for c in topo.components}
    plain = build_topology("demo", "Demo", parse_pps_rows(TABLE))
    assert len(plain.flows) == 3


def test_names_do_not_match_across_different_kinds_of_component():
    assert not names_match("Admin application", "Admin Egress Proxy")
    assert not names_match("Cloud.gov Redis", "Cloud.gov MySQL")
    assert names_match("Cloud.gov Redis", "API Redis")
    assert names_match("Admin application", "US Notify Admin")
