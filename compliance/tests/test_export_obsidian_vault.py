"""Regression tests for the Obsidian vault generator.

These tests run in any fresh clone with only committed inputs (no .docx file
required), since the vault generator consumes an OSCAL SSP JSON dict
(`build_vault` is pure; I/O is handled by `compliance/scripts/export_obsidian_vault.py`).

The committed synthetic input ``compliance/tests/fixtures/oscal/ssp-source.example.json`` is
exercised here as the canonical reference input: a regression in the vault
generator would surface as changes to either the file count, structure, or
frontmatter content delivered for those three controls + system + plan +
component + organization. The vault directory itself under ``examples/vault``
is also committed derived output (regenerable from
``compliance/tests/fixtures/oscal/ssp-source.example.json`` via the CLI script) but is not
byte-asserted here since the directory layout is exercised directly.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.vault import build_bases_views, build_vault  # noqa: E402
from compliance.lib.vault import (  # noqa: E402
    _extract_related_controls,
    build_media_notes,
    build_narrative_notes,
    canonical_control_id,
    control_title,
    enrich_system_note_from_narrative,
)

EXAMPLE_SSP_PATH = (
    _REPO_ROOT
    / "compliance"
    / "tests"
    / "fixtures"
    / "oscal"
    / "ssp-source.example.json"
)


class TestCanonicalControlId:
    """Control-id normalization (finding #2/#3): a single canonical form so
    filenames, wikilinks, and cross-SSP joins don't fragment on padding."""

    def test_zero_pads_base_number(self):
        assert canonical_control_id("AC-2") == "AC-02"
        assert canonical_control_id("AC-02") == "AC-02"

    def test_zero_pads_enhancement(self):
        assert canonical_control_id("IA-02(2)") == "IA-02(02)"
        assert canonical_control_id("IA-02(01)") == "IA-02(01)"
        assert canonical_control_id("AT-02(02)") == "AT-02(02)"

    def test_normalizes_spaced_enhancement(self):
        assert canonical_control_id("AC-2 (1)") == "AC-02(01)"

    def test_uppercases_family(self):
        assert canonical_control_id("ac-2") == "AC-02"

    def test_unparseable_id_passthrough(self):
        # Fail-open: never lose an id we can't parse.
        assert canonical_control_id("weird-thing") == "weird-thing"
        assert canonical_control_id("") == ""


class TestControlTitle:
    """Control titles come from the NIST rev5 catalog, not a fabricated stub
    (finding #1)."""

    def test_real_names_from_catalog(self):
        assert control_title("AC-1") == "AC-01 Policy and Procedures"
        assert control_title("AC-2") == "AC-02 Account Management"
        assert control_title("AC-3") == "AC-03 Access Enforcement"

    def test_enhancement_name(self):
        assert control_title("AT-02(02)") == "AT-02(02) Insider Threat"

    def test_unknown_control_falls_back_to_id(self):
        # No fabricated name for a non-catalog control.
        assert control_title("ZZ-99") == "ZZ-99"

    def test_no_more_account_management_stub(self):
        # Regression: previously AC-01/03/17 all read "Account Management".
        assert "Account Management" not in control_title("AC-1")
        assert "Account Management" not in control_title("AC-3")


class TestRelatedControlExtraction:
    """The related-control heuristic must not invent links from strings that
    merely look like control ids (finding across the batch SSPP diagnosis)."""

    @staticmethod
    def _req(desc: str, control_id: str = "AC-02"):
        return {"control-id": control_id, "statements": [{"description": desc}]}

    def test_real_reference_is_extracted(self):
        rel = _extract_related_controls(self._req("See AC-7 for lockout."))
        assert rel == ["AC-07"]

    def test_directive_id_not_extracted(self):
        # "CIO-IT-18-90" must NOT yield [[Controls/IT-18]] (finding: notify).
        rel = _extract_related_controls(self._req("per CIO-IT-18-90 policy"))
        assert rel == []

    def test_non_control_family_ignored(self):
        # SP-800, FIPS-199 etc. are not control families.
        rel = _extract_related_controls(
            self._req("aligned to SP-800 and FIPS-199 guidance")
        )
        assert rel == []

    def test_enhancement_reference_normalized(self):
        rel = _extract_related_controls(self._req("inherits AC-2 (1) behavior"))
        assert rel == ["AC-02(01)"]

    def test_self_reference_excluded(self):
        rel = _extract_related_controls(self._req("see AC-2 above", control_id="AC-2"))
        assert rel == []

    def test_statement_part_citation_maps_to_base_control(self):
        # "AC-2(c)" / "AC-2 (a)" are statement-part citations, not the AC-2(3)
        # enhancement; they must resolve to the base control AC-02, never a
        # dangling [[Controls/AC-02(a)]]. (control_id is CM-06 so AC-02 is not
        # excluded as a self-reference.)
        rel = _extract_related_controls(
            self._req(
                "granted as in AC-2 (a) by managers per AC-2(c)",
                control_id="CM-06",
            )
        )
        assert rel == ["AC-02"]


def test_build_vault_emits_complete_note_set():
    """A 3-control SSP must yield the fixed structural + control + extension notes."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")

    relpaths = {n.relpath for n in notes}

    # Fixed structural notes.
    expected_fixed = {
        "Index.md",
        "Systems/sample-system.md",
        "Components/sample-system.md",
        "Organizations/example-security-office.md",
        "Plans/sample-system-ssp.md",
        "Mappings/implementation-status.md",
        "Mappings/control-origination.md",
        "Mappings/interop-profile.md",
        "_meta/tags.md",
        "_meta/views/controls.base",
        "_meta/views/components.base",
        "_meta/views/relationships.base",
        "_meta/views/inheritance.base",
        "_meta/views/systems.base",
        "_meta/views/plans.base",
        "_meta/views/organizations.base",
        "_meta/templates/Control.md",
        "_meta/templates/System.md",
        "_meta/templates/ControlPart.md",
    }
    missing = expected_fixed - relpaths
    assert not missing, f"missing fixed notes: {sorted(missing)}"

    # Extension type templates (R4).
    expected_extensions = {
        "_meta/templates/Diagram.md",
        "_meta/templates/Evidence.md",
        "_meta/templates/Finding.md",
        "_meta/templates/Inheritance.md",
        "_meta/templates/Boundary.md",
        "_meta/templates/Decision.md",
        "_meta/templates/Relationship.md",
    }
    missing_ext = expected_extensions - relpaths
    assert not missing_ext, f"missing extension templates: {sorted(missing_ext)}"

    # OKF infrastructure (R5).
    expected_okf = {
        "governance/schema.md",
        "governance/validation-rules/implemented-controls-have-evidence.md",
        "governance/validation-rules/diagrams-have-review-dates.md",
    }
    missing_okf = expected_okf - relpaths
    assert not missing_okf, f"missing OKF infrastructure: {sorted(missing_okf)}"

    # One control note per implemented-requirement.
    expected_controls = {"Controls/AC-01.md", "Controls/AC-02.md", "Controls/AU-04.md"}
    missing_controls = expected_controls - relpaths
    assert not missing_controls, f"missing control notes: {sorted(missing_controls)}"

    # No duplicates.
    assert len(relpaths) == len(notes), "duplicate vault notes detected"

    # Total: 3 controls + 19 fixed (including seven raw Bases views) + 7
    # extension templates + 3 OKF infra + 1 Coverage = 33.
    assert len(notes) == 33, f"unexpected note count {len(notes)}"


def test_controls_base_view_is_native_yaml():
    """ADR-025 emits a native Obsidian Bases view, not a Markdown note."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    base = next(n for n in notes if n.relpath == "_meta/views/controls.base")
    assert base.raw is True
    assert not base.render().startswith("---\n")
    document = yaml.safe_load(base.render())
    assert document["filters"]["and"] == ['file.inFolder("Controls")']
    assert [view["name"] for view in document["views"]] == [
        "All controls",
        "Requiring attention",
        "Inherited / hybrid",
    ]


def test_controls_base_matches_obsidian_nested_sequence_serialization():
    """The complete Base matches the expected Obsidian 1.14.4-format fixture."""
    base = next(
        note for note in build_bases_views() if note.relpath.endswith("controls.base")
    )
    fixture = (
        Path(__file__).parent / "fixtures" / "bases" / "obsidian-1.14.4-controls.base"
    )
    assert base.render() == fixture.read_text(encoding="utf-8")


def test_every_queryable_folder_has_a_base_view():
    """Every entity folder (not just Controls) must have a Base-compatible view
    so all vault tables are Dataview-generatable (#44/#50)."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    base_notes = {n.relpath: n for n in notes if n.relpath.endswith(".base")}
    assert set(base_notes) == {
        "_meta/views/controls.base",
        "_meta/views/components.base",
        "_meta/views/relationships.base",
        "_meta/views/inheritance.base",
        "_meta/views/systems.base",
        "_meta/views/plans.base",
        "_meta/views/organizations.base",
    }
    folder_by_base = {
        "controls.base": "Controls",
        "components.base": "Components",
        "relationships.base": "Relationships",
        "inheritance.base": "Inheritance",
        "systems.base": "Systems",
        "plans.base": "Plans",
        "organizations.base": "Organizations",
    }
    for relpath, note in base_notes.items():
        document = yaml.safe_load(note.render())
        name = relpath.rsplit("/", 1)[-1]
        assert document["filters"]["and"] == [
            f'file.inFolder("{folder_by_base[name]}")'
        ]
        assert document["views"], f"{relpath} has no views"
        for view in document["views"]:
            assert view["type"] == "table"
            assert view.get("order"), f"{relpath} view {view['name']} has no order"


def test_every_note_has_valid_yaml_frontmatter():
    """The render() output of every VaultNote must round-trip through a YAML
    frontmatter parser with the expected three-dash delimiters."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    assert notes, "no notes were generated"

    for note in notes:
        if note.raw:
            # Obsidian Bases `.base` files are pure YAML views, not Markdown
            # notes with frontmatter (ADR-025).
            assert note.relpath.endswith(".base")
            assert isinstance(yaml.safe_load(note.render()), dict)
            continue
        rendered = note.render()
        # Must start and end with `---` delimiter lines.
        assert rendered.startswith("---\n"), (
            f"{note.relpath}: missing leading frontmatter delimiter"
        )
        end_of_fm = rendered.index("\n---\n", 4)
        fm_text = rendered[4:end_of_fm]
        parsed = yaml.safe_load(fm_text)
        assert isinstance(parsed, dict), (
            f"{note.relpath}: frontmatter did not parse to a dict"
        )
        assert "type" in parsed, f"{note.relpath}: frontmatter missing `type`"
        # Backward-compat: note_type still present
        assert "note_type" in parsed, f"{note.relpath}: frontmatter missing `note_type`"


def test_control_note_frontmatter_carries_required_schema_fields():
    """Every control note must carry the Required frontmatter fields from the
    schema (type, control_id, family, implementation_status, origination,
    parsed_by). Derived normalized vocabulary belongs in the mapping notes."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    control_notes = [n for n in notes if n.relpath.startswith("Controls/")]
    assert len(control_notes) >= 3, f"expected >=3 controls, got {len(control_notes)}"

    for note in control_notes:
        for field in (
            "type",
            "control_id",
            "family",
            "implementation_status",
            "origination",
            "parsed_by",
        ):
            assert field in note.frontmatter, (
                f"{note.relpath}: missing required frontmatter field {field!r}"
            )

        # Verify type taxonomy
        assert note.frontmatter["type"] == "oscal.control", (
            f"{note.relpath}: expected type=oscal.control"
        )

        # Raw fields remain the source of truth; their normalized targets are
        # derived from the versioned vocabulary mappings (ADR-026), not stored
        # in every note.
        # Control notes now carry the *canonical* zero-padded id (AC-01), while
        # the source IR uses the raw id (AC-1); match on the canonical form.
        from compliance.lib.vault import canonical_control_id

        cid = note.frontmatter["control_id"]
        raw_state = (
            next(
                r
                for r in ssp["system-security-plan"]["control-implementation"][
                    "implemented-requirements"
                ]
                if canonical_control_id(r.get("control-id") or "") == cid
            ).get("implementation-status")
            or {}
        ).get("state")
        raw_flags = next(
            r
            for r in ssp["system-security-plan"]["control-implementation"][
                "implemented-requirements"
            ]
            if canonical_control_id(r.get("control-id") or "") == cid
        ).get("control-origination", [])

        from compliance.lib import vocab

        expected_normalized_status = vocab.map_implementation_status(raw_state).target
        expected_normalized_origination = vocab.map_control_origination(
            raw_flags
        ).target
        assert "normalized_status" not in note.frontmatter
        assert "normalized_origination" not in note.frontmatter
        assert expected_normalized_status
        assert expected_normalized_origination


def test_control_note_includes_statement_narrative_in_body():
    """The control body must enumerate each statement's description, so
    compliance staff see narrative alongside structured frontmatter."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    ac1 = next(n for n in notes if n.relpath == "Controls/AC-01.md")
    # New layered sections (R3 migration)
    assert "# Upstream" in ac1.body
    assert "# Parameters" not in ac1.body
    assert "# Statement Parts" in ac1.body
    # CRM Guidance is emitted only when a secondary-CRM entry exists (ADR-025);
    # this synthetic SSP has none, so the section must be absent.
    assert "# CRM Guidance" not in ac1.body
    assert "## Part a" in ac1.body
    assert "## Part b" in ac1.body
    # Evidence / Related Controls appear because AC-01 references related
    # controls in its narrative; when empty they are omitted (ADR-025).
    assert "# Evidence" in ac1.body
    assert "## Related Controls" in ac1.body
    # The synthetic SSP body must reference the OCISO org via wikilink.
    assert "[[Organizations/example-security-office]]" in ac1.body
    # And cross-reference neo-adjacent controls for the graph view.
    assert "[[Controls/AC-02" in ac1.body


def test_ontology_graph_optimization_adr025():
    """ADR-025: the control ontology must be de-duplicated and the enhancement
    hierarchy correct.

    - No control emits the dead `statements:` field (all-null labels).
    - Base controls: parent = Plan via `up_plan`; children = enhancements via a
      populated `sub_controls`; no `up_control` pointing at the Plan.
    - Enhancement controls: parent = base control via `up_control`.
    - Exactly one control->plan provenance edge (`source_ssp`), not three.
    - Plan carries no `child_controls` mirror; component carries no
      `implements_controls` mirror.
    """
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")

    controls = [n for n in notes if n.relpath.startswith("Controls/")]
    assert controls, "expected control notes"

    for n in controls:
        fm = n.frontmatter
        # C1: dead field removed everywhere.
        assert "statements" not in fm, f"{n.relpath} still emits statements:"
        # C3: single plan-provenance edge.
        assert "source_profile" not in fm, f"{n.relpath} still emits source_profile"

    # C2: hierarchy. Find a base control that has at least one enhancement.
    by_id = {n.frontmatter.get("control_id"): n.frontmatter for n in controls}
    enhancement_ids = [
        cid for cid, fm in by_id.items() if fm.get("enhancement") is not None
    ]
    for enh_id in enhancement_ids:
        fm = by_id[enh_id]
        # Enhancement parent is a control, via up_control (not the Plan).
        up = str(fm.get("up_control") or "")
        assert "Controls/" in up, f"{enh_id} up_control should point to a control"
        assert "Plans/" not in up

    base_with_children = [
        cid
        for cid, fm in by_id.items()
        if fm.get("enhancement") is None and fm.get("sub_controls")
    ]
    for base_id in base_with_children:
        fm = by_id[base_id]
        # Base parent is the Plan, via up_plan.
        assert "Plans/" in str(fm.get("up_plan") or "")
        # Base children are enhancement controls.
        for child in fm["sub_controls"]:
            assert "Controls/" in str(child)

    # C4: no mirror lists on plan / component.
    plan = next(n for n in notes if n.relpath.startswith("Plans/"))
    assert "child_controls" not in plan.frontmatter
    comp = next(n for n in notes if n.relpath.startswith("Components/"))
    assert "implements_controls" not in comp.frontmatter


def _control_graph_violations(vault: "Path") -> list[str]:
    """ADR-025 graph contract for one generated vault: real enhancement parent/child links, no base
    ``up_control``, and no redundant Plan/Component mirror lists. Returns the violations."""
    problems: list[str] = []
    records: dict[str, dict] = {}
    for path in (vault / "Controls").glob("*.md"):
        match = re.match(
            r"---\n(.*?)\n---", path.read_text(encoding="utf-8"), re.DOTALL
        )
        fm = (yaml.safe_load(match.group(1)) or {}) if match else {}
        if fm.get("control_id"):
            records[fm["control_id"]] = fm
    for control_id, fm in records.items():
        base_match = re.match(r"([A-Z]+-\d+)", control_id)
        if not base_match:
            problems.append(f"{control_id}: invalid control id")
            continue
        base_id = base_match.group(1)
        if fm.get("enhancement") is not None and base_id in records:
            if fm.get("up_control") != f"[[Controls/{base_id}]]":
                problems.append(f"{control_id}: up_control should link {base_id}")
        elif fm.get("enhancement") is None:
            if "up_control" in fm:
                problems.append(f"{control_id}: a base control has up_control")
            expected = sorted(
                f"[[Controls/{cid}]]"
                for cid, cfm in records.items()
                if cfm.get("enhancement") is not None
                and re.match(r"([A-Z]+-\d+)", cid).group(1) == control_id
            )
            if sorted(fm.get("sub_controls") or []) != expected:
                problems.append(
                    f"{control_id}: sub_controls {fm.get('sub_controls')} != {expected}"
                )
    for folder, banned in (
        ("Plans", "child_controls"),
        ("Components", "implements_controls"),
    ):
        for path in (vault / folder).glob("*.md"):
            match = re.match(
                r"---\n(.*?)\n---", path.read_text(encoding="utf-8"), re.DOTALL
            )
            if match and banned in (yaml.safe_load(match.group(1)) or {}):
                problems.append(f"{folder}/{path.name} carries {banned}")
    return problems


def _write_notes(vault: "Path", notes) -> None:
    from compliance.scripts.export_obsidian_vault import write_vault

    write_vault(notes, vault)


def test_generated_vault_obeys_the_adr025_graph_contract(tmp_path):
    """Generate a vault from the synthetic SSP and check the ADR-025 graph contract on the output
    (this used to run over every committed example vault; the core holds none)."""
    ssp = json.loads(EXAMPLE_SSP_PATH.read_text(encoding="utf-8"))
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    vault = tmp_path / "sample"
    _write_notes(vault, notes)
    assert (vault / "Controls").is_dir() and any((vault / "Controls").glob("*.md"))
    assert _control_graph_violations(vault) == []


def test_graph_contract_check_detects_a_broken_enhancement_link(tmp_path):
    controls = tmp_path / "Controls"
    controls.mkdir()
    (tmp_path / "Plans").mkdir()
    (tmp_path / "Components").mkdir()
    (controls / "AC-02.md").write_text(
        "---\ncontrol_id: AC-02\nsub_controls: []\n---\n", encoding="utf-8"
    )
    (controls / "AC-02(01).md").write_text(
        "---\ncontrol_id: AC-02(01)\nenhancement: '1'\nup_control: '[[Controls/WRONG]]'\n---\n",
        encoding="utf-8",
    )
    problems = _control_graph_violations(tmp_path)
    assert any("up_control should link AC-02" in p for p in problems)
    assert any("sub_controls" in p for p in problems)


def test_upstream_section_carries_catalog_statement_language():
    """The `# Upstream` section must be populated with the authoritative NIST
    SP 800-53 rev5 catalog statement prose for the control, not a placeholder."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    ac1 = next(n for n in notes if n.relpath == "Controls/AC-01.md")
    upstream = ac1.body[
        ac1.body.index("# Upstream") : ac1.body.index("# Statement Parts")
    ]
    # Real AC-1 catalog language, not the old "Original upstream ..." placeholder.
    assert "access control policy" in upstream
    assert "Original upstream control language" not in upstream
    # `{{ insert: param, ... }}` catalog placeholders must be substituted away.
    assert "{{ insert:" not in upstream
    # Unresolved placeholders fall back to the catalog's descriptive label, not
    # the bare parameter id (e.g. "ac-1_prm_1" / "ac-01_odp.01").
    assert "[Assignment: ac-1_prm_1]" not in upstream
    assert not re.search(
        r"\[Assignment: [a-z]{2}-\d+[a-z0-9._]*(?:_odp|_prm)", upstream
    )


def test_upstream_section_substitutes_ssp_odp_values():
    """`_fill_catalog_prose` must substitute an SSP-supplied ODP value into the
    catalog placeholder, and fall back to the descriptive label otherwise."""
    from compliance.lib.vault import _fill_catalog_prose

    line = (
        "Enforce a limit of {{ insert: param, ac-07_odp.01 }} consecutive "
        "invalid logon attempts by a user during a "
        "{{ insert: param, ac-07_odp.02 }} ; and"
    )
    # SSP supplies odp.01 (with catalog zero-padding) but not odp.02.
    filled = _fill_catalog_prose(
        line, {"ac-07_odp.01": "not more than ten (10) failed access attempts"}
    )
    assert "not more than ten (10) failed access attempts" in filled
    assert "{{ insert:" not in filled
    # Unresolved placeholder renders the catalog label, never the bare id.
    assert "ac-07_odp.02" not in filled
    assert "[Assignment: time period" in filled
    assert " ;" not in filled


def test_fill_catalog_prose_normalizes_placeholder_spacing_without_rewriting_value():
    from compliance.lib.vault import _fill_catalog_prose

    value = "not more than ten (10) failed access attempts"
    filled = _fill_catalog_prose(
        "Enforce  {{ insert: param, ac-07_odp.01 }}  ; and", {"ac-07_odp.01": value}
    )

    assert filled == f"Enforce {value}; and"


def test_fill_catalog_prose_matches_padding_variants():
    """An SSP param id may drop the catalog's zero-padding; both must resolve."""
    from compliance.lib.vault import _fill_catalog_prose

    line = "Automatically {{ insert: param, ac-07_odp.03 }} when exceeded."
    # SSP stores the unpadded spelling `ac-7_odp.03`.
    filled = _fill_catalog_prose(line, {"ac-7_odp.03": "30 minutes"})
    assert "30 minutes" in filled
    assert "{{ insert:" not in filled


def test_index_note_includes_dataview_dashboards():
    """Index.md must contain Dataview blocks for the schema's dashboards."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    index = next(n for n in notes if n.relpath == "Index.md")
    for snippet in (
        "```dataview",
        'FROM "Controls"',
        'FROM "Components"',
        'WHERE implementation_status != "implemented"',
        'WHERE contains(origination, "inherited")',
    ):
        assert snippet in index.body, (
            f"Index.md missing Dataview block snippet {snippet!r}"
        )
    # Verify OKF type taxonomy on index
    assert index.frontmatter["type"] == "okf.index"


def test_synthetic_component_is_marked_placeholder():
    """The synthetic component note must be marked `component/synthetic` so
    nobody mistakes it for real per-component input from fismatic."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    comp = next(n for n in notes if n.relpath == "Components/sample-system.md")
    assert "component/synthetic" in comp.frontmatter.get("tags", [])
    assert "Synthetic placeholder" in comp.body
    # Verify type taxonomy
    assert comp.frontmatter["type"] == "oscal.component_definition"


def test_organization_note_links_to_systems():
    """Organization notes must record which systems they own (System note link)."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    org = next(n for n in notes if n.relpath.startswith("Organizations/"))
    systems = org.frontmatter.get("systems") or []
    assert any("[[Systems/sample-system]]" in str(s) for s in systems), (
        f"Organization note does not link to expected system: {systems}"
    )
    # Verify type taxonomy
    assert org.frontmatter["type"] == "oscal.party"


def test_fallback_organization_when_ssp_has_no_parties():
    """When metadata.parties is absent, a single synthetic CSP note is
    emitted so System/Control notes always have an organization wikilink."""
    notes = build_vault(
        # SSP with no metadata block -> no parties.
        {
            "system-security-plan": {
                "uuid": "u",
                "system-characteristics": {"system-name": "No Parties"},
                "control-implementation": {"implemented-requirements": []},
            }
        },
        plan_id="no-parties-ssp",
    )
    org_notes = [n for n in notes if n.relpath.startswith("Organizations/")]
    assert len(org_notes) == 1
    assert org_notes[0].frontmatter["type"] == "oscal.party"
    assert org_notes[0].frontmatter["note_type"] == "organization"
    assert "org/synthetic" in org_notes[0].frontmatter["tags"]


def test_organization_notes_preserve_native_party_contacts_and_roles():
    ssp = {
        "system-security-plan": {
            "uuid": "u",
            "metadata": {
                "roles": [{"id": "system-owner", "title": "System Owner"}],
                "responsible-parties": [
                    {
                        "role-id": "system-owner",
                        "party-uuids": ["5c4f7fb1-5aa5-43f5-8900-8ddc4dd352d5"],
                    }
                ],
                "parties": [
                    {
                        "uuid": "5c4f7fb1-5aa5-43f5-8900-8ddc4dd352d5",
                        "type": "organization",
                        "name": "Example Agency",
                        "email-addresses": ["owner@example.gov"],
                        "telephone-numbers": [
                            {"number": "+1-202-555-0100", "type": "work"}
                        ],
                    }
                ],
            },
            "system-characteristics": {"system-name": "Contact Test"},
            "control-implementation": {"implemented-requirements": []},
        }
    }

    notes = build_vault(ssp, plan_id="contact-test")
    org = next(note for note in notes if note.relpath.startswith("Organizations/"))
    assert org.frontmatter["role_ids"] == ["system-owner"]
    assert org.frontmatter["email_addresses"] == ["owner@example.gov"]
    assert org.frontmatter["telephone_numbers"] == [
        {"number": "+1-202-555-0100", "type": "work"}
    ]


def test_build_vault_is_deterministic_within_a_run():
    """Two calls with identical input must return identical relpaths (and the
    same set of frontmatter at least at the key level)."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes1 = build_vault(ssp, plan_id="sample-system-ssp-2024")
    notes2 = build_vault(ssp, plan_id="sample-system-ssp-2024")
    assert [n.relpath for n in notes1] == [n.relpath for n in notes2]


def test_oscal_type_taxonomy_on_all_notes():
    """Every domain note must have the new type taxonomy fields: type, oscal_model
    or extension_model, kind."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")

    for note in notes:
        if note.raw:
            assert note.relpath.endswith(".base")
            continue
        fm = note.frontmatter
        assert "type" in fm, f"{note.relpath}: missing `type`"
        t = fm["type"]

        # Canonical types must have oscal_model
        if t.startswith("oscal.") and not t.startswith("oscal.x."):
            assert "oscal_model" in fm, (
                f"{note.relpath}: canonical type {t} missing `oscal_model`"
            )
            assert fm.get("kind") == "canonical", (
                f"{note.relpath}: expected kind=canonical for {t}"
            )
        # Extension types must have extension_model OR oscal_model
        # (oscal.x.control_part uses oscal_model per the template schema).
        elif t.startswith("oscal.x."):
            has_model = "extension_model" in fm or "oscal_model" in fm
            assert has_model, (
                f"{note.relpath}: extension type {t} missing `extension_model` or `oscal_model`"
            )
            assert fm.get("kind") == "extension", (
                f"{note.relpath}: expected kind=extension for {t}"
            )


def test_okf_schema_note_present():
    """The governance/schema.md OKF schema registry note must be emitted."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    schema = next(n for n in notes if n.relpath == "governance/schema.md")
    assert schema.frontmatter["type"] == "okf.schema"
    assert "oscal.control" in schema.body
    assert "oscal.x.diagram" in schema.body


def test_okf_validation_rules_present():
    """The governance/validation-rules/ notes must be emitted."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    notes = build_vault(ssp, plan_id="sample-system-ssp-2024", parser_name="fismatic")
    rule_paths = [
        n.relpath for n in notes if n.relpath.startswith("governance/validation-rules/")
    ]
    assert len(rule_paths) == 2, f"expected 2 validation rules, got {len(rule_paths)}"
    for rp in rule_paths:
        note = next(n for n in notes if n.relpath == rp)
        assert note.frontmatter["type"] == "okf.validation_rule"


# --- CLI smoke test (compliance/scripts/export_obsidian_vault.py main()) ---


def test_cli_writes_vault_to_disk(tmp_path: Path, monkeypatch):
    """The CLI script must build and write a vault to a fresh directory."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")

    import importlib

    vault_dir = tmp_path / "vault-out"

    monkeypatch.setattr(
        "sys.argv",
        [
            "export_obsidian_vault.py",
            "--oscal-json",
            str(EXAMPLE_SSP_PATH),
            "--plan-id",
            "sample-system-ssp-2024",
            "--vault",
            str(vault_dir),
        ],
    )

    script = importlib.import_module("compliance.scripts.export_obsidian_vault")
    rc = script.main()
    assert rc == 0

    # The CLI must have created the directory and written the expected notes.
    relpaths = {
        p.relative_to(vault_dir).as_posix() for p in vault_dir.rglob("*") if p.is_file()
    }
    # Core notes that must exist
    required = {
        "Index.md",
        "Components/sample-system.md",
        "Controls/AC-01.md",
        "Controls/AC-02.md",
        "Controls/AU-04.md",
        "Mappings/control-origination.md",
        "Mappings/implementation-status.md",
        "Mappings/interop-profile.md",
        "_meta/tags.md",
        "_meta/templates/Control.md",
        "_meta/templates/System.md",
        "Organizations/example-security-office.md",
        "Plans/sample-system-ssp.md",
        "Systems/sample-system.md",
        "governance/schema.md",
        "_exports/oscal-mapping-capabilities.json",
    }
    missing = required - relpaths
    assert not missing, f"CLI is missing expected vault notes: {sorted(missing)}"


