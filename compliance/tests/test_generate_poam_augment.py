"""Unit tests for the generic OSCAL POA&M-to-CycloneDX control annotation helper."""

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


poam_aug = _load("generate_poam_augment", SCRIPTS / "generate_poam_augment.py")


def test_extract_poam_patches_from_oscal_items() -> None:
    poam = {
        "plan-of-action-and-milestones": {
            "poam-items": [
                {
                    "uuid": "item-1",
                    "title": "Complete audit review automation",
                    "props": [{"name": "nist:control-id", "value": "AU-6"}],
                    "milestones": [
                        {"scheduled-completion-date": "2026-03-01"},
                        {"scheduled-completion-date": "2026-04-01"},
                    ],
                }
            ]
        }
    }

    patches = poam_aug.extract_poam_patches(poam)

    assert len(patches) == 1
    assert patches[0].control_id == "AU-6"
    assert patches[0].poam_id == "item-1"
    assert patches[0].scheduled_completion == "2026-04-01"
    assert patches[0].title == "Complete audit review automation"


def test_apply_poam_patches_annotates_existing_control_without_new_threats() -> None:
    cdx = {
        "controls": [
            {
                "bom-ref": "control-au-6",
                "name": "Audit Review",
                "properties": [{"name": "ctrl:id", "value": "AU-6"}],
            }
        ],
        "threats": [{"bom-ref": "threat-existing"}],
    }
    patches = poam_aug.extract_poam_patches(
        {
            "poam-items": [
                {
                    "id": "POAM-1",
                    "title": "Finish review workflow",
                    "control-id": "AU-6",
                    "scheduled-completion-date": "2026-03-01",
                }
            ]
        }
    )

    patched, summary = poam_aug.apply_poam_patches(cdx, patches)

    props = {p["name"]: p["value"] for p in patched["controls"][0]["properties"]}
    assert props["poam:id"] == "POAM-1"
    assert props["poam:source"] == "oscal-poam"
    assert props["poam:due-date"] == "2026-03-01"
    assert props["poam:title"] == "Finish review workflow"
    assert patched["threats"] == [{"bom-ref": "threat-existing"}]
    assert summary == [{"control_id": "AU-6", "poam_id": "POAM-1", "status": "patched"}]
