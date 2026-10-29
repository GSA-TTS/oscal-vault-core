#!/usr/bin/env python3
"""Extract routed records from a FedRAMP SSP via an ODCS data contract.

Secondary parser companion to `scripts/docx_catchall_dump.py`. Walks a
markdown catchall blob (or a .docx, which is dumped to markdown first) and
routes content to destination note types based on the contract at
`compliance/datacontracts/fedramp-ssp-v1.yaml`.

Usage:
    # Extract from a markdown catchall blob:
    uv run python3 compliance/scripts/extract_by_contract.py \\
        --markdown examples/docx_catchall/<ssp>.catchall.md \\
        --output /tmp/routed.json

    # Extract from a .docx directly (dumps to markdown first):
    uv run python3 compliance/scripts/extract_by_contract.py \\
        --docx <ssp>.docx \\
        --output /tmp/routed.json

    # Use a custom contract:
    uv run python3 compliance/scripts/extract_by_contract.py \\
        --markdown path/to/blob.md \\
        --contract compliance/datacontracts/fedramp-ssp-v1.yaml

    # Print only the summary (filled/unfilled counts):
    uv run python3 compliance/scripts/extract_by_contract.py \\
        --markdown path/to/blob.md --summary-only

Subcommands:
    validate    Validate the contract against the ODCS v3.1.0 JSON schema.
                uv run python3 compliance/scripts/extract_by_contract.py validate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from compliance.lib.heading_parser import (  # noqa: E402
    extract,
    extract_from_docx,
    load_contract,
)

_DEFAULT_CONTRACT = _REPO_ROOT / "compliance" / "datacontracts" / "fedramp-ssp-v1.yaml"
_ODCS_SCHEMA = (
    _REPO_ROOT
    / "compliance"
    / "datacontracts"
    / "schemas"
    / "odcs-json-schema-v3.1.0.json"
)


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate the contract against the ODCS v3.1.0 JSON schema."""
    try:
        import jsonschema
        from referencing import Registry, Resource
    except ImportError:
        print(
            "ERROR: jsonschema + referencing not installed. Run `uv sync`.",
            file=sys.stderr,
        )
        return 2

    contract_path = Path(args.contract)
    schema_path = Path(args.schema)
    if not contract_path.exists():
        print(f"ERROR: contract not found: {contract_path}", file=sys.stderr)
        return 2
    if not schema_path.exists():
        print(f"ERROR: schema not found: {schema_path}", file=sys.stderr)
        return 2

    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    # ODCS schema uses $ref to #/$defs/* — register the schema as a resource
    # so $ref resolution works for sub-schemas.
    registry = Registry().with_resource(
        uri=str(schema_path.as_uri()),
        resource=Resource.from_contents(schema),
    )

    # Also register the schema under its $id if present
    schema_id = schema.get("$id") or str(schema_path.as_uri())
    registry = registry.with_resource(
        uri=schema_id,
        resource=Resource.from_contents(schema),
    )

    validator = jsonschema.Draft201909Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(contract), key=lambda e: list(e.path))
    if errors:
        print(f"INVALID: {len(errors)} validation error(s) against {schema_path.name}")
        for err in errors[:20]:
            loc = ".".join(str(p) for p in err.path) or "<root>"
            print(f"  - {loc}: {err.message}")
            if len(errors) > 20:
                print(f"  ... and {len(errors) - 20} more")
        return 1
    print(
        f"VALID: {contract_path.name} conforms to ODCS {schema.get('apiVersion', 'v3.1.0')}"
    )
    print(f"  schema: {schema_path}")
    print(f"  objects: {len(contract.get('schema') or [])}")
    total_props = sum(
        len(o.get("properties") or []) for o in contract.get("schema") or []
    )
    print(f"  properties: {total_props}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    contract = load_contract(args.contract)

    if args.docx:
        if not Path(args.docx).exists():
            print(f"ERROR: docx not found: {args.docx}", file=sys.stderr)
            return 2
        result = extract_from_docx(args.docx, contract)
    elif args.markdown:
        if not Path(args.markdown).exists():
            print(f"ERROR: markdown not found: {args.markdown}", file=sys.stderr)
            return 2
        md = Path(args.markdown).read_text(encoding="utf-8")
        result = extract(md, contract)
    else:
        print("ERROR: must supply --docx or --markdown", file=sys.stderr)
        return 2

    if args.summary_only:
        print(json.dumps(result["summary"], indent=2))
        return 0

    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
        s = result["summary"]
        print(f"wrote {args.output}")
        print(f"  objects: {s['objects_total']} (with data: {s['objects_with_data']})")
        print(f"  properties: {s['properties_filled']}/{s['properties_total']} filled")
        print(f"  unfilled: {len(s['unfilled'])}")
    else:
        print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    # extract
    pe = sub.add_parser("extract", help="Extract routed records via the contract.")
    pe.add_argument(
        "--markdown", type=Path, help="Path to a catchall dump markdown blob."
    )
    pe.add_argument(
        "--docx",
        type=Path,
        help="Path to a FedRAMP SSP .docx (dumped to markdown first).",
    )
    pe.add_argument(
        "--contract",
        type=Path,
        default=_DEFAULT_CONTRACT,
        help=f"ODCS contract YAML (default: {_DEFAULT_CONTRACT.name}).",
    )
    pe.add_argument(
        "--output", type=Path, default=None, help="Output JSON path (default: stdout)."
    )
    pe.add_argument(
        "--summary-only", action="store_true", help="Print only the fill-count summary."
    )
    pe.set_defaults(func=cmd_extract)

    # validate
    pv = sub.add_parser(
        "validate", help="Validate the contract against the ODCS JSON schema."
    )
    pv.add_argument(
        "--contract",
        type=Path,
        default=_DEFAULT_CONTRACT,
        help=f"ODCS contract YAML (default: {_DEFAULT_CONTRACT.name}).",
    )
    pv.add_argument(
        "--schema",
        type=Path,
        default=_ODCS_SCHEMA,
        help=f"ODCS JSON schema (default: {_ODCS_SCHEMA.name}).",
    )
    pv.set_defaults(func=cmd_validate)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