def test_cli_wipes_existing_vault_except_dotgit(tmp_path: Path, monkeypatch):
    """Default behaviour (no --keep-existing) must remove a stale note from a
    previous run. The .git/ directory is preserved so users can point --vault
    at a git-tracked examples/vault directory without losing history."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")

    import importlib

    vault_dir = tmp_path / "vault-out"
    vault_dir.mkdir()
    # Stale note from a hypothetical previous run; should disappear.
    (vault_dir / "Controls").mkdir(parents=True, exist_ok=True)
    (vault_dir / "Controls" / "DELETED.md").write_text("stale")
    # Should survive the wipe.
    (vault_dir / ".git").mkdir()
    (vault_dir / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    monkeypatch.setattr(
        "sys.argv",
        [
            "export_obsidian_vault.py",
            "--oscal-json",
            str(EXAMPLE_SSP_PATH),
            "--plan-id",
            "sample-system-ssp-2024",
            "--vault",
            str(vault_dir),
        ],
    )

    script = importlib.import_module("compliance.scripts.export_obsidian_vault")
    rc = script.main()
    assert rc == 0

    assert not (vault_dir / "Controls" / "DELETED.md").exists(), (
        "stale control note survived --vault wipe"
    )
    assert (vault_dir / ".git" / "HEAD").exists(), ".git/ was destroyed by the wipe"
    # The fresh notes should still be present.
    assert (vault_dir / "Index.md").exists()
    assert (vault_dir / "Controls" / "AC-01.md").exists()


# --- Chapters 1-12 narrative wiring (heading_parser -> vault) ---------------


def _fake_narrative() -> dict:
    """A minimal heading_parser-shaped extraction result covering the system
    prose (oscal_ssp) plus two populated extension objects."""
    return {
        "objects": [
            {
                "object_name": "oscal_ssp",
                "properties": {
                    "description": "The Example System serves public open data.",
                    "authorization_boundary_description": "Boundary covers all app tiers.",
                    "system_environment_description": "Runs entirely within cloud.gov.",
                    "information_system_type": "Major Information System.",
                },
            },
            {
                "object_name": "oscal_x_boundary",
                "properties": {
                    "description": "Network architecture narrative.",
                    "website_urls": {
                        "headers": ["Component", "URL"],
                        "rows": [["Web", "https://example.gov"]],
                    },
                },
            },
            {
                "object_name": "oscal_x_relationship",
                "properties": {
                    "interconnections": {
                        "headers": ["System", "Type"],
                        "rows": [["N/A", ""]],
                    },
                },
            },
            # An object with no extracted properties must be skipped.
            {"object_name": "oscal_x_runbook", "properties": {}},
        ],
        "summary": {},
    }


def test_build_narrative_notes_emits_populated_extension_notes():
    from compliance.lib.vault import VaultIndex

    index = VaultIndex()
    index.systems["Example System"] = "[[Systems/example-system]]"
    notes = build_narrative_notes(_fake_narrative(), index=index, system_id="sys-123")
    relpaths = {n.relpath for n in notes}
    # boundary + relationship populated; runbook (empty) skipped.
    assert "Boundaries/example-system-boundary.md" in relpaths
    assert "Relationships/example-system-relationship.md" in relpaths
    assert not any("runbook" in p for p in relpaths), (
        "empty runbook object should not emit a note"
    )

    boundary = next(n for n in notes if "boundary" in n.relpath)
    assert boundary.frontmatter["type"] == "oscal.x.boundary"
    assert boundary.frontmatter["kind"] == "extension"
    assert boundary.frontmatter["system_id"] == "sys-123"
    assert "narrative/chapters-1-12" in boundary.frontmatter["tags"]
    # The extracted table must be rendered as a markdown table in the body.
    assert "| Component | URL |" in boundary.body
    assert "https://example.gov" in boundary.body


def test_build_narrative_notes_no_narrative_is_noop():
    from compliance.lib.vault import VaultIndex

    index = VaultIndex()
    index.systems["Example System"] = "[[Systems/example-system]]"
    assert build_narrative_notes(None, index=index) == []
    assert build_narrative_notes({"objects": []}, index=index) == []


def test_enrich_system_note_overlays_real_prose():
    from compliance.lib.vault import VaultNote

    note = VaultNote(
        relpath="Systems/example-system.md",
        frontmatter={
            "type": "oscal.ssp",
            "note_type": "system",
            "authorization_boundary": "placeholder boundary",
        },
        body=(
            "# [[Systems/example-system|Example System]]\n\n"
            "## Description\n\nSystem Description\n\n"
            "## Authorization Boundary\n\nThe authorization boundary encompasses all "
            "components and services within the system.\n\n"
            "## Components\n\n- [[Components/example-system]]\n"
        ),
    )
    enriched = enrich_system_note_from_narrative(note, _fake_narrative())
    # Real description replaces the fismatic placeholder.
    assert "The Example System serves public open data." in enriched.body
    assert "System Description" not in enriched.body.split("## Components")[0]
    # Real boundary flows into frontmatter + body.
    assert (
        enriched.frontmatter["authorization_boundary"]
        == "Boundary covers all app tiers."
    )
    assert "## System Environment" in enriched.body
    assert "Runs entirely within cloud.gov." in enriched.body
    # Components section is preserved.
    assert "## Components" in enriched.body
    assert "[[Components/example-system]]" in enriched.body


def test_enrich_system_note_none_narrative_is_noop():
    from compliance.lib.vault import VaultNote

    note = VaultNote(
        relpath="Systems/x.md",
        frontmatter={"type": "oscal.ssp"},
        body="# X\n\n## Description\n\nSystem Description\n",
    )
    before = note.body
    out = enrich_system_note_from_narrative(note, None)
    assert out.body == before


def test_build_vault_with_narrative_adds_notes_but_keeps_baseline():
    """Passing `narrative` must ADD extension notes without dropping the
    baseline fismatic-only note set (backward compatibility)."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)

    baseline = build_vault(ssp, plan_id="sample-system-ssp-2024")
    with_narr = build_vault(
        ssp, plan_id="sample-system-ssp-2024", narrative=_fake_narrative()
    )
    baseline_paths = {n.relpath for n in baseline}
    narr_paths = {n.relpath for n in with_narr}
    # Every baseline note is still present.
    assert baseline_paths <= narr_paths
    # New narrative notes were added.
    added = narr_paths - baseline_paths
    assert any("Boundaries/" in p for p in added)
    assert any("Relationships/" in p for p in added)


