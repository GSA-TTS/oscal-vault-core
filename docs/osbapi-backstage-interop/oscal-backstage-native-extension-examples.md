# OSCAL-in-Backstage Native Extension Examples

These examples show how to extend native Backstage YAML patterns with OSCAL-oriented annotations, standard relations, TechDocs references, and a small number of optional custom relations. The goal is to keep everything inside familiar Backstage entity shapes rather than creating a separate standalone compliance model [web:151][web:68][web:67][web:136].

## Design approach

Use existing Backstage kinds and fields first, then add OSCAL references as annotations and only add custom relation names where standard relations are not expressive enough [web:151][web:68].

Recommended mapping:
- `System` = authorization boundary or platform system.
- `Component` = broker, app, adapter, control-enforcing service.
- `Resource` = brokered service offering, service instance, data store, queue.
- `API` = OSB API, compliance adapter API, evidence API.
- `metadata.annotations.oscal.io/*` = OSCAL refs and projected state.
- `backstage.io/techdocs-ref` or `backstage.io/techdocs-entity` = detailed procedures and inheritance docs [web:136][web:162].

## Proposed annotation namespace

Backstage recommends namespaced annotations and allows attaching annotations to catalog entities as extension metadata [web:151][web:67].

Suggested OSCAL-related annotations:

```yaml
metadata:
  annotations:
    oscal.io/profile-ref: https://oscal-project.github.io/osbapi-crm-profile/v1
    oscal.io/component-ref: urn:uuid:49f2f2aa-rds-component
    oscal.io/component-definition-ref: https://compliance.example.gov/oscal/components/aws-rds-postgres-component.json
    oscal.io/ssp-ref: https://compliance.example.gov/oscal/ssp/cloud-platform.json
    oscal.io/crm-endpoint: https://compliance.example.gov/api/compliance/entities/resource:default/aws-rds-postgres-offering
    oscal.io/evidence-endpoint: https://compliance.example.gov/api/evidence/resource:default/aws-rds-postgres-offering
    oscal.io/implementation-status: operational
    oscal.io/control-set: FedRAMP-High
    oscal.io/inheritance-model: leveraged-authorization
    backstage.io/techdocs-ref: dir:.
```

## Example 1: Platform system

This uses a native `System` entity to represent the Cloud Foundry platform boundary and ties it to shared docs and OSCAL package references [web:68][web:136].

```yaml
apiVersion: backstage.io/v1alpha1
kind: System
metadata:
  name: cloud-foundry-platform
  title: Cloud Foundry Platform
  description: Authorized platform boundary hosting brokered services and application workloads.
  annotations:
    oscal.io/profile-ref: https://oscal-project.github.io/osbapi-crm-profile/v1
    oscal.io/ssp-ref: https://compliance.example.gov/oscal/ssp/cloud-platform.json
    oscal.io/evidence-endpoint: https://compliance.example.gov/api/evidence/system:default/cloud-foundry-platform
    oscal.io/implementation-status: operational
    backstage.io/techdocs-ref: dir:.
  tags:
    - cloud-foundry
    - oscal
    - fedramp
spec:
  owner: group:platform-engineering
  domain: platform
```

### Why this works

- `System` is already the native Backstage concept for grouping components, APIs, and resources into a larger system [web:68].
- The OSCAL references stay in annotations, so the entity remains idiomatic Backstage YAML [web:67][web:151].

## Example 2: Broker component

This uses a native `Component` for the service broker and ties it to the system, APIs, and compliance overlays [web:68].

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: csb-broker
  title: Cloud Service Broker
  description: OSBAPI broker that provisions managed services for Cloud Foundry apps.
  annotations:
    github.com/project-slug: example-org/cloud-service-broker
    oscal.io/component-ref: urn:uuid:7d6f1c4b-broker-component
    oscal.io/component-definition-ref: https://compliance.example.gov/oscal/components/broker/csb-broker-component.json
    oscal.io/ssp-ref: https://compliance.example.gov/oscal/ssp/cloud-platform.json
    oscal.io/crm-endpoint: https://compliance.example.gov/api/compliance/entities/component:default/csb-broker
    oscal.io/implementation-status: operational
    backstage.io/techdocs-ref: dir:.
  tags:
    - broker
    - osbapi
