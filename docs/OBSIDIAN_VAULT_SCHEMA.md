# Obsidian Vault Schema for FedRAMP SSP Compliance Content

## Purpose

This document defines a YAML-frontmatter + markdown schema that maps the
OSCAL/OSBAPI/FedRAMP compliance data model onto an Obsidian vault. The goal
is a vault that is:

- **Machine-readable**: YAML frontmatter on every note carries structured
  metadata that maps directly to/from the OSCAL SSP JSON and OSBAPI
  extension schemas this pipeline already produces.
- **Human-readable**: Markdown body carries narrative content (control
  implementation descriptions, assessment notes, evidence references) that
  is directly useful to compliance staff, auditors, and engineers.
- **Linkable**: Obsidian wikilinks (`[[...]]`) replace OSCAL's UUID-based
  foreign keys, making the graph of control ↔ component ↔ system ↔
  organization ↔ evidence navigable and visualizable in Obsidian's graph
  view.
- **Queryable**: Obsidian Bases (native) and Dataview (backward-compatible)
  can aggregate frontmatter fields across the vault to produce dynamic
  dashboards (e.g. "all controls with status != operational in System X").

## Type Taxonomy

Types use a dotted namespace convention (see `archive/plans/PROJECT_REFACTORING_PLAN.md`):
- **`oscal.*`** — canonical NIST OSCAL model concepts (`oscall_model` field)
- **`oscal.x.*`** — project extensions beyond NIST OSCAL (`extension_model` field)
- **`okf.*`** — vault governance infrastructure types

### Canonical types

| `type` | `oscal_model` | Folder |
|--------|---------------|--------|
| `oscal.control` | `control` | `Controls/` |
| `oscal.component_definition` | `component-definition` | `Components/` |
| `oscal.ssp` | `system-security-plan` | `Systems/`, `Plans/` |
| `oscal.party` | `party` | `Organizations/` |

### Extension types

| `type` | `extension_model` | Folder |
|--------|-------------------|--------|
| `oscal.x.diagram` | `diagram` | `diagrams/` |
| `oscal.x.evidence` | `evidence` | `evidence/` |
| `oscal.x.finding` | `finding` | `findings/` |
| `oscal.x.inheritance` | `inheritance` | `inheritance/` |
| `oscal.x.boundary` | `boundary` | `boundaries/` |
| `oscal.x.decision` | `decision` | `decisions/` |
| `oscal.x.relationship` | `relationship` | `relationships/` |

### OKF infrastructure types

| `type` | Folder |
|--------|--------|
| `okf.schema` | `governance/` |
| `okf.index` | (root `Index.md`) |
| `okf.template` | `_meta/templates/` |
| `okf.validation_rule` | `governance/validation-rules/` |
| `okf.mapping` | `Mappings/` |

All domain notes carry `kind: canonical` or `kind: extension`, plus
backward-compatible `note_type` (flat string) for one release cycle.

---

## Vault layout

```
vault/
├── _meta/                          # Vault configuration
│   ├── templates/                  # Note templates (oscal.*, oscal.x.*)
│   │   ├── Control.md              # Template: oscal.control
│   │   ├── System.md               # Template: oscal.ssp
│   │   ├── Diagram.md              # Template: oscal.x.diagram
│   │   ├── Evidence.md             # Template: oscal.x.evidence
│   │   ├── Finding.md              # Template: oscal.x.finding
│   │   ├── Inheritance.md          # Template: oscal.x.inheritance
│   │   ├── Boundary.md             # Template: oscal.x.boundary
│   │   ├── Decision.md             # Template: oscal.x.decision
│   │   └── Relationship.md         # Template: oscal.x.relationship
│   └── tags.md                     # Tag index and taxonomy
│
├── Controls/                       # One note per NIST 800-53 control (oscal.control)
│   ├── AC-1.md
│   ├── AC-2.md
│   └── ...
│
├── Systems/                        # Information systems (oscal.ssp)
│   ├── example-system.md
│   └── ...
│
├── Components/                     # Component definitions (oscal.component_definition)
│   ├── example-platform-component.md
│   └── ...
│
├── Plans/                          # SSP documents (oscal.ssp)
│   ├── <system>.md
│   └── ...
│
├── Organizations/                  # Parties (oscal.party)
│   ├── example-agency-security-office.md
│   └── ...
│
├── governance/                     # OKF infrastructure
│   ├── schema.md                   # okf.schema: full type registry
│   └── validation-rules/           # okf.validation_rule: automated health rules
│       ├── implemented-controls-have-evidence.md
│       └── diagrams-have-review-dates.md
│
├── Mappings/                       # Vocabulary crosswalks (okf.mapping)
│   ├── control-origination.md
│   ├── implementation-status.md
│   └── interop-profile.md
│
├── diagrams/                       # Architecture and data-flow (oscal.x.diagram)
├── evidence/                       # Evidence artifacts (oscal.x.evidence)
├── findings/                       # Assessment findings (oscal.x.finding)
├── inheritance/                    # Inheritance declarations (oscal.x.inheritance)
├── boundaries/                     # Authorization boundaries (oscal.x.boundary)
├── decisions/                      # ADRs (oscal.x.decision)
├── relationships/                  # Typed relationships (oscal.x.relationship)
│
├── Index.md                        # okf.index: Dataview dashboards
└── _meta/views/controls.base        # Native Obsidian Bases controls view
```

