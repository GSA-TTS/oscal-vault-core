#!/usr/bin/env python3
"""Initialize private local integrity review records from explicit workspace IDs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE))

from compliance.lib.docx_integrity_report import timestamp  # noqa: E402


def initialize(output: Path, system_ids: list[str], workspace: Path) -> dict:
    """Exclusive creation; registry contents never confer source authorization."""
    if not system_ids or len(system_ids) != len(set(system_ids)):
        raise ValueError("provide unique nonempty system IDs")
    if any(
        not re.fullmatch(r"[a-z0-9_][a-z0-9_-]{0,100}", value) for value in system_ids
    ):
        raise ValueError("invalid system ID")
    output, workspace = output.expanduser().resolve(), workspace.expanduser().resolve()
    if output == CORE or CORE in output.parents or output == workspace:
        raise ValueError("registry must be outside the core and workspace root")
    if workspace in output.parents:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", str(output)],
            cwd=workspace,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("registry must be Git-ignored inside workspace")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    output.chmod(0o700)
    manifest = {
        "schema_version": "1.0.0",
        "created_at": timestamp(),
        "records": [
            {
                "system_id": value,
                "source_docx": None,
                "source_sha256": None,
                "source_classification": "unknown",
                "authorized_by": None,
                "template_family": "unknown",
                "expected_control_layout": "unknown",
                "run_status": "blocked",
                "blocker": "authorized source not registered",
            }
            for value in system_ids
        ],
    }
    for name, value in (
        ("manifest.json", manifest),
        ("report-index.json", {"schema_version": "1.0.0", "reports": []}),
        ("debt-ledger.json", {"schema_version": "1.0.0", "items": []}),
        ("decision-log.json", {"schema_version": "1.0.0", "decisions": []}),
    ):
        with (output / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--system-id", action="append")
    inputs.add_argument(
        "--discover-vault-ids",
        action="store_true",
        help="Read immediate workspace vault directory names only",
    )
    args = parser.parse_args(argv)
    mask = os.umask(0o077)
    try:
        ids = args.system_id
        if args.discover_vault_ids:
            vaults = args.workspace.expanduser().resolve() / "examples/vault"
            ids = sorted(
                path.name
                for path in vaults.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            )
        manifest = initialize(args.output, ids, args.workspace)
    except (OSError, ValueError) as exc:
        print(
            f"BLOCKED: {type(exc).__name__}; provide valid IDs and fresh ignored local output",
            file=sys.stderr,
        )
        return 2
    finally:
        os.umask(mask)
    print(
        f"Initialized {len(manifest['records'])} BLOCKED records; no source documents read or authorized."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
