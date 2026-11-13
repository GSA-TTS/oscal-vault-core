"""Fill FedRAMP SSP control-summary (CIS) tables in place (Phase R17).

This is the write-direction counterpart to ``fismatic.parser``'s
``parse_control_table`` / ``extract_implementation_details``: given a list
of :class:`~compliance.lib.canonical_model.CanonicalControl` records and a
``.docx`` containing CIS tables detected by
:mod:`compliance.lib.control_table_anchors`, it toggles each control's
checkbox runs and writes its narrative into the matching table -- **in
place**, preserving every other byte of styling the blank template already
carries (fonts, borders, cell shading, merged-cell structure).

Unlike ``compliance/lib/docx_generator.py`` (which builds a *new*, minimal
document from scratch), this module mutates an **existing**, fully-styled
template -- specifically the four committed
``compliance/templates/*.jinja.docx`` blanks -- so the result keeps 100% of
the original template fidelity (headers, footers, instruction text, fonts,
numbering) while also carrying full control-implementation content. This
closes the coverage gap identified in the roundtrip fidelity evaluation:
previously only ~7-52 narrative slots per template were fillable via
``docx_jinjafier``/``docx_fill``; this module fills every CIS table (54-410
per template, matching the corpus counts in ``docs/PARSING_ACCURACY_NOTES.md``).

Round-trip guarantee: ``fismatic.parser.parse_control_table`` +
``extract_implementation_details`` (the READ path, unmodified) can parse the
tables this module writes, because both sides key off the same structural
row layout (see ``control_table_anchors.py`` module docstring) and the same
checkbox-label regexes (``control_checkbox_vocab.py``). This is verified by
:mod:`compliance.tests.test_control_table_fill`'s live round-trip test.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from compliance.lib.canonical_model import CanonicalControl
from compliance.lib.control_checkbox_vocab import (
    CHECKBOX_GLYPHS,
    CHECKED_GLYPH,
    ORIGINATION_KEY_PATTERNS,
    OSCAL_ORIGINATION_TO_KEY,
    OSCAL_STATE_TO_KEY,
    STATUS_KEY_PATTERNS,
    UNCHECKED_GLYPH,
)
from compliance.lib.control_table_anchors import (
    ControlTableAnchor,
    find_control_tables,
)

_W_T = qn("w:t")
_ROUTING_LABEL_RE = re.compile(
    r"Controls Reference:|GSA Implementation of [^:\n]*:|USA\.gov [A-Za-z ]*Responsibility:"
    r"|Customer Responsibility:|Cloud\.gov [A-Za-z ]*Responsibility:|GSA Responsibility:"
    r"|Not Applicable\."
)


def _norm_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _narrative_pieces(text: str) -> list[str]:
    """Return meaningful sentence fragments after removing routing labels."""
    pieces: list[str] = []
    for sentence in re.split(
        r"(?<=[.!?])\s+|\n+", _ROUTING_LABEL_RE.sub("\n", text or "")
    ):
        piece = _norm_text(sentence)
        if piece:
            pieces.append(piece)
    return pieces


_W_SDT = qn("w:sdt")
_W_SDT_CONTENT = qn("w:sdtContent")
_PAREN_RE = re.compile(r"\([^)]*\)")
_CONTROL_ID_COMPONENTS_RE = re.compile(r"^([A-Za-z]{2})-0*(\d+)(?:\s*\(0*(\d+)\))?$")


def _unwrap_sdt_runs(paragraph: Paragraph) -> None:
    """Replace any ``<w:sdt>`` content-control wrapper with its raw content.

    The official blank templates' checkbox runs are frequently wrapped in a
    Google-Docs-originated ``<w:sdt>`` (structured document tag, tagged
    ``goog_rdk_*``) purely as an editing-suggestion artifact -- it carries no
    checkbox/form-field semantics (no ``<w:checkBox>``, just a plain
    ``<w:r>`` run inside ``<w:sdtContent>``). Word and LibreOffice both
    render the *live* text either way, but mutating the run's ``<w:t>``
    *inside* an untouched ``<w:sdt>`` wrapper has been observed to silently
    fail to round-trip through ``python-docx``'s save/reload cycle in this
    codebase's environment (reproduced directly: a `<w:t>` mutated in place
    inside its original ``<w:sdt>`` reverts to the pre-mutation glyph after
    ``doc.save()`` + reopen, even though the in-memory lxml element itself
    correctly reflects the new text before saving). Unwrapping first --
    promoting ``<w:sdtContent>``'s children up to the paragraph and
    discarding the ``<w:sdt>`` element -- makes the mutation persist
    reliably, and is a lossless transform (the run's own formatting is
    unaffected; only the removed wrapper carried no visible content).
    """
    for sdt in list(paragraph._p.findall(_W_SDT)):
        content = sdt.find(_W_SDT_CONTENT)
        if content is None:
            continue
        parent = sdt.getparent()
        idx = list(parent).index(sdt)
        for offset, child in enumerate(list(content)):
            parent.insert(idx + offset, child)
        parent.remove(sdt)


def _match_key(control_id: str | None) -> str | None:
    """Zero-padding-insensitive control-id comparison key.

    ``fismatic.parser.normalize_control_id`` normalizes case/punctuation but
    deliberately preserves leading zeros verbatim (e.g. ``"AC-2"`` stays
    ``"AC-2"``, ``"AC-02"`` stays ``"AC-02"``) since it's a display-form
    normalizer, not a matching key. The official blank templates print their
    own CIS table headings zero-padded (``"AC-02(01)"``) while
    ``CanonicalControl.control_id`` values from real parsed SSPs are usually
    NOT zero-padded (``"AC-2(1)"``) -- see the roundtrip fidelity evaluation.
    This strips leading zeros from both the family number and any
    enhancement number so ``"AC-2(1)"`` and ``"AC-02(01)"`` compare equal.
    """
    if not control_id:
        return None
    m = _CONTROL_ID_COMPONENTS_RE.match(control_id.strip())
    if not m:
        return control_id.strip().upper() or None
    family, number, enhancement = m.groups()
    base = f"{family.upper()}-{number}"
    return f"{base}({enhancement})" if enhancement else base


@dataclass
class ControlFillResult:
    """Outcome of filling a document's CIS tables."""

    output_path: Path
    filled_control_ids: list[str] = field(default_factory=list)
    filled_table_count: int = 0
    unmatched_control_ids: list[str] = field(default_factory=list)
    unmatched_table_ids: list[str] = field(default_factory=list)
    duplicate_table_ids: list[str] = field(default_factory=list)
    source_id_collisions: dict[str, list[str]] = field(default_factory=dict)
    control_results: list[dict] = field(default_factory=list)