---

## YAML frontmatter schemas

### 1. Control note (`Controls/AC-1.md`)

```yaml
---
# OSCAL + OKF type taxonomy (R1)
type: oscal.control                  # Required. Dotted namespace type
oscal_model: control                 # Required. NIST OSCAL model name
kind: canonical                      # Required. canonical | extension
note_type: control                   # Backward-compat (to be removed)

# --- Identifiers ---
control_id: AC-1                      # Required. NIST 800-53 identifier
family: AC                            # Required. Two-letter family code
enhancement: null                     # Optional. Enhancement number (e.g. 1, 2)
title: Access Control Policy and Procedures  # Optional. Friendly name

# --- Lifecycle ---
status: active                        # active | draft | archived
owner: "[[Organizations/example-agency-security-office]]"  # Responsible party
reviewed: 2026-07-16                  # Last review date (ISO)

# --- System reference ---
system: "[[Systems/example-system]]"       # Optional. Wikilink to owning system

# --- Implementation status (OSCAL vocabulary) ---
implementation_status: implemented    # Required. One of:
#   implemented, partially-implemented, alternative-implementation, planned,
#   customer-configured, not-applicable, not-implemented

# --- Control origination (OSCAL list) ---
origination:                          # Required. At least one value.
  - inherited                         # One or more of:
#     inherited, hybrid, system-specific, service-provider-corporate,
#     configured-by-customer, provided-by-customer, shared
inherited_from: "[[Components/example-agency-security-office]]"  # Optional. If inherited
hybrid_systems:                       # Optional. If hybrid
  - "[[Components/example-platform-component]]"
  - "[[Components/example-tenant]]"

# --- Downstream projection ---
# Use Mappings/implementation-status.md and Mappings/control-origination.md
# to derive OSBAPI/Backstage vocabulary from the raw values above. Generated
# control notes do not materialize normalized_* duplicates (ADR-026).
exportable: false                     # Optional. Default false.

# --- Responsibility ---
responsible_role:                     # Optional. One or more roles
  - "[[Organizations/example-agency-security-office]]"
  - "[[Organizations/example-platform-team]]"

# --- Statement IDs from OSCAL ---
statement_parts:                      # Optional. Part IDs for Dataview
  - a
  - b

# --- References ---
evidence:                             # Optional. Linked evidence notes
  - "[[Evidence/agency-it-security-policy]]"
  - "[[Evidence/access-control-procedures]]"
satisfied_by:                         # Optional. Controls/notes this satisfies
  - "[[Controls/AC-1]]"

# --- Categorization ---
tags:                                 # Optional. Faceted tags
  - control/family/ac
  - control/status/operational
  - control/baseline/low

# --- Provenance ---
source_ssp: "[[Plans/<system>]]"        # Optional; plan provenance
last_updated: 2024-07-05                          # ISO date
parsed_by: fismatic                                # Parser used

# --- Graph hierarchy (ADR-025) ---
# Base control: Plan parent + enhancement children
up_plan: "[[Plans/<system>]]"
sub_controls:
  - "[[Controls/AC-1(01)]]"
# Enhancement control instead uses `up_control: [[Controls/AC-1]]`.
---
```

