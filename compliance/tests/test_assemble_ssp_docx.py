"""Unit tests for the generic SSP DOCX assembly and fidelity helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

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


def test_manifest_writer_makes_repository_paths_portable(tmp_path: Path) -> None:
    rendering = _load("docx_rendering", SCRIPTS.parent / "lib" / "docx_rendering.py")
    manifest = tmp_path / "manifest.json"
    rendering.write_manifest(
        manifest,
        {"diagram": str(tmp_path / "build" / "diagram.png")},
        repository_root=tmp_path,
    )
    assert json.loads(manifest.read_text()) == {"diagram": "build/diagram.png"}


def test_narrative_formatting_accepts_consumer_supplied_labels(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    rendering = _load(
        "docx_rendering_with_labels", SCRIPTS.parent / "lib" / "docx_rendering.py"
    )
    output = tmp_path / "ssp.docx"
    document = docx.Document()
    table = document.add_table(rows=2, cols=1)
    table.cell(0, 0).text = "AC-02"
    table.cell(1, 0).text = "Consumer Layer: Use **formatted** prose."
    document.save(output)

    result = rendering.format_rendered_narratives(
        output,
        implementation_layer_labels=["Consumer Layer:"],
    )

    assert result["formatted_cells"] == 1
    assert not any(result["audit"].values())


def test_narrative_formatting_can_audit_all_tables(tmp_path: Path, monkeypatch) -> None:
    docx = pytest.importorskip("docx")
    rendering = _load(
        "docx_rendering_with_all_tables", SCRIPTS.parent / "lib" / "docx_rendering.py"
    )
    output = tmp_path / "ssp.docx"
    document = docx.Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Encryption: **required**"
    document.save(output)
    monkeypatch.setattr(rendering.docx_narrative_format, "format_document", lambda *_: 0)

    result = rendering.format_rendered_narratives(
        output,
        implementation_layer_labels=[],
        audit_tables="all",
    )

    assert result["audit"]["literal_bold"] == 1