def _rich_narrative() -> dict:
    """Narrative covering the previously-dropped contract objects (D-3..D-6)."""
    return {
        "objects": [
            {
                "object_name": "oscal_ssp",
                "properties": {
                    "description": "Sys desc.",
                    "cloud_service_model": "SaaS",
                    "cloud_deployment_model": "Government Community Cloud",
                },
            },
            {
                "object_name": "oscal_x_privacy",
                "properties": {
                    "pii_processing": "The system processes account email addresses.",
                    "transparency": "A privacy notice is published.",
                },
            },
            {
                "object_name": "oscal_x_evidence",
                "properties": {
                    "attachments": {
                        "headers": ["Attachment", "Description"],
                        "rows": [["Policy.pdf", "Access control policy"]],
                    },
                },
            },
            {
                "object_name": "oscal_component_definition",
                "properties": {
                    "software_inventory": {
                        "headers": ["Software", "Version"],
                        "rows": [["nginx", "1.25"]],
                    },
                },
            },
            {
                "object_name": "okf_mapping",
                "properties": {
                    "references": {
                        "headers": ["ID", "Title"],
                        "rows": [["NIST SP 800-53", "Security Controls"]],
                    },
                },
            },
            {
                "object_name": "okf_schema_glossary",
                "properties": {
                    "terms": {
                        "headers": ["Term", "Definition"],
                        "rows": [["ATO", "Authority to Operate"]],
                    },
                },
            },
        ],
        "summary": {},
    }


