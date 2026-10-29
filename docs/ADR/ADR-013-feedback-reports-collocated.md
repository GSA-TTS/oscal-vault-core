# ADR-013: Feedback Reports Co-Located with Round-Trip Outputs

**Status:** Accepted  
**Date:** 2026-07-18  
**Deciders:** Project architecture agent + user  
**Phase:** R11 (Unified Contract-Driven Converter) — feedback tooling

---

## Context

Evaluating extraction quality requires a structured feedback mechanism that
maps the document's heading tree to the extraction destinations in the
datacontract. The feedback must be co-located with the round-trip `.docx`
outputs so that reviewers can cross-reference them without hunting across
directories.

## Decision

The feedback pipeline generates two files per document alongside the
round-trip `.docx`:

```
examples/rt_docx/
  analytics-usa-gov-ssp-roundtrip.docx          # Re-serialized round-trip
  analytics-usa-gov-ssp-roundtrip.feedback.md   # Markdown feedback report
  analytics-usa-gov-ssp-roundtrip.feedback.docx # Word feedback report
```

The `.feedback.md` report contains:
- **TOC with extraction status** — every heading annotated with ✅ (all
  destinations filled), ⚠️ (unrouted content), or ❌ (empty destination)
- **Per-heading notes section** — structured template for human reviewers to
  add notes on headings with issues
- **Regex troubleshooting hints** — the `sourceHeading` pattern and
  `captureStrategy` from the contract, enabling direct regex iteration

The `.feedback.docx` is a Word-renderable version of the same report,
using the `_feedback_template.docx` style base, allowing non-technical
compliance reviewers to annotate directly in Word.

Feedback files are committed to git (unlike `examples/feedback/`, which
is gitignored for containing potentially sensitive reflected content). The
`.feedback.md` files contain only structural metadata (heading names,
field counts, routing status) — no narrative SSP content.

## Alternatives Considered

- **Feedback in a separate `docs/feedback/` directory** — Rejected: severs
  the spatial relationship between the round-trip output and its feedback;
  reviewers must navigate two directories.
- **Inline annotations in the round-trip `.docx`** — Rejected: Word comments
  are not git-diffable and break round-trip fidelity.
- **JSON feedback reports only** — Rejected: compliance reviewers expect
  Word documents; Markdown is more git-friendly than DOCX for the primary
  format.

## Consequences

- **Positive:** Reviewers can open the round-trip `.docx` and the
  `.feedback.md` side-by-side in a single directory.
- **Positive:** Feedback `.md` files are git-trackable, making quality
  improvement over time visible in git history.
- **Negative:** `examples/rt_docx/` accumulates one `.feedback.md` and one
  `.feedback.docx` per document per regeneration run. These must be committed
  explicitly — they are not auto-committed by the pre-commit hook.
- **Ongoing workflow:** Run `uv run python3 scripts/regenerate_all_docx.py`
  to regenerate all round-trip outputs and feedback reports.

## References

- `compliance/lib/docx_feedback.py`
- `compliance/lib/markdown_feedback.py`
- `compliance/scripts/generate_feedback.py`
- `scripts/regenerate_all_docx.py`
- `examples/rt_docx/`
- `.gitignore` (`examples/feedback/` rule)
