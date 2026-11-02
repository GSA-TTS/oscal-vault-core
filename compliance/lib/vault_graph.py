"""Obsidian-style link resolution and graph metrics for a generated vault (ADR-029).

The validators used to match wikilinks by file *stem*, so ``Systems/data.gov`` and
``Components/data.gov`` counted as the same note: an orphaned Component was hidden whenever the
System of the same name was linked, and a path-qualified link could not tell them apart.

This module resolves links the way Obsidian does for a single vault:

* a target is matched case-insensitively, without its ``.md`` extension;
* a target containing ``/`` is a path (a full vault-relative path, or a trailing path suffix);
* a bare target is matched by file name; more than one match is **ambiguous** and is reported,
  never silently resolved to one of them;
* heading (``#``) and block (``^``) references and ``|alias`` text are ignored;
* links inside fenced code blocks are not links;
* embeds (``![[...]]``) are links, and may point at attachments.

The result is a graph of vault-relative note paths plus unresolved and ambiguous reference lists,
and a metrics report suitable for a baseline file. Standard library plus PyYAML.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

# ``[[target]]``, ``[[target|alias]]``, ``[[target#heading]]``, ``[[target^block]]``, and embeds.
LINK_RE = re.compile(r"!?\[\[([^\]\n]*?)\]\]")
FENCE_RE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.DOTALL | re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)

# Notes surfaced through Dataview/Bases queries and tags rather than wikilinks. A static link
# graph cannot follow those, so they are exempt from the orphan check.
OKF_INFRA_TYPES = frozenset(
    {
        "okf.index",
        "okf.instruction",
        "okf.mapping",
        "okf.schema",
        "okf.table_row",
        "okf.source_table",
        "okf.validation_rule",
    }
)
IGNORE_KEY = "gardener-ignore"
TEMPLATE_MARKER = "_meta/templates/"

OK = "ok"
SELF = "self"
EXTERNAL = "external"
UNRESOLVED = "unresolved"
AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class Resolution:
    status: str
    targets: tuple[str, ...] = ()
    raw: str = ""


@dataclass
class Graph:
    notes: list[str] = field(default_factory=list)
    out: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    inn: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    unresolved: list[tuple[str, str]] = field(default_factory=list)
    ambiguous: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    exempt: set[str] = field(default_factory=set)
    types: dict[str, str] = field(default_factory=dict)
    # Attachment embeds (images, PDFs) per note. They are not note-to-note edges: Obsidian's graph
    # hides attachments by default, so a note whose only links are images looks disconnected.
    attachments: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    @property
    def edge_count(self) -> int:
        return sum(len(v) for v in self.out.values())


def _is_template(rel: str) -> bool:
    return TEMPLATE_MARKER in rel


def clean_target(raw: str) -> str:
    """The note part of a link body: no alias, heading or block reference."""
    target = raw.split("|", 1)[0]
    for marker in ("#", "^"):
        target = target.split(marker, 1)[0]
    return target.strip()


class VaultIndex:
    """Lookup tables over one vault directory."""

    def __init__(self, vault: Path) -> None:
        self.vault = Path(vault).resolve()
        self.notes: dict[str, Path] = {}
        self.by_path: dict[str, str] = {}
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.attachments_by_path: dict[str, str] = {}
        self.attachments_by_name: dict[str, list[str]] = defaultdict(list)
        for path in sorted(self.vault.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.vault).as_posix()
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            if path.suffix.lower() == ".md":
                self.notes[rel] = path
                key = rel[:-3].lower()
                self.by_path[key] = rel
                self.by_name[key.rsplit("/", 1)[-1]].append(rel)
            else:
                self.attachments_by_path[rel.lower()] = rel
                self.attachments_by_name[rel.lower().rsplit("/", 1)[-1]].append(rel)

    def resolve(self, raw: str, source: str | None = None) -> Resolution:
        target = clean_target(raw)
        if not target:
            return Resolution(SELF, raw=raw)
        if "://" in target:
            return Resolution(EXTERNAL, raw=raw)
        key = target.lower()
        note_key = key[:-3] if key.endswith(".md") else key
        # Notes first. Names such as ``data.gov`` contain a dot, so a dot never means "attachment".
        if note_key in self.by_path:
            return self._found([self.by_path[note_key]], source, raw)
        if "/" in note_key:
            matches = sorted(
                v for k, v in self.by_path.items() if k.endswith("/" + note_key)
            )
        else:
            matches = sorted(self.by_name.get(note_key, []))
        if matches:
            return self._found(matches, source, raw)
        # Only then look for an attachment (image, pdf, ...), with the extension kept.
        if "." in key.rsplit("/", 1)[-1]:
            return self._attachment(key, raw)
        return Resolution(UNRESOLVED, raw=raw)

    def _attachment(self, key: str, raw: str) -> Resolution:
        if key in self.attachments_by_path:
            return Resolution(OK, (self.attachments_by_path[key],), raw)
        base = key.rsplit("/", 1)[-1]
        hits = sorted(
            v
            for v in self.attachments_by_name.get(base, [])
            if "/" not in key or v.lower().endswith("/" + key)
        )
        if not hits:
            return Resolution(UNRESOLVED, raw=raw)
        if len(hits) > 1:
            return Resolution(AMBIGUOUS, tuple(hits), raw)
        return Resolution(OK, tuple(hits), raw)

    @staticmethod
    def _found(matches: list[str], source: str | None, raw: str) -> Resolution:
        if len(matches) > 1:
            return Resolution(AMBIGUOUS, tuple(matches), raw)
        if source is not None and matches[0] == source:
            return Resolution(SELF, tuple(matches), raw)
        return Resolution(OK, tuple(matches), raw)


def split_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    return (data if isinstance(data, dict) else {}), text[match.end() :]


def link_targets(text: str) -> list[str]:
    """Raw link bodies in ``text`` (frontmatter included), ignoring fenced code blocks."""
    return [m.group(1) for m in LINK_RE.finditer(FENCE_RE.sub("", text))]


def build_graph(vault: Path, *, include_templates: bool = False) -> Graph:
    index = VaultIndex(vault)
    graph = Graph()
    for rel, path in index.notes.items():
        if _is_template(rel) and not include_templates:
            continue
        graph.notes.append(rel)
    keep = set(graph.notes)
    for rel in graph.notes:
        text = index.notes[rel].read_text(encoding="utf-8", errors="replace")
        front, _ = split_frontmatter(text)
        graph.types[rel] = str(front.get("type") or "")
        if front.get(IGNORE_KEY) or graph.types[rel] in OKF_INFRA_TYPES:
            graph.exempt.add(rel)
        for raw in link_targets(text):
            res = index.resolve(raw, rel)
            if res.status == OK:
                for dest in res.targets:
                    if dest not in index.notes:
                        graph.attachments[rel].add(dest)
                    elif dest in keep and dest != rel:
                        graph.out[rel].add(dest)
                        graph.inn[dest].add(rel)
            elif res.status == UNRESOLVED:
                if not _is_template_slot(raw):
                    graph.unresolved.append((rel, clean_target(raw)))
            elif res.status == AMBIGUOUS:
                graph.ambiguous.append((rel, clean_target(raw), res.targets))
    return graph


def _is_template_slot(raw: str) -> bool:
    """``[[#heading]]``-only and empty Dataview slots are not references to a note."""
    return not clean_target(raw)


def orphans(graph: Graph, *, honour_exemptions: bool = True) -> list[str]:
    """Notes with neither inbound nor outbound links."""
    found = [n for n in graph.notes if not graph.out.get(n) and not graph.inn.get(n)]
    if honour_exemptions:
        found = [n for n in found if n not in graph.exempt]
    return sorted(found)


def attachment_only(graph: Graph) -> list[str]:
    """Content orphans whose only links are attachment embeds (image-only notes)."""
    return [n for n in orphans(graph) if graph.attachments.get(n)]


def duplicate_stems(graph: Graph) -> dict[str, list[str]]:
    by_stem: dict[str, list[str]] = defaultdict(list)
    for rel in graph.notes:
        by_stem[Path(rel).stem].append(rel)
    return {s: sorted(p) for s, p in by_stem.items() if len(p) > 1}


def ambiguous_bare_links(graph: Graph) -> dict[str, list[str]]:
    """Bare (no ``/``) link targets that match several notes -> the candidate notes."""
    out: dict[str, list[str]] = {}
    for _src, target, candidates in graph.ambiguous:
        if "/" not in target:
            out.setdefault(target, sorted(candidates))
    return out


def _folder(rel: str) -> str:
    return rel.split("/", 1)[0] if "/" in rel else "<root>"


def metrics(graph: Graph) -> dict:
    """A JSON-serialisable report. Stable key order so it can be diffed as a baseline."""
    raw_orphans = orphans(graph, honour_exemptions=False)
    content = orphans(graph)
    by_folder: dict[str, int] = defaultdict(int)
    for rel in content:
        by_folder[_folder(rel)] += 1
    no_in = [n for n in graph.notes if not graph.inn.get(n)]
    return {
        "notes": len(graph.notes),
        "edges": graph.edge_count,
        "orphans_all": len(raw_orphans),
        "orphans_content": len(content),
        "orphans_exempt": len(raw_orphans) - len(content),
        "orphans_attachment_only": len(attachment_only(graph)),
        "orphans_by_folder": dict(sorted(by_folder.items())),
        "no_inbound": len(no_in),
        "unresolved_links": len(graph.unresolved),
        "unresolved_targets": len({t for _, t in graph.unresolved}),
        "ambiguous_links": len(graph.ambiguous),
        "ambiguous_bare_targets": len(ambiguous_bare_links(graph)),
        "duplicate_stems": len(duplicate_stems(graph)),
    }


REGRESSION_KEYS = (
    "orphans_content",
    "unresolved_links",
    "ambiguous_links",
)


def regressions(
    current: dict, baseline: dict, keys: Iterable[str] = REGRESSION_KEYS
) -> list[str]:
    """Metrics that got worse than ``baseline`` (higher is worse for every key here)."""
    return [
        f"{key}: {baseline[key]} -> {current[key]}"
        for key in keys
        if key in baseline and current.get(key, 0) > baseline[key]
    ]
