"""Tests for GSA CCC Rev 7 parser and diff helpers."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
REPO_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
for _p in (ROOT, REPO_SCRIPTS):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


parse_ccc_docx = _load("parse_ccc_docx", SCRIPTS / "parse_ccc_docx.py")
diff_ccc = _load("diff_ccc", SCRIPTS / "diff_ccc.py")


class _Cell:
    def __init__(self, text: str):
        self.text = text


class _Row:
    def __init__(self, cells: list[str]):
        self.cells = [_Cell(cell) for cell in cells]


class _Table:
    def __init__(self, rows: list[list[str]]):
        self.rows = [_Row(row) for row in rows]
        self.columns = [object()] * 5


class _Doc:
    def __init__(self, tables: list[_Table]):
        self.tables = tables


def _doc_factory(_path: str) -> _Doc:
    return _Doc(
        [
            _Table(
                [
                    [
                        "Control ID",
                        "Control Name",
                        "Federal System Control Type",
                        "Vendor/ Contractor System Control Type",
                        "Inheritance Source",
                    ],
                    [
                        "AC-02(005)",
                        "Account Management | Inactivity",
                        "Common",
                        "Hybrid",
                        "OCISO",
                    ],
                    ["AU-03", "Content of Audit Records", "Hybrid", "Common", "OCISO"],
                ]
            ),
            _Table(
                [
                    [
                        "Control ID",
                        "Control Name",
                        "Control Type",
                        "Inheritance Source",
                        "Platform/ Program Association",
                    ],
                    [
                        "SR-04",
                        "Provenance",
                        "System Specific",
                        "System Owner",
                        "Program",
                    ],
                ]
            ),
        ]
    )


def test_parse_ccc_docx_normalizes_ids_and_appendix_c_without_docx_dependency(
    tmp_path: Path,
) -> None:
    report = parse_ccc_docx.parse(
        tmp_path / "ccc.docx",
        "Rev 7 draft",
        "Table 3-3",
        include_appendix_c=True,
        document_factory=_doc_factory,
    )

    assert report["control_count"] == 2
    assert report["controls"][0] == {
        "control_id": "AC-2(5)",
        "name": "Account Management | Inactivity",
        "federal_type": "Common",
        "contractor_type": "Hybrid",
        "inheritance_source": "OCISO",
    }
    assert report["appendix_c"] == [
        {
            "control_id": "SR-4",
            "name": "Provenance",
            "control_type": "System Specific",
            "inheritance_source": "System Owner",
            "platform_program": "Program",
        }
    ]


def test_parse_ccc_docx_rejects_duplicate_canonical_ids(tmp_path: Path) -> None:
    def duplicate_doc_factory(_path: str) -> _Doc:
        return _Doc(
            [
                _Table(
                    [
                        [
                            "Control ID",
                            "Control Name",
                            "Federal System Control Type",
                            "Vendor/ Contractor System Control Type",
                            "Inheritance Source",
                        ],
                        ["AC-2(1)", "Parent", "Common", "Hybrid", "OCISO"],
                        ["AC-02.01", "Duplicate", "Common", "Hybrid", "OCISO"],
                    ]
                )
            ]
        )

    with pytest.raises(
        ValueError, match=r"duplicate canonical control ID in CCC: AC-2\(1\)"
    ):
        parse_ccc_docx.parse(
            tmp_path / "ccc.docx",
            "Rev 7 draft",
            "Table 3-3",
            document_factory=duplicate_doc_factory,
        )


def test_diff_ccc_normalizes_ids_and_reports_added_removed_type_changed() -> None:
    current = {
        "AC-2": {"control_id": "AC-02", "gsa_ccc_control_type": "Common"},
        "AU-3": {"control_id": "AU-03", "gsa_ccc_control_type": "Common"},
        "IA-5": {"control_id": "IA-05", "gsa_ccc_control_type": "Hybrid"},
    }
    new = {
        "AC-2": {"control_id": "AC-2", "federal_type": "Common"},
        "AC-2(5)": {
            "control_id": "AC-02(005)",
            "federal_type": "Common",
            "contractor_type": "Hybrid",
            "name": "Account Management | Inactivity",
            "inheritance_source": "OCISO",
        },
        "AU-3": {"control_id": "AU-3", "federal_type": "Hybrid"},
    }

    rows = diff_ccc.diff(current, new)

    assert [(row["control_id"], row["delta"]) for row in rows] == [
        ("AC-2(5)", "ADDED"),
        ("IA-5", "REMOVED"),
        ("AU-3", "TYPE-CHANGED"),
    ]
    assert rows[0]["new_contractor_type"] == "Hybrid"


def test_diff_ccc_main_writes_stable_csv(tmp_path: Path) -> None:
    current = tmp_path / "current.yaml"
    new = tmp_path / "new.yaml"
    out = tmp_path / "diff.csv"
    current.write_text(
        yaml.safe_dump(
            {
                "controls": [
                    {"control_id": "AC-02", "gsa_ccc_control_type": "Common"},
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    new.write_text(
        yaml.safe_dump(
            {"controls": [{"control_id": "AC-2", "federal_type": "Common"}]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    assert (
        diff_ccc.main(["--current", str(current), "--new", str(new), "--out", str(out)])
        == 0
    )

    with out.open(newline="", encoding="utf-8") as fh:
        assert list(csv.DictReader(fh)) == []


def test_diff_ccc_reads_enhancement_ids_from_named_columns(tmp_path: Path) -> None:
    current = tmp_path / "current.yaml"
    new = tmp_path / "new.yaml"
    out = tmp_path / "diff.csv"
    current.write_text(
        yaml.safe_dump(
            {
                "controls": [
                    {
                        "control_id": "AC-2",
                        "core_id": "AC-02",
                        "gsa_ccc_control_type": "Hybrid",
                    },
                    {
                        "control_id": "AC-2",
                        "core_id": "AC-02(01)",
                        "gsa_ccc_control_type": "Hybrid",
                    },
                    {
                        "control_id": "CA-8",
                        "core_id": "-",
                        "service_id": "CA-08 (02)",
                        "gsa_ccc_control_type": "Common",
                    },
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    new.write_text(
        yaml.safe_dump(
            {"controls": [{"control_id": "AC-2(1)", "federal_type": "Common"}]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate canonical control ID"):
        diff_ccc.main(["--current", str(current), "--new", str(new), "--out", str(out)])

    args = ["--current", str(current), "--new", str(new), "--out", str(out)]
    args += ["--current-id-field", "core_id", "--current-id-field", "service_id"]
    assert diff_ccc.main(args) == 0

    with out.open(newline="", encoding="utf-8") as fh:
        rows = [(row["control_id"], row["delta"]) for row in csv.DictReader(fh)]
    assert rows == [
        ("AC-2", "REMOVED"),
        ("CA-8(2)", "REMOVED"),
        ("AC-2(1)", "TYPE-CHANGED"),
    ]
