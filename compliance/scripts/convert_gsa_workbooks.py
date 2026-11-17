#!/usr/bin/env python3
"""Convert GSA assessment workbooks (ODS) to canonical JSON, or validate existing JSON.

Examples:
    convert_gsa_workbooks.py --tailoring-workbook CTW.ods --test-case-workbook TC.ods
    convert_gsa_workbooks.py --tailoring-workbook CTW.ods --test-case-workbook TC.ods --check
    convert_gsa_workbooks.py --validate data/catalog/*.canonical.json
    convert_gsa_workbooks.py --tailoring-workbook CTW.ods --test-case-workbook TC.ods --bundled --check

Output goes under the workspace (``--workspace``, ``OSCAL_VAULT_WORKSPACE``, or the current
directory), never inside a vendored core. ``--check`` writes nothing and exits 1 on drift.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.lib.gsa_workbooks import (  # noqa: E402
    DATA_DIR,
    TAILORING_FILENAME,
    TEST_CASES_FILENAME,
    WorkbookError,
    convert_tailoring_workbook,
    convert_test_case_workbook,
    render_json,
    validate_canonical_json,
)
from compliance.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_arguments,
    guard_output,
    resolve_workspace,
)


def _validate(paths: list[Path]) -> int:
    failures = 0
    for path in paths:
        try:
            validate_canonical_json(json.loads(path.read_text(encoding="utf-8")))
            print(f"OK   {path}")
        except (WorkbookError, OSError, json.JSONDecodeError) as exc:
            failures += 1
            print(f"FAIL {path}: {exc}", file=sys.stderr)
    return 1 if failures else 0


def _convert(args: argparse.Namespace) -> int:
    jobs = []
    if args.tailoring_workbook:
        data = convert_tailoring_workbook(
            args.tailoring_workbook, version=args.tailoring_version
        )
        jobs.append((TAILORING_FILENAME, data))
    if args.test_case_workbook:
        data = convert_test_case_workbook(
            args.test_case_workbook, version=args.test_case_version
        )
        jobs.append((TEST_CASES_FILENAME, data))
    if not jobs:
        print("nothing to do: pass a workbook or --validate", file=sys.stderr)
        return 2

    workspace = resolve_workspace(args.workspace)
    if args.bundled:
        out_dir = DATA_DIR
    else:
        out_dir = args.out_dir or workspace / "data" / "catalog"
    guard_output(out_dir, allow_in_core=args.allow_write_in_core)

    drift = False
    for filename, data in jobs:
        validate_canonical_json(data)
        text = render_json(data)
        target = out_dir / filename
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                print(f"drift: {target}", file=sys.stderr)
                drift = True
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target}")
    if args.check:
        if drift:
            return 1
        print("OK - canonical JSON matches the workbooks")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tailoring-workbook", type=Path)
    parser.add_argument("--test-case-workbook", type=Path)
    parser.add_argument(
        "--tailoring-version", help="Override the version inferred from the file name"
    )
    parser.add_argument(
        "--test-case-version", help="Override the version read from the Notes sheet"
    )
    parser.add_argument(
        "--out-dir", type=Path, help="Default: <workspace>/data/catalog"
    )
    parser.add_argument(
        "--bundled",
        action="store_true",
        help="Target the JSON the core ships in compliance/data/gsa (maintainers; "
        "update compliance/data/gsa/PROVENANCE.md and open a reviewed PR)",
    )
    parser.add_argument(
        "--check", action="store_true", help="Fail on drift; write nothing"
    )
    parser.add_argument(
        "--validate",
        nargs="+",
        type=Path,
        metavar="JSON",
        help="Validate canonical JSON files and exit",
    )
    add_workspace_arguments(parser)
    args = parser.parse_args(argv)
    try:
        if args.validate:
            return _validate(args.validate)
        return _convert(args)
    except (WorkbookError, WorkspaceError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
