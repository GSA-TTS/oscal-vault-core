#!/usr/bin/env python3
"""Build release bundles from vault directories.

Creates compressed tarball bundles containing markdown notes, canonical JSON
sidecars, templates, governance docs, and a manifest.json with file hashes
for audit/compliance verification.

Usage:
    # Build bundle for a specific vault
    python3 compliance/scripts/bundle.py --vault examples/vault/gsa-pages-ssp-2026/

    # Custom version and description
    python3 compliance/scripts/bundle.py --vault examples/vault/gsa-pages-ssp-2026/ --version 2.0.0 --description "Q3 compliance snapshot"

    # Gzip instead of zstd
    python3 compliance/scripts/bundle.py --vault examples/vault/gsa-pages-ssp-2026/ --compression gz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.bundle import build_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build release bundles from vault directories"
    )
    parser.add_argument(
        "--vault",
        required=True,
        action="append",
        default=None,
        help="Path to vault root (repeatable; required)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for bundles (default: repo_root/.exports/bundles/)",
    )
    parser.add_argument(
        "--version",
        default="1.0.0",
        help="Semantic version for the bundle (default: 1.0.0)",
    )
    parser.add_argument(
        "--description",
        default="",
        help="Human-readable description of the bundle",
    )
    parser.add_argument(
        "--compression",
        choices=["zst", "gz", "none"],
        default="zst",
        help="Compression type: zst (Zstandard), gz (gzip), or none (default: zst)",
    )
    args = parser.parse_args()

    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else _REPO_ROOT / ".exports" / "bundles"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    total_errors = 0

    for v in args.vault:
        vault_dir = Path(v).resolve()
        if not vault_dir.is_dir():
            print(f"ERROR: Vault directory {vault_dir} does not exist", file=sys.stderr)
            total_errors += 1
            continue

        bundle_name = vault_dir.name

        try:
            tarball_path = build_bundle(
                vault_root=vault_dir,
                output_path=output_dir,
                bundle_name=bundle_name,
                version=args.version,
                description=args.description,
                compression=args.compression,
            )
            size_mb = tarball_path.stat().st_size / (1024 * 1024)
            print(f"Bundle created: {tarball_path} ({size_mb:.1f} MB)")
        except Exception as e:
            print(
                f"ERROR: failed to build bundle for {vault_dir.name}: {e}",
                file=sys.stderr,
            )
            total_errors += 1

    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
