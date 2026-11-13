import re

from docx import Document

from .control import Control
from .control_set import ControlSet
from .parser import (
    extract_implementation_details,
    is_continuation_table,
    parse_control_table,
    load_contract_config,
)


class DocxParser:
    def __init__(self, docx_path, contract_path=None):
        """Initialize DocxParser with path to docx file"""
        self.docx_path = docx_path
        self.contract_path = contract_path or self.resolve_contract_path(docx_path)
        self.config = load_contract_config(self.contract_path)
        self.document = Document(docx_path)
        self.tables = self.document.tables

    def resolve_contract_path(self, docx_path: str) -> str:
        from pathlib import Path

        # Per-project contract override: if a repo ships a per-project contract
        # (compliance/datacontracts/<plan-id>.yaml) it is used; otherwise the
        # shared base contract (fedramp-ssp-v1.yaml) applies. The per-project
        # files were historically byte-identical clones of the base and have
        # been removed (issue #9) -- so this now resolves to the base for every
        # known plan unless someone intentionally adds a divergent override.
        name = Path(docx_path).name.lower()
        base = "compliance/datacontracts/fedramp-ssp-v1.yaml"
        if "analytics" in name:
            pid = "analytics-usa-gov-ssp"
        elif "challenge" in name:
            pid = "challenge-gov-ssp"
        elif "api.data.gov" in name or "api-data" in name:
            pid = "api-data-gov-ssp"
        elif "data.gov" in name:
            pid = "data-gov-ssp"
        elif "usa.gov" in name:
            pid = "usa-gov-ssp"
        elif "gsa-pages" in name:
            pid = "gsa-pages-ssp-2024"
        elif "working copy of ssb" in name or "ssb sspp" in name or "site-scanning" in name:
            pid = "site-scanning-ssp"
        elif "site scanner" in name or "site-scanner" in name:
            pid = "site-scanner-ssp"
        elif "tock" in name:
            pid = "tock-ssp"
        elif "zendesk" in name:
            pid = "zendesk-ssp"
        else:
            return base

        for parent in Path(__file__).resolve().parents:
            dc_dir = parent / "compliance" / "datacontracts"
            if dc_dir.is_dir():
                override = dc_dir / f"{pid}.yaml"
                if override.exists():
                    return str(override)
                # No per-project override -> shared base.
                base_path = dc_dir / "fedramp-ssp-v1.yaml"
                if base_path.exists():
                    return str(base_path)
        return base

    def is_control_table(self, table):
        """Check if a table is a control implementation table"""
        if not table or len(table.rows) < 2:  # Need at least header and one data row
            return False

        if is_continuation_table(table):
            return False

        try:
            # Get cell text from the header row and up to the first 9 data rows
            # (Rev 4 / early Rev 5 CIS tables place Responsible Role and Parameter
            # rows before Implementation Status / Control Origination).
            header_cells = [cell.text.strip().lower() for cell in table.rows[0].cells]
            data_cells = [
                cell.text.strip().lower()
                for row in table.rows[1 : min(10, len(table.rows))]
                for cell in row.cells
            ]

            # Join all text for pattern matching
            header_text = " ".join(header_cells)
            data_text = " ".join(data_cells)

            print("\nAnalyzing table:")
            print(f"Header: {header_text}")
            print(f"Data: {data_text[:200]}...")  # Truncate long data

            # Common variations of control table headers
            control_headers = {
                "control": [
                    "control",
                    "control id",
                    "control number",
                    "control identifier",
                    "control name",
                    "requirement",
                    "control item",
                    "security control",
                    "control family",
                    "security requirement",
                    "security control id",
                ],
                "status": [
                    "status",
                    "implementation status",
                    "imp status",
                    "implementation state",
                    "state",
                    "control status",
                    "security status",
                    "current status",
                    "compliance status",
                    "assessment status",
                    "responsibility summary",
                    "responsibility type",
                    "customer responsibility",
                    "provider responsibility",
                    "shared responsibility",
                    "responsibility",
                    "tenant responsibility",
                ],
                "origination": [
                    "origination",
                    "control origination",
                    "responsibility",
                    "responsible",
                    "provider",
                    "implemented by",
                    "implementation responsibility",
                    "control provider",
                    "control implementation by",
                    "implemented",
                    "implementation by",
                    "responsibility for implementation",
                    "leveraged saas",
                    "saas vendor",
                    "cloud provider",
                    "service provider",
                    "tenant",
                    "customer",
                    "shared",
                ],
                "description": [
                    "description",
                    "implementation",
                    "control implementation",
                    "statement",
                    "narrative",
                    "response",
                    "control description",
                    "security requirements",
                    "what is the solution",
                    "implementation details",
                    "control statement",
                    "implementation narrative",
                    "how implemented",
                    "guidance",
                    "customer guidance",
                    "customer action",
                    "required action",
                    "customer must",
                    "tenant must",
                    "responsibility summary information",
                    "saas provider",
                ],
            }

            # Count header matches
            matches = 0
            header_matches = []
            for header_type, variations in control_headers.items():
                for variation in variations:
                    if variation in header_text:
                        matches += 1
                        header_matches.append(variation)
                        break

            print(f"Header matches found: {header_matches}")

            # Additional validation checks

            # 1. Check for control ID patterns in first row data
            control_id_patterns = [
                r"(?:^|[^A-Za-z])([A-Za-z]{2})-(\d+(?:\.\d+)?)",  # Standard format (AC-1)
                r"(?:^|[^A-Za-z])([A-Za-z]{2})\s+(\d+(?:\.\d+)?)",  # Space separated (AC 1)
                r"(?:^|[^A-Za-z])([A-Za-z]{2})\.(\d+(?:\.\d+)?)",  # Dot separated (AC.1)
                r"control\s+([A-Za-z]{2})-(\d+(?:\.\d+)?)",  # Prefixed with "Control"
                r"([A-Za-z]{2})-(\d+(?:\.\d+)?)\s*\([a-z]\)",  # With part (AC-1(a))
                r"([A-Za-z]{2})-(\d+(?:\.\d+)?)\s*\(\d+\)",  # With enhancement (AC-1(1))
                r"control\s*family\s*([A-Za-z]{2})",  # Control family reference
                r"security\s*control\s*([A-Za-z]{2})-(\d+)",  # Security control prefix
            ]

            control_id_found = False
            for pattern in control_id_patterns:
                for cell in data_cells:
                    match = re.search(pattern, cell, re.IGNORECASE)
                    if match:
                        control_id_found = True
                        print(f"Found control ID pattern: {match.group()}")
                        break
                if control_id_found:
                    matches += 1
                    break

            # 2. Check for implementation status keywords in data
            status_keywords = [
                "implemented",
                "partially",
                "planned",
                "alternative",
                "not applicable",
                "n/a",
                "compensating",
                "inherited",
                "not implemented",
                "in place",
                "in process",
                "ongoing",
                "complete",
                "completed",
                "partial",
                "pending",
                "future",
                "alternative implementation",
                "compensating control",
                "customer responsibility",
                "provider responsibility",
                "shared",
                "leveraged",
                "tenant responsibility",
                "saas vendor responsibility",
            ]
            status_found = False  # noqa: F841 — kept for readability, value unused post-break
            for keyword in status_keywords:
                if keyword in data_text:
                    status_found = True  # noqa: F841
                    print(f"Found status keyword: {keyword}")
                    matches += 1
                    break

            # 3. Check for common control description indicators
            description_indicators = [
                "part",
                "the organization",
                "the system",
                "customer responsibility",
                "provider responsibility",
                "control implementation",
                "how is this implemented",
                "implementation details",
                "control objective",
                "security requirement",
                "control requirement",
                "implementation guidance",
                "supplemental guidance",
                "customer must",
                "tenant must",
                "the customer",
                "the tenant",
                "saas vendor",
                "leveraged saas",
                "cloud service provider",
                "responsibility summary",
                "required actions",
            ]
            desc_found = False  # noqa: F841 — kept for readability, value unused post-break
            for indicator in description_indicators:
                if indicator in data_text:
                    desc_found = True  # noqa: F841
                    print(f"Found description indicator: {indicator}")
                    matches += 1
                    break

            print(f"Total matches: {matches}")

            # Consider it a control table if we have enough evidence
            # Either 2+ header matches or 1 header match + 2 supporting data evidence
            is_control = matches >= 2
            print(f"Is control table: {is_control}\n")
            return is_control

        except Exception as e:
            print(f"Warning: Error checking control table: {str(e)}")
            return False

    def get_controls(self):
        """Extract controls from the document"""
        controls = {}
        current_table = None
        current_control = None

        try:
            # Process each table in the document
            for table_index, table in enumerate(self.tables):
                try:
                    # Check if this is a control table
                    if self.is_control_table(table):
                        # Parse the control table
                        control_data = parse_control_table(table, config=self.config)
                        if control_data and "control_id" in control_data:
                            control_id = control_data["control_id"]

                            # Create new Control object
                            control = Control(
                                name=control_id,
                                responsible_role=control_data.get("responsible_role"),
                                imp_status=control_data.get("implementation_status"),
                                origination=control_data.get("control_origination"),
                            )

                            # Extract and set implementation details
                            impl_details = extract_implementation_details(control_data, config=self.config)
                            if impl_details:
                                if "implementation" in impl_details:
                                    control.implementation = impl_details[
                                        "implementation"
                                    ]
                                if "implementation_status" in impl_details:
                                    control.implementation_status = impl_details[
                                        "implementation_status"
                                    ]
                                if "control_origination" in impl_details:
                                    control.origination = impl_details[
                                        "control_origination"
                                    ]

                            controls[control_id] = control
                            current_control = control
                            current_table = table
                        else:
                            print(
                                f"Warning: Could not parse control table at index {table_index}"
                            )

                    # If this is a continuation of the previous control
                    elif current_control and current_table:
                        try:
                            # Extract additional implementation details
                            additional_details = extract_implementation_details(
                                {"table": table}, config=self.config
                            )
                            if (
                                additional_details
                                and "implementation" in additional_details
                            ):
                                # Merge the implementation details
                                current_implementation = (
                                    current_control.implementation or {}
                                )
                                current_implementation.update(
                                    additional_details["implementation"]
                                )
                                current_control.implementation = current_implementation
                            else:
                                # Non-control table with no Part/Req rows ends continuation
                                current_control = None
                                current_table = None
                        except Exception as e:
                            print(
                                f"Warning: Error processing continuation table: {str(e)}"
                            )

                except Exception as e:
                    print(f"Warning: Error processing table {table_index}: {str(e)}")
                    continue

            return controls

        except Exception as e:
            print(f"Error extracting controls: {str(e)}")
            return {}

    def get_control_set(self):
        """Get a ControlSet object containing all controls"""
        try:
            controls = list(self.get_controls().values())
            return ControlSet(controls, source=self.docx_path)
        except Exception as e:
            print(f"Error creating control set: {str(e)}")
            return ControlSet([], source=self.docx_path)
