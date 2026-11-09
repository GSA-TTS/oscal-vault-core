#!/usr/bin/env python3
"""Validate generated SSP DOCX control-fill coverage and artifact presence."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

PLACEHOLDER_PATTERNS = (
    re.compile(r"TBD\b", re.IGNORECASE),
    re.compile(r"\[ACRONYM\]", re.IGNORECASE),
    re.compile(r"\[Enter\b", re.IGNORECASE),
)


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
            if name.startswith("word/") and name.endswith(".xml")
        )


def validate(
    docx: Path,
    manifest: Path,
    *,
    fail_duplicate_targets: bool = False,
    fail_placeholders: bool = False,
    fail_pending: bool = False,
) -> list[str]:
    errors = []
    if not docx.is_file() or docx.stat().st_size == 0:
        errors.append(f"missing or empty DOCX: {docx}")
    if not manifest.is_file():
        errors.append(f"missing manifest: {manifest}")
        return errors
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("source_controls_without_target_table"):
        errors.append("source controls were omitted from the target template")
    if fail_duplicate_targets and data.get("duplicate_target_control_ids"):
        errors.append("target template contains duplicate control IDs")
    if data.get("source_controls", 0) != data.get("matching_control_ids_filled", -1):
        errors.append("not every source control was filled")
    chapter_tables = data.get("chapter_tables")
    if chapter_tables:
        if chapter_tables.get("source_rows", 0) != chapter_tables.get(
            "written_rows", -1
        ):
            errors.append("not every Chapters 1-12 source row was written")
        if chapter_tables.get("mismatches"):
            errors.append("Chapters 1-12 table readback differs from source rows")
        source_rows = chapter_tables.get("source_rows", 0)
        written_rows = chapter_tables.get("written_rows", 0)
        row_fidelity = written_rows / source_rows if source_rows else 1.0
        if row_fidelity < 0.99:
            errors.append(
                f"Chapters 1-12 row fidelity is {row_fidelity:.2%}, below 99%"
            )
        pending = (chapter_tables.get("dispositions") or {}).get(
            "pending-authoritative-source", 0
        )
        if fail_pending and pending:
            errors.append(f"{pending} document fields await an authoritative source")
    if any(not item.get("matched") for item in data.get("anchored_content", [])):
        errors.append("anchored text or image readback differs from source")
    if (data.get("template_tokens") or {}).get("remaining_tokens"):
        errors.append("allowlisted template tokens remain unresolved")
    fidelity = data.get("fidelity") or {}
    for critical in ("control_identity", "anchored_content"):
        if fidelity and fidelity.get(critical, 0) != 1.0:
            errors.append(f"critical fidelity gate {critical} is not 100%")
    for metric in ("statement_placement", "chapter_rows"):
        if fidelity and fidelity.get(metric, 0) < 0.99:
            errors.append(f"fidelity gate {metric} is below 99%")
    if data.get("source_id_collisions"):
        errors.append("source control IDs collide after normalization")
    unmatched_statements = [
        statement
        for control in data.get("controls", [])
        for statement in control.get("statements", [])
        if not statement.get("written")
    ]
    if unmatched_statements:
        errors.append(f"{len(unmatched_statements)} source statements were not written")
    statements = [
        statement
        for control in data.get("controls", [])
        for statement in control.get("statements", [])
    ]
    statement_fidelity = (
        sum(bool(statement.get("written")) for statement in statements)
        / len(statements)
        if statements
        else 1.0
    )
    if statement_fidelity < 0.99:
        errors.append(
            f"control statement fidelity is {statement_fidelity:.2%}, below 99%"
        )
    if fail_placeholders and docx.is_file():
        text = _docx_text(docx)
        placeholders = sum(
            len(pattern.findall(text)) for pattern in PLACEHOLDER_PATTERNS
        )
        if placeholders:
            errors.append(
                f"generated DOCX contains {placeholders} unresolved placeholders"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fail-duplicate-targets", action="store_true")
    parser.add_argument("--fail-placeholders", action="store_true")
    parser.add_argument("--fail-pending", action="store_true")
    args = parser.parse_args(argv)
    errors = validate(
        args.docx,
        args.manifest,
        fail_duplicate_targets=args.fail_duplicate_targets,
        fail_placeholders=args.fail_placeholders,
        fail_pending=args.fail_pending,
    )
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("OK - generated DOCX contains every source control.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