def test_privacy_and_evidence_narrative_notes_emitted():
    """oscal_x_privacy + oscal_x_evidence become vault notes (D-3 / #40)."""
    from compliance.lib.vault import VaultIndex

    index = VaultIndex()
    index.systems["Example System"] = "[[Systems/example-system]]"
    notes = build_narrative_notes(_rich_narrative(), index=index, system_id="sys-1")
    relpaths = {n.relpath for n in notes}
    assert any(p.startswith("Privacy/") for p in relpaths)
    assert any(p.startswith("Evidence/") for p in relpaths)
    privacy = next(n for n in notes if n.relpath.startswith("Privacy/"))
    assert privacy.frontmatter["type"] == "oscal.x.privacy"
    assert "account email addresses" in privacy.body
    evidence = next(n for n in notes if n.relpath.startswith("Evidence/"))
    assert evidence.frontmatter["type"] == "oscal.x.evidence"
    assert evidence.frontmatter["evidence_id"]
    assert evidence.frontmatter["evidence_kind"] == "attachment"
    assert "Policy.pdf" in evidence.body


def test_enrich_system_note_surfaces_cloud_models():
    """cloud_service_model / cloud_deployment_model reach the system note
    (D-4 / #41)."""
    from compliance.lib.vault import VaultNote

    note = VaultNote(
        relpath="Systems/x.md",
        frontmatter={"type": "oscal.ssp", "note_type": "system"},
        body="# X\n\n## Description\n\nplaceholder\n\n## Components\n\n- [[Components/x]]\n",
    )
    out = enrich_system_note_from_narrative(note, _rich_narrative())
    assert out.frontmatter["cloud_service_model"] == "SaaS"
    assert out.frontmatter["cloud_deployment_model"] == "Government Community Cloud"
    assert "## Cloud Service Model" in out.body
    assert "## Cloud Deployment Model" in out.body


