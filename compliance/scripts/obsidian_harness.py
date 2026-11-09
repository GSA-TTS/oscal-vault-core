#!/usr/bin/env python3
"""Run safe DuckDB audit blocks from Markdown notes against local evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Workspace root that evidence, suites, and callout output must stay beneath.
# Defaults to the current directory; override with --root or this variable.
ROOT = Path(os.environ.get("OSCAL_VAULT_AUDIT_ROOT", Path.cwd())).resolve()
BLOCK_RE = re.compile(
    r"```(?P<language>sql|duckdb)\s*\n(?P<body>.*?)^```", re.MULTILINE | re.DOTALL
)
DIRECTIVE_RE = re.compile(
    r"^\s*--\s*@(?P<name>evidence|assert|control):\s*(?P<value>.+?)\s*$", re.MULTILINE
)
DISALLOWED_SQL_RE = re.compile(
    r"\b(?:ATTACH|COPY|CREATE|DELETE|DROP|EXPORT|INSERT|INSTALL|LOAD|UPDATE|VACUUM)\b",
    re.IGNORECASE,
)
EVIDENCE_CALL_RE = re.compile(
    r"\bread_(?:csv_auto|json_auto|parquet)\s*\(\s*'(?P<path>[^']+)'",
    re.IGNORECASE,
)
ROBOT_MD_BLOCK_RE = re.compile(
    r"```(?P<language>robot)\s*\n(?P<body>.*?)^```", re.MULTILINE | re.DOTALL
)
ROBOT_DIRECTIVE_RE = re.compile(
    r"^\s*#\s*@(?P<name>suite|control):\s*(?P<value>.+?)\s*$", re.MULTILINE
)
INSPEC_MD_BLOCK_RE = re.compile(
    r"```(?P<language>inspec)\s*\n(?P<body>.*?)^```", re.MULTILINE | re.DOTALL
)
INSPEC_DIRECTIVE_RE = re.compile(
    r"^\s*#\s*@(?P<name>evidence|assert|control):\s*(?P<value>.+?)\s*$", re.MULTILINE
)
INSPEC_ASSERT_RE = re.compile(
    r"^file_contains\s+(?P<needle>'[^']+'|\"[^\"]+\")\s*(?P<op>==)\s*(?P<expected>true|false)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AuditBlock:
    path: Path
    line: int
    sql: str
    evidence: str
    assertion: str
    control: str | None


@dataclass(frozen=True)
class BlockResult:
    path: str
    line: int
    control: str | None
    assertion: str
    rows: list[dict[str, Any]]
    passed: bool


@dataclass(frozen=True)
class RobotResult:
    path: str
    passed: int
    failed: int
    skipped: int
    control: str | None = None


@dataclass(frozen=True)
class InspecBlock:
    path: Path
    line: int
    evidence: str
    assertion: str
    control: str | None


@dataclass(frozen=True)
class InspecResult:
    path: str
    line: int
    control: str | None
    evidence: str
    assertion: str
    passed: bool


def _repo_relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _directives(body: str) -> dict[str, str]:
    return {match["name"]: match["value"] for match in DIRECTIVE_RE.finditer(body)}


def detect_blocks(path: Path) -> list[AuditBlock]:
    text = path.read_text(encoding="utf-8")
    blocks: list[AuditBlock] = []
    for match in BLOCK_RE.finditer(text):
        directives = _directives(match["body"])
        if "evidence" not in directives or "assert" not in directives:
            continue
        line = text.count("\n", 0, match.start()) + 1
        sql = DIRECTIVE_RE.sub("", match["body"]).strip()
        blocks.append(
            AuditBlock(
                path=path,
                line=line,
                sql=sql,
                evidence=directives["evidence"],
                assertion=directives["assert"],
                control=directives.get("control"),
            )
        )
    return blocks


def detect_robot_directives(path: Path) -> list[tuple[str, str | None]]:
    text = path.read_text(encoding="utf-8")
    suites: list[tuple[str, str | None]] = []
    for match in ROBOT_MD_BLOCK_RE.finditer(text):
        directives = {
            m["name"]: m["value"] for m in ROBOT_DIRECTIVE_RE.finditer(match["body"])
        }
        if "suite" in directives:
            suites.append((directives["suite"], directives.get("control")))
    return suites


def detect_inspec_blocks(path: Path) -> list[InspecBlock]:
    text = path.read_text(encoding="utf-8")
    blocks: list[InspecBlock] = []
    for match in INSPEC_MD_BLOCK_RE.finditer(text):
        directives = {
            m["name"]: m["value"] for m in INSPEC_DIRECTIVE_RE.finditer(match["body"])
        }
        if "evidence" not in directives or "assert" not in directives:
            continue
        line = text.count("\n", 0, match.start()) + 1
        blocks.append(
            InspecBlock(
                path=path,
                line=line,
                evidence=directives["evidence"],
                assertion=directives["assert"],
                control=directives.get("control"),
            )
        )
    return blocks


def run_inspec_block(block: InspecBlock, evidence_root: Path) -> InspecResult:
    evidence_file = _resolve_evidence(block.evidence, evidence_root)
    match = INSPEC_ASSERT_RE.fullmatch(block.assertion.strip())
    if not match:
        raise ValueError(
            "inspec assertion must be \"file_contains '<substring>' == true|false\""
        )
    needle = match["needle"][1:-1]
    expected = match["expected"].lower() == "true"
    content = evidence_file.read_text(encoding="utf-8")
    passed = (needle in content) == expected
    return InspecResult(
        path=_repo_relative(block.path),
        line=block.line,
        control=block.control,
        evidence=block.evidence,
        assertion=block.assertion,
        passed=passed,
    )


def _resolve_evidence(reference: str, evidence_root: Path) -> Path:
    candidate = (ROOT / reference).resolve()
    root = evidence_root.resolve()
    if root not in candidate.parents:
        raise ValueError(
            f"evidence path must be beneath {_repo_relative(root)}: {reference}"
        )
    if not candidate.is_file():
        raise ValueError(f"evidence file does not exist: {reference}")
    return candidate


def _assertion(result: dict[str, Any], expression: str) -> bool:
    match = re.fullmatch(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*(==|!=|>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)", expression
    )
    if not match:
        raise ValueError("assertion must be '<column> <operator> <number>'")
    column, operator, expected_text = match.groups()
    if column not in result:
        raise ValueError(f"assertion column is absent from result: {column}")
    actual = result[column]
    if not isinstance(actual, (int, float)):
        raise TypeError(f"assertion column must be numeric: {column}")
    expected = float(expected_text)
    return {
        "==": actual == expected,
        "!=": actual != expected,
        ">=": actual >= expected,
        "<=": actual <= expected,
        ">": actual > expected,
        "<": actual < expected,
    }[operator]


def run_block(block: AuditBlock, evidence_root: Path) -> BlockResult:
    if DISALLOWED_SQL_RE.search(block.sql):
        raise ValueError("audit SQL must be read-only and may not install extensions")
    # Raises if the path escapes the evidence root.
    evidence_file = _resolve_evidence(block.evidence, evidence_root)
    if block.evidence not in block.sql:
        raise ValueError("audit SQL must reference the @evidence file exactly")
    query_paths = [match["path"] for match in EVIDENCE_CALL_RE.finditer(block.sql)]
    if not query_paths or any(path != block.evidence for path in query_paths):
        raise ValueError("audit SQL may only read the declared @evidence file")

    try:
        import duckdb
    except ImportError as exc:
        raise ValueError(
            "DuckDB blocks require the optional 'duckdb' package (pip install duckdb)"
        ) from exc

    # Run against the validated absolute path so results do not depend on the CWD.
    sql = block.sql.replace(
        f"'{block.evidence}'", "'" + str(evidence_file).replace("'", "''") + "'"
    )
    connection = duckdb.connect(":memory:")
    try:
        cursor = connection.execute(sql)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()
    if len(rows) != 1:
        raise ValueError("audit SQL must return exactly one summary row")
    return BlockResult(
        path=_repo_relative(block.path),
        line=block.line,
        control=block.control,
        assertion=block.assertion,
        rows=rows,
        passed=_assertion(rows[0], block.assertion),
    )


def render_callouts(
    results: list[BlockResult],
    inspec_results: list[InspecResult] | None = None,
    robot_results: list[RobotResult] | None = None,
) -> str:
    lines = ["# Obsidian Audit Results", ""]
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        lines.extend(
            [
                f"> [!check] {status}",
                f"> Source: `{result.path}:{result.line}`",
                f"> Control: `{result.control or 'not specified'}`",
                f"> Assertion: `{result.assertion}`",
                f"> Result: `{json.dumps(result.rows[0], sort_keys=True, default=str)}`",
                "",
            ]
        )
    for inspec in inspec_results or []:
        status = "PASS" if inspec.passed else "FAIL"
        lines.extend(
            [
                f"> [!check] {status}",
                f"> Source: `{inspec.path}:{inspec.line}`",
                f"> Control: `{inspec.control or 'not specified'}`",
                f"> Assertion: `{inspec.assertion}`",
                f"> Evidence: `{inspec.evidence}`",
                "",
            ]
        )
    for robot in robot_results or []:
        status = "PASS" if robot.failed == 0 else "FAIL"
        lines.extend(
            [
                f"> [!check] {status}",
                f"> Suite: `{robot.path}`",
                f"> Control: `{robot.control or 'not specified'}`",
                f"> Result: `passed={robot.passed} failed={robot.failed} skipped={robot.skipped}`",
                "",
            ]
        )
    return "\n".join(lines)


def _resolve_output(path: Path) -> Path:
    resolved = path.resolve()
    if ROOT not in resolved.parents:
        raise ValueError("callout output must be beneath the repository root")
    return resolved


def run_robot_suite(
    suite: Path, suite_root: Path, control: str | None = None
) -> RobotResult:
    resolved_suite = suite.resolve()
    resolved_root = suite_root.resolve()
    if resolved_root not in resolved_suite.parents or resolved_suite.suffix != ".robot":
        raise ValueError(
            f"Robot suite must be a .robot file beneath {_repo_relative(resolved_root)}"
        )
    if not resolved_suite.is_file():
        raise ValueError(
            f"Robot suite does not exist: {_repo_relative(resolved_suite)}"
        )

    with tempfile.TemporaryDirectory(prefix="obsidian-robot-") as output_dir:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "robot",
                "--outputdir",
                output_dir,
                str(resolved_suite),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        output = Path(output_dir) / "output.xml"
        if not output.is_file():
            raise ValueError(
                f"Robot did not produce output.xml: {completed.stderr.strip()}"
            )
        statistics = ET.parse(output).find(".//statistics/total/stat")
        if statistics is None:
            raise ValueError("Robot output is missing total statistics")
        result = RobotResult(
            path=_repo_relative(resolved_suite),
            passed=int(statistics.attrib["pass"]),
            failed=int(statistics.attrib["fail"]),
            skipped=int(statistics.attrib.get("skip", 0)),
            control=control,
        )
    if completed.returncode not in (0, 1):
        raise ValueError(f"Robot execution failed: {completed.stderr.strip()}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "notes", nargs="*", type=Path, help="Markdown notes to evaluate"
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=None,
        help="Workspace-relative directory containing permitted local evidence",
    )
    parser.add_argument(
        "--callout-out",
        type=Path,
        help="Write standalone Obsidian check callouts; source notes are never modified",
    )
    parser.add_argument(
        "--robot",
        type=Path,
        action="append",
        default=[],
        help="Run an explicit synthetic Robot suite beneath --robot-root",
    )
    parser.add_argument(
        "--robot-root",
        type=Path,
        default=None,
        help="Workspace-relative directory containing permitted synthetic Robot suites",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: $OSCAL_VAULT_AUDIT_ROOT or the current directory)",
    )
    args = parser.parse_args()
    global ROOT  # noqa: PLW0603
    if args.root:
        ROOT = args.root.resolve()
    if args.evidence_root is None:
        args.evidence_root = ROOT / "evidence"
    if args.robot_root is None:
        args.robot_root = ROOT / "evidence" / "robot"
    if not args.notes and not args.robot:
        parser.error("provide at least one Markdown note or --robot suite")

    results: list[BlockResult] = []
    inspec_results: list[InspecResult] = []
    robot_results: list[RobotResult] = []
    try:
        for note in args.notes:
            resolved_note = note.resolve()
            for block in detect_blocks(resolved_note):
                results.append(run_block(block, args.evidence_root))
            for inspec_block in detect_inspec_blocks(resolved_note):
                inspec_results.append(
                    run_inspec_block(inspec_block, args.evidence_root)
                )
            for suite_ref, control in detect_robot_directives(resolved_note):
                robot_results.append(
                    run_robot_suite(ROOT / suite_ref, args.robot_root, control=control)
                )
        for suite in args.robot:
            robot_results.append(run_robot_suite(suite, args.robot_root))
    except (OSError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.callout_out:
        output = _resolve_output(args.callout_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            render_callouts(results, inspec_results, robot_results), encoding="utf-8"
        )
        print(f"Wrote {output.relative_to(ROOT)}")
    else:
        print(
            json.dumps(
                {
                    "duckdb": [asdict(result) for result in results],
                    "inspec": [asdict(result) for result in inspec_results],
                    "robot": [asdict(result) for result in robot_results],
                },
                indent=2,
                default=str,
            )
        )
    return (
        0
        if all(result.passed for result in results)
        and all(result.passed for result in inspec_results)
        and all(result.failed == 0 for result in robot_results)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
