# Repository Folder Structure for OSBAPI–OSCAL Integration

A good structure should keep broker runtime code, OSCAL source-of-truth artifacts, Backstage descriptors, and TechDocs content separate but cross-linked. This matches Backstage’s repo expectations for `catalog-info.yaml`, `mkdocs.yml`, and `docs/`, while leaving room for OSBAPI contract files and compliance artifacts [web:136][web:140][web:96].

## Recommended monorepo

```text
osbapi-oscal-integration/
├── README.md
├── LICENSE
├── .gitignore
├── .editorconfig
├── Makefile
├── package.json                          # optional: repo tooling for docs/plugin work
├── pyproject.toml                        # optional: Python tooling for OSCAL/adapter work
├── .github/
│   └── workflows/
│       ├── validate-schemas.yml
│       ├── validate-oscal.yml
│       ├── build-techdocs.yml
│       ├── test-broker.yml
│       └── deploy.yml
│
├── docs/                                 # repository-level docs
│   ├── index.md
│   ├── architecture.md
│   ├── workflows.md
│   ├── adr/
│   │   ├── 0001-profile-carrier.md
│   │   ├── 0002-summary-vs-fetch.md
│   │   └── 0003-backstage-integration.md
│   └── diagrams/
│       ├── context.mmd
│       ├── runtime-sequence.mmd
│       └── deployment-flow.mmd
├── mkdocs.yml                            # root TechDocs config [web:136]
├── catalog-info.yaml                     # root Backstage entity for the integration project [web:136]
│
├── specs/                                # normative integration specs
│   ├── README.md
│   ├── osbapi-oscal-extension.md
│   ├── architecture.md
│   ├── workflows.md
│   └── profiles/
│       ├── osbapi-crm-profile.md
│       └── osbapi-crm-profile-registration.md
│
├── schemas/                              # JSON Schemas for projections and APIs
│   ├── README.md
│   ├── osbapi/
│   │   ├── broker-provision-response-extension.schema.json
│   │   ├── broker-crm-fetch-response.schema.json
│   │   └── service-catalog-compliance-support.schema.json
│   ├── oscal/
│   │   ├── crm-summary-projection.schema.json
│   │   ├── implemented-requirement-projection.schema.json
│   │   └── leveraged-authorization-projection.schema.json
│   ├── backstage/
│   │   ├── compliance-annotations.schema.json
│   │   ├── entity-envelope.schema.json
│   │   └── plugin-api-response.schema.json
│   └── examples/
│       ├── provision-response.example.json
│       ├── full-crm-response.example.json
│       ├── adapter-entity-response.example.json
│       └── backstage-entity.example.yaml
│
├── broker/                               # OSBAPI broker implementation
│   ├── README.md
│   ├── openapi/
│   │   ├── servicebroker-openapi.yaml    # local copy/overlay of OSB contract [web:96]
│   │   └── compliance-extension.yaml
│   ├── src/
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── routes/
│   │   │   │   ├── catalog.py
│   │   │   │   ├── provision.py
│   │   │   │   ├── update.py
│   │   │   │   ├── deprovision.py
│   │   │   │   └── crm.py
│   │   │   ├── services/
│   │   │   │   ├── catalog_service.py
│   │   │   │   ├── provisioning_service.py
│   │   │   │   ├── crm_projection_service.py
│   │   │   │   └── oscal_resolver.py
│   │   │   ├── models/
│   │   │   │   ├── service_offering.py
│   │   │   │   ├── plan.py
│   │   │   │   ├── provision_request.py
│   │   │   │   └── provision_response.py
│   │   │   └── adapters/
│   │   │       ├── cloud_foundry.py
│   │   │       ├── aws.py
│   │   │       └── service_manager.py
│   ├── config/
│   │   ├── services/
│   │   │   ├── aws-rds-postgres.yaml
│   │   │   └── aws-elasticache-redis.yaml
│   │   └── profiles/
│   │       └── osbapi-crm-profile.yaml
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── fixtures/
│   └── Dockerfile
│
├── compliance/                           # authoritative compliance source material
│   ├── README.md
│   ├── oscal/
│   │   ├── catalogs/
│   │   │   └── imported/                 # external source references or pinned copies
│   │   ├── profiles/
│   │   │   ├── fedramp-high-profile.json
│   │   │   └── osbapi-crm-profile-overlay.json
│   │   ├── components/
│   │   │   ├── broker/
│   │   │   │   └── csb-broker-component.json
│   │   │   ├── services/
│   │   │   │   ├── aws-rds-postgres-component.json
│   │   │   │   └── aws-elasticache-redis-component.json
│   │   │   └── shared/
│   │   │       └── platform-iam-component.json
│   │   ├── ssp/
│   │   │   ├── cloud-platform-ssp.json
│   │   │   └── overlays/
│   │   │       ├── broker-services-overlay.json
│   │   │       └── app-consumer-overlay.json
│   │   ├── assessment-plans/
│   │   │   └── broker-services-sap.json
│   │   ├── assessment-results/
│   │   │   └── broker-services-results.json
│   │   └── poam/
│   │       └── broker-services-poam.json
│   ├── evidence/
│   │   ├── ac-2/
│   │   ├── cm-2/
│   │   ├── sc-13/
│   │   └── inventory/
│   ├── mappings/
│   │   ├── service-offering-to-component.yaml
│   │   ├── plan-to-control-overrides.yaml
│   │   ├── evidence-index.yaml
│   │   └── implementation-status-map.yaml
│   └── scripts/
│       ├── validate_oscal.py
│       ├── build_crm_projection.py
│       └── export_backstage_summary.py
│
├── adapter/                              # thin API joining broker + OSCAL for Backstage
│   ├── README.md
│   ├── openapi/
│   │   └── compliance-adapter.openapi.yaml
│   ├── src/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── entities.py
│   │   │   ├── controls.py
│   │   │   ├── evidence.py
│   │   │   └── health.py
│   │   ├── clients/
│   │   │   ├── broker_client.py
│   │   │   ├── oscal_store_client.py
│   │   │   └── techdocs_client.py
│   │   ├── services/
│   │   │   ├── entity_projection.py
│   │   │   ├── status_normalizer.py
│   │   │   └── cache_service.py
│   │   └── models/
│   │       ├── entity_response.py
│   │       └── control_summary.py
│   ├── tests/
│   │   ├── unit/
│   │   └── integration/
│   └── Dockerfile
│
├── backstage/                            # Backstage integration assets
│   ├── README.md
│   ├── catalog/
│   │   ├── systems/
│   │   │   └── cloud-foundry-platform.yaml
│   │   ├── components/
│   │   │   ├── csb-broker.yaml
│   │   │   └── compliance-adapter.yaml
│   │   ├── resources/
│   │   │   ├── aws-rds-postgres-offering.yaml
│   │   │   └── aws-elasticache-redis-offering.yaml
│   │   └── apps/
│   │       └── customer-portal.yaml
│   ├── plugins/
│   │   └── compliance-broker/
│   │       ├── README.md
│   │       ├── package.json
│   │       ├── src/
│   │       │   ├── plugin.ts
│   │       │   ├── routes.ts
│   │       │   ├── api/
│   │       │   │   └── ComplianceApiClient.ts
│   │       │   ├── components/
│   │       │   │   ├── ComplianceSummaryCard.tsx
│   │       │   │   ├── ControlTable.tsx
│   │       │   │   └── EvidenceLinks.tsx
│   │       │   └── hooks/
│   │       │       └── useComplianceEntity.ts
│   │       └── tests/
│   └── app-config/
│       ├── app-config.local.yaml
│       └── app-config.production.overlay.yaml
│
├── techdocs/                             # richer docs-as-code content by entity
│   ├── platform/
│   │   ├── catalog-info.yaml
│   │   ├── mkdocs.yml
│   │   └── docs/
│   │       ├── index.md
│   │       ├── architecture.md
│   │       ├── inheritance-map.md
│   │       └── assessment-procedures.md
│   ├── broker/
│   │   ├── catalog-info.yaml
│   │   ├── mkdocs.yml
│   │   └── docs/
│   │       ├── index.md
│   │       ├── service-catalog.md
│   │       ├── controls.md
│   │       └── troubleshooting.md
│   ├── services/
│   │   └── aws-rds-postgres/
│   │       ├── catalog-info.yaml
│   │       ├── mkdocs.yml
│   │       └── docs/
│   │           ├── index.md
│   │           ├── hardening.md
│   │           ├── assessment-procedures.md
│   │           └── evidence.md
│   └── apps/
│       └── customer-portal/
│           ├── catalog-info.yaml
│           ├── mkdocs.yml
│           └── docs/
│               ├── index.md
│               ├── dependencies.md
│               └── customer-responsibilities.md
│
├── examples/                             # runnable example payloads and sample configs
│   ├── broker/
│   │   ├── catalog-response.json
│   │   ├── provision-request.json
│   │   ├── provision-response.json
│   │   └── crm-fetch-response.json
│   ├── backstage/
│   │   ├── catalog-info.system.yaml
│   │   ├── catalog-info.component.yaml
│   │   └── catalog-info.resource.yaml
│   ├── oscal/
│   │   ├── component-definition.example.json
│   │   └── ssp-fragment.example.json
│   └── adapter/
│       └── entity-response.json
│
├── scripts/
│   ├── bootstrap.sh
│   ├── validate_all.sh
│   ├── render_mermaid.sh
│   ├── publish_techdocs.sh
│   └── sync_backstage_catalog.sh
│
└── deployments/
    ├── docker-compose/
    │   └── local-stack.yaml
    ├── kubernetes/
    │   ├── broker-deployment.yaml
    │   ├── adapter-deployment.yaml
    │   ├── backstage-configmap.yaml
    │   └── techdocs-publisher-job.yaml
    └── cloudfoundry/
        ├── broker-manifest.yml
        └── adapter-manifest.yml
```

