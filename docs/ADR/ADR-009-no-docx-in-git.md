# ADR-009: Gitignore Strategy — No `.docx` in VCS

**Status:** Amended by [ADR-021](ADR-021-commit-blank-templates.md) (blank templates now committed)
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** All phases (foundational constraint)

---

## Context

FedRAMP SSP `.docx` files contain sensitive agency security information that
must not be committed to a public repository. The source documents are also
large binary blobs that make git history unwieldy. Tests require `.docx`
fixtures for full coverage.

## Decision

All `*.docx` files are globally gitignored. Derived/generated artifacts
(JSON, YAML, Markdown) are committed.

`.gitignore` rule:
```
*.docx
```

Test fixtures use **golden JSON** instead of `.docx` binaries:
- `compliance/tests/fixtures/` contains pre-extracted JSON representations
  of the control data from real SSPs
- Tests validate extraction logic against the golden JSON, not the raw `.docx`
- `validate_all.sh` runs a full extraction smoke-test if `.docx` files exist
  locally (the `DOCX_DIR` environment variable), but this is optional and
  not required for CI

The `.docx` pre-commit guard (`no-docx` hook) provides a safety net:
```bash
scripts/pre-commit-check.sh  # fails if any staged file ends in .docx
```

Round-trip `.docx` outputs (generated from vault data, not source SSPs) are
also gitignored under `examples/feedback/` (which may contain real agency
content reflected back) but are tracked under `examples/rt_docx/*.feedback.md`
for feedback reports (which contain only structural metadata, not narrative
content).

## Consequences

- **Positive:** No accidental exposure of agency SSP content.
- **Positive:** Repository stays lean — no binary blobs.
- **Negative:** Full end-to-end testing requires local `.docx` fixtures that
  contributors must obtain separately. Documented in CONTRIBUTING.md.
- **Ongoing workflow:** New `.docx` fixture data must be added as golden
  JSON via `scripts/docx_catchall_dump.py` and committed as JSON.

## References

- `.gitignore`
- `scripts/pre-commit-check.sh`
- `scripts/docx_catchall_dump.py`
- `compliance/tests/fixtures/`
