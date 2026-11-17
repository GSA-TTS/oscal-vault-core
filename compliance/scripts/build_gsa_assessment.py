#!/usr/bin/env python3
"""Build a system's GSA Assessment & Attestation (A&A) document.

Consumes a neutral system-input JSON (emitted by the system repository's own adapter, see
``system_assessment_input_schema_v1.json``) plus the two canonical GSA workbook JSON files.
Writes ``<out-dir>/<slug>-aa-assessment.json`` and ``.md``. ``--check`` writes nothing and
exits 1 on drift. Output goes to the workspace, never into a vendored core (ADR-028).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.lib.gsa_assessment import AssessmentError, build_assessment  # noqa: E402
from compliance.lib.gsa_assessment_report import (  # noqa: E402
    redaction_hits,
    render_json,
    render_markdown,
)
from compliance.lib.gsa_workbooks import TAILORING_FILENAME, TEST_CASES_FILENAME  # noqa: E402
from compliance.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_arguments,
    guard_output,
    resolve_workspace,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-input", type=Path, required=True)
    parser.add_argument(
        "--tailoring",
        type=Path,
        help=f"Default: <workspace>/data/catalog/{TAILORING_FILENAME}",
    )
    parser.add_argument(
        "--test-cases",
        type=Path,
        help=f"Default: <workspace>/data/catalog/{TEST_CASES_FILENAME}",
    )
    parser.add_argument(
        "--out-dir", type=Path, help="Default: <workspace>/build/assessment"
    )
    parser.add_argument(
        "--check", action="store_true", help="Fail on drift; write nothing"
    )
    add_workspace_arguments(parser)
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        catalog = workspace / "data" / "catalog"
        system = _load(args.system_input)
        assessment = build_assessment(
            system,
            _load(args.tailoring or catalog / TAILORING_FILENAME),
            _load(args.test_cases or catalog / TEST_CASES_FILENAME),
        )
        out_dir = args.out_dir or workspace / "build" / "assessment"
        guard_output(out_dir, allow_in_core=args.allow_write_in_core)
    except (AssessmentError, WorkspaceError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    slug = assessment["system"]
    confirmations_path = system["system"].get("confirmations_path")
    outputs = {
        out_dir / f"{slug}-aa-assessment.json": render_json(assessment),
        out_dir / f"{slug}-aa-assessment.md": render_markdown(
            assessment, confirmations_path=confirmations_path
        )
        + "\n",
    }
    hits = redaction_hits(*outputs.values())
    if hits:
        print(f"ERROR: redaction gate violations in output: {hits}", file=sys.stderr)
        return 1
    if args.check:
        stale = [
            str(p)
            for p, text in outputs.items()
            if not p.exists() or p.read_text(encoding="utf-8") != text
        ]
        if stale:
            print("stale assessment artifacts: " + ", ".join(stale), file=sys.stderr)
            return 1
        print("Assessment artifacts are current.")
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, text in outputs.items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    s = assessment["summary"]
    print(f"controls: {s['control_results']}")
    print(
        f"findings: {s['findings_by_severity']}; readiness: {assessment['attestation']['readiness']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
