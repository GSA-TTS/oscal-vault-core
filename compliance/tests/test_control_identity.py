"""Regression tests for cross-adapter NIST control identity."""

from __future__ import annotations

import pytest

from compliance.lib.control_identity import (
    ControlKey,
    canonical_control_id,
    parse_control_id,
    reject_duplicate_control_ids,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AC-2", "AC-2"),
        ("ac-02", "AC-2"),
        ("AC-2(01)", "AC-2(1)"),
        ("ac-2.1", "AC-2(1)"),
        (" IA - 5 ( 07 ) ", "IA-5(7)"),
    ],
)
def test_control_ids_have_one_canonical_representation(raw: str, expected: str):
    assert canonical_control_id(raw) == expected


def test_control_key_is_typed_and_enhancement_stays_distinct():
    assert parse_control_id("AC-2") == ControlKey("AC", 2)
    assert parse_control_id("AC-2(1)") == ControlKey("AC", 2, "1")
    assert parse_control_id("AC-2") != parse_control_id("AC-2(1)")


@pytest.mark.parametrize("raw", ["AC", "AC-", "AC-2(1).2", "AC-2(foo-bar)"])
def test_invalid_control_ids_fail_closed(raw: str):
    with pytest.raises(ValueError, match="invalid NIST control ID"):
        parse_control_id(raw)


def test_duplicate_ids_are_rejected_after_normalization():
    with pytest.raises(ValueError, match=r"duplicate canonical control ID 'AC-2\(1\)'"):
        reject_duplicate_control_ids(
            [{"control_id": "AC-2(1)"}, {"control_id": "ac-02.01"}]
        )
