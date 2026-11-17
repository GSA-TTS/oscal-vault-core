"""Convert GSA assessment workbooks (ODS) into schema-validated canonical JSON.

Two workbook families are supported:

* the GSA Control Tailoring Workbook (baselines, overlays, GSA parameter values), and
* the GSA NIST SP 800-53A test-case workbook (assessment objectives with
  Examine / Interview / Test methods).

The workbooks are acquisition inputs. The canonical JSON is the committed, auditable
interchange format; consumers validate it with :func:`validate_tailoring` and
:func:`validate_test_cases` and never need the ODS at runtime.

Parsing uses only the standard library. Redaction runs on every free-text field before it
is stored: URLs become ``<REDACTED_URL>``, e-mail addresses ``<POC_EMAIL>``, and ``CUI//``
banner tokens are removed. The converted data is workbook content, not system content.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from jsonschema import Draft202012Validator

from compliance.lib.control_identity import canonical_control_id

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "compliance" / "schemas" / "assessment"
TAILORING_SCHEMA = SCHEMA_DIR / "gsa_control_tailoring_workbook_schema_v1.json"
TEST_CASES_SCHEMA = SCHEMA_DIR / "gsa_test_cases_schema_v1.json"

TAILORING_KIND = "gsa-control-tailoring-workbook"
TEST_CASES_KIND = "gsa-lato-low-test-cases"
TAILORING_FILENAME = "gsa-control-tailoring-workbook.canonical.json"
TEST_CASES_FILENAME = "gsa-lato-low-test-cases.canonical.json"

TAILORING_AUTHORITY = "General Services Administration OCISO ISA Division"
TEST_CASES_AUTHORITY = "General Services Administration OCISO ISP Division"

BASELINE_KEYS = ("ccc", "low", "moderate", "high", "lato", "misaas")
OVERLAY_KEYS = ("privacy", "pci", "cui", "infra", "hva_moderate", "hva_high")
FAMILY_SHEETS = (
    "AC",
    "AU",
    "CA",
    "CM",
    "CP",
    "IA",
    "PL",
    "RA",
    "SA",
    "SC",
    "SI",
    "BOD",
)
BOD_PREFIX = "BOD"

_NS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}
_TABLE = "{%s}table" % _NS["table"]
_ROW = "{%s}table-row" % _NS["table"]
_CELL = "{%s}table-cell" % _NS["table"]
_NAME = "{%s}name" % _NS["table"]
_COLS_REPEATED = "{%s}number-columns-repeated" % _NS["table"]
_P = "{%s}p" % _NS["text"]
_LINE_BREAK = "{%s}line-break" % _NS["text"]

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(
    r"https?://(?!(?:localhost|127\.0\.0\.1|github\.com)(?=[:/]|$))[^\s\"'>)]+",
    re.IGNORECASE,
)
_CUI_BANNER_RE = re.compile(r"\bCUI//[A-Z][A-Z0-9_-]*\b")
_FILENAME_DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
_MAX_COLUMN_REPEAT = 100


class WorkbookError(ValueError):
    """The workbook is not the expected shape, or canonical JSON failed validation."""


def sanitize_text(text: str) -> str:
    """Redact URLs, e-mail addresses and CUI banner tokens, then trim."""
    text = _CUI_BANNER_RE.sub("", text)
    text = _URL_RE.sub("<REDACTED_URL>", text)
    text = _EMAIL_RE.sub("<POC_EMAIL>", text)
    return text.strip()


def normalize_title(raw_name: str) -> str:
    """Collapse mid-title newlines and runs of whitespace between lines."""
    return " ".join(line.strip() for line in raw_name.splitlines() if line.strip())


def version_from_filename(name: str) -> str:
    """``...-08-01-2026...`` -> ``2026-08-01``. Fails closed when no date is present."""
    match = _FILENAME_DATE_RE.search(name)
    if not match:
        raise WorkbookError(
            f"cannot infer a version from {name!r}; pass the version explicitly"
        )
    month, day, year = match.groups()
    return f"{year}-{month}-{day}"


# ---------------------------------------------------------------------------
# ODS reading (standard library only)
# ---------------------------------------------------------------------------


def _cell_text(node: ET.Element) -> str:
    """Cell text with paragraph and line-break newlines; trimmed at every level."""
    out: list[str] = []
    if node.text:
        out.append(node.text)
    for child in node:
        if child.tag == _LINE_BREAK:
            out.append("\n")
        elif child.tag == _P:
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            out.append(_cell_text(child))
        else:
            out.append(_cell_text(child))
        if child.tail:
            out.append(child.tail)
    return "".join(out).strip()


def _row_cells(row: ET.Element) -> list[str]:
    cells: list[str] = []
    for cell in row.iter(_CELL):
        repeat = int(cell.get(_COLS_REPEATED) or 1)
        text = _cell_text(cell)
        cells.extend([text] * min(repeat, _MAX_COLUMN_REPEAT))
    while cells and cells[-1] == "":
        cells.pop()
    return cells


class Workbook:
    """Read-only view of an ODS package: sheet name -> rows of cell strings."""

    def __init__(self, path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read("content.xml"))
        except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
            raise WorkbookError(f"not a readable ODS workbook: {path.name}") from exc
        self._sheets: dict[str, ET.Element] = {}
        for table in root.iter(_TABLE):
            self._sheets.setdefault(table.get(_NAME, ""), table)

    def rows(self, name: str) -> list[list[str]]:
        table = self._sheets.get(name)
        if table is None:
            raise WorkbookError(f"sheet {name!r} not found in workbook")
        return [_row_cells(row) for row in table.iter(_ROW)]


def _flag(row: list[str], index: int) -> bool:
    return len(row) > index and row[index].strip().upper() == "X"


def _cell(row: list[str], index: int) -> str:
    return row[index] if len(row) > index else ""


# ---------------------------------------------------------------------------
# Control Tailoring Workbook
# ---------------------------------------------------------------------------


def _read_instructions(rows: list[list[str]]) -> dict[str, Any]:
    overview = ""
    notes: list[str] = []
    columns: dict[str, str] = {}
    skip = ("Column - Name", "Instructions Tab", "GSA Standard Settings Tab")
    for row in rows:
        if not row or not any(row) or len(row) < 2:
            continue
        if row[0] == "Overview":
            overview = sanitize_text(row[1])
        elif row[0].startswith("Notes:"):
            notes.append(sanitize_text(row[1]))
        elif not row[0] and row[1].startswith("("):
            notes.append(sanitize_text(row[1]))
        elif row[0] and row[0] not in skip:
            columns[row[0]] = sanitize_text(row[1])
    return {
        "overview": overview,
        "shading_notes": notes,
        "column_descriptions": columns,
    }


def _read_hva(rows: list[list[str]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for row in rows[3:]:
        if not row or not any(row) or not row[0].strip():
            continue
        raw_id = row[0].strip()
        entries.append(
            {
                "control_id": canonical_control_id(raw_id),
                "raw_control_id": raw_id,
                "name": normalize_title(_cell(row, 1).strip()),
                "hva_moderate": _flag(row, 3),
                "hva_high": _flag(row, 4),
                "vendor_contractor_values": sanitize_text(_cell(row, 5)),
                "gsa_approval": sanitize_text(_cell(row, 6)),
            }
        )
    return entries


def convert_tailoring_workbook(
    path: Path, *, version: str | None = None
) -> dict[str, Any]:
    """Parse a GSA Control Tailoring Workbook into canonical JSON."""
    book = Workbook(path)
    instructions = _read_instructions(book.rows("Instructions"))
    designations = [
        row[0].strip()
        for row in book.rows("ControlDesignation")[1:]
        if row and row[0].strip()
    ]
    hva = _read_hva(book.rows("GSA HVA Overlay"))
    hva_by_id = {entry["control_id"]: entry for entry in hva}

    baselines_summary = dict.fromkeys(BASELINE_KEYS, 0)
    overlays_summary = dict.fromkeys(OVERLAY_KEYS, 0)
    controls: list[dict[str, Any]] = []
    for row in book.rows("GSA Standard Settings")[3:]:
        if not row or len(row) < 2 or not row[1].strip():
            continue
        raw_id = row[1].strip()
        control_id = canonical_control_id(raw_id)
        baselines = {key: _flag(row, 4 + i) for i, key in enumerate(BASELINE_KEYS)}
        hva_entry = hva_by_id.get(control_id)
        overlays = {
            "privacy": _flag(row, 10),
            "pci": _flag(row, 11),
            "cui": _flag(row, 12),
            "infra": _flag(row, 13),
            "hva_moderate": bool(hva_entry and hva_entry["hva_moderate"]),
            "hva_high": bool(hva_entry and hva_entry["hva_high"]),
        }
        for key, on in baselines.items():
            baselines_summary[key] += on
        for key, on in overlays.items():
            overlays_summary[key] += on
        controls.append(
            {
                "control_id": control_id,
                "raw_control_id": raw_id,
                "family": control_id.split("-")[0],
                "name": normalize_title(_cell(row, 2).strip()),
                "designation": row[0].strip() or None,
                "statement": sanitize_text(_cell(row, 3)),
                "baselines": baselines,
                "overlays": overlays,
                "vendor_contractor_values": sanitize_text(_cell(row, 14)),
                "gsa_approval": sanitize_text(_cell(row, 15)),
            }
        )

    return {
        "kind": TAILORING_KIND,
        "title": "GSA Control Tailoring Workbook (CTW)",
        "version": version or version_from_filename(path.name),
        "source_document": path.name,
        "authority": TAILORING_AUTHORITY,
        "poc_email": "<POC_EMAIL>",
        "instructions": instructions,
        "control_designations": designations,
        "total_controls": len(controls),
        "baselines_summary": baselines_summary,
        "overlays_summary": overlays_summary,
        "controls": controls,
        "hva_overlay": hva,
    }


# ---------------------------------------------------------------------------
# Test-case workbook
# ---------------------------------------------------------------------------


def _test_case_id(raw: str, *, bod: bool) -> str:
    raw = raw.strip()
    if bod or raw.startswith(BOD_PREFIX):
        return raw.replace("BOD-", "BOD ")
    return canonical_control_id(raw)


def _read_logic_counts(rows: list[list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows[2:]:
        if len(row) < 3 or not row[1].strip() or not row[2].strip():
            continue
        key = row[1].strip()
        try:
            counts[_test_case_id(key, bod=False)] = int(row[2].strip())
        except ValueError:
            counts[key] = int(row[2].strip())
    return counts


def _read_summary(
    rows: list[list[str]], counts: dict[str, int]
) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for row in rows[2:]:
        if not row or not any(row) or len(row) < 3 or not row[1].strip():
            continue
        raw_id = row[1].strip()
        control_id = _test_case_id(raw_id, bod=False)
        summary.append(
            {
                "family": row[0].strip(),
                "control_id": control_id,
                "raw_control_id": raw_id,
                "name": normalize_title(row[2].strip()),
                "assessment_result": _cell(row, 3).strip() or "Incomplete",
                "expected_test_cases": counts.get(control_id, counts.get(raw_id, 0)),
            }
        )
    return summary


def _read_test_cases(book: Workbook) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for sheet in FAMILY_SHEETS:
        for row in book.rows(sheet)[2:]:
            if not row or not any(row):
                continue
            raw_id = _cell(row, 1).strip()
            bod = sheet == "BOD" or raw_id.startswith(BOD_PREFIX)
            control_id = _test_case_id(raw_id, bod=bod)
            cases.append(
                {
                    "family": BOD_PREFIX if bod else control_id.split("-")[0],
                    "control_id": control_id,
                    "raw_control_id": raw_id,
                    "control_name": normalize_title(_cell(row, 0).strip()),
                    "procedure_id": _cell(row, 2).strip(),
                    "objective": sanitize_text(_cell(row, 3)),
                    "examine": sanitize_text(_cell(row, 4)),
                    "interview": sanitize_text(_cell(row, 5)),
                    "test": sanitize_text(_cell(row, 6)),
                    "observations_and_evidence": sanitize_text(_cell(row, 7)),
                    "assessment_result": _cell(row, 8).strip(),
                    "recommendation_for_mitigation": sanitize_text(_cell(row, 9)),
                }
            )
    return cases


def _read_notes(rows: list[list[str]]) -> tuple[list[str], str | None]:
    notes: list[str] = []
    version: str | None = None
    for row in rows:
        if not row or not any(row):
            continue
        text = " ".join(row).strip()
        if text.startswith("Version "):
            version = text.replace("Version ", "").strip()
        elif not text.startswith("CUI") and not text.startswith("Controlled by:"):
            notes.append(sanitize_text(text))
    return notes, version


def convert_test_case_workbook(
    path: Path, *, version: str | None = None
) -> dict[str, Any]:
    """Parse a GSA NIST SP 800-53A test-case workbook into canonical JSON."""
    book = Workbook(path)
    notes, notes_version = _read_notes(book.rows("Notes"))
    resolved = version or notes_version
    if not resolved:
        raise WorkbookError(
            f"no 'Version' line on the Notes sheet of {path.name!r}; pass the version"
        )
    summary = _read_summary(
        book.rows("CtrlSummary"), _read_logic_counts(book.rows("Logic"))
    )
    cases = _read_test_cases(book)
    return {
        "kind": TEST_CASES_KIND,
        "title": "GSA NIST SP 800-53A Rev 5 Assessment Test Cases (LATO Low)",
        "version": resolved,
        "source_document": path.name,
        "authority": TEST_CASES_AUTHORITY,
        "poc_email": "<POC_EMAIL>",
        "categorization_level": "Low",
        "notes": notes,
        "total_controls_in_scope": len(summary),
        "total_test_cases": len(cases),
        "summary": summary,
        "test_cases": cases,
    }


# ---------------------------------------------------------------------------
# Validation of canonical JSON (no ODS needed)
# ---------------------------------------------------------------------------


def _schema_errors(data: dict[str, Any], schema_path: Path) -> list[str]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    ]


def _is_canonical(control_id: str) -> bool:
    if control_id.startswith(BOD_PREFIX):
        return True
    try:
        return canonical_control_id(control_id) == control_id
    except ValueError:
        return False


def tailoring_errors(data: dict[str, Any]) -> list[str]:
    errors = _schema_errors(data, TAILORING_SCHEMA)
    if errors:
        return errors
    controls = data["controls"]
    if data["total_controls"] != len(controls):
        errors.append("total_controls does not match len(controls)")
    seen: set[str] = set()
    for control in controls:
        cid = control["control_id"]
        if not _is_canonical(cid):
            errors.append(f"{cid}: control_id is not canonical")
        if cid in seen:
            errors.append(f"{cid}: duplicate control_id")
        seen.add(cid)
        if control["family"] != cid.split("-")[0]:
            errors.append(f"{cid}: family does not match control_id")
    for key, expected in data["baselines_summary"].items():
        actual = sum(1 for c in controls if c["baselines"][key])
        if actual != expected:
            errors.append(f"baselines_summary[{key}]={expected} but {actual} controls")
    for key, expected in data["overlays_summary"].items():
        actual = sum(1 for c in controls if c["overlays"][key])
        if actual != expected:
            errors.append(f"overlays_summary[{key}]={expected} but {actual} controls")
    return errors


def test_case_errors(data: dict[str, Any]) -> list[str]:
    errors = _schema_errors(data, TEST_CASES_SCHEMA)
    if errors:
        return errors
    cases, summary = data["test_cases"], data["summary"]
    if data["total_test_cases"] != len(cases):
        errors.append("total_test_cases does not match len(test_cases)")
    if data["total_controls_in_scope"] != len(summary):
        errors.append("total_controls_in_scope does not match len(summary)")
    per_control: dict[str, int] = {}
    seen: set[str] = set()
    for case in cases:
        per_control[case["control_id"]] = per_control.get(case["control_id"], 0) + 1
        if not _is_canonical(case["control_id"]):
            errors.append(f"{case['procedure_id']}: control_id is not canonical")
        if case["procedure_id"] in seen:
            errors.append(f"{case['procedure_id']}: duplicate procedure_id")
        seen.add(case["procedure_id"])
    for entry in summary:
        actual = per_control.get(entry["control_id"], 0)
        if actual != entry["expected_test_cases"]:
            errors.append(
                f"{entry['control_id']}: expected {entry['expected_test_cases']} "
                f"test cases, found {actual}"
            )
    unsummarized = set(per_control) - {entry["control_id"] for entry in summary}
    errors.extend(
        f"{cid}: test cases without a summary row" for cid in sorted(unsummarized)
    )
    return errors


def validate_tailoring(data: dict[str, Any]) -> None:
    errors = tailoring_errors(data)
    if errors:
        raise WorkbookError(
            "invalid tailoring workbook JSON:\n  " + "\n  ".join(errors)
        )


def validate_test_cases(data: dict[str, Any]) -> None:
    errors = test_case_errors(data)
    if errors:
        raise WorkbookError("invalid test-case JSON:\n  " + "\n  ".join(errors))


def validate_canonical_json(data: dict[str, Any]) -> None:
    """Dispatch on ``kind`` and validate."""
    kind = data.get("kind")
    if kind == TAILORING_KIND:
        validate_tailoring(data)
    elif kind == TEST_CASES_KIND:
        validate_test_cases(data)
    else:
        raise WorkbookError(f"unknown canonical JSON kind: {kind!r}")


def render_json(data: dict[str, Any]) -> str:
    """The one serialization used for committed output and drift checks."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
