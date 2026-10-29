from compliance.lib.transforms import (
    filter_generated_recovery_callout,
    parse_gfm_table,
    parse_markdown_list,
    parse_port_expression,
)


def test_plain_text_emits_no_markdown_or_split_event():
    assert parse_gfm_table("Administrator").events == ()
    assert parse_markdown_list("Administrator").events == ()


def test_generated_callout_filter_preserves_authored_blockquotes():
    result = filter_generated_recovery_callout(
        "> Recovered from Section 9\n> This authored warning matters\nArchitecture"
    )

    assert "> This authored warning matters" in result.value
    assert "Recovered from" not in result.value
    assert [event.operator for event in result.events] == ["generated_callout_filter"]


def test_port_parser_handles_lists_ranges_and_transports():
    result = parse_port_expression("80/443 (TCP/UDP); Ephemeral (49152-65535)")
    values = [item.to_oscal() for item in result.value]

    assert {item["start"] for item in values} >= {80, 49152}
    assert any(item["end"] == 65535 for item in values)
    assert result.events[0].residual == ()


def test_port_parser_preserves_unparsed_residual():
    result = parse_port_expression("443 TCP and Vendor-managed dynamic ports")

    assert result.value[0].start == 443
    assert result.events[0].residual == ("Vendor-managed dynamic ports",)
    assert result.events[0].lossiness == "partial"
