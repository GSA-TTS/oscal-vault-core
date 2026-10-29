#!/usr/bin/env python3
"""Report which OSCAL schema elements the exporter uses vs leaves unused.

For the ``system-security-plan`` and ``component-definition`` models this walks
the vendored NIST JSON Schema to enumerate every assembly field, then exports a
representative document from a committed vault and records which schema fields
actually appear in the output. The result is an evidence-based coverage report
(USED / UNUSED per field) rather than a hand-maintained list that drifts.

Usage::

    # human-readable
    python compliance/scripts/oscal_schema_coverage.py

    # write the committed YAML report
    python compliance/scripts/oscal_schema_coverage.py \
        --yaml compliance/datacontracts/oscal-schema-coverage.yaml

    # CI drift gate: fail if the committed report is stale
    python compliance/scripts/oscal_schema_coverage.py \
        --yaml compliance/datacontracts/oscal-schema-coverage.yaml --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_DIR = REPO_ROOT / "compliance" / "schemas" / "oscal-1.1.3"

_MODELS = {
    "system-security-plan": {
        "schema": "oscal_ssp_schema.json",
        "root_def": "oscal-ssp-oscal-ssp:system-security-plan",
    },
    "component-definition": {
        "schema": "oscal_component_schema.json",
        "root_def": (
            "oscal-component-definition-oscal-component-definition:component-definition"
        ),
    },
}

# Fields present in every assembly that are not interesting for a coverage
# audit (they are generic OSCAL plumbing). Reported but flagged low-signal.
_GENERIC_FIELDS = {"props", "links", "remarks", "annotations"}

# Disposition of each UNUSED field: whether the data already lives somewhere in
# the pipeline (so the field is wire-able) or is genuinely absent. The key is a
# dotted path PREFIX; the longest matching prefix wins. Authoring-extension
# dispositions identify their retained oscal.x.* source, not custom OSCAL output.
#
#   authoring_extension:<note>  captured in a named oscal.x.* note; wire-able
#   prop_backed              emitted as a prop today, not the structured field
#   genuinely_absent         no source anywhere in the pipeline
_UNUSED_DISPOSITIONS: Dict[str, Dict[str, str]] = {
    "system-security-plan": {
        "system-characteristics.network-architecture": "authoring_extension:oscal.x.boundary",
        "system-characteristics.data-flow": "authoring_extension:oscal.x.relationship",
        "system-characteristics.authorization-boundary.diagrams": "authoring_extension:oscal.x.diagram",
        "system-implementation.leveraged-authorizations": "authoring_extension:oscal.x.inheritance",
        "system-implementation.components.protocols": "authoring_extension:oscal.x.relationship",
        "system-implementation.users.authorized-privileges": "authoring_extension:oscal.x.boundary",
        "control-implementation.implemented-requirements.by-components.export": "authoring_extension:oscal.x.crm",
        "back-matter": "authoring_extension:oscal.x.diagram",
        "system-implementation.inventory-items": "prop_backed",
        "metadata.revisions": "genuinely_absent",
        "metadata.locations": "genuinely_absent",
        "metadata.document-ids": "genuinely_absent",
        "metadata.published": "genuinely_absent",
    },
    "component-definition": {
        "components.control-implementations": "prop_backed",
        "components.protocols": "authoring_extension:oscal.x.relationship",
        "components.responsible-roles": "prop_backed",
        "capabilities": "genuinely_absent",
        "back-matter": "authoring_extension:oscal.x.diagram",
        "import-component-definitions": "genuinely_absent",
    },
}


def _classify_unused(model: str, path: str) -> str:
    """Return the disposition of an unused field by longest-prefix match."""
    table = _UNUSED_DISPOSITIONS.get(model, {})
    best = ""
    for prefix in table:
        if (path == prefix or path.startswith(prefix + ".")) and len(prefix) > len(
            best
        ):
            best = prefix
    if best:
        return table[best]
    # Default: generic plumbing fields are treated as not-yet-wired rather than
    # genuinely absent, since most could carry data if a parent were wired.
    return "unclassified"


def _load_schema(name: str) -> Dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _resolve(defs: Dict[str, Any], node: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a one-level ``$ref`` into the definitions table."""
    ref = node.get("$ref")
    if ref and ref.startswith("#/definitions/"):
        return defs.get(ref.split("/")[-1], {})
    return node


