import json

from compliance.lib.nist_standard import (
    STANDARD_REF,
    build_nist_standard,
    catalog_key,
    display_id,
    link_controls,
    requirement,
    requirement_ref,
)


def _doc(*ids):
    return {
        "controls": [
            {
                "bom-ref": f"control-{i}",
                "satisfies": [{"reference": i, "framework": "nist-800-53-r5"}],
            }
            for i in ids
        ]
    }


def test_control_ids_map_to_catalog_keys_and_display_form():
    for raw in ("AC-2(1)", "AC-02(01)", "ac-2.1", " AC-2 (1) "):
        assert catalog_key(raw) == "ac-2.1"
        assert display_id(raw) == "AC-2(1)"
    assert catalog_key("SC-7") == "sc-7" and display_id("sc-07") == "SC-7"
    assert (
        requirement_ref("AC-02(01)") == requirement_ref("AC-2(1)") == "req-nist-ac-2-1"
    )


def test_requirement_carries_catalog_text_with_labelled_placeholders():
    req = requirement("AC-2(1)")
    assert req["identifier"] == "AC-2(1)"
    assert req["title"].startswith("AC-2(1) ")
    assert "{{" not in req["text"] and "[Assignment:" in req["text"]


def test_unknown_control_has_no_invented_text():
    req = requirement("ZZ-99")
    assert req == {"bom-ref": "req-nist-zz-99", "identifier": "ZZ-99", "title": "ZZ-99"}


def test_standard_is_deduplicated_and_numerically_ordered():
    std = build_nist_standard(["SC-7", "AC-2(1)", "AC-10", "AC-2", "AC-2"])
    assert [r["identifier"] for r in std["requirements"]] == [
        "AC-2",
        "AC-2(1)",
        "AC-10",
        "SC-7",
    ]
    assert std["bom-ref"] == STANDARD_REF and std["owner"] == "NIST"


def test_link_controls_replaces_objects_with_refs_and_defines_the_standard():
    doc = _doc("AC-2", "AC-02", "SC-7(3)")
    assert link_controls(doc) == 3
    assert all(isinstance(r, str) for c in doc["controls"] for r in c["satisfies"])
    standard = doc["definitions"]["standards"][0]
    defined = {r["bom-ref"] for r in standard["requirements"]}
    assert {r for c in doc["controls"] for r in c["satisfies"]} <= defined
    assert len(standard["requirements"]) == 2


def test_link_controls_is_idempotent_and_keeps_foreign_entries():
    doc = _doc("AC-2")
    doc["controls"][0]["satisfies"].append("req-other")
    link_controls(doc)
    first = json.dumps(doc, sort_keys=True)
    assert link_controls(doc) == 0
    assert json.dumps(doc, sort_keys=True) == first
    assert doc["controls"][0]["satisfies"] == ["req-nist-ac-2", "req-other"]


def test_document_without_nist_references_is_untouched():
    doc = {"controls": [{"satisfies": ["req-x"]}, {}]}
    assert link_controls(doc) == 0 and "definitions" not in doc