#### Markdown body

```markdown
# [[Controls/AC-1|AC-1]] Access Control Policy and Procedures

# Upstream
> Original or summarized upstream control language.

# Parameters
`parameters` frontmatter is the lossless canonical source for organization-defined
parameter identifiers and values. The generated Dataview table renders that source
without duplicating literal parameter values in the note body.

```dataview
TABLE parameter.param_id AS "Parameter", parameter.value AS "Value"
FROM [[]]
FLATTEN parameters AS parameter
```

# Statement Parts

## Part a
statement_part:: a
inheritance_mode:: local
implementation_status:: x

Implementation narrative here.

## Part b
statement_part:: b
inheritance_mode:: inherited
inherited_from:: [[inheritance/aws-identity-shared-services]]
implementation_status:: >

Implementation narrative here.

# CRM Guidance
crm_guidance_level:: standard

- Engineering interpretation
- Required evidence
- Review cadence

# Evidence
- [[evidence/ac2-quarterly-export]]

## Related Controls
- [[Controls/AC-2|AC-2]]
```

```markdown
# [[Controls/AC-1|AC-1]] Access Control Policy and Procedures

## Description

The organization develops, documents, and disseminates an access control
policy and procedures for the system.

## Implementation

### Part a
Narrative description of how Part a is implemented...

[[Organizations/example-agency-security-office]] is responsible for developing and maintaining
the access control policy.

### Part b
Narrative for Part b...

## Evidence

- [[Evidence/agency-it-security-policy|Agency IT Security Policy]]
- [[Evidence/access-control-procedures|Access Control Procedures CIO-IT Security-01-07]]

## Related Controls

- [[Controls/AC-2|AC-2 Account Management]]
- [[Controls/AC-3|AC-3 Access Enforcement]]
```

### 2. System note (`Systems/example-system.md`)

```yaml
---
note_type: system

# --- Identifiers ---
name: Example System                           # Required
acronym: SYSTEM-ACRONYM                         # Optional
system_id: "urn:uuid:..."                 # Optional. OSCAL UUID

# --- Classification ---
fips_199: low                             # Required. low | moderate | high
security_objective_confidentiality: low    # Required
security_objective_integrity: low          # Required
security_objective_availability: low       # Required
impact_level: low                          # Required. Aggregate

# --- Status ---
status: operational                       # Required. operational | under-development | other
authorization_boundary: |                 # Optional
  The authorization boundary encompasses all components within
  the Example Platform...

# --- Ownership and contacts ---
organization: "[[Organizations/gsa]]"       # Required
system_owner: "[[Organizations/example-org]]"  # Optional
isso: "[[Organizations/example-agency-security-office]]"        # Optional
authorizing_official: "[[Organizations/gsa]]"  # Optional

# --- Components ---
components:                               # Required. Linked component notes
  - "[[Components/example-platform-component]]"
  - "[[Components/example-tenant]]"
  - "[[Components/github-actions]]"

# --- Plans ---
plans:                                    # Optional. Linked SSP/baseline notes
  - "[[Plans/<system>]]"
  - "[[Plans/fedramp-low-baseline]]"

# --- Controls (aggregated from Components) ---
controls:                                 # Optional. Linked control notes
  - "[[Controls/AC-1]]"
  - "[[Controls/AC-2]]"

# --- Provenance ---
last_updated: 2024-07-05
tags:
  - system/fips/low
  - system/status/operational
---
```

### 3. Component note (`Components/example-platform-component.md`)

