#!/usr/bin/env python3
"""Generate CycloneDX topology and C4 Mermaid for vault systems and score them.

Reads each system's Ports, Protocols, and Services tables, builds one ``Topology``, renders
the CycloneDX blueprint and C4 levels 1 and 2 from it, and applies the rubric in
docs/rubrics/topology-generator-rubric.yaml. When a hand-drawn Mermaid C4 note exists
(the system's ``Diagrams/*.md``) it is parsed and compared.

Examples:
  evaluate_topology_diagrams.py --vault <vault-dir> --system example-system
  evaluate_topology_diagrams.py --vault <vault-dir> --all --out report.json
  evaluate_topology_diagrams.py --vault <vault-dir> --all --baseline old.json
  evaluate_topology_diagrams.py --vault <vault-dir> --system example-system --write-vault
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compliance.lib.topology import build_topology, load_pps_rows  # noqa: E402
from compliance.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_arguments,
    guard_output,
)
from compliance.lib.topology_c4 import render_container, render_context  # noqa: E402
from compliance.lib.topology_cyclonedx import to_cyclonedx_blueprint  # noqa: E402
from compliance.lib.topology_eval import (  # noqa: E402
    band,
    cdx_checks,
    compare_c4,
    consistency,
    load_rubric,
    mermaid_blocks,
    parse_c4,
    readability,
    validate_generated_c4,
)
from compliance.lib.topology_vault import write_generated_blocks  # noqa: E402

RUBRIC = REPO_ROOT / "docs" / "rubrics" / "topology-generator-rubric.yaml"


def _display(note_dir: Path, slug: str) -> str:
    index = note_dir / "Index.md"
    if index.is_file():
        for line in index.read_text(encoding="utf-8").splitlines():
            if line.startswith("title:"):
                return line.split(":", 1)[1].strip().strip("\"'") or slug
    return slug


def _reference(system_dir: Path):
    best = None
    for note in sorted((system_dir / "Diagrams").glob("*.md")):
        text = note.read_text(encoding="utf-8")
        for block in mermaid_blocks(_strip_generated(text)):
            if block.lstrip().startswith(("C4Container", "C4Context")):
                diagram = parse_c4(block)
                if best is None or len(diagram.elements) > len(best.elements):
                    best = diagram
    return best


def _strip_generated(text: str) -> str:
    out, skipping = [], False
    for line in text.splitlines():
        if line.startswith("<!-- generated:") and line.endswith("begin -->"):
            skipping = True
        if not skipping:
            out.append(line)
        if line.startswith("<!-- generated:") and line.endswith("end -->"):
            skipping = False
    return "\n".join(out)


def _density_score(stats: dict[str, float], limits: dict[str, int]) -> int:
    over = sum(1 for k, v in limits.items() if stats[k] > v)
    return max(0, 4 - over)


def score_system(slug: str, system_dir: Path, rubric: dict[str, Any]) -> dict[str, Any]:
    rows = load_pps_rows(system_dir)
    topo = build_topology(slug, _display(system_dir, slug), rows)
    if not topo.flows:
        return {"system": slug, "status": "no-pps-table", "scores": {}}

    blueprint = to_cyclonedx_blueprint(topo)
    context, container = render_context(topo), render_container(topo)
    again = render_container(
        build_topology(slug, _display(system_dir, slug), load_pps_rows(system_dir))
    )

    gates = {
        "parses": None,  # checked by the Mermaid parser step in CI, not in-process
        "refs-resolve": bool(
            cdx_checks(blueprint)["refs_resolve"]
            and cdx_checks(blueprint)["refs_unique"]
        ),
        "deterministic": container == again,
        "no-sensitive-values": "http" not in context + container,
    }
    structure = {
        "context": validate_generated_c4(context),
        "container": validate_generated_c4(container),
    }
    cdx = cdx_checks(blueprint)
    agree = consistency(topo, blueprint)
    dens = readability(topo, container)

    ref = _reference(system_dir)
    comparison = compare_c4(topo, ref, "container") if ref else None
    metrics: dict[str, float] = {}
    if comparison:
        metrics.update(comparison["metrics"])
    metrics.update(cdx)
    metrics["cross_generator_consistency"] = min(agree.values())

    scores: dict[str, Any] = {}
    for dim in rubric["dimensions"]:
        did = dim["id"]
        if dim.get("review") == "human":
            scores[did] = None
        elif did == "readability":
            scores[did] = _density_score(dens, dim["limits"])
        elif dim.get("needs_reference") and not comparison:
            scores[did] = None
        else:
            key = dim["metric"]
            scores[did] = band(
                metrics.get(key, metrics.get("cross_generator_consistency", 0.0)),
                dim["bands"],
            )

    scored = [
        (d["weight"], scores[d["id"]])
        for d in rubric["dimensions"]
        if scores[d["id"]] is not None
    ]
    average = (
        sum(w * s for w, s in scored) / sum(w for w, _ in scored) if scored else 0.0
    )
    return {
        "system": slug,
        "status": "blocked"
        if not all(v for k, v in gates.items() if v is not None)
        else "scored",
        "gates": gates,
        "counts": {"components": len(topo.components), "flows": len(topo.flows)},
        "structure": structure,
        "readability": dens,
        "consistency": agree,
        "reference": comparison,
        "scores": scores,
        "weighted_average": round(average, 2),
        "scored_weight_fraction": round(
            sum(w for w, _ in scored) / sum(d["weight"] for d in rubric["dimensions"]),
            2,
        ),
        "has_reference": comparison is not None,
        "human_review_pending": [
            d["id"] for d in rubric["dimensions"] if d.get("review") == "human"
        ],
    }


def compare_to_baseline(current: list[dict], baseline: list[dict]) -> list[str]:
    old = {r["system"]: r for r in baseline}
    out = []
    for row in current:
        prev = old.get(row["system"])
        if not prev:
            continue
        for dim, score in row["scores"].items():
            was = prev["scores"].get(dim)
            if score is not None and was is not None and score < was:
                out.append(f"{row['system']}: {dim} regressed {was} -> {score}")
        if row.get("weighted_average", 0) < prev.get("weighted_average", 0):
            out.append(
                f"{row['system']}: weighted average fell {prev['weighted_average']} -> {row['weighted_average']}"
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Directory holding one folder per system",
    )
    parser.add_argument("--system", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--out", type=Path, help="Write the JSON report here")
    parser.add_argument(
        "--baseline", type=Path, help="Earlier JSON report to compare against"
    )
    parser.add_argument(
        "--write-vault",
        action="store_true",
        help="Write generated C4 blocks into each Diagrams note",
    )
    add_workspace_arguments(parser)
    args = parser.parse_args(argv)
    try:
        if args.out:
            args.out = guard_output(args.out, allow_in_core=args.allow_write_in_core)
        if args.write_vault:
            guard_output(args.vault, allow_in_core=args.allow_write_in_core)
    except WorkspaceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    rubric = load_rubric(RUBRIC)
    slugs = (
        sorted(
            p.name
            for p in args.vault.iterdir()
            if p.is_dir() and not p.name.startswith(("_", "."))
        )
        if args.all
        else args.system
    )
    if not slugs:
        parser.error("give --system or --all")

    results = [score_system(s, args.vault / s, rubric) for s in slugs]
    for r in results:
        if r["status"] == "no-pps-table":
            print(f"{r['system']:36} no PPS table")
            continue
        ref = "ref" if r["has_reference"] else "no-ref"
        print(
            f"{r['system']:36} {r['status']:8} avg={r['weighted_average']:<5} "
            f"scored={int(r['scored_weight_fraction'] * 100)}% {ref}"
        )

    if args.write_vault:
        for slug in slugs:
            topo = build_topology(
                slug,
                _display(args.vault / slug, slug),
                load_pps_rows(args.vault / slug),
            )
            if topo.flows:
                changed = write_generated_blocks(
                    args.vault / slug,
                    {
                        "c4-context": render_context(topo),
                        "c4-container": render_container(topo),
                    },
                )
                print(f"{slug}: {'updated' if changed else 'unchanged'}")

    if args.out:
        args.out.write_text(
            json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.baseline:
        regressions = compare_to_baseline(
            results, json.loads(args.baseline.read_text(encoding="utf-8"))
        )
        for line in regressions:
            print("REGRESSION", line)
        return 1 if regressions else 0
    return 1 if any(r["status"] == "blocked" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
