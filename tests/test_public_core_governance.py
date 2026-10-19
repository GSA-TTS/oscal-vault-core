from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_core_governance_baseline_is_present():
    assert (
        (ROOT / "LICENSE.md")
        .read_text()
        .startswith("# CC0 1.0 Universal Public Domain Dedication")
    )
    assert (ROOT / "CODE_OF_CONDUCT.md").is_file()
    assert (ROOT / "SECURITY.md").is_file()
    assert (ROOT / ".github/ISSUE_TEMPLATE/security.yml").is_file()
