"""System-neutral GSA assessment and attestation engine.

Crosswalks one system's implementation record against the GSA Control Tailoring Workbook and
the GSA NIST SP 800-53A test-case workbook (both as canonical JSON from
``compliance.lib.gsa_workbooks``).

The engine never reads a system's own files. A small adapter in the system repository emits one
document that conforms to ``system_assessment_input_schema_v1.json``; this module consumes only
that document and the two canonical workbook files.

Results are *proposed* desk-review results. Only ``confirmations`` in the input count as
assessor-confirmed, and the attestation block is an unsigned template: the engine never fills a
decision, name, signature or date.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from compliance.lib.control_identity import canonical_control_id
from compliance.lib.gsa_workbooks import (
    validate_tailoring,
    validate_test_cases,
)

ROOT = Path(__file__).resolve().parents[2]
INPUT_SCHEMA = (
    ROOT / "compliance/schemas/assessment/system_assessment_input_schema_v1.json"
)

SATISFIED = "SATISFIED_PENDING_CONFIRMATION"
CONDITIONAL = "CONDITIONALLY_SATISFIED"
OTS = "OTHER_THAN_SATISFIED"
INHERITED = "INHERITED_PENDING_PROVIDER_EVIDENCE"
NOT_ASSESSED = "NOT_ASSESSED_NO_EVIDENCE"
NA = "NOT_APPLICABLE"
RESULTS = [SATISFIED, CONDITIONAL, OTS, INHERITED, NOT_ASSESSED, NA]
CONFIRMED_RESULTS = {"Satisfied", "Other Than Satisfied", "Not Applicable"}
AUTOMATED_EVIDENCE_KINDS = {"ci-test", "ci-workflow"}

FINDINGS: dict[str, tuple[str, str]] = {
    "AA-1": (
        "High",
        "No tenant implementation record for a non-inherited, in-baseline control.",
    ),
    "AA-2": ("High", "Open GAP residual action or not-implemented status."),
    "AA-3": ("Medium", "Implementation is DRAFT pending ISSO review."),
    "AA-4": ("Medium", "Open residual action remains against a documented mechanism."),
    "AA-5": (
        "Medium",
        "Implementation status conflicts with residual actions or model status.",
    ),
    "AA-6": (
        "Medium",
        "Assessment objective part has no authored implementation prose.",
    ),
    "AA-7": (
        "Low",
        "Control is outside the GSA tailoring workbook baseline or catalog.",
    ),
    "AA-8": (
        "Low",
        "Control is not in the system's GSA baseline; assessed because the system "
        "selected it (gaps are advisory).",
    ),
    "AA-9": (
        "Medium",
        "All linked evidence is gap-observation evidence; no implementation evidence "
        "supports the claim.",
    ),
}
SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2}
MARKER_RX = re.compile(
    r"Residual action|\bGAP\b|DRAFT|not yet|plan-of-action|POA&M", re.I
)
PROC_RX = re.compile(r"^[A-Z]{2}-\d+(?:\(\d+\))?(?:\(([a-z])\)|([a-z])(?=[.\d\[]|$))?")
BOD_PREFIX = "BOD"
DISCLAIMER = (
    "Informational desk-review assessment. Proposed results are not assessment "
    "conclusions until confirmed by an assessor; not official GSA policy."
)


class AssessmentError(ValueError):
    """The input is malformed, or a confirmation is invalid."""


def excerpt(text: str, limit: int = 240) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def procedure_part(procedure_id: str) -> str | None:
    """Statement-part letter in a procedure ID: ``AC-02f.[03]`` -> ``f``."""
    match = PROC_RX.match(procedure_id)
    return (match.group(1) or match.group(2)) if match else None


def odps(statement: str) -> list[str]:
    return re.findall(r"\[([^\[\]]+)\]", statement or "")


def control_sort_key(cid: str) -> tuple[str, int, str]:
    match = re.match(r"([A-Z]{2})-(\d+)", cid)
    return (match.group(1), int(match.group(2)), cid) if match else ("ZZ", 0, cid)


def input_errors(data: dict[str, Any]) -> list[str]:
    schema = json.loads(INPUT_SCHEMA.read_text(encoding="utf-8"))
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(
            Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path)
        )
    ]
    if errors:
        return errors
    seen: set[str] = set()
    for control in data["controls"]:
        cid = control["control_id"]
        if canonical_control_id(cid) != cid:
            errors.append(f"{cid}: control_id is not canonical")
        if cid in seen:
            errors.append(f"{cid}: duplicate control in system input")
        seen.add(cid)
    return errors


def validate_input(data: dict[str, Any]) -> None:
    errors = input_errors(data)
    if errors:
        raise AssessmentError(
            "invalid system assessment input:\n  " + "\n  ".join(errors)
        )


def _prose_by_part(control: dict[str, Any]) -> dict[str | None, str]:
    return {p["part"]: p["text"] for p in control.get("prose", [])}


def _evidence_index(system: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for item in system.get("evidence", []):
        for cid in item["control_ids"]:
            index.setdefault(cid, []).append(item)
    return index


def assess_control(
    cid: str,
    system: dict[str, Any],
    ctw: dict[str, Any] | None,
    control: dict[str, Any] | None,
    objective_controls: set[str],
    evidence: list[dict[str, Any]],
    issue_links: dict[str, int],
) -> dict[str, Any]:
    baseline_key = system["system"]["baseline"].lower()
    prose = _prose_by_part(control) if control else {}
    inheritance = (control or {}).get("inheritance") or ""
    status = (control or {}).get("implementation_status")
    residuals = list((control or {}).get("residual_actions") or [])
    gaps = [r for r in residuals if r["gap"]]
    draft = bool((control or {}).get("draft")) or any(
        "DRAFT" in t for t in prose.values()
    )
    in_baseline = bool(ctw and ctw["baselines"].get(baseline_key))
    tenant_record = bool((control or {}).get("has_countermeasure") or prose)
    mechanisms = (control or {}).get("evidence_refs") or []
    findings: list[str] = []

    if status == "not-applicable":
        result = NA
    elif not tenant_record:
        if inheritance == "Fully Inherited":
            result = INHERITED
        elif ctw is not None and not in_baseline:
            result, findings = NA, ["AA-7"]
        else:
            result = NOT_ASSESSED
            if inheritance in ("Hybrid", "System-Specific") and in_baseline:
                findings.append("AA-1")
    else:
        if gaps or status in ("not-implemented", "gap"):
            findings.append("AA-2")
        if draft:
            findings.append("AA-3")
        if residuals and not gaps:
            findings.append("AA-4")
        if status == "implemented" and residuals:
            findings.append("AA-5")
        if (
            status == "not-implemented"
            and (control or {}).get("model_status") == "verified"
        ):
            findings.append("AA-5")
        if gaps or status in ("not-implemented", "gap", "partially-implemented"):
            result = OTS
        elif (
            draft
            or residuals
            or not (mechanisms or (control or {}).get("inline_mechanisms"))
        ):
            result = CONDITIONAL
        else:
            result = SATISFIED
    if ctw is None and control is not None and "AA-7" not in findings:
        findings.append("AA-7")
    if ctw is not None and not in_baseline and tenant_record and result != NA:
        findings.append("AA-8")
    if (
        result in (SATISFIED, CONDITIONAL)
        and evidence
        and all(e["role"] == "observation" for e in evidence)
    ):
        findings.append("AA-9")

    return {
        "control_id": cid,
        "name": (ctw or {}).get("name") or (control or {}).get("name") or "",
        "family": cid.split("-")[0],
        "inheritance": inheritance or None,
        "providing_service": (control or {}).get("providing_service"),
        "implementation_status": status,
        "model_status": (control or {}).get("model_status"),
        "gsa_baselines": sorted(
            k for k, v in (ctw or {}).get("baselines", {}).items() if v
        ),
        "gsa_overlays": sorted(
            k for k, v in (ctw or {}).get("overlays", {}).items() if v
        ),
        "gsa_odps": odps((ctw or {}).get("statement", "")),
        "in_system_baseline": in_baseline,
        "has_countermeasure": bool((control or {}).get("has_countermeasure")),
        "authored_parts": sorted(p for p in prose if p is not None)
        + (["(whole)"] if None in prose else []),
        "countermeasure_summary": excerpt((control or {}).get("summary", "")),
        "strategy": (control or {}).get("strategy"),
        "evidence_refs": mechanisms,
        "evidence": [
            {
                k: e.get(k)
                for k in (
                    "key",
                    "title",
                    "repo",
                    "path",
                    "kind",
                    "role",
                    "claim_sha256",
                )
            }
            for e in evidence
        ],
        "ci_evidence": [
            f"{e.get('repo') or 'repo'}:{e.get('path')} ({e['kind']}); claimed, run result not captured"
            for e in evidence
            if e["kind"] in AUTOMATED_EVIDENCE_KINDS and e["role"] == "implementation"
        ],
        "inline_mechanisms": (control or {}).get("inline_mechanisms", 0),
        "residual_actions": [excerpt(r["text"], 300) for r in residuals],
        "cm_test_procedures": [
            {
                "method": t["method"],
                "automated": t["automated"],
                "description": excerpt(t["description"], 200),
            }
            for t in (control or {}).get("test_procedures", [])
        ],
        "assessment_basis": "gsa-test-case"
        if cid in objective_controls
        else "desk-review-no-gsa-objectives",
        "proposed_result": result,
        "poam_issue": issue_links.get(cid) if result == OTS else None,
        "findings": sorted(set(findings)),
    }


def assess_objective(
    case: dict[str, Any], ctl: dict[str, Any], prose: dict[str | None, str]
) -> dict[str, Any]:
    cid = case["control_id"]
    letter = (
        None if cid.startswith(BOD_PREFIX) else procedure_part(case["procedure_id"])
    )
    part_text, part_key = None, None
    if prose:
        if letter and letter in prose:
            part_text, part_key = prose[letter], letter
        elif None in prose:
            part_text, part_key = prose[None], "(whole)"
        elif not letter:
            part_text, part_key = "\n".join(prose.values()), "(all parts)"
    methods = [m for m in ("examine", "interview", "test") if case.get(m)]
    findings: list[str] = []
    base = ctl["proposed_result"]
    if base in (INHERITED, NA, NOT_ASSESSED, OTS):
        result, basis = base, "control-level result"
    elif part_text is not None:
        marked = bool(MARKER_RX.search(part_text))
        result = CONDITIONAL if marked or base == CONDITIONAL else SATISFIED
        basis = "authored part prose" + (" with open-item markers" if marked else "")
    elif prose:
        result = NOT_ASSESSED
        basis = "authored prose exists but no part covers this objective"
        findings.append("AA-6")
    else:
        result = CONDITIONAL
        basis = "countermeasure summary only; no authored per-part prose"
    automated = [
        t for t in ctl["cm_test_procedures"] if t["automated"] and t["method"] == "test"
    ]
    return {
        "procedure_id": case["procedure_id"],
        "control_id": cid,
        "objective": case["objective"],
        "part": part_key,
        "methods_required": methods,
        "methods_pending_assessor": [
            m for m in methods if not (m == "test" and automated)
        ],
        "automated_test_evidence": [t["description"] for t in automated],
        "ci_evidence_claimed": ctl["ci_evidence"],
        "evidence_excerpt": excerpt(part_text, 200) if part_text else None,
        "proposed_result": result,
        "basis": basis,
        "findings": findings,
    }


def _confirmations(
    system: dict[str, Any], valid_ids: set[str]
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in system.get("confirmations", []):
        if c["id"] not in valid_ids:
            raise AssessmentError(f"confirmation id not in assessment scope: {c['id']}")
        if c["result"] not in CONFIRMED_RESULTS:
            raise AssessmentError(
                f"confirmation {c['id']}: result must be one of {sorted(CONFIRMED_RESULTS)}"
            )
        if not c.get("assessor") or not c.get("date"):
            raise AssessmentError(
                f"confirmation {c['id']}: assessor and date are required"
            )
        out[c["id"]] = c
    return out


def _apply_confirmations(
    items: list[dict[str, Any]], key: str, confirmations: dict[str, dict[str, Any]]
) -> None:
    for item in items:
        c = confirmations.get(item[key])
        item["confirmed_result"] = c["result"] if c else None
        item["confirmed_by"] = c["assessor"] if c else None
        item["confirmed_date"] = str(c["date"]) if c and c.get("date") else None


def build_assessment(
    system: dict[str, Any], tailoring: dict[str, Any], test_cases: dict[str, Any]
) -> dict[str, Any]:
    """Build the assessment document from validated inputs."""
    validate_input(system)
    validate_tailoring(tailoring)
    validate_test_cases(test_cases)

    ctw_by_id = {c["control_id"]: c for c in tailoring["controls"]}
    by_input = {c["control_id"]: c for c in system["controls"]}
    objective_controls = {t["control_id"] for t in test_cases["test_cases"]}
    scope = sorted(set(by_input) | objective_controls, key=control_sort_key)
    evidence_index = _evidence_index(system)
    issue_links = {
        canonical_control_id(k): v for k, v in system.get("issue_links", {}).items()
    }

    controls = [
        assess_control(
            cid,
            system,
            ctw_by_id.get(cid),
            by_input.get(cid),
            objective_controls,
            evidence_index.get(cid, []),
            issue_links,
        )
        for cid in scope
    ]
    by_id = {c["control_id"]: c for c in controls}
    objectives = [
        assess_objective(
            case,
            by_id[case["control_id"]],
            _prose_by_part(by_input[case["control_id"]])
            if case["control_id"] in by_input
            else {},
        )
        for case in test_cases["test_cases"]
    ]
    confirmations = _confirmations(
        system, {o["procedure_id"] for o in objectives} | set(by_id)
    )
    _apply_confirmations(controls, "control_id", confirmations)
    _apply_confirmations(objectives, "procedure_id", confirmations)

    findings = [
        {
            "id": f,
            "severity": FINDINGS[f][0],
            "control_id": c["control_id"],
            "family": c["family"],
            "title": FINDINGS[f][1],
        }
        for c in controls
        for f in c["findings"]
    ] + [
        {
            "id": f,
            "severity": FINDINGS[f][0],
            "control_id": o["procedure_id"],
            "family": o["control_id"].split("-")[0],
            "title": FINDINGS[f][1],
        }
        for o in objectives
        for f in o["findings"]
    ]
    findings.sort(key=lambda x: (SEV_ORDER[x["severity"]], x["id"], x["control_id"]))
    severity = Counter(f["severity"] for f in findings)
    confirmed = sum(1 for o in objectives if o["confirmed_result"])
    blockers = []
    if severity["High"]:
        blockers.append(f"{severity['High']} High finding(s) open")
    if confirmed < len(objectives):
        blockers.append(
            f"{len(objectives) - confirmed} of {len(objectives)} assessment objectives lack assessor confirmation"
        )

    return {
        "kind": "gsa-assessment-and-attestation",
        "system": system["system"]["slug"],
        "system_name": system["system"]["name"],
        "disclaimer": DISCLAIMER,
        "inputs": dict(sorted(system.get("input_digests", {}).items())),
        "versions": {
            "tailoring_workbook": tailoring["version"],
            "test_case_workbook": test_cases["version"],
        },
        "scope": {
            "controls": len(controls),
            "objectives": len(objectives),
            "controls_with_gsa_objectives": len(objective_controls),
            "test_case_workbook_categorization": test_cases["categorization_level"],
            "system_baseline": system["system"]["baseline"],
        },
        "summary": {
            "control_results": dict(
                sorted(Counter(c["proposed_result"] for c in controls).items())
            ),
            "objective_results": dict(
                sorted(Counter(o["proposed_result"] for o in objectives).items())
            ),
            "findings_by_severity": {k: severity.get(k, 0) for k in SEV_ORDER},
            "objectives_confirmed": confirmed,
        },
        "attestation": {
            "readiness": "NOT_READY_FOR_ATTESTATION"
            if blockers
            else "READY_FOR_AO_REVIEW",
            "blockers": blockers,
            "poam_required_controls": sorted(
                (c["control_id"] for c in controls if c["proposed_result"] == OTS),
                key=control_sort_key,
            ),
            "ao_decision": None,
            "ao_name": None,
            "ao_signature": None,
            "ao_date": None,
            "assessor_name": None,
            "assessor_signature": None,
            "assessor_date": None,
        },
        "findings": findings,
        "controls": controls,
        "objectives": objectives,
    }
