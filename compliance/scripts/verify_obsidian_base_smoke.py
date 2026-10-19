#!/usr/bin/env python3
"""Validate machine-readable evidence for the manual Obsidian Base release gate."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def validate_manifest(path: Path, *, require_complete: bool = False) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return [f"manifest does not exist: {path}"] if require_complete else []
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return ["manifest must be a mapping"]
    allowed = {
        "schema_version",
        "status",
        "obsidian_version",
        "vault_commit",
        "vault",
        "tested_at",
        "tester",
        "views",
        "no_rewrite_diff",
        "evidence",
    }
    unknown = set(payload) - allowed
    if unknown:
        errors.append(f"unknown fields: {sorted(unknown)}")
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if payload.get("obsidian_version") != "1.14.4":
        errors.append("obsidian_version must be 1.14.4")
    status = payload.get("status")
    if status not in {"pending", "passed"}:
        errors.append("status must be pending or passed")
    if not require_complete and status == "pending":
        return errors
    for field in ("vault_commit", "vault", "tested_at", "tester"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            errors.append(f"{field} must be a non-empty string")
    views = payload.get("views")
    if not isinstance(views, list) or not views:
        errors.append("views must contain at least one observed Base view")
    else:
        for index, view in enumerate(views):
            if not isinstance(view, dict) or set(view) != {"file", "view", "rows"}:
                errors.append(f"views[{index}] must contain file, view, and rows")
                continue
            if not isinstance(view["rows"], int) or view["rows"] < 0:
                errors.append(f"views[{index}].rows must be a non-negative integer")
    if payload.get("no_rewrite_diff") is not True:
        errors.append("no_rewrite_diff must be true")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence must contain screenshot or recording references")
    elif any(not isinstance(item, str) or not item.strip() for item in evidence):
        errors.append("evidence references must be non-empty strings")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--require", action="store_true", help="Require completed passing evidence"
    )
    args = parser.parse_args()
    errors = validate_manifest(args.manifest, require_complete=args.require)
    if errors:
        for error in errors:
            print(f"ERROR {args.manifest}: {error}")
        return 1
    print(f"OK {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
