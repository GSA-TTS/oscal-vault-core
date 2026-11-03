#!/usr/bin/env python3
"""DOCX SSP extraction coverage and integrity metrics.

Quantifies how much of a FedRAMP SSP ``.docx`` the current oscal-vault
extraction path reaches, and how faithfully what it reaches round-trips.

Three complementary metric families are reported:

* **Structural coverage** - of every content-bearing body block (paragraph,
  table, image) in document order, how many are routed by at least one
  extractor (R17 CIS control tables, R16.1 instruction anchors, R18 per-control
  guidance, R19 front-matter section guidance) vs. silently dropped.

* **Semantic coverage** - of the fixed set of FedRAMP SSP information elements
  a Moderate SSP must carry (system identity, categorization, roles, boundary,
  inventory, data flow, interconnections, control responses, appendices), how
  many are backed by an extractor that produces structured output vs. only seen
  as unrouted prose/tables.

* **Integrity** - of the control-summary (CIS) tables that R17 reaches, how
  many read back through ``fismatic.parser``, and whether narrative Part rows
  survive extraction.

Read-only: the document is never mutated on disk. Exit code is 0 unless
``--min-structural`` / ``--min-semantic`` / ``--min-readback`` gates are supplied
and unmet.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FISMATIC_SRC = REPO_ROOT / "fismatic" / "src" / "phase_1"

for _p in (REPO_ROOT, FISMATIC_SRC):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))


_SEMANTIC_ELEMENTS: list[dict[str, Any]] = [
    {
        "key": "system_identity",
        "headings": ["Information System Name"],
        "routed_by": None,
    },
    {
        "key": "categorization",
        "headings": [
            "Information System Categorization",
            "Information Types",
            "Potential Impacts",
        ],
        "routed_by": None,
    },
    {
        "key": "digital_identity",
        "headings": ["Digital Identity Acceptance"],
        "routed_by": None,
    },
    {
        "key": "roles_and_contacts",
        "headings": [
            "Information System Owner",
            "Authorizing Official",
            "Assignment of Security Responsibility",
            "Other Designated Contacts",
        ],
        "routed_by": None,
    },
    {
        "key": "operational_status",
        "headings": [
            "Information System Operational Status",
            "Information System Type",
        ],
        "routed_by": None,
    },
    {
        "key": "control_provision",
        "headings": ["Systems Providing Controls", "Systems Receiving Controls"],
        "routed_by": None,
    },
    {
        "key": "system_description",
        "headings": [
            "General System Description",
            "Information System Locations",
            "Information System Components and Boundaries",
        ],
        "routed_by": "R19",
    },
    {"key": "web_urls", "headings": ["Web Site URL"], "routed_by": None},
    {"key": "user_types", "headings": ["Types of Users"], "routed_by": None},
    {
        "key": "network_architecture",
        "headings": ["Network Architecture"],
        "routed_by": "R19",
    },
    {
        "key": "asset_inventory",
        "headings": ["Asset Inventory", "Software Inventory"],
        "routed_by": None,
    },
    {"key": "external_services", "headings": ["External Services"], "routed_by": "R19"},
    {"key": "data_flow", "headings": ["Data Flow"], "routed_by": "R19"},
    {"key": "ports_protocols", "headings": ["Ports, Protocols"], "routed_by": None},
    {
        "key": "devsecops_runbooks",
        "headings": [
            "DevOps/DevSecOps",
            "Container-Based Workload",
            "AWS Management",
            "Container Image",
            "Container Orchestration",
        ],
        "routed_by": "R19",
    },
    {
        "key": "interconnections",
        "headings": ["System Interconnections"],
        "routed_by": None,
    },
    {"key": "applicable_laws", "headings": ["Applicable Laws"], "routed_by": None},
    {
        "key": "control_responses",
        "headings": ["Minimum Security Controls"],
        "routed_by": "R17",
    },
    {"key": "acronyms", "headings": ["Acronyms, Terms"], "routed_by": None},
    {"key": "references", "headings": ["References"], "routed_by": None},
    {"key": "hosted_subsystems", "headings": ["Hosted Subsystems"], "routed_by": None},
]


@dataclass
class SectionResidual:
    heading: str
    dropped_prose_paras: int = 0
    dropped_prose_chars: int = 0
    dropped_tables: int = 0
    instruction_paras: int = 0


@dataclass
class StructuralCoverage:
    total_blocks: int = 0
    routed_blocks: int = 0
    dropped_blocks: int = 0
    total_paragraphs: int = 0
    heading_paragraphs: int = 0
    instruction_paragraphs: int = 0
    prose_paragraphs: int = 0
    total_tables: int = 0
    routed_tables: int = 0
    dropped_tables: int = 0
    total_images: int = 0
    routed_images: int = 0
    dropped_prose_chars: int = 0

    @property
    def routable_block_coverage(self) -> float:
        routable = self.routed_blocks + self.dropped_blocks
        return (self.routed_blocks / routable) if routable else 0.0


@dataclass
class SemanticCoverage:
    total_elements: int = 0
    routed_elements: int = 0
    present_elements: int = 0
    elements: list[dict[str, Any]] = field(default_factory=list)

    @property
    def routed_fraction(self) -> float:
        return (
            (self.routed_elements / self.total_elements) if self.total_elements else 0.0
        )

    @property
    def present_fraction(self) -> float:
        return (
            (self.present_elements / self.total_elements)
            if self.total_elements
            else 0.0
        )


@dataclass
class IntegrityMetrics:
    cis_tables_detected: int = 0
    cis_tables_readback_ok: int = 0
    cis_tables_readback_fail: int = 0
    controls_with_part_rows: int = 0
    total_part_narrative_slots: int = 0
    filled_part_narrative_slots: int = 0
    part_rows_extracted: int = 0

    @property
    def readback_fraction(self) -> float:
        return (
            self.cis_tables_readback_ok / self.cis_tables_detected
            if self.cis_tables_detected
            else 0.0
        )

    @property
    def part_narrative_fraction(self) -> float:
        if not self.filled_part_narrative_slots:
            return 1.0
        return min(1.0, self.part_rows_extracted / self.filled_part_narrative_slots)


def _load_doc(docx_path: Path):
    from docx import Document

    return Document(str(docx_path))


def _iter_body_blocks(doc):
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield "paragraph", Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield "table", Table(child, doc)


def _count_images(para) -> int:
    ns_a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    return len(para._p.findall(f".//{{{ns_a}}}blip"))


def _normalize_heading(text: str) -> str:
    return " ".join((text or "").split()).strip()


def collect(docx_path: Path) -> dict[str, Any]:
    """Run every extractor read-only and compute the three metric families."""
    from compliance.lib.control_table_anchors import find_control_tables
    from compliance.lib.instruction_anchors import (
        DEFAULT_INSTRUCTION_COLORS,
        DEFAULT_INSTRUCTION_TEXT_PREFIXES,
        _heading_level,
        instruction_text_kind,
        is_instruction_colored,
    )
    from compliance.scripts.classify_dropped_blocks import (
        CONTROL_TITLE,
        DISPOSITION,
        classify as _classify_dropped,
        collect as _collect_dropped,
    )

    doc = _load_doc(docx_path)
    doc_tables = list(doc.tables)
    control_tables = find_control_tables(doc)
    routed_table_ids = {id(a.table._tbl) for a in control_tables}
    try:
        from compliance.lib import heading_parser

        contract_path = (
            REPO_ROOT / "compliance" / "datacontracts" / "fedramp-ssp-v1.yaml"
        )
        if contract_path.is_file():
            contract = heading_parser.load_contract(contract_path)
            extracted = heading_parser.extract_from_docx(docx_path, contract)
            for obj_rec in extracted.get("objects") or []:
                for val in (obj_rec.get("properties") or {}).values():
                    items = val if isinstance(val, list) else [val]
                    for item in items:
                        if isinstance(item, dict) and isinstance(
                            item.get("table_index"), int
                        ):
                            t_idx = item["table_index"]
                            if 0 <= t_idx < len(doc_tables):
                                routed_table_ids.add(id(doc_tables[t_idx]._tbl))
    except Exception:
        pass

    def _is_instruction(para) -> bool:
        text = (para.text or "").strip()
        if not text:
            return False
        if instruction_text_kind(text, DEFAULT_INSTRUCTION_TEXT_PREFIXES) is not None:
            return True
        return is_instruction_colored(para, set(DEFAULT_INSTRUCTION_COLORS))

    struct = StructuralCoverage()
    residual: dict[str, SectionResidual] = {}
    heading_seen: dict[str, dict[str, int]] = defaultdict(
        lambda: {"prose": 0, "tables": 0, "routed_tables": 0, "instr": 0}
    )

    current_heading = "(front matter)"
    for kind, obj in _iter_body_blocks(doc):
        if kind == "paragraph":
            struct.total_paragraphs += 1
            struct.total_blocks += 1
            raw_text = (obj.text or "").strip()
            if (
                _heading_level(obj) is not None or CONTROL_TITLE.match(raw_text)
            ) and raw_text:
                struct.heading_paragraphs += 1
                current_heading = _normalize_heading(raw_text)
                continue
            imgs = _count_images(obj)
            if imgs:
                struct.total_images += imgs
            text = (obj.text or "").strip()
            if not text and not imgs:
                continue
            if _is_instruction(obj):
                struct.instruction_paragraphs += 1
                struct.routed_blocks += 1
                heading_seen[current_heading]["instr"] += 1
            elif text:
                struct.prose_paragraphs += 1
                struct.dropped_blocks += 1
                struct.dropped_prose_chars += len(text)
                r = residual.setdefault(
                    current_heading, SectionResidual(current_heading)
                )
                r.dropped_prose_paras += 1
                r.dropped_prose_chars += len(text)
                heading_seen[current_heading]["prose"] += 1
        else:
            struct.total_blocks += 1
            struct.total_tables += 1
            heading_seen[current_heading]["tables"] += 1
            if id(obj._tbl) in routed_table_ids:
                struct.routed_tables += 1
                struct.routed_blocks += 1
                heading_seen[current_heading]["routed_tables"] += 1
            else:
                struct.dropped_tables += 1
                struct.dropped_blocks += 1
                r = residual.setdefault(
                    current_heading, SectionResidual(current_heading)
                )
                r.dropped_tables += 1

    for h, seen in heading_seen.items():
        r = residual.setdefault(h, SectionResidual(h))
        r.instruction_paras = seen["instr"]

    all_headings = list(heading_seen.keys())

    def _element_present(headings: list[str]) -> tuple[bool, dict[str, int]]:
        agg = {"prose": 0, "tables": 0, "routed_tables": 0, "instr": 0}
        found = False
        for h in all_headings:
            if any(needle.lower() in h.lower() for needle in headings):
                found = True
                s = heading_seen[h]
                for k in agg:
                    agg[k] += s[k]
        return found, agg

    sem = SemanticCoverage()
    for elem in _SEMANTIC_ELEMENTS:
        present, agg = _element_present(elem["headings"])
        routed = False
        if elem["routed_by"] == "R17":
            routed = struct.routed_tables > 0
        elif elem["routed_by"] == "R19" and agg["instr"] > 0:
            routed = True
        sem.total_elements += 1
        if present:
            sem.present_elements += 1
        if routed:
            sem.routed_elements += 1
        sem.elements.append(
            {
                "element": elem["key"],
                "present": present,
                "routed": routed,
                "routed_by": elem["routed_by"],
                "dropped_prose_paras": agg["prose"],
                "dropped_tables": agg["tables"] - agg["routed_tables"],
            }
        )

    # 7 Progressive Coverage Dimensions (#53)
    # Computed strictly from actual package inventory and node-level dispositions:
    # never fabricated or defaulted on parse failures.
    pkg_coverage = 0.0
    opaque_preservation = 0.0
    try:
        from compliance.lib.docx_ir import OPCPackageWalker

        walker = OPCPackageWalker(docx_path)
        doc_ir = walker.parse()
        inv_summary = doc_ir.inventory.summary()
        pkg_parts = inv_summary["total_parts"]
        total_nodes = inv_summary["total_nodes"]
        disps = inv_summary["disposition_counts"]
        unmapped_nodes = disps.get("unmapped_error", 0)
        # Package part coverage: 1.0 only if parts exist and all nodes are classified
        if pkg_parts > 0 and total_nodes > 0:
            pkg_coverage = max(0.0, (total_nodes - unmapped_nodes) / total_nodes)
        if total_nodes > 0:
            opaque_preservation = disps.get("opaque_preserved", 0) / total_nodes
    except Exception:
        pkg_coverage = 0.0
        opaque_preservation = 0.0

    integ = _integrity(control_tables)
    # Progressive Coverage Dimensions (#53).
    #
    # Each value below is computed from real inventory/parse data. The two
    # cross-stage field-parity dimensions are intentionally NOT computed from a
    # single .docx: canonical<->OSCAL field parity requires both stages' JSON,
    # which this tool (operating on one source .docx) does not have. Reporting
    # the CIS readback number under those names was a fabrication -- it measured
    # neither the canonical nor the OSCAL field set. They are therefore reported
    # as null here and delegated to validate_oscal.py --parity (which has both
    # stages and now also diffs parameter VALUES and per-control statement
    # counts). `cis_readback_fidelity` is reported under its true name.
    unrouted_disposition: dict[str, dict[str, int]] = {}
    try:
        dropped_rep = _collect_dropped(docx_path)
        _classify_dropped(dropped_rep, fuzzy=True)
        by_cls = dropped_rep.by_class()
        unrouted_disposition = {
            name: {
                "blocks": sum(by_cls.get(cls, {}).get("blocks", 0) for cls in members),
                "chars": sum(by_cls.get(cls, {}).get("chars", 0) for cls in members),
            }
            for name, members in DISPOSITION.items()
        }
    except Exception:
        unrouted_disposition = {}

    progressive_dimensions = {
        "package_part_coverage": pkg_coverage,
        "structural_node_coverage": struct.routable_block_coverage,
        "semantic_routing_coverage": sem.routed_fraction,
        "cis_readback_fidelity": integ.readback_fraction,
        "canonical_field_parity": None,  # see validate_oscal.py --parity
        "oscal_field_parity": None,  # see validate_oscal.py --parity
        "opaque_preservation_coverage": opaque_preservation,
        "unrouted_blocks": struct.dropped_blocks,
        "unrouted_disposition": unrouted_disposition,
        "explicit_loss_budget": struct.dropped_blocks,
    }

    return {
        "docx": str(docx_path),
        "structural": struct,
        "residual": residual,
        "semantic": sem,
        "integrity": integ,
        "progressive_dimensions": progressive_dimensions,
    }


def _integrity(control_tables) -> IntegrityMetrics:
    from fismatic.parser import extract_implementation_details, parse_control_table

    integ = IntegrityMetrics(cis_tables_detected=len(control_tables))
    for anchor in control_tables:
        parsed = None
        try:
            parsed = parse_control_table(anchor.table)
        except Exception:
            parsed = None
        if parsed and parsed.get("control_id"):
            integ.cis_tables_readback_ok += 1
        else:
            integ.cis_tables_readback_fail += 1

        if anchor.part_rows:
            integ.controls_with_part_rows += 1
            integ.total_part_narrative_slots += len(anchor.part_rows)
            integ.filled_part_narrative_slots += sum(
                1 for cell in anchor.part_rows.values() if cell.text.strip()
            )
            try:
                details = extract_implementation_details(
                    {
                        "table": anchor.table,
                        "is_control_table": True,
                        "control_id": parsed.get("control_id") if parsed else None,
                    }
                )
            except Exception:
                details = {}
            integ.part_rows_extracted += len(details.get("implementation", {}))
    return integ


def _pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def render_markdown(result: dict[str, Any]) -> str:
    struct: StructuralCoverage = result["structural"]
    sem: SemanticCoverage = result["semantic"]
    integ: IntegrityMetrics = result["integrity"]
    residual: dict[str, SectionResidual] = result["residual"]

    lines: list[str] = [
        f"# DOCX Extraction Coverage & Integrity - `{Path(result['docx']).name}`",
        "",
        "## 1. Structural coverage (body blocks)",
        "",
        "| metric | value |",
        "|---|---|",
        f"| total body blocks | {struct.total_blocks} |",
        f"| - heading paragraphs | {struct.heading_paragraphs} |",
        f"| - instruction paras (routed R16/R18/R19) | {struct.instruction_paragraphs} |",
        f"| - prose paras (dropped) | {struct.prose_paragraphs} |",
        f"| tables total / routed (R17) / dropped | {struct.total_tables} / {struct.routed_tables} / {struct.dropped_tables} |",
        f"| images total / routed | {struct.total_images} / {struct.routed_images} |",
        f"| **routable-block coverage** | **{_pct(struct.routable_block_coverage)}** |",
        f"| dropped prose (chars) | {struct.dropped_prose_chars} |",
        "",
        "## 2. Semantic coverage (FedRAMP SSP information elements)",
        "",
        f"- elements present in doc: **{sem.present_elements}/{sem.total_elements}** ({_pct(sem.present_fraction)})",
        f"- elements with a structured extractor: **{sem.routed_elements}/{sem.total_elements}** ({_pct(sem.routed_fraction)})",
        "",
        "| element | present | routed | by | dropped prose | dropped tables |",
        "|---|:-:|:-:|:-:|--:|--:|",
    ]
    for e in sem.elements:
        lines.append(
            f"| {e['element']} | {'yes' if e['present'] else '-'} | "
            f"{'yes' if e['routed'] else 'no'} | {e['routed_by'] or '-'} | "
            f"{e['dropped_prose_paras']} | {e['dropped_tables']} |"
        )
    lines += [
        "",
        "## 3. Integrity (R17 CIS readback + narrative survival)",
        "",
        "| metric | value |",
        "|---|---|",
        f"| CIS tables detected | {integ.cis_tables_detected} |",
        f"| - read back OK via fismatic | {integ.cis_tables_readback_ok} |",
        f"| - read back FAILED | {integ.cis_tables_readback_fail} |",
        f"| **CIS readback fidelity** | **{_pct(integ.readback_fraction)}** |",
        f"| controls with Part rows | {integ.controls_with_part_rows} |",
        f"| Part narrative slots (detected) | {integ.total_part_narrative_slots} |",
        f"| Part narrative slots (non-empty in source) | {integ.filled_part_narrative_slots} |",
        f"| Part narrative slots (extracted) | {integ.part_rows_extracted} |",
        f"| **Part narrative survival** | **{_pct(integ.part_narrative_fraction)}** |",
        "",
        "## 4. Top residual sections (unrouted prose + tables)",
        "",
        "| section | dropped prose | chars | dropped tables |",
        "|---|--:|--:|--:|",
    ]
    ranked = sorted(
        residual.values(),
        key=lambda r: r.dropped_prose_chars + r.dropped_tables * 500,
        reverse=True,
    )
    for r in ranked[:15]:
        if r.dropped_prose_chars or r.dropped_tables:
            lines.append(
                f"| {r.heading[:55]} | {r.dropped_prose_paras} | {r.dropped_prose_chars} | {r.dropped_tables} |"
            )
    lines.append("")

    prog = result.get("progressive_dimensions", {})
    if prog:

        def _opt_pct(key: str) -> str:
            value = prog.get(key)
            return "n/a (see validate_oscal --parity)" if value is None else _pct(value)

        lines += [
            "## 5. Progressive Coverage Dimensions (#53)",
            "",
            "| dimension | metric |",
            "|---|---|",
            f"| package-part coverage | {_pct(prog.get('package_part_coverage', 1.0))} |",
            f"| structural node coverage | {_pct(prog.get('structural_node_coverage', 0.0))} |",
            f"| semantic routing coverage | {_pct(prog.get('semantic_routing_coverage', 0.0))} |",
            f"| CIS readback fidelity | {_pct(prog.get('cis_readback_fidelity', 0.0))} |",
            f"| canonical field parity | {_opt_pct('canonical_field_parity')} |",
            f"| OSCAL field parity | {_opt_pct('oscal_field_parity')} |",
            f"| opaque preservation coverage | {_pct(prog.get('opaque_preservation_coverage', 0.0))} |",
            f"| unrouted blocks | {prog.get('unrouted_blocks', prog.get('explicit_loss_budget', 0))} |",
        ]
        disp = prog.get("unrouted_disposition") or {}
        if disp:
            ref_d = disp.get("reference") or {"blocks": 0, "chars": 0}
            adopt_d = disp.get("adopt") or {"blocks": 0, "chars": 0}
            decl_d = disp.get("decline") or {"blocks": 0, "chars": 0}
            lines += [
                f"| unrouted: reference material | {ref_d['blocks']} blocks / {ref_d['chars']} chars |",
                f"| unrouted: system content | {adopt_d['blocks']} blocks / {adopt_d['chars']} chars |",
                f"| unrouted: furniture and artifacts | {decl_d['blocks']} blocks / {decl_d['chars']} chars |",
            ]
        lines += [
            f"| explicit loss budget (deprecated alias) | {prog.get('explicit_loss_budget', 0)} |",
            "",
        ]

    return "\n".join(lines)


def to_jsonable(result: dict[str, Any]) -> dict[str, Any]:
    struct: StructuralCoverage = result["structural"]
    sem: SemanticCoverage = result["semantic"]
    integ: IntegrityMetrics = result["integrity"]
    residual: dict[str, SectionResidual] = result["residual"]
    return {
        "docx": result["docx"],
        "structural": {
            **asdict(struct),
            "routable_block_coverage": struct.routable_block_coverage,
        },
        "semantic": {
            "total_elements": sem.total_elements,
            "present_elements": sem.present_elements,
            "routed_elements": sem.routed_elements,
            "present_fraction": sem.present_fraction,
            "routed_fraction": sem.routed_fraction,
            "elements": sem.elements,
        },
        "integrity": {
            **asdict(integ),
            "readback_fraction": integ.readback_fraction,
            "part_narrative_fraction": integ.part_narrative_fraction,
        },
        "residual": [asdict(r) for r in residual.values()],
        "progressive_dimensions": result.get("progressive_dimensions", {}),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--docx", required=True, type=Path, help="Path to a FedRAMP SSP .docx."
    )
    ap.add_argument(
        "--json", type=Path, default=None, help="Write metrics JSON to this path."
    )
    ap.add_argument(
        "--min-structural",
        type=float,
        default=None,
        help="Fail if routable-block coverage < this (0-1).",
    )
    ap.add_argument(
        "--min-semantic",
        type=float,
        default=None,
        help="Fail if routed-element fraction < this (0-1).",
    )
    ap.add_argument(
        "--min-readback",
        type=float,
        default=None,
        help="Fail if CIS readback fidelity < this (0-1).",
    )
    ap.add_argument(
        "--min-package-coverage",
        type=float,
        default=None,
        help="Fail if package-part/node classification coverage < this (0-1). "
        "This gate works from the OPC inventory alone and does not require a "
        "real SSP, so it can run in CI against the synthetic golden fixture.",
    )
    ap.add_argument(
        "--max-loss",
        type=int,
        default=None,
        help="Fail if the explicit loss budget (count of dropped prose "
        "paragraphs + unrouted tables) exceeds this. Gates drift in the amount "
        "of silently-dropped content.",
    )
    args = ap.parse_args(argv)

    if not args.docx.is_file():
        print(f"error: docx not found: {args.docx}", file=sys.stderr)
        return 2

    result = collect(args.docx)
    print(render_markdown(result))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(to_jsonable(result), indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.json}", file=sys.stderr)

    rc = 0
    struct: StructuralCoverage = result["structural"]
    sem: SemanticCoverage = result["semantic"]
    integ: IntegrityMetrics = result["integrity"]
    if (
        args.min_structural is not None
        and struct.routable_block_coverage < args.min_structural
    ):
        print(
            f"GATE FAIL: routable-block coverage {struct.routable_block_coverage:.3f} < {args.min_structural}",
            file=sys.stderr,
        )
        rc = 1
    if args.min_semantic is not None and sem.routed_fraction < args.min_semantic:
        print(
            f"GATE FAIL: routed-element fraction {sem.routed_fraction:.3f} < {args.min_semantic}",
            file=sys.stderr,
        )
        rc = 1
    if args.min_readback is not None and integ.readback_fraction < args.min_readback:
        print(
            f"GATE FAIL: CIS readback fidelity {integ.readback_fraction:.3f} < {args.min_readback}",
            file=sys.stderr,
        )
        rc = 1
    if args.min_package_coverage is not None:
        pkg = result.get("progressive_dimensions", {}).get("package_part_coverage", 0.0)
        if pkg < args.min_package_coverage:
            print(
                f"GATE FAIL: package-part coverage {pkg:.3f} < "
                f"{args.min_package_coverage}",
                file=sys.stderr,
            )
            rc = 1
    if args.max_loss is not None and struct.dropped_blocks > args.max_loss:
        print(
            f"GATE FAIL: explicit loss budget {struct.dropped_blocks} > "
            f"{args.max_loss}",
            file=sys.stderr,
        )
        rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
