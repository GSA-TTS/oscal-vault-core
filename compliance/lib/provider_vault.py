"""Provider-aware Obsidian vault parsing, seeding, and OSCAL 1.1.3 round-trip (#52).

Implements the provider-segmented ``# Statement Parts`` Markdown grammar,
lossless OSCAL SSP <-> Vault fact extraction (`by-components` prose,
`set-parameters`, `evidence` links, and component rules/protocols), and
multi-provider OSCAL SSP enrichment.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import yaml

from compliance.lib import vault as vault_lib

SECTION = "# Statement Parts"
WHOLE_CONTROL = "Whole control"
WHOLE = None
EMPTY = "_(No provider narrative recorded.)_"
_PART = re.compile(r"^Part (?P<label>\S+)$")
_STATEMENT_ID = re.compile(r"_s(?:mt|tmt)\.(?P<label>.+)$")
DEFAULT_NAMESPACE = uuid.UUID("80ad9a30-2ce2-4c62-86f9-b0f7462f30ba")
COMPONENT_KIND = {"this-system": "system", "service": "service"}


def statement_label(statement_id: str) -> str:
    """Extract the part label from an OSCAL statement-id (``ac-1_smt.a`` -> ``a``)."""
    match = _STATEMENT_ID.search(statement_id)
    if not match:
        raise ValueError(f"unrecognised statement id: {statement_id!r}")
    return match.group("label")


def vault_control_id(oscal_id: str) -> str:
    """Convert OSCAL token control-id (``ac-2.5``) to ``AC-2(5)``."""
    return re.sub(r"\.(\w+)$", r"(\1)", oscal_id).upper()


def note_id(control_id: str) -> str:
    """Normalize ``AC-2(5)`` / ``ac-2.5`` to zero-padded vault stem ``AC-02(05)``."""
    return vault_lib.canonical_control_id(vault_control_id(control_id))


def _check_prose(prose: str) -> None:
    if prose != prose.strip():
        raise ValueError("prose has leading or trailing whitespace")
    if any(line.startswith("#") for line in prose.splitlines()):
        raise ValueError("prose contains a line that would parse as a heading")


def render_statement_parts(
    sections: list[tuple[str | None, list[tuple[str, str]]]],
) -> str:
    """Render ``(part_label | None, [(provider_title, prose)])`` sections."""
    lines = [SECTION, ""]
    wrote = False
    for label, responses in sections:
        responses = [(title, prose) for title, prose in responses if prose]
        if not responses:
            continue
        wrote = True
        lines += [f"## {WHOLE_CONTROL if label is None else f'Part {label}'}", ""]
        for title, prose in responses:
            _check_prose(prose)
            lines += [f"### {title}", "", prose, ""]
    if not wrote:
        lines += [EMPTY, ""]
    return "\n".join(lines).rstrip() + "\n"


def parse_statement_parts(body: str) -> dict[tuple[str | None, str], str]:
    """Return ``{(part_label | None, provider_title): prose}`` from a note body."""
    _, found, tail = body.partition(SECTION + "\n")
    if not found:
        return {}
    parsed: dict[tuple[str | None, str], str] = {}
    label: str | None = None
    title: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if title is not None and any(line.strip() for line in buffer):
            parsed[(label, title)] = "\n".join(buffer).strip("\n")

    for line in tail.splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            part = _PART.match(heading)
            label = part.group("label") if part else None
            title, buffer = None, []
        elif line.startswith("### "):
            flush()
            title, buffer = line[4:].strip(), []
        elif title is not None:
            buffer.append(line)
    flush()
    return parsed


def split_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """Read a markdown note and return ``(frontmatter_dict, body_str)``."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: no frontmatter")
    head, _, body = text[4:].partition("\n---\n")
    return yaml.safe_load(head), body


