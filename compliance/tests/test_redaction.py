from compliance.lib.redaction import redact


def test_urls_emails_and_ips_are_replaced():
    out = redact(
        "Mail a@b.example, see https://example.invalid/p, host 10.1.2.3/24 ok."
    )
    assert out == "Mail <REDACTED_EMAIL>, see <REDACTED_URL>, host <REDACTED_IP> ok."


def test_cidr_followed_by_a_full_stop_redacts_the_address_but_leaves_the_prefix():
    # Known gap carried over unchanged from the original: the CIDR suffix needs a non-dot
    # character after it, so "10.1.2.3/24." matches only "10.1.2.3".
    assert redact("host 10.1.2.3/24.") == "host <REDACTED_IP>/24."


def test_internal_keeps_urls_and_emails_but_still_redacts_ips():
    out = redact("a@b.example https://example.invalid 10.1.2.3", internal=True)
    assert out == "a@b.example https://example.invalid <REDACTED_IP>"


def test_section_citations_and_out_of_range_octets_are_not_ip_addresses():
    assert (
        redact("per section 5.1.1.1 and para. 2.3.4.5")
        == "per section 5.1.1.1 and para. 2.3.4.5"
    )
    assert redact("version 999.1.1.1") == "version 999.1.1.1"


def test_empty_input_is_empty_string():
    assert redact(None) == "" and redact("") == ""
