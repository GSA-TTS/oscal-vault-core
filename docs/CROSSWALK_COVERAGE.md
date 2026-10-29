# Crosswalk & Coverage Report: OSCAL ↔ OSBAPI ↔ Backstage ↔ Obsidian

> **Last updated:** 2026-10-15 (R30–R35 reconciliation)
> **Source of executable mapping truth:** `compliance/datacontracts/fedramp-ssp-v1.yaml`

Compares the OSCAL SSP concepts covered by two adjacent repos:

| Repo | Purpose |
|---|---|
| `fedramp-automation` (this repo) | Parses FedRAMP SSP `.docx` → OSCAL SSP JSON → OSBAPI CRM projection → Backstage catalog entities → Obsidian vault. The pipeline layer between raw OSCAL and downstream tools. |
| `oscal-static-site-playground` (adjacent) | Gatsby static site rendering official NIST OSCAL schemas (SSP, Catalog, Profile, Component Definition, Assessment Plan, Assessment Results, POA&M) with GraphQL query surfaces. A reference implementation of the full NIST OSCAL model. |

---

## 0. Type Taxonomy Migration (Phases R1-R5, R9, 2026-07-18)

The vault now uses a dotted namespace type convention:

| Namespace | Purpose | Implemented Types |
|-----------|---------|------------------|
| `oscal.*` | Canonical NIST OSCAL model concepts | `oscal.catalog`, `oscal.profile`, `oscal.component_definition`, `oscal.control`, `oscal.ssp`, `oscal.assessment_plan`, `oscal.assessment_results`, `oscal.poam`, `oscal.party` |
| `oscal.x.*` | Project extensions beyond NIST OSCAL | `oscal.x.diagram`, `oscal.x.evidence`, `oscal.x.finding`, `oscal.x.inheritance`, `oscal.x.boundary`, `oscal.x.decision`, `oscal.x.relationship`, `oscal.x.runbook`, `oscal.x.parameter`, `oscal.x.control_part` |
| `okf.*` | Vault governance infrastructure | `okf.schema`, `okf.index`, `okf.instruction`, `okf.validation_rule`, `okf.mapping`, `okf.template` |

Each domain note carries: `type` (dotted), `oscal_model` or `extension_model`,
`kind` (canonical|extension), plus backward-compatible `note_type` (flat string).
An `oscal.x.*` type can still be an authoring projection while exporting through
native OSCAL: the datacontract classifies it as `authoring_extension`, records
`legacyExtensionPath`, and validates its `nativeOscalPaths` against an exporter
adapter. `true_extension` and `unimplemented` mappings remain explicitly
non-native rather than implying an `extension.*` OSCAL target.
Claims are property-specific: external services are OSCAL component props,
while capabilities come from component-linked controls. Boundary/relationship/
inheritance properties not actually consumed and standalone CRM narrative notes
are classified `unimplemented`; CRM `.ods` fields merged into control notes are
the source consumed by native SSP `by-component.export`.
See `archive/plans/PROJECT_REFACTORING_PLAN.md` for the full type registry with kind fields.

### Current executable coverage controls

- Canonical sidecars carry typed `table_records` with stable table/row locators.
  The table coverage gate baselines existing unmapped records and rejects new
  unmapped table debt.
- Source instructions render as typed `okf.instruction` notes with source
  locator/hash provenance. The instruction ledger reconciles source candidates
  against generated Markdown and canonical sidecars.
- Native OSCAL claims are property-level contract entries validated against
  vendored schemas and exporter adapters; unsupported claims remain explicitly
  `unimplemented` rather than being inferred from a broad object prefix.
- Portable JSON/HTML reviewer reports read canonical sidecars directly. Bases
  and Dataview remain non-authoritative projections with headless validation.

---

## 1. OSCAL document types: coverage matrix