def parse_h2_sections(body: str) -> dict[str, str]:
    """Parse ``## <Heading>`` sections from a markdown note body."""
    sections: dict[str, str] = {}
    heading: str | None = None
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if heading:
                sections[heading] = "\n".join(lines).strip()
            heading, lines = line[3:], []
        elif heading:
            lines.append(line)
    if heading:
        sections[heading] = "\n".join(lines).strip()
    return sections


def _state(block: dict[str, Any]) -> str:
    return (block.get("implementation-status") or {}).get("state", "")


def _props(block: dict[str, Any]) -> dict[str, str]:
    return {p["name"]: p["value"] for p in block.get("props", [])}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _write_note(path: Path, front: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        vault_lib.dump_frontmatter(front) + "\n" + body.rstrip() + "\n",
        encoding="utf-8",
    )


def facts_from_ssp(
    document: dict[str, Any],
    aliases: dict[str, dict[str, Any]],
    *,
    evidence_prop_name: str = "fy26-evidence-resource-uuid",
) -> dict[str, dict[str, Any]]:
    """Extract per-control provider responses, parameters, and evidence from an SSP."""
    ssp = document["system-security-plan"]
    resources = {r["uuid"] for r in ssp.get("back-matter", {}).get("resources", [])}
    facts: dict[str, dict[str, Any]] = {}
    for req in ssp["control-implementation"]["implemented-requirements"]:
        props = _props(req)
        evidence = [
            link["href"].lstrip("#")
            for link in req.get("links", [])
            if link.get("rel") == "evidence"
        ]
        unknown = [uid for uid in evidence if uid not in resources]
        if unknown:
            raise ValueError(f"{req['control-id']}: unresolved evidence {unknown}")
        responses: dict[tuple[str | None, str], dict[str, Any]] = {}
        blocks = [(WHOLE, b) for b in req.get("by-components", [])]
        for statement in req.get("statements", []):
            label = statement_label(statement["statement-id"])
            blocks += [(label, b) for b in statement.get("by-components", [])]
        for label, block in blocks:
            provider = aliases[block["component-uuid"]]["title"]
            responses[(label, provider)] = {
                "status": _state(block),
                "prose": block.get("description", ""),
                "rule_ids": [
                    p["value"] for p in block.get("props", []) if p["name"] == "Rule_Id"
                ],
            }
        facts[req["control-id"]] = {
            "uuid": req["uuid"],
            "status": props.get("implementation-status"),
            "origination": props.get("control-origination"),
            "parameters": {
                p["param-id"]: p["values"] for p in req.get("set-parameters", [])
            },
            "evidence": evidence,
            "evidence_props": [
                p["value"]
                for p in req.get("props", [])
                if p["name"] == evidence_prop_name
            ],
            "statement_ids": [s["statement-id"] for s in req.get("statements", [])],
            "responses": responses,
        }
    return facts


def facts_from_vault(vault_dir: Path) -> dict[str, dict[str, Any]]:
    """Extract per-control provider responses, parameters, and evidence from a vault."""
    evidence_ids = {}
    for note in sorted((vault_dir / "Evidence").glob("*.md")):
        front, _ = split_frontmatter(note)
        evidence_ids[f"[[Evidence/{note.stem}]]"] = front["evidence_id"]
    facts: dict[str, dict[str, Any]] = {}
    for note in sorted((vault_dir / "Controls").glob("*.md")):
        front, body = split_frontmatter(note)
        prose = parse_statement_parts(body)
        responses = {}
        for entry in front.get("provider_responses", []):
            label = None if entry["part"] == WHOLE_CONTROL else entry["part"]
            key = (label, entry["provider"])
            responses[key] = {
                "status": entry["status"],
                "prose": prose.get(key, ""),
                "rule_ids": entry.get("rule_ids", []),
            }
        stray = set(prose) - set(responses)
        if stray:
            raise ValueError(f"{note.name}: prose without a response entry: {stray}")
        facts[front["oscal_control_id"]] = {
            "uuid": front["oscal_uuid"],
            "status": front["implementation_status"],
            "origination": front["origination"][0],
            "parameters": {
                p["param_id"]: [p["value"]] for p in front.get("parameters", [])
            },
            "evidence": [evidence_ids[link] for link in front.get("evidence", [])],
            "evidence_props": front.get("evidence_resource_props", []),
            "statement_ids": [
                f"{front['oscal_control_id']}_smt.{label}"
                for label in front.get("statement_labels", [])
            ],
            "responses": responses,
        }
    return facts


