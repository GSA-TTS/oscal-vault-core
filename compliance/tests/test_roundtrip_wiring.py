"""Fast functional-equivalent for the docx fill-back roundtrip (R16).

The full jinjafy -> fill -> re-extract -> parity cycle against real .docx
templates is expensive (docxtpl render + XML parse) and is now gated behind
``@pytest.mark.slow`` in ``test_docx_jinjafier.py`` / ``test_docx_fill_roundtrip.py``.

These tests are the lightweight standard-suite guarantee: they prove the
roundtrip *plumbing is present, correctly shaped, and internally consistent*
WITHOUT rendering or parsing any Word document:

  1. API presence — every public roundtrip function exists with the expected
     call signature (a rename/removal breaks this immediately).
  2. Contract wiring — every committed roundtrip contract parses, and every
     property that declares a ``templateTag`` also declares the write metadata
     (writeStrategy + instructionAnchor + writeScope) the fill pipeline needs.
  3. Fixture coverage — every ``templateTag`` in a contract has a matching key
     in that contract's sample fill fixture (and vice-versa), so a real
     roundtrip run would have content for every slot and no orphan keys.

If these pass, the slow end-to-end parity tests are exercising a pipeline that
is wired correctly; if the wiring drifts, these fail in milliseconds instead of
only surfacing in the gated slow lane.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from compliance.lib import docx_fill, docx_jinjafier
from compliance.lib.heading_parser import load_contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DC = _REPO_ROOT / "compliance" / "datacontracts"
_FIX = _REPO_ROOT / "compliance" / "tests" / "fixtures" / "roundtrip"

# (contract file, sample fixture file) for every committed roundtrip template.
_ROUNDTRIP_PAIRS = [
    ("low-sspp-roundtrip.yaml", "low-sspp.sample.json"),
    ("moderate-sspp-roundtrip.yaml", "moderate-sspp.sample.json"),
    ("high-sspp-roundtrip.yaml", "high-sspp.sample.json"),
    ("leveraged-saas-crm-roundtrip.yaml", "leveraged-saas-crm.sample.json"),
]


def _writeback_tags(contract) -> List[Tuple[str, object]]:
    """Return (template_tag, property) for every destination property that
    declares a templateTag."""
    out = []
    for obj in contract.destinations():
        for prop in obj.properties:
            if prop.template_tag:
                out.append((prop.template_tag, prop))
    return out


def _fixture_keys(fixture_path: Path) -> List[str]:
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    return sorted(k for k in data if not k.startswith("_"))


# ---------------------------------------------------------------------------
# 1. API presence
# ---------------------------------------------------------------------------


class TestRoundtripApiPresent:
    """The public roundtrip surface exists with the expected signatures.

    This is the cheap tripwire: renaming/removing a function or changing its
    parameters fails here without any docx work.
    """

    def test_docx_fill_exports(self):
        for name in (
            "load_fill_context",
            "fill_template",
            "reextract_filled",
            "check_parity",
            "roundtrip",
        ):
            assert hasattr(docx_fill, name), f"docx_fill.{name} missing"
            assert callable(getattr(docx_fill, name))

    def test_docx_jinjafier_exports(self):
        assert callable(getattr(docx_jinjafier, "jinjafy", None))
        assert callable(getattr(docx_jinjafier, "resolve_bindings", None))

    def test_roundtrip_signature(self):
        params = list(inspect.signature(docx_fill.roundtrip).parameters)
        assert params[:4] == [
            "jinja_docx",
            "fixture_path",
            "contract",
            "output_path",
        ]

    def test_fill_template_signature(self):
        params = list(inspect.signature(docx_fill.fill_template).parameters)
        assert params[:3] == ["jinja_docx", "context", "output_path"]
        assert "contract" in params

    def test_jinjafy_signature(self):
        params = list(inspect.signature(docx_jinjafier.jinjafy).parameters)
        # jinjafy(source_docx, contract, output_path, ...)
        assert len(params) >= 3

    def test_parity_result_shape(self):
        result = docx_fill.ParityResult()
        assert hasattr(result, "ok")
        assert hasattr(result, "recovery_rate")
        assert result.ok is True  # empty result is trivially ok
        assert result.recovery_rate == 1.0


# ---------------------------------------------------------------------------
# 2. check_parity behavior (pure, no docx)
# ---------------------------------------------------------------------------


class TestCheckParityLogic:
    """check_parity is the pure heart of the roundtrip; exercise it directly."""

    def test_exact_match(self):
        r = docx_fill.check_parity({"a": "hello world"}, {"a": "hello world"})
        assert r.ok
        assert r.recovery_rate == 1.0

    def test_substring_match_for_append_scope(self):
        # append writeScope keeps the template sentence + appended value.
        r = docx_fill.check_parity(
            {"a": "appended value"},
            {"a": "Original instruction sentence. appended value"},
        )
        assert r.ok

    def test_missing_tag(self):
        r = docx_fill.check_parity({"a": "v"}, {})
        assert r.missing == ["a"]
        assert not r.ok

    def test_mismatch(self):
        r = docx_fill.check_parity({"a": "expected"}, {"a": "totally other"})
        assert "a" in r.mismatched
        assert not r.ok

    def test_load_fill_context_drops_meta(self, tmp_path):
        f = tmp_path / "f.json"
        f.write_text('{"_meta": {"x": 1}, "a": "b"}', encoding="utf-8")
        assert docx_fill.load_fill_context(f) == {"a": "b"}


# ---------------------------------------------------------------------------
# 3. Contract wiring + fixture coverage (parse only, no docx)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("contract_file,fixture_file", _ROUNDTRIP_PAIRS)
class TestContractWiring:
    def test_contract_parses_and_has_writeback_tags(self, contract_file, fixture_file):
        contract = load_contract(_DC / contract_file)
        tags = _writeback_tags(contract)
        assert tags, f"{contract_file} declares no templateTag write-back slots"

    def test_every_tag_has_write_metadata(self, contract_file, fixture_file):
        """A templateTag without writeStrategy/instructionAnchor/writeScope would
        silently fail to jinjafy/fill — assert the pipeline inputs are complete.
        """
        contract = load_contract(_DC / contract_file)
        problems: Dict[str, List[str]] = {}
        for tag, prop in _writeback_tags(contract):
            missing = []
            if not prop.write_strategy:
                missing.append("writeStrategy")
            if not prop.instruction_anchor:
                missing.append("instructionAnchor")
            if not prop.write_scope:
                missing.append("writeScope")
            if missing:
                problems[tag] = missing
        assert not problems, f"{contract_file} incomplete write metadata: {problems}"

    def test_fixture_matches_contract_tags(self, contract_file, fixture_file):
        """Every contract templateTag has a fixture key and vice-versa, so a
        real roundtrip would have content for every slot and no orphan keys.
        """
        contract = load_contract(_DC / contract_file)
        contract_tags = {tag for tag, _ in _writeback_tags(contract)}
        fixture_tags = set(_fixture_keys(_FIX / fixture_file))
        assert contract_tags == fixture_tags, (
            f"{contract_file} tags {sorted(contract_tags)} != "
            f"{fixture_file} keys {sorted(fixture_tags)}"
        )