```yaml
---
note_type: component

# --- Identifiers ---
name: Example Platform                               # Required
component_type: platform                            # Required. platform | service | infrastructure | tool | process
status: operational                                 # Required
version: "1.0"                                      # Optional

# --- System ---
belongs_to: "[[Systems/example-system]]"                  # Required

# --- Implementation model ---
implementation_model: paas                          # Required. iaas | paas | saas | on-premises | customer | hybrid

# --- Responsibility ---
responsible_party: "[[Organizations/example-platform]]"    # Required
# Deployment model
cloud_deployment_model: public                      # public | private | community | hybrid

# --- Classification ---
fips_199: low                                       # Optional
fedramp_package: fedramp-low                        # Optional

# --- References ---
documentation: "[[Evidence/example-platform-ssp]]"         # Optional
fedramp_authorization: "[[Evidence/example-platform-fedramp-auth]]"  # Optional.

# Controls implemented by this component are represented by each control's
# `component` link. Obsidian backlinks provide the reverse view; do not emit an
# `implements_controls` mirror list (ADR-025).

# --- Leveraged authorizations ---
leveraged_from:                                     # Optional
  - component: "[[Components/example-platform-cf]]"
    authorization: "[[Evidence/example-platform-fedramp-auth]]"

tags:
  - component/type/platform
  - component/paas
  - component/fedramp/low
---
```

### 4. Plan/SSP note (`Plans/<system>.md`)

```yaml
---
note_type: plan

# --- Identifiers ---
plan_id: "<system>"                       # Required
title: Example System System Security Plan                # Required
version: "1.0"                                       # Required
date: 2024-03-14                                     # Required
plan_type: ssp                                       # Required. ssp | profile | baseline

# --- Profile ---
profile_ref: "https://oscal-project.github.io/osbapi-crm-profile/v1"  # Optional
import_profile: "[[Plans/fedramp-low-baseline]]"                       # Optional

# --- System ---
system: "[[Systems/example-system]]"                     # Required

# --- Status ---
status: active                                      # Required. active | superseded | draft

# --- Technical metadata ---
template_version: 08282018                          # FedRAMP template version
parser: fismatic                                    # Parser used
controls_parsed: 159                                # Count
oscasl_version: "1.0.0"                             # OSCAL schema version

# --- References ---
source_docx: "<ssp>.docx"  # Original file (relative path only, never committed)

tags:
  - plan/ssp
  - plan/fedramp/low
  - plan/status/active
---
```

### 5. Organization note (`Organizations/gsa.md`)

```yaml
---
note_type: organization

# --- Identifiers ---
name: General Services Administration                # Required
acronym: GSA                                          # Required
org_type: agency                                     # Required. agency | team | company

# --- Contact ---
role: cloud-service-provider                         # Required. CSP role

# --- Relationships ---
parent: null                                         # Optional. Parent org wikilink
sub_orgs:                                            # Optional
  - "[[Organizations/example-org]]"
  - "[[Organizations/example-agency-security-office]]"

# --- Systems owned ---
systems:                                             # Optional
  - "[[Systems/example-system]]"
  - "[[Systems/site-scanner]]"

# --- Responsibilities ---
responsible_for:                                     # Optional
  - control: "[[Controls/AC-1]]"
    role: policy-authority
  - control: "[[Controls/IR-2]]"
    role: training-authority

tags:
  - org/agency
  - org/federal
---
```

### 6. Evidence note (`Evidence/agency-it-security-policy.md`)

```yaml
---
note_type: evidence

# --- Identifiers ---
title: Agency IT Security Policy              # Required
evidence_type: policy                                # Required. policy | procedure | scan | report | screenshot | assessment | training
status: current                                      # Required. current | superseded | expired
date: 2024-01-15                                     # Required
version: "2100.1"                                    # Optional

# --- References ---
uri: "https://example.invalid/policy"      # Optional
source_organization: "[[Organizations/example-agency-security-office]]"    # Required

# --- Controls supported ---
supports_controls:                                   # Required. Controls this evidence satisfies
  - control: "[[Controls/AC-1]]"
    statement: stmt.a
  - control: "[[Controls/AC-2]]"
    statement: stmt.a

tags:
  - evidence/policy
  - evidence/access-control
---
```

### 7. Assessment note (`Assessments/readiness-review-q1-2024.md`)

