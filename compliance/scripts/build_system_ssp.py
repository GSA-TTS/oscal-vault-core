#!/usr/bin/env python3
"""Project system response YAML into a deterministic OSCAL SSP document."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml

_CORE_ROOT = Path(__file__).resolve().parents[2]
if str(_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT))


# UUIDv5 namespace, derived from a stable name so no opaque literal is needed.
# Callers that must reproduce UUIDs minted under another namespace pass it explicitly.
DEFAULT_UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "urn:oscal-vault-core:system-security-plan"
)


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def control_token(control_id: str) -> str:
    from compliance.lib.control_identity import parse_control_id

    try:
        key = parse_control_id(control_id)
        base = f"{key.family.lower()}-{key.number}"
        return f"{base}.{key.enhancement.lower()}" if key.enhancement else base
    except ValueError:
        return re.sub(r"\(([^)]+)\)", r".\1", control_id.casefold())


def statement_id(control_id: str, part: str | None) -> str:
    token = control_token(control_id)
    if not part:
        return f"{token}_stmt"
    part_token = re.sub(r"[^a-z0-9.]+", "-", part.casefold()).strip("-.")
    return f"{token}_stmt.{part_token}"


def _stable_uuid(namespace: uuid.UUID, system: str, kind: str, value: str) -> str:
    return str(uuid.uuid5(namespace, f"{system}:{kind}:{value}"))


def _metadata(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    return {**defaults, **overrides}


def _evidence_resources(
    namespace: uuid.UUID,
    slug: str,
    countermeasures: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    referenced = {
        mechanism["ref"]
        for countermeasure in countermeasures.get("countermeasures") or []
        for mechanism in countermeasure.get("mechanisms") or []
        if mechanism.get("ref")
    }
    descriptions = evidence.get("evidence") or {}
    links = evidence.get("evidence_links") or {}
    artifacts = evidence.get("evidence_artifacts") or {}
    resource_ids = {
        key: _stable_uuid(namespace, slug, "evidence", key)
        for key in sorted(referenced)
    }
    resources = []
    for key, resource_uuid in resource_ids.items():
        artifact = artifacts.get(key) or {}
        resource: dict[str, Any] = {
            "uuid": resource_uuid,
            "title": artifact.get("title") or key,
            "description": descriptions.get(key)
            or f"Unresolved evidence reference: {key}",
            "props": [{"name": "evidence-key", "value": key}],
        }
        for name in ("artifact_type", "captured_at", "expires_at"):
            if artifact.get(name) is not None:
                resource["props"].append(
                    {"name": name.replace("_", "-"), "value": str(artifact[name])}
                )
        if artifact.get("is_continuous") is not None:
            resource["props"].append(
                {
                    "name": "is-continuous",
                    "value": str(artifact["is_continuous"]).lower(),
                }
            )
        if links.get(key):
            resource["rlinks"] = [{"href": links[key]}]
        resources.append(resource)
    return resource_ids, resources


def build_oscal(
    system: dict[str, Any],
    source: dict[str, Any],
    *,
    sidecar: dict[str, Any] | None = None,
    countermeasures: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    namespace: uuid.UUID = DEFAULT_UUID_NAMESPACE,
) -> dict[str, Any]:
    slug = str(system["slug"])
    name = str(system["name"])
    sensitivity = str(system.get("baseline") or "low").casefold()
    component_uuid = _stable_uuid(namespace, slug, "component", "this-system")
    defaults = source["response_defaults"]
    overrides = source.get("control_metadata") or {}
    parameter_values = ((sidecar or {}).get("sdr") or {}).get("parameter_values") or {}
    countermeasure_by_control = {
        item.get("control_id"): item
        for item in (countermeasures or {}).get("countermeasures") or []
        if item.get("control_id")
    }
    resource_ids, resources = _evidence_resources(
        namespace, slug, countermeasures or {}, evidence or {}
    )
    requirements = []
    for control_id, entries in (source.get("responses") or {}).items():
        metadata = _metadata(defaults, overrides.get(control_id) or {})
        statements = [
            {
                "statement-id": statement_id(control_id, entry.get("part")),
                "uuid": _stable_uuid(
                    namespace,
                    slug,
                    "statement",
                    statement_id(control_id, entry.get("part")),
                ),
                "remarks": entry["implementation"].strip(),
            }
            for entry in entries
        ]
        narrative = "\n\n".join(
            f"Part {entry.get('part')}: {entry['implementation'].strip()}"
            if entry.get("part")
            else entry["implementation"].strip()
            for entry in entries
        )
        requirement = {
            "uuid": _stable_uuid(namespace, slug, "control", control_id),
            "control-id": control_token(control_id),
            "props": [
                {
                    "name": "implementation-status",
                    "value": metadata["implementation_status"],
                }
            ]
            + [
                {"name": "control-origination", "value": value}
                for value in metadata["origination"]
            ],
            "statements": statements,
            "by-components": [
                {
                    "component-uuid": component_uuid,
                    "uuid": _stable_uuid(namespace, slug, "by-component", control_id),
                    "description": narrative,
                    "implementation-status": {
                        "state": metadata["implementation_status"]
                    },
                }
            ],
        }
        roles = metadata.get("responsible_roles") or []
        if roles:
            requirement["responsible-roles"] = [
                {"role-id": re.sub(r"[^a-z0-9-]", "-", role.casefold())}
                for role in roles
            ]
        parameters = parameter_values.get(control_id) or {}
        if parameters:
            requirement["set-parameters"] = [
                {"param-id": parameter_id, "values": [str(value)]}
                for parameter_id, value in parameters.items()
            ]
        evidence_keys = list(
            dict.fromkeys(
                mechanism["ref"]
                for mechanism in (
                    countermeasure_by_control.get(control_id, {}).get("mechanisms")
                    or []
                )
                if mechanism.get("ref")
            )
        )
        if evidence_keys:
            requirement["links"] = [
                {
                    "href": f"#{resource_ids[key]}",
                    "rel": "evidence",
                    "text": key,
                }
                for key in evidence_keys
            ]
        requirements.append(requirement)

    requirements.sort(key=lambda item: item["control-id"])
    document = {
        "system-security-plan": {
            "uuid": _stable_uuid(namespace, slug, "ssp", slug),
            "metadata": {
                "title": f"{name} System Security and Privacy Plan",
                "last-modified": "2000-01-01T00:00:00Z",
                "version": "draft",
                "oscal-version": "1.1.3",
                "remarks": "Generated from governed system response YAML; review before authorization use.",
            },
            "import-profile": {"href": "#profile-not-specified"},
            "system-characteristics": {
                "system-ids": [{"id": slug}],
                "system-name": name,
                "description": f"System Security and Privacy Plan for {name}.",
                "system-information": {
                    "information-types": [
                        {
                            "title": "System information",
                            "description": "Information types are maintained in the governed system sources.",
                        }
                    ]
                },
                "security-sensitivity-level": sensitivity,
                "security-impact-level": {
                    "security-objective-confidentiality": sensitivity,
                    "security-objective-integrity": sensitivity,
                    "security-objective-availability": sensitivity,
                },
                "status": {"state": "under-development"},
                "authorization-boundary": {
                    "description": "Authorization boundary details are maintained in the governed architecture sources."
                },
            },
            "system-implementation": {
                "users": [
                    {
                        "uuid": _stable_uuid(namespace, slug, "user", "system-users"),
                        "title": "System users",
                        "remarks": "User classes are maintained in the governed architecture sources.",
                    }
                ],
                "components": [
                    {
                        "uuid": component_uuid,
                        "type": "this-system",
                        "title": name,
                        "description": f"The {name} system.",
                        "status": {"state": "under-development"},
                    }
                ],
            },
            "control-implementation": {
                "description": "System-specific control responses projected from governed YAML sources.",
                "implemented-requirements": requirements,
            },
        }
    }
    if resources:
        document["system-security-plan"]["back-matter"] = {"resources": resources}
    return document


def _optional_yaml(path: Path) -> dict[str, Any]:
    return _load_yaml(path) if path.is_file() else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "system_dir",
        type=Path,
        help="System directory holding system.yaml and responses.yaml "
        "(optional: sidecar.yaml, countermeasures.yaml, model/evidence.yaml)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Output path (default: build/oscal/<slug>.ssp.json under the CWD)",
    )
    parser.add_argument(
        "--uuid-namespace",
        type=uuid.UUID,
        default=DEFAULT_UUID_NAMESPACE,
        help="UUIDv5 namespace for generated identifiers (default: derived from "
        "a fixed name). Pass an existing namespace to keep UUIDs stable across tools.",
    )
    args = parser.parse_args(argv)
    root = args.system_dir
    manifest = root / "system.yaml"
    responses = root / "responses.yaml"
    if not manifest.is_file():
        parser.error(f"{root} has no system.yaml")
    if not responses.is_file():
        parser.error(f"{root} has no responses.yaml")
    system = _load_yaml(manifest)
    slug = system.get("slug") or root.resolve().name
    output = args.out or Path("build") / "oscal" / f"{slug}.ssp.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            build_oscal(
                system,
                _load_yaml(responses),
                sidecar=_optional_yaml(root / "sidecar.yaml"),
                countermeasures=_optional_yaml(root / "countermeasures.yaml"),
                evidence=_optional_yaml(root / "model" / "evidence.yaml"),
                namespace=args.uuid_namespace,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
