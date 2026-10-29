#!/usr/bin/env python3
"""Validate Dataview query blocks in vault markdown notes.

Scans all Index.md and dashboard notes in --vault directories for Dataview
query blocks (```dataview ... ```) and validates:

1. Query syntax parses (basic structure check — FROM, WHERE, SORT, TABLE, LIST)
2. Referenced folders exist in the vault
3. Referenced frontmatter field names exist in the schema

Exit 0 if all queries are valid. Exit 1 if any query has issues.
Exit 2 on runtime errors.

Usage:
    python3 scripts/validate_dataview_queries.py [--vault <dir> ...]
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import yaml

_CORE_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE = Path.cwd()
_SCHEMA_PATH = _CORE_ROOT / "vault-schemas.json"

# Known field names from all schema $defs (extracted from the JSON schema)
# These are the fields Dataview can reference in frontmatter queries.
_KNOWN_FIELDS: Set[str] = set()
# Known vault folders (standard directory names Dataview FROM expects)
_KNOWN_FOLDERS: Set[str] = {
    "Controls",
    "Systems",
    "Components",
    "Organizations",
    "Plans",
    "Mappings",
    "governance",
    "governance/validation-rules",
    "_meta",
    "_meta/templates",
    "_source/tables",
    "_source/table-rows",
    "diagrams",
    "evidence",
    "findings",
    "inheritance",
    "boundaries",
    "decisions",
    "relationships",
    "runbooks",
    "parameters",
    "control-parts",
    "catalogs",
    "profiles",
    "assessments",
    "poam",
}

# These generated views answer conditional questions and can legitimately have
# no rows even when Controls/ is populated. Keep this policy in the validator,
# rather than writing nonstandard metadata into Obsidian's Base YAML.
_OPTIONAL_EMPTY_BASE_VIEWS = {
    ("controls.base", "Requiring attention"),
    ("controls.base", "Inherited / hybrid"),
}


class BaseFilterSyntaxError(ValueError):
    """A Base filter does not conform to the supported grammar."""


class BaseFilterUnsupportedError(ValueError):
    """A well-formed Base expression is outside the evaluator's subset."""


def _extract_known_fields(schema_path: Path) -> Set[str]:
    """Extract all property names from all $defs in the JSON schema."""
    fields: Set[str] = set()
    try:
        with open(schema_path) as f:
            schema_root = json.load(f)
    except Exception:
        return fields

    for def_name, def_schema in schema_root.get("$defs", {}).items():
        props = def_schema.get("properties", {})
        fields.update(props.keys())
    return fields


def _parse_dataview_blocks(text: str) -> List[Tuple[int, str]]:
    """Extract Dataview query blocks. Returns [(line_number, query_text), ...]."""
    blocks: List[Tuple[int, str]] = []
    pattern = re.compile(r"```dataview\s*\n(.*?)```", re.DOTALL)
    for m in pattern.finditer(text):
        line_no = text[: m.start()].count("\n") + 1
        blocks.append((line_no, m.group(1).strip()))
    return blocks