def enumerate_fields(
    defs: Dict[str, Any],
    def_name: str,
    *,
    prefix: str = "",
    seen: Set[str] | None = None,
    max_depth: int = 4,
    depth: int = 0,
) -> List[str]:
    """Return dotted field paths for an assembly, descending into sub-assemblies.

    Recursion is bounded and cycle-guarded so deeply self-referential OSCAL
    assemblies (e.g. parts) do not loop. Array fields descend into their item
    schema under the same path (OSCAL arrays of assemblies).
    """
    seen = seen if seen is not None else set()
    if def_name in seen or depth > max_depth:
        return [prefix.rstrip(".")] if prefix else []
    seen = seen | {def_name}

    node = defs.get(def_name, {})
    props = node.get("properties")
    if not props:
        return [prefix.rstrip(".")] if prefix else []

    paths: List[str] = []
    for field, schema in sorted(props.items()):
        path = f"{prefix}{field}"
        paths.append(path)
        target = schema
        if target.get("type") == "array" and "items" in target:
            target = target["items"]
        ref = target.get("$ref")
        if ref and ref.startswith("#/definitions/"):
            sub = ref.split("/")[-1]
            if defs.get(sub, {}).get("properties"):
                paths.extend(
                    enumerate_fields(
                        defs,
                        sub,
                        prefix=f"{path}.",
                        seen=seen,
                        max_depth=max_depth,
                        depth=depth + 1,
                    )
                )
    return paths


def _present_paths(node: Any, prefix: str = "") -> Set[str]:
    """Collect every dotted key path that is actually present in a document."""
    present: Set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}{key}"
            present.add(path)
            present |= _present_paths(value, f"{path}.")
    elif isinstance(node, list):
        for item in node:
            present |= _present_paths(item, prefix)
    return present


def _export_sample(model: str, vault_root: Path | None = None) -> Dict[str, Any] | None:
    """Export one representative document for the given model.

    ``vault_root`` is a directory of generated vaults, each with ``.snapshots/current``. It defaults
    to ``./examples/vault`` in the working directory; the core ships none.
    """
    from compliance.lib.export_oscal import (
        canonical_to_oscal_component_definition,
        canonical_to_oscal_ssp,
        load_canonical_json,
    )

    vault_root = vault_root or Path.cwd() / "examples" / "vault"
    if not vault_root.is_dir():
        return None

    for vault in sorted(vault_root.iterdir()):
        current = vault / ".snapshots" / "current"
        if not current.is_dir():
            continue
        plan = None
        system = None
        controls: List[dict] = []
        parties: List[dict] = []
        extensions: List[dict] = []
        component = None
        for path in sorted(current.rglob("*.json")):
            try:
                data = load_canonical_json(path)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            ntype = data.get("type") or data.get("note_type", "")
            if ntype == "oscal.ssp" and data.get("note_type") != "system":
                plan = plan or data
            elif ntype == "oscal.ssp":
                system = system or data
            elif ntype == "oscal.control":
                controls.append(data)
            elif ntype == "oscal.party":
                parties.append(data)
            elif ntype == "oscal.component_definition":
                component = component or data
            elif isinstance(ntype, str) and ntype.startswith("oscal.x."):
                extensions.append(data)

        if model == "system-security-plan" and plan and controls:
            try:
                return canonical_to_oscal_ssp(
                    plan,
                    plan.get("plan_id") or vault.name,
                    control_sidecars=controls,
                    system_data=system,
                    party_sidecars=parties,
                    extension_sidecars=extensions,
                )
            except Exception:
                continue
        if model == "component-definition" and component:
            try:
                return canonical_to_oscal_component_definition(
                    component, control_sidecars=controls
                )
            except Exception:
                continue
    return None


