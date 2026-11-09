"""Tests for the declarative Obsidian audit harness (synthetic evidence only)."""

from pathlib import Path

import pytest

pytest.importorskip("duckdb")
obsidian_harness = pytest.importorskip("compliance.scripts.obsidian_harness")

NOTE = """# Synthetic Audit

```duckdb
-- @evidence: evidence/auth-events.csv
-- @assert: failed_logins == 2
-- @control: AC-7
SELECT count(*) AS failed_logins
FROM read_csv_auto('evidence/auth-events.csv')
WHERE event_type = 'auth_failure';
```

```inspec
# @evidence: evidence/auth-events.csv
# @assert: file_contains 'auth_failure' == true
# @control: AU-6
describe file('evidence/auth-events.csv') do
end
```
"""


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "auth-events.csv").write_text(
        "event_type,principal\n"
        "auth_success,synthetic-user\n"
        "auth_failure,synthetic-user\n"
        "auth_failure,synthetic-user\n",
        encoding="utf-8",
    )
    note = tmp_path / "audit.md"
    note.write_text(NOTE, encoding="utf-8")
    monkeypatch.setattr(obsidian_harness, "ROOT", tmp_path.resolve())
    return tmp_path, note, evidence


def test_runs_duckdb_and_inspec_blocks(workspace):
    _, note, evidence = workspace

    blocks = obsidian_harness.detect_blocks(note)
    assert len(blocks) == 1
    result = obsidian_harness.run_block(blocks[0], evidence)
    assert result.control == "AC-7"
    assert result.rows == [{"failed_logins": 2}]
    assert result.passed is True

    inspec = obsidian_harness.detect_inspec_blocks(note)
    inspec_result = obsidian_harness.run_inspec_block(inspec[0], evidence)
    assert inspec_result.control == "AU-6"
    assert inspec_result.passed is True

    callouts = obsidian_harness.render_callouts([result], [inspec_result])
    assert "> [!check] PASS" in callouts


def test_rejects_evidence_outside_configured_root(workspace):
    tmp_path, note, evidence = workspace
    (tmp_path / "secret.csv").write_text("a\n1\n", encoding="utf-8")
    block = obsidian_harness.AuditBlock(
        path=note,
        line=1,
        sql="SELECT count(*) AS events FROM read_csv_auto('secret.csv')",
        evidence="secret.csv",
        assertion="events >= 0",
        control=None,
    )

    with pytest.raises(ValueError, match="beneath"):
        obsidian_harness.run_block(block, evidence)


def test_rejects_mutating_sql(workspace):
    _, note, evidence = workspace
    block = obsidian_harness.AuditBlock(
        path=note,
        line=1,
        sql="CREATE TABLE t AS SELECT * FROM read_csv_auto('evidence/auth-events.csv')",
        evidence="evidence/auth-events.csv",
        assertion="events == 0",
        control=None,
    )

    with pytest.raises(ValueError, match="read-only"):
        obsidian_harness.run_block(block, evidence)


def test_rejects_query_that_reads_an_undeclared_file(workspace):
    _, note, evidence = workspace
    block = obsidian_harness.AuditBlock(
        path=note,
        line=1,
        sql=(
            "SELECT count(*) AS events FROM read_csv_auto('evidence/auth-events.csv') "
            "UNION ALL SELECT count(*) FROM read_csv_auto('audit.md')"
        ),
        evidence="evidence/auth-events.csv",
        assertion="events >= 0",
        control=None,
    )

    with pytest.raises(ValueError, match="only read the declared"):
        obsidian_harness.run_block(block, evidence)


def test_rejects_callout_output_outside_workspace(workspace):
    with pytest.raises(ValueError, match="beneath"):
        obsidian_harness._resolve_output(Path("/tmp/obsidian-results.md"))


def test_rejects_robot_suite_outside_suite_root(workspace):
    _, note, evidence = workspace

    with pytest.raises(ValueError, match="beneath"):
        obsidian_harness.run_robot_suite(note, evidence)
