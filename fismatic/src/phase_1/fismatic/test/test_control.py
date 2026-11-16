import pytest
from ..control import Control


@pytest.mark.parametrize(
    "test_input,expected",
    [("AC-2", "AC-2"), ("AC-2 (1)", "AC-2 (1)"), ("AC-2(1)", "AC-2 (1)")],
)
def test_normalized_name(test_input, expected):
    control = Control(test_input)
    assert control.normalized_name() == expected


def test_implementation_preserves_inline_control_cross_references():
    control = Control("AC-2")
    control.implementation = {
        "a": "Session revocation is enforced upstream (see AC-02(13)). See AC-06(01) for control AC-2 detail."
    }
    assert (
        control.implementation["a"]
        == "Session revocation is enforced upstream (see AC-02(13)). See AC-06(01) for control AC-2 detail."
    )