def _strip_glyph(label: str) -> str:
    """Remove a leading checkbox glyph + whitespace from a checkbox line."""
    return label.lstrip("".join(CHECKBOX_GLYPHS)).strip()


def _replace_leading_glyph(text: str, new_glyph: str) -> str:
    """Swap a ``<w:t>``'s leading checkbox glyph, preserving any trailing text.

    Handles both "glyph alone" (``"☐"`` -> ``"☒"``) and "glyph + label in one
    run" (``"☐ Implemented"`` -> ``"☒ Implemented"``) -- see
    :func:`_find_glyph_element`.
    """
    if not text:
        return new_glyph
    return new_glyph + text[1:]


def _find_glyph_element(paragraph: Paragraph):
    """Return the ``<w:t>`` lxml element carrying a checkbox line's glyph.

    Handles three observed shapes in the official blank templates, in
    document order:

    1. Glyph alone in its own ``<w:t>`` (most common; label text is a
       separate sibling run) -- e.g. ``<w:t>☐</w:t><w:t> Planned</w:t>``.
    2. Glyph + label concatenated in a single ``<w:t>`` -- e.g.
       ``<w:t>☒ Implemented</w:t>`` (observed on some "already checked in
       the blank" lines).
    3. Either of the above wrapped in a Google-Docs ``<w:sdt>`` (handled by
       :func:`_unwrap_sdt_runs`, which callers run first).

    Matches on *startswith* rather than exact equality so shape 2 is found;
    walks all descendant ``w:t`` (not just direct-child runs) so a lone
    unwrapped-but-still-nested glyph is found regardless of run structure.
    """
    for t in paragraph._p.findall(f".//{_W_T}"):
        text = (t.text or "").lstrip()
        if text and text[0] in CHECKBOX_GLYPHS:
            return t
    return None


