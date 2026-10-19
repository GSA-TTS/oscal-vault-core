#!/usr/bin/env python3
"""Validate a generic SSP reconciliation overlay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.lib.reconciliation import validate_overlay  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overlay", type=Path)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    data = yaml.safe_load(args.overlay.read_text(encoding="utf-8")) or {}
    errors = validate_overlay(data, require_ready=args.require_ready)
    for error in errors:
        print(f"ERROR {args.overlay}: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
