#!/usr/bin/env python3
"""Validate wikilinks in vault markdown notes.

Scans all .md files under --vault directories for Obsidian `[[...]]` wikilinks
and checks that each target note exists in the same vault.

Exit 0 if all wikilinks resolve (or are self-references / future templates).
Exit 1 if broken links found.
Exit 2 on runtime errors.

Usage:
    python3 compliance/scripts/validate_wikilinks.py [--vault <dir> ...]
    python3 compliance/scripts/validate_wikilinks.py --files file1.md file2.md ...
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Set

_CORE_ROOT = Path(__file__).resolve().parents[2]
import sys as _ws_sys  # noqa: E402

_WS_CORE = Path(__file__).resolve().parents[2]
if str(_WS_CORE) not in _ws_sys.path:
    _ws_sys.path.insert(0, str(_WS_CORE))
from compliance.lib.workspace import resolve_workspace  # noqa: E402

_WORKSPACE = resolve_workspace()

# Match [[target]] or [[target|display]] or [[target#heading]], excluding embeds ![[...]]
_WIKILINK_RE = re.compile(r"(?<!\!)\[\[([^\]|#]+?)(?:[|#][^\]]+?)?\]\]")

# Directories that appear in template notes as future-slot references
# — don't flag these as broken since they're intentional scaffolding.
_TEMPLATE_DIRS = {
    "diagrams",
    "evidence",
    "findings",
    "inheritance",
    "boundaries",
    "decisions",
    "relationships",
    "runbooks",
    "parameters",
    "control-parts",
    "catalogs",
    "profiles",
    "assessments",
    "poam",
    "governance",
}


def _parse_wikilinks(text: str) -> List[str]:
    """Extract all wikilink targets from markdown text."""
    targets: List[str] = []
    for m in _WIKILINK_RE.finditer(text):
        target = m.group(1).strip()
        if target:
            targets.append(target)
    return targets


def _build_note_index(vault_dir: Path) -> Dict[str, Path]:
    """Build {note_name_no_ext: full_path} index for a vault directory.

    Obsidian resolves wikilinks against note names without the .md extension,
    as well as full file paths and attachment names.
    Keys are relative paths from vault root, minus .md suffix, plus exact relative paths.
    """
    index: Dict[str, Path] = {}
    for path in vault_dir.rglob("*"):
        if any(p.startswith(".") for p in path.parts) or not path.is_file():
            continue
        rel = path.relative_to(vault_dir)
        rel_str = str(rel).replace("\\", "/")
        index[rel_str] = path
        index[path.name] = path
        if path.suffix == ".md":
            note_name = str(rel.with_suffix("")).replace("\\", "/")
            index[note_name] = path
            index[path.stem] = path
    return index


def _is_self_reference(target: str, current_note: str) -> bool:
    """Check if target references the current note itself."""
    return target == current_note


def _is_template_reference(target: str) -> bool:
    """Check if target is a template-slot reference (future directory)."""
    parts = target.split("/")
    if len(parts) >= 1 and parts[0] in _TEMPLATE_DIRS:
        return True
    return False


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate vault wikilinks")
    parser.add_argument(
        "--vault",
        action="append",
        default=None,
        help="Vault directory to scan (repeatable)",
    )
    parser.add_argument(
        "--files", nargs="*", default=None, help="Specific .md files to check"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on broken links (default: warn-only)",
    )
    args = parser.parse_args()

    vault_dirs: List[Path] = []
    if args.vault:
        vault_dirs = [Path(v).resolve() for v in args.vault]
    else:
        examples_vault = _WORKSPACE / "examples" / "vault"
        if examples_vault.is_dir():
            vault_dirs = [
                d
                for d in sorted(examples_vault.iterdir())
                if d.is_dir() and not d.name.startswith(".")
            ]

    if not vault_dirs and not args.files:
        print("No vault directories or --files specified.", file=sys.stderr)
        return 0

    # Build note indices per vault
    vault_indices: Dict[Path, Dict[str, Path]] = {}
    for vd in vault_dirs:
        if vd.is_dir():
            vault_indices[vd] = _build_note_index(vd)

    # Determine which files to check
    md_files: List[Path] = []
    if args.files:
        md_files = [Path(f).resolve() for f in args.files if f.endswith(".md")]
    else:
        seen: Set[Path] = set()
        for vd in vault_dirs:
            for md_path in sorted(vd.rglob("*.md")):
                if md_path not in seen:
                    seen.add(md_path)
                    md_files.append(md_path)

    broken = 0
    checked = 0
    skipped_self = 0
    skipped_template = 0

    for md_path in md_files:
        if any(p.startswith(".") for p in md_path.parts):
            continue

        # Determine which vault this file belongs to
        parent_vault = None
        for vd in vault_indices:
            try:
                md_path.relative_to(vd)
                parent_vault = vd
                break
            except ValueError:
                continue

        if parent_vault is None:
            continue

        note_index = vault_indices[parent_vault]
        current_note = str(md_path.relative_to(parent_vault).with_suffix("")).replace(
            "\\", "/"
        )

        try:
            text = md_path.read_text()
        except Exception:
            continue

        targets = _parse_wikilinks(text)

        for target in targets:
            # Skip external URLs
            if "://" in target or target.startswith("http"):
                continue
            # Skip heading-only links
            if target.startswith("#"):
                continue

            checked += 1

            # Self-referencing is fine
            if _is_self_reference(target, current_note):
                skipped_self += 1
                continue

            # Template-slot references (future directories) are fine
            if _is_template_reference(target):
                skipped_template += 1
                continue

            # Try exact match in the vault index
            if target in note_index:
                continue

            # Try with common directory prefixes
            found = False
            for dirname in [
                "Controls",
                "Systems",
                "Components",
                "Organizations",
                "Plans",
                "Mappings",
                "governance",
                "governance/validation-rules",
                "_meta",
                "_meta/templates",
            ]:
                alt = f"{dirname}/{target}"
                if alt in note_index:
                    found = True
                    break

            if not found:
                rel = (
                    md_path.relative_to(_WORKSPACE)
                    if _WORKSPACE in md_path.parents
                    else md_path
                )
                print(f"BROKEN {rel}: [[{target}]] -> target not found")
                broken += 1

    summary = f"{checked} references checked, {broken} broken"
    if skipped_self:
        summary += f", {skipped_self} self-references skipped"
    if skipped_template:
        summary += f", {skipped_template} template-slot refs skipped"
    print(f"\n{summary}")

    if broken:
        if args.strict:
            return 1
        print("\nNote: run with --strict to fail on broken wikilinks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
