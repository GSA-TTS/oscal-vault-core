"""Synthetic preservation, discrepancy and fail-closed orchestration regression tests."""

import argparse
import base64
import io
import json
import zipfile
from pathlib import Path

import docx
import jsonschema
import pytest
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from compliance.lib.docx_integrity_compare import compare_packages, normalized_package
from compliance.lib.docx_integrity_coverage import preserve_source_nodes
from compliance.lib.docx_integrity_report import overall_status
from compliance.scripts.docx_e2e_integrity import execute_run, main, prepare_output


def make_source(path):
    document = docx.Document()
    document.add_heading("Repeated heading", 1)
    document.add_heading("Repeated heading", 1)
    document.add_paragraph("Synthetic narrative; not system content.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Merged inventory"
    table.cell(1, 0).text = "Synthetic entry"
    document.sections[0].header.paragraphs[0].text = "Synthetic header"
    document.sections[0].footer.paragraphs[0].text = "Synthetic footer"
    document.save(path)


def rewrite(source, target, transform):
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as output:
        for name in reversed(original.namelist()):
            payload = original.read(name)
            output.writestr(name, transform(name, payload))


def test_zip_order_is_not_a_content_gate(tmp_path):
    source, target = tmp_path / "source.docx", tmp_path / "target.docx"
    make_source(source)
    rewrite(source, target, lambda name, payload: payload)
    assert source.read_bytes() != target.read_bytes()
    assert (
        compare_packages(normalized_package(source), normalized_package(target))[
            "status"
        ]
        == "PASS"
    )


def test_volatile_revision_ids_allowed_but_text_and_geometry_fail(tmp_path):
    source = tmp_path / "source.docx"
    make_source(source)
    document = docx.Document(source)
    document.paragraphs[0]._p.set(qn("w:rsidR"), "ABCDEF00")
    volatile = tmp_path / "volatile.docx"
    document.save(volatile)
    comparison = compare_packages(
        normalized_package(source), normalized_package(volatile)
    )
    assert comparison["status"] == "PASS"
    assert all(d["category"] == "volatile_serialization" for d in comparison["deltas"])
    document.paragraphs[2].text = "Vanished narrative replacement"
    document.sections[0].left_margin = 123456
    target = tmp_path / "target.docx"
    document.save(target)
    comparison = compare_packages(
        normalized_package(source), normalized_package(target)
    )
    assert comparison["status"] == "FAIL"
    assert (
        "sections"
        in next(d for d in comparison["deltas"] if d["locator"] == "word/document.xml")[
            "invariant_changes"
        ]
    )


def test_review_decisions_bind_both_hashes_and_debt_never_passes(tmp_path):
    source, target = tmp_path / "source.docx", tmp_path / "target.docx"
    make_source(source)
    document = docx.Document(source)
    document.paragraphs[2].text = "Intentional synthetic write"
    document.save(target)
    before, after = normalized_package(source), normalized_package(target)
    delta = next(
        d
        for d in compare_packages(before, after)["deltas"]
        if d["locator"] == "word/document.xml"
    )
    decision = {
        **delta,
        "category": "structured_preserved",
        "reviewer": "synthetic-reviewer",
        "date": "2026-11-16",
        "reason": "Synthetic write verified",
        "id": "SYN-001",
    }
    assert compare_packages(before, after, [decision])["status"] == "PASS"
    decision["after_sha256"] = "0" * 64
    assert compare_packages(before, after, [decision])["status"] == "FAIL"
    decision["after_sha256"] = delta["after_sha256"]
    decision["category"] = "known_baselined_gap"
    assert compare_packages(before, after, [decision])["status"] == "FAIL"


def test_raw_ir_fallback_retains_duplicate_headings_merged_tables_and_furniture(
    tmp_path,
):
    source = tmp_path / "source.docx"
    make_source(source)
    report = preserve_source_nodes(source, tmp_path / "vault")
    assert report["status"] == "PASS"
    assert report["structured_count"] == 0
    assert any(
        t["merged"] and t["projection_status"] == "preserved_only"
        for t in report["tables"]
    )
    assert all(n["evidence"] and n["locator"] for n in report["nodes"])
    records = json.loads((tmp_path / "vault/_source/nodes.json").read_text())
    assert sum(n["text"] == "Repeated heading" for n in records) == 2


def test_unknown_content_control_wrapper_changes_are_not_volatile(tmp_path):
    source, target = tmp_path / "source.docx", tmp_path / "target.docx"
    make_source(source)
    document = docx.Document(source)
    wrapper = OxmlElement("w:sdt")
    props = OxmlElement("w:sdtPr")
    wrapper.append(props)
    document.paragraphs[2]._p.append(wrapper)
    document.save(target)
    comparison = compare_packages(
        normalized_package(source), normalized_package(target)
    )
    assert comparison["status"] == "FAIL"


def test_missing_source_report_is_blocked_and_schema_valid(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    args = argparse.Namespace(
        source=tmp_path / "absent.docx",
        system_id="synthetic",
        template_family="unknown",
        timeout=5,
        fill_context=None,
        decisions=None,
    )
    report = execute_run(args, output)
    assert report["status"] == "BLOCKED"
    assert report["source_sha256"] is None
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "datacontracts/schemas/docx-integrity-v1.schema.json"
        ).read_text()
    )
    jsonschema.validate(report, schema)
    assert (output / "compare/report.md").is_file()


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (["PASS"] * 4, "PASS"),
        (["PASS", "BLOCKED", "PASS", "PASS"], "BLOCKED"),
        (["FAIL", "BLOCKED", "PASS", "PASS"], "FAIL"),
        (["PASS", "INCOMPLETE", "PASS", "PASS"], "INCOMPLETE"),
    ],
)
def test_no_partial_or_skipped_success(statuses, expected):
    assert (
        overall_status(
            {f"L{i}": {"status": status} for i, status in enumerate(statuses)}
        )
        == expected
    )


