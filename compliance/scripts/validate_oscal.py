#!/usr/bin/env python3
"""Layer 1 OSCAL conformance gate (ADR-019, phase R17).

Validate OSCAL JSON documents against the vendored NIST OSCAL JSON Schema.
Fast, offline, no Docker/DB. This is the *primary* conformance gate; the
optional CCF relational round-trip oracle is Layer 2.

Usage:
    # Validate specific exported files
    python3 compliance/scripts/validate_oscal.py path/to/ssp.json ...

    # Validate every export under all vaults' .exports/oscal-json/
    python3 compliance/scripts/validate_oscal.py --all-exports

    # Validate exports under a specific vault (repeatable)
    python3 compliance/scripts/validate_oscal.py --vault examples/vault/gsa-pages-ssp-2026/

Exit status is non-zero if any document fails validation (fail-closed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.validate_oscal import (  # noqa: E402
    DEFAULT_OSCAL_VERSION,
    SchemaNotAvailableError,
    StageSummary,
    ValidationResult,
    summarize_canonical_ssp,
    summarize_oscal_ssp,
    validate_oscal_file,
    verify_oscal_parity,
)


def _collect_export_files(vault_dir: Path) -> List[Path]:
    exports = vault_dir / ".exports" / "oscal-json"
    if not exports.is_dir():
        return []
    return sorted(exports.rglob("*.json"))


def _vault_dirs(args: argparse.Namespace) -> List[Path]:
    if args.vault:
        return [Path(v) for v in args.vault]
    if args.all_exports:
        examples_vault = Path.cwd() / "examples" / "vault"
        if examples_vault.is_dir():
            return [
                d
                for d in sorted(examples_vault.iterdir())
                if d.is_dir() and not d.name.startswith(".")
            ]
    return []


def _iter_target_files(args: argparse.Namespace) -> List[Path]:
    files: List[Path] = [Path(f) for f in args.files]
    for vd in _vault_dirs(args):
        files.extend(_collect_export_files(vd))
    return files


def _vault_allows_synthetic(path: Path) -> bool:
    """Resolve a committed per-vault synthetic-placeholder exception.

    Synthetic placeholders are rejected by default. A vault whose source
    document legitimately contains no controls declares the exception in
    ``<vault>/.export-policy.json`` with a mandatory rationale, so the
    exception is reviewable in git rather than applied process-wide.
    """
    for parent in path.resolve().parents:
        policy_path = parent / ".export-policy.json"
        if policy_path.is_file():
            try:
                policy = json.loads(policy_path.read_text(encoding="utf-8"))
            except Exception:
                return False
            if not isinstance(policy, dict):
                return False
            return bool(policy.get("allow_synthetic")) and bool(
                str(policy.get("rationale", "")).strip()
            )
        if (parent / ".git").exists():
            break
    return False


def _load_canonical_stage(vault_dir: Path) -> StageSummary | None:
    """Summarize a vault's canonical sidecars for parity comparison."""
    current = vault_dir / ".snapshots" / "current"
    if not current.is_dir():
        return None
    plan: dict | None = None
    system: dict | None = None
    controls: List[dict] = []
    parties: List[dict] = []
    extensions: List[dict] = []
    for sidecar_path in sorted(current.rglob("*.json")):
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        note_type = data.get("type") or data.get("note_type", "")
        if note_type == "oscal.ssp" and data.get("note_type") != "system":
            plan = plan or data
        elif note_type == "oscal.ssp" and data.get("note_type") == "system":
            system = system or data
        elif note_type == "oscal.control":
            controls.append(data)
        elif note_type == "oscal.party":
            parties.append(data)
        elif isinstance(note_type, str) and note_type.startswith("oscal.x."):
            extensions.append(data)
    if plan is None:
        return None
    return summarize_canonical_ssp(
        plan,
        controls,
        parties,
        system_data=system,
        extension_sidecars=extensions,
    )


def _check_parity(vault_dir: Path) -> List[str]:
    """Compare a vault's canonical stage against its exported SSP."""
    canonical = _load_canonical_stage(vault_dir)
    if canonical is None:
        return []
    errors: List[str] = []
    allow_synthetic = _vault_allows_synthetic(vault_dir / ".export-policy.json")
    ssp_paths = [
        path
        for path in _collect_export_files(vault_dir)
        if path.name.endswith("-ssp.json")
    ]
    if not ssp_paths:
        return [f"parity: {vault_dir.name} has canonical controls but no exported SSP"]
    for path in ssp_paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"parity: cannot read {path}: {exc}")
            continue
        exported = summarize_oscal_ssp(document)
        for message in verify_oscal_parity(
            canonical, exported, allow_synthetic=allow_synthetic
        ):
            errors.append(f"{path}: {message}")
    return errors


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate OSCAL JSON against vendored NIST schemas (Layer 1)."
    )
    parser.add_argument("files", nargs="*", help="OSCAL .json files to validate")
    parser.add_argument(
        "--vault",
        action="append",
        default=None,
        help="Validate all exports under this vault's .exports/oscal-json/ (repeatable)",
    )
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="Validate UUID uniqueness and component/party/role/control/statement/"
        "parameter reference resolution after schema validation.",
    )
    parser.add_argument(
        "--parity",
        action="store_true",
        help="Compare each vault's canonical sidecars against its exported SSP and "
        "fail on any control/statement/parameter/party difference.",
    )
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Permit documented synthetic placeholder objects. Off by default; "
        "prefer a committed per-vault <vault>/.export-policy.json.",
    )
    parser.add_argument(
        "--all-exports",
        action="store_true",
        help="Validate exports under every vault in examples/vault/",
    )
    parser.add_argument(
        "--oscal-version",
        default=DEFAULT_OSCAL_VERSION,
        help=f"NIST schema version to validate against (default: {DEFAULT_OSCAL_VERSION})",
    )
    args = parser.parse_args(argv)

    targets = _iter_target_files(args)
    if not targets:
        print("No OSCAL documents to validate.", file=sys.stderr)
        return 0

    failures = 0
    for path in targets:
        allow_synthetic = args.allow_synthetic or _vault_allows_synthetic(path)
        try:
            result: ValidationResult = validate_oscal_file(
                path,
                oscal_version=args.oscal_version,
                semantic=args.semantic,
                allow_synthetic=allow_synthetic,
            )
        except SchemaNotAvailableError as e:
            print(f"SCHEMA-MISSING  {path}: {e}", file=sys.stderr)
            failures += 1
            continue
        except ValueError as e:
            print(f"UNRECOGNIZED    {path}: {e}", file=sys.stderr)
            failures += 1
            continue

        if result.valid:
            print(f"OK   [{result.document_type}] {path}")
        else:
            failures += 1
            print(f"FAIL [{result.document_type}] {path}")
            for err in result.errors:
                print(f"       - {err}")

    total = len(targets)
    print(f"\n{total - failures}/{total} OSCAL documents valid.", file=sys.stderr)

    if args.parity:
        parity_failures = 0
        for vault_dir in _vault_dirs(args):
            messages = _check_parity(vault_dir)
            if messages:
                parity_failures += 1
                print(f"PARITY-FAIL {vault_dir.name}")
                for message in messages:
                    print(f"       - {message}")
            else:
                print(f"PARITY-OK   {vault_dir.name}")
        if parity_failures:
            print(
                f"\n{parity_failures} vault(s) failed canonical/OSCAL parity.",
                file=sys.stderr,
            )
            failures += parity_failures

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
