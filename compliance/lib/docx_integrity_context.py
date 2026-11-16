"""Verify exact source-bound fill values without importing CLI entry points."""

from __future__ import annotations

import json
import re
from pathlib import Path

from compliance.lib.docx_integrity_compare import digest
from compliance.lib.docx_integrity_report import StageError


def verified_fill_context(context_path: Path, source: Path, output: Path) -> dict:
    """Accept values only when exact RFC6901 pointers resolve in this run's evidence."""
    from compliance.lib.docx_ir import LineageTracker

    envelope = json.loads(context_path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or set(envelope) != {
        "values",
        "source_sha256",
        "evidence",
    }:
        raise StageError(
            "BLOCKED", "fill context requires values, source_sha256 and evidence"
        )
    values, evidence = envelope["values"], envelope["evidence"]
    if (
        not isinstance(values, dict)
        or not isinstance(evidence, dict)
        or set(values) != set(evidence)
    ):
        raise StageError(
            "BLOCKED", "each fill value requires source/canonical evidence"
        )
    if envelope["source_sha256"] != digest(source.read_bytes()):
        raise StageError("BLOCKED", "fill context source hash differs")
    for tag, value in values.items():
        path = fill_evidence_path(evidence[tag], output)
        actual = LineageTracker._pointer_value(
            json.loads(path.read_text()), evidence[tag]["pointer"]
        )
        if not isinstance(value, str) or actual != value:
            raise StageError(
                "BLOCKED",
                "fill value does not match recorded source/canonical evidence",
            )
    return values


def fill_evidence_path(binding: dict, output: Path) -> Path:
    if not isinstance(binding, dict) or set(binding) != {"artifact", "pointer"}:
        raise StageError("BLOCKED", "invalid fill evidence binding")
    artifact = binding["artifact"]
    if (
        not isinstance(artifact, str)
        or Path(artifact).is_absolute()
        or ".." in Path(artifact).parts
    ):
        raise StageError("BLOCKED", "fill evidence must be run-relative")
    if artifact != "source/nodes.json" and not re.fullmatch(
        r"vault/[a-z0-9_-]+/\.snapshots/current/.+\.json", artifact
    ):
        raise StageError(
            "BLOCKED", "fill evidence must reference source IR or canonical sidecars"
        )
    path = (output / artifact).resolve()
    if output.resolve() not in path.parents or not path.is_file():
        raise StageError("BLOCKED", "fill evidence is missing or outside run")
    if not isinstance(binding["pointer"], str) or not binding["pointer"].startswith(
        "/"
    ):
        raise StageError("BLOCKED", "invalid evidence pointer")
    return path
