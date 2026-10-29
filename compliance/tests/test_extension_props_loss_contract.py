"""#43 lossless pipeline: pin the documented loss/passthrough contract of
``_extension_props`` and ``_prop`` so silent widening or regressions are caught.

These are not aspirational "nothing is lost" tests. They assert *exactly* what
the extension-props boundary preserves (scalars, flattened lists) and what it
deliberately drops (nested dicts, whitespace structure, already-mapped keys),
so the behavior is a reviewed contract rather than an accident.
"""

from __future__ import annotations

from compliance.lib.export_oscal import _extension_props, _prop


def _by_name(props):
    return {p["name"]: p["value"] for p in props}


def test_prop_collapses_multiline_and_padded_values():
    """OSCAL prop values are single-line; _prop must collapse whitespace."""
    prop = _prop("narrative", "  line one\n\n  line two  ")
    assert prop["value"] == "line one line two"
    assert "\n" not in prop["value"]
    assert prop["value"] == prop["value"].strip()


def test_prop_handles_none_as_empty():
    assert _prop("x", None)["value"] == ""


def test_extension_props_carries_scalar_metadata():
    """Leftover scalar frontmatter keys survive as props (carried forward)."""
    props = _by_name(_extension_props({"cloud_model": "paas", "fedramp_pkg": "low"}))
    assert props["cloud_model"] == "paas"
    assert props["fedramp_pkg"] == "low"


def test_extension_props_flattens_lists_to_comma_joined_strings():
    """A list is flattened (structure lost) but values are carried, not dropped."""
    props = _by_name(_extension_props({"tags_x": ["a", "b", "c"]}))
    assert props["tags_x"] == "a, b, c"


def test_extension_props_drops_nested_dicts_by_contract():
    """Nested dict values have no OSCAL prop mapping and are deliberately dropped.

    This is a *documented* loss (needs bespoke mapping). The test pins it so a
    future change that starts emitting a malformed prop for a dict is caught.
    """
    props = _extension_props({"scalar": "keep", "nested": {"a": 1}})
    names = {p["name"] for p in props}
    assert "scalar" in names
    assert "nested" not in names


def test_extension_props_skips_none_and_empty():
    props = _extension_props({"a": None, "b": "", "c": "  ", "d": "x"})
    names = {p["name"] for p in props}
    assert names == {"d"}


def test_extension_props_skips_structural_and_underscore_keys():
    """Canonical structural containers and private keys never become props."""
    fm = {
        "raw_body": "# h\nbody",
        "body_sections": [{"heading": "h", "content": "x"}],
        "inline_fields": {"k": "v"},
        "frontmatter": {"nested": 1},
        "table_records": [{"kind": "protocol", "rows": [["443"]]}],
        "table_record_metrics": {"tables_total": 1},
        "table_record_count": 1,
        "table_kinds": ["protocol"],
        "table_classifications": ["record_set"],
        "table_structured_count": 1,
        "table_opaque_count": 0,
        "table_unmapped_count": 0,
        "_meta": {"schema_version": 1},
        "kept": "value",
    }
    names = {p["name"] for p in _extension_props(fm)}
    assert names == {"kept"}


def test_extension_props_does_not_duplicate_first_class_keys():
    """Keys already mapped to first-class OSCAL fields are not re-emitted as props."""
    # title/version/uuid are in _NON_PROP_KEYS; a leftover custom key is not.
    props = _by_name(_extension_props({"title": "T", "version": "1.0", "custom": "c"}))
    assert "title" not in props
    assert "version" not in props
    assert props["custom"] == "c"
