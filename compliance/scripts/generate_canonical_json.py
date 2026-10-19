#!/usr/bin/env python3
"""Generate canonical JSON sidecar files for vault markdown notes.

Reads markdown notes in a vault, parses their frontmatter, sections, and inline
fields, and generates deterministic JSON sidecars under `.snapshots/current/`.

Usage:
    python3 compliance/scripts/generate_canonical_json.py --vault <vault_dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.canonical_json import generate_canonical_data, parse_frontmatter  # noqa: E402


def find_vault_notes(vault_dir: Path) -> List[Path]:
    """Find all candidate markdown notes in the vault."""
    notes: List[Path] = []
    for path in vault_dir.rglob("*.md"):
        # Skip hidden paths, templates, or existing snapshot directories
        parts = path.relative_to(vault_dir).parts
        if any(p.startswith(".") for p in parts):
            continue
        if "_meta/templates/" in str(path.relative_to(vault_dir)):
            continue
        notes.append(path)
    return sorted(notes)


def clean_orphaned_snapshots(vault_dir: Path, active_rel_paths: List[Path]) -> int:
    """Remove sidecar JSON files under .snapshots/current/ that don't have matching markdown files."""
    snapshot_root = vault_dir / ".snapshots" / "current"
    if not snapshot_root.is_dir():
        return 0

    active_json_rel_paths = {p.with_suffix(".json") for p in active_rel_paths}
    removed = 0

    for path in snapshot_root.rglob("*.json"):
        rel_json_path = path.relative_to(snapshot_root)
        if rel_json_path not in active_json_rel_paths:
            path.unlink()
            removed += 1

    # Clean empty directories in .snapshots/current/
    for path in sorted(
        snapshot_root.rglob("*"), key=lambda p: len(p.parts), reverse=True
    ):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()

    return removed


def _find_orphaned_snapshots(
    vault_dir: Path, active_rel_paths: List[Path]
) -> List[str]:
    """Return committed sidecars with no matching markdown note (read-only)."""
    snapshot_root = vault_dir / ".snapshots" / "current"
    if not snapshot_root.is_dir():
        return []
    active = {p.with_suffix(".json") for p in active_rel_paths}
    orphans: List[str] = []
    for path in sorted(snapshot_root.rglob("*.json")):
        rel = path.relative_to(snapshot_root)
        if rel not in active:
            orphans.append(str(rel))
    return orphans


