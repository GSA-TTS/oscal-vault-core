#!/usr/bin/env python3
"""Benchmark extractor backends against the FedRAMP SSP corpus.

Runs one or more extraction backends against a set of ``.docx`` files and
emits a structured JSON report + a human-readable Markdown summary.

Usage
-----
::

    # Compare fismatic vs Docling on all SSPs (requires .docx fixtures)
    uv run python3 compliance/scripts/benchmark_extractors.py \\
      --docx fismatic/*.docx \\
      --contract compliance/datacontracts/fedramp-ssp-v1.yaml \\
      --backends fismatic docling \\
      --output benchmark_results.json \\
      --markdown benchmark_results.md

    # Fismatic only (always available, no optional deps)
    uv run python3 compliance/scripts/benchmark_extractors.py \\
      --docx fismatic/*.docx \\
      --backends fismatic

Metrics collected
-----------------
For each (backend, document) pair:

* ``elapsed_s``         — wall-clock extraction time in seconds
* ``heading_count``     — number of headings detected
* ``paragraph_count``   — number of text paragraphs detected
* ``table_count``       — number of tables detected
* ``image_count``       — number of images detected
* ``heading_recall``    — headings found ÷ headings found by baseline (fismatic)
* ``table_recall``      — tables found ÷ tables found by baseline
* ``status``            — ``"ok"`` | ``"error"`` | ``"unavailable"``
* ``error``             — error message if status is ``"error"``

Comparison metrics (when two backends are run)
----------------------------------------------
* ``speedup``           — fismatic_elapsed ÷ docling_elapsed (>1 = docling faster)
* ``heading_delta``     — docling_headings − fismatic_headings
* ``table_delta``       — docling_tables − fismatic_tables
* ``paragraph_delta``   — docling_paragraphs − fismatic_paragraphs
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make compliance/ importable when run from repo root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from compliance.lib.extractor_backend import ExtractedDoc, get_backend, list_backends


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run_single(
    backend_name: str,
    docx_path: Path,
) -> Dict[str, Any]:
    """Run one backend on one document and return a metrics dict."""
    backend = get_backend(backend_name)

    if not backend.is_available():
        return {
            "backend": backend_name,
            "document": docx_path.name,
            "status": "unavailable",
            "error": f"Backend '{backend_name}' dependencies not installed.",
        }

    try:
        doc: ExtractedDoc = backend.extract(docx_path)
        return {
            "backend": backend_name,
            "document": docx_path.name,
            "document_path": str(docx_path),
            "status": "ok",
            "elapsed_s": round(doc.elapsed_s, 4),
            "heading_count": doc.heading_count(),
            "paragraph_count": doc.paragraph_count(),
            "table_count": doc.table_count(),
            "image_count": doc.image_count(),
            "metadata": doc.metadata,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "backend": backend_name,
            "document": docx_path.name,
            "document_path": str(docx_path),
            "status": "error",
            "error": str(exc),
        }


def compute_comparison(
    baseline: Dict[str, Any],
    challenger: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute comparison metrics between baseline and challenger runs."""
    if baseline["status"] != "ok" or challenger["status"] != "ok":
        return {}

    b_elapsed = baseline["elapsed_s"]
    c_elapsed = challenger["elapsed_s"]

    return {
        "speedup": round(b_elapsed / c_elapsed, 3) if c_elapsed > 0 else None,
        "elapsed_delta_s": round(c_elapsed - b_elapsed, 4),
        "heading_delta": challenger["heading_count"] - baseline["heading_count"],
        "paragraph_delta": challenger["paragraph_count"] - baseline["paragraph_count"],
        "table_delta": challenger["table_count"] - baseline["table_count"],
        "image_delta": challenger["image_count"] - baseline["image_count"],
        "heading_recall": (
            round(challenger["heading_count"] / baseline["heading_count"], 3)
            if baseline["heading_count"] > 0
            else None
        ),
        "table_recall": (
            round(challenger["table_count"] / baseline["table_count"], 3)
            if baseline["table_count"] > 0
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Contract coverage  (per-document routing metrics, backend-agnostic)
# ---------------------------------------------------------------------------


def compute_contract_coverage(
    docx_path: Path,
    contract_path: Path,
) -> Dict[str, Any]:
    """Run the contract-driven extractor on one .docx and report routing metrics.

    This is a backend-agnostic measurement: the contract pipeline is the
    same regardless of which primitive extractor was used.  It answers:

    * How many of the contract's schema objects got at least one property
      filled? (objects_routed / objects_total)
    * How many of the contract's properties got a value?
      (properties_filled / properties_total)
    * Which headings in the document have no destination in the contract?
      (unrouted_headings)
    * How many tables in the document were classified as control tables
      (Chapter 13 controls) and emitted by the catchall dumper?
    * Which contract properties were left unfilled and which sourceHeading
      they were looking for? (unfilled_properties)

    The function imports ``heading_parser.extract_from_docx`` lazily so
    that the script remains usable when the contract module is unavailable.
    """
    import sys as _sys

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))

    from compliance.lib.heading_parser import (
        extract_from_docx,
        load_contract,
        parse_catchall_blob,
    )

    contract = load_contract(contract_path)

    # Run the catchall dumper + contract extractor on the docx.
    t0 = time.perf_counter()
    result = extract_from_docx(docx_path, contract)
    elapsed = time.perf_counter() - t0

    summary = result.get("summary", {})

    # Build a set of section titles the catchall dumper saw, then mark which
    # were routed (i.e. matched some property's sourceHeading).
    # We rerun parse_catchall_blob on a fresh dump to discover section titles.
    import contextlib
    import io
    import os

    scripts_dir = str(repo_root / "scripts")
    if scripts_dir not in _sys.path:
        _sys.path.insert(0, scripts_dir)
    import docx_catchall_dump as dcd  # type: ignore

    out_md = Path(os.environ.get("TMPDIR", "/tmp")) / (
        f"_coverage_{Path(docx_path).stem}.md"
    )
    with contextlib.redirect_stdout(io.StringIO()):
        dcd.dump_docx(docx_path, out_md)
    sections = parse_catchall_blob(out_md.read_text(encoding="utf-8"))
    try:
        out_md.unlink()
    except OSError:
        pass

    # A heading is "routed" if at least one contract property's sourceHeading
    # pattern matches it (via _heading_matches semantics: exact, prefix*, regex:).
    def _heading_matches(contract_pat: Optional[str], title: str) -> bool:
        if not contract_pat:
            return False
        pat = contract_pat.strip()
        if pat.startswith("regex:"):
            import re

            return re.match(pat[len("regex:") :].strip(), title) is not None
        if pat.endswith("*"):
            return title.startswith(pat[:-1])
        return pat.lower() == title.lower()

    routed_titles: set = set()
    source_headings: set = set()
    for obj in contract.destinations():
        for pm in obj.properties:
            sh = pm.source_heading
            if sh:
                source_headings.add(sh)
            for sec in sections:
                if _heading_matches(sh, sec.title):
                    routed_titles.add(sec.title)

    # Unrouted headings = section titles that no property is looking for.
    unrouted_headings = [
        {
            "title": sec.title,
            "level": sec.level,
            "paragraph_count": len(sec.paragraphs),
            "table_count": len(sec.tables),
            "image_count": len(sec.images),
        }
        for sec in sections
        if sec.title not in routed_titles
    ]
    # Drop the synthetic "Catchall Dump: ..." H1 the dumper emits.
    unrouted_headings = [
        h for h in unrouted_headings if not h["title"].startswith("Catchall Dump")
    ]
    # Drop the dumper's own appendix headings (metadata/inventory/outline/body).
    # These are produced by the catchall dump itself, not the source document.
    _SYNTHETIC_TITLES = {
        "Document Metadata (core properties)",
        "Document Inventory",
        "Headings Outline (in document order)",
        "Body Content (in document order, grouped under each heading)",
    }
    unrouted_headings = [
        h for h in unrouted_headings if h["title"] not in _SYNTHETIC_TITLES
    ]

    objects_routed = summary.get("objects_with_data", 0)
    objects_total = summary.get("objects_total", 0)
    properties_filled = summary.get("properties_filled", 0)
    properties_total = summary.get("properties_total", 0)
    # D-12 / #47: capturable excludes `derived`/UUID properties the parser can
    # never fill from headings, so the "capturable" fill rate is the honest
    # measure of content actually recoverable from the .docx.
    properties_capturable = summary.get("properties_capturable", properties_total)
    fill_rate = (
        round(properties_filled / properties_total, 3) if properties_total else 0.0
    )
    capturable_fill_rate = (
        round(properties_filled / properties_capturable, 3)
        if properties_capturable
        else 0.0
    )

    # Control tables: count `CONTROL TABLE (CAPTURED)` markers in the dump
    # (the catchall dumper labels them; this is backend-agnostic).
    control_tables_found = sum(
        1 for sec in sections for tbl in sec.tables if tbl.control_id is not None
    )

    return {
        "elapsed_s": round(elapsed, 4),
        "objects_total": objects_total,
        "objects_routed": objects_routed,
        "properties_total": properties_total,
        "properties_filled": properties_filled,
        "properties_capturable": properties_capturable,
        "fill_rate": fill_rate,
        "capturable_fill_rate": capturable_fill_rate,
        "control_tables_found": control_tables_found,
        "routed_headings": len(routed_titles),
        "unrouted_heading_count": len(unrouted_headings),
        "unrouted_headings": unrouted_headings[:50],  # cap for readability
        "unrouted_headings_truncated": len(unrouted_headings) > 50,
        "unfilled_properties": [
            {
                "object_id": u["object_id"],
                "name": u["name"],
                "source_heading": u.get("sourceHeading"),
            }
            for u in summary.get("unfilled", [])
        ],
    }


