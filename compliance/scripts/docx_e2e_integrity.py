#!/usr/bin/env python3
"""Run one authorized DOCX through local, fail-closed integrity checks.

Exit codes: 0 machine PASS (human review pending), 1 FAIL, 2 BLOCKED,
3 INCOMPLETE. No corpus discovery, source edits, or network operations.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "fismatic" / "src" / "phase_1"))

from compliance.lib.docx_integrity_compare import (  # noqa: E402
    compare_packages,
    digest,
    normalized_package,
)
from compliance.lib.docx_integrity_report import (  # noqa: E402
    StageError,
    timestamp,
    write_json,
    write_report,
)

FAMILIES = ("low", "moderate", "high", "crm", "lightweight", "unknown")
CONTRACTS = {
    "low": "low-sspp",
    "moderate": "moderate-sspp",
    "high": "high-sspp",
    "crm": "leveraged-saas-crm",
}


def prepare_output(output: Path, source: Path, workspace: Path) -> Path:
    """Fresh private directory only; never overwrite evidence or source inputs."""
    output = output.expanduser().resolve()
    source = source.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    if output == source or output in source.parents or output == workspace:
        raise ValueError("output would contain source or overwrite workspace")
    if output == CORE or CORE in output.parents:
        raise ValueError("integrity output must be outside the core")
    if workspace in output.parents:
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", str(output)],
            cwd=workspace,
            timeout=30,
            check=False,
        )
        if ignored.returncode != 0:
            raise ValueError("output inside workspace must be Git-ignored")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    output.chmod(0o700)
    return output


def run_command(output: Path, stage: str, args: list[str], timeout: int) -> None:
    """Retain actual command output privately; transcript contains hashes, not prose."""
    command = [
        sys.executable,
        str(CORE / "compliance" / "scripts" / args[0]),
        *args[1:],
    ]
    log = output / "logs" / f"{stage}.log"
    log.parent.mkdir(exist_ok=True)
    started = timestamp()
    status = "PASS"
    returncode = None
    with log.open("wb") as stream:
        try:
            result = subprocess.run(
                command,
                cwd=output,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            returncode = result.returncode
            status = "PASS" if returncode == 0 else "FAIL"
        except subprocess.TimeoutExpired:
            status = "INCOMPLETE"
        except OSError:
            status = "BLOCKED"
    with (output / "transcript.log").open("a", encoding="utf-8") as transcript:
        transcript.write(
            json.dumps(
                {
                    "timestamp": started,
                    "ended": timestamp(),
                    "stage": stage,
                    "agent": "AI-assisted DOCX integrity harness v1",
                    "command": command,
                    "status": status,
                    "returncode": returncode,
                    "output": str(log.relative_to(output)),
                    "output_sha256": digest(log.read_bytes()),
                }
            )
            + "\n"
        )
    if status != "PASS":
        raise StageError(status, f"{stage}: {status}; inspect logs/{stage}.log locally")


def package_stage(source: Path, output: Path) -> dict:
    from compliance.lib.docx_preservation import (
        collect_opaque_artifacts,
        extract_managed_artifacts,
        verify_managed_artifacts,
        write_source_manifest,
    )

    package = normalized_package(source)
    write_json(output / "source" / "package-inventory.json", package["inventory"])
    write_json(output / "source" / "normalized-content.json", package["parts"])
    report = collect_opaque_artifacts(source)
    artifacts = extract_managed_artifacts(source, output / "source")
    manifest = write_source_manifest(report, output / "source", artifacts)
    write_json(
        output / "source" / "source-manifest.json", json.loads(manifest.read_text())
    )
    findings = report.loss_budget_findings() + verify_managed_artifacts(
        artifacts, output / "source"
    )
    from compliance.lib.docx_package import strict_findings

    findings += strict_findings(package["inventory"])
    write_json(output / "compare" / "package-accounting.json", {"findings": findings})
    if findings:
        raise StageError(
            "FAIL", "L0 package/node accounting failed; see package-accounting.json"
        )
    return package


def export_stage(
    source: Path, vault: Path, output: Path, system_id: str, timeout: int
) -> Path:
    commands = export_commands(source, vault, output, system_id)
    for stage, args in commands:
        run_command(output, stage, args, timeout)
        if stage == "canonical" and not list(
            (vault / ".snapshots" / "current").rglob("*.json")
        ):
            raise StageError("BLOCKED", "canonical generator emitted no sidecars")
        if stage == "export" and not list(
            (vault / ".exports" / "oscal-json").glob("*.json")
        ):
            raise StageError("BLOCKED", "exporter emitted no OSCAL documents")
    ssps = [
        path
        for path in (vault / ".exports" / "oscal-json").glob("*.json")
        if "system-security-plan" in json.loads(path.read_text())
    ]
    if len(ssps) != 1:
        raise StageError("BLOCKED", "expected exactly one exported SSP")
    write_json(
        output / "oscal" / "parity-report.json",
        {
            "status": "PASS",
            "schema": "PASS",
            "semantic": "PASS",
            "parity": "PASS",
            "ssp": str(ssps[0].relative_to(output)),
            "evidence": "logs/parity.log",
        },
    )
    return ssps[0]


def export_commands(source: Path, vault: Path, output: Path, system_id: str) -> tuple:
    return (
        (
            "vault",
            [
                "export_obsidian_vault.py",
                "--docx",
                str(source),
                "--plan-id",
                system_id,
                "--vault",
                str(vault),
                "--keep-existing",
            ],
        ),
        ("canonical", ["generate_canonical_json.py", "--vault", str(vault)]),
        (
            "export",
            [
                "export_oscal.py",
                "--vault",
                str(vault),
                "--validate",
                "--report-json",
                str(output / "oscal" / "export-report.json"),
            ],
        ),
        (
            "parity",
            ["validate_oscal.py", "--vault", str(vault), "--semantic", "--parity"],
        ),
    )


def control_readback(expected: dict, filled: Path, output: Path) -> dict:
    """Use unmodified Fismatic read path, compare status/origination and narratives."""
    from compliance.lib.oscal_source import (
        load_from_oscal_ssp,
        statement_text_map_from_oscal_ssp,
        whole_control_text_map_from_oscal_ssp,
    )
    from fismatic.ssp_converter import SSPConverter
    from compliance.lib.control_table_fill import _match_key

    controls = load_from_oscal_ssp(expected, strict=True)
    parsed = SSPConverter(str(filled)).get_oscal_data()
    if not parsed:
        raise StageError("FAIL", "Fismatic produced no fill-back readback")
    recovered = load_from_oscal_ssp(parsed)
    expected_text = statement_text_map_from_oscal_ssp(expected)
    actual_text = statement_text_map_from_oscal_ssp(parsed)
    expected_whole = whole_control_text_map_from_oscal_ssp(expected)
    actual_whole = whole_control_text_map_from_oscal_ssp(parsed)
    findings = []
    actual = {_match_key(c.control_id): c for c in recovered}
    actual_text = {_match_key(key): value for key, value in actual_text.items()}
    actual_whole = {_match_key(key): value for key, value in actual_whole.items()}
    if len(actual) != len(recovered):
        findings.append({"kind": "duplicate_readback_control_ids"})
    expected_keys = [_match_key(c.control_id) for c in controls]
    if len(expected_keys) != len(set(expected_keys)):
        findings.append({"kind": "duplicate_expected_control_ids"})
    if set(actual) != set(expected_keys):
        findings.append({"kind": "control_set_difference"})
    data = {
        "actual": actual,
        "expected_text": expected_text,
        "actual_text": actual_text,
        "expected_whole": expected_whole,
        "actual_whole": actual_whole,
    }
    for control in controls:
        findings.extend(compare_control(control, data))
    result = {
        "status": "FAIL" if findings else "PASS",
        "expected_controls": len(controls),
        "readback_controls": len(recovered),
        "findings": findings,
    }
    write_json(output / "compare" / "table-parity.json", result)
    return result


def compare_control(control, data: dict) -> list:
    from compliance.lib.control_table_fill import _match_key

    actual = data["actual"]
    expected_text, actual_text = data["expected_text"], data["actual_text"]
    expected_whole, actual_whole = data["expected_whole"], data["actual_whole"]
    findings = []
    key = _match_key(control.control_id)
    other = actual.get(key)
    expected_statements = set(expected_text.get(control.control_id, {}))
    recovered_statements = set(actual_text.get(key, {}))
    if (
        expected_statements or control.control_id not in expected_whole
    ) and expected_statements != recovered_statements:
        findings.append(
            {"control_id": control.control_id, "kind": "statement_set_difference"}
        )
    if (
        other is None
        or control.oscal_implementation_status != other.oscal_implementation_status
        or sorted(control.oscal_origination) != sorted(other.oscal_origination)
    ):
        findings.append(
            {"control_id": control.control_id, "kind": "status_or_origination"}
        )
    for statement_id, text in expected_text.get(control.control_id, {}).items():
        found = actual_text.get(key, {}).get(statement_id, "")
        if " ".join(text.split()) != " ".join(found.split()):
            findings.append(
                prose_delta(control.control_id, "narrative", text, found)
                | {"statement_id": statement_id}
            )
    if control.control_id in expected_whole:
        expected_body = expected_whole[control.control_id]
        actual_body = actual_whole.get(key) or "\n\n".join(
            actual_text.get(key, {}).values()
        )
        if " ".join(expected_body.split()) != " ".join(actual_body.split()):
            findings.append(
                prose_delta(
                    control.control_id,
                    "whole_control_narrative",
                    expected_body,
                    actual_body,
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
    write_json(output / "compare" / "narrative-parity.json", tags)
    comparison, readback = compare_fillback(source, ssp, filled, output, decisions_path)
    evidence = {
        "parity": parity,
        "declared": declared,
        "injected": injected,
        "result": result,
        "fill": fill,
        "readback": readback,
        "comparison": comparison,
        "tags": tags,
    }
    return fillback_result(evidence)


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
        CORE / "compliance" / "datacontracts" / f"{CONTRACTS[family]}-roundtrip.yaml"
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
    from compliance.lib.docx_fill import (
        check_parity,
        fill_template,
        reextract_filled,
    )
    from compliance.lib.docx_jinjafier import jinjafy
    from compliance.scripts.fill_ssp_from_oscal import build_from_oscal

    jinja = output / "fillback" / "template.jinja.docx"
    narrative = output / "fillback" / "narrative.docx"
    filled = output / "fillback" / "filled.docx"
    jinja.parent.mkdir(exist_ok=True)
    injected = jinjafy(source, contract, jinja)
    result = fill_template(jinja, context, narrative, contract)
    fill = build_from_oscal(
        ssp,
        narrative,
        filled,
        output / "fillback" / "fill-manifest.json",
        strict_mapping=True,
    )
    parity = check_parity(context, reextract_filled(filled, contract, strict=True))
    return filled, injected, result, fill, parity


def compare_fillback(
    source: Path, ssp: Path, filled: Path, output: Path, decisions_path: Path | None
) -> tuple:
    readback = control_readback(json.loads(ssp.read_text()), filled, output)
    normalized = normalized_package(filled)
    write_json(output / "fillback" / "package-inventory.json", normalized["inventory"])
    write_json(output / "fillback" / "rendered-text.json", normalized["parts"])
    decisions = json.loads(decisions_path.read_text()) if decisions_path else []
    comparison = compare_packages(normalized_package(source), normalized, decisions)
    write_json(output / "compare" / "package-diff.json", comparison)
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


def verified_fill_context(context_path: Path, source: Path, output: Path) -> dict:
    """Accept values only when exact RFC6901 pointers resolve in this run's evidence."""
    from compliance.lib.docx_ir import LineageTracker

    envelope = json.loads(context_path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or set(envelope) != {
        "values",
        "source_sha256",
        "evidence",
    }:
        raise StageError(
            "BLOCKED", "fill context requires values, source_sha256 and evidence"
        )
    values, evidence = envelope["values"], envelope["evidence"]
    if (
        not isinstance(values, dict)
        or not isinstance(evidence, dict)
        or set(values) != set(evidence)
    ):
        raise StageError(
            "BLOCKED", "each fill value requires source/canonical evidence"
        )
    if envelope["source_sha256"] != digest(source.read_bytes()):
        raise StageError("BLOCKED", "fill context source hash differs")
    for tag, value in values.items():
        path = fill_evidence_path(evidence[tag], output)
        actual = LineageTracker._pointer_value(
            json.loads(path.read_text()), evidence[tag]["pointer"]
        )
        if not isinstance(value, str) or actual != value:
            raise StageError(
                "BLOCKED",
                "fill value does not match recorded source/canonical evidence",
            )
    return values


def fill_evidence_path(binding: dict, output: Path) -> Path:
    if not isinstance(binding, dict) or set(binding) != {"artifact", "pointer"}:
        raise StageError("BLOCKED", "invalid fill evidence binding")
    artifact = binding["artifact"]
    if (
        not isinstance(artifact, str)
        or Path(artifact).is_absolute()
        or ".." in Path(artifact).parts
    ):
        raise StageError("BLOCKED", "fill evidence must be run-relative")
    if artifact != "source/nodes.json" and not re.fullmatch(
        r"vault/[a-z0-9_-]+/\.snapshots/current/.+\.json", artifact
    ):
        raise StageError(
            "BLOCKED", "fill evidence must reference source IR or canonical sidecars"
        )
    path = (output / artifact).resolve()
    if output.resolve() not in path.parents or not path.is_file():
        raise StageError("BLOCKED", "fill evidence is missing or outside run")
    if not isinstance(binding["pointer"], str) or not binding["pointer"].startswith(
        "/"
    ):
        raise StageError("BLOCKED", "invalid evidence pointer")
    return path


def new_report(args: argparse.Namespace) -> dict:
    return {
        "schema_version": "1.0.0",
        "system_id": args.system_id,
        "template_family": args.template_family,
        "timestamp": timestamp(),
        "source_sha256": None,
        "layers": {
            name: {"status": "BLOCKED", "reason": "not executed"}
            for name in ("L0", "L1", "L2", "L3")
        },
    }


def execute_run(args: argparse.Namespace, output: Path) -> dict:
    from compliance.lib.docx_integrity_coverage import preserve_source_nodes

    report = new_report(args)
    current = "L0"
    source = args.source.expanduser().resolve()
    try:
        initialize_source(source, output, report)
        current = "L1"
        vault = output / "vault" / args.system_id
        coverage = preserve_source_nodes(source, vault)
        write_json(output / "compare" / "block-disposition.json", coverage)
        report["layers"]["L1"] = {
            "status": coverage["status"],
            "reason": coverage["review_note"],
        }
        current = "L2"
        ssp = export_preserving_evidence(source, vault, output, args)
        current = "L3"
        report["layers"]["L2"] = passed_layer(
            "Nonempty exports passed schema, semantic and canonical parity validation"
        )
        report["layers"]["L3"] = fillback_stage(
            source,
            ssp,
            output,
            args.template_family,
            args.fill_context,
            decisions_path=args.decisions,
        )
    except StageError as exc:
        report["layers"][current] = {"status": exc.status, "reason": str(exc)}
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        report["layers"][current] = {
            "status": "INCOMPLETE",
            "reason": "execution interrupted",
        }
    except ImportError:
        report["layers"][current] = {
            "status": "BLOCKED",
            "reason": "required local dependency unavailable",
        }
    except Exception as exc:
        report["layers"][current] = {
            "status": "FAIL",
            "reason": f"{type(exc).__name__} during {current}; no success inferred",
        }
    finally:
        finalize_run(source, output, report)
    return report


def initialize_source(source: Path, output: Path, report: dict) -> None:
    if not source.is_file():
        raise StageError("BLOCKED", "authorized source DOCX is unavailable")
    report["source_sha256"] = digest(source.read_bytes())
    (output / "source").mkdir(exist_ok=True)
    (output / "source/source-sha256.txt").write_text(report["source_sha256"] + "\n")
    package_stage(source, output)
    report["layers"]["L0"] = passed_layer(
        "Package closure, node accounting and managed artifact hashes verified"
    )


def passed_layer(reason: str) -> dict:
    return {"status": "PASS", "reason": reason}


def export_preserving_evidence(source: Path, vault: Path, output: Path, args) -> Path:
    from compliance.lib.docx_integrity_coverage import preserve_source_nodes

    retained = vault / "_source/nodes.json"
    backup = output / "source/nodes.json"
    backup.write_bytes(retained.read_bytes())
    try:
        ssp = export_stage(source, vault, output, args.system_id, args.timeout)
    finally:
        retained.parent.mkdir(parents=True, exist_ok=True)
        retained.write_bytes(backup.read_bytes())
    coverage = preserve_source_nodes(source, vault)
    write_json(output / "compare/block-disposition.json", coverage)
    if coverage["status"] != "PASS":
        raise StageError("FAIL", "source retention changed after export")
    return ssp


def finalize_run(source: Path, output: Path, report: dict) -> None:
    if report["source_sha256"] and not source.is_file():
        report["layers"]["L0"] = {
            "status": "FAIL",
            "reason": "source disappeared during run",
        }
    elif (
        report["source_sha256"]
        and digest(source.read_bytes()) != report["source_sha256"]
    ):
        report["layers"]["L0"] = {
            "status": "FAIL",
            "reason": "source hash changed during run",
        }
    with (output / "transcript.log").open("a", encoding="utf-8") as transcript:
        transcript.write(
            json.dumps(
                {
                    "timestamp": timestamp(),
                    "event": "layer-results",
                    "layers": report["layers"],
                }
            )
            + "\n"
        )
    write_report(output, report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--system-id", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--template-family", choices=FAMILIES, default="unknown")
    parser.add_argument("--fill-context", type=Path)
    parser.add_argument(
        "--decisions",
        type=Path,
        help="Hash-bound reviewed package delta decisions (JSON list)",
    )
    parser.add_argument(
        "--strict", action="store_true", help="Compatibility flag; all runs are strict"
    )
    parser.add_argument(
        "--authorized",
        action="store_true",
        help="Attest local processing of this source was authorized",
    )
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    if (
        not re.fullmatch(r"[a-z0-9_][a-z0-9_-]{0,100}", args.system_id)
        or args.timeout <= 0
    ):
        parser.error("invalid system ID or timeout")
    if not args.authorized:
        parser.error(
            "--authorized is required; source processing must be authorized by its owner"
        )
    old_mask = os.umask(0o077)
    try:
        output = prepare_output(args.output, args.source, args.workspace)
        with (output / "local-processing.log").open("w", encoding="utf-8") as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                report = execute_run(args, output)
    except (OSError, ValueError) as exc:
        print(
            f"BLOCKED: {type(exc).__name__}; choose a fresh approved ignored/output directory",
            file=sys.stderr,
        )
        return 2
    finally:
        os.umask(old_mask)
    print(
        f"{report['status']}: {args.system_id}; human review pending; {output / 'compare' / 'final-report.json'}"
    )
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "INCOMPLETE": 3}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