## Minimal version

If you want the smallest viable repo, this is enough to start:

```text
osbapi-oscal-integration/
├── README.md
├── specs/
│   └── osbapi-oscal-extension.md
├── schemas/
│   ├── broker-provision-response-extension.schema.json
│   ├── broker-crm-fetch-response.schema.json
│   └── backstage-compliance-annotations.schema.json
├── broker/
│   ├── src/
│   └── tests/
├── compliance/
│   ├── oscal/
│   │   ├── components/
│   │   └── ssp/
│   └── mappings/
├── adapter/
│   ├── src/
│   └── tests/
├── backstage/
│   └── catalog/
├── techdocs/
│   └── services/
└── examples/
```

## Why this split works

- `broker/` holds runtime OSBAPI behavior and service catalog logic, which belongs close to provisioning code [web:96].
- `compliance/` holds canonical OSCAL artifacts and evidence mappings, which should remain the source of truth instead of being duplicated in portal metadata.
- `adapter/` stays thin and projection-oriented, translating broker and OSCAL state for Backstage consumers.
- `backstage/` contains entity descriptors and optional plugin code, which matches Backstage’s catalog-driven model [web:136].
- `techdocs/` follows Backstage’s standard `catalog-info.yaml` + `mkdocs.yml` + `docs/` structure for docs-as-code publishing [web:136][web:140].
- `schemas/` and `examples/` make the interoperability contract explicit and testable.