def _load_committed_sidecar(path: Path) -> Optional[dict]:
    """Return a readable committed sidecar, or ``None`` when unavailable."""
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _preserve_unchanged_generated_at(
    canonical_data: dict, committed: Optional[dict]
) -> bool:
    """Preserve provenance time when regeneration made no semantic change."""
    if committed is None:
        return False
    fresh_meta = canonical_data.get("_meta", {})
    committed_meta = committed.get("_meta", {})
    if fresh_meta.get("content_hash") != committed_meta.get("content_hash"):
        return False
    generated_at = committed_meta.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        return False
    fresh_meta["generated_at"] = generated_at
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate canonical JSON sidecars for Obsidian vault notes"
    )
    parser.add_argument(
        "--vault",
        action="append",
        default=None,
        help="Path to the Obsidian vault root directory (repeatable; default: all subdirs of examples/vault/)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        default=True,
        help="Remove orphaned JSON sidecars from .snapshots/current/ (default: True)",
    )
    parser.add_argument(
        "--no-clean",
        action="store_false",
        dest="clean",
        help="Do not remove orphaned JSON sidecars",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; instead fail (exit 2) if any committed sidecar's "
        "content_hash differs from freshly-generated content, or a sidecar is "
        "missing/orphaned. This is the drift gate: it proves the committed "
        "canonical snapshots match the current markdown + generator.",
    )
    parser.add_argument(
        "--note",
        action="append",
        default=None,
        help="Only process this Markdown path relative to each selected vault "
        "(repeatable). Targeted runs never clean unrelated sidecars.",
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
        examples_vault = _REPO_ROOT / "examples" / "vault"
        if examples_vault.is_dir():
            for entry in sorted(examples_vault.iterdir()):
                if entry.is_dir() and not entry.name.startswith("."):
                    vault_dirs.append(entry.resolve())

    if not vault_dirs:
        print("No vault directories to process.", file=sys.stderr)
        return 0

    total_errors = 0
    total_drift = 0

    for vault_dir in vault_dirs:
        if args.note:
            notes = []
            for raw_note in args.note:
                relative_note = Path(raw_note)
                if relative_note.is_absolute() or ".." in relative_note.parts:
                    print(
                        f"ERROR: --note must be a vault-relative path: {raw_note}",
                        file=sys.stderr,
                    )
                    total_errors += 1
                    continue
                note_path = vault_dir / relative_note
                if note_path.suffix.lower() != ".md" or not note_path.is_file():
                    print(
                        f"ERROR: note {relative_note} does not exist in vault "
                        f"{vault_dir.name}",
                        file=sys.stderr,
                    )
                    total_errors += 1
                    continue
                notes.append(note_path)
        else:
            notes = find_vault_notes(vault_dir)
        generated = 0
        skipped = 0
        errors = 0
        drift: List[str] = []

        active_rel_paths: List[Path] = []

        for note_path in notes:
            rel_path = note_path.relative_to(vault_dir)

            # Quick validation: check if the note actually has type/note_type
            try:
                frontmatter, _ = parse_frontmatter(note_path)
            except Exception:
                # Skip files that aren't valid markdown notes with frontmatter
                skipped += 1
                continue

            if not (frontmatter.get("type") or frontmatter.get("note_type")):
                # Note has frontmatter but isn't a recognized typed note
                skipped += 1
                continue

            active_rel_paths.append(rel_path)

            # Generate the sidecar path
            sidecar_path = (
                vault_dir / ".snapshots" / "current" / rel_path.with_suffix(".json")
            )

            try:
                # Create sidecar data
                canonical_data = generate_canonical_data(note_path, vault_dir)
                committed = _load_committed_sidecar(sidecar_path)

                if args.check:
                    # Drift gate: compare freshly-computed content_hash against
                    # the committed sidecar instead of writing. The hash covers
                    # frontmatter + raw_body + body_sections + inline_fields, so
                    # any content change is detected.
                    fresh_hash = canonical_data["_meta"]["content_hash"]
                    if not sidecar_path.is_file():
                        drift.append(f"missing committed sidecar: {rel_path}")
                    elif committed is None:
                        drift.append(f"unreadable sidecar: {rel_path}")
                    else:
                        committed_hash = committed.get("_meta", {}).get("content_hash")
                        if committed_hash is not None and committed_hash != fresh_hash:
                            drift.append(
                                f"content drift: {rel_path} "
                                f"(committed {committed_hash[:19]}... != "
                                f"fresh {fresh_hash[:19]}...)"
                            )
                    generated += 1
                    continue

                # Ensure parent directories exist
                sidecar_path.parent.mkdir(parents=True, exist_ok=True)

                _preserve_unchanged_generated_at(canonical_data, committed)

                # Write sidecar with sorted keys and pretty formatting
                with open(sidecar_path, "w", encoding="utf-8") as f:
                    json.dump(canonical_data, f, indent=2, sort_keys=True)

                generated += 1
            except Exception as e:
                print(
                    f"ERROR: failed to generate sidecar for {rel_path} in vault {vault_dir.name}: {e}",
                    file=sys.stderr,
                )
                errors += 1

        cleaned = 0
        if args.check:
            # In check mode, an orphaned committed sidecar is also drift.
            orphans = _find_orphaned_snapshots(vault_dir, active_rel_paths)
            for orphan in orphans:
                drift.append(f"orphaned committed sidecar: {orphan}")
        elif args.clean and not args.note:
            cleaned = clean_orphaned_snapshots(vault_dir, active_rel_paths)

        if args.check:
            if drift:
                print(
                    f"DRIFT vault {vault_dir.name}: {len(drift)} issue(s)",
                    file=sys.stderr,
                )
                for item in drift[:20]:
                    print(f"  - {item}", file=sys.stderr)
            else:
                print(f"OK vault {vault_dir.name}: {generated} sidecars in sync")
            total_drift += len(drift)
        else:
            print(
                f"Processed vault {vault_dir.name}: "
                f"{generated} sidecars generated, {skipped} files skipped, "
                f"{cleaned} orphans cleaned, {errors} errors."
            )
        total_errors += errors

    if args.check:
        if total_drift:
            print(
                f"\nFAIL: {total_drift} canonical sidecar drift issue(s). "
                "Run generate_canonical_json.py (without --check) and commit.",
                file=sys.stderr,
            )
            return 2
        print("\nOK: all committed canonical sidecars are in sync.")
        return 0

    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
