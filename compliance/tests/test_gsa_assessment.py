"""Tests for the GSA assessment engine, using small synthetic inputs only."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from compliance.lib import gsa_assessment as ga
from compliance.lib import gsa_poam_issues as pi
from compliance.lib.gsa_assessment_report import (
    redaction_hits,
    render_json,
    render_markdown,
)

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "compliance" / "scripts" / "build_gsa_assessment.py"
ISSUES = ROOT / "compliance" / "scripts" / "build_poam_issues.py"
SHA = "a" * 64


def ctl(cid: str, name: str, **baselines: bool) -> dict:
    flags = {k: False for k in ("ccc", "low", "moderate", "high", "lato", "misaas")}
    flags.update(baselines)
    return {
        "control_id": cid,
        "raw_control_id": cid,
        "family": cid.split("-")[0],
        "name": name,
        "designation": None,
        "statement": "Use [an approved value] here.",
        "baselines": flags,
        "overlays": {
            k: False
            for k in ("privacy", "pci", "cui", "infra", "hva_moderate", "hva_high")
        },
        "vendor_contractor_values": "",
        "gsa_approval": "",
    }


def tailoring() -> dict:
    controls = [
        ctl("AC-2", "Account Management", moderate=True),
        ctl("AC-3", "Access Enforcement", moderate=True),
        ctl("AC-4", "Information Flow", moderate=True),
        ctl("AU-2", "Event Logging", moderate=True),
        ctl("CM-5", "Access Restrictions", high=True),
        ctl("SC-7", "Boundary Protection", moderate=True),
        ctl("SC-13", "Cryptographic Protection", moderate=True),
        ctl("PE-3", "Physical Access", moderate=True),
    ]
    base = {
        k: sum(c["baselines"][k] for c in controls)
        for k in ("ccc", "low", "moderate", "high", "lato", "misaas")
    }
    return {
        "kind": "gsa-control-tailoring-workbook",
        "title": "t",
        "version": "2030-01-02",
        "source_document": "x.ods",
        "authority": "a",
        "poc_email": "<POC_EMAIL>",
        "instructions": {
            "overview": "",
            "shading_notes": [],
            "column_descriptions": {},
        },
        "control_designations": [],
        "total_controls": len(controls),
        "baselines_summary": base,
        "overlays_summary": {
            k: 0 for k in ("privacy", "pci", "cui", "infra", "hva_moderate", "hva_high")
        },
        "controls": controls,
        "hva_overlay": [],
    }


def tc(control_id: str, proc: str, **methods: str) -> dict:
    row = {
        "family": control_id.split("-")[0].split(" ")[0],
        "control_id": control_id,
        "raw_control_id": control_id,
        "control_name": "n",
        "procedure_id": proc,
        "objective": f"objective for {proc}",
        "examine": "",
        "interview": "",
        "test": "",
        "observations_and_evidence": "",
        "assessment_result": "",
        "recommendation_for_mitigation": "",
    }
    row.update(methods)
    return row


def cases_doc() -> dict:
    cases = [
        tc("AC-2", "AC-02a.[01]", examine="e", interview="i"),
        tc("AC-2", "AC-02b.", examine="e"),
        tc("AC-3", "AC-03", examine="e", test="t"),
        tc("SC-7", "SC-07a.[01]", examine="e", test="t"),
        tc("BOD 18-01", "BOD 18-01.1", test="sop"),
    ]
    counts: dict[str, int] = {}
    for c in cases:
        counts[c["control_id"]] = counts.get(c["control_id"], 0) + 1
    return {
        "kind": "gsa-lato-low-test-cases",
        "title": "t",
        "version": "01/02/2030",
        "source_document": "y.ods",
        "authority": "a",
        "poc_email": "<POC_EMAIL>",
        "categorization_level": "Low",
        "notes": [],
        "total_controls_in_scope": len(counts),
        "total_test_cases": len(cases),
        "summary": [
            {
                "family": k.split("-")[0].split(" ")[0],
                "control_id": k,
                "raw_control_id": k,
                "name": "n",
                "assessment_result": "Incomplete",
                "expected_test_cases": v,
            }
            for k, v in counts.items()
        ],
        "test_cases": cases,
    }


def sys_ctl(cid: str, **kw) -> dict:
    base = {
        "control_id": cid,
        "has_countermeasure": True,
        "draft": False,
        "implementation_status": "implemented",
        "prose": [],
        "evidence_refs": ["ev1"],
        "residual_actions": [],
        "test_procedures": [],
    }
    base.update(kw)
    return base


def system() -> dict:
    return {
        "schema_version": 1,
        "system": {
            "slug": "demo",
            "name": "Demo",
            "baseline": "Moderate",
            "confirmations_path": "conf.yaml",
        },
        "input_digests": {"a.json": SHA},
        "controls": [
            sys_ctl(
                "AC-2",
                prose=[
                    {"part": "a", "text": "Accounts are federated."},
                    {
                        "part": "b",
                        "text": "Managers are named. Residual action pending.",
                    },
                ],
            ),
            sys_ctl(
                "AC-3",
                implementation_status="not-implemented",
                residual_actions=[{"text": "GAP: masking missing", "gap": True}],
                test_procedures=[
                    {
                        "method": "test",
                        "description": "run governance tests",
                        "automated": True,
                    }
                ],
            ),
            sys_ctl("AC-4", draft=True),
            sys_ctl(
                "AU-2",
                has_countermeasure=False,
                inheritance="Fully Inherited",
                implementation_status=None,
                evidence_refs=[],
            ),
            sys_ctl("CM-5", draft=True),
            sys_ctl(
                "SC-13", evidence_refs=["ev2"], implementation_status="implemented"
            ),
            sys_ctl("XX-9", has_countermeasure=True),
        ],
        "evidence": [
            {
                "key": "ev1",
                "kind": "ci-test",
                "role": "implementation",
                "repo": "r",
                "path": "t.py",
                "claim_sha256": SHA,
                "control_ids": ["AC-3", "AC-2"],
            },
            {
                "key": "ev2",
                "kind": "config",
                "role": "observation",
                "control_ids": ["SC-13"],
            },
        ],
        "confirmations": [],
        "issue_links": {"AC-3": 12},
    }


def make(sysdoc: dict | None = None) -> dict:
    return ga.build_assessment(sysdoc or system(), tailoring(), cases_doc())


def by(a: dict, cid: str) -> dict:
    return next(c for c in a["controls"] if c["control_id"] == cid)


def test_every_objective_is_assessed_once_and_scope_unions_inputs() -> None:
    a = make()
    assert len(a["objectives"]) == 5
    ids = {c["control_id"] for c in a["controls"]}
    assert {"AC-2", "AC-3", "SC-7", "BOD 18-01", "XX-9"} <= ids


def test_open_gap_is_other_than_satisfied_with_high_finding_and_issue_link() -> None:
    a = make()
    ac3 = by(a, "AC-3")
    assert ac3["proposed_result"] == ga.OTS
    assert {"AA-2"} <= set(ac3["findings"])
    assert ac3["poam_issue"] == 12
    assert "AC-3" in a["attestation"]["poam_required_controls"]


def test_results_for_each_rule() -> None:
    a = make()
    assert by(a, "AC-2")["proposed_result"] == ga.SATISFIED
    assert by(a, "AC-4")["proposed_result"] == ga.CONDITIONAL
    assert "AA-3" in by(a, "AC-4")["findings"]
    assert by(a, "AU-2")["proposed_result"] == ga.INHERITED
    assert by(a, "SC-7")["proposed_result"] == ga.NOT_ASSESSED
    assert "AA-1" not in by(a, "SC-7")["findings"]


def test_non_inherited_in_baseline_without_record_is_a_high_finding() -> None:
    sysdoc = system()
    sysdoc["controls"].append(
        sys_ctl(
            "SC-7",
            has_countermeasure=False,
            inheritance="Hybrid",
            implementation_status=None,
            evidence_refs=[],
        )
    )
    c = by(make(sysdoc), "SC-7")
    assert c["proposed_result"] == ga.NOT_ASSESSED and "AA-1" in c["findings"]


def test_bod_has_no_part_mapping_and_stays_not_assessed() -> None:
    bod = [o for o in make()["objectives"] if o["control_id"] == "BOD 18-01"]
    assert len(bod) == 1 and bod[0]["proposed_result"] == ga.NOT_ASSESSED


def test_baseline_findings_and_unknown_controls() -> None:
    a = make()
    assert "AA-8" in by(a, "CM-5")["findings"]
    assert "AA-7" in by(a, "XX-9")["findings"]


def test_observation_only_evidence_is_flagged_aa9() -> None:
    sc13 = by(make(), "SC-13")
    assert "AA-9" in sc13["findings"]
    assert all(e["role"] == "observation" for e in sc13["evidence"])


def test_objective_part_mapping_and_markers() -> None:
    a = make()
    parts = {o["procedure_id"]: o for o in a["objectives"] if o["control_id"] == "AC-2"}
    assert parts["AC-02a.[01]"]["part"] == "a"
    assert parts["AC-02a.[01]"]["proposed_result"] == ga.SATISFIED
    assert parts["AC-02b."]["proposed_result"] == ga.CONDITIONAL
    assert "open-item markers" in parts["AC-02b."]["basis"]
    assert ga.procedure_part("CM-03(01)(c)") == "c"
    assert ga.procedure_part("AC-02l.[02]") == "l"
    assert ga.procedure_part("CM-02(02)[01]") is None
    assert ga.procedure_part("AC-03") is None


def test_uncovered_part_is_an_aa6_finding() -> None:
    sysdoc = system()
    sysdoc["controls"][0]["prose"] = [{"part": "z", "text": "only part z"}]
    ac2a = next(
        o for o in make(sysdoc)["objectives"] if o["procedure_id"] == "AC-02a.[01]"
    )
    assert ac2a["proposed_result"] == ga.NOT_ASSESSED and "AA-6" in ac2a["findings"]


def test_ci_evidence_is_claimed_and_never_counted_as_a_passed_test() -> None:
    ac3 = next(o for o in make()["objectives"] if o["control_id"] == "AC-3")
    assert ac3["ci_evidence_claimed"] and all(
        "run result not captured" in c for c in ac3["ci_evidence_claimed"]
    )
    assert "examine" in ac3["methods_pending_assessor"]
    assert "test" not in ac3["methods_pending_assessor"]


def test_results_are_proposed_and_attestation_is_unsigned() -> None:
    a = make()
    assert a["summary"]["objectives_confirmed"] == 0
    assert a["attestation"]["readiness"] == "NOT_READY_FOR_ATTESTATION"
    for field in (
        "ao_decision",
        "ao_name",
        "ao_signature",
        "ao_date",
        "assessor_name",
        "assessor_signature",
        "assessor_date",
    ):
        assert a["attestation"][field] is None


def test_confirmations_are_recorded_and_validated() -> None:
    sysdoc = system()
    sysdoc["confirmations"] = [
        {
            "id": "ZZ-1",
            "result": "Other Than Satisfied",
            "assessor": "Assessor role",
            "date": "2030-01-05",
        }
    ]
    with pytest.raises(ga.AssessmentError, match="not in assessment scope"):
        make(sysdoc)
    sysdoc["confirmations"][0]["id"] = "AC-3"
    with pytest.raises(ga.AssessmentError, match="result must be one of"):
        bad = copy.deepcopy(sysdoc)
        bad["confirmations"][0]["result"] = "Maybe"
        make(bad)
    with pytest.raises(ga.AssessmentError, match="assessor and date"):
        bad = copy.deepcopy(sysdoc)
        bad["confirmations"][0]["date"] = None
        make(bad)
    assert by(make(sysdoc), "AC-3")["confirmed_result"] == "Other Than Satisfied"


def test_input_validation_rejects_duplicates_and_noncanonical_ids() -> None:
    sysdoc = system()
    sysdoc["controls"].append(copy.deepcopy(sysdoc["controls"][0]))
    with pytest.raises(ga.AssessmentError, match="duplicate"):
        make(sysdoc)
    sysdoc = system()
    sysdoc["controls"][0]["control_id"] = "AC-02"
    with pytest.raises(ga.AssessmentError):
        make(sysdoc)
    sysdoc = system()
    sysdoc["unexpected"] = 1
    with pytest.raises(ga.AssessmentError):
        make(sysdoc)


def test_output_is_deterministic_and_redaction_clean() -> None:
    a, b = make(), make()
    assert render_json(a) == render_json(b)
    assert render_markdown(a) == render_markdown(b)
    assert redaction_hits(render_json(a), render_markdown(a)) == []
    assert redaction_hits("see https://example.org", "mail a@b.org", "CUI//ISVI") == [
        "CUI banner marking",
        "URL",
        "email address",
    ]


def test_markdown_links_issue_numbers_and_names_the_confirmations_file() -> None:
    md = render_markdown(make(), confirmations_path="conf.yaml")
    assert (
        "- AC-3: #12" in md and "`conf.yaml`" in md and "Attestation (unsigned)" in md
    )


def work_items(controls: list[str]) -> dict:
    return {
        "groups": {
            "shared": {
                "title": "Shared job",
                "evidence": ["g-evidence"],
                "validation": ["g-validation"],
                "acceptance": ["g-accept"],
                "depends_on": ["other#1"],
            }
        },
        "controls": {
            c: {
                "owner_role": "Owner",
                "why": f"why {c}",
                "evidence": [f"{c} evidence"],
                "validation": [f"{c} test"],
                "acceptance": [f"{c} accept"],
                "uses": ["shared"],
            }
            for c in controls
        },
    }


def test_issue_bodies_have_evidence_validation_and_acceptance() -> None:
    a = make()
    rendered = pi.render_all(a, work_items(["AC-3"]))
    title, body = rendered["AC-3"]
    assert title.startswith("[A&A POA&M] AC-3 ")
    for heading in (
        "## Evidence to collect",
        "## Validation testing",
        "## Acceptance criteria",
    ):
        section = body.split(heading, 1)[1].split("\n## ", 1)[0]
        assert "- [ ] " in section
    assert "`AC-03`" in body and "g-evidence" in body and "other#1" in body
    assert "this issue does not authorize it" in body
    assert body.count("- [ ] ") >= 8


def test_work_items_must_cover_exactly_the_poam_controls() -> None:
    a = make()
    with pytest.raises(pi.WorkItemsError, match="missing work items"):
        pi.render_all(a, {"groups": {}, "controls": {}})
    with pytest.raises(pi.WorkItemsError, match="not POA&M-required"):
        pi.render_all(a, work_items(["AC-3", "AC-2"]))
    broken = work_items(["AC-3"])
    broken["controls"]["AC-3"]["uses"] = ["nope"]
    with pytest.raises(pi.WorkItemsError, match="unknown group"):
        pi.render_all(a, broken)
    empty = work_items(["AC-3"])
    empty["controls"]["AC-3"].update(evidence=[], validation=[], uses=[])
    with pytest.raises(pi.WorkItemsError, match="no evidence to collect"):
        pi.render_all(a, empty)


def run(script: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, cwd=cwd
    )


def test_cli_builds_checks_and_detects_drift(tmp_path: Path) -> None:
    work = tmp_path / "ws"
    (work / "data" / "catalog").mkdir(parents=True)
    (
        work / "data" / "catalog" / "gsa-control-tailoring-workbook.canonical.json"
    ).write_text(json.dumps(tailoring()), encoding="utf-8")
    (work / "data" / "catalog" / "gsa-lato-low-test-cases.canonical.json").write_text(
        json.dumps(cases_doc()), encoding="utf-8"
    )
    inp = work / "input.json"
    inp.write_text(json.dumps(system()), encoding="utf-8")
    args = ["--system-input", str(inp), "--workspace", str(work)]
    assert run(BUILD, *args, cwd=tmp_path).returncode == 0
    md = work / "build" / "assessment" / "demo-aa-assessment.md"
    assert md.exists() and run(BUILD, *args, "--check", cwd=tmp_path).returncode == 0
    md.write_text(md.read_text(encoding="utf-8") + "x", encoding="utf-8")
    drift = run(BUILD, *args, "--check", cwd=tmp_path)
    assert drift.returncode == 1 and "stale" in drift.stderr
    assert (
        run(
            BUILD,
            "--system-input",
            str(work / "absent.json"),
            "--workspace",
            str(work),
            cwd=tmp_path,
        ).returncode
        == 2
    )


def test_issue_cli_dry_run_show_and_guards(tmp_path: Path) -> None:
    assessment = tmp_path / "a.json"
    assessment.write_text(render_json(make()), encoding="utf-8")
    items = tmp_path / "w.yaml"
    items.write_text(yaml.safe_dump(work_items(["AC-3"])), encoding="utf-8")
    base = ["--assessment", str(assessment), "--work-items", str(items)]
    dry = run(ISSUES, *base, cwd=tmp_path)
    assert dry.returncode == 0 and "1 issues would be filed" in dry.stdout
    assert (
        "## Acceptance criteria"
        in run(ISSUES, *base, "--show", "AC-3", cwd=tmp_path).stdout
    )
    assert run(ISSUES, *base, "--show", "AC-2", cwd=tmp_path).returncode == 2
    assert run(ISSUES, *base, "--create", cwd=tmp_path).returncode == 2
    items.write_text(yaml.safe_dump(work_items([])), encoding="utf-8")
    assert run(ISSUES, *base, cwd=tmp_path).returncode == 2
