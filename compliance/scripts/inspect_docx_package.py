#!/usr/bin/env python3
"""Inventory DOCX package parts/nodes and fail strict mode on unmapped content.

Strict mode is fail-closed on three findings:

* a package part with no declared disposition (including unreachable members),
* a content-bearing body node with no declared disposition,
* an internal relationship whose target part is missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compliance.lib.docx_package import (  # noqa: E402
    PackageIntegrityError,
    PackageLimitError,
    inspect_package,
    strict_findings,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", required=True, type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = inspect_package(args.docx)
    except (PackageLimitError, PackageIntegrityError) as exc:
        print(f"ERROR {args.docx}: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")

    findings = strict_findings(report)
    if findings:
        for finding in findings:
            print(f"UNCLASSIFIED {args.docx}: {finding}", file=sys.stderr)
        if args.strict:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