| OSCAL model | `oscal-static-site-playground` | `fedramp-automation` | Notes |
|---|---|---|---|
| **SSP** (System Security Plan) | ✅ Full NIST schema (6,452 lines). GraphQL + TypeScript model. Two sample queries (MathUSR, Walmart). | ✅ Subset. Input from fismatic `SSPConverter` → `OscalSspInputDocument` → projections. Parses only `control-implementation.implemented-requirements` (control status/origination/statements/roles) + `system-characteristics` + `metadata`. Does **not** model remaining SSP blocks (see §3 gaps). | Primary pipeline input. |
| **Catalog** | ✅ Full NIST schema (254,986 lines). Page at `/oscal_catalog.js`. | ❌ Not covered. No control-catalog ingestion or base-control definitions. | Controls are parsed from docx tables, not catalog definitions. |
| **Profile** | ✅ Full NIST schema (2,695 lines). Page at `/oscal_profile.js`. | ❌ Not covered. The CRM profile `osbapi-crm-profile/v1` is referenced by URI only — no local profile definition. | Future: Phase 13 (schema-drift check) would diff against a profile. |
| **Component Definition** | ✅ Schema (99 lines). Page at `/oscal_component.js`. | ⚠️ Partial. Vault emits synthetic `Components/<slug>.md` from SSP data. `FullCrmDocument.by_components` projects controls against a single synthetic component UUID. No real component-definition ingestion. | fismatic's `SSPConverter` does not emit separate component definitions. |
| **Assessment Plan** | ✅ Full NIST schema (2,378 lines). Page at `/oscal_assessment-plan.js`. | ❌ Not covered. Vault schema has `VaultAssessmentFrontmatter` for manually-created notes but no auto-generation from assessment-plan data. | |
| **Assessment Results** | ✅ Full NIST schema (2,473 lines). Page at `/oscal_assessment-results.js`. | ❌ Not covered. Same as Assessment Plan — frontmatter schema exists but no generator. | |
| **POA&M** | ✅ Full NIST schema (1,004 lines). Page at `/oscal_poam.js`. | ❌ Not covered. No POA&M tracking. | Future: vault `Assessments/` notes could track findings. |

---

## 2. OSCAL SSP concept-by-concept crosswalk