def _extract_field_refs(query: str) -> Set[str]:
    """Extract frontmatter field names from a Dataview query.

    Looks for patterns like:
      TABLE field1, field2, ...
      file.frontmatter.fieldname
      WHERE fieldname = ...
      SORT fieldname ASC
      FLATTEN fieldname
      GROUP BY fieldname
    Skips known Dataview functions, string literals, and aliases.
    """
    fields: Set[str] = set()
    flatten_aliases = {
        match.group(1)
        for match in re.finditer(r"FLATTEN\s+\S+\s+AS\s+(\w+)", query, re.IGNORECASE)
    }

    # Dataview built-in functions and keywords to skip
    _DV_FUNCTIONS = {
        "length",
        "rows",
        "contains",
        "startswith",
        "endswith",
        "date",
        "today",
        "dur",
        "now",
        "default",
        "round",
        "min",
        "max",
        "sum",
        "average",
        "filter",
        "map",
        "sort",
        "reverse",
        "slice",
        "join",
        "split",
        "replace",
        "regexreplace",
        "lower",
        "upper",
        "meta",
        "typeof",
        "nonnull",
        "any",
        "all",
        "none",
        "flat",
        "limit",
        "groupby",
        "flatten",
        "where",
        "from",
        "table",
        "list",
        "task",
    }
    _DV_KEYWORDS = {
        "ASC",
        "DESC",
        "AND",
        "OR",
        "NOT",
        "NULL",
        "TRUE",
        "FALSE",
        "AS",
        "BY",
        "IN",
        "ON",
        "WHERE",
        "FROM",
        "SORT",
        "TABLE",
        "LIST",
        "TASK",
        "FLATTEN",
        "GROUP",
        "LIMIT",
        "file",
        "this",
        # Common Dataview aliases used in generated dashboards
        "Count",
        "Status",
        "ID",
        "Type",
        "Model",
        "Name",
        "Origination",
        "System",
        "Inherited From",
    }

    # TABLE/LIST/TASK fields
    table_match = re.search(
        r"(?:TABLE|LIST|TASK)\s+(.+?)(?:\n|WHERE|FROM|SORT|GROUP|FLATTEN|LIMIT|$)",
        query,
    )
    if table_match:
        fields_str = table_match.group(1)
        for part in re.split(r",\s*", fields_str):
            # Strip AS alias
            field = part.split(" AS ")[0].strip()
            # Strip function calls like length(rows)
            field = re.sub(r"\(.*?\)", "", field)
            field = field.strip()
            if field.split(".")[0] in flatten_aliases:
                continue
            if field and field not in _DV_KEYWORDS and field not in _DV_FUNCTIONS:
                if not field.startswith('"') and not field.startswith("'"):
                    if not any(c in field for c in ('"', "'", "(", ")")):
                        fields.add(field.split(".")[-1] if "." in field else field)

    # SORT field references
    for m in re.finditer(r"SORT\s+([a-z_.]+)", query, re.IGNORECASE):
        f = m.group(1)
        if f not in _DV_KEYWORDS and f not in _DV_FUNCTIONS:
            fields.add(f.split(".")[-1] if "." in f else f)

    # FLATTEN field references
    for m in re.finditer(r"FLATTEN\s+([a-z_.]+)", query, re.IGNORECASE):
        f = m.group(1)
        if f not in _DV_KEYWORDS and f not in _DV_FUNCTIONS:
            fields.add(f.split(".")[-1] if "." in f else f)

    # GROUP BY field references
    for m in re.finditer(r"GROUP BY\s+([a-z_.]+)", query, re.IGNORECASE):
        f = m.group(1)
        if f not in _DV_KEYWORDS and f not in _DV_FUNCTIONS:
            fields.add(f.split(".")[-1] if "." in f else f)

    return fields


def _extract_folder_refs(query: str) -> Set[str]:
    """Extract folder names from FROM clauses."""
    folders: Set[str] = set()
    for m in re.finditer(r'FROM\s+"([^"]+)"', query):
        folders.add(m.group(1))
    return folders