## Recommended ownership

```text
broker/        -> platform engineering
compliance/    -> compliance engineering / GRC automation
adapter/       -> platform engineering
backstage/     -> platform enablement / portal engineering
techdocs/      -> shared ownership by service owner + compliance author
schemas/       -> shared, version-reviewed contract surface
examples/      -> shared, used by tests and docs
```

## Suggested first files to create

1. `specs/osbapi-oscal-extension.md`
2. `schemas/osbapi/broker-provision-response-extension.schema.json`
3. `schemas/oscal/crm-summary-projection.schema.json`
4. `broker/src/app/routes/crm.py`
5. `compliance/oscal/components/services/aws-rds-postgres-component.json`
6. `compliance/oscal/ssp/cloud-platform-ssp.json`
7. `adapter/src/api/entities.py`
8. `backstage/catalog/resources/aws-rds-postgres-offering.yaml`
9. `techdocs/services/aws-rds-postgres/mkdocs.yml`
10. `techdocs/services/aws-rds-postgres/docs/index.md`

## Naming guidance

- Use `service-offering` for broker catalog entities and `service-instance` only for live provisioned instances.
- Keep OSCAL files named by responsibility and scope, for example `aws-rds-postgres-component.json` and `cloud-platform-ssp.json`.
- Keep Backstage entity filenames aligned with `metadata.name` to reduce catalog drift.
- Version schemas explicitly if you expect churn, for example `v1/` folders under `schemas/` and `examples/`.