```yaml
---
note_type: assessment

# --- Identifiers ---
assessment_id: "FR-RR-2024-Q1"                      # Required
title: Readiness Review Q1 2024                     # Required
date: 2024-03-31                                     # Required
assessment_type: readiness-review                    # Required. readiness-review | annual | continuous-monitoring | penetration-test

# --- System ---
system: "[[Systems/example-system]]"                      # Required

# --- Status ---
overall_status: satisfactory                         # Required. satisfactory | at-risk | non-compliant
finding_count: 3                                     # Optional
risk_level: moderate                                 # Optional. low | moderate | high | critical

# --- Findings ---
findings:                                            # Optional
  - id: F-001
    control: "[[Controls/AC-2]]"
    severity: moderate
    status: open
    description: "Account review procedures not fully documented"
  - id: F-002
    control: "[[Controls/CM-6]]"
    severity: low
    status: closed
    description: "Configuration baseline updated per remediation"

# --- Next assessment ---
next_assessment: 2024-06-30                          # Optional

tags:
  - assessment/readiness-review
  - assessment/status/satisfactory
---
```

---

## Controlled vocabularies

### Implementation status (OSCAL → OSBAPI crosswalk)

| OSCAL raw value | OSBAPI enum | Remark |
|---|---|---|
| `implemented` | `operational` | Standard in-place |
| `partially-implemented` | `under-development` | Closest analog; verify |
| `alternative-implementation` | `operational` | Objective met via alt path |
| `planned` | `planned` | — |
| `customer-configured` | `not-applicable` | CSP perspective |
| `not-applicable` | `not-applicable` | — |
| `not-implemented` | `not-implemented` | Gap |

Full mapping table: `Mappings/implementation-status.md`

### Control origination (OSCAL → OSBAPI crosswalk)

| OSCAL raw value | OSBAPI enum | Remark |
|---|---|---|
| `inherited` | `inherited` | — |
| `hybrid` | `shared` | Shared between CSP and others |
| `system-specific` | `system-specific` | — |
| `service-provider-corporate` | `system-specific` | Closest analog |
| `configured-by-customer` | `customer-configured` | — |
| `provided-by-customer` | `customer-provided` | — |
| `shared` | `shared` | — |

Full mapping table: `Mappings/control-origination.md`

### Control families

```
AC  Access Control
AT  Awareness and Training
AU  Audit and Accountability
CA  Security Assessment and Authorization
CM  Configuration Management
CP  Contingency Planning
IA  Identification and Authentication
IR  Incident Response
MA  Maintenance
MP  Media Protection
PE  Physical and Environmental Protection
PL  Planning
PM  Program Management
PS  Personnel Security
PT  PII Processing and Transparency
RA  Risk Assessment
SA  System and Services Acquisition
SC  System and Communications Protection
SI  System and Information Integrity
SR  Supply Chain Risk Management
```

---

## Obsidian Bases and Dataview queries

`_meta/views/controls.base` is the committed native Obsidian Bases view for
all controls, controls requiring attention, and inherited/hybrid controls.
Open it directly in Obsidian for a sortable/filterable view without a
community plugin. The Dataview blocks below remain for backward compatibility.

### Typed table records

Canonical sidecars project every Markdown table into `table_records` schema
version 1 while retaining the note's `raw_body` and rendered Markdown table as
the source of truth. Each record preserves `source_heading`, `headers`, `rows`,
and `raw_markdown`. Initial mapped kinds are `external_service` (the two-column
`Service Element` / `Response` fact sheet), `protocol` (`Ports` / `Protocols` /
`Services`), and `inheritance_provider` (`Provided By`). All other or malformed
tables are retained as `kind: opaque` with `disposition: unmapped`.

Narrative notes expose only flat summary fields (`table_record_count`,
`table_kinds`, `table_classifications`, `table_structured_count`,
`table_opaque_count`, and `table_unmapped_count`) in frontmatter. The generated
`_meta/views/components.base` includes a **Typed table coverage** view over
these fields, with corresponding typed-table views in
`_meta/views/relationships.base` and `_meta/views/inheritance.base`. Table rows
remain in canonical sidecars and rendered Markdown; they are never embedded as
unsupported nested frontmatter structures.

### Dashboard: controls by status for a system

```dataview
TABLE implementation_status AS Status, origination AS Origination
FROM "Controls"
WHERE system = [[Systems/example-system]]
SORT control_id ASC
```

### Dashboard: controls requiring attention (not operational)

