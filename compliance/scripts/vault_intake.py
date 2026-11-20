#!/usr/bin/env python3
"""Generic fismatic vault-export snapshot digest and sanitization helpers.

Provides system-neutral utilities for digesting a frozen vault export directory,
normalizing Obsidian wiki links and runbook paths, redacting URLs/emails/IPv4
literals, and detecting raw vault note markers.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Callable
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "compliance").is_dir() and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from compliance.lib.control_identity import canonical_control_id
except ModuleNotFoundError:
    from control_identity import canonical_control_id  # type: ignore[no-redef]

NOTICE_NAME = "NOT-AUTHORITATIVE.md"

WIKI_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
RUNBOOK_PATH_RE = re.compile(r"`?[Rr]unbooks/([A-Za-z0-9_-]+)(?:\.md)?`?")
URL_RE = re.compile(r"https?://[^\s\"'>,)`\]]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b")

TEMPLATE_MARKERS = (
    "Instructions:",
    "[[",
    "]]",
    "my-system",
    "> [!note]",
    "dataview",
    "Template guidance",
)

RAW_VAULT_MARKERS = (
    re.compile(r"^parsed_by:\s*fismatic\s*$", re.MULTILINE),
    re.compile(r"^type:\s*oscal\.", re.MULTILINE),
    re.compile(r"^```dataview", re.MULTILINE),
)


def aggregate_digest(
    root: Path, *, exclude_names: tuple[str, ...] = (NOTICE_NAME,)
) -> tuple[str, list[tuple[str, str]]]:
    """SHA-256 over sorted ``relpath NUL filehash`` lines for every vault file."""
    excluded = set(exclude_names)
    entries = []
    for path in sorted(
        p for p in root.rglob("*") if p.is_file() and p.name != ".DS_Store"
    ):
        rel = path.relative_to(root).as_posix()
        if rel in excluded:
            continue
        entries.append((rel, hashlib.sha256(path.read_bytes()).hexdigest()))
    payload = "".join(f"{rel}\0{digest}\n" for rel, digest in entries)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), entries


def runbook_refs(raw: str) -> list[str]:
    """Return sorted unique runbook slugs referenced in raw note text."""
    names = {m.group(1) for m in re.finditer(r"[Rr]unbooks/([A-Za-z0-9_-]+)", raw)}
    return sorted(names)


def sanitize_vault_text(
    text: str,
    counts: dict[str, int],
    *,
    label_re: re.Pattern[str] | None = None,
    host_re: re.Pattern[str] | None = None,
    post_transform: Callable[[str, dict[str, int]], str] | None = None,
) -> str:
    """Sanitize raw vault note text into a single-line, redaction-clean string."""

    def bump(key: str, n: int = 1) -> None:
        counts[key] = counts.get(key, 0) + n

    def wiki(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        bump("wiki_links")
        if target.lower().startswith("runbooks/"):
            return f"runbook {target.split('/', 1)[1]}"
        if target.startswith("Controls/"):
            try:
                return canonical_control_id(target.split("/", 1)[1])
            except ValueError:
                return target.split("/", 1)[1]
        return target.split("/")[-1]

    text = WIKI_RE.sub(wiki, text)

    def runbook(match: re.Match[str]) -> str:
        bump("runbook_paths")
        return f"runbook {match.group(1)}"

    text = RUNBOOK_PATH_RE.sub(runbook, text)
    if label_re is not None:
        text = label_re.sub("", text)
    bump_n = len(URL_RE.findall(text))
    if bump_n:
        bump("urls", bump_n)
        text = URL_RE.sub("<REDACTED_URL>", text)
    if host_re is not None:
        n = len(host_re.findall(text))
        if n:
            bump("internal_git_host", n)
            text = host_re.sub("<INTERNAL_GIT_HOST>", text)
    n = len(EMAIL_RE.findall(text))
    if n:
        bump("emails", n)
        text = EMAIL_RE.sub("<REDACTED_EMAIL>", text)
    n = text.count("0.0.0.0/0")
    if n:
        bump("open_world_cidr_reworded", n)
        text = text.replace("0.0.0.0/0", "any-address CIDR")
    n = len(IPV4_RE.findall(text))
    if n:
        bump("ipv4", n)
        text = IPV4_RE.sub("<REDACTED_IP>", text)
    if post_transform is not None:
        text = post_transform(text, counts)
    return re.sub(r"\s+", " ", text).strip()
