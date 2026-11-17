"""Render one tracked-issue body per POA&M-required control from an assessment document.

Control facts come from the assessment document. Close-out content (why, evidence to collect,
validation tests, acceptance criteria, owner role, dependencies, shared groups) is curated by
the system team in a *work-items* mapping and passed in; the core holds no system content.

Work-items shape::

    groups:   {<name>: {title, depends_on, observed, evidence, validation, acceptance}}
    controls: {<control_id>: {owner_role, why, uses: [<group>], observed, depends_on,
                              evidence, validation, acceptance}}
"""

from __future__ import annotations

from typing import Any

from compliance.lib.gsa_assessment import OTS

COMMON_ACCEPTANCE = [
    "The system security plan response and countermeasure records are updated to match reality; "
    "the recorded gap is removed or rewritten as a control-specific residual, and any evidence "
    "reference points to a real artifact.",
    "The assessment regenerates with no High finding for this control and no status conflict "
    "(AA-5).",
    "An assessor records the result for the control and for each GSA objective listed above in "
    "the system's confirmations file (role not email, date, observation).",
    "The ISSO has reviewed the change. A DRAFT marker is removed only by the ISSO; this issue "
    "does not authorize it.",
    "The system's test suite and assessment drift check pass on the change that closes this issue.",
]
REQUIRED_SPEC_KEYS = ("owner_role", "why", "evidence", "validation", "acceptance")
DEFAULT_TITLE_PREFIX = "[A&A POA&M]"


class WorkItemsError(ValueError):
    """The work-items mapping does not cover the assessment's POA&M controls."""


def poam_controls(assessment: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in assessment["controls"] if c["proposed_result"] == OTS]


def check_work_items(assessment: dict[str, Any], work_items: dict[str, Any]) -> None:
    """Fail closed unless the work items cover exactly the POA&M controls, completely."""
    ids = {c["control_id"] for c in poam_controls(assessment)}
    have = set(work_items.get("controls", {}))
    problems: list[str] = []
    if ids - have:
        problems.append(f"missing work items: {sorted(ids - have)}")
    if have - ids:
        problems.append(
            f"work items for controls that are not POA&M-required: {sorted(have - ids)}"
        )
    groups = work_items.get("groups", {})
    for cid in sorted(have & ids):
        spec = work_items["controls"][cid]
        for key in REQUIRED_SPEC_KEYS:
            if not spec.get(key) and key not in (
                "evidence",
                "validation",
                "acceptance",
            ):
                problems.append(f"{cid}: empty {key}")
        for g in spec.get("uses", []):
            if g not in groups:
                problems.append(f"{cid}: unknown group {g}")
        merged_evidence = list(spec.get("evidence", [])) + [
            e
            for g in spec.get("uses", [])
            for e in groups.get(g, {}).get("evidence", [])
        ]
        merged_validation = list(spec.get("validation", [])) + [
            v
            for g in spec.get("uses", [])
            for v in groups.get(g, {}).get("validation", [])
        ]
        if not merged_evidence:
            problems.append(f"{cid}: no evidence to collect")
        if not merged_validation:
            problems.append(f"{cid}: no validation testing")
    if problems:
        raise WorkItemsError("; ".join(problems))


def title_for(control: dict[str, Any], prefix: str = DEFAULT_TITLE_PREFIX) -> str:
    short = control["name"].split("|")[-1].strip()
    return f"{prefix} {control['control_id']} {short}: close Other Than Satisfied gap"


def _bullets(items: list[str], box: bool = False) -> str:
    mark = "- [ ] " if box else "- "
    return "\n".join(mark + i for i in items) if items else "- none recorded"


def render_body(
    control: dict[str, Any], assessment: dict[str, Any], work_items: dict[str, Any]
) -> str:
    cid = control["control_id"]
    spec = work_items["controls"][cid]
    groups = [work_items["groups"][g] for g in spec.get("uses", [])]
    objectives = [o for o in assessment["objectives"] if o["control_id"] == cid]

    def merged(key: str) -> list[str]:
        out: list[str] = []
        for g in groups:
            out += g.get(key, [])
        return out + spec.get(key, [])

    depends = list(dict.fromkeys(merged("depends_on")))
    objective_lines = [
        f"`{o['procedure_id']}` ({', '.join(o['methods_required']) or 'no method'}): {o['objective']}"
        for o in objectives
    ]
    shared = "".join(
        f"\n**Shared work: {g['title']}.** Complete it once; this issue is closed by its own observations.\n"
        for g in groups
    )
    parts = [
        "> AI-assisted draft from the A&A desk review; the proposed result is not an assessment conclusion. Human review required. Items under Observed were point-in-time checks and must be re-verified by the assessor.\n",
        "## Gap",
        f"**{cid}: {control['name']}**  \nProposed result: `{control['proposed_result']}` | Findings: {', '.join(control['findings']) or 'none'} | Inheritance: {control['inheritance'] or 'n/a'} | Status: {control['implementation_status'] or 'n/a'} | GSA baselines: {', '.join(control['gsa_baselines']) or 'none'}\n",
        spec["why"],
        shared,
        "**Recorded residual actions:**",
        _bullets(control["residual_actions"]),
        "\n**Observed (verify before relying on it):**",
        _bullets(merged("observed")),
        "\n## GSA assessment objectives",
        _bullets(objective_lines)
        if objective_lines
        else "- No GSA test-case objectives exist for this control. Desk review only.",
        "\n## Evidence to collect",
        _bullets(merged("evidence"), box=True),
        "\n## Validation testing",
        _bullets(merged("validation"), box=True),
        "\n## Acceptance criteria",
        _bullets(merged("acceptance") + COMMON_ACCEPTANCE, box=True),
        "\n## Tracking",
        f"- Suggested owner role: {spec['owner_role']} (assignee and target date set by the ISSO)",
        f"- Depends on: {', '.join(depends) if depends else 'none'}",
    ]
    return "\n".join(parts).replace("\n\n\n", "\n\n") + "\n"


def render_all(
    assessment: dict[str, Any],
    work_items: dict[str, Any],
    prefix: str = DEFAULT_TITLE_PREFIX,
) -> dict[str, tuple[str, str]]:
    """Control ID -> (title, body) for every POA&M-required control."""
    check_work_items(assessment, work_items)
    return {
        c["control_id"]: (title_for(c, prefix), render_body(c, assessment, work_items))
        for c in poam_controls(assessment)
    }