def extract_evidence_resources(vault_dir: Path) -> list[dict[str, Any]]:
    """Build OSCAL ``back-matter.resources`` from ``Evidence/*.md`` notes."""
    resources = []
    for note in sorted((vault_dir / "Evidence").glob("*.md")):
        front, _ = split_frontmatter(note)
        resources.append(
            {
                "uuid": front["evidence_id"],
                "title": front["title"],
                "props": [
                    {"name": "sha256", "value": front["sha256"]},
                    {"name": "media-type", "value": front["media_type"]},
                ],
                "rlinks": [{"href": front["source_uri"]}],
            }
        )
    return resources


def extract_components(
    vault_dir: Path,
    aliases: dict[str, dict[str, Any]],
    *,
    namespace: uuid.UUID = DEFAULT_NAMESPACE,
) -> list[dict[str, Any]]:
    """Build OSCAL ``system-implementation.components`` from ``Components/*.md``."""
    components = []
    for component_uuid, provider in sorted(
        aliases.items(), key=lambda item: item[1]["id"]
    ):
        front, body = split_frontmatter(
            vault_dir / "Components" / f"{provider['id']}.md"
        )
        sections = parse_h2_sections(body)
        component: dict[str, Any] = {
            "uuid": component_uuid,
            "type": "this-system" if provider["id"] == "this-system" else "service",
            "title": provider["title"],
            "description": sections.get("Description", ""),
            "status": {"state": front["status"]},
        }
        if sections.get("Purpose"):
            component["purpose"] = sections["Purpose"]
        rules = []
        for line in sections.get("Rules", "").splitlines():
            if line.startswith("- `"):
                name, remainder = line[3:].split("` (", 1)
                remarks, value = remainder.split("): ", 1)
                rules.append({"name": name, "value": value, "remarks": remarks})
        if rules:
            component["props"] = rules
        protocols = []
        for line in sections.get("Protocols", "").splitlines():
            if not line.startswith("- "):
                continue
            name_title, port_transport = line[2:].rsplit(": ", 1)
            name, title = name_title.split(" (", 1)
            port, transport = port_transport.split("/", 1)
            seed = f"protocol:{component_uuid}:{name}:{port}:{transport}"
            protocols.append(
                {
                    "uuid": str(uuid.uuid5(namespace, seed)),
                    "name": name,
                    "title": title.removesuffix(")"),
                    "port-ranges": [
                        {"start": int(port), "end": int(port), "transport": transport}
                    ],
                }
            )
        if protocols:
            component["protocols"] = protocols
        components.append(component)
    return components


