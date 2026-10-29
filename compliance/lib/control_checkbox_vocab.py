"""Shared FedRAMP SSP control-table checkbox vocabulary (Phase R17).

Single source of truth for two things both the read path
(``fismatic.parser``) and the write path
(``compliance.lib.docx_generator``, ``compliance.lib.control_table_fill``)
need to agree on bit-for-bit:

1. The checkbox glyphs themselves (:data:`CHECKED_GLYPH`,
   :data:`UNCHECKED_GLYPH`, :data:`CHECKBOX_GLYPHS`).
2. The OSCAL vocabulary <-> internal flag-key mappings
   (:data:`OSCAL_STATE_TO_KEY`, :data:`OSCAL_ORIGINATION_TO_KEY`), which are
   the exact inverse of ``fismatic.parser._status_to_oscal_state`` /
   ``_origination_to_oscal_list``.

Deliberately does **not** hardcode the checkbox *label text* (e.g.
"Configured by Customer"). The official GSA blank templates do not all offer
the same subset of checkboxes per block (e.g. the Low/Moderate/High
Implementation Status block has no "Configured by Customer" option at all,
even though ``fismatic.parser`` recognizes that state when reading), so
label-set assumptions baked into a fixed list silently mismatch the real
templates. Instead, ``control_table_fill.py`` matches each checkbox
paragraph's own text, live, against ``fismatic.parser``'s regexes
(``_STATUS_KEYWORDS`` / ``_ORIGINATION_KEYWORDS``) -- the same regexes that
read the box back -- guaranteeing the write and read directions can never
drift apart on *which labels exist*, only on *whether a box is checked*.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Implementation Status
# ---------------------------------------------------------------------------

#: OSCAL ``implementation-status.state`` value -> internal flag key.
#: Mirrors ``fismatic.parser._status_to_oscal_state`` (inverse direction).
#: The internal flag keys match ``fismatic.parser._STATUS_KEYWORDS``'s first
#: tuple element exactly, so a key returned by matching a checkbox
#: paragraph's text against those regexes can be looked up here directly.
OSCAL_STATE_TO_KEY = {
    "implemented": "implemented",
    "partially-implemented": "partially_implemented",
    "planned": "planned",
    "alternative-implementation": "alternative_implementation",
    "customer-configured": "configured_by_customer",
    "not-applicable": "not_applicable",
    # "not-implemented" has no explicit checkbox -- it's the implicit
    # fallback when none of the above are checked.
}

# ---------------------------------------------------------------------------
# Control Origination
# ---------------------------------------------------------------------------

#: OSCAL ``control-origination[]`` value -> internal flag key. Mirrors
#: ``fismatic.parser._origination_to_oscal_list`` (inverse direction) and
#: matches ``fismatic.parser._ORIGINATION_KEYWORDS``'s first tuple element.
OSCAL_ORIGINATION_TO_KEY = {
    "inherited": "inherited",
    "hybrid": "hybrid",
    "system-specific": "system_specific",
    "service-provider-corporate": "service_provider_corporate",
    "customer-configured": "configured_by_customer",
    "customer-provided": "provided_by_customer",
    "shared": "shared",
}

# ---------------------------------------------------------------------------
# Label-matching regexes (write-direction mirror of the read-direction regexes)
# ---------------------------------------------------------------------------
#
# These are a deliberate textual mirror of
# ``fismatic.parser._STATUS_KEYWORDS`` / ``_ORIGINATION_KEYWORDS`` -- the
# same mirroring convention already used by
# ``compliance/lib/docx_generator.py``'s ``_STATUS_LINES`` /
# ``_ORIGINATION_LINES`` (see its module docstring: "Order mirrors
# fismatic.parser._STATUS_KEYWORDS"). Duplicated here (not imported from
# fismatic, a separate sub-package with independent versioning) so
# ``control_table_fill.py`` can identify which checkbox *paragraph* in an
# **existing** blank template corresponds to which flag key, matching the
# checkbox's own printed label text rather than assuming a fixed row order
# (the official templates do not all offer the same subset of checkboxes;
# see the module docstring above).
#
# If ``fismatic.parser``'s patterns change, update both this list and
# ``docx_generator.py``'s mirror in the same commit (see
# ``docs/PARSING_ACCURACY_NOTES.md`` "Common fix patterns").

#: ``(key, pattern)`` tested via ``re.match(pattern, label, re.IGNORECASE)``
#: against a checkbox line's label text (the line with its glyph stripped).
STATUS_KEY_PATTERNS = [
    ("implemented", r"^implemented\b"),
    ("partially_implemented", r"^partially\s+implemented\b"),
    ("planned", r"^planned\b"),
    ("alternative_implementation", r"^alternative\s+implementation\b"),
    ("configured_by_customer", r"^configured\s+by\s+customer\b"),
    ("not_applicable", r"^not\s+applicable\b"),
]

#: ``(key, pattern)`` tested via ``re.search(pattern, label, re.IGNORECASE)``
#: against a checkbox line's label text with any bracketed/parenthesized
#: placeholder detail removed (mirrors
#: ``fismatic.parser.parse_control_origination``'s ``without_parens`` step).
ORIGINATION_KEY_PATTERNS = [
    ("inherited", r"^inherited\s+from"),
    ("hybrid", r"^(?:hybrid\s+control|service\s+provider\s+hybrid)"),
    (
        "system_specific",
        r"^(?:system\s+specific\s+control|service\s+provider\s+system\s+specific)",
    ),
    (
        "service_provider_corporate",
        r"(?:common\s+control|service\s+provider\s+corporate)",
    ),
    ("configured_by_customer", r"^configured\s+by\s+customer"),
    ("provided_by_customer", r"^provided\s+by\s+customer"),
    ("shared", r"^shared\b"),
]

#: Checkbox glyphs. ``CHECKED`` is what this pipeline *writes*, matching
#: ``docx_generator.py``'s prior convention and what real filled FedRAMP
#: SSPs use (for example, a real Low SSP corpus fixture). ``UNCHECKED_SET``
#: covers every glyph the *read* path (``fismatic.parser``) and the blank
#: templates themselves are known to use, so a checkbox run can always be
#: recognized regardless of which unchecked variant is present in the
#: source template.
CHECKED_GLYPH = "\u2612"  # ☒ BALLOT BOX WITH X
UNCHECKED_GLYPH = "\u2610"  # ☐ BALLOT BOX

#: Every glyph a checkbox *run* might carry, checked or unchecked, across
#: the official templates and real-world filled SSPs (see
#: ``docs/PARSING_ACCURACY_NOTES.md``).
CHECKBOX_GLYPHS = frozenset(
    {
        CHECKED_GLYPH,
        UNCHECKED_GLYPH,
        "\u2611",  # ☑ BALLOT BOX WITH CHECK
        "\u2713",  # ✓ CHECK MARK
        "\u22a0",  # ⊠ SQUARED TIMES
    }
)

#: Glyphs that mean "checked" when encountered on read (superset of what we
#: write, since real-world documents use several checked variants).
CHECKED_GLYPHS = CHECKBOX_GLYPHS - {UNCHECKED_GLYPH}
