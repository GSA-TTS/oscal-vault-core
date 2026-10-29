# ADR-014: Wikilink Validator Warn-Only by Default

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R10 (Shift-Left Validation)

---

## Context

Wikilinks (`[[Note Title]]`) in Obsidian connect notes to each other, forming
the knowledge graph. A broken wikilink (referencing a note that doesn't exist)
silently creates a dead-end in the graph without any visible error in Obsidian.

Enforcing hard failure on all broken wikilinks would block all commits until
legacy vault issues are resolved. The Zendesk vault alone has ~13,400 broken
wikilinks from pre-refactoring note generation.

## Decision

The wikilink validator (`scripts/validate_wikilinks.py`) runs in **warn-only
mode** by default in both pre-commit and CI. Broken links are reported but
do not block commits or fail CI.

The `--strict` flag enables hard failure:
```bash
uv run python3 scripts/validate_wikilinks.py --strict
```

This is useful for:
- Validating a specific new vault before its first commit
- Confirming that a vault refactoring introduced no new broken links

The pre-commit hook uses:
```yaml
entry: uv run python3 scripts/validate_wikilinks.py
```
(without `--strict`)

## Alternatives Considered

- **Hard-fail on all broken links** — Rejected: would require fixing ~13,400
  pre-existing broken links in the Zendesk vault before any unrelated change
  could be committed.
- **No wikilink validation at all** — Rejected: new broken links introduced
  by refactoring would go undetected.
- **Per-vault suppression lists** — Considered; rejected as too complex for
  the current stage; the warn-only default achieves the same practical effect
  with less infrastructure.

## Consequences

- **Positive:** New broken links introduced in new code are reported and
  visible in commit output even though they don't block the commit.
- **Positive:** Contributors can iterate on vault structure without being
  blocked by pre-existing legacy issues.
- **Negative:** Broken link debt in legacy vaults (especially `zendesk-ssp/`)
  is not actively enforced. Without enforcement, the debt may grow.
- **Ongoing plan:** As vaults are regenerated with the unified converter
  (R11), wikilink quality will improve. Switch to `--strict` for vaults that
  have been fully migrated.

## Pitfall

The Zendesk vault has ~13,400 broken wikilinks because it uses custom
`CR-*` control IDs (not standard NIST OSCAL) that reference controls outside
the standard baseline. These are structural, not errors — the wikilinks
reference controls that don't exist in the standard Controls folder.

## References

- `scripts/validate_wikilinks.py`
- `.pre-commit-config.yaml` (wikilink hook, no `--strict`)
- `archive/plans/PROJECT_REFACTORING_PLAN.md` §§ Phase R10
