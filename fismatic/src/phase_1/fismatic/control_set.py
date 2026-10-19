from collections import Counter


class ControlSet:
    def __init__(self, controls, source=""):
        self._controls = controls
        self._source = source

    @property
    def source(self):
        """Where the control set came from. This will generally be the file path."""
        return self._source

    def get_implementations_by_id(self):
        """The ID (key) is the control name + part."""
        result = {}

        for control in self._controls:
            for part, imp in control.implementation.items():
                key = ": ".join([control.name, part])
                result[key] = imp

        return result

    def get_control(self, name):
        for control in self._controls:
            if control.name == name:
                return control
        return None

    def get_implementation_for(self, control_name, part):
        control = self.get_control(control_name)
        if not control:
            return None
        return control.implementation.get(part)

    def get_implementations(self):
        """Returns a list of implementation strings."""
        return list(self.get_implementations_by_id().values())

    def num_controls(self):
        return len(self._controls)

    def num_implementations(self):
        return len(self.get_implementations())

    def num_unique_implementations(self):
        implementations = self.get_implementations()
        return len(set(implementations))

    def count_implementations(self):
        """Returns a Counter of the implementation strings."""
        implementations = self.get_implementations()
        return Counter(implementations)

    def get_controls_by_family(self):
        """Returns a dict of controls grouped by family."""
        result = {}
        for control in self._controls:
            family = control.family or "unknown"
            if family not in result:
                result[family] = []
            result[family].append(control)
        return result

    def get_families(self):
        """Returns a list of unique control families."""
        families = set()
        for control in self._controls:
            if control.family:
                families.add(control.family)
        return sorted(list(families))

    def get_controls_for_family(self, family):
        """Returns a list of controls for a given family."""
        return [control for control in self._controls if control.family == family]

    def get_control_names(self):
        """Returns a list of control names."""
        return [control.name for control in self._controls]

    def __str__(self):
        return f"ControlSet(source={self._source}, controls={len(self._controls)})"
