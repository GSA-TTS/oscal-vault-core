#!/usr/bin/env python3
"""Export canonical JSON sidecars into strict NIST OSCAL JSON/YAML.

For note types that map to recognized OSCAL models (`oscal.ssp`,
`oscal.component_definition`, `oscal.poam`, etc.), this script generates
standards-conformant OSCAL documents in `.exports/oscal-json/`.

Usage:
    # Export all exportable types from all vaults
    python3 compliance/scripts/export_oscal.py

    # Export specific vault
    python3 compliance/scripts/export_oscal.py --vault examples/vault/<system>/

    # Export as YAML instead of JSON
    python3 compliance/scripts/export_oscal.py --format yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

import jsonschema

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.export_oscal import (  # noqa: E402
    DEFAULT_OSCAL_VERSION,
    ExportError,
    canonical_to_oscal_component_definition,
    canonical_to_oscal_ssp,
    get_exportable_types,
    load_canonical_json,
)
from compliance.lib.validate_oscal import (  # noqa: E402
    SUPPORTED_OSCAL_VERSIONS,
    SchemaNotAvailableError,
    validate_oscal_document,
)

# Optional YAML support
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _collect_sidecars_by_type(
    vault_dir: Path,
) -> tuple[dict, list[dict[str, str]]]:
    """Collect canonical JSON sidecars grouped by OSCAL type.

    Every sidecar gets exactly one report entry. A sidecar that cannot be
    parsed, is not a JSON object, or carries no recognized type is reported as
    ``error`` / ``untyped`` rather than skipped, so collection cannot drop a
    note without the caller seeing it.
    """
    current_root = vault_dir / ".snapshots" / "current"
    if not current_root.is_dir():
        return {}, []

    exportable = set(get_exportable_types())
    grouped: dict = {}
    report: list[dict[str, str]] = []

    # Check for plan/system notes (oscal.ssp) and component notes
    for json_path in sorted(current_root.rglob("*.json")):
        try:
            data = load_canonical_json(json_path)
        except Exception as exc:
            report.append(
                {"path": str(json_path), "status": "error", "error": str(exc)}
            )
            continue

        if not isinstance(data, dict):
            report.append(
                {
                    "path": str(json_path),
                    "status": "error",
                    "error": (
                        "canonical sidecar is not a JSON object "
                        f"(found {type(data).__name__})"
                    ),
                }
            )
            continue

        note_type = data.get("type") or data.get("note_type", "")
        if not note_type:
            # An untyped sidecar is a generator defect, not something to skip:
            # the generator only writes sidecars for typed notes.
            report.append(
                {
                    "path": str(json_path),
                    "status": "error",
                    "error": "canonical sidecar declares no type/note_type",
                }
            )
            continue

        if note_type in exportable:
            grouped.setdefault(note_type, []).append((json_path, data))
            report.append({"path": str(json_path), "status": "exportable"})
        elif note_type == "oscal.control":
            grouped.setdefault("oscal.control", []).append((json_path, data))
            report.append({"path": str(json_path), "status": "control"})
        elif note_type == "oscal.party":
            # Parties are not a standalone export, but they feed the SSP's
            # metadata.parties[] / responsible-parties[] (D-1 / #38).
            grouped.setdefault("oscal.party", []).append((json_path, data))
            report.append({"path": str(json_path), "status": "party"})
        elif isinstance(note_type, str) and note_type.startswith("oscal.x."):
            # Extension notes are mapped onto NATIVE OSCAL assemblies by the
            # exporter (network-architecture, data-flow, protocols, diagrams,
            # leveraged-authorizations), not carried as custom extensions.
            grouped.setdefault("oscal.x", []).append((json_path, data))
            report.append(
                {
                    "path": str(json_path),
                    "status": "extension-native",
                    "note_type": str(note_type),
                }
            )
        else:
            # okf.* infrastructure notes have no standalone OSCAL model.
            report.append(
                {
                    "path": str(json_path),
                    "status": "not-exportable",
                    "note_type": str(note_type),
                }
            )

    return grouped, report


def _export_ssp(
    plan_data: dict,
    plan_id: str,
    control_sidecars: List[tuple],
    output_dir: Path,
    fmt: str = "json",
    system_data: dict | None = None,
    party_sidecars: List[tuple] | None = None,
    extension_sidecars: List[tuple] | None = None,
    oscal_version: str = DEFAULT_OSCAL_VERSION,
    allow_synthetic: bool = False,
) -> int:
    """Export a system security plan with a full implemented-requirements graph."""
    controls = [cdata for _, cdata in control_sidecars]
    parties = [pdata for _, pdata in (party_sidecars or [])]
    extensions = [edata for _, edata in (extension_sidecars or [])]
    runtime_derivations: List[dict] = []
    runtime_events: List[dict] = []
    preserved_extensions: List[dict] = []
    ssp = canonical_to_oscal_ssp(
        plan_data,
        plan_id,
        control_sidecars=controls,
        system_data=system_data,
        party_sidecars=parties,
        extension_sidecars=extensions,
        oscal_version=oscal_version,
        allow_synthetic=allow_synthetic,
        lineage_events=runtime_events,
        preserved_extensions=preserved_extensions,
        lineage_derivations=runtime_derivations,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    if fmt == "yaml" and _HAS_YAML:
        output_path = output_dir / f"{plan_id}-ssp.yaml"
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(ssp, f, sort_keys=False, allow_unicode=True)
    else:
        output_path = output_dir / f"{plan_id}-ssp.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(ssp, f, indent=2, sort_keys=True)
    from compliance.lib.oscal_lineage import (
        export_lineage_report,
        validate_runtime_derivations,
    )

    contract = _REPO_ROOT / "compliance/datacontracts/fedramp-ssp-v1.yaml"
    lineage = export_lineage_report(contract, runtime_derivations=runtime_derivations)
    lineage["runtime_events"] = runtime_events
    lineage["preserved_extensions"] = preserved_extensions
    findings = validate_runtime_derivations(lineage, runtime_derivations)
    if findings:
        raise ValueError("; ".join(findings))
    lineage_schema = json.loads(
        (
            _REPO_ROOT / "compliance/schemas/lineage/oscal_lineage_schema_v1.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.validate(lineage, lineage_schema)
    lineage_dir = output_dir.parent / "lineage"
    lineage_dir.mkdir(parents=True, exist_ok=True)
    lineage_path = lineage_dir / f"{plan_id}-ssp-lineage.json"
    lineage_path.write_text(
        json.dumps(lineage, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 1


def _controls_for_component(
    component_data: dict, control_sidecars: List[tuple]
) -> List[dict]:
    """Select controls linked to a component without duplicating an index.

    Vault control notes already carry ``component`` as a wikilink/name; using it
    here makes ``component-definition.capabilities`` a projection of the
    existing authoring graph rather than a second ``implements_controls`` list
    that can drift. A synthetic system component has no explicit component
    links, so it deliberately receives no capability declaration.
    """
    import re

    fm = component_data.get("frontmatter", component_data)
    name = str(fm.get("name") or fm.get("title") or "").strip()
    if not name:
        return []

    def normalized(value: object) -> str:
        text = str(value or "").strip()
        match = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", text)
        if match:
            text = match.group(2) or match.group(1).rsplit("/", 1)[-1]
        return re.sub(r"[^a-z0-9]+", "", text.lower())

    target = normalized(name)
    selected: List[dict] = []
    for _, control in control_sidecars:
        cfm = control.get("frontmatter", control)
        if normalized(cfm.get("component")) == target:
            selected.append(control)
    return selected


def _load_export_policy(vault_dir: Path) -> tuple[dict, list[dict[str, str]]]:
    """Load a vault's committed export policy, if any.

    ``--allow-synthetic`` is a process-wide development switch, which is the
    wrong granularity for a repository that holds many vaults: one vault that
    legitimately contains zero controls (for example a vault generated from a
    FIPS-199 categorization document rather than an SSP) would otherwise force
    every other vault's fail-closed gate off.

    A vault therefore declares its own exception in
    ``<vault>/.export-policy.json``::

        {
          "allow_synthetic": true,
          "rationale": "source document is a FIPS-199 categorization, not an SSP"
        }

    The rationale is mandatory so the exception is reviewable in git rather than
    inferred from a green run. An unreadable or incoherent policy file is an
    error, not a reason to fall back to permissive behavior.
    """
    policy_path = vault_dir / ".export-policy.json"
    if not policy_path.is_file():
        return {}, []
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, [{"path": str(policy_path), "status": "error", "error": str(exc)}]
    if not isinstance(policy, dict):
        return {}, [
            {
                "path": str(policy_path),
                "status": "error",
                "error": "export policy must be a JSON object",
            }
        ]
    if policy.get("allow_synthetic") and not str(policy.get("rationale", "")).strip():
        return {}, [
            {
                "path": str(policy_path),
                "status": "error",
                "error": "allow_synthetic requires a non-empty rationale",
            }
        ]
    return policy, [
        {
            "path": str(policy_path),
            "status": "policy",
            "allow_synthetic": str(bool(policy.get("allow_synthetic"))),
            "rationale": str(policy.get("rationale", "")),
        }
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export vault notes as strict NIST OSCAL JSON/YAML"
    )
    parser.add_argument(
        "--vault",
        action="append",
        default=None,
        help="Path to vault root (repeatable)",
    )
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Permit a development-only synthetic requirement when a plan "
        "converts zero controls, for every vault in this run. Prefer a "
        "committed per-vault <vault>/.export-policy.json instead.",
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        help="Write per-sidecar collection and export outcomes as JSON.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "yaml"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate each emitted OSCAL document against the NIST schema "
        "(Layer 1); exit non-zero if any document is invalid.",
    )
    parser.add_argument(
        "--oscal-version",
        default=DEFAULT_OSCAL_VERSION,
        choices=SUPPORTED_OSCAL_VERSIONS,
        help=f"OSCAL version to emit + validate against (default: {DEFAULT_OSCAL_VERSION}). "
        "Older FedRAMP/agency portals may still require a 1.0.x baseline.",
    )
    args = parser.parse_args(argv)

    if args.format == "yaml" and not _HAS_YAML:
        print("ERROR: PyYAML is required for YAML export", file=sys.stderr)
        return 1

    vault_dirs: List[Path] = []
    if args.vault:
        for v in args.vault:
            vd = Path(v).resolve()
            if not vd.is_dir():
                print(f"ERROR: Vault {vd} does not exist", file=sys.stderr)
                return 1
            vault_dirs.append(vd)
    else:
        examples_vault = Path.cwd() / "examples" / "vault"
        if examples_vault.is_dir():
            for entry in sorted(examples_vault.iterdir()):
                if entry.is_dir() and not entry.name.startswith("."):
                    vault_dirs.append(entry.resolve())

    if not vault_dirs:
        print("No vault directories to process.", file=sys.stderr)
        return 0

    total_exported = 0
    report: list[dict[str, str]] = []
    failed_vaults: list[str] = []

    for vault_dir in vault_dirs:
        exports_dir = vault_dir / ".exports" / "oscal-json"
        policy, policy_report = _load_export_policy(vault_dir)
        report.extend(policy_report)
        if any(item["status"] == "error" for item in policy_report):
            failed_vaults.append(vault_dir.name)
            for item in policy_report:
                if item["status"] == "error":
                    print(f"FAIL {item['path']}: {item['error']}", file=sys.stderr)
            _clear_exports(exports_dir)
            continue
        allow_synthetic = args.allow_synthetic or bool(policy.get("allow_synthetic"))

        grouped, sidecar_report = _collect_sidecars_by_type(vault_dir)
        report.extend(sidecar_report)
        sidecar_errors = [item for item in sidecar_report if item["status"] == "error"]
        if sidecar_errors:
            failed_vaults.append(vault_dir.name)
            for item in sidecar_errors:
                print(
                    f"FAIL {item['path']}: {item.get('error', 'unreadable sidecar')}",
                    file=sys.stderr,
                )
            # Leaving a previous run's exports in place would let a later
            # validation pass read stale, now-unsupported documents and report
            # success. Clear them so a failed vault has no exports at all.
            _clear_exports(exports_dir)
            continue

        vault_count = 0
        vault_failed = False

        # Export SSPs with their controls. Both plan and system notes carry
        # type: oscal.ssp; disambiguate via note_type so a plan is exported
        # with its matching system note supplying impact levels / boundary.
        if "oscal.ssp" in grouped:
            ssp_items = grouped["oscal.ssp"]
            control_items = grouped.get("oscal.control", [])
            party_items = grouped.get("oscal.party", [])
            extension_items = grouped.get("oscal.x", [])

            plan_items = [
                (p, d) for p, d in ssp_items if d.get("note_type") != "system"
            ]
            system_items = [
                (p, d) for p, d in ssp_items if d.get("note_type") == "system"
            ]
            # Prefer a system note as the characteristics source when present.
            system_data = system_items[0][1] if system_items else None

            # If the only oscal.ssp notes are system notes (no explicit plan
            # note), still emit one SSP from the system note itself.
            if not plan_items and system_items:
                plan_items = [system_items[0]]

            for plan_path, plan_data in plan_items:
                plan_id = (
                    plan_data.get("plan_id")
                    or plan_data.get("system_id")
                    or vault_dir.name
                )
                try:
                    vault_count += _export_ssp(
                        plan_data,
                        plan_id,
                        control_items,
                        exports_dir,
                        args.format,
                        system_data=system_data,
                        party_sidecars=party_items,
                        extension_sidecars=extension_items,
                        oscal_version=args.oscal_version,
                        allow_synthetic=allow_synthetic,
                    )
                    report.append(
                        {
                            "path": str(plan_path),
                            "status": "exported",
                            "plan_id": str(plan_id),
                            "controls": str(len(control_items)),
                        }
                    )
                except ExportError as exc:
                    vault_failed = True
                    report.append(
                        {
                            "path": str(plan_path),
                            "status": "error",
                            "plan_id": str(plan_id),
                            "error": str(exc),
                        }
                    )
                    print(f"FAIL {plan_path}: {exc}", file=sys.stderr)

        # A vault whose SSP failed must not publish a partial document set: a
        # component-definition without its SSP is exactly the kind of quietly
        # incomplete export this gate exists to prevent. Failure is scoped to
        # this vault so one bad vault cannot suppress the next one's exports.
        if vault_failed:
            failed_vaults.append(vault_dir.name)
            _clear_exports(exports_dir)
            continue

        # Export component definitions
        if "oscal.component_definition" in grouped:
            for comp_path, comp_data in grouped["oscal.component_definition"]:
                try:
                    component_controls = _controls_for_component(
                        comp_data, grouped.get("oscal.control", [])
                    )
                    cd = canonical_to_oscal_component_definition(
                        comp_data,
                        control_sidecars=component_controls,
                        oscal_version=args.oscal_version,
                    )
                except Exception as exc:
                    vault_failed = True
                    report.append(
                        {
                            "path": str(comp_path),
                            "status": "error",
                            "error": str(exc),
                        }
                    )
                    print(f"FAIL {comp_path}: {exc}", file=sys.stderr)
                    continue
                comp_name = comp_data.get("name") or comp_data.get("title", "component")
                exports_dir.mkdir(parents=True, exist_ok=True)

                if args.format == "yaml" and _HAS_YAML:
                    out_path = exports_dir / f"{comp_name}-component.yaml"
                    with open(out_path, "w", encoding="utf-8") as f:
                        yaml.dump(cd, f, sort_keys=False, allow_unicode=True)
                else:
                    out_path = exports_dir / f"{comp_name}-component.json"
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(cd, f, indent=2, sort_keys=True)
                from compliance.lib.oscal_lineage import (
                    component_definition_lineage,
                    export_lineage_report,
                )

                component_runtime = component_definition_lineage(comp_data, cd)
                lineage = export_lineage_report(
                    _REPO_ROOT / "compliance/datacontracts/fedramp-ssp-v1.yaml",
                    runtime_derivations=[],
                )
                lineage["component_value_lineage"] = component_runtime
                lineage_schema = json.loads(
                    (
                        _REPO_ROOT
                        / "compliance/schemas/lineage/oscal_lineage_schema_v1.json"
                    ).read_text(encoding="utf-8")
                )
                jsonschema.validate(lineage, lineage_schema)
                lineage_dir = exports_dir.parent / "lineage"
                lineage_dir.mkdir(parents=True, exist_ok=True)
                lineage_path = lineage_dir / f"{comp_name}-component-lineage.json"
                lineage_path.write_text(
                    json.dumps(lineage, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                report.append({"path": str(comp_path), "status": "exported"})
                vault_count += 1

        if vault_failed:
            failed_vaults.append(vault_dir.name)
            _clear_exports(exports_dir)
            continue

        print(f"Exported vault {vault_dir.name}: {vault_count} OSCAL documents")
        total_exported += vault_count

    print(f"\nDone: {total_exported} total OSCAL documents exported.")
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if failed_vaults:
        print(
            f"\nExport FAILED for {len(failed_vaults)} vault(s): "
            f"{', '.join(sorted(set(failed_vaults)))}",
            file=sys.stderr,
        )
        return 1

    if args.validate and args.format == "json":
        invalid = _validate_exports(vault_dirs, args.oscal_version)
        if invalid:
            print(
                f"\nLayer 1 validation: {invalid} document(s) FAILED the NIST schema.",
                file=sys.stderr,
            )
            return 1
        print("\nLayer 1 validation: all exported documents are schema-valid.")

    return 0


def _clear_exports(exports_dir: Path) -> None:
    """Remove previously exported OSCAL documents for a failed vault.

    Without this, a vault that fails today keeps yesterday's exports on disk,
    and the downstream schema/oscalkit gates would validate a stale document and
    report success.
    """
    if not exports_dir.is_dir():
        return
    for path in sorted(exports_dir.glob("*.json")) + sorted(exports_dir.glob("*.yaml")):
        path.unlink()


def _validate_exports(vault_dirs: List[Path], oscal_version: str) -> int:
    """Validate every exported JSON doc; return the count of invalid docs."""
    invalid = 0
    for vault_dir in vault_dirs:
        exports = vault_dir / ".exports" / "oscal-json"
        if not exports.is_dir():
            continue
        for path in sorted(exports.rglob("*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    doc = json.load(f)
                result = validate_oscal_document(
                    doc, oscal_version=oscal_version, source=str(path)
                )
            except (SchemaNotAvailableError, ValueError) as e:
                invalid += 1
                print(f"FAIL {path}: {e}", file=sys.stderr)
                continue
            if not result.valid:
                invalid += 1
                print(f"FAIL [{result.document_type}] {path}", file=sys.stderr)
                for err in result.errors[:10]:
                    print(f"       - {err}", file=sys.stderr)
    return invalid


if __name__ == "__main__":
    sys.exit(main())
