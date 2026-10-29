"""Synthetic tests for source-located instruction ledger reconciliation."""

from __future__ import annotations

import json
import subprocess
import sys

import docx
import pytest
from docx.shared import RGBColor
from docx.enum.style import WD_STYLE_TYPE

from compliance.lib.instruction_ledger import (
    DISPOSITION_DUPLICATE,
    DISPOSITION_TEMPLATE_GUIDANCE_CALLOUT,
    assert_reconciled,
    build_instruction_ledger,
    canonical_reconciliation_findings,
    inventory_canonical_instructions,
    inventory_rendered_instructions,
    load_instruction_policy,
    reconciliation_findings,
    render_instruction_outputs,
)
from compliance.scripts.instruction_ledger import main
from compliance.scripts.generate_canonical_json import main as generate_canonical_main
from compliance.scripts.export_obsidian_vault import _write_instruction_guidance

BLUE = RGBColor(0x00, 0x70, 0xC0)


def _blue(doc: docx.Document, text: str) -> None:
    paragraph = doc.add_paragraph()
    paragraph.add_run(text).font.color.rgb = BLUE


def _source(tmp_path):
    document = docx.Document()
    document.add_paragraph("Instruction: before any heading")
    document.add_heading("Repeated Section", level=1)
    document.add_paragraph("Instruction: repeated text.")
    document.add_heading("Repeated Section", level=1)
    document.add_paragraph("Instruction: repeated text.")
    document.add_heading("System Description", level=1)
    _blue(document, "Blue-only template prose remains visible.")
    inherited = document.styles.add_style("Inherited Guidance", WD_STYLE_TYPE.PARAGRAPH)
    inherited.font.color.rgb = BLUE
    document.add_paragraph("Inherited blue prompt.", style=inherited)
    document.add_paragraph("Instructions: describe the system.")
    document.add_paragraph("Note the ordinary prose is not guidance.")
    document.add_paragraph("Examples include ordinary prose.")
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    cell.paragraphs[0].text = "ordinary table prose"
    table_prompt = cell.add_paragraph("Instructions: complete this table prompt.")
    table_prompt.runs[0].font.color.rgb = BLUE
    path = tmp_path / "instructions.docx"
    document.save(path)
    return path


def _rendered(tmp_path, ledger):
    output = tmp_path / "rendered"
    render_instruction_outputs(ledger, output)
    return inventory_rendered_instructions(output)


def test_ledger_records_locators_hashes_anchors_and_table_paragraphs(tmp_path):
    ledger = build_instruction_ledger(_source(tmp_path))
    assert len(ledger.entries) == 7
    assert len(ledger.candidate_locators) == 7
    assert all(len(entry.source_hash) == 64 for entry in ledger.entries)
    before_any_heading = next(
        entry for entry in ledger.entries if "before any heading" in entry.text
    )
    assert before_any_heading.anchor is None
    blue = next(entry for entry in ledger.entries if entry.detection == "blue_only")
    assert blue.visibility == "visible"
    assert any(
        entry.text == "Inherited blue prompt." and entry.detection == "blue_only"
        for entry in ledger.entries
    )
    table = next(
        entry for entry in ledger.entries if entry.source_kind == "table_paragraph"
    )
    assert table.anchor == "System Description"
    assert table.locator.endswith("/tr[0]/tc[0]/p[1]")
    assert table.detection == "table_text+color"


def test_same_text_at_distinct_anchors_is_not_auto_suppressed(tmp_path):
    ledger = build_instruction_ledger(_source(tmp_path))
    repeated = [
        entry for entry in ledger.entries if entry.text == "Instruction: repeated text."
    ]
    assert len(repeated) == 2
    assert all(
        entry.disposition == DISPOSITION_TEMPLATE_GUIDANCE_CALLOUT for entry in repeated
    )
    assert repeated[0].destination != repeated[1].destination
    assert_reconciled(ledger, _rendered(tmp_path, ledger))


def test_reconciliation_uses_actual_destination_and_hash(tmp_path):
    ledger = build_instruction_ledger(_source(tmp_path))
    rendered = _rendered(tmp_path, ledger)
    rendered[0].destination = "wrong.md"
    rendered[1].source_hash = "0" * 64
    findings = reconciliation_findings(ledger, rendered)
    assert any("expected" in finding for finding in findings)
    assert any("source hash does not match" in finding for finding in findings)


def test_reconciliation_hashes_actual_rendered_text(tmp_path):
    ledger = build_instruction_ledger(_source(tmp_path))
    output = tmp_path / "rendered"
    render_instruction_outputs(ledger, output)
    first = output / ledger.entries[0].destination
    first.write_text(
        first.read_text(encoding="utf-8").replace("before any heading", "tampered"),
        encoding="utf-8",
    )
    findings = reconciliation_findings(ledger, inventory_rendered_instructions(output))
    assert any(
        "rendered instruction text does not match" in finding for finding in findings
    )


def test_missing_rendered_output_fails_closed(tmp_path):
    ledger = build_instruction_ledger(_source(tmp_path))
    with pytest.raises(ValueError, match="no rendered destination found"):
        assert_reconciled(ledger, [])


