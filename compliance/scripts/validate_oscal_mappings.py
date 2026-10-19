#!/usr/bin/env python3
"""Report contract OSCAL mappings and fail on unclaimed active native targets."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compliance.lib.export_oscal import EXPORTER_MAPPING_ADAPTERS  # noqa: E402
from compliance.lib.oscal_mapping_registry import (  # noqa: E402
    MappingValidationError,
    validate_mapping_claims,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        type=Path,
        default=REPO_ROOT / "compliance/datacontracts/fedramp-ssp-v1.yaml",
    )
    args = parser.parse_args()
    try:
        mappings = validate_mapping_claims(args.contract, EXPORTER_MAPPING_ADAPTERS)
    except MappingValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    counts = Counter(mapping.classification for mapping in mappings)
    print("OSCAL mapping validation passed")
    for classification in sorted(counts):
        print(f"{classification}: {counts[classification]}")
    active = [mapping for mapping in mappings if mapping.status == "active"]
    print(f"active adapter-backed mappings: {len(active)}")
    print(f"exporter adapters: {', '.join(sorted(EXPORTER_MAPPING_ADAPTERS))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
