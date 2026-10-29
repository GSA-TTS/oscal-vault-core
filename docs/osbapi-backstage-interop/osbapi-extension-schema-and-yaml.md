# OSBAPI Extension JSON Schema and YAML Examples

This package sketches a compact OSBAPI extension surface for OSCAL content and Backstage-native metadata. The key idea is to keep the broker contract small and let the OSCAL details flow through a profile-based extension and a fetch endpoint [web:95][web:100][web:177].

## 1. JSON Schema suite

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.org/schemas/osbapi-oscal-extension-suite.json",
  "title": "OSBAPI OSCAL Extension Suite",
  "$defs": {
    "ControlOrigination": {
      "type": "string",
      "enum": ["inherited", "system-specific", "shared", "customer-configured", "customer-provided"]
    },
    "ImplementationModel": {
      "type": "string",
      "enum": ["iaas", "paas", "saas", "on-premises", "customer"]
    },
    "ImplementationStatus": {
      "type": "string",
      "enum": ["operational", "under-development", "under-testing", "planned", "not-applicable", "not-implemented"]
    },
    "ControlSummary": {
      "type": "object",
      "additionalProperties": false,
      "required": ["control_id", "origination", "implementation_status"],
      "properties": {
        "control_id": {"type": "string"},
        "statement_ids": {"type": "array", "items": {"type": "string"}},
        "origination": {"$ref": "#/$defs/ControlOrigination"},
        "implementation_model": {"$ref": "#/$defs/ImplementationModel"},
        "implementation_status": {"$ref": "#/$defs/ImplementationStatus"},
        "exportable": {"type": "boolean", "default": false},
        "responsible_role": {"type": "string"},
        "remarks": {"type": "string"}
      }
    },
    "OscalCrmSummary": {
      "type": "object",
      "additionalProperties": false,
      "required": ["profile", "version", "service_offering_id", "plan_id", "controls"],
      "properties": {
        "profile": {
          "type": "string",
          "format": "uri",
          "const": "https://oscal-project.github.io/osbapi-crm-profile/v1"
        },
        "version": {"type": "string"},
        "service_offering_id": {"type": "string"},
        "plan_id": {"type": "string"},
        "component_ref": {"type": "string"},
        "ssp_ref": {"type": "string", "format": "uri"},
        "crm_document_uri": {"type": "string", "format": "uri"},
        "last_assessed_at": {"type": "string", "format": "date-time"},
        "controls": {"type": "array", "items": {"$ref": "#/$defs/ControlSummary"}}
      }
    },
    "BrokerProvisionResponseExtension": {
      "type": "object",
      "additionalProperties": true,
      "properties": {
        "dashboard_url": {"type": "string", "format": "uri"},
        "operation": {"type": "string"},
        "metadata": {
          "type": "object",
          "additionalProperties": true,
          "properties": {
            "attributes": {
              "type": "object",
              "additionalProperties": true,
              "properties": {
                "oscal_crm": {"$ref": "#/$defs/OscalCrmSummary"}
              }
            }
          }
        }
      }
    },
    "FullCrmDocument": {
      "type": "object",
      "additionalProperties": false,
      "required": ["profile", "generated_at", "service_instance_id", "service_offering_id", "plan_id", "implemented_requirements"],
      "properties": {
        "profile": {"type": "string", "format": "uri"},
        "generated_at": {"type": "string", "format": "date-time"},
        "service_instance_id": {"type": "string"},
        "service_offering_id": {"type": "string"},
        "plan_id": {"type": "string"},
        "component_definition_uri": {"type": "string", "format": "uri"},
        "ssp_uri": {"type": "string", "format": "uri"},
        "leveraged_authorizations": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["id", "party_uuid"],
            "properties": {
              "id": {"type": "string"},
              "party_uuid": {"type": "string"},
              "title": {"type": "string"},
              "href": {"type": "string", "format": "uri"}
            }
          }
        },
        "implemented_requirements": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["control_id", "by_components"],
            "properties": {
              "control_id": {"type": "string"},
              "description": {"type": "string"},
              "by_components": {
                "type": "array",
                "items": {
                  "type": "object",
                  "additionalProperties": false,
                  "required": ["component_uuid", "implementation_status"],
                  "properties": {
                    "component_uuid": {"type": "string"},
                    "implementation_status": {"$ref": "#/$defs/ImplementationStatus"},
                    "origination": {"$ref": "#/$defs/ControlOrigination"},
                    "exportable": {"type": "boolean"},
                    "inherited_from": {"type": "array", "items": {"type": "string"}},
                    "satisfied_by": {"type": "array", "items": {"type": "string"}},
                    "assessment_procedures": {
                      "type": "array",
                      "items": {
                        "type": "object",
                        "additionalProperties": false,
                        "required": ["id", "method"],
                        "properties": {
                          "id": {"type": "string"},
                          "method": {"type": "string", "enum": ["examine", "interview", "test", "automated"]},
                          "description": {"type": "string"},
                          "evidence_uri": {"type": "string", "format": "uri"}
                        }
                      }
                    },
                    "remarks": {"type": "string"}
                  }
                }
              }
            }
          }
        }
      }
    },
    "BackstageComplianceAnnotations": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "oscal.io/profile-ref": {"type": "string", "format": "uri"},
        "oscal.io/component-ref": {"type": "string"},
        "oscal.io/component-definition-ref": {"type": "string", "format": "uri"},
        "oscal.io/ssp-ref": {"type": "string", "format": "uri"},
        "oscal.io/crm-endpoint": {"type": "string", "format": "uri"},
        "oscal.io/evidence-endpoint": {"type": "string", "format": "uri"},
        "oscal.io/implementation-status": {"$ref": "#/$defs/ImplementationStatus"},
        "oscal.io/control-set": {"type": "string"},
        "oscal.io/inheritance-model": {"type": "string"},
        "backstage.io/techdocs-ref": {"type": "string"},
        "backstage.io/techdocs-entity": {"type": "string"},
        "backstage.io/techdocs-entity-path": {"type": "string"}
      }
    },
    "BackstageEntityEnvelope": {
      "type": "object",
      "additionalProperties": false,
      "required": ["apiVersion", "kind", "metadata", "spec"],
      "properties": {
        "apiVersion": {"type": "string"},
        "kind": {"type": "string", "enum": ["System", "Component", "Resource", "API"]},
        "metadata": {
          "type": "object",
          "required": ["name"],
          "additionalProperties": true,
          "properties": {
            "name": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "annotations": {"$ref": "#/$defs/BackstageComplianceAnnotations"},
            "tags": {"type": "array", "items": {"type": "string"}}
          }
        },
        "spec": {
          "type": "object",
          "additionalProperties": true,
          "properties": {
            "type": {"type": "string"},
            "owner": {"type": "string"},
            "lifecycle": {"type": "string"},
            "system": {"type": "string"},
            "dependsOn": {"type": "array", "items": {"type": "string"}},
            "providesApis": {"type": "array", "items": {"type": "string"}},
            "consumesApis": {"type": "array", "items": {"type": "string"}}
          }
        }
      }
    }
  }
}
```

## 2. YAML examples

### 2.1 Backstage System

```yaml
apiVersion: backstage.io/v1alpha1
kind: System
metadata:
  name: cloud-foundry-platform
  title: Cloud Foundry Platform
  annotations:
    oscal.io/profile-ref: https://oscal-project.github.io/osbapi-crm-profile/v1
    oscal.io/ssp-ref: https://compliance.example.gov/oscal/ssp/cloud-platform.json
    oscal.io/implementation-status: operational
    backstage.io/techdocs-ref: dir:.