def _build_requirement(
    control_id: str,
    fact: dict[str, Any],
    by_title: dict[str, str],
    namespace: uuid.UUID,
    evidence_prop_name: str,
) -> dict[str, Any]:
    requirement: dict[str, Any] = {
        "uuid": fact["uuid"],
        "control-id": control_id,
        "props": [
            {"name": "implementation-status", "value": fact["status"]},
            {"name": "control-origination", "value": fact["origination"]},
        ],
    }
    parameters = [
        {"param-id": name, "values": values}
        for name, values in fact["parameters"].items()
    ]
    if parameters:
        requirement["set-parameters"] = parameters
    if fact["evidence"]:
        requirement["links"] = [
            {"href": f"#{value}", "rel": "evidence"} for value in fact["evidence"]
        ]
    requirement["props"].extend(
        {"name": evidence_prop_name, "value": value} for value in fact["evidence_props"]
    )
    groups: dict[str | None, list[dict[str, Any]]] = {}
    for (part, title), response in fact["responses"].items():
        component_uuid = by_title[title]
        entry: dict[str, Any] = {
            "component-uuid": component_uuid,
            "uuid": str(uuid.uuid5(namespace, f"{control_id}:{part}:{component_uuid}")),
            "description": response["prose"],
            "implementation-status": {"state": response["status"]},
        }
        if response["rule_ids"]:
            entry["props"] = [
                {"name": "Rule_Id", "value": value} for value in response["rule_ids"]
            ]
        groups.setdefault(part, []).append(entry)
    requirement["by-components"] = groups.pop(None, [])
    statements = []
    for part in fact["statement_ids"]:
        label = part.rsplit("_smt.", 1)[1]
        statements.append(
            {
                "statement-id": part,
                "uuid": str(uuid.uuid5(namespace, f"statement:{part}")),
                "by-components": groups.pop(label, []),
            }
        )
    if groups:
        raise ValueError(
            f"{control_id}: provider prose has unknown statement labels {sorted(groups)}"
        )
    if statements:
        requirement["statements"] = statements
    return requirement


def enrich_ssp_from_vault(
    document: dict[str, Any],
    vault_dir: Path,
    aliases: dict[str, dict[str, Any]],
    *,
    system_slug: str | None = None,
    plan_id: str | None = None,
    namespace: uuid.UUID = DEFAULT_NAMESPACE,
    evidence_prop_name: str = "fy26-evidence-resource-uuid",
) -> dict[str, Any]:
    """Replace placeholder SSP control/component content with lossless vault facts."""
    plan = document["system-security-plan"]
    facts = facts_from_vault(vault_dir)
    by_title = {
        provider["title"]: component_uuid
        for component_uuid, provider in aliases.items()
    }
    requirements = [
        _build_requirement(cid, facts[cid], by_title, namespace, evidence_prop_name)
        for cid in sorted(facts)
    ]
    plan["metadata"].pop("parties", None)
    plan["metadata"].pop("responsible-parties", None)
    sys_notes = sorted((vault_dir / "Systems").glob("*.md"))
    sys_path = (
        vault_dir / "Systems" / f"{system_slug}.md"
        if system_slug
        else (sys_notes[0] if sys_notes else None)
    )
    if sys_path and sys_path.is_file():
        _, system_body = split_frontmatter(sys_path)
        system_description = parse_h2_sections(system_body).get("Description")
        if system_description:
            plan["system-characteristics"]["description"] = system_description
    resolved_plan_id = plan_id
    if not resolved_plan_id:
        plan_notes = sorted((vault_dir / "Plans").glob("*.md"))
        if plan_notes:
            pfm, _ = split_frontmatter(plan_notes[0])
            resolved_plan_id = pfm.get("plan_id")
    if resolved_plan_id:
        plan["system-characteristics"]["system-ids"] = [{"id": resolved_plan_id}]
    plan["system-implementation"]["components"] = extract_components(
        vault_dir, aliases, namespace=namespace
    )
    plan["control-implementation"]["implemented-requirements"] = requirements
    plan["back-matter"] = {"resources": extract_evidence_resources(vault_dir)}
    return document


def project_baseline_ssp(
    document: dict[str, Any],
    baseline_controls: list[str],
    *,
    expected_count: int | None = None,
) -> dict[str, Any]:
    """Filter an OSCAL SSP's implemented-requirements to a baseline control list."""
    selected = {
        control.lower().replace("(", ".").replace(")", "")
        for control in baseline_controls
    }
    projected = json.loads(json.dumps(document))
    reqs = projected["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ]
    filtered = [req for req in reqs if req["control-id"].lower() in selected]
    if expected_count is not None and len(filtered) != expected_count:
        raise ValueError(
            f"Baseline projection produced {len(filtered)} controls, expected {expected_count}"
        )
    projected["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ] = filtered
    return projected
