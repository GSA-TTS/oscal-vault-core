#!/usr/bin/env python3
"""Build portable JSON and standalone HTML review reports from canonical sidecars."""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any


def build_report(vault: Path) -> dict[str, Any]:
    snapshot = vault / ".snapshots" / "current"
    notes: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    for sidecar in sorted(snapshot.rglob("*.json")):
        document = json.loads(sidecar.read_text(encoding="utf-8"))
        tables = document.get("table_records") or []
        instructions = document.get("instruction_sources") or []
        references = document.get("typed_references") or []
        if not tables and not instructions and not references:
            continue
        table_counts: Counter[str] = Counter()
        for table in tables:
            table_counts[f"classification:{table.get('classification', 'opaque')}"] += 1
            table_counts[f"kind:{table.get('kind', 'opaque')}"] += 1
            table_counts[f"disposition:{table.get('disposition', 'unmapped')}"] += 1
        totals.update(table_counts)
        totals["notes"] += 1
        totals["tables"] += len(tables)
        totals["instructions"] += len(instructions)
        totals["references"] += len(references)
        notes.append(
            {
                "path": str(document.get("_meta", {}).get("generated_from") or sidecar),
                "type": document.get("type") or document.get("note_type"),
                "title": document.get("title") or document.get("name"),
                "table_counts": dict(sorted(table_counts.items())),
                "tables": tables,
                "instruction_sources": instructions,
                "typed_references": references,
            }
        )
    return {
        "schema_version": 1,
        "vault": vault.name,
        "summary": dict(sorted(totals.items())),
        "notes": notes,
    }


def render_html(report: dict[str, Any]) -> str:
    summary = "".join(
        f"<dt>{html.escape(key)}</dt><dd>{value}</dd>"
        for key, value in report["summary"].items()
    )
    rows = []
    for note in report["notes"]:
        counts = ", ".join(
            f"{key}={value}" for key, value in note["table_counts"].items()
        )
        instructions = "<br>".join(
            html.escape(source["source_locator"])
            for source in note["instruction_sources"]
        )
        references = "<br>".join(
            html.escape(
                f"{reference['relation']}: {reference['type']} {reference['id']}"
            )
            for reference in note["typed_references"]
        )
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(note['path'])}</code></td>"
            f"<td>{html.escape(str(note['type'] or ''))}</td>"
            f"<td>{html.escape(str(note['title'] or ''))}</td>"
            f"<td>{html.escape(counts)}</td>"
            f"<td>{instructions}</td>"
            f"<td>{references}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(report["vault"])} canonical review</title>
<style>body{{font:15px system-ui;margin:2rem;color:#172033}}h1{{font-size:1.6rem}}dl{{display:grid;grid-template-columns:max-content max-content;gap:.25rem 1rem}}dt{{font-weight:700}}dd{{margin:0}}table{{border-collapse:collapse;width:100%;margin-top:2rem}}th,td{{border:1px solid #ccd3df;padding:.55rem;text-align:left;vertical-align:top}}th{{background:#eef2f7}}code{{font-size:.85em}}</style></head>
<body><h1>{html.escape(report["vault"])} canonical review</h1><dl>{summary}</dl>
 <table><thead><tr><th>Path</th><th>Type</th><th>Title</th><th>Typed tables</th><th>Instruction provenance</th><th>Typed references</th></tr></thead><tbody>{"".join(rows)}</tbody></table></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--json", dest="json_path", required=True, type=Path)
    parser.add_argument("--html", dest="html_path", required=True, type=Path)
    args = parser.parse_args()
    report = build_report(args.vault.resolve())
    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    args.html_path.parent.mkdir(parents=True, exist_ok=True)
    args.json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    args.html_path.write_text(render_html(report), encoding="utf-8")
    print(
        f"Wrote reviewer report for {report['vault']}: "
        f"{args.json_path}, {args.html_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
