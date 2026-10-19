"""Generic, fail-closed source inventory and reconciliation validation."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCHEMA = (
    ROOT / "compliance/schemas/reconciliation/source_inventory_schema_v1.json"
)
OVERLAY_SCHEMA = ROOT / "compliance/schemas/reconciliation/overlay_schema_v1.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_source_config(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    schema = json.loads(SOURCE_SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path)
    )
    if errors:
        raise ValueError("; ".join(error.message for error in errors))
    return data


def inventory_source(
    repo: Path, config: dict[str, Any], expected_commit: str
) -> dict[str, Any]:
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != expected_commit:
        raise ValueError(f"source checkout must be {expected_commit}, found {commit}")
    files: list[dict[str, Any]] = []
    control_ids: set[str] = set()
    for source in config["sources"]:
        root = (repo / source["path"]).resolve()
        if not root.is_relative_to(repo.resolve()):
            raise ValueError(f"source escapes repository: {source['path']}")
        for path in sorted(root.glob(source["include"])):
            if not path.is_file():
                continue
            relative = path.relative_to(repo).as_posix()
            files.append(
                {
                    "path": relative,
                    "kind": source["kind"],
                    "sha256": sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
            if source.get("extract_control_ids"):
                text = path.read_text(encoding="utf-8", errors="replace")
                control_ids.update(
                    match.upper()
                    for match in re.findall(
                        r"(?<![A-Za-z0-9])([a-z]{2}-\d+(?:\.\d+)?)", text, re.I
                    )
                )
    result = {
        "schema_version": 1,
        "system_id": config["system_id"],
        "repository": config["repository"],
        "commit": commit,
        "technologies": config["technologies"],
        "files": files,
        "summary": {
            "kinds": {
                kind: sum(item["kind"] == kind for item in files)
                for kind in sorted({item["kind"] for item in files})
            },
            "control_ids": sorted(control_ids),
        },
    }
    result["inventory_sha256"] = canonical_sha256(result)
    return result


def validate_overlay(data: dict[str, Any], *, require_ready: bool = False) -> list[str]:
    schema = json.loads(OVERLAY_SCHEMA.read_text(encoding="utf-8"))
    errors = [error.message for error in Draft202012Validator(schema).iter_errors(data)]
    if require_ready:
        pending = [
            change.get("operation_id", "<unknown>")
            for change in data.get("changes", [])
            if change.get("status") != "approved"
        ]
        if pending:
            errors.append(f"overlay operations require approval: {', '.join(pending)}")
    return errors