def _validate_bases_file(
    base_path: Path, expected_vault_roots: Optional[Set[Path]] = None
) -> Tuple[int, int]:
    """Validate an Obsidian Bases .base definition against schema and records."""
    warnings = 0
    errors = 0
    try:
        data = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR {base_path}: Failed to parse YAML: {e}")
        return 0, 1

    if not isinstance(data, dict):
        print(f"ERROR {base_path}: Bases file must be a YAML mapping")
        return 0, 1

    vault_root = _base_vault_root(base_path)
    if vault_root is None:
        print(
            f"ERROR {base_path}: Base must live under <vault>/_meta/views; "
            "open the plan directory as the Obsidian vault root"
        )
        return 0, 1
    if expected_vault_roots is not None and vault_root not in expected_vault_roots:
        roots = ", ".join(map(str, sorted(expected_vault_roots)))
        print(
            f"ERROR {base_path}: Base belongs to vault root {vault_root}, not the "
            f"requested --vault root(s) {roots}. Open the plan directory containing "
            "Controls/, Components/, and _meta/."
        )
        return 0, 1
    structure_errors = _validate_base_structure(data)
    if structure_errors:
        for issue in structure_errors:
            print(f"ERROR {base_path}: Base schema: {issue}")
        return 0, len(structure_errors)
    records = _load_vault_records(vault_root)

    filters = data.get("filters")
    filter_locations = [("filters", filters)]
    filter_locations.extend(
        (f"views[{index}].filters", view.get("filters"))
        for index, view in enumerate(data["views"])
        if "filters" in view
    )
    filters_valid = True
    for location, base_filter in filter_locations:
        if base_filter is None:
            continue
        try:
            _validate_base_filter(base_filter)
        except BaseFilterSyntaxError as exc:
            print(f"ERROR {base_path}: Base filter syntax at {location}: {exc}")
            errors += 1
            filters_valid = False
        except BaseFilterUnsupportedError as exc:
            print(f"ERROR {base_path}: unsupported Base filter at {location}: {exc}")
            errors += 1
            filters_valid = False

    for folder in _base_filter_folders(filters):
        if folder not in _KNOWN_FOLDERS:
            print(f'WARN  {base_path}: inFolder("{folder}") - unknown folder')
            warnings += 1

    # Check properties
    props = data.get("properties", {})
    if isinstance(props, dict):
        for prop_name in props:
            if prop_name not in _KNOWN_FIELDS:
                print(
                    f"WARN  {base_path}: property {prop_name!r} not in known schema fields"
                )
                warnings += 1

    # Check views
    views = data["views"]
    for v_idx, view in enumerate(views):
        for prop_name in view.get("order", []):
            if prop_name not in _KNOWN_FIELDS:
                print(
                    f"WARN  {base_path}: view[{v_idx}] order property {prop_name!r} not in known schema fields"
                )
                warnings += 1
        for sort_spec in view.get("sort", []):
            prop_name = sort_spec["property"]
            if prop_name not in _KNOWN_FIELDS:
                print(
                    f"WARN  {base_path}: view[{v_idx}] sort property {prop_name!r} not in known schema fields"
                )
                warnings += 1

        if not filters_valid:
            continue
        view_name = view["name"]
        matching_records = _evaluate_base_filters(records, filters)
        matching_records = _evaluate_base_filters(matching_records, view.get("filters"))
        result_count = len(matching_records)
        print(
            f"BASE  {base_path.relative_to(vault_root)}: {view_name!r} "
            f"matched {result_count} of {len(records)} note(s)"
        )
        if result_count == 0 and not _base_view_may_be_empty(
            base_path, vault_root, filters, view_name
        ):
            print(
                f"ERROR {base_path}: view {view_name!r} matched no notes under "
                f"vault root {vault_root}. A populated-folder All view must render at "
                "least one row; only documented conditional views may be empty."
            )
            errors += 1

    return warnings, errors


def _base_vault_root(base_path: Path) -> Optional[Path]:
    """Return the vault root for a conventional ``_meta/views/*.base`` file."""
    if base_path.parent.name != "views" or base_path.parent.parent.name != "_meta":
        return None
    return base_path.parent.parent.parent


def _validate_base_structure(data: Dict[str, Any]) -> List[str]:
    """Validate the supported Obsidian Base document structure."""
    issues: List[str] = []
    allowed_top_level = {"filters", "formulas", "properties", "views"}
    unknown = set(data) - allowed_top_level
    if unknown:
        issues.append(
            f"unknown top-level key(s): {', '.join(sorted(map(str, unknown)))}"
        )

    for key in ("formulas", "properties"):
        value = data.get(key, {})
        if not isinstance(value, dict):
            issues.append(f"{key} must be a mapping")
    formulas = data.get("formulas", {})
    for name, formula in formulas.items() if isinstance(formulas, dict) else ():
        if not isinstance(name, str) or not isinstance(formula, str):
            issues.append("formula names and expressions must be strings")
            break
    properties = data.get("properties", {})
    for name, config in properties.items() if isinstance(properties, dict) else ():
        if not isinstance(name, str) or not isinstance(config, dict):
            issues.append(
                "property names must be strings and definitions must be mappings"
            )
            continue
        unknown_config = set(config) - {"displayName"}
        if unknown_config:
            issues.append(
                f"property {name!r} has unknown key(s): "
                f"{', '.join(sorted(map(str, unknown_config)))}"
            )
        if "displayName" in config and not isinstance(config["displayName"], str):
            issues.append(f"property {name!r} displayName must be a string")

    views = data.get("views")
    if not isinstance(views, list) or not views:
        issues.append("views must be a non-empty list")
        return issues
    for index, view in enumerate(views):
        prefix = f"views[{index}]"
        if not isinstance(view, dict):
            issues.append(f"{prefix} must be a mapping")
            continue
        unknown_view = set(view) - {"type", "name", "filters", "order", "sort"}
        if unknown_view:
            issues.append(
                f"{prefix} has unknown key(s): {', '.join(sorted(map(str, unknown_view)))}"
            )
        for key in ("type", "name"):
            if not isinstance(view.get(key), str) or not view[key].strip():
                issues.append(f"{prefix}.{key} must be a non-empty string")
        if isinstance(view.get("type"), str) and view["type"] not in {
            "table",
            "cards",
            "list",
            "map",
        }:
            issues.append(f"{prefix}.type is not a supported Obsidian view type")
        order = view.get("order", [])
        if not isinstance(order, list) or any(
            not isinstance(item, str) for item in order
        ):
            issues.append(f"{prefix}.order must be a list of strings")
        sort_specs = view.get("sort", [])
        if not isinstance(sort_specs, list):
            issues.append(f"{prefix}.sort must be a list")
            continue
        for sort_index, sort_spec in enumerate(sort_specs):
            sort_prefix = f"{prefix}.sort[{sort_index}]"
            if not isinstance(sort_spec, dict):
                issues.append(f"{sort_prefix} must be a mapping")
                continue
            if set(sort_spec) != {"property", "direction"}:
                issues.append(f"{sort_prefix} must contain only property and direction")
                continue
            if not isinstance(sort_spec["property"], str):
                issues.append(f"{sort_prefix}.property must be a string")
            if sort_spec["direction"] not in {"ASC", "DESC"}:
                issues.append(f"{sort_prefix}.direction must be ASC or DESC")
    return issues


