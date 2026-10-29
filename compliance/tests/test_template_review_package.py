"""Tests for the local-only template review package builder."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from docx import Document


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "compliance" / "scripts" / "build_template_review_package.py"
_SPEC = importlib.util.spec_from_file_location("template_review_package", _SCRIPT)
template_review_package = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(template_review_package)

_SMOKE = (
    _REPO_ROOT / "compliance" / "scripts" / "smoke_test_template_review_packages.py"
)


def _cis_table(doc, control_id: str) -> None:
    """A minimal control-summary table in the official templates' row layout."""
    table = doc.add_table(rows=7, cols=2)
    table.rows[0].cells[0].text = control_id
    table.rows[0].cells[1].text = "Control Summary Information"
    status = table.rows[1].cells[0].merge(table.rows[1].cells[-1])
    status.text = "Implementation Status:"
    for label in ("Implemented", "Partially Implemented", "Planned", "Not applicable"):
        status.add_paragraph(f"\u2610 {label}")
    origin = table.rows[2].cells[0].merge(table.rows[2].cells[-1])
    origin.text = "Control Origination:"
    for label in ("Inherited from: [Enter System's Name]", "System Specific Control"):
        origin.add_paragraph(f"\u2610 {label}")
    describe = table.rows[3].cells[0].merge(table.rows[3].cells[-1])
    describe.text = f"{control_id} Describe how the control is implemented."
    for row, part in zip((4, 5, 6), "abc", strict=True):
        table.rows[row].cells[0].text = f"Part {part}"


def test_review_package_is_explicit_about_coverage(tmp_path):
    """Source SSP with AC-1, AC-2 and a supplemental RA-3(1); target template with AC-1, AC-2, AU-1.

    The package must fill the tables that match, leave the target-only control unfilled, and list
    the source-only control as supplemental rather than dropping it.
    """
    source, template = tmp_path / "source.docx", tmp_path / "template.docx"
    for path, ids in (
        (source, ("AC-1", "AC-2", "RA-3(1)")),
        (template, ("AC-1", "AC-2", "AU-1")),
    ):
        doc = Document()
        doc.add_heading("Control Implementation", level=1)
        for control_id in ids:
            _cis_table(doc, control_id)
        doc.save(path)

    output, manifest_path = tmp_path / "review.docx", tmp_path / "review.manifest.json"
    manifest = template_review_package.build_review_package(
        source, template, output, manifest_path
    )

    assert output.is_file() and manifest_path.is_file()
    assert "not an authorization" in manifest["purpose"].lower()
    assert manifest["target_control_tables"] == 3
    assert manifest["matching_control_tables_filled"] >= 1
    assert manifest["target_controls_left_unfilled"]
    text = "\n".join(p.text for p in Document(output).paragraphs)
    assert "Supplemental Source-Only Controls" in text


def _load_smoke():
    spec = importlib.util.spec_from_file_location("template_review_smoke", _SMOKE)
    smoke = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(smoke)
    return smoke


def _registry(tmp_path: Path, systems: list[dict]) -> Path:
    import yaml

    path = tmp_path / "scripts" / "registry.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"systems": systems}), encoding="utf-8")
    return path


def test_smoke_test_registry_enumerates_sources_from_the_workspace(
    tmp_path, monkeypatch
):
    smoke = _load_smoke()
    monkeypatch.setattr(smoke, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(
        smoke,
        "_REGISTRY",
        _registry(
            tmp_path,
            [
                {"plan_id": "alpha", "docx": "fismatic/alpha.docx"},
                {"plan_id": "beta", "docx": "fismatic/beta.docx"},
                {"plan_id": "no-source"},
            ],
        ),
    )
    sources, duplicates = smoke._registry_sources()
    assert [slug for slug, _ in sources] == ["alpha", "beta"]
    assert all(source.parent == tmp_path / "fismatic" for _slug, source in sources)
    assert duplicates == []


def test_registry_duplicate_plan_ids_are_reported(tmp_path, monkeypatch):
    """A duplicate plan_id silently overwrites local review artifacts."""
    smoke = _load_smoke()
    monkeypatch.setattr(smoke, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(
        smoke,
        "_REGISTRY",
        _registry(
            tmp_path,
            [
                {"plan_id": "alpha", "docx": "fismatic/a1.docx"},
                {"plan_id": "alpha", "docx": "fismatic/a2.docx"},
            ],
        ),
    )
    sources, duplicates = smoke._registry_sources()
    assert duplicates == ["alpha"] and len(sources) == 1
