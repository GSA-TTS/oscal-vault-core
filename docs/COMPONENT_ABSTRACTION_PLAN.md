# Component Abstraction & Coverage Uplift Plan

> **Status:** Draft — supersedes the verbal analysis from 2026-07-25  
> **Relates to:** `docs/PROJECT_REFACTORING_PLAN.md` (phases R9–R12)  
> **Last updated:** 2026-07-25  

---

## 0. Purpose and Scope

This document refines the July-25 conceptual mapping analysis into an
actionable implementation plan. It adds:

1. **Steering questions** that must be answered before writing code.
2. **Alternatives** to the proposed approach and their trade-offs.
3. **A sequenced phase list** with clear entry/exit criteria.
4. **Risk and constraint callouts** keyed to the project's existing Risk
   Register and Dataview Integrity Constraint.

> [!IMPORTANT]
> The Dataview Integrity Constraint is the project's highest-priority
> test gate. **Every frontmatter schema change in this plan must be
> accompanied by updated Dataview regression tests before merge.**
> See `docs/PROJECT_REFACTORING_PLAN.md §Dataview Integrity Constraint`.

---

## 1. Root Cause Recap (Agreed Baseline)

`build_synthetic_component_note()` in `compliance/lib/vault.py:627` emits
one flat `Components/<slug>.md` per system containing **all controls** with
`status: operational` and tag `component/synthetic`. This is the root of:

- Loss of per-component control attribution (who owns AC-2? Cloud.gov?
  GitHub Actions? Customer?)
- No inheritance chain resolution (`origination: inherited` has no
  `inherited_from` reference)
- The "scattered" vault graph — every `System → Component → Controls` path
  is identical across vaults

The **registry.yaml** file (`scripts/registry.yaml`) already has a
`components:` key for `<system>` (3 real components:
`Example Platform`, `GitHub Actions`, `CircleCI`). The other 9 systems have
no `components:` key at all. **This is the minimum viable data needed
before any code change.**

---

## 2. Steering Questions

Answer these before committing to any implementation approach:

### 2.1 On Scope Boundaries

> **Q1:** Is the goal *vault fidelity* (accurate representation of what
> the SSP says) or *compliance posture visibility* (knowing which controls
> each component is actually responsible for)?

