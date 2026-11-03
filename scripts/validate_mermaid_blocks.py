#!/usr/bin/env python3
"""Parse every tracked Markdown Mermaid fence with the real Mermaid parser."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXCLUDED_PREFIXES = (
    "vendor/",
    "node_modules/",
    ".pytest_cache/",
    ".venv/",
)
FENCE = re.compile(r"^[ \t]*```mermaid[ \t]*$(.*?)^[ \t]*```[ \t]*$", re.M | re.S)

HARNESS = """
import { JSDOM } from 'jsdom';
import fs from 'node:fs';

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  pretendToBeVisual: true,
});
global.window = dom.window;
global.document = dom.window.document;
global.MutationObserver = dom.window.MutationObserver;
Object.defineProperty(global, 'navigator', {
  value: dom.window.navigator,
  configurable: true,
});

const mermaid = (await import('mermaid')).default;
mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });

const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const results = [];
for (const job of jobs) {
  try {
    await mermaid.parse(job.text);
    results.push({ id: job.id, ok: true });
  } catch (error) {
    results.push({
      id: job.id,
      ok: false,
      message: String(error?.message ?? error).split('\\n')[0],
    });
  }
}
fs.writeFileSync(process.argv[3], JSON.stringify(results));
"""


def candidate_files(paths: list[Path]) -> list[Path]:
    if paths:
        return [path for path in paths if path.suffix == ".md" and path.is_file()]
    tracked = subprocess.check_output(
        ["git", "-C", str(REPO), "ls-files", "*.md"], text=True
    ).splitlines()
    return [
        REPO / name
        for name in tracked
        if not name.startswith(EXCLUDED_PREFIXES) and (REPO / name).is_file()
    ]


def collect_blocks(files: list[Path]) -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    for path in files:
        for index, match in enumerate(
            FENCE.finditer(path.read_text(encoding="utf-8")), start=1
        ):
            relative = path.relative_to(REPO) if path.is_relative_to(REPO) else path
            jobs.append(
                {
                    "id": f"{relative}#mermaid-{index}",
                    "text": match.group(1).strip("\n"),
                }
            )
    return jobs


def node_modules() -> Path | None:
    modules = REPO / "node_modules"
    if (modules / "mermaid").is_dir() and (modules / "jsdom").is_dir():
        return modules
    return None


def run_parser(jobs: list[dict[str, str]], modules: Path) -> list[dict]:
    with tempfile.TemporaryDirectory() as work_name:
        work = Path(work_name)
        (work / "check.mjs").write_text(HARNESS, encoding="utf-8")
        (work / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        (work / "node_modules").symlink_to(modules, target_is_directory=True)
        (work / "package.json").write_text('{"type":"module"}', encoding="utf-8")
        results = work / "results.json"
        completed = subprocess.run(
            ["node", "check.mjs", "jobs.json", "results.json"],
            cwd=work,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not results.is_file():
            raise RuntimeError(
                "mermaid parser harness failed:\n"
                + (completed.stderr or completed.stdout).strip()
            )
        return json.loads(results.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    if shutil.which("node") is None:
        print("FAIL - node is required to validate Mermaid blocks")
        return 2
    modules = node_modules()
    if modules is None:
        print("FAIL - run npm ci to install pinned mermaid and jsdom dependencies")
        return 2
    files = candidate_files([path.resolve() for path in args.paths])
    jobs = collect_blocks(files)
    results = run_parser(jobs, modules)
    failures = [result for result in results if not result["ok"]]
    for failure in failures:
        print(f"FAIL {failure['id']}: {failure.get('message', 'parse error')}")
    if failures:
        print(f"\n{len(failures)} of {len(jobs)} Mermaid block(s) failed")
        return 1
    print(f"OK - {len(jobs)} Mermaid block(s) parsed across {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
