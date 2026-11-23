#!/usr/bin/env python3
"""Generate restricted, local-only DOCX and HTML prose redlines between two DOCX files.

The output contains raw document prose. The output directory must be outside any git
repository or worktree, and this command never uploads, commits, or opens anything.

Example:
    generate_docx_redline.py before.docx after.docx --out-dir ~/review-output/redline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.lib.docx_redline import RedlineError, generate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--context", type=int, default=3)
    parser.add_argument("--max-chunk-lines", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        manifest = generate(
            args.left,
            args.right,
            args.out_dir,
            context=args.context,
            max_chunk_lines=args.max_chunk_lines,
        )
    except (RedlineError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