def test_component_note_surfaces_extracted_inventory():
    """Extracted software/asset inventory reaches the component note (D-5 / #41)."""
    from compliance.lib.vault import (
        VaultIndex,
        build_synthetic_component_note,
        enrich_component_note_from_narrative,
    )

    index = VaultIndex()
    index.systems["Example System"] = "[[Systems/example-system]]"
    ssp_root = {"system-characteristics": {"system-name": "Example System"}}
    note = build_synthetic_component_note(ssp_root, index=index)
    out = enrich_component_note_from_narrative(note, _rich_narrative())
    assert "## Software Inventory" in out.body
    assert "nginx" in out.body
    # synthetic tag dropped once real inventory is present
    assert "component/synthetic" not in (out.frontmatter.get("tags") or [])


def test_mapping_and_glossary_surface_extracted_content():
    """okf_mapping references + okf_schema_glossary terms are surfaced
    (D-6 / #41)."""
    from compliance.lib.vault import build_mapping_notes, build_okf_schema_note

    mapping_notes = build_mapping_notes(_rich_narrative())
    ref_note = next(
        (n for n in mapping_notes if n.relpath.endswith("references.md")), None
    )
    assert ref_note is not None
    assert "NIST SP 800-53" in ref_note.body

    schema_note = build_okf_schema_note("plan-x", _rich_narrative())
    assert "Glossary" in schema_note.body
    assert "Authority to Operate" in schema_note.body


