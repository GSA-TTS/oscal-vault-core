#!/usr/bin/env python3
"""Run every registry SSP through Low, Moderate, and High review templates.

This is a local-only corpus smoke test for the manual review-package builder.
It finds parser/table-anchor/fill errors across real SSP shapes without
claiming that a source system meets a target template's impact level. Coverage
gaps are recorded in per-pair manifests and the aggregate report; operational
errors cause a non-zero exit.

All generated DOCX/JSON artifacts must live in an ignored output directory.

Example:
    uv run python compliance/scripts/smoke_test_template_review_packages.py \\
      --out-dir review-output/template-smoke
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILDER = _REPO_ROOT / "compliance" / "scripts" / "build_template_review_package.py"
_WORKSPACE = Path.cwd()
_REGISTRY = _WORKSPACE / "scripts" / "registry.yaml"

_TEMPLATES = {
    "low": _WORKSPACE / "fismatic" / "Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
    "moderate": _WORKSPACE
    / "fismatic"
    / "Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
    "high": _WORKSPACE
    / "fismatic"
    / "High-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
}

_SPEC = importlib.util.spec_from_file_location("template_review_package", _BUILDER)
template_review_package = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(template_review_package)


def _registry_sources() -> tuple[list[tuple[str, Path]], list[str]]:
    data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8")) or {}
    sources: list[tuple[str, Path]] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for system in data.get("systems", []):
        slug = system.get("plan_id") or system.get("system_name")
        docx = system.get("docx")
        if slug and docx:
            if slug in seen:
                duplicates.append(str(slug))
                continue
            seen.add(str(slug))
            sources.append((str(slug), _WORKSPACE / docx))
    return sources, duplicates


def _write_summary(report: dict[str, object], path: Path) -> None:
    runs = report["runs"]
    assert isinstance(runs, list)
    lines = [
        "# Template Review Package Smoke Test",
        "",
        "Local-only review artifacts are ignored by git. Coverage gaps are "
        "expected when a source SSP and target baseline differ; only operational "
        "failures make the smoke command fail.",
        "",
        f"- Runs: **{len(runs)}**",
        f"- Failures: **{len(report['failures'])}**",
    ]
    duplicates = report.get("duplicate_registry_plan_ids", [])
    if duplicates:
        lines.append(
            f"- Duplicate registry plan IDs skipped: `{', '.join(duplicates)}`"
        )
    lines.extend(
        [
            "",
            "| Source | Target | Filled (controls/tables) | Source-only | Target-only | Duplicate targets | Status |",
            "| --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for run in runs:
        assert isinstance(run, dict)
        lines.append(
            "| {source} | {target} | {filled}/{physical} | {source_only} | {target_only} | {duplicates} | {status} |".format(
                source=run["source"],
                target=run.get("target", "-"),
                filled=run.get("matching_control_tables_filled", "-"),
                physical=run.get("matching_physical_tables_filled", "-"),
                source_only=run.get("source_controls_without_target_table", "-"),
                target_only=run.get("target_controls_left_unfilled", "-"),
                duplicates=", ".join(run.get("duplicate_target_control_ids", []))
                or "-",
                status=run["status"],
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help="Limit to one registry plan_id (repeatable). Default: every registry source.",
    )
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    selected = set(args.source or [])
    sources, duplicates = _registry_sources()
    if duplicates:
        parser.error(
            "Duplicate registry plan IDs: " + ", ".join(sorted(set(duplicates)))
        )
    report: dict[str, object] = {
        "runs": [],
        "failures": [],
        "duplicate_registry_plan_ids": duplicates,
    }
    for slug, source in sources:
        if selected and slug not in selected:
            continue
        if not source.is_file():
            report["runs"].append(
                {
                    "source": slug,
                    "status": "skipped",
                    "reason": "source docx unavailable",
                }
            )
            continue
        for target_name, template in _TEMPLATES.items():
            run_dir = args.out_dir / slug / target_name
            output = run_dir / "review.docx"
            manifest_path = run_dir / "review.manifest.json"
            try:
                manifest = template_review_package.build_review_package(
                    source, template, output, manifest_path
                )
                report["runs"].append(
                    {
                        "source": slug,
                        "target": target_name,
                        "status": "ok",
                        "matching_control_tables_filled": manifest[
                            "matching_control_tables_filled"
                        ],
                        "matching_physical_tables_filled": manifest[
                            "matching_physical_tables_filled"
                        ],
                        "duplicate_target_control_ids": manifest[
                            "duplicate_target_control_ids"
                        ],
                        "source_controls_without_target_table": len(
                            manifest["source_controls_without_target_table"]
                        ),
                        "target_controls_left_unfilled": len(
                            manifest["target_controls_left_unfilled"]
                        ),
                    }
                )
            except Exception as exc:  # Surface every failure in one smoke run.
                failure = {"source": slug, "target": target_name, "error": str(exc)}
                report["runs"].append({**failure, "status": "failed"})
                report["failures"].append(failure)

    report_path = args.out_dir / "template-review-smoke-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_summary(report, args.out_dir / "template-review-smoke-summary.md")
    counts = {
        status: sum(1 for run in report["runs"] if run["status"] == status)
        for status in ("ok", "skipped", "failed")
    }
    print(json.dumps(counts, indent=2))
    print(f"REPORT: {report_path}")
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
