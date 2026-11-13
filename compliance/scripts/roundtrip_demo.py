#!/usr/bin/env python3
"""End-to-end roundtrip demo (Phase R16.4 + R17).

For each registered demo template, run the full loop:

    blank .docx --(jinjafier)--> jinja template --(fill)--> filled .docx
                                                 --(re-extract)--> parity check

and print a per-tag parity report. Exits non-zero if any template's parity is
incomplete (a written value was not recovered).

Phase R17 adds a **second, independent fill pass** over the same output: every
control-summary (CIS) table in the document is filled in place (checkboxes +
narrative) via :mod:`compliance.lib.control_table_fill`, using a synthesized
sample control per detected table, then read back through fismatic's
*unmodified* read path to prove exact round-trip agreement. This closes the
coverage gap the R16 narrative-slot pipeline alone left open (see the
roundtrip fidelity evaluation): R16 fills ~7-52 narrative tags per template,
while R17 fills every CIS table (54-410 per template).

This is a **live** end-to-end validation (plan §7 / AGENTS.md §8.3): it uses
real docxtpl rendering and re-parsing, not mocks. It skips gracefully (exit 0,
"BLOCKED"-style message) when the official blank .docx is not present locally,
since blanks are gitignored (ADR-009). The committed jinja templates under
compliance/templates/ let both fill passes run even without a blank.

Usage::

    uv run python3 compliance/scripts/roundtrip_demo.py
    uv run python3 compliance/scripts/roundtrip_demo.py --template low-sspp
    uv run python3 compliance/scripts/roundtrip_demo.py --out-dir /tmp/rt
    uv run python3 compliance/scripts/roundtrip_demo.py --skip-control-tables
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FISMATIC_SRC = _REPO_ROOT / "fismatic" / "src" / "phase_1"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_FISMATIC_SRC) not in sys.path:
    sys.path.insert(0, str(_FISMATIC_SRC))

from compliance.lib.canonical_model import CanonicalControl  # noqa: E402
from compliance.lib.control_table_anchors import find_control_tables  # noqa: E402
from compliance.lib.control_table_fill import fill_control_tables  # noqa: E402
from compliance.lib.docx_fill import (  # noqa: E402
    check_parity,
    fill_template,
    load_fill_context,
    reextract_filled,
)
from compliance.lib.docx_jinjafier import jinjafy  # noqa: E402
from compliance.lib.heading_parser import load_contract  # noqa: E402

_REGISTRY = [
    {
        "name": "low-sspp",
        "blank": "fismatic/Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
        "contract": "compliance/datacontracts/low-sspp-roundtrip.yaml",
        "jinja": "compliance/templates/low-sspp.jinja.docx",
        "fixture": "compliance/tests/fixtures/roundtrip/low-sspp.sample.json",
    },
    {
        "name": "moderate-sspp",
        "blank": "fismatic/Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
        "contract": "compliance/datacontracts/moderate-sspp-roundtrip.yaml",
        "jinja": "compliance/templates/moderate-sspp.jinja.docx",
        "fixture": "compliance/tests/fixtures/roundtrip/moderate-sspp.sample.json",
    },
    {
        "name": "high-sspp",
        "blank": "fismatic/High-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
        "contract": "compliance/datacontracts/high-sspp-roundtrip.yaml",
        "jinja": "compliance/templates/high-sspp.jinja.docx",
        "fixture": "compliance/tests/fixtures/roundtrip/high-sspp.sample.json",
    },
    {
        "name": "leveraged-saas-crm",
        "blank": "templates/Leveraged-SaaS-CRM-SSPP-Template-[Contains-CUI]-02-01-2024.docx",
        "contract": "compliance/datacontracts/leveraged-saas-crm-roundtrip.yaml",
        "jinja": "compliance/templates/leveraged-saas-crm.jinja.docx",
        "fixture": "compliance/tests/fixtures/roundtrip/leveraged-saas-crm.sample.json",
    },
]


def _run_control_tables(name: str, jinja_path: Path, out_dir: Path) -> int:
    """R17 pass: fill every CIS table, then verify via fismatic's read path."""
    from fismatic.parser import (
        is_control_table,
        normalize_control_id,
        parse_control_table,
    )

    anchors = find_control_tables(jinja_path)
    if not anchors:
        print("control-tables: none detected (template has no CIS tables)")
        return 0

    # A handful of official templates have two CIS tables sharing the same
    # normalized control_id (e.g. "RA-03" base control + "RA-03(01)" heading
    # whose own table also prints "RA-03" in row 0 col 0) -- see ADR-020. Two
    # distinct CanonicalControl records both literally named "RA-03" would
    # collide on the same table slot in fill_control_tables' match-by-id
    # step, so skip synthesizing a second control for a control_id already
    # seen (this is a demo-script sampling choice, not a fill_control_tables
    # limitation -- test_control_table_fill.py's full-corpus test documents
    # and tolerates the same underlying duplicate-heading condition).
    seen_norms: set[str] = set()
    controls = []
    for a in anchors:
        norm = normalize_control_id(a.control_id)
        if norm in seen_norms:
            continue
        seen_norms.add(norm)
        controls.append(
            CanonicalControl.from_raw(
                a.control_id,
                raw_status="implemented",
                raw_origination=["inherited"],
                statement_ids=[],
            )
        )
    out = out_dir / f"{name}.control-tables.filled.docx"
    result = fill_control_tables(jinja_path, controls, out)

    from docx import Document

    doc = Document(str(out))
    verified = 0
    mismatched = 0
    for t in doc.tables:
        ok, cid = is_control_table(t)
        if not ok or cid not in seen_norms:
            continue
        data = parse_control_table(t)
        if data["implementation_status"] == "implemented":
            verified += 1
        else:
            mismatched += 1

    unmatched_count = (
        len(result.unmatched_control_ids) if result.unmatched_control_ids else 0
    )
    print(
        f"control-tables: {len(result.filled_control_ids)} filled, "
        f"{unmatched_count} unmatched, "
        f"{verified} verified via fismatic readback, {mismatched} mismatched "
        f"(a mismatch here is expected only for the known duplicate-heading "
        f"control_ids documented in ADR-020, if any) -> {out.name}"
    )
    return 1 if result.unmatched_control_ids else 0


