"""Extract filled control-parameter values (ODPs) from a FedRAMP SSP `.docx`.

FedRAMP rev5 SSP templates express each control's *organization-defined
parameters* (ODPs / "Assignment"/"Selection" values) as **bracketed text inside
the catalog-statement paragraphs that precede each control's Control Summary
Information (CIS) table**, e.g.::

    AC-02: Account Management
    ...
    Require approvals by [designated account managers as specified in AC-02.b] ...
    Notify account managers and [System Owner, System/Network Administrator, ...] within:
    [14 days] when accounts are no longer required;

fismatic's OSCAL output captures the CIS *implementation narrative* (Part a/b/c)
but discards these bracketed ODP values, so control notes previously emitted
"Parameters not extracted by parser". This module recovers them by walking the
document body in order, tracking the current control heading, and collecting the
bracketed values that appear before the next control's CIS table.

The result maps ``canonical_control_id -> [ControlParameter, ...]`` and is
consumed by the vault builder (Parameters table + frontmatter) and the OSCAL
exporter (``set-parameters``).

This is a *heuristic recovery*: brackets are the FedRAMP ODP convention, but a
few brackets are boilerplate placeholders (e.g. ``[Enter System's Name]``,
``[ACRONYM]``). Those are filtered so we don't emit non-parameter noise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Union

# Bracketed span with 2..400 chars, no nested brackets.
_BRACKET = re.compile(r"\[([^\[\]]{2,400})\]")

# A control heading line: "AC-02: Account Management", "AC-2:", "SC-7 (3):".
_CONTROL_HEADING = re.compile(
    r"^\s*([A-Za-z]{2}-\d{1,3}(?:\s*\(\d+\))?)\s*[:.\u2013-]", re.MULTILINE
)

# Placeholder brackets that are template boilerplate, NOT filled ODP values.
# Matched case-insensitively against the *whole* bracket content (stripped).
_PLACEHOLDER_EXACT = frozenset(
    s.lower()
    for s in (
        "name",
        "acronym",
        "enter system's name",
        "enter system name",
        "insert",
        "date",
        "pci",
        "assignment",
        "selection",
        "e.g.",
        "tbd",
    )
)
_PLACEHOLDER_PREFIX = (
    "enter ",
    "insert ",
    "select ",
    "choose ",
    "assignment:",
    "selection:",
)


@dataclass
class ControlParameter:
    """One recovered organization-defined parameter value for a control."""

    control_id: str
    value: str
    # 1-based order of appearance within the control, used to synthesize a
    # stable param-id when the source has no explicit label.
    ordinal: int = 0

    @property
    def param_id(self) -> str:
        """Synthetic OSCAL param-id, e.g. ``ac-02_odp.01``. Deterministic per
        (control, ordinal) so re-exports are stable."""
        base = self.control_id.lower().replace("(", ".").replace(")", "")
        return f"{base}_odp.{self.ordinal:02d}"


@dataclass
class ParameterExtractionResult:
    by_control: Dict[str, List[ControlParameter]] = field(default_factory=dict)

    def total(self) -> int:
        return sum(len(v) for v in self.by_control.values())

    def for_control(self, control_id: str) -> List[ControlParameter]:
        return self.by_control.get(control_id, [])


def _is_placeholder(value: str) -> bool:
    v = value.strip().lower()
    if v in _PLACEHOLDER_EXACT:
        return True
    if any(v.startswith(p) for p in _PLACEHOLDER_PREFIX):
        return True
    # Pure punctuation / single letter / obvious non-content.
    if len(v) < 2 or not any(ch.isalnum() for ch in v):
        return True
    return False


def _canonical(control_id: str) -> str:
    # Local import avoids a hard dependency cycle with vault.py at import time.
    from compliance.lib.vault import canonical_control_id

    return canonical_control_id(control_id)


def _iter_body_paragraph_texts(document) -> List[str]:
    """Return the document body paragraph texts in document order.

    Only top-level paragraphs are walked (not table cells): the ODP values live
    in the catalog-statement paragraphs *between* controls, and pulling table
    text back in would re-introduce CIS narrative noise.
    """
    from docx.text.paragraph import Paragraph

    texts: List[str] = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            texts.append(Paragraph(child, document).text)
    return texts


# CIS table first-cell control id, e.g. "AC-2", "AC-02", "AC-2 (1)".
_CIS_CELL_ID = re.compile(r"^\s*([A-Za-z]{2}-\d{1,3}(?:\s*\(\d+\))?)\s*$")


def _iter_body_items(document):
    """Yield ``('para', text)`` and ``('cis', control_id)`` in document order.

    A ``cis`` item is emitted when a table's first cell is a bare control id
    (the Control Summary Information table). Not all templates precede the CIS
    table with an ``AC-02:`` heading paragraph (e.g. Data.gov, Notify anchor the
    control only by the CIS table), so the CIS table itself also anchors the
    control that the *preceding* bracket paragraphs belong to.
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield ("para", Paragraph(child, document).text)
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            try:
                cell0 = table.rows[0].cells[0].text.strip()
            except (IndexError, AttributeError):
                continue
            m = _CIS_CELL_ID.match(cell0)
            if m:
                yield ("cis", m.group(1))


