import pytest
from ..control import Control


@pytest.mark.parametrize(
    "test_input,expected",
    [("AC-2", "AC-2"), ("AC-2 (1)", "AC-2 (1)"), ("AC-2(1)", "AC-2 (1)")],
)
def test_normalized_name(test_input, expected):
    control = Control(test_input)
    assert control.normalized_name() == expected


@pytest.mark.parametrize(
    "narrative",
    [
        "See AC-06(01) for the verified detail.",
        "Access is constrained (see AC-02(13)).",
        "This control AC-02 reference is intentional.",
        "Cross-family references: see AU-02, Control IA-05(01), and see SC-08(01) for details.",
    ],
)
def test_implementation_preserves_inline_control_cross_references(narrative):
    control = Control("AC-2")
    control.implementation = {"a": narrative}
    assert control.implementation["a"] == narrative
