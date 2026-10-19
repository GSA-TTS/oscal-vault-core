#!/usr/bin/env python3
"""Write a deterministic DOCX package, IR, table, drawing, and opaque manifest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compliance.lib.docx_preservation import (  # noqa: E402
    collect_opaque_artifacts,
    extract_managed_artifacts,
    verify_managed_artifacts,
    write_source_manifest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = collect_opaque_artifacts(args.docx)
    except ValueError as exc:
        print(f"LOSS {args.docx}: {exc}", file=sys.stderr)
        return 1
    artifacts = extract_managed_artifacts(args.docx, args.output)
    path = write_source_manifest(report, args.output, artifacts)
    print(
        f"Wrote {path}: {len(report.package_inventory.get('parts', []))} parts, "
        f"{len(report.source_tables)} table(s), {len(report.artifacts)} opaque artifact(s)"
    )
    findings = report.loss_budget_findings()
    findings.extend(verify_managed_artifacts(artifacts, args.output))
    for finding in findings:
        print(f"LOSS {args.docx}: {finding}", file=sys.stderr)
    if args.strict and findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