def test_output_never_overwrites_source_or_existing_evidence(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(b"source")
    with pytest.raises(ValueError):
        prepare_output(tmp_path, source, tmp_path / "workspace")
    output = tmp_path / "output"
    prepare_output(output, source, tmp_path / "workspace")
    with pytest.raises(FileExistsError):
        prepare_output(output, source, tmp_path / "workspace")
    assert source.read_bytes() == b"source"


def test_cli_requires_explicit_source_authorization(tmp_path):
    with pytest.raises(SystemExit):
        main(
            [
                "--source",
                str(tmp_path / "absent.docx"),
                "--system-id",
                "synthetic",
                "--workspace",
                str(tmp_path),
                "--output",
                str(tmp_path / "output"),
            ]
        )
    assert not (tmp_path / "output").exists()


def test_timed_out_stage_is_incomplete_with_preserved_transcript(tmp_path, monkeypatch):
    import subprocess
    from compliance.scripts.docx_e2e_integrity import StageError, run_command

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(StageError) as error:
        run_command(tmp_path, "synthetic", ["validate_oscal.py"], 1)
    assert error.value.status == "INCOMPLETE"
    record = json.loads((tmp_path / "transcript.log").read_text())
    assert record["status"] == "INCOMPLETE"
    assert record["output_sha256"]


def test_live_synthetic_pipeline_keeps_source_and_blocks_missing_fill_context(tmp_path):
    """Actual parser/vault/sidecars/export execution, not mocked stage results."""
    from compliance.tests.test_control_table_fill import _add_cis_table

    source = tmp_path / "synthetic.docx"
    document = docx.Document()
    document.add_heading("Synthetic system", 1)
    _add_cis_table(document, "AC-1", with_parts=True)
    document.save(source)
    original = source.read_bytes()
    output = tmp_path / "output"
    output.mkdir()
    args = argparse.Namespace(
        source=source,
        system_id="synthetic",
        template_family="unknown",
        timeout=60,
        fill_context=None,
        decisions=None,
    )
    report = execute_run(args, output)
    assert source.read_bytes() == original
    assert report["layers"]["L0"]["status"] == "PASS"
    assert report["layers"]["L1"]["status"] == "PASS"
    assert report["layers"]["L2"]["status"] == "PASS"
    assert report["layers"]["L3"]["status"] == "BLOCKED"
    assert (output / "vault/synthetic/_source/nodes.json").is_file()
    assert report["status"] != "PASS"
    assert (output / "transcript.log").is_file()


def test_substantive_delta_cannot_be_excused_as_volatile(tmp_path):
    source, target = tmp_path / "source.docx", tmp_path / "target.docx"
    make_source(source)
    document = docx.Document(source)
    document.paragraphs[2].text = "Changed visible text"
    document.save(target)
    before, after = normalized_package(source), normalized_package(target)
    delta = next(
        d
        for d in compare_packages(before, after)["deltas"]
        if d["locator"] == "word/document.xml"
    )
    decision = {
        **delta,
        "category": "volatile_serialization",
        "reviewer": "synthetic",
        "date": "2026-11-16",
        "reason": "Invalid classification",
        "id": "SYN-002",
    }
    with pytest.raises(ValueError):
        compare_packages(before, after, [decision])


def test_live_fillback_uses_actual_contract_render_and_control_readback(
    tmp_path, monkeypatch
):
    from docx.shared import RGBColor
    from compliance.scripts import docx_e2e_integrity as harness
    from compliance.tests.test_control_table_fill import _add_cis_table
    from compliance.tests.test_docx_fill_roundtrip import _mini_contract
    from compliance.lib.heading_parser import load_contract

    source = tmp_path / "source.docx"
    document = docx.Document()
    instruction = document.add_paragraph("Instruction: describe the system")
    instruction.runs[0].font.color.rgb = RGBColor(0x00, 0x70, 0xC0)
    document.add_paragraph("")
    document.add_paragraph("Synthetic exact narrative")
    _add_cis_table(document, "AC-1", with_parts=False)
    document.save(source)
    output = tmp_path / "output"
    output.mkdir()
    args = argparse.Namespace(
        source=source,
        system_id="synthetic",
        template_family="unknown",
        timeout=60,
        fill_context=None,
        decisions=None,
    )
    report = execute_run(args, output)
    assert report["layers"]["L2"]["status"] == "PASS"
    ssp = next(
        path
        for path in (output / "vault/synthetic/.exports/oscal-json").glob("*.json")
        if "system-security-plan" in json.loads(path.read_text())
    )
    # Only select a synthetic contract; all rendering, export and readback is live.
    contract = load_contract(_mini_contract(tmp_path))
    monkeypatch.setattr(
        "compliance.lib.heading_parser.load_contract", lambda path: contract
    )
    context = tmp_path / "context.json"
    from compliance.lib.docx_integrity_compare import digest

    nodes = json.loads((output / "source/nodes.json").read_text())
    index = next(
        i for i, node in enumerate(nodes) if node["text"] == "Synthetic exact narrative"
    )
    context.write_text(
        json.dumps(
            {
                "values": {"desc": "Synthetic exact narrative"},
                "source_sha256": digest(source.read_bytes()),
                "evidence": {
                    "desc": {
                        "artifact": "source/nodes.json",
                        "pointer": f"/{index}/text",
                    }
                },
            }
        )
    )
    result = harness.fillback_stage(source, ssp, output, "moderate", context)
    assert result["tags"]["recovery_rate"] == 1.0
    assert result["status"] == "FAIL"  # Intentional source changes still need review.
    assert (output / "fillback/filled.docx").is_file()
    readback = json.loads((output / "compare/table-parity.json").read_text())
    assert readback["status"] == "PASS", readback["findings"]
    deltas = json.loads((output / "compare/package-diff.json").read_text())["deltas"]
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps(
            [
                {
                    **delta,
                    "category": "structured_preserved",
                    "reviewer": "synthetic-review",
                    "date": "2026-11-16",
                    "reason": "Exact synthetic write and readback reviewed",
                    "id": f"SYN-{index}",
                }
                for index, delta in enumerate(deltas)
                if delta["category"] == "new_loss"
            ]
        )
    )
    approved = harness.fillback_stage(
        source, ssp, output, "moderate", context, decisions_path=decisions
    )
    assert approved["status"] == "PASS"


