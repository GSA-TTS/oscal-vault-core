"""Parse a FIPS-199 categorization .docx file and convert to OSCAL.

This module reads a FedRAMP FIPS-199 Security Categorization document produced
with python-docx and returns structured data about the system's information
types and overall impact level (Low / Moderate / High).

The parse is intentionally lenient: any failure returns ``None`` rather than
raising, so callers can treat FIPS-199 as an optional enrichment layer.
"""

from __future__ import annotations

import re
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# UUIDv5 namespace for deterministic information-type UUIDs (same seed as
# export_oscal._OSCAL_NS so they remain stable across re-exports).
_OSCAL_NS = _uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# Patterns to detect the overall categorization level in document text.
_LEVEL_PATTERNS: List[tuple[str, str]] = [
    (r"determined to be (?:a\s+)?high", "high"),
    (r"determined to be (?:a\s+)?moderate", "moderate"),
    (r"determined to be (?:a\s+)?low", "low"),
    (r"\boverall.*?categorization.*?:\s*(high|moderate|low)\b", None),
    (r"\bcategorized.*?(?:as|level)\s+(high|moderate|low)\b", None),
]

# SP 800-60 identifier pattern, e.g. C.2.6.1
_SP800_60_RE = re.compile(r"\b[A-Z]\.\d+(?:\.\d+){1,3}\b")

# Normalize impact cell values to canonical lower-case names.
_IMPACT_NORM: Dict[str, str] = {
    "l": "low",
    "low": "low",
    "m": "moderate",
    "mod": "moderate",
    "moderate": "moderate",
    "h": "high",
    "high": "high",
    "n/a": "low",
    "na": "low",
}


def _normalize_impact(raw: str) -> str:
    """Return canonical impact string or the raw value when unrecognized."""
    key = raw.strip().lower()
    return _IMPACT_NORM.get(key, key)


@dataclass
class InformationType:
    title: str
    sp800_60_id: Optional[str]
    confidentiality: str
    integrity: str
    availability: str


@dataclass
class Fips199Result:
    overall_level: str  # low / moderate / high
    information_types: List[InformationType] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_overall_level(full_text: str) -> Optional[str]:
    """Scan document text for the overall categorization level."""
    lower = full_text.lower()
    for pattern, fixed in _LEVEL_PATTERNS:
        m = re.search(pattern, lower)
        if m:
            if fixed:
                return fixed
            # The regex captured the level in group 1.
            return m.group(1).lower()
    return None


def _is_impact_value(s: str) -> bool:
    """Return True if *s* looks like an impact level cell value."""
    return s.strip().lower() in _IMPACT_NORM


def _extract_sp800_60(cell_text: str) -> Optional[str]:
    m = _SP800_60_RE.search(cell_text)
    return m.group(0) if m else None


def _cell_text(cell: Any) -> str:
    """Return the stripped text content of a python-docx cell."""
    return cell.text.strip()


def _parse_tables(doc: Any) -> List[InformationType]:
    """Walk all tables in *doc* and extract information-type rows."""
    info_types: List[InformationType] = []

    for table in doc.tables:
        # Collect header row to identify C/I/A columns.
        if not table.rows:
            continue
        header_cells = [_cell_text(c).lower() for c in table.rows[0].cells]

        # Heuristic: find columns for title/name, C, I, A.
        # Accept various header phrasings.
        title_col: Optional[int] = None
        c_col: Optional[int] = None
        i_col: Optional[int] = None
        a_col: Optional[int] = None

        for idx, h in enumerate(header_cells):
            if title_col is None and any(
                kw in h
                for kw in (
                    "information type",
                    "type name",
                    "information category",
                    "name",
                )
            ):
                title_col = idx
            if c_col is None and re.search(r"\bconf|\bc\b", h):
                c_col = idx
            if i_col is None and re.search(r"\binteg|\bi\b", h):
                i_col = idx
            if a_col is None and re.search(r"\bavail|\ba\b", h):
                a_col = idx

        if title_col is None or None in (c_col, i_col, a_col):
            # Tables without recognizable C/I/A headers are skipped.
            continue

        # Process data rows.
        for row in table.rows[1:]:
            cells = row.cells
            n = len(cells)
            if n <= max(filter(None, [title_col, c_col, i_col, a_col])):
                continue
            title = _cell_text(cells[title_col])
            c_raw = _cell_text(cells[c_col])
            i_raw = _cell_text(cells[i_col])
            a_raw = _cell_text(cells[a_col])

            if not title:
                continue
            if not (
                _is_impact_value(c_raw)
                or _is_impact_value(i_raw)
                or _is_impact_value(a_raw)
            ):
                continue

            # Extract SP 800-60 id from any cell in the row.
            sp_id: Optional[str] = None
            for cell in cells:
                sp_id = _extract_sp800_60(_cell_text(cell))
                if sp_id:
                    break

            info_types.append(
                InformationType(
                    title=title,
                    sp800_60_id=sp_id,
                    confidentiality=_normalize_impact(c_raw),
                    integrity=_normalize_impact(i_raw),
                    availability=_normalize_impact(a_raw),
                )
            )

    return info_types


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Module-level reference to the docx Document constructor, so tests can patch
# ``compliance.lib.fips199.Document`` without the lazy-import dance.
try:
    from docx import Document  # type: ignore[import]
