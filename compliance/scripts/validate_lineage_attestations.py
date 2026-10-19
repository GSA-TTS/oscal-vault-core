#!/usr/bin/env python3
"""Validate digest-bound review attestations for lossy lineage operators."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compliance.lib.lineage_attestation import validate_attestations  # noqa: E402


def main() -> int:
    path = REPO_ROOT / "compliance/data/lineage_operator_attestations_v1.json"
    findings = validate_attestations(path, REPO_ROOT)
    for finding in findings:
        print(f"ATTESTATION {finding}", file=sys.stderr)
    if not findings:
        print(f"Lineage operator attestations valid: {path}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
