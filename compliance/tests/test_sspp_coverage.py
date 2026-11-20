"""Unit tests for the generic SSPP-to-CycloneDX coverage and gap helpers."""

from __future__ import annotations

import importlib.util
import json
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


cov = _load("sspp_coverage", SCRIPTS / "sspp_coverage.py")


def test_coverage_uses_canonical_enhancement_identity(tmp_path: Path) -> None:
    cdx = tmp_path / "model.cdx.json"
    cdx.write_text(
        json.dumps(
            {
                "threats": {
                    "threats": [
                        {
                            "bom-ref": "threat-ac-2-1",
                            "properties": [{"name": "ctrl:id", "value": "AC-02.01"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    countermeasures = tmp_path / "countermeasures.yaml"
    countermeasures.write_text(
        yaml.safe_dump({"countermeasures": [{"control_id": "ac-2(1)", "part": "a"}]}),
        encoding="utf-8",
    )

    assert cov.collect_cdx_controls(cdx) == {"AC-2(1)": ["threat-ac-2-1"]}
    assert list(cov.load_sspp(countermeasures)) == ["AC-2(1)"]
