#!/usr/bin/env python3
"""Parse a GSA Common Control Catalog (CIO-IT Security-Privacy-18-90) .docx into tier YAML.

Reads Table 3-3 (Control ID, Control Name, Federal System Control Type,
Vendor/Contractor System Control Type, Inheritance Source) and optionally
Appendix C Table C-1, emitting deterministic YAML with canonical NIST SP 800-53
Rev. 5 control IDs. Narrative implementation text is excluded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "compliance").is_dir() and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from compliance.lib.control_identity import canonical_control_id
except ModuleNotFoundError:
    from control_identity import canonical_control_id  # type: ignore[no-redef]

_HEADER_MARKERS_3_3 = ("Federal System Control Type", "Vendor/ Contractor")
_HEADER_MARKERS_C_1 = ("Control Type", "Platform/ Program Association")


def _document_factory():
    try:
        from docx import Document
    except ImportError:
        sys.stderr.write(
            "python-docx is required. Run: uv run --with python-docx ...\n"
        )
        sys.exit(2)
    return Document


def _normalize_control_id(raw: str) -> str:
    return canonical_control_id(raw) if raw and raw.strip() else ""


def _iter_matching_tables(doc, markers: tuple[str, ...]):
    """Yield every 5-column table whose header row contains all ``markers``."""
    for table in doc.tables:
        if len(table.columns) != 5 or not table.rows:
            continue
        header = [c.text.strip() for c in table.rows[0].cells]
        if all(any(marker in cell for cell in header) for marker in markers):
            yield table


def _extract(doc, markers, mapper) -> list[dict]:
    controls: list[dict] = []
    seen: set[str] = set()
    tables = list(_iter_matching_tables(doc, markers))
    if not tables:
        return controls
    for table in tables:
        for row in table.rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            if not cells[0] or cells[0].lower().startswith("control id"):
                continue
            cid = _normalize_control_id(cells[0])
            if cid in seen:
                raise ValueError(f"duplicate canonical control ID in CCC: {cid}")
            seen.add(cid)
            controls.append(mapper(cid, cells))
    return controls


def parse(
    docx_path: Path,
    revision: str,
    source_sheet: str,
    include_appendix_c: bool = False,
    document_factory=None,
) -> dict:
    factory = document_factory or _document_factory()
    doc = factory(str(docx_path))

    def _row_3_3(cid, cells):
        return {
            "control_id": cid,
            "name": cells[1],
            "federal_type": cells[2],
            "contractor_type": cells[3],
            "inheritance_source": cells[4],
        }

    controls = _extract(doc, _HEADER_MARKERS_3_3, _row_3_3)
    if not controls:
        raise SystemExit("Could not locate Table 3-3 (expected headers not found).")

    appendix_c: list[dict] = []
    if include_appendix_c:

        def _row_c_1(cid, cells):
            return {
                "control_id": cid,
                "name": cells[1],
                "control_type": cells[2],
                "inheritance_source": cells[3],
                "platform_program": cells[4],
            }

        appendix_c = _extract(doc, _HEADER_MARKERS_C_1, _row_c_1)
    return {
        "tier": "gsa-common-controls",
        "name": "GSA Common Control Catalog (CCC)",
        "revision": revision,
        "description": (
            "Enterprise Federal / Vendor-Contractor designations parsed from Table 3-3 "
            "of CIO-IT Security-Privacy-18-90. Narrative implementation text is not "
            "included; consult the source PDF for control-by-control detail."
        ),
        "source_sheet": source_sheet,
        "control_count": len(controls),
        "controls": controls,
        **({"appendix_c": appendix_c} if include_appendix_c else {}),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docx", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--revision", default="Rev 7 draft")
    ap.add_argument(
        "--source-sheet",
        default="CIO-IT Security-Privacy-18-90 (Table 3-3)",
    )
    ap.add_argument(
        "--include-appendix-c",
        action="store_true",
        help="Also parse Appendix C Table C-1 (Platform/Program) into an appendix_c: block.",
    )
    args = ap.parse_args(argv)

    if not args.docx.exists():
        ap.error(f"docx not found: {args.docx}")

    data = parse(args.docx, args.revision, args.source_sheet, args.include_appendix_c)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, width=100, allow_unicode=True)
    extra = f" +{len(data['appendix_c'])} Appendix C" if "appendix_c" in data else ""
    print(f"wrote {args.out} ({data['control_count']} controls{extra})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