def _validate_base_filter(base_filter: Any) -> None:
    """Parse the supported recursive and/or/not Base filter grammar."""
    if isinstance(base_filter, str):
        _parse_base_expression(base_filter)
        return
    if not isinstance(base_filter, dict):
        raise BaseFilterSyntaxError("expected a string or one-operator mapping")
    if len(base_filter) != 1:
        raise BaseFilterSyntaxError(
            "operator mappings must contain exactly one of and/or/not"
        )
    operator, expressions = next(iter(base_filter.items()))
    if operator not in {"and", "or", "not"}:
        raise BaseFilterUnsupportedError(f"operator {operator!r}")
    if not isinstance(expressions, list) or not expressions:
        raise BaseFilterSyntaxError(f"{operator} must contain a non-empty list")
    for expression in expressions:
        _validate_base_filter(expression)


def _base_literal(value: str) -> Any:
    text = value.strip()
    if text in {"true", "false"}:
        return text == "true"
    if text == "null":
        return None
    if re.fullmatch(r"-?(?:\d+(?:\.\d*)?|\.\d+)", text):
        return float(text) if "." in text else int(text)
    if len(text) >= 2 and text[0] in {'"', "'"} and text[-1] == text[0]:
        try:
            return ast.literal_eval(text)
        except (SyntaxError, ValueError) as exc:
            raise BaseFilterSyntaxError(f"invalid string literal {value!r}") from exc
    raise BaseFilterSyntaxError(f"unsupported literal {value!r}")


def _base_field(value: str) -> str:
    bracket = re.fullmatch(r"\[([\"'])(.*)\1\]", value)
    if bracket:
        return str(
            _base_literal(bracket.group(1) + bracket.group(2) + bracket.group(1))
        )
    if re.fullmatch(r"[\w.]+", value):
        return value
    raise BaseFilterSyntaxError(f"invalid property access {value!r}")


def _parse_base_expression(expression: str) -> Tuple[str, str, Any]:
    """Parse one supported expression, separating malformed and unsupported input."""
    folder_match = re.fullmatch(r'file\.inFolder\(["\']([^"\']+)["\']\)', expression)
    if folder_match:
        return "inFolder", folder_match.group(1).rstrip("/"), None
    field_pattern = r"(?:[\w.]+|\[(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*')\])"
    contains_match = re.fullmatch(rf"({field_pattern})\.contains\((.+)\)", expression)
    if contains_match:
        return (
            "contains",
            _base_field(contains_match.group(1)),
            _base_literal(contains_match.group(2)),
        )
    comparison_match = re.fullmatch(
        rf"({field_pattern})\s*(==|!=|>=|<=|>|<)\s*(.+)", expression
    )
    if comparison_match:
        field, operator, literal = comparison_match.groups()
        return operator, _base_field(field), _base_literal(literal)
    if re.fullmatch(r'[\w.]+\s*=\s*["\'][^"\']*["\']', expression):
        raise BaseFilterSyntaxError(f"{expression!r}: use == for equality, not =")
    if re.match(r"^(?:[\w.]+|\[).*(?:==|!=|=)|^[\w.]+\s*\.", expression):
        raise BaseFilterSyntaxError(f"malformed expression {expression!r}")
    if re.match(r"^[\w.]+\s*\(", expression):
        raise BaseFilterUnsupportedError(f"expression {expression!r}")
    raise BaseFilterSyntaxError(f"malformed expression {expression!r}")


