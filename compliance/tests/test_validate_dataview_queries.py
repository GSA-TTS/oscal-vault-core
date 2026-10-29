"""Regression tests for vault-wide Dataview query validation."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from compliance.lib.vault import build_bases_views
from compliance.scripts import validate_dataview_queries


_FIXTURES = Path(__file__).parent / "fixtures" / "bases"


def test_validator_scans_inline_control_dataview_queries(tmp_path, monkeypatch, capsys):
    """Inline generated views must be checked, not just dashboard queries."""
    control = tmp_path / "Controls" / "AC-01.md"
    control.parent.mkdir()
    control.write_text(
        "```dataview\n"
        'TABLE parameter.param_id AS "Parameter", parameter.value AS "Value"\n'
        "FROM [[]]\n"
        "FLATTEN parameters AS parameter\n"
        "```\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["validate_dataview_queries.py", "--vault", str(tmp_path)]
    )

    assert validate_dataview_queries.main() == 0
    assert "1 Dataview blocks checked, 0 warnings" in capsys.readouterr().out


def test_validator_scans_bases_files(tmp_path, monkeypatch, capsys):
    """Obsidian Bases view definitions (.base) are validated for folders and properties (#44)."""
    views_dir = tmp_path / "_meta" / "views"
    views_dir.mkdir(parents=True)
    (tmp_path / "Controls").mkdir()
    (tmp_path / "Controls" / "AC-01.md").write_text(
        "---\ncontrol_id: AC-01\n---\n", encoding="utf-8"
    )
    base_file = views_dir / "controls.base"
    base_file.write_text(
        "filters:\n"
        "  and:\n"
        '    - file.inFolder("Controls")\n'
        "properties:\n"
        "  control_id:\n"
        "    displayName: Control\n"
        "views:\n"
        "  - type: table\n"
        "    name: All\n"
        "    order:\n"
        "      - control_id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["validate_dataview_queries.py", "--vault", str(tmp_path)]
    )

    assert validate_dataview_queries.main() == 0
    out = capsys.readouterr().out
    assert "1 Bases view file(s) checked" in out


def test_bases_filters_report_result_counts_and_require_data(
    tmp_path, monkeypatch, capsys
):
    """Base filters are evaluated against frontmatter relative to the vault root."""
    views_dir = tmp_path / "_meta" / "views"
    views_dir.mkdir(parents=True)
    (tmp_path / "Controls").mkdir()
    (tmp_path / "Controls" / "AC-01.md").write_text(
        "---\nimplementation_status: implemented\norigination: [local]\n---\n",
        encoding="utf-8",
    )
    (tmp_path / "Controls" / "AC-02.md").write_text(
        "---\nimplementation_status: planned\norigination: [inherited]\nfamily: AC\n---\n",
        encoding="utf-8",
    )
    (views_dir / "controls.base").write_text(
        "filters:\n"
        "  and:\n"
        '    - file.inFolder("Controls")\n'
        "views:\n"
        "  - type: table\n"
        "    name: All\n"
        "  - type: table\n"
        "    name: Planned or inherited\n"
        "    filters:\n"
        "      and:\n"
        '        - implementation_status != "implemented"\n'
        '        - family == "AC"\n'
        "        - or:\n"
        '            - origination.contains("inherited")\n'
        '            - origination.contains("hybrid")\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["validate_dataview_queries.py", "--vault", str(tmp_path)]
    )

    assert validate_dataview_queries.main() == 0
    out = capsys.readouterr().out
    assert "'All' matched 2 of 2 note(s)" in out
    assert "'Planned or inherited' matched 1 of 2 note(s)" in out


def test_populated_folder_all_view_must_not_be_empty(tmp_path, monkeypatch, capsys):
    """Unexpectedly empty Base views are render failures, not merely valid YAML."""
    views_dir = tmp_path / "_meta" / "views"
    views_dir.mkdir(parents=True)
    (tmp_path / "Controls").mkdir()
    (tmp_path / "Controls" / "AC-01.md").write_text(
        "---\nimplementation_status: implemented\n---\n", encoding="utf-8"
    )
    (views_dir / "controls.base").write_text(
        "filters:\n"
        "  and:\n"
        '    - file.inFolder("Controls")\n'
        "views:\n"
        "  - type: table\n"
        "    name: All controls\n"
        "    filters: 'implementation_status == \"planned\"'\n"
        "    order: [implementation_status]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["validate_dataview_queries.py", "--vault", str(tmp_path)]
    )

    assert validate_dataview_queries.main() == 1
    assert "matched no notes" in capsys.readouterr().out


def test_absent_folder_allows_empty_view_from_validator_context(
    tmp_path, monkeypatch, capsys
):
    views_dir = tmp_path / "_meta" / "views"
    views_dir.mkdir(parents=True)
    (views_dir / "controls.base").write_text(
        (_FIXTURES / "obsidian-1.14.4-controls.base").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["validate_dataview_queries.py", "--vault", str(tmp_path)]
    )

    assert validate_dataview_queries.main() == 0
    assert "'All controls' matched 0" in capsys.readouterr().out


def test_known_fixture_base_has_expected_row_counts(tmp_path, monkeypatch, capsys):
    views_dir = tmp_path / "_meta" / "views"
    controls_dir = tmp_path / "Controls"
    views_dir.mkdir(parents=True)
    controls_dir.mkdir()
    (views_dir / "controls.base").write_text(
        (_FIXTURES / "obsidian-1.14.4-controls.base").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    records = {
        "AC-01.md": "control_id: AC-01\nimplementation_status: implemented\norigination: [local]",
        "AC-02.md": "control_id: AC-02\nimplementation_status: planned\norigination: [inherited]",
        "AC-03.md": "control_id: AC-03\nimplementation_status: implemented\norigination: [hybrid]",
    }
    for name, frontmatter in records.items():
        (controls_dir / name).write_text(f"---\n{frontmatter}\n---\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["validate_dataview_queries.py", "--vault", str(tmp_path)]
    )

    assert validate_dataview_queries.main() == 0
    output = capsys.readouterr().out
    assert "'All controls' matched 3 of 3 note(s)" in output
    assert "'Requiring attention' matched 1 of 3 note(s)" in output
    assert "'Inherited / hybrid' matched 2 of 3 note(s)" in output


def test_all_generated_views_match_known_fixture_row_counts():
    fixture = yaml.safe_load(
        (_FIXTURES / "known-records.yaml").read_text(encoding="utf-8")
    )
    records = fixture["records"]
    actual = {}

    for note in build_bases_views():
        document = yaml.safe_load(note.render())
        base_records = validate_dataview_queries._evaluate_base_filters(
            records, document["filters"]
        )
        for view in document["views"]:
            matches = validate_dataview_queries._evaluate_base_filters(
                base_records, view.get("filters")
            )
            actual[f"{Path(note.relpath).name}:{view['name']}"] = len(matches)

    assert actual == fixture["expected_counts"]


def test_base_filter_grammar_supports_top_level_strings_and_nested_boolean_operators():
    record = {
        "_folder": "Controls",
        "implementation_status": "implemented",
        "origination": ["hybrid"],
    }

    assert validate_dataview_queries._matches_base_filter(
        record, 'implementation_status == "implemented"'
    )
    assert validate_dataview_queries._matches_base_filter(
        record,
        {
            "and": [
                'file.inFolder("Controls")',
                {"not": ['origination.contains("inherited")']},
                {
                    "or": [
                        'origination.contains("hybrid")',
                        'origination.contains("local")',
                    ]
                },
            ]
        },
    )


def test_base_filter_errors_distinguish_syntax_from_unsupported_grammar():
    for malformed in (
        'status = "active"',
        {"and": ['status == "active"'], "or": ['status == "draft"']},
        {"not": "status == 'draft'"},
    ):
        try:
            validate_dataview_queries._validate_base_filter(malformed)
        except validate_dataview_queries.BaseFilterSyntaxError:
            pass
        else:
            raise AssertionError(f"expected syntax error for {malformed!r}")

    try:
        validate_dataview_queries._validate_base_filter({"xor": ['status == "active"']})
    except validate_dataview_queries.BaseFilterUnsupportedError:
        pass
    else:
        raise AssertionError("expected unsupported filter error")


def test_list_contains_uses_element_equality_not_substring_matching():
    record = {"origination": ["system-specific", "inherited"]}

    assert validate_dataview_queries._matches_base_filter(
        record, 'origination.contains("inherited")'
    )
    assert not validate_dataview_queries._matches_base_filter(
        record, 'origination.contains("herit")'
    )


def test_numeric_comparisons_support_generated_typed_table_views():
    record = {"table_record_count": 2}

    assert validate_dataview_queries._matches_base_filter(
        record, "table_record_count > 0"
    )
    assert validate_dataview_queries._matches_base_filter(
        record, "table_record_count >= 2"
    )
    assert not validate_dataview_queries._matches_base_filter(
        record, "table_record_count < 2"
    )
    assert not validate_dataview_queries._matches_base_filter(
        {"table_record_count": "unknown"}, "table_record_count > 0"
    )


def test_typed_literals_and_bracket_properties_are_evaluated_exactly():
    record = {
        "active": True,
        "reviewed": None,
        "review date": "2026-10-13",
        "count": 2,
        "label": 'quoted "value"',
    }

    assert validate_dataview_queries._matches_base_filter(record, "active == true")
    assert validate_dataview_queries._matches_base_filter(record, "reviewed == null")
    assert validate_dataview_queries._matches_base_filter(record, "count == 2")
    assert not validate_dataview_queries._matches_base_filter(record, 'count == "2"')
    assert validate_dataview_queries._matches_base_filter(
        record, '["review date"] == "2026-10-13"'
    )
    assert validate_dataview_queries._matches_base_filter(
        record, 'label == "quoted \\"value\\""'
    )


def test_unsupported_literal_and_property_access_fail_closed():
    for expression in ("active == yes", "items == [1, 2]", "field.sub[0] == 1"):
        try:
            validate_dataview_queries._validate_base_filter(expression)
        except validate_dataview_queries.BaseFilterSyntaxError:
            pass
        else:
            raise AssertionError(f"expected syntax error for {expression!r}")


def test_base_schema_rejects_nonstandard_and_wrongly_typed_keys():
    issues = validate_dataview_queries._validate_base_structure(
        {
            "views": [
                {
                    "type": "table",
                    "name": "All",
                    "allowEmpty": "yes",
                    "order": "control_id",
                    "sort": [{"property": "control_id", "direction": "UP"}],
                }
            ]
        }
    )

    assert any("allowEmpty" in issue for issue in issues)
    assert any("order must be a list" in issue for issue in issues)
    assert any("direction must be ASC or DESC" in issue for issue in issues)


def test_bases_require_a_plan_vault_root(tmp_path, monkeypatch, capsys):
    """A Base outside a plan's _meta/views cannot have root-relative semantics."""
    base_file = tmp_path / "controls.base"
    base_file.write_text("views: []\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["validate_dataview_queries.py", "--vault", str(tmp_path)]
    )

    assert validate_dataview_queries.main() == 1
    assert (
        "open the plan directory as the Obsidian vault root" in capsys.readouterr().out
    )


def test_bases_reject_an_aggregate_parent_as_the_vault_root(
    tmp_path, monkeypatch, capsys
):
    """An aggregate parent cannot silently evaluate plan-relative Base filters."""
    views_dir = tmp_path / "plan-a" / "_meta" / "views"
    views_dir.mkdir(parents=True)
    (views_dir / "controls.base").write_text("views: []\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["validate_dataview_queries.py", "--vault", str(tmp_path)]
    )

    assert validate_dataview_queries.main() == 1
    assert "Base schema" in capsys.readouterr().out


def test_aggregate_parent_invocation_validates_child_vaults(
    tmp_path, monkeypatch, capsys
):
    views_dir = tmp_path / "plan-a" / "_meta" / "views"
    controls_dir = tmp_path / "plan-a" / "Controls"
    views_dir.mkdir(parents=True)
    controls_dir.mkdir()
    (controls_dir / "AC-01.md").write_text(
        "---\ncontrol_id: AC-01\nimplementation_status: implemented\n---\n",
        encoding="utf-8",
    )
    (views_dir / "controls.base").write_text(
        (_FIXTURES / "obsidian-1.14.4-controls.base").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["validate_dataview_queries.py", "--vault", str(tmp_path)]
    )

    assert validate_dataview_queries.main() == 0
    assert "1 Bases view file(s) checked" in capsys.readouterr().out


def test_evaluate_declarative_query_portable():
    """Declarative query evaluator filters and sorts frontmatter records without DataviewJS (#44)."""
    records = [
        {"_folder": "Controls", "control_id": "AC-02", "status": "planned"},
        {"_folder": "Controls", "control_id": "AC-01", "status": "implemented"},
        {"_folder": "Components", "control_id": "N/A", "status": "operational"},
    ]

    res = validate_dataview_queries.evaluate_declarative_query(
        records,
        from_folder="Controls",
        where_fn=lambda r: r.get("status") == "implemented",
        sort_key="control_id",
    )
    assert len(res) == 1
    assert res[0]["control_id"] == "AC-01"
