#!/usr/bin/env python3
"""Layer 1.5 OSCAL cross-check via the go-oscal ``validate`` CLI (ADR-019, R17).

This is an *optional, stateless* second-implementation cross-check. It runs the
`defenseunicorns/go-oscal` `validate` command — the same OSCAL binding the
compliance-framework API (CCF) uses — against our exported OSCAL documents.

Why this is not Layer 2: `go-oscal validate` is a pure JSON-Schema check from a
different codebase, so it corroborates our Python `jsonschema` gate (Layer 1)
using an independent implementation. It does NOT exercise CCF's relational
Unmarshal/Marshal round-trip, so it cannot detect the relational-store data
loss that the true Layer 2 (stateful CCF, #14b) is designed to catch.

The go-oscal binary is NOT vendored. This script:
  - locates it via ``--go-oscal <path>`` or the ``GO_OSCAL`` env var or ``PATH``;
  - SKIPS cleanly (exit 0) when the binary is absent, so it never blocks a
    developer or CI job that has not installed it — matching how the .docx
    fixtures skip gracefully.

Usage:
    GO_OSCAL=/path/to/go-oscal \\
      python3 compliance/scripts/crosscheck_go_oscal.py --all-exports

    python3 compliance/scripts/crosscheck_go_oscal.py --go-oscal ./go-oscal file.json
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from typing import List, Optional

import sys as _ws_sys  # noqa: E402

_WS_CORE = Path(__file__).resolve().parents[2]
if str(_WS_CORE) not in _ws_sys.path:
    _ws_sys.path.insert(0, str(_WS_CORE))
from compliance.lib.workspace import resolve_workspace  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_go_oscal(explicit: Optional[str]) -> Optional[str]:
    """Resolve the go-oscal binary from --go-oscal, $GO_OSCAL, or PATH."""
    for candidate in (explicit, os.environ.get("GO_OSCAL")):
        if candidate and Path(candidate).is_file():
            return candidate
    return shutil.which("go-oscal")


def _collect_export_files(vault_dir: Path) -> List[Path]:
    exports = vault_dir / ".exports" / "oscal-json"
    if not exports.is_dir():
        return []
    return sorted(exports.rglob("*.json"))


def _iter_target_files(args: argparse.Namespace) -> List[Path]:
    files: List[Path] = [Path(f) for f in args.files]
    vault_dirs: List[Path] = []
    if args.vault:
        vault_dirs = [Path(v) for v in args.vault]
    elif args.all_exports:
        examples_vault = resolve_workspace() / "examples" / "vault"
        if examples_vault.is_dir():
            vault_dirs = [
                d
                for d in sorted(examples_vault.iterdir())
                if d.is_dir() and not d.name.startswith(".")
            ]
    for vd in vault_dirs:
        files.extend(_collect_export_files(vd))
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-check OSCAL exports with the go-oscal validate CLI "
        "(optional, stateless second-implementation check)."
    )
    parser.add_argument("files", nargs="*", help="OSCAL .json files to check")
    parser.add_argument(
        "--vault", action="append", default=None, help="Vault root (repeatable)"
    )
    parser.add_argument(
        "--all-exports",
        action="store_true",
        help="Check exports under every vault in examples/vault/",
    )
    parser.add_argument(
        "--go-oscal",
        default=None,
        help="Path to the go-oscal binary (else $GO_OSCAL or PATH).",
    )
    parser.add_argument(
        "--require",
        action="store_true",
        help="Fail (exit 2) if the go-oscal binary is not found, instead of "
        "skipping. Use in a CI lane that has installed it.",
    )
    args = parser.parse_args()

    binary = _find_go_oscal(args.go_oscal)
    if not binary:
        msg = "go-oscal binary not found (checked --go-oscal, $GO_OSCAL, PATH)."
        if args.require:
            print(f"ERROR: {msg}", file=sys.stderr)
            return 2
        print(f"SKIP: {msg} Cross-check skipped.", file=sys.stderr)
        return 0

    targets = _iter_target_files(args)
    if not targets:
        print("No OSCAL documents to cross-check.", file=sys.stderr)
        return 0

    failures = 0
    for path in targets:
        proc = subprocess.run(
            [binary, "validate", "-f", str(path)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            print(f"OK   {path}")
        else:
            failures += 1
            print(f"FAIL {path}", file=sys.stderr)
            # go-oscal writes diagnostics to stderr
            for line in (proc.stderr or proc.stdout).splitlines()[:10]:
                print(f"       {line}", file=sys.stderr)

    total = len(targets)
    print(
        f"\ngo-oscal cross-check: {total - failures}/{total} documents valid.",
        file=sys.stderr,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