def _base_filter_folders(base_filter: Any) -> Set[str]:
    folders: Set[str] = set()
    if isinstance(base_filter, str):
        try:
            kind, value, _ = _parse_base_expression(base_filter)
        except (BaseFilterSyntaxError, BaseFilterUnsupportedError):
            return folders
        if kind == "inFolder":
            folders.add(value)
    elif isinstance(base_filter, dict):
        for expressions in base_filter.values():
            if isinstance(expressions, list):
                for expression in expressions:
                    folders.update(_base_filter_folders(expression))
    return folders


def _base_view_may_be_empty(
    base_path: Path, vault_root: Path, filters: Any, view_name: str
) -> bool:
    folders = _base_filter_folders(filters)
    if folders and all(not (vault_root / folder).is_dir() for folder in folders):
        return True
    return (base_path.name, view_name) in _OPTIONAL_EMPTY_BASE_VIEWS


def _load_vault_records(vault_root: Path) -> List[Dict[str, Any]]:
    """Load parseable note frontmatter with paths relative to the opened vault."""
    records: List[Dict[str, Any]] = []
    for note_path in vault_root.rglob("*.md"):
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.startswith("---\n"):
            continue
        try:
            end = text.index("\n---\n", 4)
            frontmatter = yaml.safe_load(text[4:end]) or {}
        except (ValueError, yaml.YAMLError):
            continue
        if not isinstance(frontmatter, dict):
            continue
        relative_path = note_path.relative_to(vault_root).as_posix()
        record = dict(frontmatter)
        record["_path"] = relative_path
        record["_folder"] = str(Path(relative_path).parent).replace(".", "")
        records.append(record)
    return records


def _evaluate_base_filters(
    records: List[Dict[str, Any]], filters: Any
) -> List[Dict[str, Any]]:
    """Evaluate the supported, declarative subset of Obsidian Base filters."""
    if filters is None:
        return records
    return [record for record in records if _matches_base_filter(record, filters)]


def _matches_base_filter(record: Dict[str, Any], base_filter: Any) -> bool:
    if isinstance(base_filter, dict):
        operator, expressions = next(iter(base_filter.items()))
        matches = [
            _matches_base_filter(record, expression) for expression in expressions
        ]
        if operator == "and":
            return all(matches)
        if operator == "or":
            return any(matches)
        return not any(matches)

    kind, field_or_folder, expected = _parse_base_expression(base_filter)
    if kind == "inFolder":
        folder = field_or_folder
        record_folder = str(record.get("_folder", "")).rstrip("/")
        return record_folder == folder or record_folder.startswith(f"{folder}/")
    actual = record.get(field_or_folder)
    if kind == "contains":
        if isinstance(actual, list):
            return any(item == expected or str(item) == expected for item in actual)
        return expected in str(actual or "")
    if kind in {">", ">=", "<", "<="}:
        try:
            actual_number = float(actual)
            expected_number = float(expected)
        except (TypeError, ValueError):
            return False
        if kind == ">":
            return actual_number > expected_number
        if kind == ">=":
            return actual_number >= expected_number
        if kind == "<":
            return actual_number < expected_number
        return actual_number <= expected_number
    equal = actual == expected
    return not equal if kind == "!=" else equal


