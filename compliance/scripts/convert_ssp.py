#!/usr/bin/env python3
"""Run the unified contract-driven SSP converter (Phase R11).

Consumes a raw FedRAMP SSP .docx file and an ODCS YAML datacontract, routes the
content via the capture strategies in the contract, and writes both the Obsidian
markdown notes and the canonical JSON sidecars to their destination folders.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.unified_converter import UnifiedConverter, DEFAULT_BACKEND  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a FedRAMP SSP .docx to an Obsidian vault + JSON sidecars using UnifiedConverter."
    )
    parser.add_argument(
        "--docx",
        type=Path,
        required=True,
        help="Path to the source .docx SSP file.",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        required=True,
        help="Path to the per-document ODCS v3.1.0 YAML datacontract.",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Destination directory for the Obsidian Markdown vault.",
    )
    parser.add_argument(
        "--snapshots",
        type=Path,
        default=None,
        help="Optional destination for canonical JSON sidecars. Defaults to <vault>/.snapshots/current/.",
    )
    parser.add_argument(
        "--backend",
        type=str,
        default=DEFAULT_BACKEND,
        help=f"Extractor backend to use for stage 1 (default: {DEFAULT_BACKEND}).",
    )

    args = parser.parse_args(argv)

    if not args.docx.exists():
        print(f"ERROR: docx not found at {args.docx}", file=sys.stderr)
        return 2

    if not args.contract.exists():
        print(f"ERROR: contract not found at {args.contract}", file=sys.stderr)
        return 2

    converter = UnifiedConverter(
        contract_path=args.contract,
        backend=args.backend,
    )

    print(f"Converting {args.docx.name} using contract {args.contract.name} ...")
    print(f"  Backend: {args.backend}")
    print(f"  Vault: {args.vault}")
    print(f"  Snapshots: {args.snapshots or (args.vault / '.snapshots' / 'current')}")

    result = converter.convert(
        docx_path=args.docx,
        vault_dir=args.vault,
        snapshots_dir=args.snapshots,
    )

    if result.errors:
        print("\nFATAL ERRORS:")
        for err in result.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    if result.warnings:
        print("\nWARNINGS:")
        for warn in result.warnings:
            print(f"  - {warn}")

    print(f"\nSUCCESS: Converted {len(result.entities)} entities.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