```dataview
TABLE control_id AS ID, implementation_status AS Status, system AS System
FROM "Controls"
WHERE implementation_status != "implemented"
SORT implementation_status ASC, control_id ASC
```

### Dashboard: inherited controls with source

```dataview
TABLE control_id AS ID, inherited_from AS "Inherited From"
FROM "Controls"
WHERE contains(origination, "inherited")
SORT control_id ASC
```

### Dashboard: system component breakdown

```dataview
TABLE component_type AS Type, implementation_model AS Model, status AS Status
FROM "Components"
WHERE belongs_to = [[Systems/example-system]]
SORT component_type ASC, name ASC
```

### Dashboard: open findings

```dataview
TABLE assessment_id AS Assessment, control AS Control, severity AS Severity
FROM "Assessments"
FLATTEN findings
WHERE findings.status = "open"
SORT findings.severity ASC
```

### Tag aggregation (status distribution)

```dataview
TABLE length(rows) AS Count
FROM "Controls"
FLATTEN tags
WHERE startswith(tags, "control/status/")
GROUP BY tags AS Status
SORT Count DESC
```

---

## Graph view relationships

Obsidian's graph view visualizes the following link types:

| Link type | Example | Visualization meaning |
|---|---|---|
| Control ↔ System | `system: [[Systems/example-system]]` | Which system this control applies to |
| Control ↔ Component | `hybrid_systems: [[Components/example-platform-component]]` | Implementation responsibility split |
| Control ↔ Organization | `responsible_role: [[Organizations/example-agency-security-office]]` | Who is responsible |
| Control ↔ Evidence | `evidence: [[Evidence/agency-it-security-policy]]` | What proves implementation |
| Control ↔ Control | `satisfied_by: [[Controls/AC-1]]` | Related/external controls |
| System ↔ Component | `components: [[Components/example-platform-component]]` | System composition |
| System ↔ Plan | `plans: [[Plans/<system>]]` | SSPs and baselines |
| System ↔ Assessment | findings.control → `[[Controls/AC-2]]` | Assessment findings |
| Organization ↔ Org | `sub_orgs: [[Organizations/example-org]]` | Org hierarchy |

---

## Template examples (Templater plugin)

### `_meta/templates/Control.md`

```yaml
---
note_type: control
control_id: "{{title}}"
family: "{{title|slice:0:2|upper}}"
enhancement: null
system: ""
implementation_status: not-implemented
origination:
  - system-specific
responsible_role: []
statement_parts: []
evidence: []
tags:
  - control/family/{{title|slice:0:2|lower}}
  - control/status/not-implemented
---
```

### `_meta/templates/System.md`

```yaml
---
note_type: system
name: "{{title}}"
acronym: ""
status: operational
organization: ""
components: []
tags:
  - system/status/operational
---
```

---

## Pipeline → Vault generation

The existing `archive/osbapi-projection/build_crm_projection.py (archived)` pipeline already
produces OSCAL SSP JSON from which all vault notes can be derived. A future
generator (`compliance/scripts/export_obsidian_vault.py`) would:

1. Read the OSCAL SSP JSON (same source as the CRM projection pipeline).
2. For each `implemented-requirement`, emit `Controls/<control_id>.md` with
   frontmatter and part narrative from statements.
3. For each `responsible-role` / `party`, emit `Organizations/<name>.md`.
4. For each `system-characteristics`, emit `Systems/<name>.md`.
5. For each OSCAL component (from a separate component-definition or
   extracted from the SSP), emit `Components/<name>.md`.
6. Resolve foreign keys (UUIDs) to Obsidian wikilinks using a name/alias
   index built from steps 2-5.
7. Copy templates from `_meta/templates/`.

The schema above is the target; the generator is the next implementation
step beyond this design document.

---

## ExcaliBrain Graph Integration

