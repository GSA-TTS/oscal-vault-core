#!/usr/bin/env python3
"""Coverage Reconciliation Report — Feature R25.

Reads a generated Obsidian vault directory, extracts control frontmatter,
compares against the FedRAMP baseline for the system's FIPS-199 level, and
produces a coverage report (Markdown to stdout, optional JSON to file).

Usage::

    uv run python compliance/scripts/coverage_report.py \\
        --vault examples/vault \\
        --fips-199 moderate \\
        --output-json /tmp/coverage.json

Exit code is always 0 — the report is informational, not a gate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.vault import _oscal_control_key  # noqa: E402

_BASELINES_PATH = _REPO_ROOT / "compliance" / "data" / "fedramp_baselines.json"
_BASELINES_PROVENANCE_PATH = (
    _REPO_ROOT / "compliance" / "data" / "fedramp_baselines_provenance.json"
)


# ---------------------------------------------------------------------------
# Baseline loader
# ---------------------------------------------------------------------------


def load_baselines() -> Dict[str, List[str]]:
    """Load coverage baseline control lists from the data file.

    The lists are not interchangeable with NIST catalog profiles. Their exact
    semantics and source status are recorded in
    ``fedramp_baselines_provenance.json``. In particular, ``low`` is the
    158-control GSA Low assessment baseline used for SSP coverage reconciliation.
    """
    with open(_BASELINES_PATH) as fh:
        return json.load(fh)


def load_baseline_provenance() -> Dict[str, Any]:
    """Load source receipts and verification status for the coverage lists."""
    with open(_BASELINES_PROVENANCE_PATH) as fh:
        return json.load(fh)


def get_baseline(fips_level: str) -> Set[str]:
    """Return the set of control IDs for *fips_level* (low/moderate/high)."""
    baselines = load_baselines()
    level = fips_level.lower()
    if level not in baselines:
        level = "moderate"
    return set(baselines[level])


# ---------------------------------------------------------------------------
# Vault reader
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _read_frontmatter(md_path: Path) -> Optional[Dict[str, Any]]:
    """Return the YAML frontmatter dict from a markdown file, or None."""
    try:
        import yaml

        text = md_path.read_text(encoding="utf-8")
        m = _FM_RE.match(text)
        if not m:
            return None
        return yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001
        return None


def read_vault_controls(vault_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Walk ``Controls/*.md`` and return ``{catalog_key: frontmatter}``.

    Keys are normalized to the NIST catalog key form (``ac-2``, ``ia-2.12``)
    via :func:`compliance.lib.vault._oscal_control_key` -- the same form used
    by ``compliance/data/fedramp_baselines.json`` -- so callers can compare
    directly against a baseline set without re-normalizing. The vault's own
    display id (``AC-02``, ``IA-02(12)``) is preserved under the
    ``display_control_id`` key of the returned frontmatter dict.

    Prior to this normalization, ``compute_coverage`` compared vault display
    ids (``AC-02``) directly against baseline catalog keys (``ac-2``), which
    never matched -- every vault reported 0% baseline coverage regardless of
    actual content (see ``compliance/tests/test_coverage_report.py``).
    """
    controls_dir = vault_dir / "Controls"
    out: Dict[str, Dict[str, Any]] = {}
    if not controls_dir.is_dir():
        return out
    for md_file in sorted(controls_dir.glob("*.md")):
        fm = _read_frontmatter(md_file)
        if fm is None:
            continue
        display_id = str(fm.get("control_id") or md_file.stem).strip()
        if not display_id:
            continue
        cid = _oscal_control_key(display_id)
        fm = dict(fm)
        fm["display_control_id"] = display_id
        out[cid] = fm
    return out


def read_system_fips(vault_dir: Path, plan_id: Optional[str] = None) -> Optional[str]:
    """Detect the FIPS-199 level from a system note in the vault.

    Looks for the first ``Systems/*.md`` note (or the one matching *plan_id*)
    and returns its ``fips_199`` frontmatter field.
    """
    systems_dir = vault_dir / "Systems"
    if not systems_dir.is_dir():
        return None
    candidates = sorted(systems_dir.glob("*.md"))
    if not candidates:
        return None
    # If plan_id is provided, try to find a file that contains the slug.
    target = candidates[0]
    if plan_id:
        slug = re.sub(r"[^a-z0-9]+", "-", plan_id.lower()).strip("-")
        matches = [p for p in candidates if slug in p.stem]
        if matches:
            target = matches[0]
    fm = _read_frontmatter(target)
    if fm:
        return str(fm.get("fips_199") or fm.get("impact_level") or "").lower() or None
    return None


# ---------------------------------------------------------------------------
# Computation helpers
# ---------------------------------------------------------------------------