def extract_parameters(docx_path: Union[str, Path]) -> ParameterExtractionResult:
    """Scan a FedRAMP SSP ``.docx`` and recover filled ODP values per control.

    Two heading conventions are handled:

    1. Templates with ``AC-02: Account Management`` heading paragraphs before
       the catalog statements (for example, two real agency SSPs). The heading sets
       the current control directly.
    2. Templates that anchor a control only by its Control Summary Information
       (CIS) table (e.g. Data.gov, Notify). Here the bracket paragraphs appear
       *before* the CIS table, so pending bracket values are attributed to the
       control id read from the upcoming CIS table's first cell.
    """
    import docx as _docx

    document = _docx.Document(str(docx_path))
    result = ParameterExtractionResult()

    current: str | None = None
    counters: Dict[str, int] = {}
    # Bracket values seen since the last control anchor, awaiting a CIS table to
    # attribute them to (convention #2). Cleared whenever a heading (#1) or a
    # CIS table resolves the owning control.
    pending: List[str] = []

    def _emit(control_id: str, values: List[str]) -> None:
        counters.setdefault(control_id, 0)
        for value in values:
            counters[control_id] += 1
            result.by_control.setdefault(control_id, []).append(
                ControlParameter(
                    control_id=control_id, value=value, ordinal=counters[control_id]
                )
            )

    for kind, payload in _iter_body_items(document):
        if kind == "cis":
            cis_control = _canonical(payload)
            # Bracket values gathered before this CIS table belong to it
            # (convention #2), unless a heading already claimed a *different*
            # control for them.
            if pending and (current is None or current == cis_control):
                _emit(cis_control, pending)
            current = cis_control
            pending = []
            continue

        stripped = (payload or "").strip()
        if not stripped:
            continue

        heading = _CONTROL_HEADING.match(stripped)
        if heading:
            new_control = _canonical(heading.group(1))
            # A heading resolves ownership immediately (convention #1): flush any
            # pending values to the *previous* control is wrong, so attribute
            # subsequent brackets to the new control and drop stale pending.
            current = new_control
            pending = []

        for match in _BRACKET.finditer(stripped):
            value = match.group(1).strip()
            if _is_placeholder(value):
                continue
            if current is not None and _CONTROL_HEADING.match(stripped):
                # Heading line with an inline bracket -> attribute directly.
                _emit(current, [value])
            elif current is not None:
                # Post-heading paragraph (convention #1): attribute directly.
                _emit(current, [value])
            else:
                # Pre-CIS paragraph, no heading yet (convention #2): defer.
                pending.append(value)

    # Drop controls that ended up with no real parameters.
    result.by_control = {k: v for k, v in result.by_control.items() if v}
    return result