[ExcaliBrain](https://github.com/zsviczian/excalibrain) transforms this vault
into an interactive, spatially-organized mind-map. It reads Dataview fields
from each note's YAML frontmatter to build explicit (DEFINED) relationships
that overlay the implicit wikilink graph.

### Why ExcaliBrain over the built-in Obsidian graph view

The built-in graph treats every `[[wikilink]]` equally. ExcaliBrain assigns
spatial roles — **Parent (North)**, **Children (South)**, **Left Friends
(West)**, **Right Friends (East)** — so compliance staff can navigate the
vault in a semantically-meaningful way: starting from a Plan, drilling into
Systems, then Components, then Controls; or lateraling from a control to
its related siblings.

### Vault graph topology

```
Organization  ──(child_systems)──▶  System  ──(child_components)──▶  Component
     ▲                                 ▲
     │                                 │  (up_system)
     │  (up_plan)                      │
   Plan  ──(child_systems)──────▶  System
     │
     └──(up_plan)────▶ Base control ──(sub_controls)──▶ Enhancement control
                              │                 ▲
                              └── related ──────┘ (up_control)
```

The relationship fields emitted by `compliance/lib/vault.py` are:

| Field | Note type | Relationship role | Notes |
|---|---|---|---|
| `up_control` | `oscal.control` enhancement | **Parent** | Points only to its base control |
| `up_plan` | `oscal.control` base | **Parent** | Points to the Plan note |
| `sub_controls` | `oscal.control` base | **Children** | Actual enhancement controls, emitted by the generator |
| `related_controls` | `oscal.control` | **Left Friends** | Heuristically extracted from statement narratives |
| `up_plan` | `oscal.ssp` (system) | **Parent** | Points to the Plan note |
| `child_components` | `oscal.ssp` (system) | **Children** | All Components belonging to this System |
| `child_systems` | `oscal.ssp` (plan) | **Children** | All Systems described by this Plan |
| `up_system` | `oscal.component_definition` | **Parent** | Points to the owning System note |
| `child_systems` | `oscal.party` (org) | **Children** | All Systems the Organization is responsible for |

### ExcaliBrain settings (Hierarchy → Ontology)

Open **ExcaliBrain Settings → Hierarchy** and register the following field
names in the appropriate slots. Field names are case-insensitive and spaces
are interchangeable with hyphens, so `up_control` and `Up-Control` are
equivalent.

| Hierarchy slot | Add these field names |
|---|---|
| **Parents** | `up_control`, `up_plan`, `up_system` |
| **Children** | `sub_controls`, `child_systems`, `child_components` |
| **Left Friends** | `related_controls`, `responsible_role`, `responsible_party`, `organization` |

> **Tip:** The defaults already include `Parent`, `Children`, `Friends` etc.
> You only need to *add* the project-specific names above — do not remove the
> defaults unless you want to disable inferred-link navigation.

### Quick-start

1. Install **Dataview**, **Excalidraw**, and **ExcaliBrain** from the
   Obsidian Community Plugins panel.
2. Open ExcaliBrain Settings → Hierarchy and add the field names from the
   table above.
3. Open any vault note (e.g. a Plan note such as `Plans/example-system-ssp.md`).
4. Run the command **ExcaliBrain: Open ExcaliBrain** (Command Palette `⌘/Ctrl+P`).
5. Click any neighbour node to pivot the graph to that note.

### Styling by note type

To make different OSCAL types visually distinct, configure ExcaliBrain's
tag-based node styles:

1. Add `Note type: "#oscal/control"` (or whichever tag) to the notes you
   want to style, **or** rely on the Obsidian tags already present (e.g.
   `tags: - control/family/ac`). ExcaliBrain reads `file.etags`.
2. In ExcaliBrain Settings → Node Styles → Tag-Based Styles, add entries for:
   - `#control/family/ac` → blue border, 🔐 prefix
   - `#system/fips/low` → green border
   - `#system/fips/moderate` → orange border
   - `#system/fips/high` → red border
   - `#plan/ssp` → gold border, 📋 prefix
   - `#org/federal` → purple border, 🏛 prefix

### Relationship inference (background)

ExcaliBrain also infers relationships from plain Obsidian wikilinks when no
explicit Dataview field covers the same target:

- A `[[wikilink]]` is inferred as a **child** by default.
- Mutual links (`A → B` and `B → A`) are inferred as **friends**.

The project's explicit fields (`up_control`, `child_systems`, etc.) take
full precedence (DEFINED > INFERRED), so the inferred graph is a safety net
rather than the primary navigation mechanism.