def _set_checkbox_lines(
    cell_paragraphs: list[Paragraph],
    target_keys: Iterable[str],
    key_patterns: list[tuple],
) -> None:
    """Toggle every checkbox paragraph in a status/origination cell.

    ``cell_paragraphs`` includes the header line ("Implementation Status:")
    which has no glyph and is skipped automatically since
    :func:`_find_glyph_element` returns ``None`` for it.
    """
    targets = set(target_keys)
    for para in cell_paragraphs:
        _unwrap_sdt_runs(para)
        glyph_el = _find_glyph_element(para)
        if glyph_el is None:
            continue
        label = _strip_glyph(para.text)
        label_lower = label.lower()
        without_parens = _PAREN_RE.sub("", label_lower).strip()
        matched_key = None
        for key, pattern in key_patterns:
            haystack = (
                without_parens
                if key_patterns is ORIGINATION_KEY_PATTERNS
                else label_lower
            )
            if re.search(pattern, haystack, re.IGNORECASE):
                matched_key = key
                break
        glyph_el.text = _replace_leading_glyph(
            glyph_el.text, CHECKED_GLYPH if matched_key in targets else UNCHECKED_GLYPH
        )


def _text_already_in_table(anchor: ControlTableAnchor, text: str) -> bool:
    """True when every sentence-level piece of ``text`` already sits in this table.

    Used for statements that have no Part row and no blank answer slot. Only the
    statement's own owning table is searched, never the whole document, so a
    match proves placement under the owning control. Controls Reference labels
    are not narrative and are ignored.
    """
    table_pieces = _narrative_pieces(
        "\n".join(cell.text for row in anchor.table.rows for cell in row.cells)
    )
    pieces = _narrative_pieces(text)
    return bool(pieces) and all(piece in table_pieces for piece in pieces)


def _fill_narrative(
    anchor: ControlTableAnchor, control: CanonicalControl
) -> list[dict[str, str | bool | None]]:
    """Write ``control``'s statement text into the anchor's narrative slot(s).

    Prefers matching each statement to an existing "Part <key>" row (by
    inverting ``fismatic.parser.generate_statement_id``'s
    ``{control_id}_stmt.{part}`` convention); falls back to the single blank
    answer row when the control has no Part rows at all. Any statement that
    doesn't match an existing Part row (more statements than the template
    has rows for) is appended to the last matched/blank cell so no content
    is silently dropped.
    """
    if not control.statement_ids and not anchor.part_rows:
        return []

    texts_by_id = getattr(control, "_statement_text_by_id", {}) or {}

    # statement_id -> part key. OSCAL exports use ``_smt.`` while older
    # fismatic-derived records use ``_stmt.``; both identify the same part.
    remaining_text: list[str] = []
    results: list[dict[str, str | bool | None]] = []
    for stmt_id in control.statement_ids:
        text = texts_by_id.get(stmt_id, "")
        part_key = None
        for marker in ("_smt.", "_stmt."):
            if marker in (stmt_id or ""):
                part_key = stmt_id.rsplit(marker, 1)[1].lower()
                break
        cell = anchor.part_rows.get(part_key) if part_key else None
        if cell is not None:
            cell.text = text or cell.text
            results.append(
                {
                    "statement_id": stmt_id,
                    "part": part_key,
                    "placement": "part-row",
                    "written": bool(text),
                }
            )
        elif text and _text_already_in_table(anchor, text):
            results.append(
                {
                    "statement_id": stmt_id,
                    "part": part_key,
                    "placement": "existing-text",
                    "written": True,
                }
            )
        elif text:
            remaining_text.append(text)
            results.append(
                {
                    "statement_id": stmt_id,
                    "part": part_key,
                    "placement": "unmatched",
                    "written": False,
                }
            )

    if remaining_text and anchor.blank_answer_cell is not None:
        _write_into_slot(anchor.blank_answer_cell, "; ".join(remaining_text))
        for result in results:
            if result["placement"] == "unmatched":
                result["placement"] = "blank-answer"
                result["written"] = True
    return results


