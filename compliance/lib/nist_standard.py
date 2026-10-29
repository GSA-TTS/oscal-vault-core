"""CycloneDX 2.0 ``definitions.standards`` entry for NIST SP 800-53 Rev. 5.

A CycloneDX 2.0 control may only reference a requirement by ``bom-ref``; an object such as
``{"reference": "AC-2", "framework": "nist-800-53-r5"}`` is rejected by threat-model importers.
This module builds the standard once, with one requirement per cited control, so a control's
``satisfies`` can list the requirement's ``bom-ref`` and show the catalog text.

The statement text comes from the vendored NIST catalog extract (``compliance/data``). Catalog
parameter placeholders are rendered as ``[Assignment: <label>]``; this module never invents an
organization-defined value (the SSP's own values belong on the control, not on the standard).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

STANDARD_REF = "std-nist-800-53-r5"
STANDARD_NAME = "NIST SP 800-53 Rev. 5"
_DATA = Path(__file__).resolve().parents[1] / "data"
_PLACEHOLDER = re.compile(r"\{\{\s*insert:\s*param,\s*([^}\s]+)\s*\}\}")
_ID = re.compile(r"^([A-Za-z]{2})-(\d+)(?:\(([^)]+)\)|\.(\w+))?$")


@lru_cache(maxsize=None)
def _load(name: str) -> dict[str, Any]:
    try:
        return json.loads((_DATA / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def catalog_key(control_id: str) -> str:
    """``AC-02(01)`` / ``AC-2(1)`` / ``ac-2.1`` -> ``ac-2.1`` (the catalog's key form)."""
    text = re.sub(r"\s+", "", control_id or "")
    match = _ID.match(text)
    if not match:
        return text.lower()
    family, number, paren, dotted = match.groups()
    enhancement = paren or dotted
    key = f"{family.lower()}-{int(number)}"
    if enhancement is None:
        return key
    return f"{key}.{int(enhancement) if enhancement.isdigit() else enhancement.lower()}"


def display_id(control_id: str) -> str:
    """``ac-2.1`` or ``AC-02(01)`` -> ``AC-2(1)``: the identifier as the standard prints it."""
    key = catalog_key(control_id)
    base, _, enhancement = key.partition(".")
    return base.upper() + (f"({enhancement})" if enhancement else "")


def requirement_ref(control_id: str) -> str:
    return "req-nist-" + re.sub(r"[^a-z0-9]+", "-", catalog_key(control_id)).strip("-")


def _render(line: str) -> str:
    labels = _load("nist_sp800-53_rev5_param_labels.json")

    def sub(match: re.Match[str]) -> str:
        pid = match.group(1)
        return f"[Assignment: {labels.get(pid) or pid}]"

    text = _PLACEHOLDER.sub(sub, line)
    return re.sub(r"[ \t]+([,;:.!?])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()


def requirement(control_id: str) -> dict[str, Any]:
    key = catalog_key(control_id)
    lines = _load("nist_sp800-53_rev5_control_statements.json").get(key) or []
    names = _load("nist_sp800-53_rev5_control_names.json")
    identifier = display_id(control_id)
    entry: dict[str, Any] = {
        "bom-ref": requirement_ref(control_id),
        "identifier": identifier,
        "title": f"{identifier} {names[key]}" if key in names else identifier,
    }
    # No catalog text is better than invented text: a missing control keeps only its title.
    if lines:
        entry["text"] = "\n".join(_render(line) for line in lines)
    return entry


def build_nist_standard(control_ids: Iterable[str]) -> dict[str, Any]:
    """The ``definitions.standards`` entry covering ``control_ids`` (deduplicated, sorted)."""
    by_ref = {requirement_ref(c): requirement(c) for c in control_ids if c}

    def order(item: dict[str, Any]) -> tuple[str, int, str]:
        fam, _, rest = catalog_key(item["identifier"]).partition("-")
        num, _, enh = rest.partition(".")
        return fam, int(num) if num.isdigit() else 0, enh.zfill(3)

    return {
        "bom-ref": STANDARD_REF,
        "name": STANDARD_NAME,
        "version": "5",
        "owner": "NIST",
        "requirements": sorted(by_ref.values(), key=order),
    }


def link_controls(document: dict[str, Any]) -> int:
    """Rewrite object-style ``satisfies`` into requirement ``bom-ref`` strings.

    Adds one standard to ``document['definitions']['standards']`` covering every NIST
    control cited, and replaces ``{"reference", "framework": "nist-800-53-r5"}`` entries with
    the matching requirement's ``bom-ref``. Entries of any other shape are left alone.
    Returns the number of ``satisfies`` entries rewritten; 0 leaves the document untouched.
    """
    cited: list[str] = []
    for control in document.get("controls") or []:
        for item in control.get("satisfies") or []:
            if isinstance(item, dict) and item.get("framework") == "nist-800-53-r5":
                cited.append(str(item.get("reference") or ""))
    if not [c for c in cited if c]:
        return 0

    definitions = document.setdefault("definitions", {})
    standards = [
        s
        for s in definitions.get("standards") or []
        if s.get("bom-ref") != STANDARD_REF
    ]
    standards.append(build_nist_standard(cited))
    definitions["standards"] = standards

    rewritten = 0
    for control in document.get("controls") or []:
        refs = []
        for item in control.get("satisfies") or []:
            if isinstance(item, dict) and item.get("framework") == "nist-800-53-r5":
                refs.append(requirement_ref(str(item.get("reference") or "")))
                rewritten += 1
            else:
                refs.append(item)
        if "satisfies" in control:
            control["satisfies"] = list(dict.fromkeys(refs))
    return rewritten
