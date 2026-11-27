#!/usr/bin/env python3
"""Assemble a filled FedRAMP SSP .docx from an OSCAL SSP, fill context, and docx.yaml metadata.

Combines the core DOCX stages into one reusable assembly pipeline:
1. Jinja template fill + optional round-trip parity check
2. OSCAL control-table fill
3. Chapters 1-12 named table fill
4. Supplemental control tables and overflow statement rows
5. Anchored text and diagram image insertion
6. Allowlisted template token replacement
7. Fidelity score computation and manifest output
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def compute_fidelity(
    manifest: dict[str, Any],
    chapter_manifest: dict[str, Any],
    anchored_results: list[dict[str, Any]],
) -> dict[str, float]:
    """Compute normalized fidelity ratios across control, statement, chapter, and anchor stages."""
    statements = [
        statement
        for control in manifest.get("controls", [])
        for statement in control.get("statements", [])
    ]
    return {
        "control_identity": (
            manifest["matching_control_ids_filled"] / manifest["source_controls"]
            if manifest.get("source_controls")
            else 1.0
        ),
        "statement_placement": (
            sum(bool(statement.get("written")) for statement in statements)
            / len(statements)
            if statements
            else 1.0
        ),
        "chapter_rows": (
            chapter_manifest["written_rows"] / chapter_manifest["source_rows"]
            if chapter_manifest.get("source_rows")
            else 1.0
        ),
        "anchored_content": (
            sum(bool(item.get("matched")) for item in anchored_results)
            / len(anchored_results)
            if anchored_results
            else 1.0
        ),
    }


def assemble_ssp_docx(
    *,
    template_path: Path,
    contract_path: Path,
    context: dict[str, str],
    oscal_path: Path,
    system_root: Path,
    out_path: Path,
    manifest_path: Path,
    docx_source: dict[str, Any],
    diagram_manifests: list[dict[str, Any]],
    resolve_diagram_image_path: Callable[[dict[str, Any]], Path],
    check_parity_flag: bool = False,
    fail_unmatched_source: bool = False,
    fail_duplicate_targets: bool = False,
    narrative_format: dict[str, Any] | None = None,
    manifest_repository_root: Path | None = None,
    normalize_output_zip: bool = False,
) -> int:
    """Execute the end-to-end SSP DOCX assembly pipeline and write the manifest."""
    from compliance.lib.anchored_content import (
        fill_anchored_image,
        fill_anchored_text,
    )
    from compliance.lib.docx_chapter_tables import fill_chapter_tables
    from compliance.lib.docx_fill import check_parity, fill_template, reextract_filled
    from compliance.lib.docx_rendering import (
        format_rendered_narratives,
        normalize_docx_zip,
        write_manifest,
    )
    from compliance.lib.docx_supplemental_controls import (
        append_supplemental_controls,
        append_unwritten_statements,
    )
    from compliance.lib.heading_parser import load_contract
    from compliance.scripts.fill_ssp_from_oscal import build_from_oscal
    from compliance.scripts.replace_docx_tokens import replace_tokens

    contract = load_contract(contract_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = fill_template(template_path, context, out_path, contract=contract)
    print(f"wrote {result.output_path} ({len(result.filled_tags)} tags filled)")
    if result.missing_tags:
        print(f"  contract declares tags not supplied: {result.missing_tags}")

    if check_parity_flag:
        recovered = reextract_filled(result.output_path, contract)
        parity = check_parity(context, recovered)
        rate = parity.recovery_rate * 100
        print(f"parity: {len(parity.matched)}/{len(context)} recovered ({rate:.0f}%)")
        for tag, entry in parity.mismatched.items():
            print(
                f"  MISMATCH {tag}: wrote={entry['written']!r} got={entry['recovered']!r}"
            )
        for tag in parity.missing:
            print(f"  MISSING  {tag}")
        if not parity.ok:
            return 1

    manifest = build_from_oscal(
        oscal_path,
        result.output_path,
        result.output_path,
        manifest_path,
        strict_mapping=True,
    )
    chapter_manifest = fill_chapter_tables(
        result.output_path,
        system_root,
        result.output_path,
    )
    supplemental_manifest = append_supplemental_controls(
        result.output_path,
        oscal_path,
        manifest["source_controls_without_target_table"],
    )
    overflow_manifest = append_unwritten_statements(
        result.output_path,
        oscal_path,
        manifest.get("controls", []),
    )
    if overflow_manifest["statements"]:
        for control in manifest.get("controls", []):
            for statement in control.get("statements", []):
                if not statement.get("written"):
                    statement["placement"] = "supplemental-row"
                    statement["written"] = True
    if supplemental_manifest["controls"]:
        supplemented = {
            item["control_id"] for item in supplemental_manifest["controls"]
        }
        manifest["source_controls_without_target_table"] = [
            control_id
            for control_id in manifest["source_controls_without_target_table"]
            if control_id not in supplemented
        ]
        manifest["matching_control_ids_filled"] += len(supplemented)

    system_type = (docx_source.get("chapters") or {}).get("system_type") or {}
    laws = (docx_source.get("chapters") or {}).get("laws") or []
    anchored_results = [
        fill_anchored_text(
            result.output_path,
            anchor="system_type",
            containing_text="Major Information System/Minor Application/Subsystem",
            value=str(system_type.get("value") or "Pending authoritative source"),
            output_path=result.output_path,
        )
    ]
    if laws:
        law_text = "Applicable system-specific authorities:\n" + "\n".join(
            f"- {law['citation']}: {law['applicability']}" for law in laws
        )
        anchored_results.append(
            fill_anchored_text(
                result.output_path,
                anchor="applicable_laws",
                containing_text="See Appendix B, References.",
                value=law_text,
                output_path=result.output_path,
            )
        )
    diagrams = docx_source.get("diagrams") or {}
    for diagram_name, diagram in diagrams.items():
        anchored_results.append(
            fill_anchored_image(
                result.output_path,
                anchor=f"{diagram_name}_diagram",
                containing_text=diagram.get("anchor_text") or diagram["caption"],
                image_path=resolve_diagram_image_path(diagram),
                caption=diagram["caption"],
                alt_text=diagram["alt_text"],
                output_path=result.output_path,
            )
        )
    token_manifest = replace_tokens(
        result.output_path, docx_source.get("template_replacements") or {}
    )
    manifest["chapter_tables"] = chapter_manifest
    manifest["anchored_content"] = anchored_results
    manifest["diagrams"] = diagram_manifests
    manifest["template_tokens"] = token_manifest
    manifest["supplemental_controls"] = supplemental_manifest
    manifest["supplemental_statements"] = overflow_manifest
    manifest["fidelity"] = compute_fidelity(
        manifest, chapter_manifest, anchored_results
    )
    if narrative_format is not None:
        labels = narrative_format.get("implementation_layer_labels") or []
        manifest["narrative_format"] = format_rendered_narratives(
            result.output_path,
            implementation_layer_labels=labels,
            include_supplemental_control_tables=bool(
                narrative_format.get("include_supplemental_control_tables", False)
            ),
        )
    write_manifest(manifest_path, manifest, repository_root=manifest_repository_root)
    if normalize_output_zip:
        normalize_docx_zip(result.output_path)
    print(
        "control tables: "
        f"{manifest['matching_control_ids_filled']}/{manifest['source_controls']} matched; "
        f"manifest {manifest_path}"
    )
    print(
        "chapter tables: "
        f"{chapter_manifest['written_rows']}/{chapter_manifest['source_rows']} rows; "
        f"mismatches={chapter_manifest['mismatches']}"
    )
    if manifest["source_controls_without_target_table"]:
        print(
            "unmatched source controls: "
            + ", ".join(manifest["source_controls_without_target_table"])
        )
    if fail_unmatched_source and manifest["source_controls_without_target_table"]:
        print("FAIL: source controls were not present in the target template")
        return 1
    if fail_duplicate_targets and manifest["duplicate_target_control_ids"]:
        print("FAIL: duplicate control IDs were found in the target template")
        return 1

    return 0
