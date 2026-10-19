"""Snapshot capture from canonical JSON sidecars.

Generates immutable time-stamped snapshots from `.snapshots/current/` sidecars
into `.snapshots/history/`. Supports optional cryptographic signing via
SHA-256 manifests when invoked with `--release` or `--tag` flags.

Architecture (Phase R6):
  1. Authoring layer: markdown OKF notes (done)
  2. Canonical layer: `.snapshots/current/<type>/<id>.json` (done via R2)
  3. Snapshot layer: `.snapshots/history/<type>/<id>.<timestamp>.json` (this module)
  4. Export layer: `.exports/` OSCAL JSON/YAML (export_oscal.py)
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def capture_snapshot(
    canonical_path: Path,
    history_root: Path,
    vault_root: Path,
    timestamp_override: Optional[str] = None,
) -> Path:
    """Create a time-stamped snapshot from a canonical JSON sidecar.

    Copies the canonical JSON to `.snapshots/history/<rel_path>.<timestamp>.json`
    where `<rel_path>` mirrors the structure under `.snapshots/current/`.

    Args:
        canonical_path: Path to the canonical JSON sidecar.
        history_root: Root of the history snapshots directory (e.g. `.snapshots/history/`).
        vault_root: Root directory of the vault.
        timestamp_override: Optional ISO timestamp for deterministic testing.

    Returns:
        Path to the created snapshot file.

    Raises:
        FileNotFoundError: If the canonical JSON doesn't exist.
        ValueError: If the canonical JSON is invalid.
    """
    if not canonical_path.exists():
        raise FileNotFoundError(f"Canonical JSON not found: {canonical_path}")

    # Read and validate the canonical JSON
    with open(canonical_path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {canonical_path}: {e}")

    # Determine relative path from .snapshots/current/
    current_root = vault_root / ".snapshots" / "current"
    try:
        rel_path = canonical_path.relative_to(current_root)
    except ValueError:
        raise ValueError(f"Canonical path {canonical_path} is not under {current_root}")

    # Generate timestamp
    timestamp = timestamp_override or datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H-%M-%SZ")

    # Build snapshot path: .snapshots/history/<rel_path>.<timestamp>.json
    snapshot_name = f"{rel_path.stem}.{timestamp}{rel_path.suffix}"
    snapshot_path = history_root / rel_path.parent / snapshot_name
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    # Add snapshot metadata
    data["_snapshot"] = {
        "captured_at": timestamp_override
        or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "captured_from": str(rel_path),
        "snapshot_version": 1,
    }

    # Write snapshot
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)

    return snapshot_path


def generate_sha256_manifest(snapshot_paths: List[Path], output_path: Path) -> Path:
    """Generate a SHA-256 manifest for a set of snapshot files.

    Creates a JSON file mapping each file's relative path to its SHA-256 hash.
    Used for cryptographic integrity verification in release/signing workflows.

    Args:
        snapshot_paths: List of paths to snapshot files.
        output_path: Path where the manifest JSON will be written.

    Returns:
        Path to the generated manifest file.
    """
    manifest: Dict[str, str] = {}
    base_path = output_path.parent

    for sp in sorted(snapshot_paths):
        try:
            rel = sp.relative_to(base_path)
        except ValueError:
            rel = sp

        # Compute SHA-256
        sha256 = hashlib.sha256()
        with open(sp, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        manifest[str(rel)] = "sha256:" + sha256.hexdigest()

    manifest_data: Dict[str, Any] = {
        "manifest_version": 1,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_files": len(manifest),
        "files": manifest,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, sort_keys=True)

    return output_path


def collect_canonical_sidecars(vault_root: Path) -> List[Path]:
    """Find all canonical JSON sidecars in `.snapshots/current/` for a vault.

    Args:
        vault_root: Root directory of the vault.

    Returns:
        Sorted list of Path objects for all canonical JSON sidecars.
    """
    current_root = vault_root / ".snapshots" / "current"
    if not current_root.is_dir():
        return []

    sidecars = sorted(current_root.rglob("*.json"))
    return sidecars
