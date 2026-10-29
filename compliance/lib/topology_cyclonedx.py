"""CycloneDX threat-model blueprint topology from a parsed ``Topology``.

Returns the ``zones``, ``assets``, ``dataStores`` and ``flows`` members of a blueprint.
Canvas layout is a presentation concern of the consuming tool and stays out of core.
"""

from __future__ import annotations

from typing import Any

from .topology import (
    DATASTORE,
    EXTERNAL,
    HUMAN,
    ZONE_LABELS,
    ZONE_TRUST,
    Topology,
)

_PLACEMENT = {"users": "external", "external": "external"}
_LIBRARY = {"relational": "aws/rds", "key-value": "aws/elasticache", "object": "aws/s3"}


def _prop(name: str, value: Any) -> dict[str, str]:
    return {"name": name, "value": str(value)}


def _store_type(kind: str) -> Any:
    if kind == "legacy-unspecified":
        return {
            "name": "legacy-unspecified",
            "description": "The SSP does not classify this data store.",
        }
    return kind


def to_cyclonedx_blueprint(topo: Topology) -> dict[str, list[dict[str, Any]]]:
    zone_ref = {z: f"zone-{topo.system_id}-{z}" for z in ZONE_LABELS}
    ref = {c.key: f"asset-{topo.system_id}-{c.key}" for c in topo.components}

    zones = [
        {
            "bom-ref": zone_ref[z],
            "name": ZONE_LABELS[z].format(display=topo.display),
            "type": "trust",
            "properties": [
                _prop("precogly:trust-level", ZONE_TRUST[z]),
                _prop("precogly:canvas-placement", _PLACEMENT.get(z, "internal")),
                _prop("vault:role", "topology"),
            ],
        }
        for z in topo.zones
    ]

    assets: list[dict[str, Any]] = []
    stores: list[dict[str, Any]] = []
    for c in topo.components:
        props = [_prop("vault:role", "topology")]
        if c.kind == DATASTORE:
            library = _LIBRARY.get(c.store_type or "")
            if library:
                props.append(_prop("precogly:library", library))
            stores.append(
                {
                    "bom-ref": ref[c.key],
                    "name": c.name,
                    "type": _store_type(c.store_type or "legacy-unspecified"),
                    "zone": zone_ref[c.zone],
                    "properties": props,
                }
            )
            continue
        if "aws sns" in c.name.lower():
            props.append(_prop("precogly:library", "aws/sns"))
        assets.append(
            {
                "bom-ref": ref[c.key],
                "name": c.name,
                "type": {HUMAN: "actor", EXTERNAL: "system"}.get(c.kind, "process"),
                "zone": zone_ref[c.zone],
                "properties": props,
            }
        )

    flows = []
    for f in topo.flows:
        props = [
            _prop("vault:role", "topology"),
            _prop("resp:crosses-trust-zone", str(f.crosses_boundary).lower()),
            _prop("resp:data-classification", f.sensitivity),
        ]
        if f.port:
            props.append(_prop("precogly:port", f.port))
        flow: dict[str, Any] = {
            "bom-ref": f.ref,
            "name": f.label,
            "source": ref[f.source],
            "destination": ref[f.destination],
            "encrypted": f.encrypted,
            "protocols": [f.protocol] if f.protocol else [],
            "properties": props,
        }
        if f.purpose:
            flow["description"] = f.purpose
        flows.append(flow)
    return {"zones": zones, "assets": assets, "dataStores": stores, "flows": flows}
