#!/usr/bin/env python3
"""Render, and optionally file, one tracked issue per POA&M-required control.

Reads an assessment JSON (from build_gsa_assessment.py) and a work-items YAML that the system
team curates. Dry run by default: prints titles. ``--show CONTROL`` prints one body.
``--create`` files missing issues with the GitHub CLI and writes ``--issue-map`` (control ->
issue number) so the next assessment run can link them. Nothing is filed without ``--create``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.lib.gsa_assessment_report import redaction_hits  # noqa: E402
from compliance.lib.gsa_poam_issues import (  # noqa: E402
    DEFAULT_TITLE_PREFIX,
    WorkItemsError,
    render_all,
)


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    ).stdout


def _existing(prefix: str, label: str) -> dict[str, int]:
    rows = json.loads(
        _gh(
            "issue",
            "list",
            "--state",
            "all",
            "--label",
            label,
            "--limit",
            "500",
            "--json",
            "number,title",
        )
    )
    found: dict[str, int] = {}
    for row in rows:
        title = row["title"]
        if title.startswith(prefix):
            found[title[len(prefix) :].split()[0]] = row["number"]
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--work-items", type=Path, required=True)
    parser.add_argument("--show", metavar="CONTROL")
    parser.add_argument(
        "--create", action="store_true", help="File missing issues with gh"
    )
    parser.add_argument(
        "--issue-map", type=Path, help="YAML written with control -> issue number"
    )
    parser.add_argument("--title-prefix", default=DEFAULT_TITLE_PREFIX)
    parser.add_argument(
        "--label", action="append", default=[], help="Issue label (repeatable)"
    )
    args = parser.parse_args(argv)
    try:
        assessment = json.loads(args.assessment.read_text(encoding="utf-8"))
        work_items = yaml.safe_load(args.work_items.read_text(encoding="utf-8"))
        rendered = render_all(assessment, work_items, args.title_prefix)
    except (WorkItemsError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    hits = redaction_hits(*[t + b for t, b in rendered.values()])
    if hits:
        print(f"ERROR: redaction gate violations: {hits}", file=sys.stderr)
        return 1
    if args.show:
        if args.show not in rendered:
            print(f"ERROR: {args.show} is not POA&M-required", file=sys.stderr)
            return 2
        title, body = rendered[args.show]
        print(f"# {title}\n\n{body}")
        return 0
    if not args.create:
        for title, _ in rendered.values():
            print(title)
        print(f"{len(rendered)} issues would be filed (use --create).")
        return 0
    if not args.label:
        print(
            "ERROR: --create needs at least one --label (the label also finds existing issues)",
            file=sys.stderr,
        )
        return 2
    have = _existing(args.title_prefix, args.label[0])
    issues: dict[str, int] = {}
    label_args = [x for lab in args.label for x in ("--label", lab)]
    for cid, (title, body) in rendered.items():
        if cid in have:
            issues[cid] = have[cid]
            print(f"exists  #{have[cid]} {cid}")
            continue
        url = _gh(
            "issue", "create", "--title", title, "--body", body, *label_args
        ).strip()
        issues[cid] = int(url.rsplit("/", 1)[1])
        print(f"created #{issues[cid]} {cid}")
    if args.issue_map:
        args.issue_map.write_text(
            yaml.safe_dump({"issues": dict(sorted(issues.items()))}, sort_keys=False),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