def _write_into_slot(cell, text: str) -> None:
    """Write ``text`` into a blank-answer slot without destroying existing content.

    An empty slot receives the text. A slot that already carries template or
    source narrative (a labelled row such as "Cloud.gov Customer Responsibility:")
    keeps every existing paragraph; only sentences not already present are
    appended as new paragraphs, so nothing is duplicated and nothing is lost.
    """
    existing = cell.text
    if not _norm_text(existing):
        cell.text = text
        return
    existing_pieces = _narrative_pieces(existing)
    missing = [
        sentence
        for sentence in re.split(
            r"(?<=[.!?])\s+|\n+", _ROUTING_LABEL_RE.sub("\n", text)
        )
        if _norm_text(sentence) and _norm_text(sentence) not in existing_pieces
    ]
    if missing:
        cell.add_paragraph(" ".join(missing))


def _fill_whole_control(
    anchor: ControlTableAnchor, text: str
) -> dict[str, str | bool | None]:
    """Write a statement-less control's narrative into the single blank answer cell.

    Only the blank answer cell is used. A template table that has "Part" rows but a
    source control without statements is reported as ``unmatched`` instead of being
    guessed into a part row, so the gap stays visible in the manifest.
    """
    if anchor.blank_answer_cell is not None:
        _write_into_slot(anchor.blank_answer_cell, text)
        return {"placement": "blank-answer", "written": True}
    return {"placement": "unmatched", "written": False}


def _replace_origination_tokens(origination_cell, provider_label: str) -> None:
    if not provider_label:
        return
    tokens = (
        "[Enter System\u2019s Name]",
        "[Enter System's Name]",
        "[list system(s) providing common portion(s)]",
        "[list systems]",
    )
    for elem in origination_cell._tc.iter(_W_T):
        text = elem.text
        if not text:
            continue
        updated = text
        for token in tokens:
            if token in updated:
                updated = updated.replace(token, provider_label)
        if updated != text:
            elem.text = updated


