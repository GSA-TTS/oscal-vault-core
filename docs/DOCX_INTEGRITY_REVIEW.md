---
title: DOCX integrity review and implementation evidence
description: Core-owned review workflow, root causes, verification and remaining work
status: draft
tier: 2
last_updated: 2026-11-16
related_files: [docs/DOCX_INTEGRITY.md, docs/ADR/ADR-033-per-document-docx-integrity.md]
---

# Core-owned integrity review

This AI-assisted implementation and its tests are self-contained in this core.
No retiring consumer repository is needed. Source paths, corpus identifiers,
system authorization and generated reports remain in the authorized workspace,
never embedded in core. No real SSP corpus was processed during implementation.

## Initialize local review records

```bash
mise exec -- uv run --frozen python compliance/scripts/init_docx_integrity_registry.py \
  --workspace /path/to/system-workspace --system-id my-system \
  --output /approved/local/docx-integrity-run
```

Repeat `--system-id`, or explicitly select `--discover-vault-ids` to read immediate
`examples/vault/` directory names. Discovery does not read sources or authorize
processing. Every entry starts BLOCKED with unknown classification, null source
path/hash and no approver. Empty, duplicate or unsafe IDs fail before output.
Existing evidence is never overwritten; output must be outside core and ignored
if inside the workspace. `init_workspace.py` installs the integrity-run ignore rule.

Created files: `manifest.json`, `report-index.json`, `debt-ledger.json`,
`decision-log.json`. Keep all four private and local. Populate authorized source
path/hash/classification and owner approval only after explicit human review.

## Sequential per-document review

1. Register authorization, source hash, template family and expected layout.
2. Run the [single-document harness](DOCX_INTEGRITY.md) into a fresh run directory.
3. Review L0 package closure and retention; L1 structured/opaque/exclusion
   dispositions; L2 supported-field parity; L3 slots, control readback and deltas.
4. Record adopt/reference/decline/investigate with reviewer/date. Machine PASS
   does not constitute reviewed completion. Missing sources stay BLOCKED.
5. Record concrete debt immediately: stable ID, locator/hashes, accountable
   owner, remediation and blocking dependency/pickup trigger. Do not publish prose.
6. After a generalized fix, add synthetic shape-only regressions; run full verify
   and sentinel; then authorized source/same-family reruns and consumer parity
   before a separately approved immutable core pin update.

Prefer authorized published blanks first, then CRM-shaped systems, representative
modern SSPs, and remaining authorized sources sequentially. No fixed customer
queue belongs in this reusable core.

## Root causes and repairs

### PR #79 generic-preservation audit

FPDF evidence motivated generic regression tests, not customer-specific parsing
branches. Independent synthetic review reproduced additional failures: the
no-op narrative comparator erased punctuation/case/Unicode distinctions, and
long merged `Part b:` prose was routed to part `a`. Equality now ignores only
whitespace; standard named merged parts retain identity. Extended authored
labels such as `Part PT-07_01_` remain explicit fallback prose, not guessed IDs.

Checkbox labels are read from OOXML because python-docx omits SDT-contained
text from `Paragraph.text`. Only the owning changed editorial checkbox wrapper
is unwrapped; unrelated wrappers and semantic binding/lock/checkbox properties
remain. Synthetic save/reload tests cover wrapped toggles, unchanged wrappers,
Unicode/punctuation changes and numbered no-op cells. Normalization collisions
remain failing evidence and no longer overwrite the first observed value.

These tests reduce regression risk but do not prove support for every Word
layout. Full suite, real blank-template sentinel and unaffected family readback
remain required. No private FPDF source files were accessed in this audit.

Unaffected Moderate demo after review fixes: 49/49 narrative tags recovered,
332/332 control tables verified, zero unmatched/mismatched. The demo's blank
path was absent and it used the committed Jinja template; fresh Jinjafy was
SKIPPED in this run, not counted as a full source-to-template pass. Focused
generic/Fismatic/live-integrity regression run: 67 passed. Full core verify
passed: 1222 tests, 37 skips, 20 deselections; lint/format, security/dependency
audits and all 53 Mermaid blocks passed. Slow synthetic sentinel passed:
3 tests, 17 deselected in 275.73 seconds. Optional fixture skips are not
source-document passes.

