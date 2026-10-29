import textwrap

from compliance.lib.vault_notes import (
    OriginationMap,
    StatusMap,
    parse_control,
    parse_inheritance,
    parse_org_parties,
    split_frontmatter,
)

CONTROL = textwrap.dedent(
    """\
    ---
    note_type: control
    control_id: AC-02(01)
    title: Account Management
    implementation_status: implemented
    origination:
    - hybrid
    - inherited
    parameters:
    - param_id: ac-02_odp.01
      value: ninety days
    ---

    # AC-02(01)

    ## Part a
    inheritance_mode:: shared
    implementation_status:: partial
    The system disables accounts.

    ## Part b
    Reviews accounts monthly.

    # Evidence

    See the quarterly review.
    """
)


def test_frontmatter_split_tolerates_missing_or_broken_yaml():
    assert split_frontmatter("no frontmatter") == ({}, "no frontmatter")
    assert split_frontmatter("---\n: [bad\n---\nbody")[0] == {}
    meta, body = split_frontmatter("---\na: 1\n---\nbody\n")
    assert meta == {"a": 1} and body == "body\n"


def test_control_is_parsed_with_parts_sections_and_mapped_vocabulary(tmp_path):
    path = tmp_path / "AC-02(01).md"
    path.write_text(CONTROL, encoding="utf-8")
    status = StatusMap({"implemented": {"target": "operational"}}, {"target": "gap"})
    orig = OriginationMap(
        {"hybrid": {"target": "shared"}, "inherited": {"target": "platform-inherited"}},
        ["inherited", "hybrid"],
        {"target": "system-specific"},
    )

    record = parse_control(str(path), status_map=status, origination_map=orig)

    assert record.control_id == "AC-2(1)"
    assert record.normalized_status == "operational"
    assert record.normalized_origination == "platform-inherited"
    assert [p.part for p in record.parts] == ["a", "b"]
    assert record.parts[0].inheritance_mode == "shared"
    assert record.parts[0].implementation_status == "partial"
    assert record.parts[0].prose == "The system disables accounts."
    assert record.body_sections["Evidence"] == "See the quarterly review."
    assert "disables accounts" in record.combined_prose


def test_non_control_notes_and_unmapped_values_fall_back_safely(tmp_path):
    other = tmp_path / "x.md"
    other.write_text("---\nnote_type: system\n---\nbody", encoding="utf-8")
    assert parse_control(str(other)) is None
    assert StatusMap({}, {}).lookup("anything") == ("not-implemented", None)
    assert OriginationMap({}, [], {}).lookup([]) == ("system-specific", None, [])


def test_inheritance_rows_are_read_from_provided_by_and_receives(tmp_path):
    note = tmp_path / "inh.md"
    note.write_text(
        textwrap.dedent(
            """\
            ---
            note_type: inheritance
            ---
            # Provided by

            | Provider | Owner | Control | Mode |
            | --- | --- | --- | --- |
            | Platform | Ops | AC-02(1) Account Management | inherited |
            | [placeholder] | x | y | z |
            """
        ),
        encoding="utf-8",
    )
    (entry,) = parse_inheritance(str(note))
    assert (entry.provider, entry.control_id, entry.mode) == (
        "Platform",
        "AC-2(1)",
        "inherited",
    )


def test_org_parties_get_a_contact_type_and_skip_other_notes(tmp_path):
    orgs = tmp_path / "Organizations"
    orgs.mkdir()
    (orgs / "a.md").write_text(
        "---\nnote_type: organization\nname: Example Org\nrole: ISSO\n---\n",
        encoding="utf-8",
    )
    (orgs / "b.md").write_text(
        "---\nnote_type: system\nname: Nope\n---\n", encoding="utf-8"
    )
    (party,) = parse_org_parties(str(tmp_path))
    assert (party.name, party.contact_type) == ("Example Org", "Security")


def test_mappings_fall_back_to_the_cores_tables_and_a_local_copy_overrides_them(
    tmp_path,
):
    from compliance.lib.vault_notes import load_status_and_origination

    status, origination = load_status_and_origination(str(tmp_path))
    assert status.mapping and origination.mapping

    local = tmp_path / "compliance" / "mappings"
    local.mkdir(parents=True)
    (local / "implementation-status-map.yaml").write_text(
        "mapping:\n  weird: {target: custom}\ndefault: {target: nope}\n",
        encoding="utf-8",
    )
    (local / "control-origination-map.yaml").write_text(
        "mapping: {}\nprecedence: []\ndefault: {target: x}\n", encoding="utf-8"
    )
    overridden, _ = load_status_and_origination(str(tmp_path))
    assert overridden.lookup("weird")[0] == "custom"
