#!/usr/bin/env python3
"""Validate YAML frontmatter in vault markdown notes against the schema.

Reads every .md file under one or more --vault directories (or the current
directory tree) and checks that each note's YAML frontmatter conforms to the
type definitions in `vault-schemas.json`.

Schema validation is per-type: each note's `type` field selects the matching
`$defs/*` schema. Notes without a recognized `type` emit a warning but do not
fail validation (extension types and hand-authored notes may have novel types).

Exit 0 if all validatable notes pass schema checks. Exit 1 if any schema
violation is found. Exit 2 on runtime errors.

Usage:
    python3 scripts/validate_frontmatter.py [--vault <dir> ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from jsonschema import Draft202012Validator

_CORE_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE = Path.cwd()
_SCHEMA_PATH = _CORE_ROOT / "vault-schemas.json"

# Map vault `type` values to `$defs` schema names.
# Canonical types
_TYPE_TO_SCHEMA: Dict[str, str] = {
    "oscal.control": "VaultControlFrontmatterR1",
    "oscal.component_definition": "VaultComponentFrontmatter",
    "oscal.ssp": "VaultPlanFrontmatter",  # disambiguated by note_type below
    "oscal.party": "VaultOrganizationFrontmatter",
    # Extension types
    "oscal.x.diagram": "VaultExtensionDiagramFrontmatter",
    "oscal.x.evidence": "VaultExtensionEvidenceFrontmatter",
    "oscal.x.finding": "VaultExtensionNarrativeFrontmatter",
    "oscal.x.inheritance": "VaultExtensionNarrativeFrontmatter",
    "oscal.x.boundary": "VaultExtensionNarrativeFrontmatter",
    "oscal.x.decision": "VaultExtensionNarrativeFrontmatter",
    "oscal.x.relationship": "VaultExtensionNarrativeFrontmatter",
    "oscal.x.runbook": "VaultExtensionNarrativeFrontmatter",
    "oscal.x.crm": "VaultExtensionNarrativeFrontmatter",
    "oscal.x.privacy": "VaultExtensionNarrativeFrontmatter",
    # OKF types
    "okf.schema": "VaultOkfSchemaFrontmatter",
    "okf.index": "VaultOkfIndexFrontmatter",
    "okf.validation_rule": "VaultOkfValidationRuleFrontmatter",
    "okf.mapping": "VaultMappingFrontmatter",
    "okf.instruction": "VaultInstructionFrontmatter",
    "okf.table_row": "VaultTableRowFrontmatter",
    "okf.source_table": "VaultSourceTableFrontmatter",
    # Backward-compat flat types
    "control": "VaultControlFrontmatterR1",
    "system": "VaultSystemFrontmatter",
    "component": "VaultComponentFrontmatter",
    "organization": "VaultOrganizationFrontmatter",
    "plan": "VaultPlanFrontmatter",
    "index": "VaultOkfIndexFrontmatter",
    "mapping": "VaultMappingFrontmatter",
    "diagram": "VaultExtensionDiagramFrontmatter",
    "evidence": "VaultExtensionEvidenceFrontmatter",
    "finding": "VaultExtensionEvidenceFrontmatter",
    "inheritance": "VaultExtensionEvidenceFrontmatter",
    "boundary": "VaultExtensionEvidenceFrontmatter",
    "decision": "VaultExtensionEvidenceFrontmatter",
    "relationship": "VaultExtensionEvidenceFrontmatter",
    "schema": "VaultOkfSchemaFrontmatter",
    "validation_rule": "VaultOkfValidationRuleFrontmatter",
}


def _parse_frontmatter(path: Path) -> Tuple[Optional[Dict], Optional[str]]:
    """Extract frontmatter dict from a markdown file. Returns (dict, error)."""
    try:
        text = path.read_text()
    except Exception as e:
        return None, f"cannot read: {e}"

    if not text.startswith("---\n"):
        return None, "no frontmatter delimiters"

    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return None, "unclosed frontmatter block"

    fm_text = text[4:end]
    try:
        parsed = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        return None, f"YAML parse error: {e}"

    if not isinstance(parsed, dict):
        return None, "frontmatter is not a mapping"

    return parsed, None


def _find_md_files(vault_dirs: List[Path]) -> List[Path]:
    """Collect all .md files under the given directories."""
    files: List[Path] = []
    for vd in vault_dirs:
        if not vd.is_dir():
            continue
        for md in vd.rglob("*.md"):
            # Skip hidden dirs
            if any(p.startswith(".") for p in md.parts):
                continue
            files.append(md)
    return sorted(files)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate vault frontmatter against JSON Schema"
    )
    parser.add_argument(
        "--vault",
        action="append",
        default=None,
        help="Vault directory to scan (repeatable; default: find examples/vault/)",
    )
    parser.add_argument(
        "--schema",
        default=str(_SCHEMA_PATH),
        help="Path to vault-schemas.json",
    )
    parser.add_argument(
        "--strict", action="store_true", help="Fail on unknown types too"
    )
    args = parser.parse_args()

    if not _SCHEMA_PATH.exists():
        print(f"ERROR: schema not found at {_SCHEMA_PATH}", file=sys.stderr)
        return 2

    with open(args.schema) as f:
        schema_root = json.load(f)

    defs = schema_root.get("$defs", {})

    # Build a registry that knows how to resolve $ref from the full schema.
    from referencing import Registry, Resource

    resource = Resource.from_contents(schema_root)
    registry = Registry().with_resource(uri="urn:schemas:root", resource=resource)

    # For each def, create a wrapper {"$ref": "urn:schemas:root#/$defs/<name>"}
    # so Draft202012Validator resolves nested $refs against the full schema.
    validators: Dict[str, Draft202012Validator] = {}
    for name in defs:
        wrapper = {"$ref": f"urn:schemas:root#/$defs/{name}"}
        try:
            validators[name] = Draft202012Validator(wrapper, registry=registry)
        except Exception as e:
            print(f"WARNING: cannot compile schema {name}: {e}", file=sys.stderr)

    vault_dirs: List[Path] = []
    if args.vault:
        vault_dirs = [Path(v).resolve() for v in args.vault]
    else:
        # Default: scan examples/vault/ and compliance/tests/
        examples_vault = _WORKSPACE / "examples" / "vault"
        if examples_vault.is_dir():
            vault_dirs.append(examples_vault)

    if not vault_dirs:
        print("No vault directories to scan. Use --vault.", file=sys.stderr)
        return 0

    files = _find_md_files(vault_dirs)
    if not files:
        print("No .md files found.")
        return 0

    errors = 0
    warnings = 0
    validated = 0

    for path in files:
        try:
            rel = path.relative_to(_WORKSPACE)
        except ValueError:
            containing_vault = next(
                (vault for vault in vault_dirs if path.is_relative_to(vault)), None
            )
            rel = (
                Path(containing_vault.name) / path.relative_to(containing_vault)
                if containing_vault
                else path
            )
        fm, err = _parse_frontmatter(path)
        if err:
            print(f"WARN  {rel}: {err}")
            warnings += 1
            continue

        note_type = fm.get("type") or fm.get("note_type")
        if not note_type:
            print(f"WARN  {rel}: missing both 'type' and 'note_type'")
            warnings += 1
            continue

        # Disambiguate oscal.ssp: use note_type to select system vs plan schema
        effective_type = note_type
        fallback_note_type = fm.get("note_type")
        if note_type == "oscal.ssp" and fallback_note_type:
            if fallback_note_type == "system":
                effective_type = "system"
            elif fallback_note_type == "plan":
                effective_type = "plan"

        # Skip template notes — they're intentionally incomplete scaffolds
        # (placeholder types/values), so skip before the unknown-type check.
        if "_meta/templates/" in str(rel):
            continue

        schema_name = _TYPE_TO_SCHEMA.get(effective_type)
        if not schema_name:
            msg = f"WARN  {rel}: unknown type {effective_type!r}"
            if args.strict:
                print(f"ERROR {rel}: unknown type {effective_type!r} (strict mode)")
                errors += 1
            else:
                print(msg)
                warnings += 1
            continue

        validator = validators.get(schema_name)
        if not validator:
            print(f"WARN  {rel}: schema {schema_name!r} not compiled")
            warnings += 1
            continue

        schema_errors = list(validator.iter_errors(fm))
        if schema_errors:
            for se in schema_errors:
                print(
                    f"ERROR {rel}: {se.message} (at {'.'.join(str(p) for p in se.absolute_path)})"
                )
                errors += 1
        else:
            validated += 1

    print(f"\n{validated} notes validated, {warnings} warnings, {errors} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