### Parser versus compliance boundary

Both directories are maintained in this core, not separate products. Fismatic
owns legacy DOCX control-table recognition and raw read extraction; compliance
owns IR preservation, canonical mapping, export, writing and validation. The
different names reflect historical layering, but duplicated identifiers and
private helper imports cause coupling. Combining directories alone will not
remove those semantic differences and risks imports/schema/consumer contracts.
Prefer a separately approved incremental migration: shared public identifier
and table-layout primitives; typed raw parser boundary; deprecate path hacks;
then move modules with compatibility aliases and synthetic corpus parity gates.
No namespace merge is part of PR #79.

| Finding | Root cause | Repair and actual evidence |
|---|---|---|
| L1 serialization mistaken for independent preservation | JSON write/read does not prove full source retention | Every bounded ZIP member retained with manifest; independent byte/set verifier; corruption and extra-member tests |
| Extra controls escaped parity | One-directional expected→actual comparison | Bidirectional control sets and normalized-ID collision tests through actual Fismatic execution |
| Whole-control readback mismatch | Reader creates an unlabelled statement, while exporter uses whole-control description | Exact normalized prose comparison across those representations; named statements still require ID sets |
| Whole-control prose has no Part-row target | Empty synthetic control exports schema-required placeholder; Part-only template has no supported whole-control slot | Explicit unmatched fixture; supported answer-cell render/readback passes; no guessed Part identity |
| Unproven fill values | Plain context dictionary had no source/canonical binding | Source-hash envelope and RFC6901 pointers; altered values/hash/path bindings fail |
| Custom XML lost tails/space in signatures | Over-broad XML whitespace stripping | Preserve substantive mixed content; namespace-prefix and relationship-ID rewrite regressions |
| Staged core test failed after split | Test staged consumer repository and assumed __init__.py files rather than namespace packages | Core-only independent relocation test runs actual synthetic roundtrip in consumer-shaped temporary tree |
| Mermaid gate failed | Locked Node prerequisites absent | Approved frozen-lockfile install with lifecycle scripts disabled; actual invalid-parser and documentation tests |

## Durable remaining-work register

Owners require human maintainer assignment. Deferred work is not silently passed.

| ID | Status | Acceptance / pickup trigger |
|---|---|---|
| DI-002 | BLOCKED | Per-source authorization, corpus evidence and human sign-off; source owner approval required |
| DI-003 | REVIEW | Proven per-field DOCX→canonical→OSCAL lineage beyond opaque fallback; approve mapping scope and synthetic fixtures |
| DI-004 | REVIEW | Fine-grained XML delta evidence and automatic provenance-bound writable-slot decisions; retain substantive fail-closed gate |
| DI-005 | COMPLETE | Core PR #76 merged at 33d613bd5c8ccae3a959d3e9e2b9c56545b16ffd; consumer PR #130 merged at 7e98b63b9db5557f260718813016566428efe829 adopting that immutable pin. User reported review/merge; PR validation passed and local consumer parity/attestations passed. Core post-merge validation passed |
| DI-007 | VERIFYING | Thin CLI delegates to pipeline, fillback, context and render libraries; all integrity modules under 400 lines and functions under 50. Architecture regressions enforce bounded stages and no library imports of executable scripts; legacy CLI aliases retained. Full verify/sentinel and human PR review pending |
| DI-009 | FIXED | Consumer canonical check completed; content-keyed mapping cache invalidation tested. Corrected full consumer gate checked 5835 frontmatter notes and 5576 Dataview blocks; 52/52 exports schema/semantic valid, canonical parity and lineage attestations passed. Existing portfolio warnings and baselined debt remain visible |

## Verification transcript

Implementation used approved scoped edits in an isolated worktree, initially
without commits, pushes or pin changes. Later explicit user approvals authorized
publication through core PR #76 and consumer adoption PR #130. Both merged;
the user reported review and merge. No real-source DOCX corpus runs occurred,
and source corpus was not copied into core. Consumer validation read existing
workspace vaults and generated local ignored exports.

- Initial verify failed formatting; corrected and rerun. Early full-suite and
  sentinel attempts timed out at 120 seconds and were not counted as passes.
