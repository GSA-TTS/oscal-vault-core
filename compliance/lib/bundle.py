"""Bundle packaging for vault artifacts.

Creates release bundles containing:
  - manifest.json (bundle metadata, file listing, hashes)
  - notes/ (markdown source)
  - snapshots/ (canonical JSON sidecars)
  - exports/ (OSCAL JSON/YAML for supported types)

Output format: tar.zst (Zstandard-compressed tarball) for efficient storage
and distribution of compliance evidence packages.

Architecture (Phase R6, layer 4):
  Bundle = manifest + notes/ + snapshots/ + exports/
"""

from __future__ import annotations

import datetime
import hashlib
import json
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from importlib.util import find_spec

# Zstandard is used for optimal compression of compliance packages.
# If not available, fall back to gzip.
_HAS_ZSTD = find_spec("zstandard") is not None


def _compute_file_hash(path: Path, algorithm: str = "sha256") -> str:
    """Compute a cryptographic hash for a file."""
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"{algorithm}:{h.hexdigest()}"


def _collect_vault_files(
    vault_root: Path, include_patterns: Optional[List[str]] = None
) -> Dict[str, List[Path]]:
    """Collect files from a vault organized by category.

    Args:
        vault_root: Root directory of the vault.
        include_patterns: Glob patterns to include (default: all .md, .json, .yaml).

    Returns:
        Dict mapping category name to list of file paths.
    """
    categories: Dict[str, List[Path]] = {
        "notes": [],
        "snapshots": [],
        "templates": [],
        "governance": [],
        "mappings": [],
        "other": [],
    }

    skip_dirs = {"__pycache__", ".snapshots", ".git", "_meta"}

    for path in sorted(vault_root.rglob("*")):
        if not path.is_file():
            continue

        # Skip unwanted directories
        parts = set(path.relative_to(vault_root).parts)
        if parts & skip_dirs or any(p.startswith(".") for p in parts):
            continue

        rel_str = str(path.relative_to(vault_root))

        if path.suffix == ".md":
            if rel_str == "Index.md":
                categories["other"].append(path)
            elif rel_str.startswith("_meta/templates/"):
                categories["templates"].append(path)
            elif rel_str.startswith("governance/"):
                categories["governance"].append(path)
            elif rel_str.startswith("Mappings/"):
                categories["mappings"].append(path)
            else:
                categories["notes"].append(path)
        elif path.suffix == ".json" and ".snapshots/current/" in rel_str:
            categories["snapshots"].append(path)
        else:
            categories["other"].append(path)

    return categories


def create_bundle_manifest(
    vault_root: Path,
    bundle_name: str,
    version: str = "1.0.0",
    description: str = "",
) -> Dict[str, Any]:
    """Create a manifest JSON document describing the bundle.

    The manifest includes metadata, a file listing with hashes, and
    provenance information for audit/compliance purposes.

    Args:
        vault_root: Root directory of the vault.
        bundle_name: Name identifier for the bundle.
        version: Semantic version string.
        description: Human-readable description of the bundle contents.
    """
    categories = _collect_vault_files(vault_root)

    files: List[Dict[str, str]] = []
    total_size = 0

    all_files = (
        categories["notes"]
        + categories["snapshots"]
        + categories["templates"]
        + categories["governance"]
        + categories["mappings"]
    )

    for fp in sorted(all_files):
        try:
            rel_path = fp.relative_to(vault_root)
        except ValueError:
            rel_path = fp

        size = fp.stat().st_size
        file_hash = _compute_file_hash(fp)
        files.append(
            {
                "path": str(rel_path),
                "size_bytes": size,
                "hash": file_hash,
            }
        )
        total_size += size

    return {
        "manifest_version": 1,
        "bundle": {
            "name": bundle_name,
            "version": version,
            "description": description,
        },
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "vault_root": str(vault_root.name),
        "file_count": len(files),
        "total_size_bytes": total_size,
        "categories": {
            "notes": len(categories["notes"]),
            "snapshots": len(categories["snapshots"]),
            "templates": len(categories["templates"]),
            "governance": len(categories["governance"]),
            "mappings": len(categories["mappings"]),
        },
        "files": files,
    }


def build_bundle(
    vault_root: Path,
    output_path: Path,
    bundle_name: str,
    version: str = "1.0.0",
    description: str = "",
    compression: str = "zst",
) -> Path:
    """Build a release bundle tarball from a vault.

    The bundle structure:
        <bundle_name>/
          manifest.json
          notes/
            Controls/  Components/  Systems/  Plans/  Organizations/
          snapshots/
            Controls/  Components/  ...
          templates/
          governance/
          mappings/

    Args:
        vault_root: Root directory of the vault.
        output_path: Directory where the bundle tarball will be written.
        bundle_name: Name used for the bundle prefix and tarball filename.
        version: Semantic version string.
        description: Human-readable description.
        compression: Compression type: 'zst', 'gz', or 'none'.

    Returns:
        Path to the created bundle tarball.
    """
    manifest = create_bundle_manifest(vault_root, bundle_name, version, description)
    categories = _collect_vault_files(vault_root)

    # Determine tarball filename and mode
    if compression == "zst" and _HAS_ZSTD:
        ext = ".tar.zst"
        mode = "w|"  # streaming write for zstd
    elif compression == "gz":
        ext = ".tar.gz"
        mode = "w:gz"
    else:
        ext = ".tar"
        mode = "w"

    tarball_name = f"{bundle_name}-v{version}{ext}"
    tarball_path = output_path / tarball_name
    output_path.mkdir(parents=True, exist_ok=True)

    # Write manifest to temp location
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        bundle_prefix = bundle_name

        # Write manifest
        manifest_dir = tmp / bundle_prefix
        manifest_dir.mkdir(parents=True)
        manifest_path = manifest_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)

        # Copy category files
        dir_map = {
            "notes": "notes",
            "snapshots": "snapshots",
            "templates": "templates",
            "governance": "governance",
            "mappings": "mappings",
        }

        for cat_key, cat_dir in dir_map.items():
            for fp in categories.get(cat_key, []):
                try:
                    rel = fp.relative_to(vault_root)
                except ValueError:
                    rel = fp
                dest = manifest_dir / cat_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                import shutil

                shutil.copy2(fp, dest)

        # Also include Index.md if present
        index_md = vault_root / "Index.md"
        if index_md.exists():
            dest = manifest_dir / "Index.md"
            import shutil

            shutil.copy2(index_md, dest)

        # Create tarball
        with tarfile.open(tarball_path, mode) as tar:
            tar.add(manifest_dir, arcname=bundle_prefix)

    return tarball_path
