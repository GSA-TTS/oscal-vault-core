"""Loaders that turn parser output into a list of `CanonicalControl` records.

Two sources are supported:
  - A raw FedRAMP SSP .docx file, parsed live via fismatic's `SSPConverter`.
  - An OSCAL SSP JSON document already produced by fismatic's `SSPConverter`
    (i.e. one that has real `implementation-status`/`control-origination`
    fields on each implemented-requirement, not raw checkbox text).

Older OSCAL SSP JSON files at the repo root (generated before the fismatic
parser fixes) do NOT have usable `implementation-status`/`control-origination`
fields -- they predate this pipeline and should be regenerated from their
source .docx via `load_from_docx` instead of read with `load_from_oscal_ssp`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from .canonical_model import CanonicalControl

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FISMATIC_SRC = _REPO_ROOT / "fismatic" / "src" / "phase_1"
_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2019": "'", "\u2018": "'"})
_PART_SPLIT_RE = re.compile(r"(?:^|\n\n)Part\s+([a-z]):\s*\n+", re.IGNORECASE)


def _norm_prose(text: str) -> str:
    cleaned = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text or "").translate(_QUOTES)
    return re.sub(r"[^a-z0-9]+", "", cleaned.lower())


def _deduplicate_component_descriptions(components: list[dict[str, Any]]) -> list[str]:
    raw_texts = [
        str(component.get("description") or "").strip()
        for component in components
        if str(component.get("description") or "").strip()
    ]
    if len(raw_texts) <= 1:
        return raw_texts
    kept: list[str] = []
    for idx, desc in enumerate(raw_texts):
        nd = _norm_prose(desc)
        if not nd:
            continue
        is_dup = any(
            i != idx
            and _norm_prose(other)
            and nd in _norm_prose(other)
            and (len(_norm_prose(other)) > len(nd) or i > idx)
            for i, other in enumerate(raw_texts)
        )
        if not is_dup:
            kept.append(desc)
    return kept


def _split_part_sections(text: str) -> dict[str, str]:
    if "Part a:" not in text and "Part b:" not in text:
        return {}
    chunks = _PART_SPLIT_RE.split(text.strip())
    if len(chunks) < 3:
        return {}
    out: dict[str, str] = {}
    for idx in range(1, len(chunks), 2):
        part_key = chunks[idx].lower().strip()
        part_body = chunks[idx + 1].strip()
        if part_body and _norm_prose(part_body) != _norm_prose("This System:"):
            out[part_key] = part_body
    return out


def _ensure_fismatic_importable() -> None:
    path_str = str(_FISMATIC_SRC)
    if _FISMATIC_SRC.is_dir() and path_str not in sys.path:
        sys.path.insert(0, path_str)


def _canonical_controls_from_implemented_requirements(
    requirements: list[dict[str, Any]], *, strict: bool = False
) -> list[CanonicalControl]:
    controls: list[CanonicalControl] = []
    for req in requirements:
        control_id = req.get("control-id")
        if not control_id:
            continue

        props = req.get("props") or []
        prop_values: dict[str, list[str]] = {}
        for prop in props:
            if prop.get("name") and prop.get("value") is not None:
                prop_values.setdefault(prop["name"], []).append(str(prop["value"]))
        raw_status = (req.get("implementation-status") or {}).get("state") or next(
            iter(prop_values.get("implementation-status", [])), None
        )
        raw_origination = req.get("control-origination") or prop_values.get(
            "control-origination", []
        )
        statement_ids = [
            s["statement-id"]
            for s in req.get("statements", [])
            if s.get("statement-id")
        ]
        responsible_roles = req.get("responsible-roles") or []
        responsible_role = (
            responsible_roles[0].get("role-id") if responsible_roles else None
        )
        if not raw_status:
            statement_statuses = {
                (component.get("implementation-status") or {}).get("state")
                for statement in req.get("statements", [])
                for component in statement.get("by-components", [])
                if (component.get("implementation-status") or {}).get("state")
            }
            if len(statement_statuses) == 1:
                raw_status = statement_statuses.pop()

        controls.append(
            CanonicalControl.from_raw(
                control_id,
                raw_status=raw_status,
                raw_origination=raw_origination,
                statement_ids=statement_ids,
                responsible_role=responsible_role,
                strict=strict,
            )
        )
    return controls


def load_from_oscal_ssp(
    oscal_ssp: dict[str, Any], *, strict: bool = False
) -> list[CanonicalControl]:
    """Build CanonicalControl records from an already-parsed OSCAL SSP dict
    (the shape produced by `fismatic.ssp_converter.SSPConverter.get_oscal_data()`).
    """
    ssp = oscal_ssp.get("system-security-plan", oscal_ssp)
    requirements = (ssp.get("control-implementation") or {}).get(
        "implemented-requirements", []
    )
    return _canonical_controls_from_implemented_requirements(
        requirements, strict=strict
    )


def load_from_docx(docx_path: str, *, strict: bool = False) -> list[CanonicalControl]:
    """Parse a FedRAMP SSP .docx file live and build CanonicalControl records."""
    _ensure_fismatic_importable()
    from fismatic.ssp_converter import SSPConverter

    converter = SSPConverter(docx_path)
    oscal_data = converter.get_oscal_data()
    if not oscal_data:
        raise ValueError(f"Failed to parse SSP document: {docx_path}")
    return load_from_oscal_ssp(oscal_data, strict=strict)


def statement_text_map_from_oscal_ssp(
    oscal_ssp: dict[str, Any],
) -> dict[str, dict[str, str]]:
    """Build ``{control_id: {statement_id: description}}`` from an OSCAL SSP dict.

    ``CanonicalControl`` deliberately carries only ``statement_ids`` (see
    ``canonical_model.py`` -- it's the OSBAPI-facing shape and statement
    *text* isn't part of the OSBAPI ``ControlSummary`` wire format). The
    ``.docx`` control-table fill path
    (:func:`compliance.lib.control_table_fill.fill_control_tables`) needs
    the actual narrative text to write into each control's "Part <key>"
    rows, so this helper extracts it straight from the raw OSCAL SSP dict
    (the same source :func:`load_from_oscal_ssp` reads) without changing
    ``CanonicalControl``'s shape.
    """
    ssp = oscal_ssp.get("system-security-plan", oscal_ssp)
    requirements = (ssp.get("control-implementation") or {}).get(
        "implemented-requirements", []
    )
    out: dict[str, dict[str, str]] = {}
    for req in requirements:
        control_id = req.get("control-id")
        if not control_id:
            continue
        stmts = {}
        for statement in req.get("statements", []):
            statement_id = statement.get("statement-id")
            if not statement_id:
                continue
            text = statement.get("description") or statement.get("remarks", "")
            if not text:
                text = "\n\n".join(
                    _deduplicate_component_descriptions(
                        statement.get("by-components", [])
                    )
                )
            stmts[statement_id] = text
        by_component_texts = _deduplicate_component_descriptions(
            req.get("by-components", [])
        )
        if by_component_texts and stmts:
            by_part = {
                sid.replace("_stmt.", "_smt.").rsplit("_smt.", 1)[-1].lower(): sid
                for sid in stmts
                if "_smt." in sid.replace("_stmt.", "_smt.")
            }
            for wt in by_component_texts:
                for p_key, p_txt in _split_part_sections(wt).items():
                    target_sid = by_part.get(p_key)
                    if target_sid and _norm_prose(p_txt) not in _norm_prose(
                        stmts[target_sid]
                    ):
                        cur = stmts[target_sid].strip()
                        stmts[target_sid] = (
                            f"{p_txt}\n\n{cur}".strip() if cur else p_txt
                        )
            combined_stmt_norm = "".join(_norm_prose(v) for v in stmts.values())
            first_statement = next(iter(stmts))
            for wt in by_component_texts:
                if _norm_prose(wt) and _norm_prose(wt) not in combined_stmt_norm:
                    cur = stmts[first_statement].strip()
                    stmts[first_statement] = f"{wt}\n\n{cur}".strip() if cur else wt
                    combined_stmt_norm += _norm_prose(wt)
        if stmts:
            out[control_id] = stmts
    return out


def origination_provider_map_from_oscal_ssp(
    oscal_ssp: dict[str, Any],
) -> dict[str, str]:
    """Build ``{control_id: provider_label}`` from component titles in an OSCAL SSP."""
    ssp = oscal_ssp.get("system-security-plan", oscal_ssp)
    comp_titles = {
        str(comp.get("uuid") or ""): str(comp.get("title") or "").strip()
        for comp in (ssp.get("system-implementation") or {}).get("components", [])
        if comp.get("uuid") and comp.get("title") and comp.get("type") != "this-system"
    }
    requirements = (ssp.get("control-implementation") or {}).get(
        "implemented-requirements", []
    )
    out: dict[str, str] = {}
    for req in requirements:
        control_id = req.get("control-id")
        if not control_id:
            continue
        names: list[str] = []
        for block in [
            *req.get("by-components", []),
            *(c for s in req.get("statements", []) for c in s.get("by-components", [])),
        ]:
            title = comp_titles.get(str(block.get("component-uuid") or ""))
            if title and title not in names:
                names.append(title)
        if names:
            out[control_id] = " and ".join(names)
    return out


def whole_control_text_map_from_oscal_ssp(oscal_ssp: dict[str, Any]) -> dict[str, str]:
    """Build ``{control_id: text}`` for requirements that have no statements.

    A requirement without ``statements`` carries its narrative only in
    ``by-components`` on the requirement itself. ``statement_text_map_from_oscal_ssp``
    cannot represent that (there is no statement id to key it by), so without this
    map the narrative never reaches the DOCX control table. Requirements that do
    have statements are excluded: their text is already in the statement map.
    """
    ssp = oscal_ssp.get("system-security-plan", oscal_ssp)
    requirements = (ssp.get("control-implementation") or {}).get(
        "implemented-requirements", []
    )
    out: dict[str, str] = {}
    for req in requirements:
        control_id = req.get("control-id")
        if not control_id or req.get("statements"):
            continue
        text = "\n\n".join(
            str(component.get("description") or "").strip()
            for component in req.get("by-components", [])
            if str(component.get("description") or "").strip()
        )
        if text:
            out[control_id] = text
    return out
