"""Tests for control-parameter (ODP) extraction and OSCAL set-parameters (#18)."""

from __future__ import annotations

import os

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.control_parameters import (  # noqa: E402
    ControlParameter,
    _is_placeholder,
    extract_parameters,
)
from compliance.lib.export_oscal import _oscal_set_parameters  # noqa: E402

# A real FedRAMP Low SSP supplied by the environment; tests using it skip when it is not set.
_SAMPLE_DOCX = Path(
    os.environ.get("OSCAL_VAULT_SAMPLE_LOW_SSP", "/nonexistent/sample-low-ssp.docx")
)


class TestParamId:
    def test_synthetic_param_id_is_stable_token(self):
        p = ControlParameter(control_id="AC-02", value="14 days", ordinal=6)
        assert p.param_id == "ac-02_odp.06"

    def test_enhancement_param_id(self):
        p = ControlParameter(control_id="IA-02(01)", value="x", ordinal=1)
        assert p.param_id == "ia-02.01_odp.01"


class TestPlaceholderFilter:
    @pytest.mark.parametrize(
        "value",
        ["Enter System's Name", "ACRONYM", "Name", "Assignment", "insert date", "]"],
    )
    def test_placeholders_rejected(self, value):
        assert _is_placeholder(value)

    @pytest.mark.parametrize(
        "value",
        ["14 days", "annually", "CISO", "at least every three (3) years"],
    )
    def test_real_values_kept(self, value):
        assert not _is_placeholder(value)


class TestSetParametersExport:
    def test_frontmatter_shape_to_oscal(self):
        raw = [
            {"param_id": "ac-02_odp.01", "value": "14 days"},
            {"param_id": "ac-02_odp.02", "value": "annually"},
        ]
        out = _oscal_set_parameters(raw)
        assert out == [
            {"param-id": "ac-02_odp.01", "values": ["14 days"]},
            {"param-id": "ac-02_odp.02", "values": ["annually"]},
        ]

    def test_skips_incomplete_entries(self):
        raw = [
            {"param_id": "", "value": "x"},
            {"param_id": "ac-1_odp.01", "value": ""},
            {"param_id": "ac-1_odp.02", "value": "keep"},
        ]
        out = _oscal_set_parameters(raw)
        assert out == [{"param-id": "ac-1_odp.02", "values": ["keep"]}]

    def test_none_and_non_list_safe(self):
        assert _oscal_set_parameters(None) == []
        assert _oscal_set_parameters("nope") == []


@pytest.mark.skipif(not _SAMPLE_DOCX.exists(), reason="sample SSP .docx not present")
class TestExtractFromRealDocx:
    def test_recovers_parameters(self):
        result = extract_parameters(_SAMPLE_DOCX)
        # The Low SSP has ODPs on the majority of controls.
        assert len(result.by_control) > 50
        assert result.total() > 100

    def test_ac02_parameters_present_and_ordered(self):
        result = extract_parameters(_SAMPLE_DOCX)
        ac02 = result.for_control("AC-02")
        assert ac02, "expected AC-02 to have recovered parameters"
        # ordinals are 1-based and monotonic
        assert [p.ordinal for p in ac02] == list(range(1, len(ac02) + 1))
        # a known value from the template appears
        assert any("14 days" in p.value for p in ac02)

    def test_no_placeholder_noise(self):
        result = extract_parameters(_SAMPLE_DOCX)
        for params in result.by_control.values():
            for p in params:
                assert not _is_placeholder(p.value)


class TestBuildVaultWithParameters:
    """Parameters flow through build_vault into the control note + frontmatter."""

    def test_control_note_renders_parameters(self):
        import json

        from compliance.lib.control_parameters import ControlParameter
        from compliance.lib.vault import build_vault

        ssp_path = (
            _REPO_ROOT
            / "compliance"
            / "tests"
            / "fixtures"
            / "oscal"
            / "ssp-source.example.json"
        )
        if not ssp_path.exists():
            pytest.skip("example SSP source missing")
        ssp = json.loads(ssp_path.read_text())
        params = {
            "AC-01": [
                ControlParameter("AC-01", "annually", 1),
                ControlParameter("AC-01", "the CISO", 2),
            ]
        }
        notes = build_vault(
            ssp,
            plan_id="sample-system-ssp-2024",
            parser_name="fismatic",
            control_parameters=params,
        )
        ac1 = next(n for n in notes if n.relpath == "Controls/AC-01.md")
        # frontmatter carries structured params
        assert ac1.frontmatter.get("parameters") == [
            {"param_id": "ac-01_odp.01", "value": "annually"},
            {"param_id": "ac-01_odp.02", "value": "the CISO"},
        ]
        # The body renders the canonical frontmatter data through Dataview;
        # parameter values are not duplicated as literal Markdown rows.
        assert (
            'TABLE parameter.param_id AS "Parameter", parameter.value AS "Value"'
            in ac1.body
        )
        assert "FROM [[]]" in ac1.body
        assert "FLATTEN parameters AS parameter" in ac1.body
        assert "| `ac-01_odp.01` | annually" not in ac1.body

    def test_control_without_parameters_omits_empty_view(self):
        import json

        from compliance.lib.vault import build_vault

        ssp_path = (
            _REPO_ROOT
            / "compliance"
            / "tests"
            / "fixtures"
            / "oscal"
            / "ssp-source.example.json"
        )
        if not ssp_path.exists():
            pytest.skip("example SSP source missing")
        ssp = json.loads(ssp_path.read_text())
        notes = build_vault(ssp, plan_id="s", parser_name="fismatic")
        ac1 = next(n for n in notes if n.relpath == "Controls/AC-01.md")
        assert "parameters" not in ac1.frontmatter
        assert "# Parameters" not in ac1.body
        assert "FLATTEN parameters AS parameter" not in ac1.body