def evaluate_declarative_query(
    records: List[Dict[str, Any]],
    *,
    from_folder: Optional[str] = None,
    where_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
    sort_key: Optional[str] = None,
    reverse: bool = False,
) -> List[Dict[str, Any]]:
    """Portable evaluation of a declarative query against frontmatter records (#44)."""
    results = []
    for r in records:
        if from_folder:
            rel = str(r.get("_folder") or r.get("path") or "")
            if not rel.startswith(from_folder):
                continue
        if where_fn is not None and not where_fn(r):
            continue
        results.append(r)
    if sort_key:
        results.sort(key=lambda x: str(x.get(sort_key, "")), reverse=reverse)
    return results


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate Dataview query blocks")
    parser.add_argument(
        "--vault",
        action="append",
        default=None,
        help="Vault directory to scan (repeatable)",
    )
    parser.add_argument(
        "--files", nargs="*", default=None, help="Specific .md files to check"
    )
    args = parser.parse_args()

    global _KNOWN_FIELDS
    _KNOWN_FIELDS = _extract_known_fields(
        args.schema if hasattr(args, "schema") else _SCHEMA_PATH
    )

    # Add well-known Dataview virtual fields
    _KNOWN_FIELDS.update(
        {
            "file.name",
            "file.path",
            "file.folder",
            "file.link",
            "file.frontmatter",
            "file.tags",
            "file.outlinks",
            "file.inlinks",
            "file.ctime",
            "file.mtime",
            "file.size",
            "file.day",
            "title",
            "tags",
            "status",
            "component",
            "components",
        }
    )

    vault_dirs: List[Path] = []
    if args.vault:
        vault_dirs = [Path(v).resolve() for v in args.vault]
    else:
        examples_vault = _WORKSPACE / "examples" / "vault"
        if examples_vault.is_dir():
            vault_dirs.append(examples_vault)

    # Determine files to scan. Queries appear both in dashboards and generated
    # domain notes (for example, a control's frontmatter-backed parameters).
    md_files: List[Path] = []
    if args.files:
        md_files = [Path(f).resolve() for f in args.files if f.endswith(".md")]
    else:
        for vd in vault_dirs:
            md_files.extend(vd.rglob("*.md"))
        md_files = sorted(set(md_files))

    bases_files: List[Path] = []
    for vd in vault_dirs:
        bases_files.extend(vd.rglob("*.base"))
    bases_files = sorted(set(bases_files))

    if not md_files and not bases_files:
        print("No Dataview-containing files found.")
        return 0

    errors = 0
    warnings = 0
    blocks_checked = 0

    for md_path in md_files:
        try:
            text = md_path.read_text()
        except Exception:
            continue

        blocks = _parse_dataview_blocks(text)
        if not blocks:
            continue

        rel = (
            md_path.relative_to(_WORKSPACE)
            if _WORKSPACE in md_path.parents
            else md_path
        )

        for line_no, query in blocks:
            blocks_checked += 1

            # Basic structure check
            has_from = bool(re.search(r"\bFROM\b", query))
            has_table_or_list = bool(re.search(r"\b(TABLE|LIST|TASK)\b", query))
            if not has_from and not has_table_or_list:
                print(
                    f"WARN  {rel}:{line_no}: Dataview block missing FROM or TABLE/LIST"
                )
                warnings += 1

            # Check folder references
            folders = _extract_folder_refs(query)
            for folder in folders:
                # Strip trailing slashes and wildcards
                folder_clean = folder.rstrip("/")
                if folder_clean not in _KNOWN_FOLDERS:
                    print(f'WARN  {rel}:{line_no}: FROM "{folder}" — unknown folder')
                    warnings += 1

            # Check field references
            fields = _extract_field_refs(query)
            for field in fields:
                # Skip literals, function calls, and known virtual fields
                if field.startswith('"') or field in _KNOWN_FIELDS:
                    continue
                # Skip Dataview builtins
                if field in ("file", "this", "dv", "luxon"):
                    continue
                # Skip assignment targets
                if re.match(r"^\d", field):
                    continue

                print(
                    f"WARN  {rel}:{line_no}: field {field!r} not found in known schema fields"
                )
                warnings += 1

    # Check Bases views (.base files)
    bases_checked = 0
    expected_roots: Optional[Set[Path]] = None
    if args.vault:
        expected_roots = set()
        for vault_dir in vault_dirs:
            if (vault_dir / "_meta" / "views").is_dir():
                expected_roots.add(vault_dir)
            else:
                expected_roots.update(
                    root
                    for base_path in vault_dir.rglob("*.base")
                    if (root := _base_vault_root(base_path)) is not None
                )
    for bp in bases_files:
        w, e = _validate_bases_file(bp, expected_roots)
        warnings += w
        errors += e
        bases_checked += 1

    if bases_checked:
        print(f"{bases_checked} Bases view file(s) checked")
    print(
        f"\n{blocks_checked} Dataview blocks checked, {warnings} warnings, {errors} errors"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
