"""Fill a jinjafied SSP template with content and verify roundtrip parity
(Phase R16.4).

The fill stage is the inverse of extraction: it takes a docxtpl template
produced by :mod:`compliance.lib.docx_jinjafier`, a context dict keyed by the
contract's ``templateTag`` names, and renders a filled ``.docx``.

For validation it also provides :func:`reextract_filled` — read the filled
values back out of the rendered document by locating each template tag's slot
(via the same instruction-anchor pass + contract) — so a test can assert that
every value written is recovered (roundtrip content parity, plan §7).

References
----------
- ``compliance/lib/docx_jinjafier.py`` (produces the template)
- ``compliance/lib/instruction_anchors.py`` (locates slots for re-extraction)
- ``compliance/lib/heading_parser.py`` (contract)
- ``archive/plans/ROUNDTRIP_TEMPLATE_PLAN.md`` §7
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

from docx import Document
from docx.table import Table
from docxtpl import DocxTemplate

from compliance.lib.docx_jinjafier import resolve_bindings
from compliance.lib.heading_parser import Contract, load_contract
from compliance.lib.instruction_anchors import find_instruction_anchors


@dataclass
class FillResult:
    """Outcome of a fill run."""

    output_path: Path
    filled_tags: List[str] = field(default_factory=list)
    missing_tags: List[str] = field(default_factory=list)


@dataclass
class ParityResult:
    """Outcome of a roundtrip parity check."""

    matched: Dict[str, str] = field(default_factory=dict)
    mismatched: Dict[str, Dict[str, str]] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatched and not self.missing

    @property
    def recovery_rate(self) -> float:
        total = len(self.matched) + len(self.mismatched) + len(self.missing)
        return (len(self.matched) / total) if total else 1.0


@dataclass
class StatementTupleParityResult:
    """Parity result for normalized statement content tuples (#47)."""

    matched: List[Tuple] = field(default_factory=list)
    missing: List[Tuple] = field(default_factory=list)
    mismatched: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_exact(self) -> bool:
        return not self.missing and not self.mismatched


def check_statement_tuple_parity(
    expected: List[Tuple[str, str, str, Any, Any]],
    actual: List[Tuple[str, str, str, Any, Any]],
) -> StatementTupleParityResult:
    """Compare normalized statement tuples: (control_id, statement_part_id, normalized_text, parameters, provenance) (#47)."""

    def _to_key(item: Tuple) -> Tuple:
        cid, part_id, text, params, prov = item
        p_tup = (
            tuple(sorted(params))
            if isinstance(params, (list, tuple, set))
            else (str(params),)
        )
        return (
            str(cid).strip().lower(),
            str(part_id).strip().lower(),
            _normalize(str(text)),
            p_tup,
            str(prov) if prov is not None else None,
        )

    exp_map = {_to_key(x)[:2]: _to_key(x) for x in expected}
    act_map = {_to_key(x)[:2]: _to_key(x) for x in actual}

    res = StatementTupleParityResult()
    for (cid, pid), exp_tup in exp_map.items():
        if (cid, pid) not in act_map:
            res.missing.append(exp_tup)
        else:
            act_tup = act_map[(cid, pid)]
            if exp_tup == act_tup:
                res.matched.append(exp_tup)
            else:
                res.mismatched.append({"expected": exp_tup, "actual": act_tup})
    return res


def _template_tags(contract: Contract) -> List[str]:
    tags: List[str] = []
    for obj in contract.destinations():
        for p in obj.properties:
            if p.template_tag:
                tags.append(p.template_tag)
    return tags


SYNTHETIC_TEMPLATE_SENTINEL = "[SYNTHETIC-SSPP-TEMPLATE-TEST]"


def synthetic_fill_context(contract: Union[str, Path, Contract]) -> Dict[str, str]:
    """Return a deterministic non-production value for every declared template tag.

    This is intentionally a renderer-test helper, not parser output. Blank
    templates remain blank when parsed; tests use these conspicuous values to
    prove that each contract slot can be filled and re-extracted.
    """
    ctr = contract if isinstance(contract, Contract) else load_contract(contract)
    return {tag: f"{SYNTHETIC_TEMPLATE_SENTINEL}:{tag}" for tag in _template_tags(ctr)}


def load_fill_context(fixture_path: Union[str, Path]) -> Dict[str, Any]:
    """Load a fill-content fixture JSON, dropping the ``_meta`` block."""
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def fill_template(
    jinja_docx: Union[str, Path],
    context: Dict[str, Any],
    output_path: Union[str, Path],
    contract: Union[str, Path, Contract, None] = None,
) -> FillResult:
    """Render a jinjafied template with ``context`` to a filled ``.docx``.

    Parameters
    ----------
    jinja_docx:
        Path to a template produced by the jinjafier.
    context:
        Mapping of ``templateTag`` -> value.
    output_path:
        Where to write the rendered ``.docx``.
    contract:
        Optional contract (path or loaded) used to report which of its
        declared tags were / were not supplied in ``context``, and to scope
        ``filled_tags`` to tags the template actually consumes (extra
        context keys the template doesn't reference are not "filled").
    """
    tpl = DocxTemplate(str(jinja_docx))
    tpl.render(context, autoescape=True)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tpl.save(str(out))

    missing: List[str] = []
    if contract is not None:
        ctr = contract if isinstance(contract, Contract) else load_contract(contract)
        declared = _template_tags(ctr)
        filled = [t for t in declared if t in context]
        missing = [t for t in declared if t not in context]
    else:
        filled = list(context.keys())

    return FillResult(output_path=out, filled_tags=filled, missing_tags=missing)


def _instruction_config_from_contract(contract: Contract):
    """Return (colors, prefixes) from the contract source object, or (None, None)."""
    colors = None
    prefixes = None
    for src_id in contract.source_object_ids:
        for raw_obj in contract.raw.get("schema", []):
            if (raw_obj.get("id") or raw_obj.get("name")) != src_id:
                continue
            for cp in raw_obj.get("customProperties") or []:
                if cp.get("property") == "instructionColors" and cp.get("value"):
                    colors = [
                        c.strip() for c in str(cp["value"]).split(",") if c.strip()
                    ]
                if cp.get("property") == "instructionTextPrefixes" and cp.get("value"):
                    prefixes = [
                        c.strip() for c in str(cp["value"]).split(",") if c.strip()
                    ]
    return colors, prefixes


def reextract_filled(
    filled_docx: Union[str, Path],
    contract: Union[str, Path, Contract],
    *,
    strict: bool = False,
) -> Dict[str, str]:
    """Read back the filled value for each templateTag from a rendered .docx.

    Re-runs the instruction-anchor pass against the *filled* document and,
    for each contract write-back property, returns the text of the paragraph
    or table slot immediately after its instruction anchor. This mirrors exactly
    how the jinjafier chose the slot, so a value written there is recovered here.
    """
    ctr = contract if isinstance(contract, Contract) else load_contract(contract)
    doc = Document(str(filled_docx))
    colors, prefixes = _instruction_config_from_contract(ctr)
    anchors = find_instruction_anchors(
        doc, instruction_colors=colors, instruction_text_prefixes=prefixes
    )
    bindings, unmatched, _warnings = resolve_bindings(anchors, ctr)

    if strict and unmatched:
        raise ValueError(
            f"Strict re-extraction failed: {len(unmatched)} unmatched anchor(s)"
        )

    out: Dict[str, str] = {}
    for b in bindings:
        slot = b.anchor.slot_ref
        if slot is None:
            if strict:
                raise ValueError(
                    f"Binding {b.template_tag} has null slot reference in strict mode"
                )
            continue
        if isinstance(slot, Table):
            # Table slot: extract all cell text
            cell_texts = [
                cell.text.strip()
                for row in slot.rows
                for cell in row.cells
                if cell.text.strip()
            ]
            text = " ".join(cell_texts)
            if strict and not text.strip():
                raise ValueError(
                    f"Binding {b.template_tag} has an empty table slot in strict mode"
                )
            out[b.template_tag] = text.strip()
        else:
            text = getattr(slot, "text", "") or ""
            out[b.template_tag] = text.strip()
    return out


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def check_parity(
    context: Dict[str, Any],
    recovered: Dict[str, str],
) -> ParityResult:
    """Compare written context values against re-extracted values.

    A written value matches if it is recovered as a substring of the slot text
    (append writeScope keeps the template sentence + appended value), compared
    after whitespace normalization.
    """
    result = ParityResult()
    for tag, written in context.items():
        written_norm = _normalize(str(written))
        if tag not in recovered:
            result.missing.append(tag)
            continue
        got = _normalize(recovered[tag])
        if written_norm and written_norm in got:
            result.matched[tag] = recovered[tag]
        else:
            result.mismatched[tag] = {
                "written": str(written),
                "recovered": recovered[tag],
            }
    return result


def roundtrip(
    jinja_docx: Union[str, Path],
    fixture_path: Union[str, Path],
    contract: Union[str, Path, Contract],
    output_path: Union[str, Path],
) -> ParityResult:
    """Full fill -> re-extract -> parity check convenience wrapper."""
    ctr = contract if isinstance(contract, Contract) else load_contract(contract)
    context = load_fill_context(fixture_path)
    fill_template(jinja_docx, context, output_path, contract=ctr)
    recovered = reextract_filled(output_path, ctr)
    return check_parity(context, recovered)
