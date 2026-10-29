from compliance.lib.threat_model_checks import (
    bidirectional_control_threat_errors,
    find_ipv4_leak,
    find_leaks,
)


def test_leak_scan_finds_urls_emails_and_ips_but_not_section_citations():
    kinds = {
        label
        for label, _ in find_leaks(
            "see https://example.invalid/x or a@b.example, 10.1.2.3"
        )
    }
    assert kinds == {"URL", "email", "IPv4"}
    assert find_ipv4_leak("per section 10.1.2.3 of the standard") is None
    assert find_leaks("nothing sensitive here") == []


def test_control_side_mitigation_links_must_be_symmetric_only_when_present():
    threats = [{"bom-ref": "t1", "mitigations": ["c1", "c2", "ghost"]}]
    controls = [
        {"bom-ref": "c1", "mitigations": []},
        {"bom-ref": "c2"},
    ]
    errors = bidirectional_control_threat_errors(threats, controls)
    assert len(errors) == 1 and "c1" in errors[0]