spec:
  type: service
  lifecycle: production
  owner: group:platform-engineering
  system: cloud-foundry-platform
  providesApis:
    - osb-broker-api
  dependsOn:
    - component:default/compliance-adapter
    - resource:default/platform-iam
```

### Native concepts used

- `spec.system` generates `partOf` / `hasPart` style system linkage [web:68].
- `spec.providesApis` generates `providesApi` relations [web:68].
- `spec.dependsOn` generates dependency graph edges without inventing new YAML structure [web:68].

## Example 3: Brokered service offering resource

This models a broker offering as a `Resource`, which is a natural fit for a provisionable managed dependency [web:68].

```yaml
apiVersion: backstage.io/v1alpha1
kind: Resource
metadata:
  name: aws-rds-postgres-offering
  title: AWS RDS PostgreSQL Offering
  description: Brokered PostgreSQL offering surfaced through the Cloud Service Broker.
  annotations:
    oscal.io/component-ref: urn:uuid:49f2f2aa-rds-component
    oscal.io/component-definition-ref: https://compliance.example.gov/oscal/components/services/aws-rds-postgres-component.json
    oscal.io/crm-endpoint: https://compliance.example.gov/api/compliance/entities/resource:default/aws-rds-postgres-offering
    oscal.io/evidence-endpoint: https://compliance.example.gov/api/evidence/resource:default/aws-rds-postgres-offering
    oscal.io/implementation-status: operational
    oscal.io/control-set: FedRAMP-High
    backstage.io/techdocs-ref: dir:.
  tags:
    - aws
    - postgres
    - brokered-service
spec:
  type: service-offering
  owner: group:platform-engineering
  system: cloud-foundry-platform
  dependsOn:
    - component:default/csb-broker
    - resource:default/platform-iam
```

### Why `Resource`

Backstage uses `Resource` for infrastructure dependencies and external backing services, which matches a brokered offering better than forcing it into a custom compliance object [web:68].

## Example 4: App component consuming brokered resources

This keeps application YAML standard while adding only a compliance endpoint pointer [web:68][web:67].

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: customer-portal
  title: Customer Portal
  description: Cloud Foundry application consuming brokered PostgreSQL and Redis.
  annotations:
    github.com/project-slug: example/customer-portal
    oscal.io/crm-endpoint: https://compliance.example.gov/api/compliance/entities/component:default/customer-portal
    oscal.io/implementation-status: operational
    backstage.io/techdocs-ref: dir:.
  tags:
    - app
    - cloud-foundry
spec:
  type: service
  lifecycle: production
  owner: group:app-team
  system: cloud-foundry-platform
  dependsOn:
    - resource:default/aws-rds-postgres-offering
    - resource:default/aws-elasticache-redis-offering
  consumesApis:
    - api:default/osb-broker-api
```

### Native benefit

This expresses consumer responsibility through standard `dependsOn` and `consumesApis` relations rather than a custom compliance-only syntax [web:68].

## Example 5: Compliance adapter API

Use a normal `API` entity for the projection layer that joins broker and OSCAL data [web:68].

```yaml
apiVersion: backstage.io/v1alpha1
kind: API
metadata:
  name: compliance-adapter-api
  title: Compliance Adapter API
  description: API returning normalized OSCAL summary and detail for Backstage entities.
  annotations:
    backstage.io/techdocs-ref: dir:.
    oscal.io/profile-ref: https://oscal-project.github.io/osbapi-crm-profile/v1
spec:
  type: openapi
  lifecycle: production
  owner: group:platform-engineering
  system: cloud-foundry-platform
  definition:
    $text: https://compliance.example.gov/openapi.json
```

## Example 6: Shared TechDocs entity

