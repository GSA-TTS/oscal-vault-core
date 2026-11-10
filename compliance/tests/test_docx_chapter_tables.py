"""Tests for Chapters 1-12 native table source projection (synthetic data only)."""

import pytest
import yaml
from docx import Document

from compliance.lib.docx_chapter_tables import chapter_table_rows


def _system(tmp_path):
    root = tmp_path / "demo"
    (root / "model").mkdir(parents=True)
    (root / "system.yaml").write_text(
        yaml.safe_dump({"slug": "demo", "name": "Demo System (DEMO)"}), encoding="utf-8"
    )
    (root / "sidecar.yaml").write_text(
        yaml.safe_dump(
            {
                "sdr": {
                    "ports_and_protocols": [
                        {
                            "source": "client",
                            "destination": "api",
                            "port_number": 443,
                            "transport_protocol": "TCP",
                            "service_name": "HTTPS",
                            "encryption": "TLS 1.2",
                        },
                        {"source": "a", "destination": "b", "encryption": "none"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "model" / "architecture.yaml").write_text(
        yaml.safe_dump(
            {
                "asset_descriptions": {"api": "Public API", "db": "Datastore"},
                "user_roles": [{"role": "Admin", "authorized_privileges": "all"}],
            }
        ),
        encoding="utf-8",
    )
    (root / "docx.yaml").write_text(
        yaml.safe_dump(
            {
                "chapters": {
                    "document_prepared_by": {"organization_name": "Example Org"},
                    "revision_history": [{"version": "Draft"}],
                    "categorization": {
                        "information_types": [
                            {"name": {"value": "Logs"}, "status": "resolved"}
                        ]
                    },
                    "inventories": {"software": [{"name": "tool"}] * 3},
                }
            }
        ),
        encoding="utf-8",
    )
    return root


def test_rows_are_projected_from_the_system_directory(tmp_path):
    rows = chapter_table_rows(_system(tmp_path))

    assert rows["system_identity"] == [
        {"label": "Information System Name:", "value": "Demo System (DEMO)"},
        {"label": "Information System Abbreviation:", "value": "DEMO"},
    ]
    assert [row["asset_type"] for row in rows["system_assets"]] == ["api", "db"]
    assert rows["user_roles"][0]["sensitivity_detail"] == "all"
    assert rows["document_prepared_by"][0]["value"] == "Example Org"
    assert rows["revision_history"][0]["version"] == "Draft"
    assert rows["information_types"] == [{"name": "Logs"}]
    assert len(rows["software_inventory"]) == 3


def test_ports_rows_normalize_encryption_and_defaults(tmp_path):
    ports = chapter_table_rows(_system(tmp_path))["ports_protocols_services"]

    assert ports[0]["ports"] == "443 (T)"
    assert ports[0]["encrypted"] == "Y"
    assert ports[1]["encrypted"] == "N"
    assert ports[1]["direction"] == "Outbound"


def test_empty_sources_yield_empty_rows(tmp_path):
    root = tmp_path / "bare"
    (root / "model").mkdir(parents=True)
    for name in ("system.yaml", "sidecar.yaml", "docx.yaml", "model/architecture.yaml"):
        (root / name).write_text("{}\n", encoding="utf-8")

    rows = chapter_table_rows(root)

    assert rows["system_assets"] == []
    assert rows["receiving_systems"][0]["system"] == "Not applicable"
    assert rows["designated_contact_1"][0]["value"] == "Pending authoritative source"


def test_missing_source_file_fails_closed(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "system.yaml").write_text("name: Bare\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        chapter_table_rows(root)


def test_module_imports_with_python_docx_only():
    assert Document is not None
