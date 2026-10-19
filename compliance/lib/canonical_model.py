"""Canonical intermediate model bridging FedRAMP/OSCAL parser output and the
OSBAPI projections defined in vault-schemas.json.

Every OSBAPI-facing generator (CRM projection, Backstage annotations, broker
adapter responses) should build its output from a list of `CanonicalControl`
records rather than directly from a parser's raw `Control` objects or raw
OSCAL SSP JSON. This keeps vocabulary mapping and source-format parsing
decoupled from OSBAPI's wire format, so a new source parser or a new OSBAPI
consumer only needs to plug into this one seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import vocab


@dataclass
class CanonicalControl:
    control_id: str
    implementation_status: str  # OSBAPI ImplementationStatus enum value
    origination: str  # OSBAPI ControlOrigination enum value
    statement_ids: List[str] = field(default_factory=list)
    responsible_role: Optional[str] = None
    exportable: bool = False
    remarks: Optional[str] = None
    # Raw OSCAL SSP values, preserved verbatim alongside the OSBAPI-mapped
    # fields above. OSCAL is treated as this pipeline's canonical
    # interoperability/storage format: its implementation-status/
    # control-origination vocabulary is richer (and sometimes multi-valued)
    # than OSBAPI's coarser enums, so nothing is discarded on the way in --
    # only collapsed for OSBAPI's simpler wire format, which carries the raw
    # values through in `oscal_implementation_status`/`oscal_origination` so
    # a round-trip back to OSCAL (or a docx regeneration) never has to guess.
    oscal_implementation_status: Optional[str] = None
    oscal_origination: List[str] = field(default_factory=list)

    @classmethod
    def from_raw(
        cls,
        control_id: str,
        *,
        raw_status: Optional[str],
        raw_origination: Optional[List[str]],
        statement_ids: Optional[List[str]] = None,
        responsible_role: Optional[str] = None,
        exportable: bool = False,
        strict: bool = False,
    ) -> "CanonicalControl":
        """Build a CanonicalControl from raw fismatic/python-ssp parser output.

        `strict=False` (default) lets unmapped/missing raw values fall back
        to the mapping tables' documented defaults rather than raising, since
        real-world SSPs are frequently missing or malformed checkbox data
        (see fismatic/src/phase_1/parser_comparison_report.md). Pass
        `strict=True` to fail loudly instead, e.g. in CI/regression tests
        against known-clean fixtures.
        """
        status = vocab.map_implementation_status(raw_status, strict=strict)
        origination = vocab.map_control_origination(raw_origination, strict=strict)

        remarks = "; ".join(r for r in (status.remark, origination.remark) if r) or None

        return cls(
            control_id=control_id,
            implementation_status=status.target,
            origination=origination.target,
            statement_ids=statement_ids or [],
            responsible_role=responsible_role,
            exportable=exportable,
            remarks=remarks,
            oscal_implementation_status=raw_status,
            oscal_origination=list(raw_origination or []),
        )

    def to_control_summary(self) -> Dict[str, Any]:
        """Render as an OSBAPI ControlSummary object.

        See vault-schemas.json #/$defs/ControlSummary.
        """
        summary: Dict[str, Any] = {
            "control_id": self.control_id,
            "statement_ids": list(self.statement_ids),
            "origination": self.origination,
            "implementation_status": self.implementation_status,
            "exportable": self.exportable,
        }
        if self.responsible_role:
            summary["responsible_role"] = self.responsible_role
        if self.remarks:
            summary["remarks"] = self.remarks
        if self.oscal_implementation_status:
            summary["oscal_implementation_status"] = self.oscal_implementation_status
        summary["oscal_origination"] = list(self.oscal_origination)
        return summary