For each standard OSCAL SSP concept (from NIST's `system-security-plan` model), whether and how `fedramp-automation` covers it.

### 2.1 Metadata block

| OSCAL SSP field | Covered? | Where |
|---|---|---|
| `metadata.title` | ✅ | `OscalSspInputDocument.metadata.title`; vault `VaultPlanFrontmatter.title` |
| `metadata.last-modified` | ✅ | `OscalSspInputDocument.metadata.last-modified` |
| `metadata.version` | ✅ | `OscalSspInputDocument.metadata.version`; vault `VaultPlanFrontmatter.version` |
| `metadata.oscal-version` | ✅ | `OscalSspInputDocument.metadata.oscal-version`; vault `VaultPlanFrontmatter.oscasl_version` |
| `metadata.roles` | ✅ | `OscalSspInputDocument.metadata.roles`; vault organization generation uses role IDs. No role-to-control link beyond `responsible-roles[].role-id`. |
| `metadata.parties` | ✅ | `OscalSspInputDocument.metadata.parties`; used to generate `Organizations/*.md` vault notes. |
| `metadata.locations` | ❌ | Not modeled. Physical addresses not relevant to current pipeline scope. |
| `metadata.revisions` (revision-history) | ❌ | Not modeled. No change-history tracking across SSP versions. |
| `metadata.responsible-parties` | ⚠️ | `responsible_role` on `CanonicalControl` stores a single role reference. No party-role link to specific controls beyond what fismatic extracts from the docx table. |
| `metadata.document-ids` | ❌ | Not modeled. |
| `metadata.props` (properties) | ❌ | Not modeled. Key/value metadata with namespaces. |

### 2.2 Import Profile

| OSCAL SSP field | Covered? | Where |
|---|---|---|
| `import-profile.href` | ✅ | `OscalSspInputDocument.import-profile.href`; passed as `CRM_PROFILE_URI` in `build_crm_projection.py`. |

### 2.3 System Characteristics

| OSCAL SSP field | Covered? | Where |
|---|---|---|
| `system-characteristics.system-name` | ✅ | `OscalSspInputDocument`; vault `Systems/<slug>.md` (CLI `--system-name` override available). |
| `system-characteristics.description` | ✅ | Extracted for System vault note body. |
| `system-characteristics.system-ids` | ✅ | `OscalSspInputDocument.system-characteristics.system-ids`. |
| `system-characteristics.security-sensitivity-level` | ✅ | `OscalSspInputDocument`; vault `fips_199` field. CLI `--fips-199` override available. |
| `system-characteristics.security-impact-level` | ✅ | Full confidentiality/integrity/availability objectives in `OscalSspInputDocument` and vault `VaultSystemFrontmatter`. |
| `system-characteristics.status.state` | ✅ | `OscalSspInputDocument`; vault `VaultSystemFrontmatter.status`. |
| `system-characteristics.authorization-boundary` | ✅ | `OscalSspInputDocument`; vault body `## Authorization Boundary` and frontmatter. |
| `system-characteristics.system-information` / `information-types` | ⚠️ | Partially modeled in schema (`system-information.information-types` as generic array). Detailed NIST 800-60 information-type categorization not extracted. |

### 2.4 System Implementation

| OSCAL SSP field | Covered? | Where |
|---|---|---|
| `system-implementation.remarks` | ❌ | Not modeled. |
| `system-implementation.users` | ❌ | Not modeled. User access/privilege details. |
| `system-implementation.components` | ⚠️ | Vault emits a single synthetic `Components/<system>.md` note. `FullCrmDocument.by_components` uses a single synthetic component UUID. No real component-definition ingestion from the SSP. |
| `system-implementation.inventory-items` (system-inventory) | ❌ | Not modeled. Hardware/software inventory. |
| `system-implementation.leveraged-authorizations` | ⚠️ | `FullCrmDocument.leveraged_authorizations` has the schema shape (id, party_uuid, title, href) but is not populated from fismatic output. |
| `system-implementation.data-flow` / `network-architecture` | ❌ | Not modeled. Network diagrams and data flow descriptions. |

### 2.5 Control Implementation

| OSCAL SSP field | Covered? | Where |
|---|---|---|
| `control-implementation.description` | ✅ | `OscalSspInputDocument.control-implementation.description`. |
| `control-implementation.implemented-requirements` | ✅ | The primary pipeline input. Parsed into `CanonicalControl` → `ControlSummary` → vault `Controls/<id>.md`. |
| `implemented-requirements[].control-id` | ✅ | Maps to `CanonicalControl.control_id` → `ControlSummary.control_id` → vault `control_id`. |
| `implemented-requirements[].uuid` | ✅ | Preserved in `OscalSspImplementedRequirement`. Not projected into OSBAPI/Backstage (OSCAL UUIDs are internal). |
| `implemented-requirements[].implementation-status.state` | ✅ | `CanonicalControl.oscal_implementation_status` preserves raw value. `CanonicalControl.implementation_status` carries the OSBAPI-mapped enum. |
| `implemented-requirements[].control-origination` | ✅ | `CanonicalControl.oscal_origination` preserves raw list. `CanonicalControl.origination` carries the single OSBAPI enum (picked by precedence). |
| `implemented-requirements[].statements` | ✅ | `statement-id` + `description` → vault `## Implementation` `### Part X` narrative body. |
| `implemented-requirements[].responsible-roles` | ✅ | Extracted as `role-id` → vault `responsible_role` wikilinks. |
| `implemented-requirements[].by-components` | ⚠️ | Modeled in `FullCrmDocument` as `OscalImplementedRequirementProjection.by_components` with a single synthetic component. The source SSP has no real per-component breakdown. |
| `implemented-requirements[].remarks` | ⚠️ | `CanonicalControl.remarks` carries a concatenated summary of mapping remarks. |
| `implemented-requirements[].links` (related controls, evidence links) | ❌ | Not extracted. Related controls are heuristically detected from statement descriptions in the vault generator but not taken from explicit `links` blocks. |
| `implemented-requirements[].props` (properties) | ❌ | Not extracted. Key/value metadata on implemented requirements. |

### 2.6 Back Matter

| OSCAL SSP field | Covered? | Where |
|---|---|---|
| `back-matter.resources` | ❌ | Not modeled. Attachment references, citations, resource links. |
| `back-matter.resources[].attachments` | ❌ | Not modeled. |
| `back-matter.resources[].rlinks` | ❌ | Not modeled. |
| `back-matter.resources[].citation` | ❌ | Not modeled. |
| `back-matter.resources[].document-ids` | ❌ | Not modeled. |

---

## 3. OSBAPI extension namespace — what `fedramp-automation` adds beyond NIST OSCAL

These concepts exist in `fedramp-automation` but have **no equivalent in the standard NIST OSCAL SSP model**. They are the "extension namespace" this pipeline projects OSCAL onto.

> **Prefer OSCAL core.** Where a concept *does* have a native OSCAL home, the
> pipeline now uses it instead of an extension (see
> [`osbapi-backstage-interop/capabilities-as-osbapi-offerings.md`](osbapi-backstage-interop/capabilities-as-osbapi-offerings.md)).
> In particular, an **OSBAPI service offering** is modeled as a
> `component-definition.capabilities[]` `capability` that declares the controls
> it provides via `control-implementations[].implemented-requirements[]`, and
> the **CRM inheritance statement** (what the provider satisfies vs. what the
> consumer must still do to fully inherit) is carried in
> `by-component.export.provided[]` / `.responsibilities[]` — all OSCAL core, no
> custom namespace. The OSBAPI enum/projection `$defs` below remain for the
> *downstream catalog summary*, not as the inheritance source of truth.

| Extension concept | Schema def | Purpose |
|---|---|---|
| `ImplementationStatus` (OSBAPI enum) | `#/$defs/ImplementationStatus` | Coarser enum than OSCAL's `implementation-status.state`. 6 values vs. 7. Maps OSCAL → OSBAPI via `compliance/mappings/implementation-status-map.yaml`. |
| `ControlOrigination` (OSBAPI enum) | `#/$defs/ControlOrigination` | Single-value origination (OSCAL allows multiple simultaneous flags). 5 values vs. 7. Picks canonical value by precedence from `control-origination-map.yaml`. |
| `ImplementationModel` (OSBAPI enum) | `#/$defs/ImplementationModel` | Service model (iaas/paas/saas/on-premises/customer). Not part of the base OSCAL SSP model — comes from the OSBAPI CRM profile. |
| `OscalCrmSummary` | `#/$defs/OscalCrmSummary` | Per-service-offering/per-plan CRM projection. Combines a `profile` URI, `service_offering_id`, `plan_id`, and a list of `ControlSummary` records. This is the `GET /crm/{service_offering_id}/{plan_id}` response shape. |
| `FullCrmDocument` | `#/$defs/FullCrmDocument` | Full CRM document with per-component `by_components` projections. The `GET /crm/{service_instance_id}` response shape. |
| `OscalImplementedRequirementProjection` | `#/$defs/OscalImplementedRequirementProjection` | Per-control + per-component implementation detail with `oscal_*` raw-value passthrough fields. |
| `BrokerProvisionResponseExtension` | `#/$defs/BrokerProvisionResponseExtension` | OSBAPI broker provision response carrying an inline CRM summary. |
| `BackstageComplianceAnnotations` | `#/$defs/BackstageComplianceAnnotations` | `oscal.io/*` / `backstage.io/*` annotation namespace for Backstage catalog entities. |
| `BackstageEntityComplianceEnvelope` | `#/$defs/BackstageEntityComplianceEnvelope` | Full Backstage catalog entity (Component/Resource/System/API) with compliance annotations embedded. |
| `ComplianceAdapterEntityResponse` | `#/$defs/ComplianceAdapterEntityResponse` | Adapter response shape for `GET /entities/{entity_ref}`. Combines broker metadata + OSCAL refs + CRM summary + implemented requirements. |
| `CanonicalControl` | `#/$defs/CanonicalControl` | Internal pipeline intermediate model. Bridges raw parser output to all OSBAPI projections. |
| `FederationDocument` / `FederationSystem` | `#/$defs/FederationDocument` | Multi-SSP inheritance chain manifest. Not part of OSCAL. |
| **Vault frontmatter schemas** (7 types) | `Vault*Frontmatter` defs | Obsidian note YAML frontmatter. No OSCAL equivalent — entirely a presentation/interop layer. |
| **Vocabulary mapping tables** | `ImplementationStatusMappingTable`, `ControlOriginationMappingTable` | Human-editable YAML tables mapping OSCAL → OSBAPI. Data, not code. |
| `OscalSspInputDocument` | `#/$defs/OscalSspInputDocument` | Formalised shape of the fismatic OSCAL SSP output (a subset of the full NIST SSP model). |

---

## 4. Namespace mapping summary

| Layer | Namespace | Schema location |
|---|---|---|
| **Raw OSCAL SSP** | `system-security-plan.*` (NIST OSCAL 1.0) | `content/oscal_ssp_schema.json` (playground), `#/$defs/OscalSspInputDocument` (automation) |
| **Internal canonical** | `CanonicalControl` | `compliance/lib/canonical_model.py`, `#/$defs/CanonicalControl` |
| **Vocabulary mapping** | OSCAL raw → OSBAPI enum | `compliance/mappings/*.yaml`, `#/$defs/*MappingTable` |
| **OSBAPI CRM** | `profile` + `controls[]` + `implemented_requirements[]` | `#/$defs/OscalCrmSummary`, `#/$defs/FullCrmDocument` |
| **OSBAPI broker** | `operation` + `metadata.attributes.oscal_crm` | `#/$defs/BrokerProvisionResponseExtension` |
| **Backstage catalog** | `oscal.io/*` annotations + `backstage.io/*` entity envelope | `#/$defs/BackstageComplianceAnnotations`, `#/$defs/BackstageEntityComplianceEnvelope` |
| **Compliance adapter** | `GET /entities/{entity_ref}` response | `#/$defs/ComplianceAdapterEntityResponse` |
| **Obsidian vault** | YAML frontmatter `note_type: *` + wikilinks + Dataview | `docs/OBSIDIAN_VAULT_SCHEMA.md`, `#/$defs/Vault*Frontmatter` |
| **Federation** | `systems[]` + `inherits_from[]` | `#/$defs/FederationDocument` |

---

## 5. Coverage gap analysis — concepts in the playground missing from automation

### High-priority gaps (affect pipeline fidelity)

| Gap | Impact | Mitigation |
|---|---|---|
| **`system-implementation.users`** | No user/persona data flows into projections. Audit/ATO reviewers can't see which roles have access. | Parse from docx cover sheet. Populate vault `Organizations/` with proper role→person mappings. |
| **`leveraged-authorizations`** | No inheritance chain from parent CSP authorization packages. `controls` labeled "inherited" have no `inherited-from` reference. | `FederationDocument` partial: defines the relationship shape but no generator populates it yet. |
| **`system-implementation.components`** (real) | Vault emits only a synthetic component. No per-component control status breakdown. Real CSP SSPs have multiple components with different origination per component. | fismatic `SSPConverter` only emits a single undifferentiated control list. Multi-component extraction would require docx table heuristics beyond current scope. |
| **`implemented-requirements[].by-components`** | `FullCrmDocument` uses a single synthetic UUID. Cannot express "AC-2 is inherited from the platform provider but implemented by Example Tenant." | Same as above. |
| **`implemented-requirements[].props`** | No namespaced key/value metadata on controls. FedRAMP-specific properties (FedRAMP parameter values, authorization package IDs) are lost. | fismatic doesn't extract properties from docx headers/footers. |

### Medium-priority gaps (affect completeness but not core pipeline)

| Gap | Impact | Mitigation |
|---|---|---|
| **`metadata.revisions`** (revision history) | No SSP version diff capability. `docs/SUGGESTIONS_AND_IMPROVEMENTS.md` item #3 proposes an SSP diff tool but it has no data to diff against. | Store revision history when parsing. `compliance/lib/ssp_diff.py` could compare two SSP dicts. |
| **`back-matter.resources`** (attachments, citations) | Evidence/artifact references are not carried through. Audit trail incomplete. | Vault `Evidence/` note type exists but no extraction from SSP into it. |
| **`implemented-requirements[].links`** | Explicit related-control or evidence links in the SSP are not projected. The vault generator heuristically spots `AC-2` references in statement description text as a fallback. | Parse `links` blocks when present. |
| **`system-characteristics.system-information.information-types`** | NIST 800-60 information-type categorization (C/I/A impact per info type) is not extracted. This is core to FIPS 199 scoping. | Parse from docx cover sheet tables. |
| **`metadata.responsible-parties`** | Role-to-party mapping at the document level. Individual control `responsible-roles` carry role-ids but no party resolution. | Resolve role-ids against `metadata.parties`. |

### Low-priority gaps (cosmetic or out of scope)

| Gap | Impact |
|---|---|
| **`metadata.locations`** (physical addresses) | Physical data center location is irrelevant to most cloud SSPs. |
| **`system-implementation.data-flow` / `network-architecture`** | Diagrams. Not machine-processable from docx. |
| **`metadata.props`** (document-level properties) | Key/value metadata. Nice-to-have but not critical. |
| **Assessment Plan / Assessment Results / POA&M models** | Separate OSCAL models unrelated to the SSP→CRM pipeline. Assessment data would be the next major integration point (see Phase 14 in the now-deleted `archive/plans/NEXT_STEPS_PLAN.md`). |
| **Catalog model** | Base control definitions. The pipeline parses control *implementations* (filled-in SSPs), not control *definitions* (catalog). FedRAMP baselines are the catalog layer and exist as imported profiles. |
| **Profile model** | Baseline profile definitions. Referenced by URI only. |

---

## 6. Recommendation: next integration steps

1. **Parse `leveraged-authorizations`** ✅ *Partial* — `FederationDocument` schema shape exists;
   `oscal.x.inheritance` notes are auto-generated for inheritance-labeled controls.
   Full resolution requires detecting parent SSP references from the docx cover sheet.

2. **Resolve `responsible-roles` → party UUIDs → wikilinks** ✅ *Partial* — Multi-party
   destinations (`oscal_party_ao`, `oscal_party_security`, `oscal_party_preparer`,
   `oscal_party_contact`) now populated from datacontract-driven extraction.
   Full wikilink resolution from role-id → party UUID → vault note is the next step.

3. **Parse `implemented-requirements[].links`** for evidence references — Populate vault
   `evidence:` frontmatter field with real links from the SSP, replacing the current empty
   state. Blocked on R11 unified converter providing access to `links` blocks.

4. **Extract `system-information.information-types`** from the docx cover sheet — Feed into
   vault `Systems/<slug>.md` body and the federation model. The `oscal_x_decision` destination
   in the datacontract covers the information nature section but not structured NIST 800-60
   categorization.

5. **Add revision-history tracking** — Store `metadata.revisions` so `ssp_diff.py`
   (per `docs/SUGGESTIONS_AND_IMPROVEMENTS.md` item #3) has data to compare across
   SSP versions.

6. **Complete R11 unified converter** — Consolidate `fismatic/SSPConverter` and
   `heading_parser.py` into a single contract-driven converter that handles all sections
   including Chapter 13 control tables. This unblocks items 3 and 4.

---

## 7. Extension Type (`oscal.x.*`) Coverage Matrix

All `oscal.x.*` types are defined in the project ontology. Coverage below reflects
current extraction and vault population status.

| Type | `extension_model` | Vault Folder | Template | Dashboard Query | Auto-Extracted? |
|------|-------------------|-------------|----------|-----------------|-----------------|
| `oscal.x.diagram` | `diagram` | `Diagrams/` | ✅ | ✅ | ✅ (via `data_flow_images` destination) |
| `oscal.x.decision` | `decision` | `Decisions/` | ✅ | ✅ | ✅ (IPv6 transition, GSA showstoppers) |
| `oscal.x.evidence` | `evidence` | `Evidence/` | ✅ | ✅ | ❌ (template only; no extractor yet) |
| `oscal.x.finding` | `finding` | `Findings/` | ✅ | ✅ | ❌ (template only; no extractor yet) |
| `oscal.x.inheritance` | `inheritance` | `Inheritance/` | ✅ | ✅ | ✅ (controls with inherited origination) |
| `oscal.x.boundary` | `boundary` | `Boundaries/` | ✅ | ✅ | ✅ (authorization boundary section) |
| `oscal.x.runbook` | `runbook` | `Runbooks/` | ⚠️ Needs creation | ⚠️ Needs query | ✅ (DevSecOps, container, AWS management) |
| `oscal.x.relationship` | `relationship` | `Relationships/` | ✅ | ✅ | ✅ (interconnections, ports/protocols) |
| `oscal.x.parameter` | `parameter` | `Parameters/` | ⚠️ Needs creation | ⚠️ Needs query | ✅ (control parameters via control_table) |
| `oscal.x.control_part` | `control_part` | `Controls/parts/` | ⚠️ Needs creation | ⚠️ Needs query | ✅ (statement parts via control_table) |

### Outstanding template gaps (⚠️ above)

Three extension types need templates and dashboard queries created in R9/R11:

1. **`oscal.x.runbook`** — Template file: `_meta/templates/oscal.x.runbook-template.md`
   - Fields: `type`, `title`, `status`, `owner`, `extension_model: runbook`,
     `runbook_kind`, `cadence`, `runbook_id`, `trigger_event`, `expected_output`,
     `automation_hooks`
2. **`oscal.x.parameter`** — Template file: `_meta/templates/oscal.x.parameter-template.md`
   - Fields: `type`, `title`, `status`, `owner`, `extension_model: parameter`,
     `parameter_kind`, `parameter_id`, `value`, `source_profile`, `control_id`
3. **`oscal.x.control_part`** — Template file: `_meta/templates/oscal.x.control_part-template.md`
   - Fields: `type`, `title`, `status`, `owner`, `extension_model: control_part`,
     `inheritance_mode`, `part_id`, `control_id`, `implemented_by`, `inherited_from`,
     `evidence_refs`
