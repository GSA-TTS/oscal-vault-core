"""Library-owned control-table rendering previously hosted by a CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from compliance.lib.control_table_anchors import find_control_tables
from compliance.lib.control_table_fill import fill_control_tables
from compliance.lib.oscal_source import (
    load_from_oscal_ssp,
    origination_provider_map_from_oscal_ssp,
    statement_text_map_from_oscal_ssp,
    whole_control_text_map_from_oscal_ssp,
)


def build_from_oscal(
    oscal_path: Path,
    template_path: Path,
    output_path: Path,
    manifest_path: Path,
    *,
    strict_mapping: bool = False,
) -> dict[str, Any]:
    oscal = json.loads(oscal_path.read_text(encoding="utf-8"))
    controls = load_from_oscal_ssp(oscal, strict=strict_mapping)
    statements = statement_text_map_from_oscal_ssp(oscal)
    result = fill_control_tables(
        template_path,
        controls,
        output_path,
        statement_text_by_control=statements,
        whole_control_text_by_control=whole_control_text_map_from_oscal_ssp(oscal),
        origination_providers_by_control=origination_provider_map_from_oscal_ssp(oscal),
    )
    manifest = {
        "purpose": "Manual review only; not an authorization or submission package.",
        "source_oscal": oscal_path.name,
        "target_template": template_path.name,
        "source_controls": len(controls),
        "source_statement_controls": len(statements),
        "target_control_tables": len(find_control_tables(template_path)),
        "matching_control_ids_filled": len(result.filled_control_ids),
        "matching_physical_tables_filled": result.filled_table_count,
        "source_controls_without_target_table": sorted(result.unmatched_control_ids),
        "target_controls_left_unfilled": sorted(result.unmatched_table_ids),
        "duplicate_target_control_ids": result.duplicate_table_ids,
        "source_id_collisions": result.source_id_collisions,
        "controls": result.control_results,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
