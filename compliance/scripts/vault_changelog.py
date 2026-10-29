#!/usr/bin/env python3
"""Pin-advance change summary for consumers vendoring oscal-vault as a submodule.

A downstream consumer that pins ``vendor/oscal-vault`` to a git ref and
advances that pin needs to know, in one glance, whether the advance changed
anything their tooling depends on: control counts per system, ODP
(organization-defined-parameter) wording, or system membership under
``examples/vault/``. This is OPEN_WORK 6.4 in the SSPP project's tracker
(and Need 6 in ``docs/plans/agent-handoff-oscal-vault.yaml`` in that repo) —
a generic diff tool any submodule consumer of this repo needs, not
SSPP-specific, so it lives here rather than being re-implemented downstream.

Compares two git refs *within this repository* (not a submodule checkout —
run this in the oscal-vault repo itself, before or after advancing a
consumer's pin, to generate the note that goes in the consumer's pin-bump
PR) and reports:

  - systems added/removed under ``examples/vault/``
  - per-system control-note count changes
  - ODP (``parameters[].value``) wording changes, by control + param_id
  - Template-guidance callout coverage changes (see
    ``check_template_guidance_invariant`` in ``scripts/validate_vault.py``)

Read-only: never mutates the working tree or the refs being compared. Each
ref is extracted once into a temporary directory via ``git archive`` (not
one ``git show`` per file, which does not scale to 5000+ control notes).

Usage:
    python3 compliance/scripts/vault_changelog.py --from <ref> --to <ref>
    python3 compliance/scripts/vault_changelog.py --from ef7a1021 --to HEAD
    python3 compliance/scripts/vault_changelog.py --from v1.2.0 --to v1.3.0 --out CHANGELOG-slice.md
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

_CORE_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE = Path.cwd()
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _git(args: List[str]) -> str:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=str(_WORKSPACE)
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _resolve(ref: str) -> str:
    return _git(["rev-parse", ref]).strip()


def _checkout_tree(ref: str, dest: Path) -> None:
    """Extract examples/vault/ at `ref` into `dest` via `git archive`."""
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(
        ["git", "archive", ref, "examples/vault/"],
        stdout=subprocess.PIPE,
        cwd=str(_WORKSPACE),
    )
    extract = subprocess.Popen(["tar", "-x", "-C", str(dest)], stdin=archive.stdout)
    assert archive.stdout is not None
    archive.stdout.close()
    extract.communicate()
    archive.wait()
    if archive.returncode != 0 or extract.returncode != 0:
        raise RuntimeError(f"failed to extract examples/vault/ at {ref}")


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _vault_systems(tree: Path) -> set[str]:
    root = tree / "examples" / "vault"
    if not root.is_dir():
        return set()
    return {d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")}


def _control_files(tree: Path) -> List[Path]:
    root = tree / "examples" / "vault"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/Controls/*.md"))


def _control_counts(tree: Path) -> Dict[str, int]:
    """system -> number of Controls/*.md notes."""
    counts: Dict[str, int] = {}
    for p in _control_files(tree):
        system = p.parent.parent.name
        counts[system] = counts.get(system, 0) + 1
    return counts


def _guidance_counts(tree: Path) -> Dict[str, Tuple[int, int]]:
    """system -> (total control notes, notes with 'Template guidance')."""
    totals: Dict[str, int] = {}
    with_guidance: Dict[str, int] = {}
    for p in _control_files(tree):
        system = p.parent.parent.name
        totals[system] = totals.get(system, 0) + 1
        text = p.read_text(encoding="utf-8", errors="replace")
        if "Template guidance" in text:
            with_guidance[system] = with_guidance.get(system, 0) + 1
    return {s: (totals[s], with_guidance.get(s, 0)) for s in totals}


def _odp_values(tree: Path) -> Dict[str, Dict[str, str]]:
    """ "{system}::{control_id}" -> {param_id: value}."""
    result: Dict[str, Dict[str, str]] = {}
    for p in _control_files(tree):
        system = p.parent.parent.name
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text)
        control_id = fm.get("control_id")
        params = fm.get("parameters") or []
        if not control_id or not isinstance(params, list):
            continue
        key = f"{system}::{control_id}"
        values: Dict[str, str] = {}
        for entry in params:
            if isinstance(entry, dict) and "param_id" in entry:
                values[str(entry["param_id"])] = str(entry.get("value", ""))
        if values:
            result[key] = values
    return result


def build_changelog(from_ref: str, to_ref: str) -> str:
    from_sha = _resolve(from_ref)
    to_sha = _resolve(to_ref)

    with tempfile.TemporaryDirectory(prefix="vault-changelog-") as tmp:
        tmp_path = Path(tmp)
        from_tree = tmp_path / "from"
        to_tree = tmp_path / "to"
        _checkout_tree(from_ref, from_tree)
        _checkout_tree(to_ref, to_tree)

        from_systems = _vault_systems(from_tree)
        to_systems = _vault_systems(to_tree)
        added_systems = sorted(to_systems - from_systems)
        removed_systems = sorted(from_systems - to_systems)

        from_counts = _control_counts(from_tree)
        to_counts = _control_counts(to_tree)
        count_changes = []
        for system in sorted(set(from_counts) | set(to_counts)):
            before = from_counts.get(system, 0)
            after = to_counts.get(system, 0)
            if before != after:
                count_changes.append((system, before, after))

        from_guidance = _guidance_counts(from_tree)
        to_guidance = _guidance_counts(to_tree)
        guidance_changes = []
        for system in sorted(set(from_guidance) | set(to_guidance)):
            before = from_guidance.get(system)
            after = to_guidance.get(system)
            if before != after:
                guidance_changes.append((system, before, after))

        from_odp = _odp_values(from_tree)
        to_odp = _odp_values(to_tree)
        odp_changes = []
        for key in sorted(set(from_odp) | set(to_odp)):
            before = from_odp.get(key, {})
            after = to_odp.get(key, {})
            for param_id in sorted(set(before) | set(after)):
                bv = before.get(param_id)
                av = after.get(param_id)
                if bv != av:
                    odp_changes.append((key, param_id, bv, av))

    lines = [
        "# oscal-vault pin-advance change summary",
        "",
        f"- From: `{from_ref}` ({from_sha[:12]})",
        f"- To: `{to_ref}` ({to_sha[:12]})",
        "",
        "## Systems",
        "",
    ]
    if added_systems:
        lines.append("**Added:**")
        lines.extend(f"- `{s}`" for s in added_systems)
        lines.append("")
    if removed_systems:
        lines.append("**Removed:**")
        lines.extend(f"- `{s}`" for s in removed_systems)
        lines.append("")
    if not added_systems and not removed_systems:
        lines.append("_No system additions or removals._")
        lines.append("")

    lines.append("## Control-count changes")
    lines.append("")
    if count_changes:
        lines.append("| System | Before | After |")
        lines.append("| --- | --- | --- |")
        for system, before, after in count_changes:
            lines.append(f"| `{system}` | {before} | {after} |")
    else:
        lines.append("_No control-count changes._")
    lines.append("")

    lines.append("## Template-guidance coverage changes")
    lines.append("")
    if guidance_changes:
        lines.append("| System | Before (with/total) | After (with/total) |")
        lines.append("| --- | --- | --- |")
        for system, before, after in guidance_changes:
            b = f"{before[1]}/{before[0]}" if before else "—"
            a = f"{after[1]}/{after[0]}" if after else "—"
            lines.append(f"| `{system}` | {b} | {a} |")
    else:
        lines.append("_No template-guidance coverage changes._")
    lines.append("")

    lines.append("## ODP (organization-defined-parameter) value changes")
    lines.append("")
    if odp_changes:
        lines.append("| System::Control | Param | Before | After |")
        lines.append("| --- | --- | --- | --- |")
        for key, param_id, bv, av in odp_changes[:200]:
            system, control_id = key.split("::", 1)
            lines.append(
                f"| `{system}::{control_id}` | `{param_id}` | "
                f"{bv or '—'} | {av or '—'} |"
            )
        if len(odp_changes) > 200:
            lines.append(f"| … | … | … | {len(odp_changes) - 200} more not shown |")
    else:
        lines.append("_No ODP value changes._")
    lines.append("")

    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a pin-advance change summary between two refs "
        "of this repository (for downstream submodule consumers)."
    )
    parser.add_argument("--from", dest="from_ref", required=True, help="Base ref")
    parser.add_argument("--to", dest="to_ref", required=True, help="Target ref")
    parser.add_argument(
        "--out", type=str, default=None, help="Write to this path (default: stdout)"
    )
    args = parser.parse_args(argv)

    try:
        report = build_changelog(args.from_ref, args.to_ref)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"Wrote change summary to {out_path}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
