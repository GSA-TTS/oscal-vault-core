import re


class Control:
    def __init__(self, name, responsible_role=None, imp_status=None, origination=None):
        self.name = name
        self.responsible_role = responsible_role if responsible_role else []
        self._implementation_status = None
        self._origination = None
        self._implementation = {}

        # Parse the control number and part (allow optional space before parenthesis)
        parsed = re.match(r"([A-Z]{2})-(\d+)\s*(?:\(([\d]+|[a-zA-Z]+)\))?", name)
        if parsed:
            self.family = parsed.group(1)
            self.number = parsed.group(2)
            self.part = parsed.group(3)
        else:
            self.family = None
            self.number = None
            self.part = None

        # Set initial values if provided
        if imp_status:
            self.implementation_status = imp_status
        if origination:
            self.origination = origination

    @property
    def implementation_status(self):
        return self._implementation_status

    @implementation_status.setter
    def implementation_status(self, value):
        if value is None:
            self._implementation_status = None
        elif isinstance(value, dict) and "state" in value:
            self._implementation_status = value
        elif isinstance(value, str):
            self._implementation_status = {"state": value}
        else:
            print(f"Warning: Invalid implementation status format: {value}")
            self._implementation_status = None

    @property
    def origination(self):
        return self._origination

    @origination.setter
    def origination(self, value):
        if value is None:
            self._origination = None
        elif isinstance(value, list):
            self._origination = value
        elif isinstance(value, str):
            self._origination = [value]
        elif isinstance(value, dict):
            # Convert dict format to list format
            originations = []
            mapping = {
                "service-provider-corporate": ["corporate", "organization-wide"],
                "service-provider-system-specific": [
                    "system specific",
                    "system-specific",
                ],
                "service-provider-hybrid": ["hybrid"],
                "configured-by-customer": [
                    "configured by customer",
                    "customer configured",
                ],
                "provided-by-customer": ["provided by customer", "customer provided"],
                "shared": ["shared"],
                "inherited": ["inherited", "common control"],
            }
            for oscal_type, variations in mapping.items():
                for variation in variations:
                    if variation in str(value).lower():
                        originations.append(oscal_type)
                        break
            self._origination = originations if originations else None
        else:
            print(f"Warning: Invalid control origination format: {value}")
            self._origination = None

    @property
    def implementation(self):
        return self._implementation

    @implementation.setter
    def implementation(self, value):
        if value is None:
            self._implementation = {}
        else:
            try:
                # Store implementation parts as cleaned strings
                self._implementation = {}
                for part, imp in value.items():
                    if imp:  # Only process non-empty implementations
                        try:
                            # Convert to string and clean up whitespace
                            imp_text = str(imp).strip()
                            # Remove control references
                            control_pattern = r"(?:AC|AT|AU|CA|CM|CP|IA|IR|MA|MP|PE|PL|PS|RA|SA|SC|SI)-\d+(?:\(\d+\))?"
                            imp_text = re.sub(
                                r"(?i)control\s+" + control_pattern, "", imp_text
                            )
                            imp_text = re.sub(
                                r"(?i)see\s+" + control_pattern, "", imp_text
                            )
                            # Clean up whitespace
                            imp_text = re.sub(r"\s+", " ", imp_text).strip()
                            if imp_text:
                                self._implementation[part] = imp_text
                        except Exception as e:
                            print(
                                f"Warning: Could not process implementation text for part {part}: {str(e)}"
                            )
            except Exception as e:
                print(f"Warning: Could not process implementation dictionary: {str(e)}")
                self._implementation = {}

    def normalized_name(self):
        """Return the normalized control name (e.g., 'AC-1' or 'AC-2 (1)')."""
        if self.family and self.number:
            base = f"{self.family}-{self.number}"
            return f"{base} ({self.part})" if self.part else base
        return self.name

    def __str__(self):
        return f"Control({self.name}, {self.responsible_role}, {self.implementation_status}, {self.origination})"