def compute_coverage(
    ssp_controls: Dict[str, Dict[str, Any]],
    baseline: Set[str],
    crm_controls: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Compute coverage metrics.

    Returns a dict with:
    - ``ssp_ids``: set of control IDs in the SSP
    - ``baseline_ids``: the baseline set
    - ``ssp_only``: controls in SSP but not in baseline
    - ``baseline_gaps``: controls in baseline but not in SSP
    - ``crm_only``: controls in CRM but not in SSP (empty when no CRM)
    - ``status_dist``: Counter of implementation_status values
    - ``origination_dist``: Counter of origination values
    """
    ssp_ids = set(ssp_controls.keys())
    ssp_only = ssp_ids - baseline
    baseline_gaps = baseline - ssp_ids
    crm_only: Set[str] = set()
    if crm_controls:
        crm_only = crm_controls - ssp_ids

    status_dist: Counter = Counter()
    origination_dist: Counter = Counter()
    for fm in ssp_controls.values():
        status = str(
            fm.get("implementation_status") or fm.get("normalized_status") or "unknown"
        )
        status_dist[status] += 1
        orig = fm.get("origination") or fm.get("normalized_origination") or "unknown"
        if isinstance(orig, list):
            for o in orig:
                origination_dist[str(o)] += 1
        else:
            origination_dist[str(orig)] += 1

    return {
        "ssp_ids": ssp_ids,
        "baseline_ids": baseline,
        "ssp_only": ssp_only,
        "baseline_gaps": baseline_gaps,
        "crm_only": crm_only,
        "status_dist": status_dist,
        "origination_dist": origination_dist,
    }


# ---------------------------------------------------------------------------
# Report renderer
# ---------------------------------------------------------------------------


def render_markdown(
    metrics: Dict[str, Any],
    *,
    system_name: str = "System",
    fips_level: str = "moderate",
    baseline_provenance: Optional[Dict[str, Any]] = None,
) -> str:
    """Render a Markdown coverage report from computed *metrics*."""
    ssp_ids = metrics["ssp_ids"]
    baseline_ids = metrics["baseline_ids"]
    ssp_only = metrics["ssp_only"]
    baseline_gaps = metrics["baseline_gaps"]
    crm_only = metrics["crm_only"]
    status_dist: Counter = metrics["status_dist"]
    origination_dist: Counter = metrics["origination_dist"]

    n_ssp = len(ssp_ids)
    n_baseline = len(baseline_ids)
    n_covered = n_baseline - len(baseline_gaps)
    pct = f"{100 * n_covered / n_baseline:.1f}" if n_baseline else "0.0"

    lines: List[str] = [
        f"# Coverage Report: {system_name} ({fips_level})",
        f"Generated: {date.today().isoformat()}",
        "",
        "## Summary",
        "",
        f"- SSP controls: {n_ssp}",
        f"- Baseline controls (FedRAMP {fips_level}): {n_baseline}",
        f"- Baseline coverage: {n_covered}/{n_baseline} ({pct}%)",
    ]
    if baseline_provenance:
        lines.append(
            f"- Baseline semantics: {baseline_provenance.get('semantics', 'unspecified')}"
        )
        if baseline_provenance.get("verification_status"):
            lines.append(
                "- Baseline source status: "
                f"{baseline_provenance['verification_status']}"
            )
        source = baseline_provenance.get("source") or {}
        if source.get("source_workbook_sha256"):
            lines.append(
                "- Baseline source workbook SHA-256: "
                f"`{source['source_workbook_sha256']}`"
            )
    if crm_only:
        lines.append(f"- CRM-only controls: {len(crm_only)}")
    lines += ["", "## Implementation Status Distribution", ""]
    lines += ["| Status | Count |", "|--------|-------|"]
    for status, count in sorted(status_dist.items()):
        lines.append(f"| {status} | {count} |")
    lines += ["", "## Origination Distribution", ""]
    lines += ["| Origination | Count |", "|-------------|-------|"]
    for orig, count in sorted(origination_dist.items()):
        lines.append(f"| {orig} | {count} |")
    lines += ["", "## Baseline Gaps (controls in baseline not implemented in SSP)", ""]
    if baseline_gaps:
        lines.append(
            f"{len(baseline_gaps)} controls in the FedRAMP {fips_level} baseline "
            f"are not present in this SSP:"
        )
        lines.append("")
        lines.append(", ".join(sorted(baseline_gaps)))
    else:
        lines.append("None — full baseline coverage.")
    if crm_only:
        lines += ["", "## CRM-Only Controls", ""]
        lines.append(f"{len(crm_only)} controls present in CRM but not in SSP:")
        lines.append("")
        lines.append(", ".join(sorted(crm_only)))
    lines += ["", "## SSP-Only Controls (beyond baseline)", ""]
    if ssp_only:
        lines.append(
            f"{len(ssp_only)} controls implemented beyond the FedRAMP {fips_level} baseline:"
        )
        lines.append("")
        lines.append(", ".join(sorted(ssp_only)))
    else:
        lines.append("No controls beyond the baseline.")
    return "\n".join(lines) + "\n"


def render_json(
    metrics: Dict[str, Any],
    *,
    system_name: str = "System",
    fips_level: str = "moderate",
    baseline_provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert *metrics* to a JSON-serializable dict."""
    ssp_ids = metrics["ssp_ids"]
    baseline_ids = metrics["baseline_ids"]
    baseline_gaps = metrics["baseline_gaps"]
    n_baseline = len(baseline_ids)
    n_covered = n_baseline - len(baseline_gaps)
    return {
        "system_name": system_name,
        "fips_level": fips_level,
        "generated": date.today().isoformat(),
        "baseline_provenance": baseline_provenance or {},
        "summary": {
            "ssp_controls": len(ssp_ids),
            "baseline_controls": n_baseline,
            "baseline_covered": n_covered,
            "baseline_coverage_pct": round(100 * n_covered / n_baseline, 1)
            if n_baseline
            else 0.0,
            "crm_only": len(metrics["crm_only"]),
        },
        "baseline_gaps": sorted(metrics["baseline_gaps"]),
        "ssp_only": sorted(metrics["ssp_only"]),
        "crm_only": sorted(metrics["crm_only"]),
        "status_distribution": dict(metrics["status_dist"]),
        "origination_distribution": dict(metrics["origination_dist"]),
    }


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a FedRAMP coverage reconciliation report from a vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--vault", required=True, help="Path to a generated vault directory"
    )
    parser.add_argument(
        "--crm-ods",
        help="Optional CRM .ods path; controls in CRM but not SSP are reported",
    )
    parser.add_argument(
        "--fips-199",
        choices=["low", "moderate", "high"],
        help="FIPS-199 level; auto-detected from vault system note when omitted",
    )
    parser.add_argument(
        "--output-json",
        help="Write JSON report to this path in addition to Markdown stdout",
    )
    parser.add_argument(
        "--plan-id",
        help="Used to locate the vault system note for FIPS level auto-detection",
    )
    args = parser.parse_args(argv)

    vault_dir = Path(args.vault).resolve()
    if not vault_dir.is_dir():
        print(f"ERROR: vault directory not found: {vault_dir}", file=sys.stderr)
        return 1

    # 1. Read SSP controls from vault.
    ssp_controls = read_vault_controls(vault_dir)

    # 2. Determine FIPS level.
    fips_level = args.fips_199
    if not fips_level:
        fips_level = read_system_fips(vault_dir, plan_id=args.plan_id)
    if not fips_level:
        fips_level = "moderate"
        print(
            "INFO: FIPS-199 level not specified and not found in vault; "
            "defaulting to 'moderate'.",
            file=sys.stderr,
        )

    # 3. Load FedRAMP baseline.
    baseline = get_baseline(fips_level)
    provenance = load_baseline_provenance().get(fips_level, {})

    # 4. CRM controls (optional).
    crm_controls: Optional[Set[str]] = None
    if args.crm_ods:
        try:
            from compliance.lib.crm_ods import extract_crm

            crm_result = extract_crm(args.crm_ods)
            # crm_ods keys are in canonical display form (AC-02); normalize to
            # the same catalog-key form (ac-2) used by read_vault_controls()
            # and the baseline set so set comparisons are apples-to-apples.
            crm_controls = {
                _oscal_control_key(cid) for cid in crm_result.by_control.keys()
            }
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: Failed to read CRM ODS: {exc}", file=sys.stderr)

    # 5. Detect system name from vault.
    system_name = "System"
    systems_dir = vault_dir / "Systems"
    if systems_dir.is_dir():
        syss = sorted(systems_dir.glob("*.md"))
        if syss:
            fm = _read_frontmatter(syss[0])
            if fm:
                system_name = str(fm.get("name") or syss[0].stem)

    # 6. Compute coverage.
    metrics = compute_coverage(ssp_controls, baseline, crm_controls=crm_controls)

    # 7. Output Markdown to stdout.
    md = render_markdown(
        metrics,
        system_name=system_name,
        fips_level=fips_level,
        baseline_provenance=provenance,
    )
    print(md, end="")

    # 8. Optional JSON output.
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump(
                render_json(
                    metrics,
                    system_name=system_name,
                    fips_level=fips_level,
                    baseline_provenance=provenance,
                ),
                fh,
                indent=2,
            )
        print(f"JSON report written to {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
