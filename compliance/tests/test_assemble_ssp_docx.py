"""Unit tests for the generic SSP DOCX assembly and fidelity helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


assemble = _load("assemble_ssp_docx", SCRIPTS / "assemble_ssp_docx.py")


def test_compute_fidelity_handles_populated_and_empty_inputs() -> None:
    manifest = {
        "matching_control_ids_filled": 8,
        "source_controls": 10,
        "controls": [
            {
                "statements": [
                    {"written": True},
                    {"written": True},
                    {"written": False},
                    {"written": True},
                ]
            }
        ],
    }
    chapter_manifest = {"written_rows": 5, "source_rows": 5}
    anchored_results = [{"matched": True}, {"matched": False}]

    fidelity = assemble.compute_fidelity(manifest, chapter_manifest, anchored_results)
    assert fidelity == {
        "control_identity": 0.8,
        "statement_placement": 0.75,
        "chapter_rows": 1.0,
        "anchored_content": 0.5,
    }


def test_compute_fidelity_defaults_to_unity_when_counts_are_zero() -> None:
    fidelity = assemble.compute_fidelity(
        {"matching_control_ids_filled": 0, "source_controls": 0, "controls": []},
        {"written_rows": 0, "source_rows": 0},
        [],
    )
    assert fidelity == {
        "control_identity": 1.0,
        "statement_placement": 1.0,
        "chapter_rows": 1.0,
        "anchored_content": 1.0,
    }
