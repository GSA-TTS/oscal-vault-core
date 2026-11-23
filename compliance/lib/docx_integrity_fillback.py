"""Live DOCX rendering/readback, independent of executable entry points."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from compliance.lib.docx_integrity_compare import (
    compare_packages,
    digest,
    normalized_package,
)
from compliance.lib.docx_integrity_context import verified_fill_context
from compliance.lib.docx_integrity_report import StageError, write_json

CORE = Path(__file__).resolve().parents[2]
CONTRACTS = {
    "low": "low-sspp",
    "moderate": "moderate-sspp",
    "high": "high-sspp",
    "crm": "leveraged-saas-crm",
}


def control_readback(expected: dict, filled: Path, output: Path) -> dict:
    from compliance.lib import oscal_source as src
    from compliance.lib.control_table_anchors import find_control_tables
    from compliance.lib.control_table_fill import _match_key

    fismatic = str(CORE / "fismatic/src/phase_1")
    if fismatic not in sys.path:
        sys.path.insert(0, fismatic)
    from fismatic.ssp_converter import SSPConverter

    controls = src.load_from_oscal_ssp(expected, strict=True)
    parsed = SSPConverter(str(filled)).get_oscal_data()
    if not parsed:
        raise StageError("FAIL", "Fismatic produced no fill-back readback")
    recovered = src.load_from_oscal_ssp(parsed)
    actual = {_match_key(c.control_id): c for c in recovered}
    expected_keys = [_match_key(c.control_id) for c in controls]
    findings = []
    if len(actual) != len(recovered):
        findings.append({"kind": "duplicate_readback_control_ids"})
    if len(expected_keys) != len(set(expected_keys)):
        findings.append({"kind": "duplicate_expected_control_ids"})
    if set(actual) != set(expected_keys):
        findings.append({"kind": "control_set_difference"})
    st_map, wc_map = (
        src.statement_text_map_from_oscal_ssp,
        src.whole_control_text_map_from_oscal_ssp,
    )
    data = {
        "actual": actual,
        "no_origination": {
            _match_key(a.control_id)
            for a in find_control_tables(filled)
            if a.origination_cell is None
        },
        "expected_text": st_map(expected),
        "actual_text": {_match_key(k): v for k, v in st_map(parsed).items()},
        "expected_whole": wc_map(expected),
        "actual_whole": {_match_key(k): v for k, v in wc_map(parsed).items()},
    }
    for control in controls:
        findings.extend(compare_control(control, data))
    result = {
        "status": "FAIL" if findings else "PASS",
        "expected_controls": len(controls),
        "readback_controls": len(recovered),
        "findings": findings,
    }
    write_json(output / "compare/table-parity.json", result)
    return result


def compare_control(control, data: dict) -> list:
    from compliance.lib.control_table_fill import _match_key, _normalize_statement_map

    key = _match_key(control.control_id)
    other = data["actual"].get(key)
    expected_text, exp_collision = _normalize_statement_map(
        key, data["expected_text"].get(control.control_id, {}), control.statement_ids
    )
    actual_text, act_collision = _normalize_statement_map(
        key, data["actual_text"].get(key, {}), getattr(other, "statement_ids", None)
    )
    findings = []
    if exp_collision or act_collision:
        findings.append(
            {"control_id": control.control_id, "kind": "duplicate_statement_ids"}
        )
    if (expected_text or control.control_id not in data["expected_whole"]) and set(
        expected_text
    ) != set(actual_text):
        findings.append(
            {"control_id": control.control_id, "kind": "statement_set_difference"}
        )
    origination_diff = sorted(control.oscal_origination) != sorted(
        getattr(other, "oscal_origination", [])
    ) and not (
        key in data.get("no_origination", set())
        and control.oscal_origination == ["system-specific"]
        and not getattr(other, "oscal_origination", [])
    )
    if (
        other is None
        or control.oscal_implementation_status != other.oscal_implementation_status
        or origination_diff
    ):
        findings.append(
            {"control_id": control.control_id, "kind": "status_or_origination"}
        )
    findings.extend(
        compare_narratives(control.control_id, key, expected_text, actual_text, data)
    )
    return findings


def compare_narratives(
    control_id: str, key: str | None, expected_text: dict, actual_text: dict, data: dict
) -> list:
    findings = []
    for statement_id, text in expected_text.items():
        found = actual_text.get(statement_id, "")
        if " ".join(text.split()) != " ".join(found.split()):
            findings.append(
                prose_delta(control_id, "narrative", text, found)
                | {"statement_id": statement_id}
            )
    if control_id in data["expected_whole"]:
        expected_body = data["expected_whole"][control_id]
        actual_body = data["actual_whole"].get(key) or "\n\n".join(actual_text.values())
        if " ".join(expected_body.split()) != " ".join(actual_body.split()):
            findings.append(
                prose_delta(
                    control_id, "whole_control_narrative", expected_body, actual_body
                )
            )
    return findings


def prose_delta(control_id: str, kind: str, expected: str, actual: str) -> dict:
    return {
        "control_id": control_id,
        "kind": kind,
        "expected_sha256": digest(expected.encode()),
        "actual_sha256": digest(actual.encode()),
    }


def fillback_stage(
    source: Path,
    ssp: Path,
    output: Path,
    family: str,
    context_path: Path | None,
    *,
    decisions_path: Path | None = None,
) -> dict:
    contract, context, declared = fill_inputs(source, output, family, context_path)
    filled, injected, result, fill, parity = render_fillback(
        source, ssp, output, contract, context
    )
    tags = {
        "injected_tags": injected.injected_count,
        "filled_tags": len(result.filled_tags),
        "matched": len(parity.matched),
        "missing": parity.missing,
        "mismatched": list(parity.mismatched),
        "recovery_rate": parity.recovery_rate,
    }
    write_json(output / "compare/narrative-parity.json", tags)
    comparison, readback = compare_fillback(source, ssp, filled, output, decisions_path)
    return fillback_result(
        {
            "parity": parity,
            "declared": declared,
            "injected": injected,
            "result": result,
            "fill": fill,
            "readback": readback,
            "comparison": comparison,
            "tags": tags,
        }
    )


def fill_inputs(
    source: Path, output: Path, family: str, context_path: Path | None
) -> tuple:
    from compliance.lib.docx_fill import synthetic_fill_context
    from compliance.lib.heading_parser import load_contract

    if family not in CONTRACTS or context_path is None or not context_path.is_file():
        raise StageError(
            "BLOCKED",
            "L3 requires a supported family and authorized --fill-context; no fixture substitution",
        )
    contract = load_contract(
        CORE / "compliance/datacontracts" / f"{CONTRACTS[family]}-roundtrip.yaml"
    )
    context = verified_fill_context(context_path, source, output)
    declared = set(synthetic_fill_context(contract))
    if (
        not declared
        or set(context) != declared
        or any(not isinstance(v, str) or not v.strip() for v in context.values())
    ):
        raise StageError(
            "BLOCKED",
            "fill context must supply every declared writable tag, with nonempty text and no extra tags",
        )
    return contract, context, declared


def render_fillback(
    source: Path, ssp: Path, output: Path, contract, context: dict
) -> tuple:
    from compliance.lib.docx_fill import check_parity, fill_template, reextract_filled
    from compliance.lib.docx_jinjafier import jinjafy
    from compliance.lib.docx_integrity_render import build_from_oscal

    jinja = output / "fillback/template.jinja.docx"
    narrative = output / "fillback/narrative.docx"
    filled = output / "fillback/filled.docx"
    jinja.parent.mkdir(exist_ok=True)
    injected = jinjafy(source, contract, jinja)
    result = fill_template(jinja, context, narrative, contract)
    fill = build_from_oscal(
        ssp,
        narrative,
        filled,
        output / "fillback/fill-manifest.json",
        strict_mapping=True,
    )
    parity = check_parity(context, reextract_filled(filled, contract, strict=True))
    return filled, injected, result, fill, parity


def compare_fillback(
    source: Path, ssp: Path, filled: Path, output: Path, decisions_path: Path | None
) -> tuple:
    readback = control_readback(json.loads(ssp.read_text()), filled, output)
    normalized = normalized_package(filled)
    write_json(output / "fillback/package-inventory.json", normalized["inventory"])
    write_json(output / "fillback/rendered-text.json", normalized["parts"])
    decisions = json.loads(decisions_path.read_text()) if decisions_path else []
    comparison = compare_packages(normalized_package(source), normalized, decisions)
    write_json(output / "compare/package-diff.json", comparison)
    for label, prefix in (
        ("media", "word/media/"),
        ("style", "word/styles"),
        ("content", "word/document"),
    ):
        write_json(
            output / "compare" / f"{label}-parity.json",
            [
                delta
                for delta in comparison["deltas"]
                if delta["locator"].startswith(prefix)
            ],
        )
    return comparison, readback


def fillback_result(evidence: dict) -> dict:
    parity, declared, injected, result, fill, readback, comparison, tags = (
        evidence[key]
        for key in (
            "parity",
            "declared",
            "injected",
            "result",
            "fill",
            "readback",
            "comparison",
            "tags",
        )
    )
    bad = (
        not parity.ok
        or len(parity.matched) != len(declared)
        or injected.injected_count != len(declared)
        or result.missing_tags
        or fill["source_controls_without_target_table"]
        or fill["source_id_collisions"]
        or fill["duplicate_target_control_ids"]
        or any(
            s.get("placement") == "unmatched"
            for c in fill["controls"]
            for s in c["statements"]
        )
        or any(
            c["whole_control"] and c["whole_control"].get("placement") == "unmatched"
            for c in fill["controls"]
        )
        or readback["status"] != "PASS"
        or comparison["status"] != "PASS"
    )
    return {
        "status": "FAIL" if bad else "PASS",
        "reason": "Strict declared-slot, control readback and reviewed package-delta gates",
        "tags": tags,
    }
