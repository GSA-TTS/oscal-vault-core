#!/usr/bin/env python3
"""Render a network or data-flow figure (SVG or PNG) from a CycloneDX threat-model DFD.

Examples:
  render_dfd_diagram.py --dfd model/base-dfd.cdx.json --output net.png \\
      --title "Figure 9-1. Network Diagram" --view network
  render_dfd_diagram.py --dfd model/base-dfd.cdx.json --architecture \\
      model/architecture.yaml --output flow.svg --title "Data Flow"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from compliance.lib.dfd_diagram import VIEWS, render_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dfd", type=Path, required=True)
    parser.add_argument("--architecture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--view", choices=VIEWS, default="data-flow")
    parser.add_argument(
        "--font-file",
        type=Path,
        action="append",
        default=[],
        help="Font file used for PNG text (repeatable); defaults to DejaVu Sans",
    )
    args = parser.parse_args(argv)
    architecture = (
        yaml.safe_load(args.architecture.read_text(encoding="utf-8")) or {}
        if args.architecture
        else None
    )
    stats = render_file(
        json.loads(args.dfd.read_text(encoding="utf-8")),
        args.output,
        title=args.title,
        architecture=architecture,
        view=args.view,
        font_files=args.font_file or None,
    )
    print(f"Wrote {args.output} ({stats})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
