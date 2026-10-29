#!/usr/bin/env python3
"""Generate time-stamped snapshots from canonical JSON sidecars.

Creates immutable snapshot copies in `.snapshots/history/` from the
current canonical JSON sidecars in `.snapshots/current/`.

Usage:
    # Capture snapshots for all vaults
    python3 compliance/scripts/snapshot.py

    # Capture for a specific vault
    python3 compliance/scripts/snapshot.py --vault examples/vault/<system>/

    # Generate SHA-256 manifest (cryptographic signing)
    python3 compliance/scripts/snapshot.py --release --vault examples/vault/<system>/

    # With a custom release tag
    python3 compliance/scripts/snapshot.py --tag v1.2.0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.snapshot import (  # noqa: E402
    capture_snapshot,
    collect_canonical_sidecars,
    generate_sha256_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate time-stamped snapshots from canonical JSON sidecars"
    )
    parser.add_argument(
        "--vault",
        action="append",
        default=None,
        help="Path to vault root (repeatable; default: all subdirs of examples/vault/)",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="Generate SHA-256 manifest for cryptographic signing",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Release tag to include in manifest metadata",
    )
    args = parser.parse_args()

    vault_dirs: List[Path] = []
    if args.vault:
        for v in args.vault:
            vd = Path(v).resolve()
            if not vd.is_dir():
                print(f"ERROR: Vault directory {vd} does not exist", file=sys.stderr)
                return 1
            vault_dirs.append(vd)
    else:
        examples_vault = Path.cwd() / "examples" / "vault"
        if examples_vault.is_dir():
            for entry in sorted(examples_vault.iterdir()):
                if entry.is_dir() and not entry.name.startswith("."):
                    vault_dirs.append(entry.resolve())

    if not vault_dirs:
        print("No vault directories to process.", file=sys.stderr)
        return 0

    total_errors = 0
    total_snapshots = 0

    for vault_dir in vault_dirs:
        sidecars = collect_canonical_sidecars(vault_dir)
        history_root = vault_dir / ".snapshots" / "history"

        if not sidecars:
            print(f"No canonical sidecars found in vault {vault_dir.name}")
            continue

        snapshot_paths: List[Path] = []
        errors = 0

        for sc_path in sidecars:
            try:
                snap_path = capture_snapshot(sc_path, history_root, vault_dir)
                snapshot_paths.append(snap_path)
            except Exception as e:
                print(f"ERROR: failed to snapshot {sc_path.name}: {e}", file=sys.stderr)
                errors += 1

        print(
            f"Snapshotted vault {vault_dir.name}: "
            f"{len(snapshot_paths)} snapshots captured, {errors} errors."
        )
        total_snapshots += len(snapshot_paths)
        total_errors += errors

        # Generate manifest if --release
        if args.release and snapshot_paths:
            manifest_path = history_root / "manifest-sha256.json"
            generate_sha256_manifest(snapshot_paths, manifest_path)
            print(f"  Manifest written: {manifest_path}")

    print(
        f"\nDone: {total_snapshots} total snapshots across {len(vault_dirs)} vault(s), "
        f"{total_errors} errors."
    )
    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
