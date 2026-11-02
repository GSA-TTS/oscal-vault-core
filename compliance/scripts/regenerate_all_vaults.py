#!/usr/bin/env python3
"""Batch-regenerate a system repository's Obsidian vaults from their source SSP .docx files.

Run from the *system repository* (the working directory), not from the core:

    uv run python <core>/compliance/scripts/regenerate_all_vaults.py [--force] [--registry PATH]

Inputs, all relative to the working directory unless overridden:
  - ``scripts/registry.yaml``  per-system metadata (``plan_id``, ``system_name``, ``fips_199``,
                               ``docx`` and optional owners/components)
  - the ``docx`` path named by each registry entry
Output goes to ``examples/vault/<plan_id>/``. The .docx sources are intentionally not committed;
systems whose source is absent are skipped, so this runs cleanly on a bare clone.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


import yaml

import sys as _ws_sys  # noqa: E402

_WS_CORE = Path(__file__).resolve().parents[2]
if str(_WS_CORE) not in _ws_sys.path:
    _ws_sys.path.insert(0, str(_WS_CORE))
from compliance.lib.workspace import resolve_workspace  # noqa: E402

CORE_ROOT = Path(__file__).resolve().parents[2]
EXPORTER = CORE_ROOT / "compliance" / "scripts" / "export_obsidian_vault.py"


def load_systems(registry_path: Path) -> list[dict]:
    """Registry entries that name a source document."""
    with open(registry_path, encoding="utf-8") as f:
        registry = yaml.safe_load(f) or {}
    return [s for s in registry.get("systems", []) if s.get("docx")]


def plan(
    system: dict, workspace: Path, *, force: bool
) -> tuple[str, Path, Path, str | None]:
    """``(plan_id, docx, vault_dir, skip_reason)`` for one registry entry."""
    plan_id = system["plan_id"]
    docx = workspace / system["docx"]
    vault_dir = workspace / "examples" / "vault" / plan_id
    if not docx.exists():
        return plan_id, docx, vault_dir, f"docx not found at {docx}"
    if vault_dir.exists() and not force:
        return (
            plan_id,
            docx,
            vault_dir,
            "vault already exists (use --force to overwrite)",
        )
    return plan_id, docx, vault_dir, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing vaults."
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=resolve_workspace() / "scripts" / "registry.yaml",
        help="Registry YAML (default: ./scripts/registry.yaml).",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=resolve_workspace(),
        help="Directory holding examples/vault/ and the source documents (default: cwd).",
    )
    args = parser.parse_args(argv)

    if not args.registry.is_file():
        print(f"ERROR: registry not found: {args.registry}", file=sys.stderr)
        return 2

    success = fail = skipped = 0
    for system in load_systems(args.registry):
        plan_id, docx, vault_dir, skip = plan(system, args.workspace, force=args.force)
        if skip:
            print(f"[SKIP] {plan_id}: {skip}")
            skipped += 1
            continue

        print(
            f"[GEN]  {plan_id}: {system['system_name']} ({system['fips_199']}) from {docx.name}"
        )
        cmd = [
            sys.executable,
            str(EXPORTER),
            "--docx",
            str(docx),
            "--plan-id",
            plan_id,
            "--system-name",
            system["system_name"],
            "--fips-199",
            system["fips_199"],
            "--metadata-yaml",
            str(args.registry),
            "--vault",
            str(vault_dir),
        ]
        result = subprocess.run(
            cmd,
            cwd=str(args.workspace),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            notes = len(list(vault_dir.rglob("*.md"))) if vault_dir.exists() else 0
            print(f"  OK  -> {notes} notes written")
            success += 1
        else:
            print(f"  FAIL: {result.stderr.strip()[:200]}")
            fail += 1

    print(f"\nDone: {success} succeeded, {fail} failed, {skipped} skipped")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
