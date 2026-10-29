#!/usr/bin/env python3
"""Generate feedback artifacts (skeleton .docx + skeleton .md) from a .docx or
markdown catchall blob, using the ODCS data contract as a reverse template.

Pipeline:
    .docx --docx_catchall_dump.py--> markdown catchall blob
          --heading_parser.extract()--> routed records (forward, via contract)
          --docx_feedback + markdown_feedback--> skeleton feedback artifacts

The skeleton is a compact TOC showing, per source heading, whether it was
routed, to which destinations, and whether the routed properties were filled.
Designed for an agent to scan and take back to the parsing logic + datacontract
to investigate and fix.

Usage:
    # From a .docx (dumps to markdown first, then extracts + renders):
    uv run python3 compliance/scripts/generate_feedback.py \\
        --docx <ssp>.docx \\
        --output-dir examples/feedback/gsa-pages/

    # From an existing catchall markdown blob:
    uv run python3 compliance/scripts/generate_feedback.py \\
        --markdown examples/docx_catchall/<ssp>.catchall.md \\
        --output-dir examples/feedback/gsa-pages/

    # Generate only the markdown skeleton (skip .docx):
    uv run python3 compliance/scripts/generate_feedback.py \\
        --markdown path/to/blob.md --markdown-only

    # Generate only the .docx skeleton:
    uv run python3 compliance/scripts/generate_feedback.py \\
        --markdown path/to/blob.md --docx-only

    # Custom contract:
    uv run python3 compliance/scripts/generate_feedback.py \\
        --markdown path/to/blob.md \\
        --contract compliance/datacontracts/fedramp-ssp-v1.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from compliance.lib.docx_feedback import render_feedback_docx  # noqa: E402
from compliance.lib.heading_parser import (  # noqa: E402
    extract,
    extract_from_docx,
    load_contract,
    parse_catchall_blob,
)
from compliance.lib.markdown_feedback import generate_skeleton  # noqa: E402

_DEFAULT_CONTRACT = _REPO_ROOT / "compliance" / "datacontracts" / "fedramp-ssp-v1.yaml"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--markdown", type=Path, help="Path to a catchall dump markdown blob."
    )
    src.add_argument(
        "--docx",
        type=Path,
        help="Path to a FedRAMP SSP .docx (dumped to markdown first).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory for feedback artifacts.",
    )
    p.add_argument(
        "--contract",
        type=Path,
        default=_DEFAULT_CONTRACT,
        help=f"ODCS contract YAML (default: {_DEFAULT_CONTRACT.name}).",
    )
    p.add_argument(
        "--markdown-only",
        action="store_true",
        help="Generate only the skeleton .md (skip .docx).",
    )
    p.add_argument(
        "--docx-only",
        action="store_true",
        help="Generate only the skeleton .docx (skip .md).",
    )
    p.add_argument(
        "--basename",
        type=str,
        default=None,
        help="Output basename (default: derived from source).",
    )
    args = p.parse_args(argv)

    contract = load_contract(args.contract)

    if args.docx:
        if not args.docx.exists():
            print(f"ERROR: docx not found: {args.docx}", file=sys.stderr)
            return 2
        result = extract_from_docx(args.docx, contract)
        source_docx = str(args.docx)
        # extract_from_docx already parsed the blob; re-parse for sections
        # (cheap — string in memory)
        # Actually we need the markdown. Re-dump to a temp file.
        import io
        import contextlib
        import os
        import sys as _sys

        scripts_dir = str(_REPO_ROOT / "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        import docx_catchall_dump as dcd  # type: ignore

        tmp_md = (
            Path(os.environ.get("TMPDIR", "/tmp")) / f"_catchall_{args.docx.stem}.md"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            dcd.dump_docx(args.docx, tmp_md)  # type: ignore[attr-defined]
        md = tmp_md.read_text(encoding="utf-8")
        basename = args.basename or args.docx.stem
    else:
        if not args.markdown.exists():
            print(f"ERROR: markdown not found: {args.markdown}", file=sys.stderr)
            return 2
        md = args.markdown.read_text(encoding="utf-8")
        result = extract(md, contract)
        # Derive source name from the markdown's first heading
        source_docx = args.markdown.stem.replace(".catchall", "")
        basename = args.basename or args.markdown.stem.replace(".catchall", "")

    sections = parse_catchall_blob(md)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    md_path = args.output_dir / f"{basename}.feedback.md"
    docx_path = args.output_dir / f"{basename}.feedback.docx"

    if not args.docx_only:
        stats_md = generate_skeleton(result, contract, sections, md_path, source_docx)
        print(
            f"wrote {stats_md['output']}  — "
            f"sections={stats_md['sections_total']} "
            f"(routed={stats_md['routed_count']}, "
            f"unrouted={stats_md['unrouted_count']})"
        )

    if not args.markdown_only:
        stats_docx = render_feedback_docx(
            result, contract, sections, docx_path, source_docx
        )
        print(
            f"wrote {stats_docx['output']}  — "
            f"sections={stats_docx['sections_total']} "
            f"(routed={stats_docx['routed_count']}, "
            f"unrouted={stats_docx['unrouted_count']}, "
            f"unfilled={stats_docx['unfilled_count']})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
