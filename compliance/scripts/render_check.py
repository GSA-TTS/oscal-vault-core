#!/usr/bin/env python3
"""Optional headless-render sanity check for jinjafied .docx templates (#10).

python-docx and docxtpl can accept a `.docx` that a real word processor would
reject: a malformed run-split from tag injection can produce structurally-valid
OOXML that still fails to *render*. This script converts each committed
`compliance/templates/*.jinja.docx` (and any file passed explicitly) to PDF via
headless LibreOffice (`soffice --headless --convert-to pdf`) to catch that.

It is defense-in-depth, NOT a primary gate:
  - SKIPS cleanly (exit 0) when LibreOffice is unavailable, printing BLOCKED,
    so it never fails a dev/CI without soffice installed.
  - `--require` flips that to a hard failure (exit 2) for a CI lane that has
    installed LibreOffice.

Usage:
    python3 compliance/scripts/render_check.py                 # all committed templates
    python3 compliance/scripts/render_check.py path/to.docx    # specific files
    python3 compliance/scripts/render_check.py --require       # fail if soffice absent
    SOFFICE=/opt/libreoffice/program/soffice python3 ... --require
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO_ROOT / "compliance" / "templates"

# Common binary names across distros/installs.
_SOFFICE_NAMES = ("soffice", "libreoffice")


def find_soffice(explicit: Optional[str]) -> Optional[str]:
    """Resolve the LibreOffice binary from --soffice, $SOFFICE, or PATH."""
    for candidate in (explicit, os.environ.get("SOFFICE")):
        if candidate and Path(candidate).is_file():
            return candidate
    for name in _SOFFICE_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _committed_templates() -> List[Path]:
    if not _TEMPLATES.is_dir():
        return []
    return sorted(_TEMPLATES.glob("*.jinja.docx"))


def render_to_pdf(soffice: str, docx_path: Path, outdir: Path) -> tuple[bool, str]:
    """Convert one .docx to PDF; return (ok, message).

    A non-zero exit, a soffice error, or a missing output PDF are all failures.
    """
    proc = subprocess.run(
        [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(docx_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    pdf = outdir / (docx_path.stem + ".pdf")
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "soffice non-zero exit").strip()
    if not pdf.is_file() or pdf.stat().st_size == 0:
        return False, "no (or empty) PDF produced"
    return True, f"{pdf.stat().st_size} bytes"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Headless-render committed jinjafied templates via LibreOffice."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help=".docx files to render (default: all committed templates)",
    )
    parser.add_argument(
        "--soffice", default=None, help="Path to soffice/libreoffice binary"
    )
    parser.add_argument(
        "--require",
        action="store_true",
        help="Fail (exit 2) if LibreOffice is not found, instead of skipping.",
    )
    args = parser.parse_args()

    soffice = find_soffice(args.soffice)
    if not soffice:
        msg = "LibreOffice (soffice) not found (checked --soffice, $SOFFICE, PATH)."
        if args.require:
            print(f"ERROR: {msg}", file=sys.stderr)
            return 2
        print(f"BLOCKED: {msg} Render check skipped.", file=sys.stderr)
        return 0

    targets = [Path(f) for f in args.files] or _committed_templates()
    if not targets:
        print("No templates to render.", file=sys.stderr)
        return 0

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        outdir = Path(tmp)
        for path in targets:
            if not path.is_file():
                failures += 1
                print(f"FAIL {path}: not found", file=sys.stderr)
                continue
            try:
                ok, detail = render_to_pdf(soffice, path, outdir)
            except subprocess.TimeoutExpired:
                ok, detail = False, "soffice timed out"
            if ok:
                print(f"OK   {path} ({detail})")
            else:
                failures += 1
                print(f"FAIL {path}: {detail}", file=sys.stderr)

    total = len(targets)
    print(
        f"\nRender check: {total - failures}/{total} templates rendered.",
        file=sys.stderr,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
