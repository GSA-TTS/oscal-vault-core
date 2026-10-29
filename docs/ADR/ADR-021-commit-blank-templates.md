# ADR-021: Commit the Official Blank GSA SSP Templates

**Status:** Accepted
**Date:** 2026-09-23
**Deciders:** Project architecture agent + user
**Phase:** R18 (reproducible roundtrip inputs)
**Amends:** [ADR-009](ADR-009-no-docx-in-git.md)

---

## Context

[ADR-009](ADR-009-no-docx-in-git.md) globally gitignores all `*.docx` because
FedRAMP SSP source documents contain agency CUI. That constraint is correct
for *customer* SSPs, but it also blocked committing the **official blank GSA
SSP templates** — the empty, publicly published FedRAMP rev5 templates that
every roundtrip demo, test, and jinjafier run needs as input.

The consequence documented in ADR-009 ("full end-to-end testing requires
local `.docx` fixtures that contributors must obtain separately") was a
recurring friction point: a fresh clone cannot run
`compliance/scripts/roundtrip_demo.py` against real blanks, and the demo's
registry expects them at `fismatic/<name>.docx`. The blanks are not CUI — they
are the same empty templates anyone can download from fedramp.gov — so the CUI
rationale of ADR-009 does not apply to them.

The R16 exception already committed the *jinjafied* versions
(`compliance/templates/*.jinja.docx`) on exactly this reasoning ("official
blanks + Jinja tags, no CUI"). Committing the un-jinjafied blanks is the same
category of artifact and makes the full `blank → jinja → filled → re-extract`
chain reproducible from a clean checkout.

## Decision

**Commit the official blank GSA SSP templates under `templates/`.** These are:

- `Low-SSPP-Template-[Contains-CUI]-10-16-2024.docx`
- `Moderate-SSPP-Template-[Contains-CUI]-10-16-2024.docx`
- `High-SSPP-Template-[Contains-CUI]-10-16-2024.docx`
- `Leveraged-SaaS-CRM-SSPP-Template-[Contains-CUI]-02-01-2024.docx`
- `MiSaaS-SSPP-Template-[Contains-CUI]-03-19-2025.docx`

The `[Contains-CUI]` marking is GSA's own filename boilerplate on the blank
template; the committed files contain **no filled-in customer content**.

Mechanics:

- `.gitignore` keeps the global `*.docx` rule and adds a negation
  `!templates/*.docx` (mirroring the existing `!compliance/templates/*.jinja.docx`).
- `scripts/pre-commit-check.sh` (the `no-docx` guard) adds `templates/*.docx`
  to its allowlist alongside the jinja exception.
- Derived/generated roundtrip outputs (`roundtrip_review/`,
  `examples/feedback/`, any `*.filled.docx`) remain **gitignored** — only the
  pristine blank inputs are tracked.

The roundtrip demo registry references `fismatic/<name>.docx`; the committed
copies live in `templates/`. A local symlink (or copy) from `fismatic/` to
`templates/` bridges them for the demo without duplicating binaries in git.

## Consequences

- **Positive:** A fresh clone can run the full end-to-end roundtrip demo and
  fixture-gated tests with no separate download step.
- **Positive:** Roundtrip inputs are pinned to exact template versions in
  history, so results are reproducible and diffable across template revisions.
- **Negative:** ~4 MB of binary blobs enter git history. Acceptable: these are
  stable, rarely-changing published templates, not per-customer documents.
- **Guardrail preserved:** The no-docx hook still blocks *all other* `.docx`
  (i.e. any customer/agency SSP), so ADR-009's CUI protection is intact for
  everything except these known-empty published blanks.

## References

- [ADR-009](ADR-009-no-docx-in-git.md) (amended)
- `.gitignore`
- `scripts/pre-commit-check.sh`
- `compliance/scripts/roundtrip_demo.py`
