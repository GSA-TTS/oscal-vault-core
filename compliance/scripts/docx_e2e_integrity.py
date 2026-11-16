#!/usr/bin/env python3
"""Run one authorized DOCX through local, fail-closed integrity checks.

Exit codes: 0 machine PASS (human review pending), 1 FAIL, 2 BLOCKED,
3 INCOMPLETE. No corpus discovery, source edits, or network operations.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "fismatic" / "src" / "phase_1"))

# Keep the original import surface for existing callers; implementation is library-owned.
from compliance.lib.docx_integrity_context import (  # noqa: E402, F401
    verified_fill_context,
)
from compliance.lib.docx_integrity_fillback import (  # noqa: E402, F401
    control_readback,
    fillback_stage,
)
from compliance.lib.docx_integrity_pipeline import (  # noqa: E402, F401
    execute_run,
    prepare_output,
    run_command,
)
from compliance.lib.docx_integrity_report import StageError  # noqa: E402, F401

FAMILIES = ("low", "moderate", "high", "crm", "lightweight", "unknown")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--system-id", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--template-family", choices=FAMILIES, default="unknown")
    parser.add_argument("--fill-context", type=Path)
    parser.add_argument(
        "--decisions",
        type=Path,
        help="Hash-bound reviewed package delta decisions (JSON list)",
    )
    parser.add_argument(
        "--strict", action="store_true", help="Compatibility flag; all runs are strict"
    )
    parser.add_argument(
        "--authorized",
        action="store_true",
        help="Attest local processing of this source was authorized",
    )
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    if (
        not re.fullmatch(r"[a-z0-9_][a-z0-9_-]{0,100}", args.system_id)
        or args.timeout <= 0
    ):
        parser.error("invalid system ID or timeout")
    if not args.authorized:
        parser.error(
            "--authorized is required; source processing must be authorized by its owner"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    old_mask = os.umask(0o077)
    try:
        output = prepare_output(args.output, args.source, args.workspace)
        with (output / "local-processing.log").open("w", encoding="utf-8") as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                report = execute_run(args, output)
    except (OSError, ValueError) as exc:
        print(
            f"BLOCKED: {type(exc).__name__}; choose a fresh approved ignored/output directory",
            file=sys.stderr,
        )
        return 2
    finally:
        os.umask(old_mask)
    print(
        f"{report['status']}: {args.system_id}; human review pending; {output / 'compare' / 'final-report.json'}"
    )
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "INCOMPLETE": 3}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
