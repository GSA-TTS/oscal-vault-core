---
title: Per-document DOCX integrity contract
description: Versioned accountable-preservation gates and local-only evidence handling
status: draft
tier: 2
last_updated: 2026-11-16
related_files: [README.md, docs/ADR/ADR-033-per-document-docx-integrity.md]
---

# Per-document DOCX integrity — v1.0.0

Lossless means accountable preservation, **not byte-identical ZIP archives**.
This AI-assisted harness is for local review, not authorization or submission.
No corpus run is implicit in installing or testing it.

```bash
python compliance/scripts/docx_e2e_integrity.py \
  --source /authorized/input/source.docx --authorized \
  --system-id my-system --workspace /path/to/system-workspace \
  --output /approved/local/docx-integrity-run/my-system-run-001 \
  --template-family moderate --fill-context /authorized/input/fill-context.json \
  --strict
```

Use the core's locked environment (`mise exec -- uv run --frozen python`) when
available. Supported family values: low, moderate, high, crm, lightweight,
unknown. Lightweight/unknown have no registered fill contract and block L3.
`--authorized` is an operator attestation, not a substitute for owner approval.

## Contract and gates

| Layer | Evidence | Gate |
|---|---|---|
| L0 | Source hash, exhaustive package inventory, IR manifest, binary artifact checksums | No strict package or loss-budget findings |
| L1 | Full source-located IR under regenerated vault `_source/nodes.json`; table/cell locators and merged-table records | Each enumerated IR record is retained and re-read exactly |
| L2 | Canonical sidecars, nonempty SSP export, actual validator log | Schema, semantic references and canonical→OSCAL parity pass |
| L3 | Jinjafy, fill, strict final re-extraction, Fismatic readback, package delta ledger | All declared tags recovered; no unmatched source/statement/whole-control writes, no collisions, status/origination/narrative parity; no unexplained package delta |

L1 deliberately reports **opaque retention**, not inferred semantic routing.
It also stores every bounded source ZIP member under `_source/package/<sha256>`
with a package manifest. An independent verifier compares the retained member
set and each member's bytes against the source, including media, custom XML,
styles and headers/footers. Corrupt, missing or unexpected retained members fail.
It preserves prose and complex tables without inventing canonical pointers.
The current v1 report does not certify per-field DOCX→canonical lineage or
native OSCAL mapping for every opaque record. L2 proves only the fields covered
by the existing validator. Human review must examine opaque debt separately.
All tables in the fallback coverage ledger are `preserved_only`, even if a
separate query projection exists; no partial row projection is promoted to full.

An explicit fill context must contain `values`, `source_sha256`, and `evidence`.
`values` contains exactly the declared contract tags with nonempty text.
Each tag's evidence has a run-relative `artifact` and RFC6901 `pointer` resolving
to that exact value, either in `source/nodes.json` or this run's regenerated
`vault/<id>/.snapshots/current/` canonical JSON. Paths cannot escape the run;
the envelope source hash must match. For example:

```json
{"values":{"description":"Source-bound prose"},"source_sha256":"<source digest>","evidence":{"description":{"artifact":"source/nodes.json","pointer":"/4/text"}}}
```

No synthetic fixture is substituted for a real SSP. Values not already captured
as exact source/canonical fields remain BLOCKED rather than silently authored.
Written control data comes from the freshly exported SSP, not a demo sample.
Control sets are compared bidirectionally after padding normalization; expected
and recovered collisions fail. Named statements require exact ID sets. A
whole-control description may read back as an unlabelled statement, but only
exact normalized prose agreement is accepted (no guessed named Part identity).
CRM responsibility/reference rows are preserved in the full IR and package
comparison; any changed wrapper or reference text needs reviewed disposition.

## Normalization and discrepancies

XML signatures use expanded namespace names, retain visible text whitespace,
child ordering, formatting, relationships, table geometry/merges, drawings,
section settings, numbering and content-control properties. Relationship IDs
in XML references resolve to relationship type/mode/target identity. Relationship
and content-type maps ignore member ordering. Only enumerated revision IDs,
paragraph/text IDs, core creation/modification timestamps and revision counters
are volatile. Arbitrary generated IDs, authors, font/style changes and wrappers
are **not** silently stripped. Non-XML parts use exact content hashes.

Substantive package deltas initially fail as `new_loss`. Run again into a fresh
directory with `--decisions /authorized/local/decisions.json` after review.
Each decision must bind `locator`, `before_sha256`, `after_sha256`, `category`,
stable `id`, `reviewer`, `date`, and `reason`. Wildcards are not supported.
Categories: volatile_serialization, template_boilerplate, catalog_reference,
structured_preserved, opaque_preserved, known_baselined_gap, new_loss, ambiguous.
Known debt warns **and prevents PASS**; new_loss and ambiguous always fail.
Reviewers must not approve an entire changed part without examining its delta;
part-level hash approval is conservative, not an automated proof of every write.

## Evidence and privacy

Outputs include `source/`, `vault/<id>/`, `oscal/`, `fillback/`, `compare/`, private
`logs/`, `local-processing.log`, and a UTC JSON-lines `transcript.log`.
Canonical sidecars and OSCAL remain in their native vault subdirectories,
`.snapshots/current/` and `.exports/oscal-json/`, avoiding duplicate sensitive data.
The run directory is private (0700, file creation mask 077), must be fresh,
must be outside the core, and must be Git-ignored if inside the workspace.
Source files are never copied into Git or overwritten. A final hash recheck
detects source changes during processing. Authorized temporary evidence must
be retained/removed under the owner's retention policy, not silently deleted.

Reports contain counts, hashes and locators by default. Raw IR, manifests,
rendered DOCX and parser logs can contain SSP prose/PII; keep the **entire run**
inside the approved environment. Do not publish even minimized reports without
review. Transcript commands include local paths; they are private too.

Exit 0: machine PASS; 1: FAIL; 2: BLOCKED; 3: INCOMPLETE. Missing input,
empty export, unavailable mapping/dependency, interrupted/timeout execution
never passes. Final reports always set human review to PENDING.

## Review and regression checklist

- Owner authorization and source hash/provenance recorded locally.
- Inspect L0 findings and every L1 fallback record; do not confuse inventory
  `structured` labels with proven structured projection.
- Review canonical/OSCAL parity and unsupported model debt.
- Review 100% narrative and control readback plus all structural deltas.
- Record stable debt IDs, owner, locator/hash, accountable disposition and
  remediation trigger in the local ledger before deferring work.
- Add synthetic shape-only regression tests for parser/fill/export fixes;
  never copy real SSP prose into fixtures.
- Run `mise run verify` and the slow `synthetic_contract_sentinel` gate.
- Rerun the affected source and an unaffected same-family template only after
  authorization; revalidate the consumer before any core pin update.
- Record reviewer, date and adopt/reference/decline/investigate decision.

See [ADR-033](ADR/ADR-033-per-document-docx-integrity.md). This v1 is a conservative
foundation: a machine PASS still requires human sign-off and does not certify
that opaque content has a supported writable DOCX or native OSCAL target.

The core-owned [review workflow and evidence ledger](DOCX_INTEGRITY_REVIEW.md)
includes generic registry setup, root causes, validation history and remaining
work. Registry and standalone relocation tests run in this core's test suite;
no consumer repository is needed to exercise synthetic integrity behavior.