def compute_coverage(model: str, vault_root: Path | None = None) -> Dict[str, Any]:
    spec = _MODELS[model]
    schema = _load_schema(spec["schema"])
    defs = schema["definitions"]
    all_paths = enumerate_fields(defs, spec["root_def"])
    sample = _export_sample(model, vault_root)
    # The exporter returns a wrapper-keyed document ({"system-security-plan":
    # {...}}); unwrap to the root assembly so present paths line up with the
    # schema paths, which are relative to that assembly.
    root_doc = sample.get(model, sample) if isinstance(sample, dict) else None
    present = _present_paths(root_doc) if root_doc else set()
    used: List[str] = []
    unused: List[str] = []
    for path in all_paths:
        (used if path in present else unused).append(path)
    dispositions: Dict[str, str] = {
        path: _classify_unused(model, path) for path in unused
    }
    disposition_counts: Dict[str, int] = {}
    for value in dispositions.values():
        disposition_counts[value] = disposition_counts.get(value, 0) + 1
    return {
        "model": model,
        "schema": spec["schema"],
        "sample_exported": sample is not None,
        "total_fields": len(all_paths),
        "used_count": len(used),
        "unused_count": len(unused),
        "unused_disposition_counts": dict(sorted(disposition_counts.items())),
        "used": used,
        "unused": unused,
        "unused_dispositions": dispositions,
    }


def render_yaml(reports: List[Dict[str, Any]]) -> str:
    import yaml

    doc = {
        "apiVersion": "oscal-vault/oscal-schema-coverage/v1",
        "kind": "OscalSchemaCoverage",
        "description": (
            "Evidence-based inventory of which OSCAL 1.1.3 schema fields the "
            "exporter emits (USED) vs does not (UNUSED), computed by exporting "
            "a representative committed vault and diffing against the vendored "
            "NIST schema. Each unused field carries a disposition: "
            "authoring_extension:<note> (the data already lives in that oscal.x.* "
            "note and is wire-able into native OSCAL), prop_backed (emitted as a "
            "prop today, not the structured assembly), genuinely_absent (no "
            "source anywhere), or unclassified. Regenerate with "
            "compliance/scripts/oscal_schema_coverage.py."
        ),
        "models": {},
    }
    for report in reports:
        doc["models"][report["model"]] = {
            "schema": report["schema"],
            "sample_exported": report["sample_exported"],
            "total_fields": report["total_fields"],
            "used_count": report["used_count"],
            "unused_count": report["unused_count"],
            "unused_disposition_counts": report["unused_disposition_counts"],
            "used": report["used"],
            "unused": report["unused"],
            "unused_dispositions": report["unused_dispositions"],
        }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def render_markdown(reports: List[Dict[str, Any]]) -> str:
    lines = ["# OSCAL 1.1.3 schema element coverage", ""]
    for report in reports:
        pct = (
            100.0 * report["used_count"] / report["total_fields"]
            if report["total_fields"]
            else 0.0
        )
        lines += [
            f"## {report['model']}",
            "",
            f"- schema: `{report['schema']}`",
            f"- sample exported: {report['sample_exported']}",
            f"- fields used: {report['used_count']}/{report['total_fields']} "
            f"({pct:.0f}%)",
            "",
            "### Unused fields",
            "",
        ]
        lines += [f"- `{path}`" for path in report["unused"]]
        lines.append("")
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yaml", type=Path, help="Write/compare the YAML report here.")
    parser.add_argument(
        "--vaults",
        type=Path,
        default=None,
        help="Directory of generated vaults to sample (default: ./examples/vault).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 2 if the committed --yaml report differs from freshly "
        "computed coverage (CI drift gate).",
    )
    args = parser.parse_args(argv)

    reports = [compute_coverage(model, args.vaults) for model in _MODELS]

    if args.yaml:
        rendered = render_yaml(reports)
        if args.check:
            if not args.yaml.is_file():
                print(f"missing coverage report: {args.yaml}", file=sys.stderr)
                return 2
            if args.yaml.read_text(encoding="utf-8") != rendered:
                print(
                    f"OSCAL coverage report is stale: {args.yaml}\n"
                    "Regenerate with: python compliance/scripts/"
                    "oscal_schema_coverage.py --yaml <path>",
                    file=sys.stderr,
                )
                return 2
            print(f"OK: {args.yaml} is up to date.")
            return 0
        args.yaml.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.yaml}", file=sys.stderr)
        return 0

    print(render_markdown(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
