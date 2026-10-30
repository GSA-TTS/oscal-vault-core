# ADR-002: Markdown-First, Canonical-JSON-Sidecar Architecture

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R2 (Canonical JSON Sidecar Generation)

---

## Context

Two representations of the same compliance data are needed: Markdown notes
for human authoring in Obsidian, and machine-readable JSON for downstream
tooling (Backstage, OSBAPI, exports). Keeping both in sync manually is
error-prone and creates dual sources of truth.

## Decision

**Markdown is the single, authoritative source of truth.** Canonical JSON
sidecars are always _generated_ from markdown — never edited directly.

Architecture layers:

```
1. Authoring layer   → Obsidian Markdown notes (human editable)
2. Canonical layer   → per-note JSON sidecars (.snapshots/current/*.json)
3. Snapshot layer    → immutable time-stamped captures
4. Export layer      → OSCAL JSON/YAML for standards-bound types
```

The sidecar generator (`compliance/lib/canonical_json.py`) parses
frontmatter, body sections, and inline Dataview fields into deterministic
JSON with a `sha256` content hash. Generation is triggered by:
- Pre-commit hook (on markdown file changes)
- CLI: `uv run python3 compliance/scripts/generate_canonical_json.py`

## Alternatives Considered

- **JSON as primary, Markdown generated** — Rejected: JSON is not ergonomic
  for prose-heavy compliance narratives; YAML frontmatter in Markdown is
  well-supported by Obsidian tooling.
- **SQLite database as source of truth** — Rejected: breaks the Obsidian
  plugin ecosystem and makes git history unreadable.
- **Single-file YAML/JSON per control** — Rejected: loses the linked-note
  model that enables Obsidian graph view and wikilink navigation.

## Consequences

- **Positive:** Obsidian plugins (Dataview, Templater, graph view) work
  natively on the markdown layer without any adapter.
- **Positive:** Canonical JSON has a `sha256` hash so downstream consumers
  can detect stale caches without full re-parse.
- **Negative:** Canonical JSON is never the edit target — contributors who
  modify JSON directly will have their changes overwritten on next generation.
  Documentation and CI must enforce this.
- **Ongoing constraint:** The pre-commit hook auto-regenerates sidecars;
  `.snapshots/current/` is committed to git; `.snapshots/history/` is
  gitignored to avoid history bloat.

## References

- `compliance/lib/canonical_json.py`
- `compliance/scripts/generate_canonical_json.py`
- the project refactoring plan (not exported to the public core) §§ Phase R2, Phase R6
