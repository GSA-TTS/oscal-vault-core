"""CLI tool for DOCX package inventory and disposition reporting (#52).

Usage:
    python3 compliance/scripts/docx_inventory.py path/to/document.docx
    python3 compliance/scripts/docx_inventory.py path/to/document.docx --output inventory.json --strict
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.docx_ir import OPCPackageWalker  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory DOCX package parts, relationships, and nodes with dispositions."
    )
    parser.add_argument("docx_path", help="Path to the .docx file")
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Optional path to write inventory JSON",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Fail if any reachable content-bearing node is unclassified (default: False)",
    )
    args = parser.parse_args()

    docx_path = Path(args.docx_path)
    if not docx_path.is_file():
        print(f"ERROR: File not found: {docx_path}", file=sys.stderr)
        return 1

    walker = OPCPackageWalker(docx_path)
    doc_ir = walker.parse()
    inv = doc_ir.inventory
    summary = inv.summary()

    print(f"Package: {summary['package_path']}")
    print(
        f"Parts: {summary['total_parts']}, Relationships: {summary['total_relationships']}"
    )
    print(f"Total Nodes: {summary['total_nodes']}")
    print("Dispositions:")
    for disp, cnt in sorted(summary["disposition_counts"].items()):
        print(f"  - {disp}: {cnt}")
    print("Node Kinds:")
    for kind, cnt in sorted(summary["kind_counts"].items()):
        print(f"  - {kind}: {cnt}")

    if args.output:
        inv.write_json(args.output)
        print(f"Wrote inventory to: {args.output}")

    if args.strict:
        ok, errors = doc_ir.verify_all_classified(strict=False)
        if not ok:
            print(
                f"\nERROR: Strict validation failed with {len(errors)} unclassified node(s):",
                file=sys.stderr,
            )
            for err in errors[:10]:
                print(f"  - {err}", file=sys.stderr)
            return 1
        print(
            "\nStrict check PASSED: all content-bearing nodes have explicit dispositions."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
