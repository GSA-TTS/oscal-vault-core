"""Strict, fail-closed NIST control identity for adapters that must keep enhancements distinct.

Not the same as ``compliance.lib.vault.canonical_control_id``, which zero-pads for vault file
names (``AC-02``) and passes unparseable ids through. This module emits ``AC-2`` / ``AC-2(1)``
and raises on anything it cannot parse; use it at adapter boundaries (CycloneDX, DOCX, YAML).

Control enhancements must remain distinct across YAML, OSCAL, DOCX, and CDX
boundaries. This module deliberately accepts the common parenthesized and dot
forms, but always emits the unambiguous parenthesized representation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CONTROL_ID = re.compile(
    r"^(?P<family>[A-Za-z]{2})\s*-?\s*0*(?P<number>\d+)"
    r"(?:\s*(?:\(\s*(?P<paren>\d+|[A-Za-z]+)\s*\)|\.(?P<dot>\d+)))?$"
)


@dataclass(frozen=True, slots=True)
class ControlKey:
    """Stable identity for a NIST control or numeric enhancement."""

    family: str
    number: int
    enhancement: str | None = None

    def __str__(self) -> str:
        base = f"{self.family}-{self.number}"
        return f"{base}({self.enhancement})" if self.enhancement else base


def parse_control_id(raw: str) -> ControlKey:
    """Parse a control ID and return its canonical typed identity.

    ``AC-02(01)`` and ``ac-2.1`` both become ``ControlKey("AC", 2, "1")``.
    Invalid or partially matched IDs fail closed instead of being silently
    passed through to a downstream adapter.
    """

    value = str(raw or "").strip()
    match = _CONTROL_ID.fullmatch(value)
    if not match:
        raise ValueError(f"invalid NIST control ID: {raw!r}")
    enhancement = match.group("paren") or match.group("dot")
    if enhancement and enhancement.isdigit():
        enhancement = str(int(enhancement))
    return ControlKey(
        family=match.group("family").upper(),
        number=int(match.group("number")),
        enhancement=enhancement.upper() if enhancement else None,
    )


def canonical_control_id(raw: str) -> str:
    """Return the canonical parenthesized representation of ``raw``."""

    return str(parse_control_id(raw))


def reject_duplicate_control_ids(
    records: list[dict], field: str = "control_id"
) -> None:
    """Reject records that collide after canonical control-ID normalization."""

    seen: dict[str, int] = {}
    for index, record in enumerate(records):
        raw = record.get(field, "")
        canonical = canonical_control_id(raw)
        previous = seen.get(canonical)
        if previous is not None:
            raise ValueError(
                f"duplicate canonical control ID {canonical!r} in records "
                f"{previous} and {index}"
            )
        seen[canonical] = index