def test_fill_context_rejects_unbound_or_wrong_provenance(tmp_path):
    from compliance.scripts.docx_e2e_integrity import StageError, verified_fill_context

    source = tmp_path / "source.docx"
    make_source(source)
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"desc": "invented prose"}))
    with pytest.raises(StageError):
        verified_fill_context(context, source, tmp_path)


def test_retained_package_detects_corrupt_missing_and_unexpected_members(tmp_path):
    from compliance.lib.docx_integrity_coverage import verify_package_retention

    source = tmp_path / "source.docx"
    make_source(source)
    vault = tmp_path / "vault"
    preserve_source_nodes(source, vault)
    assert verify_package_retention(source, vault)["status"] == "PASS"
    member = next((vault / "_source/package").iterdir())
    original = member.read_bytes()
    member.write_bytes(b"corrupt")
    assert verify_package_retention(source, vault)["status"] == "FAIL"
    member.write_bytes(original)
    (member.parent / "unexpected").write_bytes(b"extra")
    assert verify_package_retention(source, vault)["status"] == "FAIL"


def test_custom_xml_mixed_content_and_whitespace_are_not_discarded():
    import xml.etree.ElementTree as ET
    from compliance.lib.docx_integrity_compare import _signature

    assert _signature(
        ET.fromstring("<root> leading <child/>tail</root>"), {}
    ) != _signature(ET.fromstring("<root>leading<child/>different</root>"), {})


