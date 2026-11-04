#!/usr/bin/env python3
"""Refuse changes that add system content to the upstream core.

Modes (exactly one):
  --git-range BASE..HEAD   diff two revisions in this checkout (CI, pre-push)
  --staged                 the index (pre-commit)
  --tracked                every tracked file (audit the current tree)
  --github-pr N            the files of pull request N via the REST API (pull_request_target;
                           needs GITHUB_REPOSITORY and GITHUB_TOKEN; never checks out PR code)

The blank-template allowlist is read from this checkout, which in CI is the *base* branch, so a
pull request cannot widen its own allowlist. Standard library only. See ADR-028.
Exit 0 clean, 1 violations, 2 usage or runtime error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from compliance.lib.system_content_guard import (  # noqa: E402
    Entry,
    check_entries,
    entries_from_github_files,
    load_allowlist,
    load_allowlisted_paths,
)

_ALLOWLIST = _ROOT / "compliance" / "data" / "blank_template_allowlist.json"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _blob(name: str, rev: str | None, cwd: Path) -> str | None:
    spec = f"{rev}:{name}" if rev else f":{name}"
    result = subprocess.run(
        ["git", "rev-parse", spec], cwd=cwd, capture_output=True, text=True
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def entries_from_git(repo: Path, diff_args: list[str], rev: str | None) -> list[Entry]:
    raw = _git("diff", "--name-status", "-z", "--find-renames", *diff_args, cwd=repo)
    tokens = raw.split("\0")
    entries: list[Entry] = []
    i = 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i]
        if status[0] in "RC":
            name, i = tokens[i + 2], i + 3
        else:
            name, i = tokens[i + 1], i + 2
        kind = {"A": "added", "D": "removed", "R": "renamed"}.get(status[0], "modified")
        patch = None
        if kind != "removed":
            patch = _git("diff", "-U0", *diff_args, "--", name, cwd=repo)
        entries.append(
            Entry(path=name, status=kind, blob_sha=_blob(name, rev, repo), patch=patch)
        )
    return entries


def entries_from_tracked(repo: Path) -> list[Entry]:
    out = []
    for name in _git("ls-files", "-z", cwd=repo).split("\0"):
        if not name:
            continue
        full = repo / name
        text = None
        if full.suffix.lower() in {".md", ".yaml", ".yml"} and full.is_file():
            text = full.read_text(encoding="utf-8", errors="replace")
        out.append(
            Entry(
                path=name, status="added", blob_sha=_blob(name, "HEAD", repo), text=text
            )
        )
    return out


def entries_from_pr(number: int) -> list[Entry]:
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    files: list[dict] = []
    page = 1
    while True:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/pulls/{number}/files?per_page=100&page={page}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https host
            batch = json.load(resp)
        files.extend(batch)
        if len(batch) < 100:
            return entries_from_github_files(files)
        page += 1
        if (
            page > 30
        ):  # the API stops at 3000 files; fail closed rather than pass a truncated list
            raise RuntimeError(
                "pull request lists 3000+ files; refusing to pass an incomplete list"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--git-range")
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--tracked", action="store_true")
    mode.add_argument("--github-pr", type=int)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="git checkout to inspect (default: cwd)",
    )
    parser.add_argument("--allowlist", type=Path, default=_ALLOWLIST)
    args = parser.parse_args(argv)

    try:
        allowed = load_allowlist(args.allowlist)
        allowed_paths = load_allowlisted_paths(args.allowlist)
        if args.git_range:
            rev = args.git_range.split("..")[-1] or "HEAD"
            entries = entries_from_git(args.repo, [args.git_range], rev)
        elif args.staged:
            entries = entries_from_git(args.repo, ["--cached"], None)
        elif args.tracked:
            entries = entries_from_tracked(args.repo)
        else:
            entries = entries_from_pr(args.github_pr)
    except (
        OSError,
        subprocess.CalledProcessError,
        KeyError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"ERROR: could not inspect changes: {exc}", file=sys.stderr)
        return 2  # fail closed: an unreadable change set must not look clean

    violations = check_entries(entries, allowed, allowed_paths)
    print(f"checked {len(entries)} changed file(s)")
    for violation in violations:
        print(f"  VIOLATION {violation}")
    if violations:
        print(
            f"\n{len(violations)} violation(s). This repository holds no system content; vendor the "
            "core from a system repository instead (see CONTRIBUTING.md and ADR-028).",
            file=sys.stderr,
        )
        return 1
    print("OK - no system content detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
