"""Deterministic JSON and Markdown rendering for the GSA assessment document."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from compliance.lib.gsa_assessment import FINDINGS, RESULTS, control_sort_key
from compliance.lib.redaction import EMAIL_RE, URL_RE


def render_json(assessment: dict[str, Any]) -> str:
    return json.dumps(assessment, indent=2, ensure_ascii=False) + "\n"


def cell(text: Any, limit: int | None = None) -> str:
    s = " ".join(str(text if text is not None else "").split()).replace("|", "\\|")
    return s if limit is None or len(s) <= limit else s[: limit - 1] + "…"


def render_markdown(a: dict[str, Any], *, confirmations_path: str | None = None) -> str:
    name = a["system_name"]
    summary, att = a["summary"], a["attestation"]
    out: list[str] = [
        f"# {name} Assessment & Attestation (A&A)",
        "",
        f"> {a['disclaimer']}",
        "",
    ]
    confirm_ref = (
        f"`{confirmations_path}`"
        if confirmations_path
        else "the system's confirmations file"
    )
    out += [
        "## 1. Basis",
        "",
        f"- Tailoring workbook version: `{a['versions']['tailoring_workbook']}`; test-case workbook version: `{a['versions']['test_case_workbook']}` ({a['scope']['test_case_workbook_categorization']} categorization).",
        f"- System baseline: {a['scope']['system_baseline']}. Controls in scope: {a['scope']['controls']}; assessment objectives: {a['scope']['objectives']} across {a['scope']['controls_with_gsa_objectives']} controls.",
        "- Controls without GSA objectives are desk-reviewed against implementation records only (`desk-review-no-gsa-objectives`).",
        f"- Results are **proposed**; `Satisfied`/`Other Than Satisfied` count only when recorded in {confirm_ref}.",
        "",
        "Input digests (SHA-256):",
        "",
    ]
    out += [f"- `{k}` `{v[:16]}`" for k, v in a["inputs"].items()]
    out += [
        "",
        "## 2. Readiness summary",
        "",
        f"**Readiness:** `{att['readiness']}`",
        "",
    ]
    out += [f"- Blocker: {b}" for b in att["blockers"]] or ["- No blockers."]
    out += ["", "| Proposed result | Controls | Objectives |", "| --- | ---: | ---: |"]
    out += [
        f"| {r} | {summary['control_results'].get(r, 0)} | {summary['objective_results'].get(r, 0)} |"
        for r in RESULTS
    ]
    out += ["", "| Severity | Findings |", "| --- | ---: |"]
    out += [f"| {k} | {v} |" for k, v in summary["findings_by_severity"].items()]
    out += ["", "| ID | Severity | Meaning |", "| --- | --- | --- |"]
    out += [f"| {k} | {v[0]} | {v[1]} |" for k, v in FINDINGS.items()]

    out += ["", "## 3. Controls with GSA assessment objectives", ""]
    objs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in a["objectives"]:
        objs[o["control_id"]].append(o)
    ctl = {c["control_id"]: c for c in a["controls"]}
    order = list(ctl)
    for cid in sorted(
        objs, key=lambda c: (c.startswith("BOD"), c.split("-")[0], order.index(c))
    ):
        c = ctl[cid]
        out += [
            f"### {cid} — {cell(c['name'])}",
            "",
            f"- Result (proposed): `{c['proposed_result']}`; inheritance: {c['inheritance'] or 'n/a'}; status: {c['implementation_status'] or 'n/a'}; findings: {', '.join(c['findings']) or 'none'}",
            f"- Evidence refs: {', '.join(c['evidence_refs']) or 'none'}; authored parts: {', '.join(c['authored_parts']) or 'none'}",
        ]
        out += [f"- Residual: {cell(r, 220)}" for r in c["residual_actions"]]
        out += [
            "",
            "| Procedure | Objective | Methods pending | Part | Proposed | Basis |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for o in objs[cid]:
            out.append(
                f"| {cell(o['procedure_id'])} | {cell(o['objective'], 110)} | {', '.join(o['methods_pending_assessor']) or 'none'} | {cell(o['part'])} | {o['proposed_result']} | {cell(o['basis'], 70)} |"
            )
        out.append("")
    out += [
        f"## 4. Other {name} controls (no GSA objectives; desk review)",
        "",
        "| Control | Name | Inheritance | Status | Proposed | Findings |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    out += [
        f"| {c['control_id']} | {cell(c['name'], 60)} | {c['inheritance'] or ''} | {c['implementation_status'] or ''} | {c['proposed_result']} | {', '.join(c['findings'])} |"
        for c in a["controls"]
        if c["control_id"] not in objs
    ]
    out += ["", "## 5. Findings", "", "| ID | Severity | Item |", "| --- | --- | --- |"]
    out += [
        f"| {f['id']} | {f['severity']} | {f['control_id']} |" for f in a["findings"]
    ]
    out += ["", "## 6. POA&M-required controls (proposed Other Than Satisfied)", ""]
    out += [
        f"- {c}: "
        + (f"#{ctl[c]['poam_issue']}" if ctl[c]["poam_issue"] else "NO TRACKED ISSUE")
        for c in sorted(att["poam_required_controls"], key=control_sort_key)
    ] or ["- none"]
    out += [
        "",
        "## 7. Attestation (unsigned)",
        "",
        f"Computed readiness: `{att['readiness']}`. The agent does not pre-fill any decision or signature.",
        "",
        "| Field | Value |",
        "| --- | --- |",
        "| AO decision (ATO / ATO with conditions / DATO) | |",
        "| AO name | |",
        "| AO signature | |",
        "| AO date | |",
        "| Assessor name | |",
        "| Assessor signature | |",
        "| Assessor date | |",
        "",
    ]
    return "\n".join(out)


_CUI_BANNER_RE = re.compile(r"\bCUI//[A-Z][A-Z0-9_-]*\b")
_CHECKS = (
    (URL_RE, "URL"),
    (EMAIL_RE, "email address"),
    (_CUI_BANNER_RE, "CUI banner marking"),
)


def redaction_hits(*texts: str) -> list[str]:
    """Labels of any URL, e-mail address or CUI banner left in rendered output."""
    return sorted({label for t in texts for rx, label in _CHECKS if rx.search(t)})