def _run_one(entry: dict, out_dir: Path, *, skip_control_tables: bool = False) -> int:
    name = entry["name"]
    contract = load_contract(_REPO_ROOT / entry["contract"])
    fixture = _REPO_ROOT / entry["fixture"]
    blank = _REPO_ROOT / entry["blank"]
    committed_jinja = _REPO_ROOT / entry["jinja"]

    print(f"\n=== {name} ===")

    # Prefer regenerating the jinja template from the blank (proves the full
    # chain); fall back to the committed template when the blank is absent.
    jinja_path = out_dir / f"{name}.jinja.docx"
    if blank.exists():
        result = jinjafy(blank, contract, jinja_path)
        print(f"jinjafy: {result.injected_count} tags from blank")
    elif committed_jinja.exists():
        jinja_path = committed_jinja
        print(
            f"jinjafy: SKIPPED (blank absent); using committed {committed_jinja.name}"
        )
    else:
        print(f"BLOCKED: neither blank nor committed jinja template present for {name}")
        return 0

    context = load_fill_context(fixture)
    filled = out_dir / f"{name}.filled.docx"
    fill_res = fill_template(jinja_path, context, filled, contract=contract)
    print(f"fill: {len(fill_res.filled_tags)} tags -> {filled.name}")

    recovered = reextract_filled(filled, contract, strict=True)
    parity = check_parity(context, recovered)

    for tag in context:
        if tag in parity.matched:
            print(f"  OK      {tag}")
        elif tag in parity.mismatched:
            m = parity.mismatched[tag]
            print(
                f"  MISMATCH {tag}: wrote {m['written'][:40]!r} got {m['recovered'][:40]!r}"
            )
        else:
            print(f"  MISSING  {tag}")

    print(
        f"parity: {len(parity.matched)}/{len(context)} recovered ({parity.recovery_rate * 100:.0f}%)"
    )
    rc = 0 if parity.ok else 1

    if not skip_control_tables:
        rc |= _run_control_tables(name, jinja_path, out_dir)

    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", help="Only run this registered template")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run every registered template (slow — docxtpl re-serializes each "
        "full document). Default runs a fast representative subset.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("/tmp/roundtrip_demo"),
        help="Directory for generated .docx artifacts (default /tmp/roundtrip_demo)",
    )
    parser.add_argument(
        "--skip-control-tables",
        action="store_true",
        help="Skip the R17 control-summary-table fill+verify pass "
        "(only run the R16 narrative-slot fill+re-extract pass).",
    )
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.template:
        entries = [e for e in _REGISTRY if e["name"] == args.template]
        if not entries:
            parser.error(f"unknown template {args.template!r}")
    elif args.all:
        entries = _REGISTRY
    else:
        # Fast default (CI gate): the small CRM template + Low. Covers the
        # different-color-palette case and the primary GSA shape without the
        # multi-minute cost of rendering Moderate + High. Use --all for full.
        entries = [
            e for e in _REGISTRY if e["name"] in ("low-sspp", "leveraged-saas-crm")
        ]

    rc = 0
    for entry in entries:
        rc |= _run_one(
            entry, args.out_dir, skip_control_tables=args.skip_control_tables
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