def test_independent_candidate_inventory_fails_closed(tmp_path):
    ledger = build_instruction_ledger(_source(tmp_path))
    missing = ledger.entries.pop()
    findings = reconciliation_findings(ledger, _rendered(tmp_path, ledger))
    assert f"{missing.locator}: candidate inventory node was not classified" in findings


def test_explicit_duplicate_override_is_destination_aware(tmp_path):
    source = _source(tmp_path)
    initial = build_instruction_ledger(source)
    repeated = [
        entry
        for entry in initial.entries
        if entry.text == "Instruction: repeated text."
    ]
    overrides = {
        repeated[1].locator: {
            "disposition": DISPOSITION_DUPLICATE,
            "duplicate_of": repeated[0].locator,
            "destination": repeated[0].destination,
            "visibility": "not_rendered",
        }
    }
    ledger = build_instruction_ledger(source, overrides=overrides)
    assert_reconciled(ledger, _rendered(tmp_path, ledger))

    duplicate = next(
        entry for entry in ledger.entries if entry.disposition == DISPOSITION_DUPLICATE
    )
    duplicate.destination = "guidance/wrong.md"
    assert any(
        "duplicate destination differs from primary" in finding
        for finding in reconciliation_findings(ledger, _rendered(tmp_path, ledger))
    )


def test_policy_is_versioned_and_rejects_unknown_or_unused_locators(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text("policy_version: 2\noverrides: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported instruction policy_version"):
        load_instruction_policy(policy)
    with pytest.raises(ValueError, match="Unused or unknown override locators"):
        build_instruction_ledger(
            _source(tmp_path), overrides={"word/document.xml#missing": {}}
        )


def test_cli_rejects_unused_policy_override(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "policy_version: 1\noverrides:\n  word/document.xml#missing: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        main(["--docx", str(_source(tmp_path)), "--policy", str(policy)])
    assert exc.value.code == 2


def test_cli_renders_and_strictly_reconciles_synthetic_fixture(tmp_path):
    report = tmp_path / "ledger.json"
    output = tmp_path / "rendered"
    assert (
        main(
            [
                "--synthetic",
                "--json",
                str(report),
                "--output-dir",
                str(output),
                "--render",
                "--strict",
            ]
        )
        == 0
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["policy_version"] == 1
    assert payload["summary"]["total"] == 3
    assert len(list(output.rglob("*.md"))) == 3


def test_rendered_guidance_is_typed_and_reconciles_canonical_sidecars(
    tmp_path, monkeypatch
):
    ledger = build_instruction_ledger(_source(tmp_path))
    output = tmp_path / "vault"
    render_instruction_outputs(ledger, output)
    monkeypatch.setattr(
        "sys.argv", ["generate_canonical_json.py", "--vault", str(output)]
    )
    assert generate_canonical_main() == 0

    first = output / ledger.entries[0].destination
    assert "type: okf.instruction" in first.read_text(encoding="utf-8")
    validation = subprocess.run(
        [
            sys.executable,
            "compliance/scripts/validate_frontmatter.py",
            "--vault",
            str(output),
            "--strict",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert validation.returncode == 0, validation.stdout + validation.stderr
    canonical = inventory_canonical_instructions(output)
    assert len(canonical) == len(ledger.entries)
    assert canonical_reconciliation_findings(ledger, canonical) == []


def test_production_export_seam_writes_reconcilable_guidance(tmp_path, monkeypatch):
    source = _source(tmp_path)
    output = tmp_path / "vault"
    ledger = build_instruction_ledger(source)

    assert _write_instruction_guidance(str(source), output) == len(ledger.entries)
    monkeypatch.setattr(
        "sys.argv", ["generate_canonical_json.py", "--vault", str(output)]
    )
    assert generate_canonical_main() == 0
    assert (
        reconciliation_findings(ledger, inventory_rendered_instructions(output)) == []
    )
    assert (
        canonical_reconciliation_findings(
            ledger, inventory_canonical_instructions(output)
        )
        == []
    )


def test_cli_reports_markdown_and_canonical_reconciliation(tmp_path, monkeypatch):
    source = _source(tmp_path)
    ledger = build_instruction_ledger(source)
    output = tmp_path / "vault"
    report = tmp_path / "report.json"
    render_instruction_outputs(ledger, output)
    monkeypatch.setattr(
        "sys.argv", ["generate_canonical_json.py", "--vault", str(output)]
    )
    assert generate_canonical_main() == 0

    assert (
        main(
            [
                "--docx",
                str(source),
                "--output-dir",
                str(output),
                "--json",
                str(report),
                "--strict",
                "--require-canonical",
            ]
        )
        == 0
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["reconciliation"] == {
        "rendered_records": len(ledger.entries),
        "canonical_records": len(ledger.entries),
        "findings": 0,
        "coverage": 1.0,
    }


def test_cli_can_explicitly_skip_missing_protected_fixture(tmp_path):
    assert main(["--docx", str(tmp_path / "missing.docx"), "--skip-missing"]) == 0
