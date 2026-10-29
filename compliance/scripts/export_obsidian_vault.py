#!/usr/bin/env python3
"""Build an Obsidian vault from an OSCAL SSP JSON document.

Consumes either an OSCAL SSP JSON file (the shape produced by
`fismatic.ssp_converter.SSPConverter.get_oscal_data()`) or a raw FedRAMP SSP
`.docx` parsed live via fismatic, and emits an Obsidian-compatible markdown
vault following the schema in `docs/OBSIDIAN_VAULT_SCHEMA.md`.

Output layout (created under `--vault`):

    <vault>/
    ├── Index.md                # Dataview dashboards + counts
    ├── Controls/<control_id>.md (one per implemented-requirement)
    ├── Systems/<system-slug>.md
    ├── Components/<system-slug>.md  (synthetic; fismatic has no real
    │                                per-component block)
    ├── Organizations/<slug>.md (one per metadata.parties organization,
    │                             or a single synthetic CSP fallback)
    ├── Plans/<slug>.md         (one, describing the SSP itself)
    └── Mappings/               (vocabulary crosswalks rendered as markdown
                                 tables, sourced from
                                 compliance/mappings/*.yaml)

Examples:
    python3 compliance/scripts/export_obsidian_vault.py \\
        --oscal-json examples/oscal/ssp-source.example.json \\
        --plan-id <system> \\
        --vault examples/vault/

    python3 compliance/scripts/export_obsidian_vault.py \\
        --docx path/to/your-ssp.docx \\
        --plan-id my-plan \\
        --vault examples/vault/
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.vault import build_vault  # noqa: E402

# ---------------------------------------------------------------------------
# Vault I/O helpers
# ---------------------------------------------------------------------------


def _wipe_vault(vault_dir: Path, keep: tuple[str, ...] = (".git",)) -> None:
    """Remove every entry under `vault_dir` except those in `keep`."""
    if not vault_dir.exists():
        return
    for entry in vault_dir.iterdir():
        if entry.name in keep:
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def write_vault(notes, vault_dir: Path) -> int:
    """Write each VaultNote to `vault_dir / note.relpath`, creating parents.

    Binary notes (``note.is_binary`` — e.g. extracted diagram images) are
    written verbatim in binary mode; text notes are rendered to markdown.
    """
    vault_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for note in notes:
        path = vault_dir / note.relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if getattr(note, "is_binary", False):
            with open(path, "wb") as f:
                f.write(note.data)
        else:
            with open(path, "w") as f:
                f.write(note.render())
        written += 1
    return written


# ---------------------------------------------------------------------------
# OSCAL dict overrides (pre-generator)
# ---------------------------------------------------------------------------


def _apply_overrides(oscal_ssp: Dict[str, Any], args) -> None:
    """Inject --system-name and --fips-199 CLI overrides into the OSCAL dict."""
    if not (args.system_name or args.fips_199):
        return
    ssp_root = oscal_ssp.get("system-security-plan", oscal_ssp)
    sc = ssp_root.setdefault("system-characteristics", {})
    if args.system_name:
        sc["system-name"] = args.system_name
    if args.fips_199:
        sc["security-sensitivity-level"] = args.fips_199
        sil = sc.setdefault("security-impact-level", {})
        for obj in ("confidentiality", "integrity", "availability"):
            sil[f"security-objective-{obj}"] = args.fips_199


def _apply_metadata_yaml(oscal_ssp: Dict[str, Any], args) -> tuple:
    """Merge per-system metadata from --metadata-yaml registry.

    Returns
    -------
    tuple[list, list]
        ``(registry_components, registry_leveraged_auths)`` extracted from the
        registry match, or ``([], [])`` if none found. These are forwarded to
        :func:`build_vault` so it can emit real component notes.
    """
    if not args.metadata_yaml:
        return [], []
    import yaml

    with open(args.metadata_yaml) as f:
        registry = yaml.safe_load(f)
    systems = registry.get("systems") or []
    match = next((s for s in systems if s.get("plan_id") == args.plan_id), None)
    if not match:
        return [], []

    ssp_root = oscal_ssp.setdefault("system-security-plan", oscal_ssp)
    metadata = ssp_root.setdefault("metadata", {})
    parties = metadata.setdefault("parties", [])
    roles = metadata.setdefault("roles", [])
    sc = ssp_root.setdefault("system-characteristics", {})

    if not args.system_name and match.get("system_name"):
        sc["system-name"] = match["system_name"]
    if not args.fips_199 and match.get("fips_199"):
        fips = match["fips_199"]
        sc["security-sensitivity-level"] = fips
        sil = sc.setdefault("security-impact-level", {})
        for obj in ("confidentiality", "integrity", "availability"):
            sil[f"security-objective-{obj}"] = fips
    if match.get("status"):
        sc.setdefault("status", {})["state"] = match["status"]

    for role_key, role_title in [
        ("system_owner", "System Owner"),
        ("isso", "Information System Security Officer"),
        ("authorizing_official", "Authorizing Official"),
    ]:
        name = match.get(role_key)
        if not name:
            continue
        role_id = role_title.lower().replace(" ", "-")
        if not any(r.get("id") == role_id for r in roles):
            roles.append(
                {
                    "id": role_id,
                    "title": role_title,
                    "description": f"{role_title} for {match.get('system_name', args.plan_id)}",
                }
            )
        existing = next((p for p in parties if p.get("name") == name), None)
        if existing:
            rids = existing.setdefault("role-ids", [])
            if role_id not in rids:
                rids.append(role_id)
        else:
            parties.append(
                {
                    "uuid": str(__import__("uuid").uuid4()),
                    "type": "organization",
                    "name": name,
                    "role-ids": [role_id],
                }
            )

    # Components are no longer injected into the OSCAL dict here.
    # They are forwarded directly to build_vault() via the return value so
    # build_real_component_notes() can own the vault note generation.
    registry_components = match.get("components") or []
    registry_leveraged_auths = match.get("leveraged_authorizations") or []
    return registry_components, registry_leveraged_auths


def _apply_fips199_level(oscal_ssp: Dict[str, Any], level: str) -> None:
    """Set ``security-sensitivity-level`` from a FIPS-199 derived level."""
    ssp_root = oscal_ssp.get("system-security-plan", oscal_ssp)
    sc = ssp_root.setdefault("system-characteristics", {})
    sc["security-sensitivity-level"] = level
    sil = sc.setdefault("security-impact-level", {})
    for obj in ("confidentiality", "integrity", "availability"):
        sil.setdefault(f"security-objective-{obj}", level)


def _load_from_docx(docx_path: str) -> Dict[str, Any]:
    """Parse a .docx via fismatic and return the raw OSCAL SSP dict."""
    _FISMATIC_SRC = _REPO_ROOT / "fismatic" / "src" / "phase_1"
    if _FISMATIC_SRC.is_dir() and str(_FISMATIC_SRC) not in sys.path:
        sys.path.insert(0, str(_FISMATIC_SRC))
    from fismatic.ssp_converter import SSPConverter  # noqa: E402

    converter = SSPConverter(docx_path)
    oscal_data = converter.get_oscal_data()
    if not oscal_data:
        raise ValueError(f"Failed to parse SSP document: {docx_path}")
    return oscal_data


def _extract_narrative(docx_path: str) -> Dict[str, Any]:
    """Recover Chapters 1-12 narrative from a .docx via the contract-driven
    ``heading_parser``.

    fismatic (the primary parser) only reads Chapter 13 control tables, so
    everything in Chapters 1-12 (system description, authorization boundary,
    system environment, interconnections, inheritance, DevSecOps runbooks,
    digital-identity/IPv6/showstopper decisions, data-flow diagrams) is dropped.
    This routes that content into typed records the vault builder can emit as
    populated ``oscal.x.*`` notes and use to enrich the System note body.

    Returns ``{}`` (an empty narrative) on any failure so the vault build never
    regresses relative to the fismatic-only path.
    """
    try:
        from compliance.lib.heading_parser import extract_from_docx, load_contract

        contract_path = (
            _REPO_ROOT / "compliance" / "datacontracts" / "fedramp-ssp-v1.yaml"
        )
        contract = load_contract(str(contract_path))
        return extract_from_docx(docx_path, contract)
    except Exception as exc:  # noqa: BLE001 — narrative is best-effort
        print(
            f"WARNING: Chapters 1-12 narrative extraction failed for "
            f"{Path(docx_path).name}: {exc}",
            file=sys.stderr,
        )
        return {}


def _extract_images(docx_path: str) -> Dict[str, Any]:
    """Extract embedded image bytes from a `.docx`, keyed by relationship id.

    The text-based catchall/heading_parser path records only image *references*
    (rId + media part name). This pulls the actual bytes so the vault builder
    can write real ``Diagrams/media/*.png`` attachments and embed them.

    Returns ``{}`` on failure so a broken/renderless image never aborts a build.
    """
    try:
        from compliance.lib.docx_images import extract_images

        return extract_images(docx_path)
    except Exception as exc:  # noqa: BLE001 — images are best-effort
        print(
            f"WARNING: image extraction failed for {Path(docx_path).name}: {exc}",
            file=sys.stderr,
        )
        return {}


def _preserve_opaque_artifacts(docx_path: str, vault_dir: Path) -> None:
    """Carry opaque DOCX artifacts (comments, tracked revisions, footnotes,
    endnotes, drawings, headers/footers) forward into a committed preservation
    sidecar.

    fismatic and the Chapters 1-12 narrative parser do not read these artifacts,
    so without this step they are silently dropped at the ``.docx`` boundary.
    The sidecar records each one with its stable source locator so a later
    consumer can prove it was carried forward rather than lost (lossless
    pipeline, EPIC #43).

    Best-effort: a corrupt/renderless ``.docx`` logs a warning and never aborts
    the vault build, mirroring the other optional extractors above.
    """
    try:
        from compliance.lib.docx_preservation import (
            collect_opaque_artifacts,
            extract_managed_artifacts,
            verify_managed_artifacts,
            write_preservation_sidecar,
            write_source_manifest,
        )

        report = collect_opaque_artifacts(docx_path)
        sidecar = write_preservation_sidecar(report, vault_dir)
        print(f"Wrote opaque-artifact preservation sidecar: {sidecar}")
        managed_artifacts = extract_managed_artifacts(docx_path, vault_dir)
        artifact_findings = verify_managed_artifacts(managed_artifacts, vault_dir)
        if artifact_findings:
            raise ValueError("; ".join(artifact_findings))
        manifest = write_source_manifest(report, vault_dir, managed_artifacts)
        print(f"Wrote DOCX source preservation manifest: {manifest}")
        from compliance.lib.table_projection import (
            write_source_table_base,
            write_source_table_notes,
            write_table_row_notes,
        )

        table_notes = write_source_table_notes(Path(docx_path), vault_dir)
        row_notes = write_table_row_notes(Path(docx_path), vault_dir)
        write_source_table_base(vault_dir)
        print(f"Wrote {len(table_notes)} source-table review note(s)")
        print(f"Wrote {len(row_notes)} queryable source-table row note(s)")
    except Exception as exc:  # noqa: BLE001 — preservation is best-effort
        print(
            f"WARNING: opaque-artifact preservation failed for "
            f"{Path(docx_path).name}: {exc}",
            file=sys.stderr,
        )


def _write_instruction_guidance(
    docx_path: str,
    vault_dir: Path,
    policy_path: str | None = None,
) -> int:
    """Write source-located typed guidance notes through the production path."""
    from compliance.lib.instruction_ledger import (
        build_instruction_ledger,
        load_instruction_policy,
        render_instruction_outputs,
    )

    overrides = load_instruction_policy(policy_path) if policy_path else None
    ledger = build_instruction_ledger(docx_path, overrides=overrides)
    render_instruction_outputs(ledger, vault_dir)
    return sum(
        1
        for entry in ledger.entries
        if entry.destination
        and entry.disposition not in {"duplicate", "opaque_preserved", "unmapped_error"}
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an Obsidian vault from an OSCAL SSP JSON document",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--oscal-json", help="Path to an OSCAL SSP JSON file")
    source_group.add_argument("--docx", help="Path to a raw FedRAMP SSP .docx")
    parser.add_argument("--plan-id", required=True, help="Identifier for this SSP")
    parser.add_argument(
        "--parser-name", default="fismatic", help="Recorded as parsed_by"
    )
    parser.add_argument("--source-docx", help="Original .docx filename for provenance")
    parser.add_argument(
        "--vault", required=True, help="Directory to write the vault into"
    )
    parser.add_argument(
        "--keep-existing", action="store_true", help="Don't wipe --vault before writing"
    )
    parser.add_argument(
        "--system-name", help="Override system-characteristics.system-name"
    )
    parser.add_argument(
        "--fips-199",
        choices=["low", "moderate", "high"],
        help="Override FIPS 199 level",
    )
    parser.add_argument(
        "--metadata-yaml",
        help="Path to a per-system metadata registry YAML (see scripts/registry.yaml)",
    )
    parser.add_argument(
        "--crm-ods",
        help="Path to a secondary CRM .ods (inheritance/control-implementation "
        "matrix) to augment control notes with tenant responsibilities.",
    )
    parser.add_argument(
        "--fips199-docx",
        help="Path to a FIPS-199 categorization .docx to extract information types",
    )
    parser.add_argument(
        "--instruction-policy",
        help="Versioned instruction disposition policy for DOCX guidance notes",
    )
    args = parser.parse_args()

    if args.docx:
        oscal_ssp = _load_from_docx(args.docx)
        if not args.source_docx:
            args.source_docx = Path(args.docx).name
        # fismatic's OSCAL output drops organization-defined parameters (ODPs);
        # recover them from the .docx separately (issue #18).
        from compliance.lib.control_parameters import extract_parameters

        control_params = extract_parameters(args.docx).by_control
        # Recover Chapters 1-12 narrative (system description, boundary,
        # environment, interconnections, inheritance, runbooks, decisions,
        # diagrams) that the primary Chapter-13 parser drops.
        narrative = _extract_narrative(args.docx)
        # Extract embedded image bytes so diagrams are saved as real vault
        # attachments (Diagrams/media/*.png) and embedded via ![[...]].
        images = _extract_images(args.docx)
        # Recover per-control authoring guidance (blue #0070C0 instruction
        # blocks + CIS-table "Describe how..." prompts) so control notes retain
        # the template's fill guidance (R18).
        from compliance.lib.control_guidance import extract_control_guidance

        control_guidance = extract_control_guidance(args.docx)
        # Recover Chapters 1-12 front-matter authoring guidance keyed by
        # section heading so the narrative notes retain the template's fill
        # prompts (R19 — the ~140 instruction paragraphs before the control
        # section that R18's control-keyed extractor drops).
        from compliance.lib.control_guidance import extract_section_guidance

        section_guidance = extract_section_guidance(args.docx)
    else:
        with open(args.oscal_json, "r") as f:
            oscal_ssp = json.load(f)
        control_params = {}
        narrative = None
        images = None
        control_guidance = {}
        section_guidance = {}

    _apply_overrides(oscal_ssp, args)
    registry_components, registry_leveraged_auths = _apply_metadata_yaml(
        oscal_ssp, args
    )

    # Optional FIPS-199 .docx enrichment — extract information types.
    fips199_data = None
    if args.fips199_docx:
        from compliance.lib.fips199 import (
            fips199_to_oscal_information_types,
            parse_fips199,
        )

        result = parse_fips199(args.fips199_docx)
        if result:
            fips199_data = fips199_to_oscal_information_types(
                result, oscal_version="1.1.3"
            )
            # Also derive --fips-199 level if not set on the CLI.
            if not args.fips_199:
                _apply_fips199_level(oscal_ssp, result.overall_level)

    # Optional secondary CRM (.ods) augmentation.
    crm_entries = {}
    if args.crm_ods:
        from compliance.lib.crm_ods import extract_crm
        from compliance.lib.vault import canonical_control_id

        crm_entries = extract_crm(args.crm_ods).by_control
        # Flag CRM controls that have no matching SSP control (their inheritance
        # data cannot be attached to a note) rather than dropping them silently.
        ssp_ids = {
            canonical_control_id(r.get("control-id") or "")
            for r in oscal_ssp.get("system-security-plan", {})
            .get("control-implementation", {})
            .get("implemented-requirements", [])
            if (r.get("control-id") or "").strip()
        }
        crm_only = sorted(set(crm_entries) - ssp_ids)
        if crm_only:
            print(
                f"WARNING: {len(crm_only)} CRM control(s) not present in the SSP "
                f"(inheritance data not attached): {', '.join(crm_only)}",
                file=sys.stderr,
            )

    notes = build_vault(
        oscal_ssp,
        plan_id=args.plan_id,
        parser_name=args.parser_name,
        source_docx=args.source_docx,
        control_parameters=control_params,
        crm_entries=crm_entries,
        narrative=narrative,
        images=images,
        fips199_data=fips199_data,
        registry_components=registry_components,
        registry_leveraged_auths=registry_leveraged_auths,
        control_guidance=control_guidance,
        section_guidance=section_guidance,
    )

    vault_dir = Path(args.vault).resolve()
    if not args.keep_existing:
        _wipe_vault(vault_dir)
    written = write_vault(notes, vault_dir)
    from compliance.lib.oscal_lineage import mapping_lineage_report

    capabilities_path = vault_dir / "_exports" / "oscal-mapping-capabilities.json"
    capabilities_path.parent.mkdir(parents=True, exist_ok=True)
    capabilities_path.write_text(
        json.dumps(
            mapping_lineage_report(
                Path(__file__).resolve().parents[1]
                / "datacontracts/fedramp-ssp-v1.yaml"
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    obsolete_lineage = vault_dir / "_exports" / "oscal-lineage.json"
    if obsolete_lineage.is_file():
        obsolete_lineage.unlink()
    if args.docx:
        written += _write_instruction_guidance(
            args.docx,
            vault_dir,
            args.instruction_policy,
        )
    print(f"Wrote {written} vault notes to {vault_dir}")
    print(f"Wrote OSCAL mapping capabilities: {capabilities_path}")

    # Carry opaque DOCX artifacts (comments, tracked revisions, footnotes,
    # endnotes, drawings, headers/footers) forward into a committed sidecar so
    # they are preserved rather than silently dropped at the .docx boundary.
    if args.docx:
        _preserve_opaque_artifacts(args.docx, vault_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
