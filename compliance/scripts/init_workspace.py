#!/usr/bin/env python3
"""Set up a system repository that vendors this core.

Run it from the system repository (or pass ``--workspace``):

    python <core>/compliance/scripts/init_workspace.py            # install defaults
    python <core>/compliance/scripts/init_workspace.py --dry-run  # show what would change
    python <core>/compliance/scripts/init_workspace.py --check    # fail if not installed

It creates the default layout (``examples/vault/`` and ``scripts/``) and maintains one marked block
in the workspace ``.gitignore`` covering extraction intermediates and source documents, so they are
not committed by accident. Re-running rewrites only that block. ``examples/vault/`` stays tracked
by default (it is the output of record in repositories such as oscal-vault); pass
``--ignore-vault`` for a repository that must not commit source-derived vault content.

A .gitignore does not stop ``git add -f`` or help with files that are already tracked, so also run
``check_system_content.py`` (see ADR-028). Refuses to run inside a vendored core's own tree.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from compliance.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_arguments,
    guard_output,
    resolve_workspace,
)

BEGIN = "# >>> oscal-vault-core workspace (managed block; edits inside are overwritten)"
END = "# <<< oscal-vault-core workspace"

BASE_RULES = (
    "# Source documents and extraction intermediates: never commit these.",
    "*.docx",
    "*.doc",
    "*.ods",
    ".exports/",
    ".snapshots/history/",
    "review-output/",
    "roundtrip_review/",
    "examples/docx_catchall/",
    "examples/feedback/",
    "# Obsidian per-user state",
    ".obsidian/",
)
VAULT_RULES = (
    "# --ignore-vault: this repository must not commit source-derived vault output.",
    "examples/vault/",
)
DIRECTORIES = ("examples/vault", "scripts")


def render_block(ignore_vault: bool) -> str:
    rules = list(BASE_RULES) + (list(VAULT_RULES) if ignore_vault else [])
    return "\n".join([BEGIN, *rules, END]) + "\n"


def merge_block(existing: str, block: str) -> str:
    """Replace the managed block if present, otherwise append it; touch nothing else."""
    if BEGIN in existing and END in existing:
        start = existing.index(BEGIN)
        stop = existing.index(END, start) + len(END)
        tail = existing[stop:].lstrip("\n")
        head = existing[:start]
        return head + block + (("\n" + tail) if tail else "")
    sep = (
        ""
        if not existing or existing.endswith("\n\n")
        else ("\n" if existing.endswith("\n") else "\n\n")
    )
    return existing + sep + block


def plan(workspace: Path, ignore_vault: bool) -> tuple[list[Path], str, str]:
    missing = [workspace / d for d in DIRECTORIES if not (workspace / d).is_dir()]
    gitignore = workspace / ".gitignore"
    before = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    return missing, before, merge_block(before, render_block(ignore_vault))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_workspace_arguments(parser)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true", help="exit 1 if not installed")
    parser.add_argument("--ignore-vault", action="store_true")
    args = parser.parse_args(argv)

    workspace = resolve_workspace(args.workspace)
    try:
        guard_output(workspace, allow_in_core=args.allow_write_in_core)
    except WorkspaceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    missing, before, after = plan(workspace, args.ignore_vault)
    current = before == after and not missing
    if args.check:
        print("installed" if current else "not installed (or out of date)")
        return 0 if current else 1

    for directory in missing:
        print(f"  create  {directory.relative_to(workspace)}/")
    if before != after:
        print("  update  .gitignore (managed block)")
    if current:
        print("already installed; nothing to do")
        return 0
    if args.dry_run:
        print("dry run: no changes written")
        return 0
    for directory in missing:
        directory.mkdir(parents=True, exist_ok=True)
    (workspace / ".gitignore").write_text(after, encoding="utf-8")
    print(f"installed in {workspace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