# --- Diagram image extraction & attachment writing -------------------------


class _FakeImage:
    """Stand-in for compliance.lib.docx_images.ExtractedImage."""

    def __init__(self, rid, media_stem, ext, data):
        self.rid = rid
        self.media_stem = media_stem
        self.ext = ext
        self.data = data
        self.part_name = f"/word/media/{media_stem}{ext}"
        self.content_type = "image/png"


# 1x1 RGB PNG (structurally valid).
def _valid_png_1x1() -> bytes:
    import struct
    import zlib

    def _chunk(typ, data):
        body = typ + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


_PNG_BYTES = _valid_png_1x1()


def test_build_media_notes_writes_binary_attachments():
    from compliance.lib.vault import VaultIndex

    index = VaultIndex()
    index.systems["Example System"] = "[[Systems/example-system]]"
    images = {
        "rId7": _FakeImage("rId7", "image2", ".png", _PNG_BYTES),
        "rId89": _FakeImage("rId89", "image3", ".png", _PNG_BYTES),
    }
    notes = build_media_notes(images, index=index)
    relpaths = {n.relpath for n in notes}
    assert "Diagrams/media/example-system-image2.png" in relpaths
    assert "Diagrams/media/example-system-image3.png" in relpaths
    for n in notes:
        assert n.is_binary
        assert n.data == _PNG_BYTES
    # The index records rId -> attachment filename for embedding.
    assert index.media["rId7"] == "example-system-image2.png"