These diverge. The first is a parsing problem (extract what's in the docx).
The second is an editorial problem (curate who owns what, which the SSP
may not record explicitly). The plan below addresses both, but the answer
determines **whether component data comes from docx extraction or from
hand-curated registry entries**.

---

> **Q2:** Do any of the 10 source docx files contain explicit per-component
> control tables (e.g. "Component: the platform provider | Control: AC-2 | Status:
> inherited")?

If yes → R11 docx extraction is the right path for those systems.
If no → registry-driven enrichment is faster and more reliable.

The crosswalk notes (`CROSSWALK_COVERAGE.md §2.4`) suggest fismatic's
`SSPConverter` never emits per-component detail, implying the docx
tables do not have it either. **Verify before assuming extraction is
possible.**

---

> **Q3:** What is the actual consumer of per-component data?

- Obsidian graph view / Dataview dashboards → needs wikilinks
- OSBAPI CRM API (`FullCrmDocument.by_components`) → needs UUIDs + typed
  structs
- OSCAL export (R6 export pipeline) → needs full `component-definition`
  OSCAL JSON

The answer scopes which output formats must be generated and which can be
deferred.

---

### 2.2 On the Control-Part Decomposition

> **Q4:** The original plan proposes `Controls/parts/<control_id>-part-<letter>.md`
> (`oscal.x.control_part`) as separate notes. Is part-level splitting
> worth the note explosion? A control like AC-2 has up to 12 parts.
> 10 systems × ~325 controls × avg 3 parts = ~9,750 new notes.

**Alternative:** keep part data as inline Dataview fields in the parent
control note (which is already done today with `statement_part::`,
`inheritance_mode::`, `implementation_status::`) and only split into
separate `oscal.x.control_part` notes when the part has a distinct
component attribution or evidence reference. This is a partial split that
keeps the vault manageable.

---

> **Q5:** The inline `inheritance_mode::` field on each statement part is
> currently hardcoded to `local`. Is there a trustworthy source in the
> source docx or OSCAL JSON to derive the per-part inheritance mode?

If no → changing the default from `local` to `undetermined` is the honest
fix and avoids misleading auditors. The `oscal.x.control_part` type already
has `inheritance_mode: undetermined` in its kind vocabulary.

---

### 2.3 On the Inheritance Chain

> **Q6:** The plan proposes populating `FederationDocument` with parent
> CSP authorization package references (e.g., "the provider FedRAMP Moderate
> P-ATO"). Where do these package IDs come from in practice?

Options (in order of reliability):
1. Hard-coded in `registry.yaml` per system (fastest, brittle)
2. Extracted from the docx "Leveraged Authorizations" section (would need
   R11 routing)
3. Looked up from the FedRAMP marketplace API (runtime dependency)

---

> **Q7:** The `oscal.x.inheritance` type is already auto-generated for
> controls with `origination: inherited`. The crosswalk notes that these
> notes exist but lack a resolved `provider` field. Is completing the
> `provider` field the right next step, or is linking the inheritance note
> back to a specific component (`implemented_by: example-platform-pages`) the
> higher value?

---

### 2.4 On R11 Sequencing

> **Q8:** R11 (`UnifiedConverter`) currently raises `NotImplementedError`
> on all five stages. The plan treats R11 completion as a prerequisite for
> docx-based component extraction. Is it feasible to implement **just the
> `heading_table_all` strategy for the "External Services" and
> "Asset Inventory" headings** without completing the full R11 pipeline?
> This would be a targeted R11 partial implementation rather than a full
> one.

**Recommendation:** Yes. Implement R11 Stage 1 (document tree extraction)
and Stage 2 (heading routing) as a standalone utility used **only** by
the component extractor. Full R11 Stage 3–5 (strategy, assembly, writing)
can remain `NotImplementedError` for non-component destinations.

Note: `ExtractedDoc.to_document_tree()` already exists in
`extractor_backend.py`. Stage 1 is largely a connection task.

---

## 3. Improvement Recommendations & Alternatives

### 3.1 Registry-First vs Docx-Extraction-First

**Original plan:** Declare components in datacontract YAML (1–2 days)
*then* extract real components from docx in R11 (3–5 days).

**Concern:** The datacontract (`fedramp-ssp-v1.yaml`) is a *generic*
contract covering all FedRAMP SSPs. Adding system-specific `components:`
blocks to it would make it non-generic. The per-system supplement is
`scripts/registry.yaml`, which already has a `components:` key on one
system.

**Recommendation — Registry-First (preferred):**
1. Complete `components:` entries in `scripts/registry.yaml` for all 10
   systems. This is editorial, not code, and can be done in hours.
2. Update `build_synthetic_component_note()` to call
   `build_real_component_notes()` when `registry_components` is non-empty,
   falling back to the synthetic note when the registry has no data.
3. Datacontract YAML changes are only needed if the unified converter
   (R11) will extract components from docx tables — which is an R11
   concern, not a prerequisite.

**Alternative — Datacontract-First:**  
Add a `components:` section to each system's *per-system datacontract*
(not the master `fedramp-ssp-v1.yaml`). This is cleaner architecturally
but requires creating 10 per-system YAML files, which is more work than
filling in registry.yaml.

---

### 3.2 Control-Part Split: Full vs Selective

**Original plan:** Generate `Controls/parts/<id>-part-<letter>.md` for
all controls and all parts (~9,750 new notes).

**Risks:**
- Note explosion creates Dataview query performance problems (Obsidian
  processes all files on load).
- Template and validation infrastructure for `oscal.x.control_part` is
  currently `⚠️ Needs creation` (per `CROSSWALK_COVERAGE.md §7`).
- Every new note type needs a dashboard query — the Dataview Integrity
  Constraint will require new tests before merge.

**Recommendation — Selective Split:**  
Only generate `oscal.x.control_part` notes when **both** of the following
are true:
1. The part has a distinct `component_attribution` (different from the
   system-level default).
2. The part has an inheritance mode that differs from the parent control.

This keeps the common case (all parts locally implemented by the same
component) as inline fields in the parent control note, and only explodes
parts into separate notes when the graph genuinely needs the edge.

**Alternative — No Split:**  
The existing inline `statement_part::` / `inheritance_mode::` / 
`implementation_status::` Dataview fields are sufficient for dashboards.
Defer `oscal.x.control_part` notes entirely until an auditor or workflow
actually requires them. This is valid if the consumer (Q3) is primarily
the Obsidian vault, not the OSCAL export pipeline.

---

### 3.3 Inheritance Chain: Provider Resolution

**Original plan:** Datacontract declares parent CSP authorization packages;
`oscal.x.inheritance` notes link provider → consumer.

**Concern:** The `FederationDocument` schema shape exists but is empty.
The existing auto-generated `Inheritance/` notes have `provider: null`.
Resolving this requires knowing which parent CSP packages each system
inherits from — data that is not in the docx (for most systems).

**Recommendation — Two-Phase:**

*Phase A (registry-driven, fast):*  
Add `leveraged_authorizations:` to `scripts/registry.yaml` for each system:
```yaml
leveraged_authorizations:
  - id: example-platform-fedramp-moderate
    name: the provider FedRAMP Moderate P-ATO
    href: https://marketplace.fedramp.gov/products/F1607067912
    provider_system: the platform provider
```
Update `build_inheritance_note()` in `vault.py` to populate `provider:`
from this registry data when present.

*Phase B (docx-extraction, R11):*  
Route the "Leveraged Authorizations" table in the docx to
`oscal.x.inheritance` notes via the R11 unified converter. This closes
the extraction gap without requiring manual registry maintenance long-term.

**Alternative — FedRAMP Marketplace API Integration:**  
Fetch authorization package metadata at vault generation time from
`https://marketplace.fedramp.gov/api/products`. This adds a runtime
network dependency but keeps registry.yaml clean. Only viable if the
pipeline can run with network access.

---

### 3.4 Dataview Dashboard Strategy

**Original plan:** Add a "Component coverage matrix" Dataview query to
`Index.md`.

**Concern:** The current `Index.md` template in `build_index_note()` is
generated programmatically from `vault.py`. Adding new queries requires
changing the Python function, not just editing the markdown. This is an
intentional design choice (generated files are authoritative), but it
means dashboard changes require code changes.

**Recommendation:**  
Add a per-system `Components/index.md` note (`okf.index` with
`index_kind: system`) rather than adding more queries to the root
`Index.md`. This gives each system a dedicated component dashboard without
cluttering the root index. The `okf.index` type already supports
`index_kind` sub-typing for this use case.

```dataview
TABLE component_kind, implementation_model, fedramp_package,
      length(implements_controls) AS "Controls"
FROM "Components"
WHERE system_id = this.system_id
SORT component_kind ASC
```

---

## 4. Revised Implementation Phases

### Phase C1 — Registry Completion (1–2 days)

**Entry:** `registry.yaml` has `components:` only for `<system>`.

**Work:**
1. Research and fill in `components:` for all 10 systems in
   `scripts/registry.yaml`. Use publicly available FedRAMP SSP summaries
   and the existing docx corpus (without code changes).
2. Add `leveraged_authorizations:` for each system (provider name, FedRAMP
   package ID, mode: full/partial).
3. No code changes; no test changes. Pure data editorial.

**Exit:** `scripts/registry.yaml` has `components:` and
`leveraged_authorizations:` for all 10 systems.

**ADR needed:** None (no architectural change).

---

### Phase C2 — Multi-Component Note Generation (2–3 days)

**Entry:** C1 complete.

**Work:**
1. Add `build_real_component_notes(registry_entry, index)` to `vault.py`.
   - Accepts the `components:` list from `registry.yaml` for the system.
   - Emits one `oscal.component_definition` note per component with
     `component_kind`, `implementation_model`, `fedramp_package`,
     `responsible_party`.
   - Does **not** yet populate `implements_controls` (that requires C3).
   - Tags: `component/type/<kind>` (no `component/synthetic`).
2. Update `build_synthetic_component_note()` to call
   `build_real_component_notes()` when `registry_components` is non-empty,
   and fall back to synthetic generation when empty.
3. Update `build_system_note()` to populate `components:` with wikilinks
   to all real component notes, not just the single synthetic one.
4. Add `oscal.x.runbook`, `oscal.x.parameter`, `oscal.x.control_part`
   templates to `_meta/templates/` (currently `⚠️ Needs creation` per
   crosswalk). Required to satisfy the Dataview Integrity Constraint before
   the next full regeneration.

**Tests required (Dataview Integrity Constraint):**
- `test_vault.py`: assert `build_real_component_notes()` produces N notes
  for N registry components.
- `validate_dataview_queries.py`: assert the existing "System component
  breakdown" query in `Index.md` returns rows for all component kinds.
- New test: assert no note has `tags: [component/synthetic]` when the
  registry has real components.

**Exit:** All 10 vaults regenerated with real component notes. Zero
`component/synthetic` tags for systems with registry data. All tests pass.

**ADR needed:** ADR-016 — Real Component Generation from Registry.

---

### Phase C3 — Per-Component Control Attribution (3–4 days)

**Entry:** C2 complete.

**Work:**
1. Add `component_attribution:` to registry component entries — a mapping
   of `control_id` patterns to component slugs:
   ```yaml
   components:
     - name: Example Platform
       component_slug: example-platform-component
       owns_controls:
         - pattern: "AC-*"
           mode: inherited
         - pattern: "AU-*"
           mode: inherited
     - name: GitHub Actions
       component_slug: github-actions
       owns_controls:
         - pattern: "SA-11"
           mode: local
   ```
2. In `build_control_note()`, resolve the control's primary component from
   the attribution table and add `component: [[Components/<slug>]]` to the
   frontmatter. Fall back to system-level component when unresolved.
3. In `build_real_component_notes()`, populate `implements_controls:` by
   reading the attribution table (filtered per component).
4. Update `inheritance_mode::` inline fields in control note body from
   `local` to the resolved mode for each statement part.

**Selective `oscal.x.control_part` split (per §3.2 recommendation):**
- Only generate `Controls/parts/<id>-<letter>.md` when the part's
  attribution component differs from the parent control's component, OR
  when `inheritance_mode` differs from the parent.
- Add `oscal.x.control_part` template (from Phase C2 work) and one
  Dataview dashboard query in `Components/index.md`.

**Tests required:**
- Assert `implements_controls` on a component note matches the registry
  attribution for that component.
- Assert no `Controls/parts/` note exists for controls where all parts
  share the same component attribution and inheritance mode.

**Exit:** Component notes have `implements_controls` lists. Control notes
have `component:` wikilinks. `Controls/parts/` notes exist only for
genuinely split attributions. All 85+ tests pass.

---

### Phase C4 — Inheritance Chain Resolution (2–3 days)

**Entry:** C1 and C2 complete (C3 optional).

**Work:**
1. Update `build_inheritance_note()` to populate `provider:` from the
   `leveraged_authorizations:` registry entries. Resolve to a wikilink in
   `Organizations/<slug>.md` when the provider organization note exists.
2. Add `consumer: [[Systems/<slug>]]` back-reference to each inheritance
   note.
3. Add `controls:` list to each inheritance note — the subset of the
   system's controls with `origination: inherited` that have no other
   attribution.
4. Add a `residual_responsibility:` field (free text, from registry or
   docx extraction) describing what the CSP leaves to the customer.
5. Update `FederationDocument` schema in `osbapi-oscal-json-schemas.json`
   to add `leveraged_system_fedramp_package` field.

**Tests required:**
- `test_inheritance.py`: assert every inheritance note with `provider: null`
  in the current vaults is replaced by a resolved wikilink after Phase C4.
- Assert `consumer:` field is present and valid on all inheritance notes.

**Exit:** All inheritance notes have resolved `provider:` wikilinks. Zero
`provider: null` values in systems covered by registry `leveraged_authorizations`.

**ADR needed:** ADR-017 — Inheritance Chain Resolution Strategy.

---

### Phase C5 — Dashboard & Validation Uplift (1–2 days)

**Entry:** C2 complete.

**Work:**
1. Add `Components/index.md` per system (type `okf.index`,
   `index_kind: system`) with component coverage matrix query.
2. Add inheritance burndown query to root `Index.md` (count of inherited
   controls with vs. without resolved `provider`).
3. Add evidence gap query (count of controls with zero evidence notes).
4. Update `validate_dataview_queries.py` to cover all new queries.
5. Add `okf.validation_rule` notes for:
   - Every component must link to at least one control.
   - Every inheritance note must have a non-null `provider`.
   - Every `oscal.x.control_part` note must have `part_id` and
     `control_id`.

**Exit:** All new Dataview queries pass `validate_dataview_queries.py`.
All new validation rules documented in `governance/validation-rules/`.

---

### Phase C6 — R11 Partial: Component Table Extraction (3–5 days)

**Entry:** C2 complete. Answers to Q2 (do docx files contain component
tables?) confirmed.

**Work (only if Q2 = yes):**
1. Implement R11 Stage 1 (`extract_document_tree`) for the `FismaticBackend`
   — a thin wrapper over the already-implemented `FismaticBackend.extract()`
   in `extractor_backend.py` that converts `ExtractedDoc` to `DocumentTree`.
   Note: `to_document_tree()` already exists on `ExtractedDoc` — this is
   largely a connection task.
2. Implement R11 Stage 2 (`route_document_tree`) for the
   `heading_table_all` strategy only, targeting the headings:
   - `"External Services"` → `oscal.component_definition` notes
   - `"Asset Inventory"` → `oscal.component_definition` notes
   - `"Systems Providing Controls"` → `oscal.x.inheritance` notes
3. Wire the result into `build_real_component_notes()` as a fallback when
   registry data is absent.

**Tests required:**
- Acceptance test: run the partial R11 extractor on golden fixture JSON
  and assert component notes are emitted.
- Parity test: assert registry-driven and docx-extraction-driven component
  notes have the same frontmatter schema (same fields, possibly different
  values).

**Exit:** At least one system gets real component notes from docx extraction.
Full R11 `NotImplementedError` stubs remain for all other strategies.

---

## 5. Alternative Sequencing: Extraction-First

If the answer to Q2 is "yes, docx tables do contain component data", the
optimal sequence inverts Phases C1 and C6:

```
C6 (partial R11 extraction) → C2 (multi-component note gen) → C3 (attribution) → C4 (inheritance) → C5 (dashboards)
```

This gives registry.yaml the extracted values as a starting point rather
than requiring manual research. The downside is higher upfront engineering
cost before any visible vault improvement.

---

## 6. Coverage Impact Projection

| Layer | Current | After C1–C5 | After C6 |
|---|---|---|---|
| **System Implementation — Components** | 20% (synthetic) | 70% (registry-driven) | 85% (docx-extracted) |
| **Control Implementation — by-components** | 5% (synthetic UUID) | 60% (attribution table) | 75% (docx-extracted) |
| **Leveraged Authorizations** | 30% (notes exist, provider=null) | 80% (provider resolved) | 90% |
| **oscal.x.control_part** | 0% (type defined, no notes) | ~15% (selective split) | ~30% |
| **OKF Dashboards** | 85% | 92% | 95% |

---

## 7. Risk Register Additions

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Registry component data is stale or wrong | Medium | High | Add a CI check that every `registry.yaml` component has a corresponding `fedramp_package` and `responsible_party` field. |
| Note explosion from `oscal.x.control_part` split | High | Medium | Use selective split (§3.2). Gate behind `--split-control-parts` CLI flag. |
| Attribution table too coarse (pattern matching) | Medium | Medium | Use `exact` match as the default; support `glob:` and `regex:` the same way `sourceHeading` does in the datacontract. |
| Registry-docx divergence after R11 | Low | Medium | R12 parity test suite already planned. Extend to assert registry attribution matches docx-extracted attribution. |
| Inheritance provider resolution wrong | Medium | High | Require human review on all `provider:` fields before removing `⚠️ unresolved` tag. Add a CI rule that warns when `provider: null` exists. |

---

## 8. Open Decisions (Needs Owner Assignment)

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | Where does system-specific component data live? | `registry.yaml` (current supplement) vs. per-system datacontracts vs. the master datacontract | `registry.yaml` for registry-driven path; per-system datacontract for R11 extraction path |
| D2 | Is `oscal.x.control_part` selective or universal? | All parts split vs. only cross-component parts | Selective (§3.2) |
| D3 | Inheritance provider source | Registry vs. docx extraction vs. FedRAMP API | Registry first (Phase C4), docx second (Phase C6) |
| D4 | Component dashboard placement | Add to root `Index.md` vs. `Components/index.md` | `Components/index.md` per system (§3.4) |
| D5 | When does `component/synthetic` tag become an error? | Never vs. after C2 completes vs. immediately | After C2 completes; add a validation rule |

---

## 9. Relationship to Existing Phases

```
R10 ✅  R2 ✅
        ↓
C1 (Registry Completion)      — parallel to R9 crosswalk
        ↓
C2 (Multi-Component Notes)    — closes R8 regeneration gap
        ↓
C3 (Control Attribution)      — depends on C2
C4 (Inheritance Resolution)   — depends on C1, C2
C5 (Dashboard Uplift)         — depends on C2
        ↓
C6 (Partial R11 Extraction)   — depends on C2; feeds into full R11
        ↓
R11 (Full Unified Converter)  — depends on C6 partial work
        ↓
R12 (Benchmark Suite)         — depends on R11
```

Phases C1–C5 can be completed before R11 without blocking it. Phase C6
is a partial R11 implementation that reduces the R11 surface and validates
the Stage 1–2 design before the full five-stage commit.

---

## 10. First Concrete Steps

> [!TIP]
> The fastest path to visible improvement with zero code changes:

1. **Fill in `registry.yaml`** — add `components:` and
   `leveraged_authorizations:` for all 10 systems. ~2–4 hours of research.
2. **Verify Q2** — open the first docx file and check whether "External
   Services" or "Asset Inventory" tables have per-component rows. ~30 min.
3. **Answer D1–D5** above. ~1 hour discussion.

Once those three steps are done, Phase C2 can begin with full confidence
in the approach.