def test_namespace_prefix_and_relationship_id_changes_are_serialization_only(tmp_path):
    source, target = tmp_path / "source.docx", tmp_path / "target.docx"
    make_source(source)

    def transform(name, payload):
        if name == "word/document.xml":
            return (
                payload.replace(b"xmlns:w=", b"xmlns:word=")
                .replace(b"<w:", b"<word:")
                .replace(b"</w:", b"</word:")
                .replace(b" w:", b" word:")
                .replace(b'r:id="rId9"', b'r:id="newHeaderId"')
            )
        if name == "word/_rels/document.xml.rels":
            return payload.replace(b'Id="rId9"', b'Id="newHeaderId"')
        return payload

    rewrite(source, target, transform)
    assert (
        compare_packages(normalized_package(source), normalized_package(target))[
            "status"
        ]
        == "PASS"
    )


def test_conflicting_decisions_fail_closed():
    with pytest.raises(ValueError):
        compare_packages(
            {"parts": {}}, {"parts": {}}, [{"locator": "x"}, {"locator": "x"}]
        )


def test_actual_readback_detects_unexpected_controls_and_expected_collisions(tmp_path):
    import copy
    from fismatic.ssp_converter import SSPConverter
    from compliance.scripts.docx_e2e_integrity import control_readback
    from compliance.tests.test_control_table_fill import _add_cis_table

    document = docx.Document()
    _add_cis_table(document, "AC-1", with_parts=False)
    source = tmp_path / "source.docx"
    from compliance.lib.control_table_fill import fill_control_tables
    from compliance.lib.canonical_model import CanonicalControl

    control = CanonicalControl.from_raw(
        "AC-1", raw_status="implemented", raw_origination=["system-specific"]
    )
    fill_control_tables(document, [control], source)
    expected = SSPConverter(str(source)).get_oscal_data()
    _add_cis_table(document, "AC-2", with_parts=False)
    filled = tmp_path / "filled.docx"
    document.save(filled)
    result = control_readback(expected, filled, tmp_path)
    assert any(f["kind"] == "control_set_difference" for f in result["findings"])
    collision = copy.deepcopy(expected)
    requirements = collision["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ]
    requirements.append(copy.deepcopy(requirements[0]))
    result = control_readback(collision, source, tmp_path)
    assert any(
        f["kind"] == "duplicate_expected_control_ids" for f in result["findings"]
    )


def test_provenance_hash_value_and_pointer_are_all_validated(tmp_path):
    from compliance.scripts.docx_e2e_integrity import StageError, verified_fill_context
    from compliance.lib.docx_integrity_compare import digest

    source = tmp_path / "source.docx"
    make_source(source)
    (tmp_path / "source").mkdir()
    (tmp_path / "source/nodes.json").write_text(json.dumps([{"text": "source-bound"}]))
    context = tmp_path / "context.json"
    envelope = {
        "values": {"desc": "source-bound"},
        "source_sha256": digest(source.read_bytes()),
        "evidence": {"desc": {"artifact": "source/nodes.json", "pointer": "/0/text"}},
    }
    context.write_text(json.dumps(envelope))
    assert verified_fill_context(context, source, tmp_path) == {"desc": "source-bound"}
    envelope["values"]["desc"] = "invented"
    context.write_text(json.dumps(envelope))
    with pytest.raises(StageError):
        verified_fill_context(context, source, tmp_path)
    envelope["values"]["desc"] = "source-bound"
    envelope["source_sha256"] = "wrong"
    context.write_text(json.dumps(envelope))
    with pytest.raises(StageError):
        verified_fill_context(context, source, tmp_path)


def test_unsupported_whole_control_part_rows_report_unmatched_and_preserve_template(
    tmp_path,
):
    from compliance.tests.test_control_table_fill import _add_cis_table
    from compliance.lib.canonical_model import CanonicalControl
    from compliance.lib.control_table_fill import fill_control_tables

    document = docx.Document()
    _add_cis_table(document, "AC-1", with_parts=True)
    control = CanonicalControl.from_raw(
        "AC-1", raw_status="implemented", raw_origination=["system-specific"]
    )
    result = fill_control_tables(
        document,
        [control],
        tmp_path / "filled.docx",
        whole_control_text_by_control={"AC-1": "Whole-control synthetic prose"},
    )
    assert result.control_results[0]["whole_control"]["placement"] == "unmatched"
    reloaded = docx.Document(tmp_path / "filled.docx")
    assert all(
        "Whole-control synthetic prose" not in cell.text
        for row in reloaded.tables[0].rows
        for cell in row.cells
    )


def test_media_relationships_and_header_prose_are_required_invariants(tmp_path):
    source, target = tmp_path / "source.docx", tmp_path / "target.docx"
    make_source(source)
    document = docx.Document(source)
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ9sAAAAASUVORK5CYII="
    )
    document.add_picture(io.BytesIO(image))
    document.save(source)
    before = normalized_package(source)
    assert any(name.startswith("word/media/") for name in before["parts"])
    document.sections[0].header.paragraphs[0].text = "Changed header text"
    document.save(target)
    comparison = compare_packages(before, normalized_package(target))
    assert comparison["status"] == "FAIL"
    assert any(d["locator"].startswith("word/header") for d in comparison["deltas"])


