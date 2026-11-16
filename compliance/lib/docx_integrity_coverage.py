"""Prove source-node retention without mistaking an inventory label for projection."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from compliance.lib.docx_integrity_compare import digest
from compliance.lib.docx_ir import OPCPackageWalker
from compliance.lib.docx_package import inspect_package, strict_findings


def verify_package_retention(source: Path, vault: Path) -> dict:
    """Independently compare retained bytes against every bounded source ZIP member."""
    inventory = inspect_package(source)
    root = vault / "_source" / "package"
    findings = strict_findings(inventory)
    expected = {part["sha256"] for part in inventory["parts"]}
    actual = (
        {path.name for path in root.iterdir() if path.is_file()}
        if root.is_dir()
        else set()
    )
    if actual != expected:
        findings.append("retained package member set differs")
    with zipfile.ZipFile(source) as archive:
        for part in inventory["parts"]:
            path = root / part["sha256"]
            if not path.is_file() or path.read_bytes() != archive.read(part["path"]):
                findings.append(f"package retention mismatch: {part['path']}")
    return {
        "status": "FAIL" if findings else "PASS",
        "findings": findings,
        "part_count": len(inventory["parts"]),
    }


def preserve_source_nodes(source: Path, vault: Path) -> dict:
    """Retain full source-located IR locally; no invented canonical/OSCAL pointers.

    This explicitly provides opaque retention for every node, including paragraphs
    the current extractors cannot route. It does not claim semantic projection.
    """
    inventory = inspect_package(source)
    document = OPCPackageWalker(source).parse()
    package_root = vault / "_source" / "package"
    package_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        for part in inventory["parts"]:
            (package_root / part["sha256"]).write_bytes(archive.read(part["path"]))
    (vault / "_source/package-manifest.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    package_evidence = verify_package_retention(source, vault)
    target = vault / "_source" / "nodes.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    records = [node.to_dict() for node in document.nodes]
    deltas = retain_ir_records(target, records)
    tables = table_dispositions(document)
    return {
        "status": "PASS"
        if package_evidence["status"] == "PASS"
        and all(d["category"] == "opaque_preserved" for d in deltas)
        else "FAIL",
        "structured_count": 0,
        "opaque_preserved_count": len(deltas),
        "review_note": "Source IR retained locally; semantic projection is evaluated separately at L2.",
        "nodes": deltas,
        "tables": tables,
        "package_retention": package_evidence,
    }


def retain_ir_records(target: Path, records: list[dict]) -> list[dict]:
    target.write_text(
        json.dumps(records, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    recovered = json.loads(target.read_text(encoding="utf-8"))
    if len(recovered) != len(records) or len(
        {node["locator"]["stable_id"] for node in recovered}
    ) != len(records):
        raise ValueError("source IR count or locator uniqueness differs")
    deltas = []
    for index, node in enumerate(records):
        locator = node["locator"]["stable_id"]
        retained = recovered[index] == node
        deltas.append(
            {
                "locator": locator,
                "category": "opaque_preserved" if retained else "new_loss",
                "evidence": f"_source/nodes.json#/{index}",
                "sha256": digest(json.dumps(node, sort_keys=True).encode()),
                "kind": node["kind"],
                "source_disposition": node["disposition"],
            }
        )
    return deltas


def table_dispositions(document) -> list[dict]:
    tables = []
    for table in document.tables:
        merged = any(
            cell.grid_span > 1 or cell.v_merge is not None
            for row in table.cells
            for cell in row
        )
        tables.append(
            {
                "source_table_id": table.node_id,
                "locator": table.locator.stable_id,
                "projection_status": "preserved_only",
                "merged": merged,
                "row_count": table.num_rows,
                "column_count": table.num_cols,
                "cell_locators": [
                    cell.cell_locator for row in table.cells for cell in row
                ],
            }
        )
    return tables
