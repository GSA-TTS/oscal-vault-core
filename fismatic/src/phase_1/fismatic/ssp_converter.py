import datetime
import json
import uuid
from typing import Any, Dict, Optional

import yaml

from .docx_parser import DocxParser
from .parser import generate_statement_id, validate_control_data


class SSPConverter:
    def __init__(self, docx_path: str, contract_path: Optional[str] = None) -> None:
        self.docx_path: str = docx_path
        self.contract_path = contract_path
        self.parser = DocxParser(docx_path, contract_path=contract_path)
        self.oscal_data: Optional[Dict[str, Any]] = None
        self.controls: Optional[Dict[str, Any]] = None
        self._parse()

    def _parse(self):
        """Parse the docx file and store OSCAL data"""
        try:
            self.controls = self.parser.get_controls()
        except Exception as e:
            print(f"Warning: Error getting controls: {str(e)}")
            self.controls = {}
        self.oscal_data = self.convert_to_oscal()

    def get_oscal_data(self):
        """Get the parsed OSCAL data"""
        if self.oscal_data is None:
            self._parse()
        return self.oscal_data

    def convert_to_oscal(self):
        """Convert the SSP to OSCAL format"""
        try:
            # Generate UUIDs for the document
            doc_uuid = str(uuid.uuid4())

            # Build basic OSCAL structure
            oscal: Dict[str, Any] = {
                "system-security-plan": {
                    "uuid": doc_uuid,
                    "metadata": {
                        "title": "FedRAMP System Security Plan",
                        "last-modified": datetime.datetime.now().isoformat(),
                        "version": "1.0",
                        "oscal-version": "1.0.0",
                        "parties": [
                            {
                                "uuid": str(uuid.uuid4()),
                                "type": "organization",
                                "name": "Cloud Service Provider",
                                "role-ids": ["cloud-service-provider"],
                            }
                        ],
                        "roles": [
                            {
                                "id": "cloud-service-provider",
                                "title": "Cloud Service Provider",
                                "description": "Cloud Service Provider implementing the controls",
                            }
                        ],
                    },
                    "import-profile": {
                        "href": "https://raw.githubusercontent.com/GSA/fedramp-automation/master/baselines/rev5/xml/FedRAMP_rev5_HIGH-baseline-resolved-profile_catalog.xml"
                    },
                    "system-characteristics": {
                        "system-ids": [
                            {
                                "id": str(uuid.uuid4()),
                                "identifier-type": "https://fedramp.gov",
                            }
                        ],
                        "system-name": "System Name",
                        "description": "System Description",
                        "security-sensitivity-level": "moderate",
                        "system-information": {"information-types": []},
                        "security-impact-level": {
                            "security-objective-confidentiality": "moderate",
                            "security-objective-integrity": "moderate",
                            "security-objective-availability": "moderate",
                        },
                        "status": {"state": "operational"},
                        "authorization-boundary": {
                            "description": "The authorization boundary encompasses all components and services within the system."
                        },
                    },
                    "control-implementation": {
                        "description": "Implementation of FedRAMP Moderate Baseline Security Controls",
                        "implemented-requirements": [],
                    },
                }
            }

            # Add controls
            if self.controls:
                for control_id, control in self.controls.items():
                    if not control:  # Skip empty controls
                        continue

                    try:
                        # Validate control data (control is a fismatic.control.Control instance)
                        is_valid, errors = validate_control_data(control)
                        if not is_valid:
                            print(
                                f"Warning: Invalid control data for {control_id}: {', '.join(errors)}"
                            )
                            continue

                        # Convert control to OSCAL format
                        control_data: Dict[str, Any] = {
                            "uuid": str(uuid.uuid4()),
                            "control-id": control_id,
                            "implementation-status": control.implementation_status
                            or {"state": "planned"},
                            "control-origination": control.origination or [],
                            "statements": [],
                        }

                        if getattr(control, "responsible_role", None):
                            roles = control.responsible_role
                            if not isinstance(roles, list):
                                roles = [roles]
                            control_data["responsible-roles"] = [
                                {"role-id": role} for role in roles if role
                            ]

                        # Add a statement per implementation part
                        for part, description in sorted(
                            (control.implementation or {}).items()
                        ):
                            if not description:
                                continue
                            control_data["statements"].append(
                                {
                                    "statement-id": generate_statement_id(
                                        control_id, part=part
                                    ),
                                    "uuid": str(uuid.uuid4()),
                                    "description": description,
                                }
                            )

                        oscal["system-security-plan"]["control-implementation"][
                            "implemented-requirements"
                        ].append(control_data)

                    except Exception as e:
                        print(
                            f"Warning: Error processing control {control_id}: {str(e)}"
                        )
                        continue

            return oscal

        except Exception as e:
            print(f"Error converting to OSCAL: {str(e)}")
            return None

    def save_oscal_yaml(self, output_path):
        """Save the OSCAL data as YAML"""
        if self.oscal_data is None:
            self._parse()

        with open(output_path, "w") as f:
            yaml.dump(self.oscal_data, f, default_flow_style=False)

    def save_oscal_json(self, output_path):
        """Save the OSCAL output to a JSON file"""
        try:
            oscal = self.get_oscal_data()
            if oscal:
                with open(output_path, "w") as f:
                    json.dump(oscal, f, indent=2)
                return True
            return False
        except Exception as e:
            print(f"Error saving JSON: {str(e)}")
            return False