def run_benchmark(
    docx_paths: List[Path],
    backend_names: List[str],
    baseline_name: str = "fismatic",
    contract_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run all backends against all documents.

    Parameters
    ----------
    docx_paths:
        List of ``.docx`` files to benchmark.
    backend_names:
        List of backend names to run.
    baseline_name:
        Backend to use as the recall baseline (default: ``"fismatic"``).
    contract_path:
        Optional ODCS data contract YAML. When provided, contract-coverage
        metrics (objects_routed/total, properties_filled/total, fill_rate,
        unrouted_headings, control_tables_found, unfilled_properties) are
        computed for every document and included in the report.

    Returns
    -------
    dict
        Full benchmark report with per-document and aggregate metrics.
    """
    results: List[Dict[str, Any]] = []
    contract_aggregates: Dict[str, List[Dict[str, Any]]] = {}
    t_total = time.perf_counter()

    print(f"Benchmarking {len(backend_names)} backends × {len(docx_paths)} documents")
    print(f"Backends: {', '.join(backend_names)}")
    print()

    for docx_path in docx_paths:
        doc_results: Dict[str, Dict[str, Any]] = {}
        print(f"  [{docx_path.name}]")

        for backend_name in backend_names:
            print(f"    → {backend_name} ... ", end="", flush=True)
            r = run_single(backend_name, docx_path)
            doc_results[backend_name] = r
            if r["status"] == "ok":
                print(
                    f"{r['elapsed_s']:.3f}s | "
                    f"{r['heading_count']}h {r['table_count']}t "
                    f"{r['paragraph_count']}p {r['image_count']}i"
                )
            else:
                print(
                    f"{'SKIP' if r['status'] == 'unavailable' else 'ERROR'}: {r.get('error', '')}"
                )

        # Compute comparisons vs baseline
        comparisons: Dict[str, Dict[str, Any]] = {}
        if (
            baseline_name in doc_results
            and doc_results[baseline_name]["status"] == "ok"
        ):
            baseline = doc_results[baseline_name]
            for name, challenger in doc_results.items():
                if name != baseline_name:
                    comparisons[f"{baseline_name}_vs_{name}"] = compute_comparison(
                        baseline, challenger
                    )

        results.append(
            {
                "document": docx_path.name,
                "backends": doc_results,
                "comparisons": comparisons,
            }
        )

        # Contract coverage (per-document, backend-agnostic). Run once per
        # document even when multiple backends are benchmarked.
        if contract_path is not None:
            print("    → contract coverage ... ", end="", flush=True)
            try:
                cov = compute_contract_coverage(docx_path, contract_path)
                results[-1]["contract_coverage"] = cov
                contract_aggregates.setdefault("per_doc", []).append(cov)
                print(
                    f"{cov['elapsed_s']:.3f}s | "
                    f"{cov['objects_routed']}/{cov['objects_total']} obj, "
                    f"{cov['properties_filled']}/{cov['properties_total']} prop "
                    f"({cov['fill_rate']:.0%}), "
                    f"{cov['unrouted_heading_count']} unrouted, "
                    f"{cov['control_tables_found']} controls"
                )
            except Exception as exc:  # noqa: BLE001
                results[-1]["contract_coverage"] = {
                    "status": "error",
                    "error": str(exc),
                }
                print(f"ERROR: {exc}")

    total_elapsed = time.perf_counter() - t_total

    # Aggregate metrics per backend
    aggregates: Dict[str, Dict[str, Any]] = {}
    for backend_name in backend_names:
        ok_runs = [
            r["backends"][backend_name]
            for r in results
            if r["backends"].get(backend_name, {}).get("status") == "ok"
        ]
        if ok_runs:
            aggregates[backend_name] = {
                "documents_ok": len(ok_runs),
                "total_elapsed_s": round(sum(r["elapsed_s"] for r in ok_runs), 4),
                "mean_elapsed_s": round(
                    sum(r["elapsed_s"] for r in ok_runs) / len(ok_runs), 4
                ),
                "total_headings": sum(r["heading_count"] for r in ok_runs),
                "total_tables": sum(r["table_count"] for r in ok_runs),
                "total_paragraphs": sum(r["paragraph_count"] for r in ok_runs),
                "total_images": sum(r["image_count"] for r in ok_runs),
            }

    return {
        "benchmark_elapsed_s": round(total_elapsed, 3),
        "backends": backend_names,
        "baseline": baseline_name,
        "document_count": len(docx_paths),
        "contract_path": str(contract_path) if contract_path else None,
        "results": results,
        "aggregates": aggregates,
        "contract_aggregate": _summarize_contract_coverage(
            contract_aggregates.get("per_doc", [])
        ),
    }


def _summarize_contract_coverage(
    per_doc: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Compute aggregate contract-coverage statistics over all documents."""
    if not per_doc:
        return None
    fill_rates = [d["fill_rate"] for d in per_doc if "fill_rate" in d]
    fill_rates_sorted = sorted(fill_rates)
    n = len(fill_rates_sorted)
    median = (
        fill_rates_sorted[n // 2]
        if n % 2 == 1
        else round((fill_rates_sorted[n // 2 - 1] + fill_rates_sorted[n // 2]) / 2, 3)
    )
    return {
        "documents_with_coverage": len(per_doc),
        "mean_fill_rate": round(sum(fill_rates) / n, 3) if n else 0.0,
        "median_fill_rate": median,
        "min_fill_rate": min(fill_rates) if fill_rates else 0.0,
        "max_fill_rate": max(fill_rates) if fill_rates else 0.0,
        "total_control_tables": sum(d.get("control_tables_found", 0) for d in per_doc),
        "total_unrouted_headings": sum(
            d.get("unrouted_heading_count", 0) for d in per_doc
        ),
        "total_unfilled_properties": sum(
            len(d.get("unfilled_properties", [])) for d in per_doc
        ),
        "documents_full_coverage": sum(1 for r in fill_rates if r >= 0.999),
        "documents_zero_coverage": sum(1 for r in fill_rates if r == 0.0),
    }


# ---------------------------------------------------------------------------
# Markdown report generator
# ---------------------------------------------------------------------------


def render_markdown_report(report: Dict[str, Any]) -> str:
    """Render a human-readable Markdown benchmark report."""
    lines = [
        "# Extractor Benchmark Report",
        "",
        f"**Backends**: {', '.join(f'`{b}`' for b in report['backends'])}  ",
        f"**Baseline**: `{report['baseline']}`  ",
        f"**Documents**: {report['document_count']}  ",
        f"**Total benchmark time**: {report['benchmark_elapsed_s']:.1f}s  ",
        "",
        "---",
        "",
        "## Aggregate Metrics",
        "",
    ]

    # Aggregate table
    agg = report.get("aggregates", {})
    if agg:
        headers = [
            "Backend",
            "Docs OK",
            "Total time (s)",
            "Mean time (s)",
            "Headings",
            "Tables",
            "Paragraphs",
            "Images",
        ]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for backend_name, stats in agg.items():
            lines.append(
                f"| `{backend_name}` "
                f"| {stats['documents_ok']} "
                f"| {stats['total_elapsed_s']:.3f} "
                f"| {stats['mean_elapsed_s']:.3f} "
                f"| {stats['total_headings']} "
                f"| {stats['total_tables']} "
                f"| {stats['total_paragraphs']} "
                f"| {stats['total_images']} |"
            )
        lines.append("")

    # Per-document results
    lines += ["---", "", "## Per-Document Results", ""]

    for doc_result in report["results"]:
        lines.append(f"### {doc_result['document']}")
        lines.append("")

        backends = doc_result["backends"]
        be_headers = [
            "Backend",
            "Status",
            "Time (s)",
            "Headings",
            "Tables",
            "Paragraphs",
            "Images",
        ]
        lines.append("| " + " | ".join(be_headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(be_headers)) + " |")

        for name, r in backends.items():
            if r["status"] == "ok":
                lines.append(
                    f"| `{name}` | ✅ ok "
                    f"| {r['elapsed_s']:.3f} "
                    f"| {r['heading_count']} "
                    f"| {r['table_count']} "
                    f"| {r['paragraph_count']} "
                    f"| {r['image_count']} |"
                )
            else:
                lines.append(
                    f"| `{name}` | {'⚠️ skip' if r['status'] == 'unavailable' else '❌ error'} "
                    f"| — | — | — | — | — |"
                )
        lines.append("")

        # Comparison metrics
        comparisons = doc_result.get("comparisons", {})
        if comparisons:
            lines.append("**Comparison vs baseline:**")
            lines.append("")
            cmp_headers = [
                "Comparison",
                "Speedup",
                "Δ Time (s)",
                "Δ Headings",
                "Δ Tables",
                "Δ Paragraphs",
                "Heading recall",
                "Table recall",
            ]
            lines.append("| " + " | ".join(cmp_headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(cmp_headers)) + " |")
            for cmp_key, cmp in comparisons.items():
                if cmp:
                    speedup = f"{cmp['speedup']}×" if cmp.get("speedup") else "—"
                    h_recall = (
                        f"{cmp['heading_recall']:.2f}"
                        if cmp.get("heading_recall")
                        else "—"
                    )
                    t_recall = (
                        f"{cmp['table_recall']:.2f}" if cmp.get("table_recall") else "—"
                    )
                    lines.append(
                        f"| {cmp_key} "
                        f"| {speedup} "
                        f"| {cmp['elapsed_delta_s']:+.3f} "
                        f"| {cmp['heading_delta']:+d} "
                        f"| {cmp['table_delta']:+d} "
                        f"| {cmp['paragraph_delta']:+d} "
                        f"| {h_recall} "
                        f"| {t_recall} |"
                    )
            lines.append("")

    lines += [
        "---",
        "",
        "## Interpretation Guide",
        "",
        "| Metric | Meaning |",
        "|--------|---------|",
        "| **Speedup** | >1× = challenger is faster; <1× = baseline is faster |",
        "| **Heading recall** | 1.0 = challenger found same headings; <1.0 = missed headings |",
        "| **Table recall** | 1.0 = challenger found same tables; <1.0 = missed tables |",
        "| **Δ Headings** | Positive = challenger found more; negative = fewer |",
        "",
        "> _Generated by `compliance/scripts/benchmark_extractors.py`_",
        "",
    ]

    # ---------------------------------------------------------------
    # Contract Coverage  (only if --contract was supplied)
    # ---------------------------------------------------------------
    if report.get("contract_path"):
        lines += [
            "---",
            "",
            "## Contract Coverage",
            "",
            f"**Contract**: `{report['contract_path']}`  ",
            "",
        ]
        agg = report.get("contract_aggregate") or {}
        if agg:
            lines.append("### Corpus-wide routing summary")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|---|---|")
            lines.append(
                f"| Documents benchmarked | {agg['documents_with_coverage']} |"
            )
            lines.append(f"| Mean fill rate | {agg['mean_fill_rate']:.1%} |")
            lines.append(f"| Median fill rate | {agg['median_fill_rate']:.1%} |")
            lines.append(f"| Min fill rate | {agg['min_fill_rate']:.1%} |")
            lines.append(f"| Max fill rate | {agg['max_fill_rate']:.1%} |")
            lines.append(f"| Control tables found | {agg['total_control_tables']} |")
            lines.append(
                f"| Unrouted headings (sum) | {agg['total_unrouted_headings']} |"
            )
            lines.append(
                f"| Unfilled properties (sum) | {agg['total_unfilled_properties']} |"
            )
            lines.append(
                f"| Documents at 100% coverage | {agg['documents_full_coverage']} |"
            )
            lines.append(
                f"| Documents at 0% coverage | {agg['documents_zero_coverage']} |"
            )
            lines.append("")

        # Per-document coverage table
        lines += ["### Per-document coverage", ""]
        lines.append(
            "| Document | Objects | Properties | Fill rate | Controls | Unrouted |"
        )
        lines.append("|---|---|---|---|---|---|")
        for doc_result in report["results"]:
            cov = doc_result.get("contract_coverage")
            if not cov or cov.get("status") == "error":
                lines.append(f"| {doc_result['document']} | ❌ error | — | — | — | — |")
                continue
            lines.append(
                f"| {doc_result['document']} "
                f"| {cov['objects_routed']}/{cov['objects_total']} "
                f"| {cov['properties_filled']}/{cov['properties_total']} "
                f"| {cov['fill_rate']:.1%} "
                f"| {cov['control_tables_found']} "
                f"| {cov['unrouted_heading_count']} |"
            )
        lines.append("")

        # Unrouted headings detail  (only for the worst-coverage doc, capped)
        # to keep the report readable.
        per_doc_with_unrouted = [
            (r["document"], r["contract_coverage"])
            for r in report["results"]
            if r.get("contract_coverage", {}).get("unrouted_heading_count", 0) > 0
        ]
        if per_doc_with_unrouted:
            lines += [
                "### Unrouted headings (most-affected documents)",
                "",
            ]
            per_doc_with_unrouted.sort(
                key=lambda x: x[1]["unrouted_heading_count"], reverse=True
            )
            for doc_name, cov in per_doc_with_unrouted[:3]:
                lines.append(
                    f"**{doc_name}** — {cov['unrouted_heading_count']} unrouted "
                    f"(fill rate {cov['fill_rate']:.1%})"
                )
                lines.append("")
                lines.append("| Heading | Level | ¶ | ▦ | 🖼 |")
                lines.append("|---|---|---|---|---|")
                for h in cov.get("unrouted_headings", []):
                    lines.append(
                        f"| {h['title'][:80]} "
                        f"| H{h['level']} "
                        f"| {h['paragraph_count']} "
                        f"| {h['table_count']} "
                        f"| {h['image_count']} |"
                    )
                if cov.get("unrouted_headings_truncated"):
                    lines.append(
                        f"| _… {cov['unrouted_heading_count'] - 50} more_ | | | |"
                    )
                lines.append("")

    return "\n".join(lines)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Benchmark extractor backends against FedRAMP SSP .docx files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--docx",
        nargs="+",
        required=True,
        metavar="PATH",
        help="One or more .docx files to benchmark (globs expanded by shell)",
    )
    p.add_argument(
        "--backends",
        nargs="+",
        default=["fismatic"],
        choices=list_backends(),
        metavar="BACKEND",
        help=f"Backends to run (default: fismatic). Available: {list_backends()}",
    )
    p.add_argument(
        "--baseline",
        default="fismatic",
        help="Backend to use as recall baseline (default: fismatic)",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="FILE.json",
        help="Write JSON report to this path (default: print to stdout)",
    )
    p.add_argument(
        "--markdown",
        default=None,
        metavar="FILE.md",
        help="Write Markdown report to this path",
    )
    p.add_argument(
        "--contract",
        default=None,
        metavar="FILE.yaml",
        help="ODCS data contract YAML; enables per-document contract-coverage "
        "metrics (objects_routed/total, properties_filled/total, fill_rate, "
        "unrouted_headings, control_tables_found, unfilled_properties).",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    docx_paths = [Path(p) for p in args.docx]
    missing = [p for p in docx_paths if not p.exists()]
    if missing:
        print(
            f"ERROR: .docx files not found: {[str(m) for m in missing]}",
            file=sys.stderr,
        )
        return 1

    report = run_benchmark(
        docx_paths=docx_paths,
        backend_names=args.backends,
        baseline_name=args.baseline,
        contract_path=Path(args.contract) if args.contract else None,
    )

    json_str = json.dumps(report, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"\nJSON report → {args.output}")
    else:
        print("\n" + json_str)

    if args.markdown:
        md = render_markdown_report(report)
        Path(args.markdown).write_text(md, encoding="utf-8")
        print(f"Markdown report → {args.markdown}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
