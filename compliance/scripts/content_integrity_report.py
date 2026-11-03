#!/usr/bin/env python3
"""Content Coverage & Integrity Report — Phase R29.

Complements ``coverage_report.py`` (baseline reconciliation: which control
*IDs* are present) with a per-control **content-completeness** audit: which
control notes still carry a placeholder marker instead of real recovered
content, for every section R17-R19 introduced or restructured (control-table
structural fill, `# Upstream` catalog-statement population, ODP fallback
labeling, per-control + Chapters 1-12 authoring guidance).

Motivation
----------
Regenerating the four official blank templates after R17-R19 landed shows a
clean bill of health (no raw ``{{ insert: }}`` placeholders, no bare-id
``[Assignment: ac-1_prm_1]`` markers, no ``Original upstream control
language...`` stub). But every one of the 21 *other* committed example
vaults under ``examples/vault/`` — the ones built from real customer SSPs
before R18/R19 shipped — was never regenerated, so they still carry the
pre-R18 stub in ``# Upstream`` and never got template-guidance callouts. This
script makes that drift visible and countable instead of only discoverable
by manual `grep`.

It also quantifies two *structural* gaps in every vault regardless of
regeneration status: (1) most Chapters 1-12 front-matter authoring-guidance
sections have no rendered narrative-note counterpart to attach a callout to
(only ~4 of ~24 mapped narrative sections have a matching front-matter
heading in the Moderate template — see ADR-023), so R19's extractor recovers
guidance that the renderer has nowhere to place; (2) every ``implemented``
control's ``# Evidence`` section is a placeholder — fismatic's SSP model
carries no `links[]`/evidence-reference field to populate it from (see
``docs/CROSSWALK_COVERAGE.md`` §5).

Checks performed per ``Controls/*.md`` note:
  - ``stale_upstream``:      pre-R18 static placeholder still present
  - ``no_catalog_match``:    control id has no vendored NIST rev5 statement
                              (expected for custom/non-catalog controls;
                              reported for visibility, not as a defect)
  - ``raw_odp_placeholder``: an un-substituted ``{{ insert: param, ... }}``
                              token leaked into rendered prose (R18/R19 bug)
  - ``bare_param_fallback``: an ODP placeholder fell back to the bare
                              parameter id instead of a descriptive label
                              (missing entry in
                              ``nist_sp800-53_rev5_param_labels.json``)
  - ``no_part_narrative``:   the control has no statement-part breakdown at
                              all (fismatic could not parse per-part text)
  - ``empty_part_text``:     at least one statement part has no narrative
  - ``no_evidence``:         `# Evidence` section is the empty placeholder
  - ``no_related_controls``: `## Related Controls` section is the empty
                              placeholder
  - ``no_parameters``:       control carries zero recovered ODP values

Also reports, once per vault (not per control):
  - ``section_guidance_unreachable``: count of Chapters 1-12 front-matter
    instruction blocks recovered by R19's ``extract_section_guidance`` that
    have no matching rendered narrative-note heading to attach to (requires
    ``--docx`` since this needs the source document; skipped otherwise).

Exit code is always 0 — informational, like ``coverage_report.py``.

Usage::

    uv run python compliance/scripts/content_integrity_report.py \\
        --vault examples/vault/<system>

    # Every vault under examples/vault/, plus a docx-driven section-guidance
    # reachability check for one of them:
    uv run python compliance/scripts/content_integrity_report.py \\
        --docx templates/Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx

    uv run python compliance/scripts/content_integrity_report.py \\
        --output-json /tmp/integrity.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.workspace import resolve_workspace  # noqa: E402
from compliance.lib.vault import _NARRATIVE_NOTE_SPECS  # noqa: E402

# ---------------------------------------------------------------------------
# Markers -- exact strings/patterns emitted by compliance/lib/vault.py.
# Keep these in sync with build_control_note()/build_narrative_notes(); a
# rename there without a matching update here will silently zero out a check.
# ---------------------------------------------------------------------------

_STALE_UPSTREAM_RE = re.compile(
    r"Original upstream control language for `([^`]+)` from the catalog "
    r"or imported baseline\."
)
_NO_CATALOG_MATCH_RE = re.compile(
    r"No NIST SP 800-53 rev5 catalog statement is vendored for `([^`]+)`"
)
_RAW_ODP_RE = re.compile(r"\{\{\s*insert:\s*param,")
_BARE_PARAM_FALLBACK_RE = re.compile(
    r"\[Assignment: [a-z]{2}-\d+[a-z0-9._]*(?:_odp|_prm)[a-z0-9._]*\]"
)
_NO_NARRATIVE_PART = "_(No narrative recorded in source SSP.)_"
_NO_PART_BREAKDOWN = "_(No part-level narrative was captured by the parser.)_"
_NO_EVIDENCE = "_(No evidence references recorded in source SSP.)_"
_NO_RELATED = "_(Not recorded in source SSP.)_"
_EMPTY_PARAMS_ROW = "| _(none recorded)_ |"
_GUIDANCE_CALLOUT = "[!note]- Template guidance"

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ---------------------------------------------------------------------------
# Per-control result
# ---------------------------------------------------------------------------


@dataclass
class ControlIntegrity:
    control_id: str
    relpath: str
    implementation_status: str = "unknown"
    stale_upstream: bool = False
    no_catalog_match: bool = False
    raw_odp_placeholder: bool = False
    bare_param_fallback: bool = False
    no_part_narrative: bool = False
    empty_part_text: bool = False
    no_evidence: bool = False
    no_related_controls: bool = False
    no_parameters: bool = False
    guidance_present: bool = False

    def defect_flags(self) -> List[str]:
        """Flags that represent a *regression/gap*, excluding informational
        ones (``no_catalog_match`` is expected for non-catalog controls)."""
        flags = []
        if self.stale_upstream:
            flags.append("stale_upstream")
        if self.raw_odp_placeholder:
            flags.append("raw_odp_placeholder")
        if self.bare_param_fallback:
            flags.append("bare_param_fallback")
        if self.no_part_narrative:
            flags.append("no_part_narrative")
        if self.empty_part_text:
            flags.append("empty_part_text")
        if self.no_evidence:
            flags.append("no_evidence")
        if self.no_related_controls:
            flags.append("no_related_controls")
        if self.no_parameters:
            flags.append("no_parameters")
        return flags


def _read_note(md_path: Path) -> Optional[Dict[str, Any]]:
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FM_RE.match(text)
    if not m:
        return None
    try:
        import yaml

        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001
        fm = {}
    body = text[m.end() :]
    return {"frontmatter": fm, "body": body}


def analyze_control_note(md_path: Path) -> ControlIntegrity:
    """Run every marker check against one ``Controls/<id>.md`` note."""
    note = _read_note(md_path)
    fm = (note or {}).get("frontmatter") or {}
    body = (note or {}).get("body") or ""
    control_id = str(fm.get("control_id") or md_path.stem)

    result = ControlIntegrity(
        control_id=control_id,
        relpath=str(md_path),
        implementation_status=str(fm.get("implementation_status") or "unknown"),
    )
    result.stale_upstream = bool(_STALE_UPSTREAM_RE.search(body))
    result.no_catalog_match = bool(_NO_CATALOG_MATCH_RE.search(body))
    result.raw_odp_placeholder = bool(_RAW_ODP_RE.search(body))
    result.bare_param_fallback = bool(_BARE_PARAM_FALLBACK_RE.search(body))
    result.no_part_narrative = _NO_PART_BREAKDOWN in body
    result.empty_part_text = _NO_NARRATIVE_PART in body
    # After ADR-025, empty Evidence / Related Controls sections are omitted
    # entirely rather than filled with placeholder prose. "No evidence" is
    # therefore the absence of a `# Evidence` heading (or the legacy
    # placeholder string, for vaults generated before ADR-025).
    result.no_evidence = _NO_EVIDENCE in body or "# Evidence" not in body
    result.no_related_controls = (
        _NO_RELATED in body or "## Related Controls" not in body
    )
    result.no_parameters = not fm.get("parameters") or _EMPTY_PARAMS_ROW in body
    result.guidance_present = _GUIDANCE_CALLOUT in body
    return result


def analyze_vault_controls(vault_dir: Path) -> List[ControlIntegrity]:
    """Run :func:`analyze_control_note` over every note in ``Controls/``."""
    controls_dir = vault_dir / "Controls"
    if not controls_dir.is_dir():
        return []
    return [analyze_control_note(p) for p in sorted(controls_dir.glob("*.md"))]


# ---------------------------------------------------------------------------
# Section-guidance reachability (R19 / ADR-023) -- requires the source .docx
# ---------------------------------------------------------------------------


def section_guidance_reachability(docx_path: Path) -> Dict[str, Any]:
    """Measure how much of the recovered Chapters 1-12 front-matter guidance
    (R19's ``extract_section_guidance``) has a rendered narrative-note
    heading to attach a callout to (see ``_NARRATIVE_NOTE_SPECS`` in
    ``compliance/lib/vault.py`` and ADR-023's own measurement: ~4/24 headings
    matched in the Moderate template at time of writing).

    Returns a dict with ``total_headings``, ``total_instructions``,
    ``reachable_headings``, ``reachable_instructions``, and
    ``unreachable_headings`` (a list of ``{heading, instruction_count}``).
    """
    from compliance.lib.control_guidance import (
        _normalize_heading_key,
        extract_section_guidance,
    )

    section_guidance = extract_section_guidance(docx_path)
    rendered_keys = set()
    for spec in _NARRATIVE_NOTE_SPECS:
        for _prop, heading in spec["sections"]:
            rendered_keys.add(_normalize_heading_key(heading))

    total_instructions = 0
    reachable_instructions = 0
    unreachable: List[Dict[str, Any]] = []
    for heading, guidance in section_guidance.items():
        n = len(guidance.instruction_blocks)
        total_instructions += n
        if _normalize_heading_key(heading) in rendered_keys:
            reachable_instructions += n
        else:
            unreachable.append({"heading": heading, "instruction_count": n})

    return {
        "total_headings": len(section_guidance),
        "total_instructions": total_instructions,
        "reachable_headings": len(section_guidance) - len(unreachable),
        "reachable_instructions": reachable_instructions,
        "unreachable_headings": sorted(
            unreachable, key=lambda d: -d["instruction_count"]
        ),
    }


# ---------------------------------------------------------------------------
# Aggregation + rendering
# ---------------------------------------------------------------------------


@dataclass
class VaultIntegritySummary:
    vault_name: str
    total_controls: int = 0
    defect_counts: Counter = field(default_factory=Counter)
    implemented_no_evidence: int = 0
    implemented_total: int = 0


def summarize(
    vault_name: str, results: List[ControlIntegrity]
) -> VaultIntegritySummary:
    summary = VaultIntegritySummary(vault_name=vault_name, total_controls=len(results))
    for r in results:
        for flag in r.defect_flags():
            summary.defect_counts[flag] += 1
        if r.implementation_status == "implemented":
            summary.implemented_total += 1
            if r.no_evidence:
                summary.implemented_no_evidence += 1
    return summary


def render_markdown(summaries: List[VaultIntegritySummary]) -> str:
    lines: List[str] = [
        "# Content Coverage & Integrity Report",
        f"Generated: {date.today().isoformat()}",
        "",
        "## Per-Vault Summary",
        "",
        "| Vault | Controls | Stale Upstream | Raw ODP | Bare Param | No Part | "
        "Empty Part | No Evidence | No Related | No Params |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        dc = s.defect_counts
        lines.append(
            f"| {s.vault_name} | {s.total_controls} | "
            f"{dc.get('stale_upstream', 0)} | "
            f"{dc.get('raw_odp_placeholder', 0)} | "
            f"{dc.get('bare_param_fallback', 0)} | "
            f"{dc.get('no_part_narrative', 0)} | "
            f"{dc.get('empty_part_text', 0)} | "
            f"{dc.get('no_evidence', 0)} | "
            f"{dc.get('no_related_controls', 0)} | "
            f"{dc.get('no_parameters', 0)} |"
        )
    lines += ["", "## Implemented Controls Missing Evidence", ""]
    lines += ["| Vault | Implemented | Missing Evidence | % |", "|---|---|---|---|"]
    for s in summaries:
        pct = (
            f"{100 * s.implemented_no_evidence / s.implemented_total:.0f}%"
            if s.implemented_total
            else "n/a"
        )
        lines.append(
            f"| {s.vault_name} | {s.implemented_total} | "
            f"{s.implemented_no_evidence} | {pct} |"
        )
    total_controls = sum(s.total_controls for s in summaries)
    total_stale = sum(s.defect_counts.get("stale_upstream", 0) for s in summaries)
    lines += [
        "",
        "## Aggregate",
        "",
        f"- Total control notes scanned: {total_controls}",
        f"- Notes with the pre-R18 stale `# Upstream` placeholder: {total_stale} "
        f"({100 * total_stale / total_controls:.1f}%)"
        if total_controls
        else "",
        "",
        "Notes:",
        "  - `stale_upstream`/`raw_odp_placeholder`/`bare_param_fallback` are",
        "    regressions relative to R18/R19 (#18, #19) — a vault carrying them",
        "    was built before those phases landed and has not been regenerated.",
        "  - `no_evidence`/`no_related_controls`/`no_parameters` reflect known,",
        "    documented pipeline gaps (see `docs/CROSSWALK_COVERAGE.md` §5),",
        "    not per-vault regressions; they are reported for completeness",
        "    tracking, not as defects to immediately fix.",
    ]
    return "\n".join(lines) + "\n"


def render_json(
    summaries: List[VaultIntegritySummary],
    section_guidance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "generated": date.today().isoformat(),
        "vaults": [
            {
                "vault_name": s.vault_name,
                "total_controls": s.total_controls,
                "defect_counts": dict(s.defect_counts),
                "implemented_total": s.implemented_total,
                "implemented_no_evidence": s.implemented_no_evidence,
            }
            for s in summaries
        ],
    }
    if section_guidance is not None:
        out["section_guidance_reachability"] = section_guidance
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_vaults(arg_vaults: Optional[List[str]]) -> List[Path]:
    if arg_vaults:
        return [Path(v).resolve() for v in arg_vaults]
    examples = resolve_workspace() / "examples" / "vault"
    if examples.is_dir():
        return [
            d
            for d in sorted(examples.iterdir())
            if d.is_dir() and not d.name.startswith(".")
        ]
    return []


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Content coverage & integrity report: per-control placeholder/"
            "regression audit across one or more vaults."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--vault",
        action="append",
        default=None,
        help="Vault directory (repeatable; default: every vault in examples/vault/)",
    )
    parser.add_argument(
        "--docx",
        help="Optional source .docx to also measure section-guidance "
        "reachability (R19/ADR-023)",
    )
    parser.add_argument(
        "--output-json",
        help="Write JSON report to this path in addition to Markdown stdout",
    )
    args = parser.parse_args(argv)

    vaults = _resolve_vaults(args.vault)
    if not vaults:
        print("No vault directories to analyze. Use --vault.", file=sys.stderr)
        return 0

    summaries: List[VaultIntegritySummary] = []
    for vd in vaults:
        results = analyze_vault_controls(vd)
        if not results:
            continue
        summaries.append(summarize(vd.name, results))

    section_guidance: Optional[Dict[str, Any]] = None
    if args.docx:
        docx_path = Path(args.docx)
        if docx_path.exists():
            section_guidance = section_guidance_reachability(docx_path)
        else:
            print(f"WARNING: --docx path not found: {docx_path}", file=sys.stderr)

    md = render_markdown(summaries)
    print(md, end="")

    if section_guidance is not None:
        print("## Section Guidance Reachability (front-matter, R19/ADR-023)\n")
        print(
            f"- Front-matter guidance headings recovered: "
            f"{section_guidance['total_headings']}"
        )
        print(
            f"- Front-matter guidance instructions recovered: "
            f"{section_guidance['total_instructions']}"
        )
        print(
            f"- Headings reachable via a rendered narrative-note heading: "
            f"{section_guidance['reachable_headings']}"
        )
        print(f"- Instructions reachable: {section_guidance['reachable_instructions']}")
        unreachable = section_guidance["unreachable_headings"]
        if unreachable:
            print(f"\n{len(unreachable)} unreachable headings (top 10 by count):")
            for item in unreachable[:10]:
                print(f"  {item['instruction_count']:3d}  {item['heading']}")

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as fh:
            json.dump(render_json(summaries, section_guidance), fh, indent=2)
        print(f"\nJSON report written to {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
