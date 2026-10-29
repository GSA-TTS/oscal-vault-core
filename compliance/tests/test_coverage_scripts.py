import importlib
import sys

import pytest
import yaml
from docx import Document

vault_cov = importlib.import_module("compliance.scripts.vault_coverage_analysis")
gap = importlib.import_module("compliance.scripts.coverage_gap_analysis")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A system-repo-shaped directory: registry, one source .docx and one generated vault."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src").mkdir()
    doc = Document()
    doc.add_heading("System Description", level=1)
    doc.add_paragraph("alpha beta gamma delta epsilon")
    doc.save(tmp_path / "src" / "demo.docx")
    (tmp_path / "scripts" / "registry.yaml").write_text(
        yaml.safe_dump(
            {
                "systems": [
                    {"plan_id": "demo", "docx": "src/demo.docx"},
                    {"plan_id": "no-source"},
                ]
            }
        ),
        encoding="utf-8",
    )
    vault = tmp_path / "examples" / "vault" / "demo"
    (vault / "Systems").mkdir(parents=True)
    (vault / "Systems" / "demo.md").write_text(
        "---\ntype: oscal.system\n---\n\nalpha beta gamma\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    for module in (vault_cov, gap):
        monkeypatch.setattr(module, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(vault_cov, "_VAULT_DIR", tmp_path / "examples" / "vault")
    return tmp_path


def test_registry_maps_plan_ids_to_workspace_relative_docx(workspace):
    for module in (vault_cov, gap):
        mapping = module.load_vault_to_docx(workspace / "scripts" / "registry.yaml")
        assert mapping == {"demo": workspace / "src" / "demo.docx"}


def test_vault_coverage_reports_a_ratio_for_a_registry_system(workspace, capsys):
    assert (
        vault_cov.main(["--registry", str(workspace / "scripts" / "registry.yaml")])
        == 0
    )
    out = capsys.readouterr().out
    assert "demo" in out and "TOTALS" in out


def test_gap_analysis_runs_against_the_registry(workspace, capsys, monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["x", "--registry", str(workspace / "scripts" / "registry.yaml")]
    )
    assert gap.main() == 0
    assert "demo" in capsys.readouterr().out


def test_vault_coverage_missing_registry_is_a_clear_error(tmp_path, capsys):
    assert vault_cov.main(["--registry", str(tmp_path / "nope.yaml")]) == 2
    assert "registry not found" in capsys.readouterr().err


def test_gap_analysis_missing_registry_is_a_clear_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["x", "--registry", str(tmp_path / "nope.yaml")])
    assert gap.main() == 2
    assert "registry not found" in capsys.readouterr().err


def test_prose_delta_reads_systems_and_levels_from_the_registry(workspace, capsys):
    from compliance.scripts import prose_delta_analysis as prose

    mapping = prose.load_actual_ssps(workspace / "scripts" / "registry.yaml")
    assert mapping == {"src/demo.docx": ""}


def test_prose_delta_missing_registry_is_a_clear_error(tmp_path, capsys):
    from compliance.scripts import prose_delta_analysis as prose

    assert prose.main(["--registry", str(tmp_path / "nope.yaml")]) == 2
    assert "registry not found" in capsys.readouterr().err


def test_prose_delta_runs_end_to_end_on_a_synthetic_system(
    workspace, monkeypatch, capsys
):
    from compliance.scripts import prose_delta_analysis as prose

    monkeypatch.setattr(prose, "_WORKSPACE", workspace)
    assert prose.main(["--registry", str(workspace / "scripts" / "registry.yaml")]) == 0
    out = capsys.readouterr()
    assert "ACTUAL SSPs" in out.out and "demo.docx" in out.out


def test_convert_all_reads_the_registry_and_skips_what_is_missing(workspace, capsys):
    from compliance.scripts import convert_all_unified as conv

    registry = workspace / "scripts" / "registry.yaml"
    assert [s["plan_id"] for s in conv.load_systems(registry)] == ["demo"]
    # no contract exists for "demo", so it is skipped rather than crashing
    assert (
        conv.main(["--registry", str(registry), "--contracts-dir", str(workspace)]) == 0
    )
    out = capsys.readouterr().out
    assert "[SKIP] demo: contract not found" in out and "Skipped:   1" in out


def test_convert_all_missing_registry_is_a_clear_error(tmp_path, capsys):
    from compliance.scripts import convert_all_unified as conv

    assert conv.main(["--registry", str(tmp_path / "nope.yaml")]) == 2
    assert "registry not found" in capsys.readouterr().err
