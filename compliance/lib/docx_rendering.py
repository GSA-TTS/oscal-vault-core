"""Reusable post-assembly DOCX rendering and reproducibility helpers.

Consumers provide their system-specific implementation-layer labels as data;
this module owns the generic Markdown-to-Word pass, manifest portability, and
deterministic DOCX ZIP packaging.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

from docx import Document

from compliance.lib import docx_narrative_format

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_CONTROL_TABLE_RE = re.compile(r"^[A-Z]{2}-\d+")
_CONTROL_OR_SUPPLEMENTAL_TABLE_RE = re.compile(r"^(?:[A-Z]{2}-\d+|Control ID\b)")
_ALL_TABLES_RE = re.compile(r".*", re.DOTALL)
_AUDIT_TABLE_SCOPES = {
    "control": _CONTROL_TABLE_RE,
    "control-and-supplemental": _CONTROL_OR_SUPPLEMENTAL_TABLE_RE,
    "all": _ALL_TABLES_RE,
}


def portable_manifest_paths(value: Any, repository_root: Path) -> Any:
    """Replace paths under *repository_root* with stable relative paths."""
    if isinstance(value, dict):
        return {
            key: portable_manifest_paths(item, repository_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [portable_manifest_paths(item, repository_root) for item in value]
    if isinstance(value, str):
        try:
            return str(Path(value).resolve().relative_to(repository_root.resolve()))
        except ValueError:
            return value
    return value


def normalize_docx_zip(path: Path) -> None:
    """Rewrite a DOCX with fixed ZIP timestamps for byte-stable output."""
    with zipfile.ZipFile(path) as source:
        members = [(info, source.read(info)) for info in source.infolist()]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w") as target:
        for info, data in members:
            fixed = zipfile.ZipInfo(info.filename, date_time=_ZIP_EPOCH)
            fixed.compress_type = zipfile.ZIP_DEFLATED
            fixed.external_attr = info.external_attr
            target.writestr(fixed, data)
    temporary.replace(path)


def format_rendered_narratives(
    output_path: Path,
    *,
    implementation_layer_labels: list[str],
    include_supplemental_control_tables: bool = False,
    audit_tables: str | None = None,
) -> dict[str, Any]:
    """Render Markdown in control narratives and return auditable formatting facts."""
    if not all(
        isinstance(label, str) and label.strip()
        for label in implementation_layer_labels
    ):
        raise ValueError("implementation_layer_labels must contain non-empty strings")
    header_re = (
        re.compile(
            "("
            + "|".join(re.escape(label) for label in implementation_layer_labels)
            + ")",
            re.IGNORECASE,
        )
        if implementation_layer_labels
        else None
    )
    full_header_re = (
        re.compile(
            "|".join(re.escape(label) for label in implementation_layer_labels),
            re.IGNORECASE,
        )
        if implementation_layer_labels
        else None
    )
    default_scope = (
        "control-and-supplemental" if include_supplemental_control_tables else "control"
    )
    audit_scope = audit_tables or default_scope
    try:
        table_re = _AUDIT_TABLE_SCOPES[audit_scope]
    except KeyError as exc:
        allowed = ", ".join(sorted(_AUDIT_TABLE_SCOPES))
        raise ValueError(f"audit_tables must be one of: {allowed}") from exc
    document = Document(output_path)
    result = {
        "formatted_cells": docx_narrative_format.format_document(document, header_re),
        "removed_table_page_breaks": docx_narrative_format.strip_table_page_breaks(
            document
        ),
    }
    result["audit"] = docx_narrative_format.audit_document(
        document, full_header_re, table_re
    )
    if result["formatted_cells"] or result["removed_table_page_breaks"]:
        document.save(output_path)
    return result


def write_manifest(
    path: Path, manifest: dict[str, Any], *, repository_root: Path | None = None
) -> None:
    """Write a deterministic manifest, optionally portable relative to a repository."""
    payload = (
        portable_manifest_paths(manifest, repository_root)
        if repository_root
        else manifest
    )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
