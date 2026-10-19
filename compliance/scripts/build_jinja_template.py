#!/usr/bin/env python3
"""Build docxtpl templates from official FedRAMP SSP blank .docx files (R16.3).

Reads a blank template + its roundtrip datacontract, injects Jinja tags into
the writable slots after each instruction block, and writes the jinjafied
``.docx`` to ``compliance/templates/``.

The emitted ``*.jinja.docx`` templates are **committed to git** (they contain
no CUI — they are the official *blank* templates with Jinja tags added). A
narrow ``.gitignore`` exception permits ``compliance/templates/*.jinja.docx``.

Usage::

    # Single template
    uv run python3 compliance/scripts/build_jinja_template.py \\
      --blank "fismatic/Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx" \\
      --contract compliance/datacontracts/low-sspp-roundtrip.yaml \\
      --output compliance/templates/low-sspp.jinja.docx

    # Both demo templates from the registry (uses default paths)
    uv run python3 compliance/scripts/build_jinja_template.py --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.docx_jinjafier import JinjafyError, jinjafy, rendered_tags  # noqa: E402

# Registry of the R16 demo templates: (blank path, contract path, output path).
_REGISTRY = [
    {
        "name": "low-sspp",
        "blank": "fismatic/Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
        "contract": "compliance/datacontracts/low-sspp-roundtrip.yaml",
        "output": "compliance/templates/low-sspp.jinja.docx",
    },
    {
        "name": "moderate-sspp",
        "blank": "fismatic/Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
        "contract": "compliance/datacontracts/moderate-sspp-roundtrip.yaml",
        "output": "compliance/templates/moderate-sspp.jinja.docx",
    },
    {
        "name": "high-sspp",
        "blank": "fismatic/High-SSPP-Template-[Contains-CUI]-10-16-2024.docx",
        "contract": "compliance/datacontracts/high-sspp-roundtrip.yaml",
        "output": "compliance/templates/high-sspp.jinja.docx",
    },
    {
        "name": "leveraged-saas-crm",
        "blank": "fismatic/Leveraged-SaaS-CRM-SSPP-Template-[Contains-CUI]-02-01-2024.docx",
        "contract": "compliance/datacontracts/leveraged-saas-crm-roundtrip.yaml",
        "output": "compliance/templates/leveraged-saas-crm.jinja.docx",
    },
]


def _build_one(
    blank: Path, contract: Path, output: Path, *, strip_instructions: bool = False
) -> int:
    if not blank.exists():
        print(f"SKIP: blank template not present: {blank}")
        print("  (official blanks are gitignored; supply locally to build)")
        return 0
    try:
        result = jinjafy(blank, contract, output, strip_instructions=strip_instructions)
    except JinjafyError as e:
        print(f"ERROR building {output.name}: {e}", file=sys.stderr)
        return 1

    tags = rendered_tags(output)
    mode = "instructions stripped" if strip_instructions else "instructions preserved"
    print(f"Built {output} ({result.injected_count} tags injected, {mode})")
    for b in result.bindings:
        print(f"  + {b.template_tag}  <- {b.instruction_anchor[:50]!r}")
    if result.unmatched_tags:
        print(f"  unmatched templateTags (no writable slot): {result.unmatched_tags}")
    for w in result.warnings:
        print(f"  warn: {w}")
    # Verify every injected tag is actually present in the output.
    missing = [
        b.template_tag for b in result.bindings if tags.get(b.template_tag, 0) != 1
    ]
    if missing:
        print(
            f"ERROR: tags not present exactly once in output: {missing}",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blank", type=Path, help="Path to blank .docx")
    parser.add_argument("--contract", type=Path, help="Path to roundtrip contract YAML")
    parser.add_argument("--output", type=Path, help="Output .jinja.docx path")
    parser.add_argument(
        "--all", action="store_true", help="Build all registered demo templates"
    )
    parser.add_argument(
        "--strip-instructions",
        action="store_true",
        help="Remove instruction blocks from the output template (default: keep)",
    )
    args = parser.parse_args(argv)

    rc = 0
    if args.all:
        for entry in _REGISTRY:
            rc |= _build_one(
                _REPO_ROOT / entry["blank"],
                _REPO_ROOT / entry["contract"],
                _REPO_ROOT / entry["output"],
                strip_instructions=args.strip_instructions,
            )
        return rc

    if not (args.blank and args.contract and args.output):
        parser.error("provide --blank, --contract, and --output (or use --all)")
    return _build_one(
        args.blank,
        args.contract,
        args.output,
        strip_instructions=args.strip_instructions,
    )


if __name__ == "__main__":
    raise SystemExit(main())
