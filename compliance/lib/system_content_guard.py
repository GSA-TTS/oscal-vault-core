"""Detect system content that must not be committed to the upstream core.

The core is meant to be *vendored* by a system repository. A fork that drops a source ``.docx``
or generated vault output into the core and opens a pull request would publish system content.
This module classifies paths (and, where available, blob hashes and added text) so a check can
refuse those changes. It is a safety net for the common accidents, not a content scanner: review
is the backstop. See ADR-028.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

OFFICE_SUFFIXES = {".docx", ".doc", ".ods", ".odt", ".xlsx", ".xls"}

# Directory classes that hold extracted, generated or source-derived material.
FORBIDDEN_DIR_PARTS = (
    "examples",
    ".exports",
    ".snapshots",
    "review-output",
    "roundtrip_review",
    "docx_catchall",
)

# Paths where generated-vault style notes are legitimate documentation or test data.
NOTE_EXEMPT_PREFIXES = ("docs/", "compliance/tests/", "tests/", "skills/", ".github/")

_GENERATED_NOTE = re.compile(r"^\+?type:\s*oscal\.", re.IGNORECASE)
_NOTE_SUFFIXES = {".md", ".yaml", ".yml"}


@dataclass(frozen=True)
class Entry:
    """One changed file: ``status`` is ``added``/``modified``/``renamed``/``removed``."""

    path: str
    status: str = "added"
    blob_sha: str | None = None
    patch: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class Violation:
    path: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: [{self.rule}] {self.detail}"


def load_allowlist(path: Path) -> set[str]:
    """Blob SHAs of the committed blank templates (the only office files allowed)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {entry["blob_sha"] for entry in data.get("blank_templates", [])}


def _forbidden_dir(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    for index, part in enumerate(parts[:-1]):
        if part in FORBIDDEN_DIR_PARTS:
            return "/".join(parts[: index + 1])
    return None


def _added_lines(patch: str, limit: int = 14) -> list[str]:
    """First added lines of a unified-diff hunk (``+`` lines, header markers excluded)."""
    lines: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line)
            if len(lines) >= limit:
                break
    return lines


def _looks_like_generated_note(entry: Entry) -> bool:
    if entry.text is not None:
        head = entry.text.splitlines()[:14]
        return any(_GENERATED_NOTE.match(line) for line in head)
    if entry.patch:
        return any(_GENERATED_NOTE.match(line) for line in _added_lines(entry.patch))
    return False


def check_entry(entry: Entry, allowed_blobs: Iterable[str]) -> list[Violation]:
    if entry.status == "removed":
        return []
    found: list[Violation] = []
    path = entry.path.replace("\\", "/")
    suffix = PurePosixPath(path).suffix.lower()

    if suffix in OFFICE_SUFFIXES and (
        entry.blob_sha is None or entry.blob_sha not in set(allowed_blobs)
    ):
        found.append(
            Violation(
                path,
                "office-document",
                "source documents (.docx/.doc/.ods/...) are not accepted; only the blank "
                "templates listed in compliance/data/blank_template_allowlist.json",
            )
        )
    bad_dir = _forbidden_dir(path)
    if bad_dir:
        found.append(
            Violation(
                path,
                "generated-output",
                f"'{bad_dir}/' holds extracted or generated system output; keep it in a "
                "system repository that vendors the core",
            )
        )
    if (
        suffix in _NOTE_SUFFIXES
        and not path.startswith(NOTE_EXEMPT_PREFIXES)
        and _looks_like_generated_note(entry)
    ):
        found.append(
            Violation(
                path,
                "generated-note",
                "generated vault note frontmatter (type: oscal.*) outside docs/ and test fixtures",
            )
        )
    return found


def check_entries(
    entries: Sequence[Entry], allowed_blobs: Iterable[str]
) -> list[Violation]:
    allowed = set(allowed_blobs)
    out: list[Violation] = []
    for entry in entries:
        out.extend(check_entry(entry, allowed))
    return out


def entries_from_github_files(files: Sequence[dict]) -> list[Entry]:
    """Adapt the REST ``pulls/{n}/files`` payload (``filename``, ``status``, ``sha``, ``patch``)."""
    entries: list[Entry] = []
    for item in files:
        status = {"added": "added", "removed": "removed", "renamed": "renamed"}.get(
            item.get("status", "modified"), "modified"
        )
        entries.append(
            Entry(
                path=item["filename"],
                status=status,
                blob_sha=item.get("sha"),
                patch=item.get("patch"),
            )
        )
    return entries
