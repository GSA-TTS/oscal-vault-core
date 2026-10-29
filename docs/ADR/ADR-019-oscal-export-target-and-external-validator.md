# ADR-019: Two-Layer OSCAL Validation — NIST Schema (primary) + Compliance-Framework API (optional semantic oracle)

**Status:** Accepted
**Date:** 2026-07-20
**Deciders:** Project architecture agent + user
**Phase:** R-OSCAL-1 (OSCAL export hardening + external validation)

---

## Context

This project turns FedRAMP SSP `.docx` files into an Obsidian vault whose
type taxonomy (`oscal.*`, `oscal.x.*`, `okf.*`) is modeled on OSCAL, and then
emits derived OSCAL JSON via `compliance/lib/export_oscal.py` (driven from the
canonical JSON sidecars under each vault's `.snapshots/current/`). The
4-layer architecture is: **Authoring (markdown) → Canonical (JSON sidecars) →
Snapshot (time-stamped) → Export (OSCAL JSON/YAML)**.

Two gaps exist today:

1. **Our OSCAL export is partial and hand-rolled.** `export_oscal.py` produces
   a thin SSP / component-definition shell (metadata + system-characteristics +
   a synthetic by-component), targets `oscal-version: 1.0.4`, and does not emit
   a full `control-implementation.implemented-requirements[]` graph. There is
   **no independent check** that what we emit is actually ingestible by a real
   OSCAL consumer.
2. **We have no conformance oracle.** Nothing in CI proves our export is
   round-trippable against a standards-based implementation.

The user asked whether it makes sense to build a datacontract from the Obsidian
vault to the [compliance-framework/api](https://github.com/compliance-framework/api)
(CCF API) project — either as a **storage layer** and/or as a
**compiler/validator** that produces an OSCAL-ready export.

### What the investigation found

The CCF API was cloned to a scratch dir (not added to this repo), built, and
run via Docker Compose (`golang:1.26` + `postgres:17.5`). Live findings:

- **Language / stack:** Go 1.26, Echo v4 REST, GORM over Postgres 17, Swagger.
  License is **AGPLv3** (relevant — see Consequences).
- **OSCAL binding:** uses `github.com/defenseunicorns/go-oscal` **v0.7.0**,
  types package `oscal-1-1-3`. So CCF is an **OSCAL 1.1.3** implementation.
  The FedRAMP testdata it ships (`testdata/fedramp_ssp.json`) declares
  `oscal-version: 1.0.4` but is parsed against the 1.1.3 Go types.
- **Storage model:** every OSCAL model (`catalog`, `profile`,
  `system-security-plan`, `component-definition`, `assessment-plan`,
  `assessment-results`, `plan-of-action-and-milestones`) has a relational GORM
  model in `internal/service/relational/` with symmetric
  `UnmarshalOscal()` / `MarshalOscal()` methods. It is a fully normalized
  decomposition of OSCAL into ~24 handler areas and a deep association graph
  (the SSP export `Preload` chain alone is ~70 relations deep).
- **Ingest/emit contract:** both the CLI (`main.go oscal import|export`) and
  the REST API (`POST /oscal/import`, `GET /oscal/system-security-plans/...`)
  key strictly on the **top-level OSCAL wrapper object**
  (`system-security-plan`, `catalog`, `profile`, `component-definition`,
  `assessment-plan`, `assessment-results`, `plan-of-action-and-milestones`).
  Anything else is rejected as "unsupported document type". Documents are
  upserted by their OSCAL `uuid`.

### Live round-trip verified

Against the running service (schema migrated, then import → export):

```
oscal import -f testdata/fedramp_ssp.json   → Created SSP "FedRAMP System Security Plan (SSP)"
oscal import -f testdata/basic-catalog.json → Created Catalog
oscal import -f testdata/profile_fedramp_low.json → Created Profile
oscal export -t ssp -o /tmp/ssp_out.json    → Success

Verification of the exported SSP:
  uuid match (source vs exported):        True
  implemented-requirements (source):      20
  implemented-requirements (exported):    20
  metadata.title preserved:               "FedRAMP System Security Plan (SSP)"
```

This confirms CCF is a **credible, standards-grounded OSCAL 1.1.3 round-trip
implementation**. It is a useful *semantic/integration* oracle — but note (see
"Two things CCF is NOT" below) it is **not** the right tool for the fast,
primary syntactic conformance gate, and a passing round-trip does **not** by
itself prove zero data loss.

### Two things CCF is NOT

1. **Not a fast syntactic validator.** CCF is a fully-normalized relational
   engine (Echo + GORM + Postgres, migrations, deep preload graph). Standing it
   up per CI run is minutes of Go build + a database lifecycle. Proving our JSON
   is *syntactically* valid OSCAL does not need any of that — the official NIST
   OSCAL 1.1.3 JSON Schema + Python `jsonschema` does it offline in
   milliseconds.

2. **Not a lossless pass by default.** CCF ingests via `UnmarshalOscal` into
   relational tables and re-emits via `MarshalOscal`. Any field, `prop`, or
   extension namespace CCF has no column for **could be silently dropped** on
   the round-trip. *This is a risk to be verified, not an established fact* —
   OSCAL carries a first-class `props[]`/`links[]` extension mechanism and CCF
   preserves `props` on the models inspected, so well-formed extension props
   likely survive; loose non-standard top-level keys likely do not. A "round
   trip succeeded" assertion is therefore insufficient; the pass criterion must
   be a **semantic deep-diff** showing no loss of data we care about.

## Decision

**Adopt a two-layer validation strategy. CCF is the *optional, secondary*
integration oracle — NOT the primary gate — and is NOT adopted as a storage
layer.** Concretely:

1. **Keep markdown-as-source-of-truth unchanged.** The vault, canonical JSON
   sidecars, and snapshot layers remain authoritative. OSCAL export stays a
   *derived*, generated artifact (consistent with ADR-012 snapshot layer and
   the "canonical JSON is generated-only" rule).

2. **Layer 1 — fast, primary, offline syntactic gate.** A Python-native
   validator (`jsonschema` against the official NIST OSCAL 1.1.3 JSON Schema)
   runs on every export, in the default test suite and a pre-commit hook, with
   zero Docker/network/DB dependency. This is the conformance baseline.

3. **Layer 2 — slow, optional, Docker-gated semantic/integration oracle.** The
   CCF import→export round-trip runs as a separate, non-blocking-by-default CI
   lane (analogous to how `.docx`-dependent tests skip gracefully). Its pass
   criterion is a **semantic deep-diff** between our emitted OSCAL and CCF's
   re-exported OSCAL asserting **zero loss of the data we care about** (uuid
   stable, IR count/content preserved, our extension `props` intact) — *not*
   merely "import returned success". This catches real-world relational-store
   compatibility that Layer 1 cannot.

4. **Harden `export_oscal.py` to emit full OSCAL 1.1.3** — specifically a
   complete `control-implementation.implemented-requirements[]` graph built
   from the `oscal.control` notes (we already preserve raw OSCAL
   implementation-status/origination verbatim in `CanonicalControl` — see
   `canonical_model.py` — precisely so a round-trip "never has to guess").
   **Map vault-specific extension metadata (`okf.*`, `oscal.x.*`) onto standard
   OSCAL `props[]` with a custom `name`/`ns`, never as loose non-standard
   top-level keys**, so both the NIST schema (Layer 1) and CCF's relational
   mapper (Layer 2) preserve them.

5. **The "datacontract" between the vault and CCF is simply strict OSCAL JSON
   keyed by the top-level wrapper object.** No bespoke schema is needed — the
   contract *is* OSCAL. `go-oscal` v0.7.0 / `oscal-1-1-3` is CCF's concrete
   schema authority; the official NIST schemas are Layer 1's.

### On version: default 1.1.3, but do not hardcode

Retargeting from `1.0.4` to `1.1.3` is the correct default (it aligns with CCF
and modern tooling). But federal/FedRAMP consumers adopt schema versions
slowly, and some agency portals may still be pinned to 1.0.x. So the exporter
MUST keep `oscal-version` and any version-specific key shapes **parameterized**
(a target-version argument), not hardcoded inline. A full version-abstraction
layer (e.g. a per-version formatter/strategy object) is **explicitly deferred as
YAGNI** until a second concrete target version is actually required — the
immediate requirement is only that the version is a settable parameter, not a
constant.

### Explicitly rejected: full storage-layer integration

Making the vault read/write through the CCF API or its Postgres was considered
and rejected. It would invert source-of-truth (markdown → database), couple our
Python pipeline to a Go+Postgres service and its **AGPLv3** license, and add
heavy operational surface (JWT bootstrap, migrations, worker/email service
dependencies observed at runtime) for no authoring benefit. CCF is valuable
here as a *downstream consumer and validator*, not as our backing store.

## Consequences

**Positive**
- Closes the conformance gap with a **fast, offline, always-on** syntactic gate
  (Layer 1) — every export gets an independent standards-based pass/fail in
  milliseconds, no Docker required.
- Adds a **semantic** safety net (Layer 2) that catches relational-store
  incompatibility and data loss the schema check cannot see, without coupling
  it into the default pipeline.
- Zero new runtime coupling in the default pipeline; the CCF lane is optional
  and isolated.
- Teams that *also* run CCF as a compliance dashboard get a documented path
  (import our export) without us taking a hard dependency — a possible future
  opt-in "live sync" script, filed as follow-up, not built now.

**Negative / risks**
- **Silent data loss on the CCF round-trip (to verify):** CCF's relational
  mapper may drop fields/props/extensions it has no column for. Mitigated by
  making Layer 2's pass criterion a semantic deep-diff, and by mapping our
  extensions onto standard `props[]`. Must be empirically confirmed, not
  assumed.
- **OSCAL version skew:** we currently emit `1.0.4`; CCF and modern tooling use
  `1.1.3`. Mitigated by making the export version a parameter (default 1.1.3),
  so we can still emit 1.0.x for laggard agency portals. Tracked as follow-up.
- **Layer 2 operational cost:** the CCF lane needs Docker + Postgres and a
  multi-minute Go build; it must be a separate, non-blocking-by-default lane.
  Layer 1 carries the day-to-day load precisely to keep this cost off the hot
  path.
- **AGPLv3 boundary:** CCF must remain a separate process invoked over its
  CLI/HTTP boundary and never be linked into or vendored as source. Referencing
  by URL + pinned upstream commit (this ADR) keeps us clear of AGPL
  reciprocity.
- **Docker/network in CI:** the validation job needs Docker + Postgres and a
  multi-minute Go build; it must be a separate, non-blocking-by-default lane.

**Provenance**
- Upstream: `https://github.com/compliance-framework/api`
- Reviewed at: shallow clone of `main`, 2026-07-20 (pin the exact commit SHA
  when the CI validation job is implemented).
- The clone was performed in scratch space only and is **not** added to this
  repository (no submodule, no vendored code).

## Follow-up work (to be tracked as issues)

1. **Layer 1 syntactic validator** — add a `jsonschema`-based validator (+
   pre-commit hook + default-suite test) checking every export against the
   official NIST OSCAL 1.1.3 JSON Schema. Offline, no Docker. *This is the
   primary gate and should land first.*
2. **Full IR graph + extension props** — extend `export_oscal.py` to emit a
   complete OSCAL 1.1.3 `control-implementation.implemented-requirements[]`
   graph from `oscal.control` notes (statements, by-components,
   responsible-roles, control-origination), and map `okf.*` / `oscal.x.*`
   metadata onto standard OSCAL `props[]` (custom `name`/`ns`). *Depends on: #1
   (validate the richer output against the schema).*
3. **Parameterize export version** — make `oscal-version` (and any
   version-specific key shapes) a settable parameter, default `1.1.3`, still
   able to emit 1.0.x. Update downstream fixtures/assertions. Full
   per-version strategy object deferred as YAGNI. *Depends on: #2.*
4. **Layer 2 CCF integration lane** — optional, Docker-gated CI job: import our
   generated OSCAL into a throwaway CCF instance, re-export, and assert a
   **semantic deep-diff** with zero loss of data we care about (not just
   "import succeeded"). Pin the CCF commit SHA. *Depends on: #1 + #2 (need
   schema-valid, rich output to diff meaningfully).*
5. **(Optional, deferred)** a `--push` script that POSTs a generated export to
   a running CCF `POST /oscal/import` for teams running CCF as a dashboard.
   *No trigger yet — build only on concrete demand (YAGNI).*
