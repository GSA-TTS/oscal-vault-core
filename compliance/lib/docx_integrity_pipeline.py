"""Read-only source pipeline orchestration with fail-closed local evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from compliance.lib.docx_integrity_compare import digest, normalized_package
from compliance.lib.docx_integrity_fillback import fillback_stage
from compliance.lib.docx_integrity_report import (
    StageError,
    timestamp,
    write_json,
    write_report,
)

CORE = Path(__file__).resolve().parents[2]


def prepare_output(output: Path, source: Path, workspace: Path) -> Path:
    output, source, workspace = (
        path.expanduser().resolve() for path in (output, source, workspace)
    )
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
    command = [sys.executable, str(CORE / "compliance/scripts" / args[0]), *args[1:]]
    log = output / "logs" / f"{stage}.log"
    log.parent.mkdir(exist_ok=True)
    started, status, returncode = timestamp(), "PASS", None
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
    from compliance.lib.docx_package import strict_findings

    package = normalized_package(source)
    write_json(output / "source/package-inventory.json", package["inventory"])
    write_json(output / "source/normalized-content.json", package["parts"])
    report = collect_opaque_artifacts(source)
    artifacts = extract_managed_artifacts(source, output / "source")
    manifest = write_source_manifest(report, output / "source", artifacts)
    write_json(output / "source/source-manifest.json", json.loads(manifest.read_text()))
    findings = (
        report.loss_budget_findings()
        + verify_managed_artifacts(artifacts, output / "source")
        + strict_findings(package["inventory"])
    )
    write_json(output / "compare/package-accounting.json", {"findings": findings})
    if findings:
        raise StageError(
            "FAIL", "L0 package/node accounting failed; see package-accounting.json"
        )
    return package


def export_stage(
    source: Path, vault: Path, output: Path, system_id: str, timeout: int
) -> Path:
    for stage, args in export_commands(source, vault, output, system_id):
        run_command(output, stage, args, timeout)
        if stage == "canonical" and not list(
            (vault / ".snapshots/current").rglob("*.json")
        ):
            raise StageError("BLOCKED", "canonical generator emitted no sidecars")
        if stage == "export" and not list(
            (vault / ".exports/oscal-json").glob("*.json")
        ):
            raise StageError("BLOCKED", "exporter emitted no OSCAL documents")
    ssps = [
        path
        for path in (vault / ".exports/oscal-json").glob("*.json")
        if "system-security-plan" in json.loads(path.read_text())
    ]
    if len(ssps) != 1:
        raise StageError("BLOCKED", "expected exactly one exported SSP")
    write_json(
        output / "oscal/parity-report.json",
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
                str(output / "oscal/export-report.json"),
            ],
        ),
        (
            "parity",
            ["validate_oscal.py", "--vault", str(vault), "--semantic", "--parity"],
        ),
    )


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

    report, current = new_report(args), "L0"
    source = args.source.expanduser().resolve()
    try:
        initialize_source(source, output, report)
        current = "L1"
        vault = output / "vault" / args.system_id
        coverage = preserve_source_nodes(source, vault)
        write_json(output / "compare/block-disposition.json", coverage)
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

    retained, backup = vault / "_source/nodes.json", output / "source/nodes.json"
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
