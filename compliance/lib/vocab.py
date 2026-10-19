"""Vocabulary mapping loader: translate raw FedRAMP/parser output into a
normalized status/origination enum used across the vault and OSCAL export.

Mapping tables live in compliance/mappings/*.yaml so they can be reviewed and
edited by compliance/GRC staff independently of parsing or projection code.
See:
  - compliance/mappings/implementation-status-map.yaml
  - compliance/mappings/control-origination-map.yaml
  - vault-schemas.json
    (#/$defs/ImplementationStatus, #/$defs/ControlOrigination)

The normalized enums intentionally match the OSBAPI CRM vocabulary so the
exported OSCAL can be projected to an OSBAPI/Backstage catalog; that
recommended projection is documented under docs/osbapi-backstage-interop/.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

MAPPINGS_DIR = Path(__file__).resolve().parent.parent / "mappings"


class MappingError(ValueError):
    """Raised when a raw value has no mapping entry and strict mode is on."""


@dataclass
class MappedValue:
    target: str
    remark: Optional[str] = None


def _load_yaml(name: str) -> Dict[str, Any]:
    path = MAPPINGS_DIR / name
    with open(path, "r") as f:
        return yaml.safe_load(f)


@functools.lru_cache(maxsize=None)
def _status_table() -> Dict[str, Any]:
    return _load_yaml("implementation-status-map.yaml")


@functools.lru_cache(maxsize=None)
def _origination_table() -> Dict[str, Any]:
    return _load_yaml("control-origination-map.yaml")


def map_implementation_status(
    raw_state: Optional[str], *, strict: bool = False
) -> MappedValue:
    """Map a raw implementation-status value to an OSBAPI ImplementationStatus
    enum value, per compliance/mappings/implementation-status-map.yaml.

    `raw_state` is expected to be one of the values fismatic's parser produces
    (e.g. Control.implementation_status['state']): implemented,
    partially-implemented, alternative-implementation, planned,
    customer-configured, not-applicable, not-implemented.
    """
    table = _status_table()
    mapping = table["mapping"]
    key = (raw_state or "").strip().lower()
    entry = mapping.get(key)
    if entry is None:
        if strict:
            raise MappingError(
                f"No implementation-status mapping for raw value {raw_state!r}. "
                f"Known values: {sorted(mapping)}"
            )
        entry = table["default"]
    return MappedValue(target=entry["target"], remark=entry.get("remark"))


def map_control_origination(
    raw_flags: Optional[List[str]], *, strict: bool = False
) -> MappedValue:
    """Map a list of raw control-origination flags to a single OSBAPI
    ControlOrigination enum value, per
    compliance/mappings/control-origination-map.yaml.

    `raw_flags` is expected to be the list fismatic's parser produces (e.g.
    Control.origination): any of inherited, hybrid, system-specific,
    service-provider-corporate, customer-configured, customer-provided,
    shared. Multiple flags may be set simultaneously; the table's
    `precedence` order picks the one reported as canonical.
    """
    table = _origination_table()
    mapping = table["mapping"]
    precedence = table["precedence"]

    raw_set = {f.strip().lower() for f in (raw_flags or []) if f}
    for candidate in precedence:
        if candidate in raw_set:
            entry = mapping[candidate]
            return MappedValue(target=entry["target"], remark=entry.get("remark"))

    if strict:
        raise MappingError(
            f"No recognized control-origination flags in {raw_flags!r}. "
            f"Known values: {sorted(mapping)}"
        )
    entry = table["default"]
    return MappedValue(target=entry["target"], remark=entry.get("remark"))
