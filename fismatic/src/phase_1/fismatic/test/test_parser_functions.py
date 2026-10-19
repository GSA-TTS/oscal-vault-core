import pytest

from ..parser import (
    extract_customer_responsibilities,
    parse_implementation_details,
)


@pytest.fixture
def implementation_status_text():
    return """Implementation Status:
☒ Implemented
☐ Partially Implemented
☐ Planned
☐ Alternative implementation
☐ Not applicable"""


@pytest.fixture
def control_origination_text():
    return """Control Origination:
☒ Inherited from: cloud.gov Pages
☐ Common Control
☒ Hybrid Control (Shared Between GSA Pages and cloud.gov Pages)
☐ System Specific Control"""


@pytest.fixture
def control_origination_alt_checked_glyph_text():
    # Some source SSPPs (e.g. FPDF) use U+2611 (☑) instead of U+2612 (☒).
    return """Control Origination:
☐ Inherited from: [Enter System's Name]
☑ Hybrid Control with [EDS Databricks]
☐ System Specific Control"""


@pytest.fixture
def section_text():
    return """cloud.gov Pages:
The site owner must ensure incidents are reported.
Customer must maintain compliance.

GSA OCISO:
System Owners are responsible for security controls."""


@pytest.fixture
def customer_responsibilities_text():
    return """The site owner must ensure incidents are reported.
Customers are required to maintain compliance.
System owners shall implement security controls.
The customer is responsible for configuration settings.
Users have to review access logs monthly."""


def test_implementation_status(implementation_status_text):
    """Test implementation status parsing"""
    parsed = parse_implementation_details(implementation_status_text)
    assert parsed["implementation_status"]["implemented"] == True
    assert parsed["implementation_status"]["partially_implemented"] == False


def test_control_origination(control_origination_text):
    """Test control origination parsing"""
    parsed = parse_implementation_details(control_origination_text)
    assert parsed["control_origination"]["inherited"] == True
    assert parsed["control_origination"]["inherited_from"] == "cloud.gov Pages"


def test_control_origination_alt_checked_glyph(control_origination_alt_checked_glyph_text):
    """U+2611 (☑) checked boxes must be recognized same as U+2612 (☒)."""
    parsed = parse_implementation_details(control_origination_alt_checked_glyph_text)
    assert parsed["control_origination"]["hybrid"] == True
    assert parsed["control_origination"]["inherited"] == False


def test_section_parsing(section_text):
    """Test section parsing"""
    parsed = parse_implementation_details(section_text)
    assert "cloud.gov Pages" in parsed["sections"]
    assert "GSA OCISO" in parsed["sections"]


def test_customer_responsibilities(customer_responsibilities_text):
    """Test customer responsibility extraction"""
    responsibilities = extract_customer_responsibilities(customer_responsibilities_text)
    assert len(responsibilities) >= 4


def test_combined_parsing(
    implementation_status_text, control_origination_text, section_text
):
    """Test parsing of combined control text"""
    combined_text = (
        f"{implementation_status_text}\n\n{control_origination_text}\n\n{section_text}"
    )
    parsed = parse_implementation_details(combined_text)

    assert parsed["implementation_status"]["implemented"] == True
    assert parsed["control_origination"]["inherited"] == True
    assert "cloud.gov Pages" in parsed["sections"]
