"""Project a system directory into a docxtpl fill context.

The directory holds ``system.yaml`` plus optional ``sidecar.yaml``,
``docx.yaml``, ``model/architecture.yaml`` and ``model/base-dfd.cdx.json``. Narrative
slots for the Moderate SSP template (general description, components and
boundaries, network architecture, data flow, inventories) are composed from them,
each with provenance and readiness metadata.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ContextField:
    value: str
    kind: str = "narrative"
    source_paths: tuple[str, ...] = ()
    source_records: int = 0

    @property
    def ready(self) -> bool:
        return bool(self.value.strip()) and self.value != TBD_SENTINEL


@dataclass
class DocxContext:
    fields: dict[str, ContextField] = field(default_factory=dict)

    def render_context(self) -> dict[str, str]:
        return {name: item.value for name, item in self.fields.items()}

    def readiness_report(self) -> dict[str, Any]:
        ready = sum(item.ready for item in self.fields.values())
        total = len(self.fields)
        return {
            "fields": {
                name: {**asdict(item), "ready": item.ready}
                for name, item in self.fields.items()
            },
            "summary": {
                "total_fields": total,
                "ready_fields": ready,
                "placeholder_fields": total - ready,
                "readiness_fraction": round(ready / total, 4) if total else 1.0,
                "source_records": sum(
                    item.source_records for item in self.fields.values()
                ),
            },
        }


def _load_yaml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    return json.loads(path.read_text())


def _paragraph(*parts: str) -> str:
    """Join non-empty pieces with a blank line; strip leading/trailing whitespace."""
    joined = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return joined


def _general_system_description(manifest: dict, sidecar: dict) -> str:
    cpo = sidecar.get("cpo") or {}
    name = manifest.get("name") or cpo.get("service_name") or "the system"
    baseline = manifest.get("baseline") or ""
    service_model = (
        ", ".join(manifest.get("service_model") or []) or "unspecified service model"
    )
    inherits = manifest.get("inherits_from") or []
    inherits_txt = (
        f"Inherits controls from: {', '.join(inherits)}."
        if inherits
        else "No inherited tier CRMs declared."
    )
    baseline_txt = f"FedRAMP {baseline} baseline; " if baseline else ""
    description = cpo.get("service_description") or ""
    return _paragraph(
        f"{name} ({baseline_txt}{service_model}).",
        description,
        inherits_txt,
    )


def _components_and_boundaries(cdx: dict, architecture: dict) -> str:
    zones = []
    assets: list[str] = []
    blueprints = cdx.get("blueprints") or []
    if blueprints:
        bp = blueprints[0]
        for z in bp.get("zones") or []:
            zones.append(z.get("name") or z.get("bom-ref") or "")
        for a in bp.get("assets") or []:
            assets.append(a.get("name") or a.get("bom-ref") or "")
    aliases = architecture.get("assets") or {}
    zones_txt = ", ".join(sorted(z for z in zones if z)) or "(no zones defined)"
    assets_txt = ", ".join(sorted(a for a in assets if a)) or "(no assets defined)"
    alias_txt = (
        f"Named asset aliases: {', '.join(sorted(aliases.keys()))}." if aliases else ""
    )
    return _paragraph(
        f"Authorization boundary zones: {zones_txt}.",
        f"Components: {assets_txt}.",
        alias_txt,
    )


def _network_architecture(cdx: dict, architecture: dict) -> str:
    # `flow_overrides` is keyed by the semantic flow alias declared in
    # `architecture.yaml`'s `flows:` map (e.g. "storage_to_warehouse"),
    # not by the CDX flow's `bom-ref` (e.g. "flow-flow-1") — those namespaces
    # never intersect. Join via the (source-asset-bomref, destination-asset-
    # bomref) pair instead, which uniquely identifies every override except
    # one duplicate pair; ties are broken by matching the flow `name`.
    assets = architecture.get("assets") or {}
    flow_overrides = architecture.get("flow_overrides") or {}
    override_by_endpoints: dict[tuple[str, str], list[tuple[str, dict]]] = {}
    for alias, spec in (architecture.get("flows") or {}).items():
        src = assets.get(spec.get("source_asset"))
        dst = assets.get(spec.get("destination_asset"))
        if not src or not dst:
            continue
        override_by_endpoints.setdefault((src, dst), []).append(
            (spec.get("name") or "", flow_overrides.get(alias) or {})
        )

    flows_seen: list[tuple[str, str, str, str]] = []
    for bp in cdx.get("blueprints") or []:
        for flow in bp.get("flows") or []:
            ref = flow.get("bom-ref") or ""
            name = flow.get("name") or ""
            candidates = (
                override_by_endpoints.get((flow.get("source"), flow.get("destination")))
                or []
            )
            override = {}
            if len(candidates) == 1:
                override = candidates[0][1]
            elif candidates:
                override = next(
                    (o for n, o in candidates if n == name), candidates[0][1]
                )
            protocols = ", ".join(override.get("protocols") or []) or "unspecified"
            auth = override.get("authMechanism") or "unspecified"
            crosses = "yes" if override.get("crosses_trust_zone") else "no"
            flows_seen.append((name or ref, protocols, auth, crosses))
    if not flows_seen:
        return "Network architecture: no flows enumerated in model/base-dfd.cdx.json."
    lines = [
        f"- {name}: protocols={protocols}; auth={auth}; crosses trust zone={crosses}"
        for name, protocols, auth, crosses in flows_seen
    ]
    return _paragraph(
        "Network architecture (from model/base-dfd.cdx.json + architecture.yaml flow_overrides):",
        "\n".join(lines),
    )


def _data_flow(cdx: dict, architecture: dict) -> str:
    assets = {
        asset.get("bom-ref"): asset.get("name") or asset.get("bom-ref")
        for blueprint in cdx.get("blueprints") or []
        for asset in blueprint.get("assets") or []
    }
    asset_refs = architecture.get("assets") or {}
    overrides = architecture.get("flow_overrides") or {}
    endpoint_resolution = (architecture.get("context_projection") or {}).get(
        "flow_override_resolution"
    ) == "endpoints"
    override_by_endpoints: dict[tuple[str, str], list[tuple[str, dict]]] = {}
    override_by_name: dict[str, dict] = {}
    for alias, spec in (architecture.get("flows") or {}).items():
        if spec.get("name"):
            override_by_name[spec["name"]] = overrides.get(alias) or {}
        source = asset_refs.get(spec.get("source_asset"))
        destination = asset_refs.get(spec.get("destination_asset"))
        if endpoint_resolution and source and destination:
            override_by_endpoints.setdefault((source, destination), []).append(
                (spec.get("name") or "", overrides.get(alias) or {})
            )
    lines = []
    for blueprint in cdx.get("blueprints") or []:
        for flow in blueprint.get("flows") or []:
            candidates = override_by_endpoints.get(
                (flow.get("source"), flow.get("destination")), []
            )
            override = {}
            if len(candidates) == 1:
                override = candidates[0][1]
            elif candidates:
                override = next(
                    (item for name, item in candidates if name == flow.get("name")),
                    candidates[0][1],
                )
            elif flow.get("name"):
                override = override_by_name.get(flow["name"]) or {}
            source = assets.get(flow.get("source"), flow.get("source") or "unresolved")
            destination = assets.get(
                flow.get("destination"), flow.get("destination") or "unresolved"
            )
            protocols = ", ".join(override.get("protocols") or []) or "unresolved"
            auth = override.get("authMechanism") or "unresolved"
            classification = override.get("data_classification") or "unresolved"
            lines.append(
                f"- {flow.get('name') or flow.get('bom-ref')}: {source} to "
                f"{destination}; protocols={protocols}; authentication={auth}; "
                f"classification={classification}."
            )
    return _paragraph(
        "The following governed flows are represented in Figure 10-1:",
        "\n".join(lines),
    )


# Sentinel used when a slot has no source content in the system YAML.
TBD_SENTINEL = "TBD — populate this section in sidecar.yaml under `sspp:`."


def _tabulate(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    """Render `rows` (list of dicts) as a simple text table using `columns` [(key,label)]."""
    if not rows:
        return TBD_SENTINEL
    header = " | ".join(label for _, label in columns)
    body = [
        " | ".join(str(row.get(key, "")) or "-" for key, _ in columns) for row in rows
    ]
    return "\n".join([header, "-" * len(header), *body])


def _encrypted_flag(row: dict) -> str:
    """Y / N / Unresolved for the SSPP table's Encrypted column.

    An explicit boolean ``encrypted`` key wins. Otherwise fall back to the
    ``encryption`` prose, which must not be treated as "yes" merely by being
    non-empty: a row may legitimately record ``None - loopback only`` (an
    internal, non-boundary-crossing hop) or an ``UNRESOLVED:`` transport that a
    reviewer still has to confirm. Rendering either as ``Y`` would assert
    encryption the system does not have (SC-8).
    """
    explicit = row.get("encrypted")
    if isinstance(explicit, bool):
        return "Y" if explicit else "N"
    prose = str(row.get("encryption") or "").strip()
    if not prose:
        return "N"
    head = prose.lower()
    if head.startswith(("none", "no", "plaintext", "unencrypted")):
        return "N"
    if head.startswith(("unresolved", "unknown", "tbd")):
        return "Unresolved"
    return "Y"


def _ports_protocols_services(sidecar: dict) -> str:
    sdr = sidecar.get("sdr") or {}
    rows = sdr.get("ports_and_protocols") or []
    normalized = [
        {
            "direction": r.get("direction", "Outbound"),
            "boundary": r.get("boundary_crossing", "Y"),
            "source": r.get("source", "-"),
            "destination": r.get("destination", "-"),
            "ports": f"{r.get('port_number', '')} ({r.get('transport_protocol', 'T')[:1]})",
            "protocols": r.get("transport_protocol", "-"),
            "services": r.get("service_name", "-"),
            "purpose": r.get("purpose", "-"),
            "encrypted": _encrypted_flag(r),
            "sensitivity": r.get("data_sensitivity", "-"),
        }
        for r in rows
    ]
    return _tabulate(
        normalized,
        [
            ("direction", "Direction"),
            ("boundary", "Boundary"),
            ("source", "Source"),
            ("destination", "Destination"),
            ("ports", "Ports"),
            ("protocols", "Protocols"),
            ("services", "Services"),
            ("purpose", "Purpose"),
            ("encrypted", "Encrypted"),
            ("sensitivity", "Data Sensitivity"),
        ],
    )


def _external_services(sidecar: dict) -> str:
    rows = (sidecar.get("sspp") or {}).get("external_services") or []
    return _tabulate(
        rows,
        [
            ("name", "Service"),
            ("provider", "Provider"),
            ("purpose", "Purpose"),
            ("fedramp", "FedRAMP"),
        ],
    )


def _operating_systems_table(sidecar: dict) -> str:
    rows = (sidecar.get("sspp") or {}).get("operating_systems") or []
    return _tabulate(
        rows,
        [
            ("name", "OS / App"),
            ("version", "Version"),
            ("count", "Count"),
            ("purpose", "Purpose"),
        ],
    )


def _aws_services_table(sidecar: dict) -> str:
    rows = (sidecar.get("sspp") or {}).get("aws_services") or []
    return _tabulate(
        rows,
        [
            ("name", "AWS Service"),
            ("approval", "Approval Status"),
            ("description", "Use"),
        ],
    )


def _interconnections(sidecar: dict) -> str:
    rows = (sidecar.get("sspp") or {}).get("interconnections") or []
    if not rows:
        return TBD_SENTINEL
    high = _tabulate(
        rows,
        [
            ("system", "System"),
            ("organization", "Organization"),
            ("type", "Type"),
            ("agreement_type", "Agreement"),
            ("agreement_date", "Date"),
            ("fips199", "FIPS 199"),
            ("ao_name", "AO"),
        ],
    )
    detail = _tabulate(
        rows,
        [
            ("system", "System"),
            ("poc", "POC"),
            ("connection_security", "Connection Security"),
            ("data_direction", "Data Direction"),
            ("information", "Information"),
            ("ports", "Ports/Circuit"),
        ],
    )
    # Labels intentionally omitted: the Moderate SSPP template already prints
    # "Table 11-1. System Interconnections" and "Table 11-2. Connection Details
    # of Interconnected Systems" directly above the two blank Word tables.
    return _paragraph(high, detail)


def _sspp_field(sidecar: dict, key: str, default: str | None = None) -> str:
    """Fetch a scalar/paragraph field from sidecar.sspp.<key>, falling back to TBD."""
    val = (sidecar.get("sspp") or {}).get(key)
    if val is None or val == "":
        return default if default is not None else TBD_SENTINEL
    return str(val).strip()


# Narrative fields that project directly from `sidecar.sspp.<tag>` with TBD fallback.
_SIMPLE_SSPP_TAGS = (
    "signatures",
    "digital_identity_acceptance",
    "controls_summary",
    "urls_and_hosting",
    "user_roles_and_sensitivity",
    "privileged_authentication",
    "non_privileged_authentication",
    "network_diagram",
    "data_flow_diagram",
    "devsecops_code_management",
    "devsecops_infrastructure",
    "devsecops_pipeline",
    "devsecops_static_analysis",
    "devsecops_dependency_scanning",
    "devsecops_ami_process",
    "devsecops_secrets_management",
    "devsecops_artifact_management",
    "devsecops_change_management",
    "microservices_architecture",
    "containers_build",
    "containers_vulnerability_scan",
    "containers_registry",
    "containers_image_development",
    "containers_logging",
    "containers_infrastructure",
    "containers_least_privilege",
    "containers_network_security",
    "containers_orchestrator",
    "containers_monitoring",
    "aws_iam",
    "aws_account_strategy",
    "aws_ha_strategy",
    "aws_security_groups",
    "aws_encryption_in_transit",
    "aws_encryption_at_rest",
    "aws_s3_bucket_security",
    "aws_kms_key_management",
    "aws_account_provisioning",
    "aws_cloud_native_security",
    "aws_baseline_security",
)


def build_typed_context(root: Path) -> DocxContext:
    """Return typed template fields with provenance and readiness metadata."""
    root = Path(root)
    manifest = _load_yaml(root / "system.yaml")
    sidecar = _load_yaml(root / "sidecar.yaml")
    docx_source = _load_yaml(root / "docx.yaml")
    architecture = _load_yaml(root / "model" / "architecture.yaml")
    cdx = _load_json(root / "model" / "base-dfd.cdx.json")

    blueprints = cdx.get("blueprints") or []
    fields = {
        "general_system_description": ContextField(
            _general_system_description(manifest, sidecar),
            source_paths=("system.yaml", "sidecar.yaml#/cpo"),
            source_records=2,
        ),
        "components_and_boundaries": ContextField(
            _components_and_boundaries(cdx, architecture),
            source_paths=(
                "model/base-dfd.cdx.json#/blueprints/0",
                "model/architecture.yaml#/assets",
            ),
            source_records=sum(len(bp.get("assets") or []) for bp in blueprints),
        ),
        "network_architecture": ContextField(
            _network_architecture(cdx, architecture),
            source_paths=(
                "model/base-dfd.cdx.json#/blueprints/0/flows",
                "model/architecture.yaml#/flow_overrides",
            ),
            source_records=sum(len(bp.get("flows") or []) for bp in blueprints),
        ),
        "data_flow": ContextField(
            _data_flow(cdx, architecture),
            source_paths=(
                "model/base-dfd.cdx.json#/blueprints/0/flows",
                "model/architecture.yaml#/flow_overrides",
            ),
            source_records=sum(len(bp.get("flows") or []) for bp in blueprints),
        ),
        "ports_protocols_services": ContextField(
            "See Table 10-4 for the structured ports, protocols, and services inventory.",
            source_paths=("docx.yaml#/chapters/ports_protocols_services",),
        ),
        "external_services": ContextField(
            "See Table 10-2 for external-service dispositions.",
            source_paths=("docx.yaml#/chapters/inventories",),
        ),
        "operating_systems_table": ContextField(
            "See Table 10-3 for the structured software inventory.",
            source_paths=("docx.yaml#/chapters/inventories/software",),
        ),
        "aws_services_table": ContextField(
            "See Table 10-5 for AWS service use and approval dispositions.",
            source_paths=("docx.yaml#/chapters/inventories/aws_services",),
        ),
        "interconnections": ContextField(
            "See Tables 11-1 and 11-2 for structured interconnection records.",
            source_paths=("docx.yaml#/chapters/interconnections",),
        ),
    }
    narratives = (docx_source.get("chapters") or {}).get("narratives") or {}
    for tag in _SIMPLE_SSPP_TAGS:
        value = narratives.get(tag) or _sspp_field(sidecar, tag)
        fields[tag] = ContextField(
            value,
            source_paths=(f"sidecar.yaml#/sspp/{tag}",),
            source_records=1 if value != TBD_SENTINEL else 0,
        )
    return DocxContext(fields)


def build_context(root: Path) -> dict[str, str]:
    """Return the backwards-compatible ``{templateTag: value}`` fill context."""
    return build_typed_context(root).render_context()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "system_dir", type=Path, help="System directory holding system.yaml"
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write context JSON to this path (default: stdout).",
    )
    ap.add_argument(
        "--readiness-out",
        type=Path,
        help="Write typed field provenance and readiness metrics to this path.",
    )
    args = ap.parse_args(argv)

    root = args.system_dir
    if not (root / "system.yaml").is_file():
        ap.error(f"no manifest at {root / 'system.yaml'}")

    typed_context = build_typed_context(root)
    context = typed_context.render_context()
    payload = json.dumps(context, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n")
        print(f"wrote {args.out} ({len(context)} tags)")
    else:
        sys.stdout.write(payload + "\n")
    if args.readiness_out:
        args.readiness_out.parent.mkdir(parents=True, exist_ok=True)
        args.readiness_out.write_text(
            json.dumps(typed_context.readiness_report(), indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