def test_build_media_notes_no_images_is_noop():
    from compliance.lib.vault import VaultIndex

    index = VaultIndex()
    index.systems["Example System"] = "[[Systems/example-system]]"
    assert build_media_notes(None, index=index) == []
    assert build_media_notes({}, index=index) == []


def test_build_media_notes_skips_empty_bytes():
    from compliance.lib.vault import VaultIndex

    index = VaultIndex()
    index.systems["Example System"] = "[[Systems/example-system]]"
    images = {"rId1": _FakeImage("rId1", "image1", ".png", b"")}
    assert build_media_notes(images, index=index) == []


def test_narrative_diagram_note_embeds_media_files():
    from compliance.lib.vault import VaultIndex

    index = VaultIndex()
    index.systems["Example System"] = "[[Systems/example-system]]"
    embeds = ["example-system-image3.png", "example-system-image2.png"]
    notes = build_narrative_notes({"objects": []}, index=index, media_embeds=embeds)
    # Even with no oscal_x_diagram metadata object, the presence of media
    # embeds must still yield a Diagram note that embeds the images.
    diagram = next((n for n in notes if "Diagrams/" in n.relpath), None)
    assert diagram is not None, "diagram note should be emitted when media exists"
    assert "![[example-system-image3.png]]" in diagram.body
    assert "![[example-system-image2.png]]" in diagram.body
    assert diagram.frontmatter["media_files"] == embeds
    assert diagram.frontmatter["diagram_kind"] == "dataflow"


def test_build_vault_with_images_writes_attachments_and_embeds():
    """End-to-end: images passed to build_vault yield binary attachment notes
    plus a Diagram note that embeds them."""
    if not EXAMPLE_SSP_PATH.exists():
        pytest.skip("example SSP source missing from checkout")
    with open(EXAMPLE_SSP_PATH) as f:
        ssp = json.load(f)
    images = {"rId7": _FakeImage("rId7", "image2", ".png", _PNG_BYTES)}
    notes = build_vault(
        ssp,
        plan_id="sample-system-ssp-2024",
        narrative=_fake_narrative(),
        images=images,
    )
    binaries = [n for n in notes if n.is_binary]
    assert binaries, "expected at least one binary attachment note"
    assert any(n.relpath.startswith("Diagrams/media/") for n in binaries)
    diagram = next(
        (n for n in notes if n.relpath.startswith("Diagrams/") and not n.is_binary),
        None,
    )
    assert diagram is not None
    assert "![[" in diagram.body


def test_write_vault_writes_binary_and_text(tmp_path):
    from compliance.lib.vault import VaultNote

    import importlib

    script = importlib.import_module("compliance.scripts.export_obsidian_vault")
    notes = [
        VaultNote(relpath="Diagrams/media/x.png", data=_PNG_BYTES),
        VaultNote(
            relpath="Systems/s.md",
            frontmatter={"type": "oscal.ssp", "note_type": "system"},
            body="# S\n",
        ),
    ]
    written = script.write_vault(notes, tmp_path)
    assert written == 2
    assert (tmp_path / "Diagrams/media/x.png").read_bytes() == _PNG_BYTES
    assert (tmp_path / "Systems/s.md").read_text().startswith("---")


