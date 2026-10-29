import json
from pathlib import Path

from compliance.scripts.build_reviewer_report import build_report, main, render_html


def test_report_exposes_tables_and_instruction_provenance(tmp_path: Path):
    vault = tmp_path / "example-vault"
    sidecar = vault / ".snapshots" / "current" / "Components" / "app.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(
        json.dumps(
            {
                "_meta": {"generated_from": "Components/app.md"},
                "type": "oscal.component_definition",
                "title": "App",
                "table_records": [
                    {
                        "classification": "record_set",
                        "kind": "software_inventory",
                        "disposition": "mapped",
                    }
                ],
                "instruction_sources": [
                    {
                        "source_locator": "word/document.xml#body/p[1]",
                        "source_hash": "a" * 64,
                    }
                ],
                "typed_references": [
                    {"relation": "scoped_to", "type": "oscal.ssp", "id": "system-1"}
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_report(vault)
    assert report["summary"]["tables"] == 1
    assert report["summary"]["instructions"] == 1
    assert report["summary"]["references"] == 1
    assert report["summary"]["kind:software_inventory"] == 1
    rendered = render_html(report)
    assert "Components/app.md" in rendered
    assert "word/document.xml#body/p[1]" in rendered
    assert "scoped_to: oscal.ssp system-1" in rendered


def test_cli_writes_deterministic_json_and_html(tmp_path: Path, monkeypatch):
    vault = tmp_path / "empty"
    (vault / ".snapshots" / "current").mkdir(parents=True)
    json_path = tmp_path / "report.json"
    html_path = tmp_path / "report.html"
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_reviewer_report.py",
            "--vault",
            str(vault),
            "--json",
            str(json_path),
            "--html",
            str(html_path),
        ],
    )

    assert main() == 0
    first_json = json_path.read_text(encoding="utf-8")
    first_html = html_path.read_text(encoding="utf-8")
    assert main() == 0
    assert json_path.read_text(encoding="utf-8") == first_json
    assert html_path.read_text(encoding="utf-8") == first_html