except ImportError:  # pragma: no cover — python-docx is an optional dep
    Document = None  # type: ignore[assignment,misc]


def parse_fips199(docx_path: str) -> Optional[Fips199Result]:
    """Parse a FIPS-199 categorization `.docx` and return structured data.

    Returns ``None`` on any failure so callers may treat this as optional
    enrichment — the OSCAL exporter falls back to a synthetic information-type.
    """
    try:
        if Document is None:
            return None
        doc = Document(docx_path)
    except Exception:  # noqa: BLE001
        return None

    try:
        # Collect all text for level detection.
        full_text = "\n".join(p.text for p in doc.paragraphs)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    full_text += "\n" + cell.text

        info_types = _parse_tables(doc)

        # Derive overall level: scan explicit markers first, then fall back to
        # the highest impact found across information types.
        overall_level = _detect_overall_level(full_text)
        if not overall_level and info_types:
            _rank = {"low": 0, "moderate": 1, "high": 2}
            highest = max(
                info_types,
                key=lambda it: max(
                    _rank.get(it.confidentiality, 0),
                    _rank.get(it.integrity, 0),
                    _rank.get(it.availability, 0),
                ),
            )
            val = max(
                (highest.confidentiality, highest.integrity, highest.availability),
                key=lambda v: _rank.get(v, 0),
            )
            overall_level = val
        overall_level = overall_level or "moderate"

        return Fips199Result(
            overall_level=overall_level,
            information_types=info_types,
        )
    except Exception:  # noqa: BLE001
        return None


def fips199_to_oscal_information_types(
    result: Fips199Result,
    oscal_version: str = "1.1.3",
) -> List[Dict[str, Any]]:
    """Convert a :class:`Fips199Result` to an OSCAL ``information-types[]`` list.

    Shape for OSCAL 1.1.3::

        {
          "uuid": "<uuidv5>",
          "title": "<title>",
          "description": "<title> information type",
          "categorizations": [{"system": "...", "information-type-ids": ["<id>"]}]
        }

    For OSCAL 1.0.x, ``confidentiality-impact`` / ``integrity-impact`` /
    ``availability-impact`` are added as well.
    """
    out: List[Dict[str, Any]] = []
    for it in result.information_types:
        seed = f"infotype:{it.title}"
        uid = str(_uuid.uuid5(_OSCAL_NS, seed))
        entry: Dict[str, Any] = {
            "uuid": uid,
            "title": it.title,
            "description": f"{it.title} information type",
        }
        if it.sp800_60_id:
            entry["categorizations"] = [
                {
                    "system": "http://doi.org/10.6028/NIST.SP.800-60v2r1",
                    "information-type-ids": [it.sp800_60_id],
                }
            ]
        if oscal_version.startswith("1.0"):
            for key, val in (
                ("confidentiality-impact", it.confidentiality),
                ("integrity-impact", it.integrity),
                ("availability-impact", it.availability),
            ):
                entry[key] = {"base": val}
        out.append(entry)
    return out
