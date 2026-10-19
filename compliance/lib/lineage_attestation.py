"""Digest-bound review attestations for lossy lineage operators."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_IMPLEMENTATIONS = {
    "gfm_table_parse": (
        "compliance/lib/transforms.py",
        "compliance/lib/oscal_extension_bridge.py",
    ),
    "markdown_list_parse": (
        "compliance/lib/transforms.py",
        "compliance/lib/oscal_extension_bridge.py",
    ),
    "port_expression_parse": (
        "compliance/lib/transforms.py",
        "compliance/lib/oscal_extension_bridge.py",
    ),
}


def implementation_digest(operator: str, repo_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in _IMPLEMENTATIONS[operator]:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((repo_root / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_attestations(path: Path, repo_root: Path) -> list[str]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    findings: list[str] = []
    records = payload.get("attestations") or []
    by_operator = {record.get("operator"): record for record in records}
    for operator in _IMPLEMENTATIONS:
        record = by_operator.get(operator)
        if not record:
            findings.append(f"missing review attestation: {operator}")
            continue
        expected = implementation_digest(operator, repo_root)
        if record.get("implementation_sha256") != expected:
            findings.append(f"stale review attestation: {operator}")
        if not record.get("reviewer") or not record.get("rationale"):
            findings.append(f"incomplete review attestation: {operator}")
        if record.get("decision") != "approved":
            findings.append(f"operator is not approved: {operator}")
    unknown = set(by_operator) - set(_IMPLEMENTATIONS)
    if unknown:
        findings.append(f"unknown attested operators: {sorted(unknown)}")
    return findings
