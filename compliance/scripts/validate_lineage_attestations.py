#!/usr/bin/env python3
"""Validate digest-bound review attestations for lossy lineage operators.

An attestation is a human reviewer's approval of one operator's implementation at a specific
digest. It records a decision made by whoever runs the pipeline, so it lives with the vaults (the
system repository), not in the core. The digest covers the core's own source files, so upgrading
the core invalidates an attestation until it is reviewed again; that is the intended fail-closed
behaviour.

    python <core>/compliance/scripts/validate_lineage_attestations.py [--attestations PATH]

Default path: ./scripts/lineage_operator_attestations_v1.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[2]
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from compliance.lib.lineage_attestation import validate_attestations  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--attestations",
        type=Path,
        default=Path.cwd() / "scripts" / "lineage_operator_attestations_v1.json",
    )
    args = parser.parse_args(argv)
    if not args.attestations.is_file():
        print(f"ERROR: attestations not found: {args.attestations}", file=sys.stderr)
        return 2
    findings = validate_attestations(args.attestations, CORE_ROOT)
    for finding in findings:
        print(f"ATTESTATION {finding}", file=sys.stderr)
    if not findings:
        print(f"Lineage operator attestations valid: {args.attestations}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
