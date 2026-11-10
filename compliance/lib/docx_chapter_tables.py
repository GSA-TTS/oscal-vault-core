"""Project governed system sources into native Chapters 1-12 SSP tables."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from compliance.lib.named_table_fill import NamedTableSpec, fill_named_table

ASSETS = NamedTableSpec(
    name="system_assets",
    headers=("Asset Type", "Description of Function or Service Provided"),
    columns=("asset_type", "description"),
)
ROLES = NamedTableSpec(
    name="user_roles",
    headers=(
        "Role",
        "Internal or External",
        "Privileged (P), Non-Privileged (NP), or No Logical Access (NLA)",
        "Sensitivity Level",
        "Sensitivity Level",
        "Authorized Privileges",
        "Functions Performed",
        "MFA Authentication Method",
    ),
    columns=(
        "name",
        "internal_external",
        "privilege_level",
        "sensitivity_level",
        "sensitivity_detail",
        "authorized_privileges",
        "functions_performed",
        "mfa_authentication_method",
    ),
)
PORTS = NamedTableSpec(
    name="ports_protocols_services",
    headers=(
        "Direction (Inbound, Outbound, or Bi-Directional)",
        "Boundary Crossings (Y/N)",
        "Source",
        "Destination",
        "Ports (T or U)",
        "Protocols",
        "Services",
        "Purpose",
        "Encrypted (Y/N)",
        "Data Sensitivity",
    ),
    columns=(
        "direction",
        "boundary_crossing",
        "source",
        "destination",
        "ports",
        "protocols",
        "services",
        "purpose",
        "encrypted",
        "data_sensitivity",
    ),
)
INFORMATION_TYPES = NamedTableSpec(
    name="information_types",
    headers=("Information Type", "Confidentiality", "Integrity", "Availability"),
    columns=("information_type", "confidentiality", "integrity", "availability"),
)
IMPACTS = NamedTableSpec(
    name="security_objective_impacts",
    headers=("Security Objective", "Impact Level"),
    columns=("objective", "impact"),
)
ASSURANCE = NamedTableSpec(
    name="assurance_levels",
    headers=("Assurance Levels", "Implemented Assurance Level"),
    columns=("level", "implementation"),
)
PROVIDING = NamedTableSpec(
    name="providing_systems",
    headers=(
        "[Providing System Name] (FISMA System Identifier)",
        "[Providing System Owner]",
        "Control Identifier, Name",
        "Common/ Hybrid",
    ),
    columns=("system", "owner", "controls", "inheritance"),
)
RECEIVING = NamedTableSpec(
    name="receiving_systems",
    headers=(
        "[Receiving System Name] (FISMA System Identifier)",
        "[Receiving System Owner]",
        "Control Identifier, Name",
        "Common/ Hybrid",
    ),
    columns=("system", "owner", "controls", "inheritance"),
)
URLS = NamedTableSpec(
    name="system_urls",
    headers=(
        "System Component",
        "Application URL",
        "Internal, External, or Both",
        "MFA Authentication Method",
    ),
    columns=("component", "url", "exposure", "mfa"),
)
INVENTORY = NamedTableSpec(
    name="physical_virtual_inventory",
    headers=(
        "Component Name",
        "OS/Make",
        "Operational Environment",
        "Location (Physical or Virtual)",
    ),
    columns=("component", "os_make", "environment", "location"),
)
SOFTWARE = NamedTableSpec(
    name="software_inventory",
    headers=(
        "Software Component/Name",
        "Function",
        "Version",
        "Patch Level",
        "Virtual (Yes/No)",
    ),
    columns=("component", "function", "version", "patch_level", "virtual"),
)
AWS_SERVICES = NamedTableSpec(
    name="aws_services",
    headers=(
        "AWS Service Name",
        "Approval Status (FedRAMP and/or OCISO Approved)",
        "Brief Description of Use(s)",
    ),
    columns=("service", "approval", "use"),
)
INTERCONNECTIONS = NamedTableSpec(
    name="interconnection_summary",
    headers=(
        "System Name",
        "Organization",
        "Type of System (Major Information System/ Minor Application/ Subsystem)",
        "Agreement Type (IEA, ISA, MOA)",
        "Date of Agreement",
        "FIPS 199 Security Category of System",
        "A&A Status of System",
        "Name and Title of AO",
    ),
    columns=(
        "system",
        "organization",
        "system_type",
        "agreement_type",
        "agreement_date",
        "fips_category",
        "authorization_status",
        "authorizing_official",
    ),
)
INTERCONNECTION_DETAILS = NamedTableSpec(
    name="interconnection_details",
    headers=(
        "System Name",
        "Organization",
        "Point of Contact and Phone Number",
        "Connection Security (IPSec VPN, SSL, Certificates, Secure File Transfer, etc.)",
        "Data Direction (incoming, outgoing, or both)",
        "Information Being Transmitted",
        "Ports or Circuit #",
    ),
    columns=(
        "system",
        "organization",
        "point_of_contact",
        "connection_security",
        "direction",
        "information",
        "ports",
    ),
)
REVISION_HISTORY = NamedTableSpec(
    name="revision_history",
    headers=("Date", "Comments", "Version", "Author"),
    columns=("date", "comments", "version", "author"),
)


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _acronym(name: str) -> str:
    match = re.search(r"\(([^)]+)\)", name)
    if match:
        return match.group(1)
    words = re.findall(r"[A-Za-z]+", name)
    return "".join(word[0] for word in words if word[0].isupper()) or name


def _encrypted(row: dict[str, Any]) -> str:
    if isinstance(row.get("encrypted"), bool):
        return "Y" if row["encrypted"] else "N"
    text = str(row.get("encryption") or "").strip().casefold()
    if not text or text.startswith(("none", "no", "plaintext", "unencrypted")):
        return "N"
    if text.startswith(("unresolved", "unknown", "tbd")):
        return "Unresolved"
    return "Y"


def _value(field: Any) -> str:
    if isinstance(field, dict) and "value" in field:
        return "" if field["value"] is None else str(field["value"])
    return "" if field is None else str(field)


def _dispositions(value: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    if isinstance(value, dict):
        status = value.get("status")
        if isinstance(status, str):
            counts[status] = counts.get(status, 0) + 1
        for child in value.values():
            for key, count in _dispositions(child).items():
                counts[key] = counts.get(key, 0) + count
    elif isinstance(value, list):
        for child in value:
            for key, count in _dispositions(child).items():
                counts[key] = counts.get(key, 0) + count
    return counts


def _contact_rows(contact: dict[str, Any]) -> list[dict[str, str]]:
    source = contact.get("resolved") or {}
    pending = contact.get("status") == "pending-authoritative-source"
    values = {
        "Name": source.get("contactName"),
        "Title": source.get("contactType"),
        "Organization": source.get("organization"),
        "Address": source.get("address"),
        "Phone Number": source.get("contactPhone"),
        "Email Address": source.get("contactEmail"),
    }
    return [
        {
            "label": label,
            "value": value
            or ("Pending authoritative source" if pending else "Not provided"),
        }
        for label, value in values.items()
    ]


def chapter_table_rows(system_root: Path) -> dict[str, list[dict[str, Any]]]:
    manifest = _load(system_root / "system.yaml")
    sidecar = _load(system_root / "sidecar.yaml")
    architecture = _load(system_root / "model" / "architecture.yaml")
    docx_source = _load(system_root / "docx.yaml")
    chapters = docx_source.get("chapters") or {}
    cpo = sidecar.get("cpo") or {}
    name = cpo.get("service_name") or manifest.get("name") or ""
    acronym = cpo.get("service_acronym") or _acronym(name)

    contacts = chapters.get("contacts") or {}
    source_contacts = sidecar.get("contacts") or []
    contact_rows = {}
    for name_key in ("system_owner", "authorizing_official", "issm", "isso"):
        contact = dict(contacts.get(name_key) or {})
        if contact.get("source") == "sidecar.yaml#/contacts/0" and source_contacts:
            contact["resolved"] = source_contacts[0]
        contact_rows[name_key] = _contact_rows(contact)
    designated = []
    for index, contact in enumerate(contacts.get("designated") or []):
        resolved = dict(contact)
        if (
            contact.get("source") == "sidecar.yaml#/contacts/1"
            and len(source_contacts) > 1
        ):
            resolved["resolved"] = source_contacts[1]
        designated.append(_contact_rows(resolved))

    categorization = chapters.get("categorization") or {}
    control_provision = chapters.get("control_provision") or {}
    inventories = chapters.get("inventories") or {}
    interconnections = chapters.get("interconnections") or []
    prepared_by = chapters.get("document_prepared_by") or {}
    external_services = chapters.get("external_services") or []
    result = {
        "document_prepared_by": [
            {
                "label": "Organization Name",
                "value": prepared_by.get("organization_name", ""),
            },
            {"label": "Address Line 1", "value": prepared_by.get("address_line_1", "")},
            {"label": "Address Line 2", "value": prepared_by.get("address_line_2", "")},
            {
                "label": "City, State Zip",
                "value": prepared_by.get("city_state_zip", ""),
            },
        ],
        "revision_history": [
            {key: row.get(key, "") for key in REVISION_HISTORY.columns}
            for row in chapters.get("revision_history") or []
        ],
        "system_identity": [
            {"label": "Information System Name:", "value": name},
            {"label": "Information System Abbreviation:", "value": acronym},
        ],
        "system_assets": [
            {"asset_type": alias, "description": description}
            for alias, description in (
                architecture.get("asset_descriptions") or {}
            ).items()
        ],
        "user_roles": [
            {
                **role,
                # The official template's data row merges this logical grid
                # position with Authorized Privileges. python-docx exposes
                # both indexes as the same physical cell.
                "sensitivity_detail": role.get("authorized_privileges", ""),
            }
            for role in architecture.get("user_roles") or []
        ],
        "ports_protocols_services": [
            {
                "direction": row.get("direction", "Outbound"),
                "boundary_crossing": row.get("boundary_crossing", "Y"),
                "source": row.get("source", ""),
                "destination": row.get("destination", ""),
                "ports": f"{row.get('port_number', '')} ({str(row.get('transport_protocol', 'T'))[:1]})",
                "protocols": row.get("transport_protocol", ""),
                "services": row.get("service_name", ""),
                "purpose": row.get("purpose", ""),
                "encrypted": _encrypted(row),
                "data_sensitivity": row.get("data_sensitivity", ""),
            }
            for row in (sidecar.get("sdr") or {}).get("ports_and_protocols") or []
        ],
        "information_types": [
            {key: _value(value) for key, value in row.items() if key != "status"}
            for row in categorization.get("information_types") or []
        ],
        "security_objective_impacts": [
            {key: _value(value) for key, value in row.items() if key != "status"}
            for row in categorization.get("impacts") or []
        ],
        "assurance_levels": [
            {key: _value(value) for key, value in row.items() if key != "status"}
            for row in categorization.get("assurance_levels") or []
        ],
        "providing_systems": [
            {key: _value(value) for key, value in row.items() if key != "status"}
            for row in control_provision.get("providing") or []
        ],
        "receiving_systems": [
            {key: _value(value) for key, value in row.items() if key != "status"}
            for row in control_provision.get("receiving") or []
        ]
        or [
            {
                "system": "Not applicable",
                "owner": "Not applicable",
                "controls": control_provision.get(
                    "receiving_disposition", "Not applicable"
                ),
                "inheritance": "Not applicable",
            }
        ],
        "system_urls": [
            {
                "component": row.get("component", ""),
                "url": _value(row.get("url")),
                "exposure": row.get("exposure", ""),
                "mfa": row.get("mfa", ""),
            }
            for row in chapters.get("urls") or []
        ],
        "physical_virtual_inventory": inventories.get("physical_virtual") or [],
        "software_inventory": inventories.get("software") or [],
        "aws_services": inventories.get("aws_services") or [],
        "interconnection_summary": [
            {key: row.get(key, "") for key in INTERCONNECTIONS.columns}
            for row in interconnections
        ],
        "interconnection_details": [
            {key: row.get(key, "") for key in INTERCONNECTION_DETAILS.columns}
            for row in interconnections
        ],
        "external_services": [
            [
                {"label": "System Name", "value": row.get("system_name", "")},
                {"label": "Connection Type", "value": row.get("connection_type", "")},
                {"label": "Data Description", "value": row.get("data_description", "")},
                {"label": "Data Sensitivity", "value": row.get("data_sensitivity", "")},
                {
                    "label": "Level of Vendor Dependency",
                    "value": row.get("vendor_dependency", ""),
                },
                {
                    "label": "Alternative Exists",
                    "value": row.get("alternative_exists", ""),
                },
                {"label": "Is API over HTTPS?", "value": row.get("api_over_https", "")},
                {
                    "label": "API Connection Security",
                    "value": row.get("api_connection_security", ""),
                },
                {
                    "label": "API Connection Type",
                    "value": row.get("api_connection_type", ""),
                },
                {
                    "label": "Authentication and Authorization",
                    "value": row.get("authentication_authorization", ""),
                },
                {"label": "MFA", "value": row.get("mfa", "")},
                {
                    "label": "Role-based Access Control",
                    "value": row.get("role_based_access_control", ""),
                },
                {
                    "label": "Audit Logs Available",
                    "value": row.get("audit_logs_available", ""),
                },
                {
                    "label": "Encryption in Transit",
                    "value": row.get("encryption_in_transit", ""),
                },
                {
                    "label": "Encryption in Storage",
                    "value": row.get("encryption_in_storage", ""),
                },
            ]
            for row in external_services
        ],
    }
    result.update(contact_rows)
    for index in range(2):
        result[f"designated_contact_{index + 1}"] = (
            designated[index]
            if index < len(designated)
            else _contact_rows({"status": "pending-authoritative-source"})
        )
    return result


def _fill_fixed_table(
    source: Path,
    *,
    name: str,
    first_cell: str,
    values: list[str],
    output: Path,
) -> dict[str, Any]:
    document = Document(source)
    matches = [
        (index, table)
        for index, table in enumerate(document.tables)
        if table.rows and table.rows[0].cells[0].text.strip() == first_cell
    ]
    if len(matches) != 1:
        raise ValueError(f"{name}: expected one fixed table, found {len(matches)}")
    table_index, table = matches[0]
    if len(values) != len(table.rows):
        raise ValueError(
            f"{name}: expected {len(table.rows)} values, got {len(values)}"
        )
    for row, value in zip(table.rows, values, strict=True):
        row.cells[-1].text = value
    document.save(output)
    observed = [
        row.cells[-1].text.strip() for row in Document(output).tables[table_index].rows
    ]
    return {
        "output_path": str(output),
        "table_name": name,
        "table_index": table_index,
        "input_row_count": len(values),
        "rows_written": len(values),
        "rows_removed": 0,
        "readback_rows": observed,
        "mismatches": []
        if observed == values
        else [{"expected": values, "observed": observed}],
    }


def _fill_external_service(
    source: Path, rows: list[dict[str, Any]], output: Path
) -> dict[str, Any]:
    document = Document(source)
    matches = [
        (index, table)
        for index, table in enumerate(document.tables)
        if table.rows
        and tuple(cell.text.strip() for cell in table.rows[0].cells)
        == ("Service Element", "Response")
    ]
    if len(matches) != 1:
        raise ValueError(
            f"external_services: expected one service table, found {len(matches)}"
        )
    table_index, table = matches[0]
    if len(rows) != len(table.rows) - 1:
        raise ValueError(
            f"external_services: expected {len(table.rows) - 1} values, got {len(rows)}"
        )
    expected_labels = [row.cells[0].text.strip() for row in table.rows[1:]]
    provided_labels = [row["label"] for row in rows]
    if provided_labels != expected_labels:
        raise ValueError(
            "external_services: YAML labels do not match template service elements"
        )
    for table_row, source_row in zip(table.rows[1:], rows, strict=True):
        table_row.cells[-1].text = str(source_row["value"])
    document.save(output)
    observed = [
        {"label": row.cells[0].text.strip(), "value": row.cells[-1].text.strip()}
        for row in Document(output).tables[table_index].rows[1:]
    ]
    return {
        "output_path": str(output),
        "table_name": "external_services",
        "table_index": table_index,
        "input_row_count": len(rows),
        "rows_written": len(rows),
        "rows_removed": 0,
        "readback_rows": observed,
        "mismatches": []
        if observed == rows
        else [{"expected": rows, "observed": observed}],
    }


def _fill_operational_status(
    source: Path, selection: str, output: Path
) -> dict[str, Any]:
    document = Document(source)
    matches = [
        (index, table)
        for index, table in enumerate(document.tables)
        if table.rows
        and table.rows[0].cells[0].text.strip() == "System Operational Status"
    ]
    if len(matches) != 1:
        raise ValueError(
            f"operational_status: expected one table, found {len(matches)}"
        )
    table_index, table = matches[0]
    for row in table.rows[1:]:
        label = row.cells[0].text.strip().lstrip("☐☒").strip()
        glyph = "☒" if label.casefold() == selection.casefold() else "☐"
        texts = row.cells[0]._tc.findall(f".//{qn('w:t')}")
        if texts:
            texts[0].text = glyph
    document.save(output)
    observed = [
        row.cells[0].text.strip()
        for row in Document(output).tables[table_index].rows[1:]
    ]
    options = ["Operational", "Under Development", "Major Modification", "Other"]
    selected_indexes = [
        index for index, row in enumerate(observed) if row.startswith("☒")
    ]
    matched = (
        selected_indexes == [options.index(selection)]
        if selection in options
        else False
    )
    return {
        "output_path": str(output),
        "table_name": "operational_status",
        "table_index": table_index,
        "input_row_count": 1,
        "rows_written": 1,
        "rows_removed": 0,
        "readback_rows": observed,
        "mismatches": []
        if matched
        else [{"expected": selection, "observed": observed}],
    }


def _fill_section_narrative(
    source: Path,
    *,
    anchor: str,
    before_heading: str,
    value: str,
    output: Path,
) -> dict[str, Any]:
    document = Document(source)
    bookmark_name = f"ttse_{anchor}"
    matches = [
        paragraph
        for paragraph in document.paragraphs
        if paragraph._p.xpath(f'.//w:bookmarkStart[@w:name="{bookmark_name}"]')
    ]
    if matches:
        if len(matches) != 1:
            raise ValueError(f"{anchor}: duplicate paragraph bookmarks")
        paragraph = matches[0]
        paragraph.text = value
    else:
        headings = [
            paragraph
            for paragraph in document.paragraphs
            if paragraph.text.strip() == before_heading
        ]
        if len(headings) != 1:
            raise ValueError(
                f"{anchor}: expected one heading {before_heading!r}, found {len(headings)}"
            )
        paragraph = headings[0].insert_paragraph_before(value)
    bookmark_id = str(int(hashlib.sha256(bookmark_name.encode()).hexdigest()[:7], 16))
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), bookmark_id)
    start.set(qn("w:name"), bookmark_name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), bookmark_id)
    paragraph._p.insert(0, start)
    paragraph._p.append(end)
    document.save(output)
    reloaded = Document(output)
    observed = next(
        (
            item.text
            for item in reloaded.paragraphs
            if item._p.xpath(f'.//w:bookmarkStart[@w:name="{bookmark_name}"]')
        ),
        "",
    )
    return {
        "anchor": anchor,
        "expected": value,
        "observed": observed,
        "matched": observed == value,
    }


def _fill_identity(
    source: Path, rows: list[dict[str, Any]], output: Path
) -> dict[str, Any]:
    document = Document(source)
    labels = tuple(row["label"] for row in rows)
    matches = [
        (index, table)
        for index, table in enumerate(document.tables)
        if tuple(row.cells[0].text.strip() for row in table.rows) == labels
    ]
    if len(matches) != 1:
        raise ValueError(
            f"system_identity: expected one labeled table, found {len(matches)}"
        )
    table_index, table = matches[0]
    for index, row in enumerate(rows):
        table.rows[index].cells[-1].text = str(row["value"])
    document.save(output)
    observed = [
        {
            "label": row.cells[0].text.strip(),
            "value": row.cells[-1].text.strip(),
        }
        for row in Document(output).tables[table_index].rows
    ]
    return {
        "output_path": str(output),
        "table_name": "system_identity",
        "table_index": table_index,
        "input_row_count": len(rows),
        "rows_written": len(rows),
        "rows_removed": 0,
        "readback_rows": observed,
        "mismatches": []
        if observed == rows
        else [{"expected": rows, "observed": observed}],
    }


def _fill_labeled_table(
    source: Path,
    *,
    name: str,
    rows: list[dict[str, Any]],
    output: Path,
    occurrence: int,
) -> dict[str, Any]:
    document = Document(source)
    labels = tuple(row["label"] for row in rows)
    matches = [
        (index, table)
        for index, table in enumerate(document.tables)
        if tuple(row.cells[0].text.strip() for row in table.rows) == labels
    ]
    if occurrence >= len(matches):
        raise ValueError(
            f"{name}: expected labeled-table occurrence {occurrence}, found {len(matches)}"
        )
    table_index, table = matches[occurrence]
    for index, row in enumerate(rows):
        table.rows[index].cells[-1].text = str(row["value"])
    document.save(output)
    observed = [
        {
            "label": row.cells[0].text.strip(),
            "value": row.cells[-1].text.strip(),
        }
        for row in Document(output).tables[table_index].rows
    ]
    return {
        "output_path": str(output),
        "table_name": name,
        "table_index": table_index,
        "input_row_count": len(rows),
        "rows_written": len(rows),
        "rows_removed": 0,
        "readback_rows": observed,
        "mismatches": []
        if observed == rows
        else [{"expected": rows, "observed": observed}],
    }


def fill_chapter_tables(
    source: Path, system_root: Path, output: Path
) -> dict[str, Any]:
    rows_by_name = chapter_table_rows(system_root)
    results = [
        _fill_labeled_table(
            source,
            name="document_prepared_by",
            rows=rows_by_name["document_prepared_by"],
            output=output,
            occurrence=0,
        )
    ]
    current_source = output
    result = fill_named_table(
        current_source,
        REVISION_HISTORY,
        rows_by_name["revision_history"],
        output,
    )
    results.append({**asdict(result), "output_path": str(result.output_path)})
    current_source = output
    results.append(
        _fill_identity(current_source, rows_by_name["system_identity"], output)
    )
    current_source = output
    docx_source = _load(system_root / "docx.yaml")
    chapters = docx_source.get("chapters") or {}
    system_environment = (chapters.get("narratives") or {}).get("system_environment")
    if system_environment:
        narrative_result = _fill_section_narrative(
            current_source,
            anchor="system_environment",
            before_heading="Asset Inventory",
            value=system_environment,
            output=output,
        )
        current_source = output
        results.append(
            {
                "output_path": str(output),
                "table_name": "system_environment_narrative",
                "table_index": -1,
                "input_row_count": 1,
                "rows_written": 1,
                "rows_removed": 0,
                "readback_rows": [narrative_result["observed"]],
                "mismatches": [] if narrative_result["matched"] else [narrative_result],
            }
        )
    locations = chapters.get("locations") or {}
    result = _fill_fixed_table(
        current_source,
        name="system_locations",
        first_cell="Primary",
        values=[
            "Primary",
            _value(locations.get("primary")),
            "Secondary (if applicable)",
            _value(locations.get("secondary")),
            "",
        ],
        output=output,
    )
    results.append(result)
    current_source = output
    result = _fill_operational_status(
        current_source,
        _value((chapters.get("operational_status") or {}).get("value")),
        output,
    )
    results.append(result)
    current_source = output
    for name, occurrence in (
        ("system_owner", 0),
        ("authorizing_official", 1),
        ("issm", 2),
        ("isso", 3),
        ("designated_contact_1", 4),
        ("designated_contact_2", 5),
    ):
        result = _fill_labeled_table(
            current_source,
            name=name,
            rows=rows_by_name[name],
            output=output,
            occurrence=occurrence,
        )
        current_source = output
        results.append(result)
    for spec in (
        INFORMATION_TYPES,
        IMPACTS,
        ASSURANCE,
        PROVIDING,
        RECEIVING,
        ASSETS,
        URLS,
        ROLES,
        INVENTORY,
        SOFTWARE,
        PORTS,
        AWS_SERVICES,
        INTERCONNECTIONS,
        INTERCONNECTION_DETAILS,
    ):
        result = fill_named_table(current_source, spec, rows_by_name[spec.name], output)
        current_source = output
        serialized = asdict(result)
        serialized["output_path"] = str(serialized["output_path"])
        results.append(serialized)
    for rows in rows_by_name["external_services"]:
        result = _fill_external_service(current_source, rows, output)
        current_source = output
        results.append(result)
    return {
        "tables": results,
        "source_rows": sum(result["input_row_count"] for result in results),
        "written_rows": sum(result["rows_written"] for result in results),
        "mismatches": sum(len(result["mismatches"]) for result in results),
        "dispositions": _dispositions(docx_source),
    }
