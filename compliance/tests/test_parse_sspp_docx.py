"""Unit tests for the generic SSPP DOCX response extractor helpers."""

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


parse_sspp_docx = _load("parse_sspp_docx", SCRIPTS / "parse_sspp_docx.py")


def test_placeholder_and_inheritance_detection_respect_custom_markers() -> None:
    markers = ("custom system implementation:", "tenant application implementation:")
    assertions = ("please see the provider crm", "inherited from")

    assert parse_sspp_docx.is_placeholder(
        "Custom System Implementation: —", placeholder_markers=markers
    )
    assert not parse_sspp_docx.is_placeholder(
        "Custom System Implementation: Enforced via IAM policy.",
        placeholder_markers=markers,
    )
    assert parse_sspp_docx.is_inheritance_assertion(
        "Custom System Implementation: Please see the provider CRM.",
        placeholder_markers=markers,
        inheritance_assertions=assertions,
    )
    assert (
        parse_sspp_docx.default_status(
            "Custom System Implementation: Please see the provider CRM.",
            "planned",
            placeholder_markers=markers,
            inheritance_assertions=assertions,
        )
        == "inherited"
    )


def test_to_countermeasures_projects_parts_and_matrix_metadata() -> None:
    items = parse_sspp_docx.to_countermeasures(
        "AC-2(1)",
        {
            "implementation_status": "implemented",
            "origination": [],
            "responsible_role": "System Administrator",
            "implementation": {"a": "Automated provisioning via SCIM."},
        },
        {
            "control_id": "AC-02(01)",
            "control_name": "Automated System Account Management",
            "inheritance_type": "Hybrid",
            "providing_service": "Shared Identity Service",
        },
    )
    assert len(items) == 1
    assert items[0]["id"] == "cm-ac-2-1-a"
    assert items[0]["origination"] == ["hybrid"]
    assert items[0]["default_status"] == "implemented"
    assert items[0]["is_placeholder"] is False
