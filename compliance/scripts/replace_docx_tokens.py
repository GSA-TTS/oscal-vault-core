#!/usr/bin/env python3
"""Replace an allowlist of exact template tokens throughout a DOCX package."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path


def replace_tokens(path: Path, replacements: dict[str, str]) -> dict:
    counts = dict.fromkeys(replacements, 0)
    remaining = dict.fromkeys(replacements, 0)
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with (
            zipfile.ZipFile(path) as source,
            zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as destination,
        ):
            for item in source.infolist():
                content = source.read(item.filename)
                if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                    text = content.decode("utf-8")
                    for token, value in replacements.items():
                        counts[token] += text.count(token)
                        text = text.replace(token, value)
                        remaining[token] += text.count(token)
                    content = text.encode("utf-8")
                destination.writestr(item, content)
        shutil.move(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "replacements": counts,
        "remaining": remaining,
        "total_replacements": sum(counts.values()),
        "remaining_tokens": sum(remaining.values()),
    }