@pytest.mark.parametrize("shape", ["crm", "row2"])
def test_cis_row2_and_crm_reference_rows_remain_explicit_source_records(
    tmp_path, shape
):
    document = docx.Document()
    table = document.add_table(rows=6, cols=2)
    table.cell(0, 0).text = "CR-1" if shape == "crm" else "AC-1"
    table.cell(0, 1).text = (
        "Responsibility Summary Information"
        if shape == "crm"
        else "Control Summary Information"
    )
    row = 1 if shape == "crm" else 2
    table.cell(row, 0).merge(
        table.cell(row, 1)
    ).text = "Implementation Status:\n☒ Implemented"
    table.cell(3, 0).merge(table.cell(3, 1)).text = "Controls Reference: AC-2, IA-2"
    table.cell(4, 0).merge(
        table.cell(4, 1)
    ).text = "Customer Responsibility: synthetic prose"
    source = tmp_path / f"{shape}.docx"
    document.save(source)
    report = preserve_source_nodes(source, tmp_path / "vault")
    assert report["status"] == "PASS"
    records = json.loads((tmp_path / "vault/_source/nodes.json").read_text())
    assert any("Controls Reference: AC-2, IA-2" in record["text"] for record in records)
    assert any(
        "Customer Responsibility: synthetic prose" in record["text"]
        for record in records
    )