# ---------------------------------------------------------------------------
# Phase C2 tests: real component note generation from registry data
# ---------------------------------------------------------------------------

_REGISTRY_COMPONENTS = [
    {
        "name": "cloud.gov",
        "component_slug": "cloud-gov",
        "type": "platform",
        "component_kind": "platform",
        "implementation_model": "paas",
        "fedramp_package": "fedramp-moderate",
        "responsible_party": "cloud.gov",
        "note": "Primary PaaS hosting layer.",
    },
    {
        "name": "GitHub Actions",
        "component_slug": "github-actions",
        "type": "tool",
        "component_kind": "software",
        "implementation_model": "saas",
        "fedramp_package": "fedramp-moderate",
        "responsible_party": "GitHub",
        "note": "Source control and CI/CD.",
    },
]

_REGISTRY_LEVERAGED_AUTHS = [
    {
        "id": "cloud-gov-fedramp-moderate",
        "name": "cloud.gov FedRAMP Moderate P-ATO",
        "href": "https://marketplace.fedramp.gov/products/F1607067912",
        "provider_system": "cloud.gov",
        "mode": "partial",
    }
]

_MINIMAL_SSP_FOR_C2 = {
    "system-security-plan": {
        "uuid": "test-uuid",
        "metadata": {
            "parties": [
                {
                    "uuid": "org-uuid",
                    "type": "organization",
                    "name": "Example Security Office",
                    "role-ids": ["system-owner"],
                }
            ]
        },
        "system-characteristics": {
            "system-name": "Test System",
            "security-sensitivity-level": "low",
        },
        "control-implementation": {"implemented-requirements": []},
    }
}


def test_build_vault_real_component_notes_emitted():
    """When registry_components is supplied, build_vault must emit one
    oscal.component_definition note per component (not a synthetic note)."""
    notes = build_vault(
        _MINIMAL_SSP_FOR_C2,
        plan_id="test-system-ssp",
        registry_components=_REGISTRY_COMPONENTS,
        registry_leveraged_auths=_REGISTRY_LEVERAGED_AUTHS,
    )
    comp_notes = [n for n in notes if n.relpath.startswith("Components/")]
    assert len(comp_notes) == len(_REGISTRY_COMPONENTS), (
        f"Expected {len(_REGISTRY_COMPONENTS)} component notes, got {len(comp_notes)}: "
        f"{[n.relpath for n in comp_notes]}"
    )
    slugs = {n.relpath for n in comp_notes}
    assert "Components/cloud-gov.md" in slugs
    assert "Components/github-actions.md" in slugs


def test_build_vault_real_components_not_tagged_synthetic():
    """Real component notes must NOT carry the component/synthetic tag."""
    notes = build_vault(
        _MINIMAL_SSP_FOR_C2,
        plan_id="test-system-ssp",
        registry_components=_REGISTRY_COMPONENTS,
    )
    comp_notes = [n for n in notes if n.relpath.startswith("Components/")]
    assert comp_notes, "no component notes generated"
    for note in comp_notes:
        assert "component/synthetic" not in note.frontmatter.get("tags", []), (
            f"{note.relpath} is incorrectly tagged component/synthetic"
        )
        assert note.frontmatter["type"] == "oscal.component_definition"
        assert note.frontmatter.get("component_kind") is not None


def test_build_vault_system_note_links_all_real_components():
    """The System note's components: list must wikilink to every real component."""
    notes = build_vault(
        _MINIMAL_SSP_FOR_C2,
        plan_id="test-system-ssp",
        registry_components=_REGISTRY_COMPONENTS,
    )
    system_note = next(n for n in notes if n.relpath.startswith("Systems/"))
    comp_links = system_note.frontmatter.get("components", [])
    assert len(comp_links) == len(_REGISTRY_COMPONENTS), (
        f"System note has {len(comp_links)} component links, "
        f"expected {len(_REGISTRY_COMPONENTS)}: {comp_links}"
    )
    link_strs = [str(lnk) for lnk in comp_links]
    assert any("cloud-gov" in lnk for lnk in link_strs), (
        f"cloud-gov wikilink missing from System note components: {link_strs}"
    )
    assert any("github-actions" in lnk for lnk in link_strs), (
        f"github-actions wikilink missing from System note components: {link_strs}"
    )
    # Body also lists all components.
    for slug in ("cloud-gov", "github-actions"):
        assert slug in system_note.body, (
            f"System note body missing component link for {slug}"
        )


def test_build_vault_leveraged_auth_in_component_body():
    """Component notes must include the leveraged authorization details in body."""
    notes = build_vault(
        _MINIMAL_SSP_FOR_C2,
        plan_id="test-system-ssp",
        registry_components=_REGISTRY_COMPONENTS,
        registry_leveraged_auths=_REGISTRY_LEVERAGED_AUTHS,
    )
    cloud_gov_note = next(n for n in notes if n.relpath == "Components/cloud-gov.md")
    assert "Leveraged Authorizations" in cloud_gov_note.body
    assert "cloud.gov FedRAMP Moderate P-ATO" in cloud_gov_note.body
    assert "marketplace.fedramp.gov" in cloud_gov_note.body


def test_index_note_has_inheritance_burndown_query():
    """Index.md must include the inheritance burndown Dataview panel."""
    notes = build_vault(
        _MINIMAL_SSP_FOR_C2,
        plan_id="test-system-ssp",
    )
    index = next(n for n in notes if n.relpath == "Index.md")
    assert "Inheritance burndown" in index.body, (
        "Index.md missing '### Inheritance burndown' section"
    )
    assert 'contains(origination, "inherited")' in index.body


def test_control_part_template_present_and_valid():
    """build_template_notes must emit a ControlPart.md with oscal.x.control_part type."""
    from compliance.lib.vault import build_template_notes

    templates = build_template_notes()
    cp = next(
        (t for t in templates if t.relpath == "_meta/templates/ControlPart.md"), None
    )
    assert cp is not None, "ControlPart.md template missing from build_template_notes()"
    assert cp.frontmatter["type"] == "oscal.x.control_part"
    assert cp.frontmatter["kind"] == "extension"
    assert "inheritance_mode" in cp.frontmatter
    assert "part_letter" in cp.frontmatter
    assert "component" in cp.frontmatter


def test_control_template_defers_parameter_view_until_values_exist():
    """The generic authoring template must not create an empty parameter view."""
    from compliance.lib.vault import build_template_notes

    control = next(
        note
        for note in build_template_notes()
        if note.relpath == "_meta/templates/Control.md"
    )

    assert "# Parameters" not in control.body
    assert "FLATTEN parameters AS parameter" not in control.body
