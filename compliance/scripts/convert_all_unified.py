#!/usr/bin/env python3
"""Run every source document named in a system registry through the UnifiedConverter pipeline.

Run from the system repository:
    uv run python3 <core>/compliance/scripts/convert_all_unified.py [--backend BACKEND] [--force]
        [--registry scripts/registry.yaml] [--contracts-dir compliance/datacontracts]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_CORE_ROOT = Path(__file__).resolve().parents[2]
import sys as _ws_sys  # noqa: E402

_WS_CORE = Path(__file__).resolve().parents[2]
if str(_WS_CORE) not in _ws_sys.path:
    _ws_sys.path.insert(0, str(_WS_CORE))
from compliance.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_arguments,
    guard_output,
    resolve_workspace,
)

_WORKSPACE = resolve_workspace()
if str(_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT))

from compliance.lib.unified_converter import UnifiedConverter  # noqa: E402


def load_systems(registry: Path) -> list[dict]:
    """Registry entries that name a source document (``plan_id``, ``docx``, ``system_name``)."""
    import yaml

    data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    return [s for s in data.get("systems", []) if s.get("docx") and s.get("plan_id")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Batch-convert every registry system with the unified pipeline."
    )
    parser.add_argument(
        "--backend",
        type=str,
        default="fismatic",  # default to fismatic for speed/compatibility, but can override
        help="Backend to use (fismatic, docling, mineru, unstructured, mammoth).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite existing vaults.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_WORKSPACE / "examples" / "vault",
        help="Root directory where vaults should be written.",
    )

    parser.add_argument(
        "--registry",
        type=Path,
        default=_WORKSPACE / "scripts" / "registry.yaml",
        help="System registry naming each plan_id and its source .docx (default: ./scripts/registry.yaml).",
    )
    parser.add_argument(
        "--contracts-dir",
        type=Path,
        default=_WORKSPACE / "compliance" / "datacontracts",
        help="Per-system extraction contracts <plan_id>.yaml (default: ./compliance/datacontracts).",
    )

    add_workspace_arguments(parser)
    args = parser.parse_args(argv)
    try:
        args.output_dir = guard_output(
            args.output_dir, allow_in_core=args.allow_write_in_core
        )
    except WorkspaceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not args.registry.is_file():
        print(f"ERROR: registry not found: {args.registry}", file=sys.stderr)
        return 2
    systems = load_systems(args.registry)
    backend = args.backend
    force = args.force
    output_dir = args.output_dir

    print(f"Batch conversion using UnifiedConverter with backend={backend!r}")
    print(f"Output directory: {output_dir}\n")

    success_count = 0
    fail_count = 0
    skipped_count = 0

    total_start = time.perf_counter()

    for sys_info in systems:
        plan_id = sys_info["plan_id"]
        docx_rel = sys_info["docx"]
        docx_path = _WORKSPACE / docx_rel
        contract_path = args.contracts_dir / f"{plan_id}.yaml"
        vault_dir = output_dir / plan_id
        snapshots_dir = vault_dir / ".snapshots" / "current"

        if not docx_path.exists():
            print(f"[SKIP] {plan_id}: docx not found at {docx_path}")
            skipped_count += 1
            continue

        if not contract_path.exists():
            print(f"[SKIP] {plan_id}: contract not found at {contract_path}")
            skipped_count += 1
            continue

        if vault_dir.exists() and not force:
            print(f"[SKIP] {plan_id}: vault already exists (use --force to overwrite)")
            skipped_count += 1
            continue

        print(f"[CONV] {plan_id} ({sys_info.get('system_name', plan_id)}) ...")
        t0 = time.perf_counter()

        try:
            converter = UnifiedConverter(
                contract_path=contract_path,
                backend=backend,
            )
            result = converter.convert(
                docx_path=docx_path,
                vault_dir=vault_dir,
                snapshots_dir=snapshots_dir,
            )
            elapsed = time.perf_counter() - t0

            if result.errors:
                print(f"  FAIL in {elapsed:.2f}s with {len(result.errors)} errors:")
                for err in result.errors:
                    print(f"    - {err}")
                fail_count += 1
            else:
                # Count files generated
                md_files = list(vault_dir.rglob("*.md"))
                json_files = list(snapshots_dir.rglob("*.json"))
                print(
                    f"  OK   in {elapsed:.2f}s | {len(result.entities)} entities, {len(md_files)} md notes, {len(json_files)} sidecars"
                )
                if result.warnings:
                    print(f"    Warnings ({len(result.warnings)}):")
                    # Limit output to first 10 warnings
                    for warn in result.warnings[:10]:
                        print(f"      - {warn}")
                    if len(result.warnings) > 10:
                        print(
                            f"      - ... and {len(result.warnings) - 10} more warnings"
                        )
                success_count += 1
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"  CRASH in {elapsed:.2f}s: {e}")
            import traceback

            traceback.print_exc()
            fail_count += 1

    total_elapsed = time.perf_counter() - total_start
    print(f"\nBatch Conversion complete in {total_elapsed:.2f}s:")
    print(f"  Succeeded: {success_count}")
    print(f"  Failed:    {fail_count}")
    print(f"  Skipped:   {skipped_count}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