def fill_control_tables(
    docx_path: str | Path | DocxDocument,
    controls: list[CanonicalControl],
    output_path: str | Path,
    *,
    statement_text_by_control: dict[str, dict[str, str]] | None = None,
    whole_control_text_by_control: dict[str, str] | None = None,
    origination_providers_by_control: dict[str, str] | None = None,
    normalize_id=None,
) -> ControlFillResult:
    """Fill every matching CIS table in ``docx_path`` from ``controls``.

    Parameters
    ----------
    docx_path:
        Path to a blank or jinjafied FedRAMP SSP ``.docx`` (any of the four
        committed ``compliance/templates/*.jinja.docx``, or the original
        blank), or an already-open ``Document``.
    controls:
        Records to write. Each is matched to a CIS table by comparing
        ``normalize_id(control.control_id)`` against
        ``normalize_id(anchor.control_id)`` (both normalized the same way,
        so ``"AC-2(1)"`` matches the template's ``"AC-02(01)"``).
    output_path:
        Where to save the filled ``.docx``.
    statement_text_by_control:
        Optional ``{control_id: {statement_id: text}}`` map giving the
        actual narrative text for each statement (``CanonicalControl`` only
        carries ``statement_ids``, not their text -- see
        ``canonical_model.py``). When omitted, narrative Part rows are left
        untouched (only checkboxes are set).
    whole_control_text_by_control:
        Optional ``{control_id: text}`` for controls that have no statements, so
        their narrative would otherwise be dropped (see
        ``oscal_source.whole_control_text_map_from_oscal_ssp``). Written to the
        single blank answer cell; reported under ``whole_control`` in each control
        result.
    normalize_id:
        Control-ID normalizer, defaulting to
        ``fismatic.parser.normalize_control_id`` (imported lazily so this
        module has no hard dependency on fismatic being importable unless
        actually called). Pass a custom callable to override. Applied
        *before* the zero-padding-insensitive comparison key (see
        :func:`_match_key`) so ``"AC-2(1)"`` (typical parsed-SSP form) and
        ``"AC-02(01)"`` (the templates' own zero-padded heading form)
        always match regardless of which convention the caller's data uses.
    """
    doc = docx_path if isinstance(docx_path, DocxDocument) else Document(str(docx_path))

    if normalize_id is None:
        from fismatic.parser import normalize_control_id as normalize_id

    def match_key(control_id: str | None) -> str | None:
        return _match_key(normalize_id(control_id)) if control_id else None

    anchors = find_control_tables(doc)
    anchors_by_id: dict[str, list[ControlTableAnchor]] = {}
    for a in anchors:
        norm = match_key(a.control_id)
        if norm:
            anchors_by_id.setdefault(norm, []).append(a)

    duplicate_table_ids = sorted(
        anchors_for_id[0].control_id
        for anchors_for_id in anchors_by_id.values()
        if len(anchors_for_id) > 1
    )

    controls_by_key: dict[str, list[str]] = {}
    for control in controls:
        norm = match_key(control.control_id)
        if norm:
            controls_by_key.setdefault(norm, []).append(control.control_id)
    source_id_collisions = {
        key: ids for key, ids in controls_by_key.items() if len(set(ids)) > 1
    }
    if source_id_collisions:
        collisions = ", ".join(
            f"{key}: {', '.join(ids)}" for key, ids in source_id_collisions.items()
        )
        raise ValueError(f"source control ID normalization collisions: {collisions}")

    filled: list[str] = []
    filled_table_count = 0
    unmatched_controls: list[str] = []
    control_results: list[dict] = []
    for control in controls:
        norm = match_key(control.control_id)
        matching_anchors = anchors_by_id.get(norm, []) if norm else []
        if not matching_anchors:
            unmatched_controls.append(control.control_id)
            control_results.append(
                {
                    "source_control_id": control.control_id,
                    "normalized_control_id": norm,
                    "target_tables": [],
                    "statements": [],
                    "whole_control": None,
                }
            )
            continue

        status_key = None
        if control.oscal_implementation_status:
            status_key = OSCAL_STATE_TO_KEY.get(control.oscal_implementation_status)
        origination_keys = {
            OSCAL_ORIGINATION_TO_KEY[v]
            for v in (control.oscal_origination or [])
            if v in OSCAL_ORIGINATION_TO_KEY
        }

        if statement_text_by_control:
            texts = statement_text_by_control.get(control.control_id, {})
            control._statement_text_by_id = texts  # type: ignore[attr-defined]
        target_tables = []
        statement_results = []
        whole_control: dict[str, str | bool | None] | None = None
        for anchor in matching_anchors:
            _set_checkbox_lines(
                anchor.status_cell.paragraphs,
                {status_key} if status_key else set(),
                STATUS_KEY_PATTERNS,
            )
            if anchor.origination_cell is not None:
                _set_checkbox_lines(
                    anchor.origination_cell.paragraphs,
                    origination_keys,
                    ORIGINATION_KEY_PATTERNS,
                )
            provider_label = (origination_providers_by_control or {}).get(
                control.control_id, ""
            )
            if provider_label and anchor.origination_cell is not None:
                _replace_origination_tokens(anchor.origination_cell, provider_label)
            statement_results.extend(_fill_narrative(anchor, control))
            whole_text = (whole_control_text_by_control or {}).get(control.control_id)
            if whole_text and not control.statement_ids:
                result = _fill_whole_control(anchor, whole_text)
                if whole_control is not None:
                    result["written"] = bool(
                        result["written"] and whole_control["written"]
                    )
                whole_control = result
            target_tables.append(
                {
                    "table_index": anchor.table_index,
                    "raw_control_id": anchor.control_id,
                    "normalized_control_id": match_key(anchor.control_id),
                }
            )
            filled_table_count += 1

        filled.append(control.control_id)
        control_results.append(
            {
                "source_control_id": control.control_id,
                "normalized_control_id": norm,
                "target_tables": target_tables,
                "statements": statement_results,
                "whole_control": whole_control,
            }
        )

    matched_norms = {
        match_key(c.control_id) for c in controls if match_key(c.control_id)
    }
    unmatched_tables = [
        anchor.control_id
        for norm, anchors_for_id in anchors_by_id.items()
        if norm not in matched_norms
        for anchor in anchors_for_id
    ]

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))

    return ControlFillResult(
        output_path=out,
        filled_control_ids=filled,
        filled_table_count=filled_table_count,
        unmatched_control_ids=unmatched_controls,
        unmatched_table_ids=unmatched_tables,
        duplicate_table_ids=duplicate_table_ids,
        source_id_collisions=source_id_collisions,
        control_results=control_results,
    )
