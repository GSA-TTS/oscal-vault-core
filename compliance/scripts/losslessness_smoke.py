#!/usr/bin/env python3
"""Run the synthetic DOCX preservation, projection, lineage, and loss-budget gate."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compliance.lib.canonical_json import generate_canonical_data  # noqa: E402
from compliance.lib.docx_preservation import (  # noqa: E402
    BUNDLE_RELPATH,
    collect_opaque_artifacts,
    extract_managed_artifacts,
    verify_managed_artifacts,
    write_source_manifest,
)
from compliance.lib.docx_ir import OPCPackageWalker  # noqa: E402
from compliance.lib.export_oscal import canonical_to_oscal_component_definition  # noqa: E402
from compliance.lib.oscal_lineage import (  # noqa: E402
    component_definition_lineage,
    verify_oscal_lineage,
)
from compliance.lib.synthetic_golden_docx import create_synthetic_golden_docx  # noqa: E402
from compliance.lib.table_projection import (  # noqa: E402
    classify_source_table,
    write_table_row_notes,
)


def run(output: Path) -> list[str]:
    findings: list[str] = []
    source = output / "source.docx"
    vault = output / "vault"
    create_synthetic_golden_docx(source)
    report = collect_opaque_artifacts(source)
    managed = extract_managed_artifacts(source, vault)
    write_source_manifest(report, vault, managed)
    findings.extend(report.loss_budget_findings())
    findings.extend(verify_managed_artifacts(managed, vault))
    row_notes = write_table_row_notes(source, vault)
    classified_tables = sum(
        classify_source_table(table) is not None
        for table in OPCPackageWalker(source).parse().tables
    )
    manifest = json.loads((vault / BUNDLE_RELPATH).read_text(encoding="utf-8"))
    if not manifest.get("source_sha256"):
        findings.append("source package hash is missing")
    if not manifest.get("source_tables"):
        findings.append("source tables are missing")
    if not manifest.get("opaque_artifacts"):
        findings.append("opaque artifact inventory is missing")
    if not managed:
        findings.append("managed artifacts are missing")
    if not row_notes:
        findings.append("no simple table rows were projected")
    for note in row_notes:
        canonical = generate_canonical_data(note, vault)
        lineage = canonical.get("source_lineage") or []
        if not lineage:
            findings.append(f"canonical lineage is missing: {note.relative_to(vault)}")
        if "source_table_id::" not in note.read_text(encoding="utf-8"):
            findings.append(f"Obsidian inline source table property is missing: {note}")
    adapter_canonical = {
        "frontmatter": {
            "type": "oscal.component_definition",
            "name": "Synthetic external service",
            "component_type": "service",
        },
        "body_sections": [{"heading": "Description", "content": "Synthetic"}],
        "table_records": [
            {
                "source_locator": "word/document.xml#body/tbl[synthetic]",
                "kind": "external_service",
                "disposition": "mapped",
                "headers": ["Service Element", "Response"],
                "rows": [["Service Name", "login.gov"]],
            }
        ],
    }
    adapter_oscal = canonical_to_oscal_component_definition(adapter_canonical)
    oscal_lineage = component_definition_lineage(adapter_canonical, adapter_oscal)
    findings.extend(
        verify_oscal_lineage(adapter_canonical, adapter_oscal, oscal_lineage)
    )
    summary = {
        "source_sha256": manifest.get("source_sha256"),
        "package_parts": len(manifest.get("package_inventory", {}).get("parts", [])),
        "source_nodes": manifest.get("loss_budget", {}).get("total_nodes", 0),
        "source_tables": len(manifest.get("source_tables", [])),
        "classified_source_tables": classified_tables,
        "opaque_artifacts": len(manifest.get("opaque_artifacts", [])),
        "managed_artifacts": len(managed),
        "projected_rows": len(row_notes),
        "oscal_lineage_records": len(oscal_lineage),
        "findings": findings,
    }
    (output / "losslessness-smoke.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        return 1 if run(args.output) else 0
    with tempfile.TemporaryDirectory() as directory:
        return 1 if run(Path(directory)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