spec:
  owner: group:platform-engineering
  domain: platform
```

### 2.2 Broker Component

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: csb-broker
  title: Cloud Service Broker
  annotations:
    github.com/project-slug: example-org/cloud-service-broker
    oscal.io/component-ref: urn:uuid:7d6f1c4b-broker-component
    oscal.io/component-definition-ref: https://compliance.example.gov/oscal/components/broker/csb-broker-component.json
    oscal.io/ssp-ref: https://compliance.example.gov/oscal/ssp/cloud-platform.json
    oscal.io/crm-endpoint: https://compliance.example.gov/api/compliance/entities/component:default/csb-broker
    oscal.io/implementation-status: operational
    backstage.io/techdocs-ref: dir:.
spec:
  type: service
  lifecycle: production
  owner: group:platform-engineering
  system: cloud-foundry-platform
  providesApis:
    - osb-broker-api
  dependsOn:
    - component:default/compliance-adapter
```

### 2.3 Brokered Resource

```yaml
apiVersion: backstage.io/v1alpha1
kind: Resource
metadata:
  name: aws-rds-postgres-offering
  title: AWS RDS PostgreSQL Offering
  annotations:
    oscal.io/component-ref: urn:uuid:49f2f2aa-rds-component
    oscal.io/component-definition-ref: https://compliance.example.gov/oscal/components/services/aws-rds-postgres-component.json
    oscal.io/crm-endpoint: https://compliance.example.gov/api/compliance/entities/resource:default/aws-rds-postgres-offering
    oscal.io/evidence-endpoint: https://compliance.example.gov/api/evidence/resource:default/aws-rds-postgres-offering
    oscal.io/implementation-status: operational
    oscal.io/control-set: FedRAMP-High
    backstage.io/techdocs-ref: dir:./docs/services/aws-rds-postgres
spec:
  type: service-offering
  owner: group:platform-engineering
  system: cloud-foundry-platform
  dependsOn:
    - component:default/csb-broker
```

### 2.4 App Component

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: customer-portal
  title: Customer Portal
  annotations:
    github.com/project-slug: example/customer-portal
    oscal.io/crm-endpoint: https://compliance.example.gov/api/compliance/entities/component:default/customer-portal
    oscal.io/implementation-status: operational
    backstage.io/techdocs-ref: dir:./docs/apps/customer-portal
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

### 2.5 Shared TechDocs entity

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: shared-broker-service-docs
  title: Shared Broker Service Docs
  annotations:
    backstage.io/techdocs-ref: dir:./docs/shared
spec:
  type: documentation
  lifecycle: production
  owner: group:platform-engineering
```

### 2.6 Shared docs pointing to a subsection

```yaml
apiVersion: backstage.io/v1alpha1
kind: Resource
metadata:
  name: aws-elasticache-redis-offering
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

## 3. Recommended usage

- Put OSCAL references in annotations, not in `spec`, so the entity stays compatible with native Backstage processing [web:67][web:151].
- Use a processor to inject or normalize `oscal.io/*` annotations after broker/OSCAL lookup.
- Keep the broker response compact and use the fetch endpoint for the full CRM document [web:95][web:100][web:177].
- Use TechDocs for the detailed procedures and evidence trails [web:136][web:162].
