"""Tests for the GSA workbook converter, using small synthetic ODS packages only."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from compliance.lib import gsa_workbooks as g

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "compliance" / "scripts" / "convert_gsa_workbooks.py"

NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
)


def _cell(value: str) -> str:
    if not value:
        return "<table:table-cell/>"
    paragraphs = "".join(f"<text:p>{escape(p)}</text:p>" for p in value.split("\n"))
    return f"<table:table-cell>{paragraphs}</table:table-cell>"


def write_ods(path: Path, sheets: dict[str, list[list[str]]]) -> Path:
    body = ""
    for name, rows in sheets.items():
        row_xml = "".join(
            "<table:table-row>" + "".join(_cell(c) for c in row) + "</table:table-row>"
            for row in rows
        )
        body += f'<table:table table:name="{escape(name)}">{row_xml}</table:table>'
    content = (
        f'<?xml version="1.0"?><office:document-content {NS}><office:body>'
        f"<office:spreadsheet>{body}</office:spreadsheet></office:body>"
        "</office:document-content>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", content)
    return path


def tailoring_ods(tmp: Path) -> Path:
    header = [
        "",
        "Control ID",
        "Control Name",
        "Control Statement",
        "CCC",
        "Low",
        "Moderate",
        "High",
        "LATO",
        "MiSaaS",
        "Privacy",
        "PCI",
        "CUI",
        "Infra",
        "Vendor",
        "Approval",
    ]
    return write_ods(
        tmp / "Example-Control-Tailoring-Workbook-[Contains-CUI]-01-02-2030.ods",
        {
            "Instructions": [
                [
                    "Overview",
                    "See https://example.org/guide or mail ops@example.org.\nCUI//ISVI",
                ],
                ["Notes:", "Shaded rows are optional."],
                ["Column B- Control ID", "NIST control identifier."],
            ],
            "ControlDesignation": [["Designation"], ["Withdrawn"], ["PM"]],
            "GSA HVA Overlay": [
                [],
                [],
                [
                    "Control ID",
                    "Control Name",
                    "Control Statement",
                    "HVA Moderate",
                    "HVA High",
                    "Vendor",
                    "Approval",
                ],
                ["AC-02", "Account Management", "", "X", "X"],
            ],
            "GSA Standard Settings": [
                [],
                [],
                header,
                [
                    "",
                    "AC-01",
                    "(Access Control)\nPolicy",
                    "a. Develop a policy.",
                    "X",
                    "X",
                    "X",
                    "X",
                    "",
                    "X",
                    "",
                    "",
                    "",
                    "",
                ],
                [
                    "",
                    "AC-02",
                    "Account Management",
                    "a. Define account types.",
                    "",
                    "",
                    "X",
                    "X",
                    "X",
                    "",
                    "X",
                    "",
                    "X",
                    "X",
                    "value",
                    "approved",
                ],
                [
                    "PM",
                    "AC-02(01)",
                    "Automated Account Management",
                    "Support automation.",
                    "",
                    "",
                    "",
                    "X",
                ],
            ],
        },
    )


def build_test_cases_ods(tmp: Path) -> Path:
    cases = [
        "Account Management",
        "AC-02",
        "AC-02a.[01]",
        "types defined;",
        "[SELECT FROM: policy]",
        "[SELECT FROM: admins]",
        "",
        "",
        "",
        "",
    ]
    return write_ods(
        tmp / "Example-Test-Case-Workbook-[Contains-CUI]-01-02-2030.ods",
        {
            "Notes": [
                ["CUI//ISVI"],
                ["Version 01/02/2030"],
                ["Guidance. See https://example.org/x"],
            ],
            "Logic": [
                ["Control Family", "Control ID"],
                ["", "", "Total"],
                ["AC", "AC-02", "2"],
                ["BOD", "BOD 18-01", "1"],
            ],
            "CtrlSummary": [
                [],
                ["Control Family", "Control ID", "Control Name", "Assessment Result"],
                ["", "", "Access Control (AC)"],
                ["AC", "AC-02", "Account Management", "Incomplete"],
                ["BOD", "BOD 18-01", "Email and Web Security", "Incomplete"],
            ],
            **{
                s: [
                    [],
                    [],
                    *(
                        [
                            cases,
                            [
                                "Account Management",
                                "AC-02",
                                "AC-02b.",
                                "types assigned;",
                                "",
                                "",
                                "run test",
                                "",
                                "",
                                "",
                            ],
                        ]
                        if s == "AC"
                        else []
                    ),
                ]
                for s in g.FAMILY_SHEETS
                if s != "BOD"
            },
            "BOD": [
                [],
                [],
                [
                    "BOD 18-01",
                    "BOD 18-01",
                    "BOD 18-01.1",
                    "weak ciphers are disabled.",
                    "",
                    "",
                    "Follow the SOP",
                    "",
                    "",
                    "",
                ],
            ],
        },
    )


@pytest.fixture
def tailoring(tmp_path: Path) -> dict:
    return g.convert_tailoring_workbook(tailoring_ods(tmp_path))


@pytest.fixture
def cases(tmp_path: Path) -> dict:
    return g.convert_test_case_workbook(build_test_cases_ods(tmp_path))


def test_control_ids_are_normalized_and_enhancements_stay_distinct(
    tailoring: dict,
) -> None:
    ids = [c["control_id"] for c in tailoring["controls"]]
    assert ids == ["AC-1", "AC-2", "AC-2(1)"]
    assert tailoring["controls"][2]["raw_control_id"] == "AC-02(01)"
    assert tailoring["controls"][2]["designation"] == "PM"
    assert tailoring["controls"][2]["family"] == "AC"


def test_baseline_and_overlay_flags_and_summaries_agree(tailoring: dict) -> None:
    ac2 = next(c for c in tailoring["controls"] if c["control_id"] == "AC-2")
    assert ac2["baselines"] == {
        "ccc": False,
        "low": False,
        "moderate": True,
        "high": True,
        "lato": True,
        "misaas": False,
    }
    assert (
        ac2["overlays"]["privacy"]
        and ac2["overlays"]["cui"]
        and ac2["overlays"]["infra"]
    )
    assert ac2["overlays"]["hva_moderate"] and ac2["overlays"]["hva_high"]
    assert tailoring["baselines_summary"]["high"] == 3
    assert tailoring["overlays_summary"]["hva_high"] == 1
    assert [h["control_id"] for h in tailoring["hva_overlay"]] == ["AC-2"]


def test_free_text_is_redacted_and_multiline_titles_collapse(tailoring: dict) -> None:
    overview = tailoring["instructions"]["overview"]
    assert "<REDACTED_URL>" in overview and "<POC_EMAIL>" in overview
    assert "CUI//" not in overview and "example.org" not in overview
    assert tailoring["controls"][0]["name"] == "(Access Control) Policy"


def test_version_is_inferred_from_the_file_name_and_overridable(tmp_path: Path) -> None:
    path = tailoring_ods(tmp_path)
    assert g.convert_tailoring_workbook(path)["version"] == "2030-01-02"
    assert g.convert_tailoring_workbook(path, version="v9")["version"] == "v9"
    with pytest.raises(g.WorkbookError, match="version"):
        g.version_from_filename("no-date.ods")


def test_test_cases_keep_methods_and_bod_ids(cases: dict) -> None:
    assert cases["version"] == "01/02/2030"
    assert cases["total_test_cases"] == 3
    first, second, bod = cases["test_cases"]
    assert first["control_id"] == "AC-2" and first["procedure_id"] == "AC-02a.[01]"
    assert first["examine"].startswith("[SELECT FROM") and second["test"] == "run test"
    assert bod["control_id"] == "BOD 18-01" and bod["family"] == "BOD"
    counts = {s["control_id"]: s["expected_test_cases"] for s in cases["summary"]}
    assert counts == {"AC-2": 2, "BOD 18-01": 1}
    assert "<REDACTED_URL>" in " ".join(cases["notes"])


def test_conversion_is_deterministic(tmp_path: Path) -> None:
    path = tailoring_ods(tmp_path)
    assert g.render_json(g.convert_tailoring_workbook(path)) == g.render_json(
        g.convert_tailoring_workbook(path)
    )


def test_converted_output_validates(tailoring: dict, cases: dict) -> None:
    g.validate_canonical_json(tailoring)
    g.validate_canonical_json(cases)


def test_validation_rejects_count_and_identity_drift(
    tailoring: dict, cases: dict
) -> None:
    bad = copy.deepcopy(tailoring)
    bad["baselines_summary"]["high"] += 1
    assert any("baselines_summary[high]" in e for e in g.tailoring_errors(bad))
    bad = copy.deepcopy(tailoring)
    bad["controls"][1]["control_id"] = "AC-02"
    assert g.tailoring_errors(bad)
    bad = copy.deepcopy(tailoring)
    bad["controls"].append(copy.deepcopy(bad["controls"][0]))
    bad["total_controls"] += 1
    assert any("duplicate control_id" in e for e in g.tailoring_errors(bad))
    bad = copy.deepcopy(cases)
    bad["summary"][0]["expected_test_cases"] = 9
    assert any("expected 9" in e for e in g.test_case_errors(bad))
    bad = copy.deepcopy(cases)
    bad["test_cases"].append(copy.deepcopy(bad["test_cases"][0]))
    bad["total_test_cases"] += 1
    assert any("duplicate procedure_id" in e for e in g.test_case_errors(bad))


def test_schema_rejects_unknown_fields_and_unredacted_contact(tailoring: dict) -> None:
    bad = copy.deepcopy(tailoring)
    bad["extra"] = 1
    assert g.tailoring_errors(bad)
    bad = copy.deepcopy(tailoring)
    bad["poc_email"] = "someone@example.org"
    assert g.tailoring_errors(bad)
    with pytest.raises(g.WorkbookError, match="unknown canonical JSON kind"):
        g.validate_canonical_json({"kind": "nope"})


def test_invalid_workbooks_fail_closed(tmp_path: Path) -> None:
    junk = tmp_path / "junk.ods"
    junk.write_bytes(b"not an ods")
    with pytest.raises(g.WorkbookError, match="not a readable ODS"):
        g.convert_tailoring_workbook(junk)
    empty = write_ods(
        tmp_path / "Empty-01-02-2030.ods", {"Instructions": [["Overview", "x"]]}
    )
    with pytest.raises(g.WorkbookError, match="sheet 'ControlDesignation' not found"):
        g.convert_tailoring_workbook(empty)
    with pytest.raises(ValueError):
        g.canonical_control_id("not-a-control")


def test_cli_bundled_flag_targets_the_shipped_data_dir(tmp_path: Path) -> None:
    ctw, tc = tailoring_ods(tmp_path), build_test_cases_ods(tmp_path)
    result = run_cli(
        "--tailoring-workbook",
        str(ctw),
        "--test-case-workbook",
        str(tc),
        "--bundled",
        "--check",
        cwd=tmp_path,
    )
    # synthetic workbooks differ from the shipped data, so this must report drift
    assert result.returncode == 1 and str(g.DATA_DIR) in result.stderr


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=cwd
    )


def test_cli_writes_checks_and_detects_drift(tmp_path: Path) -> None:
    ctw, tc = tailoring_ods(tmp_path), build_test_cases_ods(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    args = [
        "--tailoring-workbook",
        str(ctw),
        "--test-case-workbook",
        str(tc),
        "--workspace",
        str(work),
    ]
    wrote = run_cli(*args, cwd=tmp_path)
    assert wrote.returncode == 0, wrote.stderr
    out = work / "data" / "catalog" / g.TAILORING_FILENAME
    assert json.loads(out.read_text(encoding="utf-8"))["kind"] == g.TAILORING_KIND
    assert run_cli(*args, "--check", cwd=tmp_path).returncode == 0
    out.write_text(
        out.read_text(encoding="utf-8").replace("Account Management", "Changed"),
        encoding="utf-8",
    )
    drift = run_cli(*args, "--check", cwd=tmp_path)
    assert drift.returncode == 1 and "drift" in drift.stderr
    assert (
        run_cli(
            "--validate",
            str(work / "data" / "catalog" / g.TEST_CASES_FILENAME),
            cwd=tmp_path,
        ).returncode
        == 0
    )


def test_cli_validate_reports_failures_and_missing_input(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"kind": "nope"}), encoding="utf-8")
    assert run_cli("--validate", str(bad), cwd=tmp_path).returncode == 1
    assert run_cli(cwd=tmp_path).returncode == 2
    missing = run_cli(
        "--tailoring-workbook",
        str(tmp_path / "absent.ods"),
        "--workspace",
        str(tmp_path),
        cwd=tmp_path,
    )
    assert missing.returncode == 2
