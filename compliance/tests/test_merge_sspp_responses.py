"""Unit tests for the generic SSPP response-draft merger."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


merge_mod = _load("merge_sspp_responses", SCRIPTS / "merge_sspp_responses.py")


def test_null_part_response_merges_without_injecting_orphan(tmp_path: Path) -> None:
    countermeasures = tmp_path / "countermeasures.yaml"
    drafts = tmp_path / "responses.yaml"
    countermeasures.write_text(
        yaml.safe_dump(
            {
                "countermeasures": [
                    {
                        "id": "cm-ac-6-2",
                        "control_id": "AC-06(02)",
                        "part": None,
                        "description": "Existing placeholder",
                        "default_status": "gap",
                        "is_placeholder": True,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    drafts.write_text(
        yaml.safe_dump(
            {
                "responses": {
                    "AC-6(2)": [
                        {
                            "part": None,
                            "implementation": "Merged response prose.",
                        }
                    ]
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = merge_mod.merge(countermeasures, drafts)
    merged = yaml.safe_load(countermeasures.read_text(encoding="utf-8"))

    assert result["matched"] == ["AC-6(2)::-"]
    assert result["injected"] == []
    assert len(merged["countermeasures"]) == 1
    assert merged["countermeasures"][0]["is_placeholder"] is False
    assert merged["countermeasures"][0]["default_status"] == "planned"
    assert "Merged response prose." in merged["countermeasures"][0]["description"]


def test_mismatched_part_response_injects_orphan_countermeasure(tmp_path: Path) -> None:
    countermeasures = tmp_path / "countermeasures.yaml"
    drafts = tmp_path / "responses.yaml"
    countermeasures.write_text(
        yaml.safe_dump(
            {
                "countermeasures": [
                    {
                        "id": "cm-ac-6-2",
                        "control_id": "AC-06(02)",
                        "part": None,
                        "description": "Existing placeholder",
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    drafts.write_text(
        yaml.safe_dump(
            {
                "responses": {
                    "AC-6(2)": [
                        {
                            "part": "a",
                            "implementation": "Wrong-part response prose.",
                        }
                    ]
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = merge_mod.merge(countermeasures, drafts)
    merged = yaml.safe_load(countermeasures.read_text(encoding="utf-8"))

    assert result["matched"] == []
    assert result["injected"] == ["AC-6(2)::a"]
    assert len(merged["countermeasures"]) == 2
    assert merged["countermeasures"][1]["injected_by_merger"] is True
    assert merged["countermeasures"][1]["part"] == "a"


def test_merge_canonicalizes_existing_control_id(tmp_path: Path) -> None:
    countermeasures = tmp_path / "countermeasures.yaml"
    drafts = tmp_path / "responses.yaml"
    countermeasures.write_text(
        yaml.safe_dump({"countermeasures": [{"control_id": "AC-02(01)", "part": "a"}]}),
        encoding="utf-8",
    )
    drafts.write_text(
        yaml.safe_dump(
            {"responses": {"AC-2(1)": [{"part": "a", "implementation": "Response."}]}}
        ),
        encoding="utf-8",
    )

    merge_mod.merge(countermeasures, drafts)
    merged = yaml.safe_load(countermeasures.read_text(encoding="utf-8"))
    assert merged["countermeasures"][0]["control_id"] == "AC-2(1)"
