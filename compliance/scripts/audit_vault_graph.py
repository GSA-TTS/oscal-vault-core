#!/usr/bin/env python3
"""Measure the link graph of generated vaults, and gate against regressions (ADR-029).

    audit_vault_graph.py --vault examples/vault/<system>              # print metrics
    audit_vault_graph.py                                              # every vault in the workspace
    audit_vault_graph.py --out build/graph-metrics.json               # write a baseline
    audit_vault_graph.py --baseline build/graph-metrics.json          # fail if any metric got worse
    audit_vault_graph.py --list                                       # also list each finding

Each vault is analysed on its own, the way Obsidian opens it. Links are resolved by path (see
``compliance/lib/vault_graph.py``). Exit 0 ok, 1 regression against the baseline, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from compliance.lib import vault_graph  # noqa: E402
from compliance.lib.workspace import (  # noqa: E402
    WorkspaceError,
    add_workspace_arguments,
    guard_output,
    resolve_workspace,
)


def _vault_dirs(args) -> list[Path]:
    if args.vault:
        dirs = [Path(v).resolve() for v in args.vault]
    else:
        root = resolve_workspace(args.workspace) / "examples" / "vault"
        dirs = (
            [
                d
                for d in sorted(root.iterdir())
                if d.is_dir() and not d.name.startswith(".")
            ]
            if root.is_dir()
            else []
        )
    return dirs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--vault", action="append", help="vault directory (repeatable)")
    parser.add_argument("--out", type=Path, help="write the metrics JSON here")
    parser.add_argument(
        "--baseline", type=Path, help="earlier metrics JSON to compare against"
    )
    parser.add_argument(
        "--list", action="store_true", help="list orphans, unresolved, ambiguous"
    )
    add_workspace_arguments(parser)
    args = parser.parse_args(argv)

    vaults = _vault_dirs(args)
    if not vaults:
        print(
            "ERROR: no vault directories (use --vault or run in a workspace)",
            file=sys.stderr,
        )
        return 2
    missing = [v for v in vaults if not v.is_dir()]
    if missing:
        print(f"ERROR: vault not found: {missing[0]}", file=sys.stderr)
        return 2
    baseline = None
    if args.baseline:
        try:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(
                f"ERROR: cannot read baseline {args.baseline}: {exc}", file=sys.stderr
            )
            return 2

    report: dict = {"schema": 1, "vaults": {}}
    for vault in vaults:
        graph = vault_graph.build_graph(vault)
        report["vaults"][vault.name] = vault_graph.metrics(graph)
        m = report["vaults"][vault.name]
        print(
            f"{vault.name}: {m['notes']} notes, {m['edges']} edges, "
            f"{m['orphans_content']} content orphans ({m['orphans_exempt']} exempt), "
            f"{m['unresolved_links']} unresolved, {m['ambiguous_links']} ambiguous"
        )
        if args.list:
            for rel in vault_graph.orphans(graph):
                print(f"  orphan      {rel}")
            for src, target in graph.unresolved:
                print(f"  unresolved  {src} -> [[{target}]]")
            for src, target, cands in graph.ambiguous:
                print(f"  ambiguous   {src} -> [[{target}]] ({len(cands)} candidates)")

    if args.out:
        try:
            out = guard_output(args.out, allow_in_core=args.allow_write_in_core)
        except WorkspaceError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {out}")

    if baseline is not None:
        bad: list[str] = []
        for name, current in report["vaults"].items():
            before = baseline.get("vaults", {}).get(name)
            if before is None:
                continue  # a new vault has no baseline to regress against
            bad += [f"{name}: {r}" for r in vault_graph.regressions(current, before)]
        if bad:
            print("REGRESSION against baseline:", file=sys.stdout)
            for line in bad:
                print(f"  {line}", file=sys.stdout)
            return 1
        print("OK - no metric is worse than the baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