- Core `mise run verify`: PASS, 1173 tests, 37 skipped, 20 deselected;
  53 Mermaid blocks parsed across 75 files; lint, secrets/workflow security and
  Python/Node dependency audits passed. Local evidence, not a CI attestation.
- Slow sentinel repair rerun: PASS, 3 passed, 17 deselected, 401.95 seconds.
- Latest focused integrity tests before consolidation: PASS, 27 tests.
- Consumer workspace Python tests after staging repair: PASS, 17 tests.
- Core consolidation verify passed: 1182 tests, 37 skips, 20 deselections; security/audit/lint passed; 53 Mermaid blocks across 78 files.
- Final consumer-repair core verify passed: 1188 tests, 37 skips, 20 deselections in 154.41 seconds; 225 files formatted; security/audit passed; 53 Mermaid blocks across 78 files.
- Final slow synthetic sentinel passed: 3 tests, 17 deselected in 268.01 seconds.
- Consolidation slow sentinel: 3 passed, 17 deselected in 316.62 seconds.
- Consumer canonical drift/parity completed: 52/52 documents valid; configured baselines and lineage attestations passed. No baselines were waived or rewritten.
- Consumer frontmatter/wikilink discovery incorrectly excluded every note under an absolute hidden parent, producing zero-count PASS. Fixed to inspect vault-relative paths; regression verifies visible notes are checked and hidden children excluded. Corrected full consumer gate passed with real counts and unchanged baselines.
- Canonical check now fails on missing content hashes and generation errors. Content-keyed bounded mapping cache avoids reparsing unchanged YAML while invalidating on content changes; mutable return-list isolation tested.
- Consumer Taskfile verify/roundtrip no longer depend on setup, avoiding implicit submodule reset or installs. Core-owned Task dispatch regression covers delegation without setup/reset.
- PR #76 CI exposed an undeclared integration-test prerequisite: Task existed on the local machine but not in core's pinned CI environment. Declare Task 3.54.0 in core mise tools, matching the consumer; retain the actual dispatch test rather than skipping or mocking it. CI run 43924141591 otherwise passed 1190 tests; the missing executable was its sole test failure.
- Merged core [PR #76](https://github.com/GSA-TTS/oscal-vault-core/pull/76):
  GitHub validate and system-content guard passed. Core post-merge
  [validation run 43946716841](https://github.com/GSA-TTS/oscal-vault-core/actions/runs/43946716841)
  passed on commit `33d613bd5c8ccae3a959d3e9e2b9c56545b16ffd`.
- Merged consumer [PR #130](https://github.com/GSA-TTS/oscal-vault/pull/130):
  PR CI passed; top-level local verify passed through the adopted core, including
  5835 frontmatter notes, 5576 Dataview blocks, 52/52 valid OSCAL documents,
  canonical parity and lineage attestations. Existing warnings/debt were not waived.
- Consumer post-merge [validation run 43947901553](https://github.com/GSA-TTS/oscal-vault/actions/runs/43947901553)
  passed on `7e98b63b9db5557f260718813016566428efe829`, including pipeline gates
  and core-template roundtrip fill-back parity (4m10s).
- Consumer pin-only PR #129 was closed as superseded by #130, not conflict-resolved
  to the older pin: ancestry confirms `b4bd872` is included in `33d613bd`.
- DI-007 refactor verification: 40 focused architecture/live integrity/relocation
  tests passed. Full core verify passed with 1202 tests, 37 skips and 20
  deselections; lint/format, security/audits and 53 Mermaid blocks passed.
  Slow synthetic sentinel passed: 3 tests, 17 deselected in 385.82 seconds.
  Final full-suite rerun passed with 1203 tests (37 skips, 20 deselections),
  including the last unrelated-directory CLI regression; lint/security/audits
  and all 53 Mermaid blocks passed.

An early frozen UV invocation created a core-local virtualenv despite the initial
no-install scope; this was disclosed. Later Node installation was explicitly
approved and used 153 cached locked packages with lifecycle scripts disabled.
No dependency manifest, global configuration or CI workflow was changed.
Skipped optional backends/fixtures are not source integrity passes.
