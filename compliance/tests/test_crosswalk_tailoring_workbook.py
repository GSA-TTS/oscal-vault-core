"""Unit tests for the generic Control Tailoring Workbook crosswalk helper."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


cw = _load("crosswalk_tailoring_workbook", SCRIPTS / "crosswalk_tailoring_workbook.py")


def test_parse_control_tailoring_and_sspp_rows_normalize_ids(tmp_path: Path) -> None:
    ct_rows = [
        ["Title"],
        ["Header"],
        [
            "AC-02(01)",
            "Automated System Account Management",
            "X",
            "",
            "Hybrid",
            "Shared Identity",
            "Applicable",
            "X",
            "Notes",
        ],
    ]
    sspp_rows = [
        ["Header"],
        [
            "AC-02(01)",
            "Automated System Account Management",
            "X",
            "Hybrid",
            "Shared Identity",
        ],
    ]

    ct = cw.parse_control_tailoring(tmp_path / "dummy.ods", rows=ct_rows)
    sspp = cw.parse_sspp(tmp_path / "dummy.ods", rows=sspp_rows)

    assert "AC-2(1)" in ct
    assert ct["AC-2(1)"]["moderate"] is True
    assert ct["AC-2(1)"]["low"] is False
    assert ct["AC-2(1)"]["ebta"] == "X"
    assert "AC-2(1)" in sspp
    assert sspp["AC-2(1)"]["inheritance"] == "Hybrid"