Backstage supports using `backstage.io/techdocs-entity` and `backstage.io/techdocs-entity-path` so multiple entities can point into a shared documentation set instead of duplicating docs [web:162][web:65].

```yaml
apiVersion: backstage.io/v1alpha1
kind: Resource
metadata:
  name: aws-elasticache-redis-offering
  title: AWS ElastiCache Redis Offering
  annotations:
    oscal.io/component-ref: urn:uuid:redis-component-001
    oscal.io/crm-endpoint: https://compliance.example.gov/api/compliance/entities/resource:default/aws-elasticache-redis-offering
    backstage.io/techdocs-entity: component:default/shared-broker-service-docs
    backstage.io/techdocs-entity-path: /redis
spec:
  type: service-offering
  owner: group:platform-engineering
  system: cloud-foundry-platform
```

And the shared docs component:

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: shared-broker-service-docs
  title: Shared Broker Service Docs
  annotations:
    backstage.io/techdocs-ref: dir:.
spec:
  type: documentation
  lifecycle: production
  owner: group:platform-engineering
```

## Example 7: Optional custom relation vocabulary

Backstage already has standard relations, but it also supports extending the model when you need custom semantics [web:151].

Suggested custom relations for OSCAL overlay semantics:
- `implementsControl`
- `controlImplementedBy`
- `inheritsControlFrom`
- `controlInheritedBy`
- `evidenceFor`
- `evidenceProvidedBy`

Conceptual examples:

```text
resource:default/aws-rds-postgres-offering implementsControl control:default/sc-13
component:default/customer-portal inheritsControlFrom system:default/cloud-foundry-platform
resource:default/aws-rds-postgres-offering evidenceFor component:default/csb-broker
```

### Recommendation

Use these as plugin or processor-generated relations, not as hand-maintained duplication when `dependsOn`, `partOf`, and docs links already cover most use cases [web:68][web:151].

## Example 8: Minimal YAML pattern for broad rollout

If you want the simplest pattern that can be added to many existing entities quickly, use this:

```yaml
apiVersion: backstage.io/v1alpha1
kind: Resource
metadata:
  name: aws-rds-postgres-offering
  annotations:
    oscal.io/component-ref: urn:uuid:49f2f2aa-rds-component
    oscal.io/ssp-ref: https://compliance.example.gov/oscal/ssp/cloud-platform.json
    oscal.io/crm-endpoint: https://compliance.example.gov/api/compliance/entities/resource:default/aws-rds-postgres-offering
    oscal.io/implementation-status: operational
    backstage.io/techdocs-ref: dir:.
spec:
  type: service-offering
  owner: group:platform-engineering
  system: cloud-foundry-platform
```

This gives you an OSCAL-aware entity while staying entirely inside standard Backstage entity structure [web:67][web:68][web:136].

## Example 9: Suggested `mkdocs.yml` for compliance-aligned docs

TechDocs uses `mkdocs.yml` and a `docs/` folder as its standard content layout [web:136].

```yaml
site_name: AWS RDS PostgreSQL Offering
site_description: Compliance and operational guidance for the brokered PostgreSQL offering.
plugins:
  - techdocs-core
nav:
  - Overview: index.md
  - Hardening: hardening.md
  - Control Mapping: control-mapping.md
  - Assessment Procedures: assessment-procedures.md
  - Evidence Guide: evidence.md
```

## Example 10: Corresponding docs outline

```text
aws-rds-postgres-offering/
├── catalog-info.yaml
├── mkdocs.yml
└── docs/
    ├── index.md
    ├── hardening.md
    ├── control-mapping.md
    ├── assessment-procedures.md
    └── evidence.md
```

## Pattern summary

The most Backstage-native pattern is:
- put OSCAL pointers in annotations,
- keep system/resource/component/API modeling conventional,
- use standard relations first,
- use shared or local TechDocs for rich narratives,
- and only introduce custom relations or custom kinds after the lightweight annotation pattern proves insufficient [web:151][web:68][web:67][web:136].
