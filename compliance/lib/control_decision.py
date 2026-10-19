"""Deterministic control decision packets and review-gated grading."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol
from xml.etree import ElementTree as ET

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
PACKET_SCHEMA = (
    ROOT / "compliance/schemas/decision/control_decision_packet_schema_v1.json"
)
GRADE_SCHEMA = ROOT / "compliance/schemas/decision/control_grade_schema_v1.json"
D3FEND_SCHEMA = ROOT / "compliance/schemas/decision/d3fend_nist_mapping_schema_v1.json"
REFERENCE_SCHEMA = (
    ROOT / "compliance/schemas/decision/assessment_reference_schema_v1.json"
)
DEFAULT_D3FEND_MAPPING = ROOT / "compliance/data/d3fend_nist_rev5_mapping.json"
_CONTROL = re.compile(r"^([A-Z]{2})-0*(\d+)(?:[.(]0*([0-9a-z]+)\)?)?$", re.I)
_PLACEHOLDER = re.compile(
    r"\b(?:TBD|TODO|placeholder|no substantive prose)\b|\{\{\s*insert:", re.I
)
_URL = re.compile(r"https?://[^\s)>\]]+", re.I)
_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


def normalize_control_id(value: str) -> str:
    match = _CONTROL.match((value or "").strip().replace(".", "("))
    if not match:
        return (value or "").strip().upper()
    family, base, enhancement = match.groups()
    return f"{family.upper()}-{int(base)}" + (
        f"({int(enhancement) if enhancement.isdigit() else enhancement})"
        if enhancement
        else ""
    )


def _text(node: ET.Element) -> str:
    return "".join(node.itertext()).strip()


def _ods_rows(path: Path) -> dict[str, list[list[str]]]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    tables: dict[str, list[list[str]]] = {}
    for table in root.findall(
        ".//{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table"
    ):
        name = table.attrib.get(
            "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}name", ""
        )
        rows: list[list[str]] = []
        for row in table.findall(
            "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-row"
        ):
            values: list[str] = []
            repeat_rows = min(
                int(
                    row.attrib.get(
                        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-rows-repeated",
                        "1",
                    )
                ),
                10000,
            )
            for cell in row.findall(
                "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-cell"
            ):
                repeats = min(
                    int(
                        cell.attrib.get(
                            "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-columns-repeated",
                            "1",
                        )
                    ),
                    64,
                )
                values.extend([_text(cell)] * repeats)
            while values and not values[-1]:
                values.pop()
            rows.extend([values] * repeat_rows)
        tables[name] = rows
    return tables


def parse_assessment_workbook(path: Path) -> dict[str, list[dict[str, Any]]]:
    objectives: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sheet, rows in _ods_rows(path).items():
        if not re.fullmatch(r"[A-Z]{2}", sheet):
            continue
        header_index = next(
            (
                i
                for i, row in enumerate(rows)
                if "Control ID" in row and "Assessment Procedure Identifier" in row
            ),
            None,
        )
        if header_index is None:
            continue
        headers = rows[header_index]
        positions = {name: headers.index(name) for name in headers if name}
        for row_index, row in enumerate(
            rows[header_index + 1 :], start=header_index + 2
        ):
            raw_id = (
                row[positions["Control ID"]].strip()
                if len(row) > positions["Control ID"]
                else ""
            )
            if not _CONTROL.match(raw_id):
                continue

            def cell(name: str) -> str:
                index = positions.get(name)
                return (
                    row[index].strip() if index is not None and len(row) > index else ""
                )

            procedure = cell("Assessment Procedure Identifier")
            citation = f"workbook:{sheet}:row:{row_index}:{procedure or raw_id}"
            methods = {
                name.lower(): cell(name)
                for name in ("Examine", "Interview", "Test")
                if cell(name)
            }
            objectives[normalize_control_id(raw_id)].append(
                {
                    "citation_id": citation,
                    "procedure_id": procedure,
                    "control_name": cell("Control Name"),
                    "objective": cell(
                        'Assessment Objective("Determine if:" is an implied predecessor to each objective)'
                    ),
                    "methods": methods,
                    "historical_observation": cell("Observations and Evidence"),
                    "historical_result": cell("Assessment Result"),
                    "historical_recommendation": cell("Recommendation for Mitigation"),
                    "historical_context_only": True,
                }
            )
    return dict(objectives)


def export_assessment_reference(
    workbook: Path,
    reference_id: str,
    *,
    include_historical_observations: bool = False,
) -> dict[str, Any]:
    parsed = parse_assessment_workbook(workbook)
    controls: dict[str, Any] = {}
    redactions = {"urls": 0, "emails": 0}

    def safe_text(value: str) -> tuple[str, str]:
        original = value or ""
        redactions["urls"] += len(_URL.findall(original))
        redactions["emails"] += len(_EMAIL.findall(original))
        sanitized = _EMAIL.sub(
            "<REFERENCE_EMAIL>", _URL.sub("<REFERENCE_URL>", original)
        )
        return sanitized, hashlib.sha256(original.encode("utf-8")).hexdigest()

    for control_id, rows in sorted(parsed.items()):
        objectives = []
        for row in rows:
            observation = row["historical_observation"]
            prerequisites = {}
            prerequisite_hashes = {}
            for method in ("examine", "interview", "test"):
                value = row["methods"].get(method, "")
                sanitized, digest = safe_text(value)
                prerequisites[method] = [sanitized] if sanitized else []
                prerequisite_hashes[method] = digest if value else None
            title, title_hash = safe_text(row["control_name"])
            objective, objective_hash = safe_text(row["objective"])
            result_label, result_hash = safe_text(row["historical_result"])
            recommendation, recommendation_hash = safe_text(
                row["historical_recommendation"]
            )
            objectives.append(
                {
                    "objective_id": row["citation_id"],
                    "procedure_id": row["procedure_id"],
                    "title": title,
                    "objective": objective,
                    "prerequisites": prerequisites,
                    "historical": {
                        "observation_present": bool(observation),
                        "observation_sha256": hashlib.sha256(
                            observation.encode("utf-8")
                        ).hexdigest()
                        if observation
                        else None,
                        "observation": observation
                        if include_historical_observations
                        else None,
                        "result": result_label,
                        "recommendation": recommendation,
                        "context_only": True,
                    },
                    "extensions": {
                        "source_text_sha256": {
                            "title": title_hash,
                            "objective": objective_hash,
                            "prerequisites": prerequisite_hashes,
                            "result": result_hash,
                            "recommendation": recommendation_hash,
                        }
                    },
                }
            )
        controls[control_id] = {"control_id": control_id, "objectives": objectives}
    result = {
        "schema_version": 1,
        "reference_id": reference_id,
        "source": {
            "format": workbook.suffix.lower().lstrip("."),
            "filename": workbook.name,
            "sha256": hashlib.sha256(workbook.read_bytes()).hexdigest(),
            "importer": "oscal-vault.assessment-reference:v1",
            "historical_observations_included": include_historical_observations,
            "redactions": redactions,
        },
        "controls": controls,
        "extensions": {},
    }
    validate_assessment_reference(result)
    return result


def validate_assessment_reference(data: dict[str, Any]) -> None:
    schema = json.loads(REFERENCE_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(data))
    if errors:
        raise ValueError("; ".join(error.message for error in errors))


def load_assessment_reference(path: Path) -> dict[str, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_assessment_reference(data)
    result: dict[str, list[dict[str, Any]]] = {}
    for control_id, control in data["controls"].items():
        rows = []
        for objective in control["objectives"]:
            historical = objective["historical"]
            rows.append(
                {
                    "citation_id": objective["objective_id"],
                    "procedure_id": objective["procedure_id"],
                    "control_name": objective["title"],
                    "objective": objective["objective"],
                    "methods": {
                        method: "\n".join(values)
                        for method, values in objective["prerequisites"].items()
                        if values
                    },
                    "historical_observation": historical["observation"] or "",
                    "historical_observation_present": historical["observation_present"],
                    "historical_observation_sha256": historical["observation_sha256"],
                    "historical_result": historical["result"],
                    "historical_recommendation": historical["recommendation"],
                    "historical_context_only": True,
                }
            )
        result[control_id] = rows
    return result


def parse_ssp_implementations(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    plan = json.loads(path.read_text(encoding="utf-8"))["system-security-plan"]
    components = {
        item["uuid"]: item.get("title", item["uuid"])
        for item in plan.get("system-implementation", {}).get("components", [])
    }
    result: dict[str, dict[str, Any]] = {}
    for requirement in plan.get("control-implementation", {}).get(
        "implemented-requirements", []
    ):
        control_id = normalize_control_id(requirement.get("control-id", ""))
        responses: list[dict[str, Any]] = []
        sources = list(requirement.get("by-components", []))
        for statement in requirement.get("statements", []):
            sources.extend(statement.get("by-components", []))
        for index, response in enumerate(sources):
            citation = f"response:{control_id}:{index}"
            responses.append(
                {
                    "citation_id": citation,
                    "provider": components.get(
                        response.get("component-uuid", ""),
                        response.get("component-uuid", "unknown"),
                    ),
                    "status": (response.get("implementation-status") or {}).get(
                        "state", "missing"
                    ),
                    "narrative": (response.get("description") or "").strip(),
                    "links": response.get("links") or [],
                    "props": response.get("props") or [],
                }
            )
        result[control_id] = {
            "responses": responses,
            "parameters": requirement.get("set-parameters") or [],
            "links": requirement.get("links") or [],
        }
    return result, components


def load_instructions(path: Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("entries") or []


def load_d3fend(
    path: Path | None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    if not path:
        return {}, {
            "status": "unavailable",
            "reason": "No pinned MITRE D3FEND-to-NIST Rev. 5 mapping was supplied.",
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(D3FEND_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(data))
    if errors:
        raise ValueError("; ".join(error.message for error in errors))
    by_control: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in data["mappings"]:
        for control in item["nist_controls"]:
            by_control[normalize_control_id(control)].append(item)
    return dict(by_control), {
        "status": "available",
        "source": data["source"],
        "source_sha256": data["source_sha256"],
    }


D3FEND_TECHNIQUE_METADATA: dict[str, tuple[str, str, str]] = {
    "D3-ACH": (
        "Application Configuration Hardening",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#ApplicationConfigurationHardening",
        "Hardening application settings, disabling unnecessary features, and securing configurations.",
    ),
    "D3-AH": (
        "Application Hardening",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#ApplicationHardening",
        "Applying defenses to restrict application attack surface and enforce secure execution.",
    ),
    "D3-AL": (
        "Account Locking",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#AccountLocking",
        "Automatically disabling, locking, or removing accounts based on inactivity, policy, or anomalies.",
    ),
    "D3-DA": (
        "Dynamic Analysis",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#DynamicAnalysis",
        "Analyzing program behavior during execution to detect vulnerabilities or malicious activity.",
    ),
    "D3-DAM": (
        "Domain Account Monitoring",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#DomainAccountMonitoring",
        "Auditing and monitoring domain account creation, privilege changes, and authentication events.",
    ),
    "D3-DE": (
        "Decoy Environment",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#DecoyEnvironment",
        "Deploying decoy systems, sandboxes, or deception environments to detect adversarial activity.",
    ),
    "D3-DLIC": (
        "Driver Load Integrity Checking",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#DriverLoadIntegrityChecking",
        "Verifying cryptographic signatures and integrity before loading system drivers.",
    ),
    "D3-DO": (
        "Decoy Object",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#DecoyObject",
        "Planting decoy credentials, files, or resources to identify unauthorized access.",
    ),
    "D3-DTP": (
        "Domain Trust Policy",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#DomainTrustPolicy",
        "Enforcing cryptographic trust, cross-domain verification, and mutual authentication policies.",
    ),
    "D3-EAL": (
        "Executable Allowlisting",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#ExecutableAllowlisting",
        "Restricting executable execution to explicitly authorized software binaries and hashes.",
    ),
    "D3-EDL": (
        "Executable Denylisting",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#ExecutableDenylisting",
        "Prohibiting execution of known malicious or unauthorized binaries and scripts.",
    ),
    "D3-EI": (
        "Execution Isolation",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#ExecutionIsolation",
        "Isolating processes, containers, or virtual environments to prevent lateral privilege escalation.",
    ),
    "D3-ET": (
        "Encrypted Tunnels",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#EncryptedTunnels",
        "Encrypting network communication channels using TLS/IPsec tunnels to protect confidentiality and integrity.",
    ),
    "D3-FA": (
        "File Analysis",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#FileAnalysis",
        "Inspecting file metadata, signatures, and content structure for integrity and malicious payloads.",
    ),
    "D3-FCR": (
        "File Content Rules",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#FileContentRules",
        "Enforcing content inspection and policy rules on files traversing system boundaries.",
    ),
    "D3-FV": (
        "Firmware Verification",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#FirmwareVerification",
        "Validating firmware cryptographic signatures and measurements prior to boot execution.",
    ),
    "D3-HBPI": (
        "Hardware-based Process Isolation",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#Hardware-basedProcessIsolation",
        "Using hardware enclaves, virtualization rings, or memory protection to isolate critical processes.",
    ),
    "D3-ID": (
        "Identifier Analysis",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#IdentifierAnalysis",
        "Validating and analyzing session tokens, process identifiers, or subject credentials.",
    ),
    "D3-ITF": (
        "Inbound Traffic Filtering",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#InboundTrafficFiltering",
        "Filtering incoming network packets, ports, and protocols using perimeter and host firewalls.",
    ),
    "D3-JFAPA": (
        "Job Function Access Pattern Analysis",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#JobFunctionAccessPatternAnalysis",
        "Monitoring user and service access patterns against designated organizational job roles.",
    ),
    "D3-LAM": (
        "Local Account Monitoring",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#LocalAccountMonitoring",
        "Auditing local system accounts, authentication attempts, and privilege delegations.",
    ),
    "D3-LFP": (
        "Local File Permissions",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#LocalFilePermissions",
        "Restricting file system read, write, and execute permissions according to least privilege.",
    ),
    "D3-MA": (
        "Message Analysis",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#MessageAnalysis",
        "Analyzing message headers, payloads, and protocols for policy compliance and anomalies.",
    ),
    "D3-MAC": (
        "Mandatory Access Control",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#MandatoryAccessControl",
        "Enforcing non-discretionary access policies based on security labels and classification clearance.",
    ),
    "D3-MAN": (
        "Message Authentication",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#MessageAuthentication",
        "Verifying origin authenticity and cryptographic integrity of transmitted messages.",
    ),
    "D3-MFA": (
        "Multi-factor Authentication",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#Multi-factorAuthentication",
        "Requiring two or more independent authentication factors prior to granting system access.",
    ),
    "D3-NI": (
        "Network Isolation",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#NetworkIsolation",
        "Segmenting networks and isolating security zones to restrict unauthorized lateral movement.",
    ),
    "D3-NTA": (
        "Network Traffic Analysis",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#NetworkTrafficAnalysis",
        "Analyzing network flow telemetry and packet captures to identify malicious or anomalous communications.",
    ),
    "D3-OSM": (
        "Operating System Monitoring",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#OperatingSystemMonitoring",
        "Monitoring OS audit logs, system calls, service states, and kernel events for unauthorized actions.",
    ),
    "D3-OTF": (
        "Outbound Traffic Filtering",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#OutboundTrafficFiltering",
        "Inspecting and restricting egress traffic to authorized destinations, ports, and protocols.",
    ),
    "D3-PA": (
        "Process Analysis",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#ProcessAnalysis",
        "Inspecting running processes, memory spaces, and thread execution for anomalous behavior.",
    ),
    "D3-PFV": (
        "Peripheral Firmware Verification",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#PeripheralFirmwareVerification",
        "Validating cryptographic integrity of firmware installed on attached peripheral hardware devices.",
    ),
    "D3-PH": (
        "Platform Hardening",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#PlatformHardening",
        "Applying system security configuration baselines, removing unneeded software, and closing unused ports.",
    ),
    "D3-PM": (
        "Platform Monitoring",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#PlatformMonitoring",
        "Aggregating security telemetry across host platforms to detect configuration drift and compromises.",
    ),
    "D3-RAPA": (
        "Resource Access Pattern Analysis",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#ResourceAccessPatternAnalysis",
        "Profiling and analyzing baseline access patterns to sensitive data stores and services.",
    ),
    "D3-SCF": (
        "System Call Filtering",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#SystemCallFiltering",
        "Restricting allowed kernel system calls per process using seccomp or mandatory execution filters.",
    ),
    "D3-SCP": (
        "System Configuration Permissions",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#SystemConfigurationPermissions",
        "Gating modifications to system configuration files and registry hives to authorized privileged roles.",
    ),
    "D3-SFV": (
        "System Firmware Verification",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#SystemFirmwareVerification",
        "Cryptographically verifying UEFI/BIOS firmware integrity during measured boot sequences.",
    ),
    "D3-SU": (
        "Software Update",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#SoftwareUpdate",
        "Patching operating systems and application software within required security SLAs.",
    ),
    "D3-UAP": (
        "User Account Permissions",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#UserAccountPermissions",
        "Managing user role assignments and granular access permissions across applications.",
    ),
    "D3-UBA": (
        "User Behavior Analysis",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#UserBehaviorAnalysis",
        "Profiling baseline user activity to identify anomalous account takeovers or insider threats.",
    ),
    "D3-UDTA": (
        "User Data Transfer Analysis",
        "http://d3fend.mitre.org/ontologies/d3fend.owl#UserDataTransferAnalysis",
        "Monitoring and controlling volume, destination, and sensitivity of user data downloads and transfers.",
    ),
}


def parse_d3fend_catalog_xlsx(path: Path) -> dict[str, list[str]]:
    """Parse D3FEND-to-NIST mappings from an OpenXML workbook (.xlsx) using stdlib only."""
    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            sst = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in sst.findall(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"
            ):
                t = si.find(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                )
                if t is not None:
                    shared_strings.append(t.text or "")
                else:
                    shared_strings.append(
                        "".join(
                            x.text or ""
                            for x in si.findall(
                                ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                            )
                        )
                    )

        wb = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
        sheet_target = "worksheets/sheet2.xml"
        for s in wb.findall(
            ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"
        ):
            if "d3fend" in s.attrib.get("name", "").lower():
                rid = s.attrib[
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                ]
                sheet_target = rel_map.get(rid, sheet_target)
                break
        if not sheet_target.startswith("xl/"):
            sheet_target = f"xl/{sheet_target}"

        sheet = ET.fromstring(archive.read(sheet_target))
        tech_to_controls: dict[str, set[str]] = defaultdict(set)
        for row in sheet.findall(
            ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"
        ):
            cells: dict[str, str] = {}
            for c in row.findall(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"
            ):
                coord = c.attrib.get("r", "")
                col = "".join(x for x in coord if x.isalpha())
                t = c.attrib.get("t")
                v = c.find(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v"
                )
                val = ""
                if v is not None and v.text:
                    if t == "s":
                        idx = int(v.text)
                        val = shared_strings[idx] if idx < len(shared_strings) else ""
                    else:
                        val = v.text
                elif t == "inlineStr":
                    is_el = c.find(
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is"
                    )
                    if is_el is not None:
                        val = "".join(
                            x.text or ""
                            for x in is_el.findall(
                                ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                            )
                        )
                cells[col] = val.strip()

            ctrl = cells.get("A", "")
            tech_field = cells.get("E", "")
            if ctrl and tech_field and ctrl != "Control Identifier":
                norm_ctrl = normalize_control_id(ctrl)
                for item in tech_field.split("|"):
                    item = item.strip()
                    if item and item != "NA":
                        tech_to_controls[item].add(norm_ctrl)

    return {k: sorted(v) for k, v in tech_to_controls.items()}


def export_d3fend_mapping(
    xlsx_path: Path, output_path: Path | None = None
) -> dict[str, Any]:
    """Convert an OpenXML D3FEND catalog into a validated JSON mapping artifact."""
    source_sha256 = hashlib.sha256(xlsx_path.read_bytes()).hexdigest()
    tech_to_controls = parse_d3fend_catalog_xlsx(xlsx_path)

    mappings: list[dict[str, Any]] = []
    for tid, (name, uri, desc) in sorted(D3FEND_TECHNIQUE_METADATA.items()):
        ctrls = tech_to_controls.get(tid, [])
        if ctrls:
            mappings.append(
                {
                    "d3fend_id": tid,
                    "name": name,
                    "nist_controls": ctrls,
                    "description": desc,
                    "source_uri": uri,
                }
            )

    payload = {
        "schema_version": 1,
        "source": "MITRE D3FEND to NIST SP 800-53 Rev. 5 Catalog Mapping",
        "source_sha256": source_sha256,
        "mappings": mappings,
    }
    schema = json.loads(D3FEND_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        raise ValueError("; ".join(error.message for error in errors))

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return payload


def _family_instruction(entry: dict[str, Any], family: str) -> bool:
    anchor = entry.get("anchor") or ""
    return (
        f"({family})" in anchor
        or re.search(rf"\b{family}-\d+", entry.get("text") or "", re.I) is not None
    )


def build_packets(
    ssp: Path,
    workbook: Path | None = None,
    *,
    assessment_reference: Path | None = None,
    instruction_ledger: Path | None = None,
    d3fend_mapping: Path | None = None,
    controls: set[str] | None = None,
    include_historical_observations: bool = False,
) -> list[dict[str, Any]]:
    if bool(workbook) == bool(assessment_reference):
        raise ValueError("provide exactly one of workbook or assessment_reference")
    reference_path = assessment_reference or workbook
    assert reference_path is not None
    implementations, _ = parse_ssp_implementations(ssp)
    objectives = (
        load_assessment_reference(assessment_reference)
        if assessment_reference
        else parse_assessment_workbook(reference_path)
    )
    instructions = load_instructions(instruction_ledger)
    d3fend, d3fend_meta = load_d3fend(d3fend_mapping)
    selected = sorted(
        (set(implementations) | set(objectives))
        if not controls
        else {normalize_control_id(item) for item in controls}
    )
    packets: list[dict[str, Any]] = []
    for control_id in selected:
        implementation = implementations.get(
            control_id, {"responses": [], "parameters": [], "links": []}
        )
        family = control_id.split("-")[0]
        relevant_instructions = []
        for entry in instructions:
            if _family_instruction(entry, family):
                relevant_instructions.append(
                    {
                        "citation_id": f"instruction:{entry.get('stable_key') or entry.get('locator')}",
                        "text": entry.get("text", ""),
                        "anchor": entry.get("anchor"),
                        "disposition": entry.get("disposition"),
                        "source_locator": entry.get("locator"),
                    }
                )
        findings: list[dict[str, Any]] = []
        responses = implementation["responses"]
        substantive = [
            item
            for item in responses
            if len(item["narrative"]) >= 80
            and not _PLACEHOLDER.search(item["narrative"])
        ]
        if not responses:
            findings.append(
                {
                    "id": "missing-response",
                    "severity": "blocker",
                    "description": "No OSCAL implementation response exists.",
                    "citations": [],
                }
            )
        elif not substantive:
            findings.append(
                {
                    "id": "missing-substantive-narrative",
                    "severity": "blocker",
                    "description": "No response contains substantive implementation prose.",
                    "citations": [item["citation_id"] for item in responses],
                }
            )
        planned = [
            item for item in responses if item["status"] in {"planned", "missing"}
        ]
        if planned:
            findings.append(
                {
                    "id": "unapproved-implementation-status",
                    "severity": "major",
                    "description": "One or more responses remain planned or have no status.",
                    "citations": [item["citation_id"] for item in planned],
                }
            )
        objective_rows = []
        for source_row in objectives.get(control_id, []):
            row = dict(source_row)
            observation = row.pop("historical_observation", "")
            row["historical_observation_present"] = row.get(
                "historical_observation_present", bool(observation)
            )
            row["historical_observation_sha256"] = row.get(
                "historical_observation_sha256"
            ) or (
                hashlib.sha256(observation.encode("utf-8")).hexdigest()
                if observation
                else None
            )
            if include_historical_observations:
                row["historical_observation"] = observation
                row["sensitive_historical_content"] = True
            objective_rows.append(row)
        if not objective_rows:
            findings.append(
                {
                    "id": "missing-workbook-objectives",
                    "severity": "major",
                    "description": "No assessment objectives were found in the tailoring workbook.",
                    "citations": [],
                }
            )
        evidence_links = implementation["links"] + [
            link for response in responses for link in response["links"]
        ]
        evidenced_methods = {
            str((link.get("resource-fragment") or link.get("rel") or "")).lower()
            for link in evidence_links
            if isinstance(link, dict)
        }
        for method in ("examine", "interview", "test"):
            rows_requiring_method = [
                row for row in objective_rows if method in row["methods"]
            ]
            if rows_requiring_method and not any(
                method in value for value in evidenced_methods
            ):
                findings.append(
                    {
                        "id": f"missing-{method}-evidence-reference",
                        "severity": "major",
                        "description": (
                            f"Workbook {method} procedures exist but no current OSCAL "
                            f"evidence link is classified for {method}."
                        ),
                        "citations": [
                            row["citation_id"] for row in rows_requiring_method
                        ],
                    }
                )
        technique_rows = []
        for item in d3fend.get(control_id, []):
            technique_rows.append(
                {"citation_id": f"d3fend:{item['d3fend_id']}", **item}
            )
        packet = {
            "schema_version": 1,
            "packet_id": f"control:{control_id}",
            "control_id": control_id,
            "sources": {
                "ssp": {
                    "path": str(ssp),
                    "sha256": hashlib.sha256(ssp.read_bytes()).hexdigest(),
                },
                "assessment_reference": {
                    "path": str(reference_path),
                    "sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
                    "format": "normalized-json"
                    if assessment_reference
                    else "source-workbook",
                },
            },
            "data_handling": {
                "historical_observations_included": include_historical_observations,
                "llm_safe_default": not include_historical_observations,
            },
            "implementation": implementation,
            "assessment_objectives": objective_rows,
            "instructions": relevant_instructions,
            "d3fend": {**d3fend_meta, "techniques": technique_rows},
            "deterministic_findings": findings,
            "allowed_citations": sorted(
                [item["citation_id"] for item in responses]
                + [item["citation_id"] for item in objective_rows]
                + [item["citation_id"] for item in relevant_instructions]
                + [item["citation_id"] for item in technique_rows]
            ),
        }
        validate_packet(packet)
        packets.append(packet)
    return packets


def validate_packet(packet: dict[str, Any]) -> None:
    schema = json.loads(PACKET_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(packet))
    if errors:
        raise ValueError("; ".join(error.message for error in errors))


def deterministic_grade(packet: dict[str, Any]) -> dict[str, Any]:
    findings = packet["deterministic_findings"]
    severities = {item["severity"] for item in findings}
    grade = (
        "insufficient"
        if "blocker" in severities
        else "partial"
        if findings
        else "review_required"
    )
    citations = sorted(
        {citation for item in findings for citation in item["citations"]}
    )
    result = {
        "schema_version": 1,
        "packet_id": packet["packet_id"],
        "control_id": packet["control_id"],
        "method": "deterministic_preflight",
        "grade": grade,
        "confidence": 1.0,
        "summary": "Deterministic preflight identifies prerequisites and evidence gaps; it does not approve implementation status.",
        "strengths": [],
        "missing_prerequisites": [
            {
                "prerequisite_id": item["id"],
                "description": item["description"],
                "severity": item["severity"],
                "citations": item["citations"],
            }
            for item in findings
        ],
        "recommended_actions": [item["description"] for item in findings],
        "citations": citations,
        "human_review": {
            "required": True,
            "status": "pending",
            "reviewer": None,
            "decision_ref": None,
        },
    }
    validate_grade(result, packet)
    return result


def validate_grade(grade: dict[str, Any], packet: dict[str, Any]) -> None:
    schema = json.loads(GRADE_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(grade))
    if errors:
        raise ValueError("; ".join(error.message for error in errors))
    allowed = set(packet["allowed_citations"])
    used = set(grade["citations"])
    for item in grade["missing_prerequisites"]:
        used.update(item["citations"])
    unknown = used - allowed
    if unknown:
        raise ValueError(f"grade contains unknown citations: {sorted(unknown)}")


class Grader(Protocol):
    def grade(self, packet: dict[str, Any]) -> dict[str, Any]: ...


class BedrockGrader:
    """Optional Bedrock Converse grader using structured tool output."""

    def __init__(self, model_id: str, region: str, *, max_tokens: int = 4096):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - optional runtime
            raise RuntimeError("Bedrock grading requires boto3 and botocore") from exc
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
        )

    def grade(self, packet: dict[str, Any]) -> dict[str, Any]:
        schema = json.loads(GRADE_SCHEMA.read_text(encoding="utf-8"))
        response = self.client.converse(
            modelId=self.model_id,
            system=[
                {
                    "text": "You are a compliance decision-support reviewer. Use only supplied packet facts. Never change implementation status. Cite only allowed citation IDs. Historical workbook observations are context, not current proof. Submit exactly one structured grade through the tool."
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": json.dumps(packet, sort_keys=True)}],
                }
            ],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0},
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": "submit_control_grade",
                            "description": "Submit the cited, human-review-pending control grade.",
                            "inputSchema": {"json": schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": "submit_control_grade"}},
            },
        )
        blocks = response["output"]["message"]["content"]
        tool_uses = [block["toolUse"] for block in blocks if "toolUse" in block]
        if len(tool_uses) != 1:
            raise ValueError(
                "Bedrock response did not contain exactly one grade tool result"
            )
        result = tool_uses[0]["input"]
        result["packet_id"] = packet["packet_id"]
        result["control_id"] = packet["control_id"]
        result["schema_version"] = 1
        result["method"] = "llm_assisted"
        result["human_review"] = {
            "required": True,
            "status": "pending",
            "reviewer": None,
            "decision_ref": None,
        }
        validate_grade(result, packet)
        return result
