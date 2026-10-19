"""Pure builders for the Obsidian vault representation of an OSCAL SSP.

Reads an OSCAL SSP dict (the shape produced by
`fismatic.ssp_converter.SSPConverter.get_oscal_data()` -- a top-level
`system-security-plan` object, or the inner document directly) and produces
in-memory `VaultNote` records: each one is a (relative_path, frontmatter dict,
markdown body string) triple. `compliance/scripts/export_obsidian_vault.py`
takes these and writes a directory tree to disk.

This module deliberately contains no I/O of its own so it can be unit-tested
with a synthetic SSP dict (see `compliance/tests/test_export_obsidian_vault.py`).

The vault schema (note types, frontmatter fields, controlled vocabularies,
Dataview queries) is documented in `docs/OBSIDIAN_VAULT_SCHEMA.md` and
`archive/plans/PROJECT_REFACTORING_PLAN.md`. This module is the programmatic
codification of that schema.

Type taxonomy follows the OSCAL + OKF convention:
  - `oscal.*` = canonical OSCAL model concepts
  - `oscal.x.*` = project extensions beyond NIST OSCAL
  - `okf.*` = vault governance infrastructure types

Notes on the source SSP shape:
  - fismatic's `SSPConverter` only emits a single stub `metadata.parties`
    entry (`Cloud Service Provider`) and the `system-characteristics` block;
    it does NOT emit separate component definitions, evidence, or assessment
    results. This generator therefore emits only System, Organization, Plan,
    and Control notes from the SSP, plus a single synthetic Component note
    derived from the system itself (so the System note's `components:` ref
    is non-empty and the graph view has something to show). When richer SSP
    inputs are available (e.g. produced by `compliance/lib/docx_generator.py`
    or hand-authored), Section 5 of the schema's "Pipeline -> Vault generation"
    notes the additional component mapping.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from . import vocab
from .table_records import table_projection

# ---------------------------------------------------------------------------
# Naming / slug helpers
# ---------------------------------------------------------------------------

# Obsidian note filenames are a bit more lenient than URL slugs but should be
# filesystem-safe and stable. We keep them lowercase, kebab-cased, and scrub
# of characters that collide with Obsidian's wikilink syntax or Windows paths.
_UNSAFE_FOR_FILENAME = re.compile(r"[^a-z0-9._-]+")
# Role-id text from fismatic often contains entire paragraphs of narrative
# (the parser dumps the full "Responsible Role" cell content). Extract just
# the first meaningful line or name for display and matching.
_ROLE_FIRST_LINE = re.compile(r"^([^\n:]+?)(?:\s*:.*)?$", re.MULTILINE)


def _clean_role_text(rid: str) -> str:
    """Extract a human-facing name from a fismatic role-id string.

    fismatic's parser currently emits the entire `Responsible Role` cell
    content as a single `role-id`, which can be many paragraphs of prose.
    This helper distills it into the first short segment, suitable for
    display in vault frontmatter.
    """
    if not rid:
        return ""
    # Take the first line before a newline, and strip trailing colons.
    first = rid.split("\n", 1)[0].strip()
    # If it's still very long (>80 chars), truncate with ellipsis.
    if len(first) > 80:
        first = first[:77] + "..."
    return first.rstrip(":")


def _build_role_to_party_index(ssp_root: dict) -> dict:
    """Build a lookup from role-id -> party name (from metadata.roles and
    metadata.parties)."""
    idx = {}
    metadata = ssp_root.get("metadata") or {}
    parties = metadata.get("parties") or []
    for party in parties:
        pname = (party.get("name") or "").strip()
        for rid in party.get("role-ids") or []:
            idx[rid.strip()] = pname
        puuid = party.get("uuid")
        if puuid:
            idx[str(puuid)] = pname
    return idx


def _resolve_role_to_org(rid, role_index, org_links):
    """Resolve a raw fismatic role-id to an Organization wikilink."""
    rid_clean = rid.strip()
    if not rid_clean:
        return None
    # Exact match
    if rid_clean in role_index:
        name = role_index[rid_clean]
        return org_links.get(name)
    # Substring match
    rid_lower = rid_clean.lower()
    for name, link in org_links.items():
        if name.lower() in rid_lower:
            return link
    # Short name heuristic
    short = _clean_role_text(rid_clean)
    if short and len(short) <= 60 and "\n" not in short:
        return org_links.get(short)
    return None


def slugify(text: str) -> str:
    """Render free-form text as a stable, filesystem-safe Obsidian note slug."""
    text = (text or "").strip().lower()
    text = text.replace("&", " and ").replace("/", " or ")
    slug = _UNSAFE_FOR_FILENAME.sub("-", text).strip("-")
    return slug or "untitled"


def _control_slug(control_id: str) -> str:
    """Filename stem for a Controls note, e.g. 'AC-2 (1)'. The caller
    (typically `_note_path`) adds the `.md` suffix."""
    raw = (control_id or "").strip()
    if not raw:
        return "untitled"
    # Normalize whitespace; leave parentheses intact (enhancement).
    return re.sub(r"\s+", " ", raw)


def _control_wikilink(control_id: str) -> str:
    """`[[Controls/AC-1|AC-1]]` wikilink. Path relative to vault root."""
    cid = (control_id or "").strip()
    return f"[[Controls/{cid}|{cid}]]"


def _note_path(dir_name: str, slug: str) -> str:
    """Filesystem relpath for a note (e.g. 'Controls/AC-1.md')."""
    return f"{dir_name}/{slug}.md"


def _wl(dir_name: str, slug: str) -> str:
    """Obsidian wikilink form, e.g. '[[Controls/AC-1]]' (no .md extension).

    Obsidian resolves wikilinks against note names regardless of extension,
    but the schema in docs/OBSIDIAN_VAULT_SCHEMA.md uses the extension-less
    shape, so we follow that convention here.
    """
    return f"[[{dir_name}/{slug}]]"


# ---------------------------------------------------------------------------
# Frontmatter serialization
# ---------------------------------------------------------------------------


class _Literal(str):
    """A string that PyYAML should emit unquoted (used for `[[wikilinks]]`).

    We tag wikilink values so the dumper writes them as a bare scalar rather
    than wrapping them in quotes, since YAML quotes are fine for parsing but
    look ugly and obscure the link shape for humans reading the frontmatter.
    """


def _wikilink(target: str) -> _Literal:
    return _Literal(f"[[{target}]]")


def _wikilink_repr(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data))


yaml.add_representer(_Literal, _wikilink_repr, Dumper=yaml.SafeDumper)


def dump_frontmatter(fm: Dict[str, Any]) -> str:
    """Render a note's frontmatter dict as the `--- ... ---` YAML block."""
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n"


# ---------------------------------------------------------------------------
# VaultNote dataclass
# ---------------------------------------------------------------------------


@dataclass
class VaultNote:
    relpath: str  # e.g. "Controls/AC-1.md"
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    body: str = ""  # markdown body (without frontmatter), without leading newline
    # Binary attachment payload. When set, this note is an asset (e.g. an
    # extracted diagram image) written verbatim to disk in binary mode instead
    # of a rendered markdown note. `frontmatter`/`body` are ignored for assets.
    data: Optional[bytes] = None
    # When True, `body` is written verbatim with no frontmatter block. Used for
    # non-markdown vault files that are pure YAML/text (e.g. Obsidian Bases
    # `.base` views, ADR-025).
    raw: bool = False

    @property
    def is_binary(self) -> bool:
        return self.data is not None

    def render(self) -> str:
        if self.raw:
            return self.body
        return dump_frontmatter(self.frontmatter) + (
            "\n" + self.body if self.body else ""
        )


# ---------------------------------------------------------------------------
# Cross-reference / wikilink target index
# ---------------------------------------------------------------------------


@dataclass
class VaultIndex:
    """A lookup table used to resolve UUIDs and bare names into wikilinks.

    Populated in the same pass that builds the note set. The keys are stable
    identifiers carried in the SSP (party UUIDs, component UUIDs, control
    IDs, system-name); values are `[[Path/Slugged-name]]` strings.

    The generator is run as a single pass per SSP, so this index is built
    incrementally and never needs to be persisted.
    """

    systems: Dict[str, str] = field(default_factory=dict)  # keyed by system-name
    components: Dict[str, str] = field(
        default_factory=dict
    )  # keyed by component UUID or name
    controls: Dict[str, str] = field(default_factory=dict)  # keyed by control-id
    organizations: Dict[str, str] = field(default_factory=dict)  # keyed by party UUID
    plans: Dict[str, str] = field(default_factory=dict)  # keyed by plan_id
    media: Dict[str, str] = field(
        default_factory=dict
    )  # keyed by image rId -> attachment filename (Diagrams/media/<fname>)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _today() -> str:
    return datetime.date.today().isoformat()


def _split_control_id(control_id: str) -> Dict[str, Any]:
    """Split a control_id like 'AC-1' or 'AC-2 (1)' into the schema's fields."""
    text = control_id.strip()
    m = re.match(r"^([A-Za-z]{2})-(\d+)(?:\s*\(([^)]+)\))?$", text)
    if not m:
        return {"family": "", "enhancement": None}
    family = m.group(1).upper()
    enhancement = m.group(3) if m.group(3) is not None else None
    # Try to coerce enhancement to an int for cleaner YAML when numeric.
    if enhancement is not None and enhancement.isdigit():
        enhancement_val: Any = int(enhancement)
    else:
        enhancement_val = enhancement
    return {"family": family, "enhancement": enhancement_val}


def canonical_control_id(control_id: str) -> str:
    """Return a single canonical string form for a control id.

    Source SSPs write the same control inconsistently — ``AC-2``, ``AC-02``,
    ``IA-02(2)``, ``IA-02(01)``, ``AT-02(02)``. That inconsistency fragments
    filenames, wikilinks, and cross-SSP joins, and produces broken
    ``[[Controls/AC-2]]`` links against a zero-padded ``AC-02.md`` file. This
    normalizes to a stable ``<FAMILY>-<NN>`` base with a zero-padded 2-digit
    number, and ``(NN)`` zero-padded enhancements:

        AC-2      -> AC-02
        AC-02     -> AC-02
        IA-2 (12) -> IA-02(12)
        IA-02(2)  -> IA-02(02)
        AT-02(02) -> AT-02(02)

    Non-numeric / unrecognized ids are returned trimmed but otherwise
    unchanged (fail-open: never lose an id we can't parse).
    """
    text = re.sub(r"\s+", "", (control_id or "").strip())
    m = re.match(r"^([A-Za-z]{2})-(\d+)(?:\(([^)]+)\))?$", text)
    if not m:
        return (control_id or "").strip()
    family = m.group(1).upper()
    number = int(m.group(2))
    base = f"{family}-{number:02d}"
    enh = m.group(3)
    if enh is None:
        return base
    # Zero-pad numeric enhancements; leave alpha enhancements as-is.
    if enh.isdigit():
        return f"{base}({int(enh):02d})"
    return f"{base}({enh})"


# The 20 NIST SP 800-53 rev5 control families. Used to gate the related-control
# heuristic so that non-control identifiers that merely look like a control id
# (e.g. the "IT-18" inside directive "CIO-IT-18-90") don't yield dangling links.
NIST_CONTROL_FAMILIES = frozenset(
    {
        "AC",
        "AT",
        "AU",
        "CA",
        "CM",
        "CP",
        "IA",
        "IR",
        "MA",
        "MP",
        "PE",
        "PL",
        "PM",
        "PS",
        "PT",
        "RA",
        "SA",
        "SC",
        "SI",
        "SR",
    }
)


# --- NIST SP 800-53 rev5 control-name lookup ------------------------------
# Vendored map of OSCAL control-id (e.g. "ac-2", "ac-2.1") -> human title
# Used to give control notes a real title instead of a fabricated stub.
_CONTROL_NAMES_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "nist_sp800-53_rev5_control_names.json"
)


@lru_cache(maxsize=1)
def _control_names() -> Dict[str, str]:
    try:
        with open(_CONTROL_NAMES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# Vendored map of OSCAL control-id -> ordered list of statement-prose lines,
# derived from the NIST SP 800-53 rev5 catalog. Populates each control note's
# `# Upstream` section with the authoritative catalog language.
_CONTROL_STATEMENTS_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "nist_sp800-53_rev5_control_statements.json"
)


@lru_cache(maxsize=1)
def _control_statements() -> Dict[str, List[str]]:
    try:
        with open(_CONTROL_STATEMENTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# Vendored map of OSCAL parameter-id -> catalog label (e.g.
# "organization-defined personnel or roles"), used to render a readable
# assignment marker when the SSP did not supply a value for a placeholder.
_PARAM_LABELS_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "nist_sp800-53_rev5_param_labels.json"
)


@lru_cache(maxsize=1)
def _param_labels() -> Dict[str, str]:
    try:
        with open(_PARAM_LABELS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# `{{ insert: param, ac-07_odp.01 }}` placeholders in catalog prose.
_ODP_PLACEHOLDER_RE = re.compile(r"\{\{\s*insert:\s*param,\s*([^}\s]+)\s*\}\}")


def _param_id_variants(pid: str) -> List[str]:
    """Candidate SSP param-id spellings for a catalog placeholder id.

    The catalog and SSP disagree on zero-padding of the control number
    (``ac-7_odp.01`` vs ``ac-07_odp.01``), so try both forms.
    """
    variants = [pid]
    m = re.match(r"^([a-z]{2})-(\d+)(.*)$", pid)
    if m:
        fam, num, rest = m.group(1), m.group(2), m.group(3)
        padded = f"{fam}-{int(num):02d}{rest}"
        unpadded = f"{fam}-{int(num)}{rest}"
        for v in (padded, unpadded):
            if v not in variants:
                variants.append(v)
    return variants


def _fill_catalog_prose(line: str, param_values: Dict[str, str]) -> str:
    """Replace ``{{ insert: param, <id> }}`` with the SSP's ODP value.

    Falls back to the catalog's descriptive ``[Assignment: <label>]`` marker
    (or the bare id when no label is vendored) when the SSP did not supply a
    value, matching the OSCAL assignment-operation convention.
    """

    def _sub(m: "re.Match[str]") -> str:
        pid = m.group(1).strip()
        for candidate in _param_id_variants(pid):
            val = param_values.get(candidate)
            if val:
                return str(val)
        label = _param_labels().get(pid)
        return f"[Assignment: {label}]" if label else f"[Assignment: {pid}]"

    filled = _ODP_PLACEHOLDER_RE.sub(_sub, line)
    # This is presentation-only cleanup. It never rewrites an SSP-supplied
    # value or attempts to infer a grammatically better selection.
    filled = re.sub(r"\s+([;,:.])", r"\1", filled)
    filled = re.sub(r"[ \t]{2,}", " ", filled)
    return filled.strip()


def _oscal_control_key(control_id: str) -> str:
    """Map a canonical id (AC-02, IA-02(12)) to the NIST catalog key form
    (ac-2, ia-2.12): lowercase, no zero-pad, enhancement as ``.N``."""
    text = re.sub(r"\s+", "", (control_id or "").strip())
    m = re.match(r"^([A-Za-z]{2})-(\d+)(?:\(([^)]+)\))?$", text)
    if not m:
        return text.lower()
    family = m.group(1).lower()
    number = int(m.group(2))
    enh = m.group(3)
    if enh is None:
        return f"{family}-{number}"
    enh_norm = str(int(enh)) if enh.isdigit() else enh.lower()
    return f"{family}-{number}.{enh_norm}"


def control_title(control_id: str) -> str:
    """Human-facing title for a control note.

    Prefers the official NIST rev5 catalog name (``AC-02 Account Management``)
    and falls back to the bare canonical id when the name is unknown (a custom
    or non-catalog control), rather than fabricating one.
    """
    canonical = canonical_control_id(control_id)
    name = _control_names().get(_oscal_control_key(canonical))
    return f"{canonical} {name}" if name else canonical


def resolve_control_components(
    control_id: str,
    origination: List[str],
    registry_components: List[Dict[str, Any]],
) -> tuple[List[str], str]:
    """Find all matching component slugs for a control, and the resolved inheritance mode.

    Returns:
        (list_of_component_slugs, resolved_inheritance_mode)
    """
    matched_slugs = []
    resolved_mode = "local"

    # Check if origination contains inherited/hybrid
    has_inherited = "inherited" in origination
    has_hybrid = "hybrid" in origination

    for comp in registry_components:
        slug = comp.get("component_slug")
        if not slug:
            continue
        owns = comp.get("owns_controls") or []
        for rule in owns:
            pattern = None
            rule_mode = None
            if isinstance(rule, str):
                pattern = rule
            elif isinstance(rule, dict):
                pattern = rule.get("pattern")
                rule_mode = rule.get("mode")

            if not pattern:
                continue

            # 1. Check glob match
            import fnmatch

            if not fnmatch.fnmatch(control_id.upper(), pattern.upper()):
                continue

            # 2. Check mode compatibility
            if rule_mode:
                if rule_mode == "inherited" and not (has_inherited or has_hybrid):
                    continue
                if (
                    rule_mode == "local"
                    and has_inherited
                    and not (
                        has_hybrid
                        or "system-specific" in origination
                        or "configured-by-customer" in origination
                    )
                ):
                    continue

            # Match succeeded!
            if slug not in matched_slugs:
                matched_slugs.append(slug)
            if rule_mode:
                resolved_mode = rule_mode

    # Fallbacks if no component matched
    if not matched_slugs and registry_components:
        # If inherited, attribute to the first platform component
        if has_inherited:
            platform_comp = next(
                (
                    c
                    for c in registry_components
                    if c.get("component_kind") == "platform"
                    or c.get("type") == "platform"
                ),
                None,
            )
            if platform_comp:
                matched_slugs.append(platform_comp.get("component_slug"))
                resolved_mode = "inherited"

        # If still empty, attribute to the first non-platform (app) component
        if not matched_slugs:
            app_comp = next(
                (
                    c
                    for c in registry_components
                    if c.get("component_kind") != "platform"
                    and c.get("type") != "platform"
                ),
                None,
            )
            if app_comp:
                matched_slugs.append(app_comp.get("component_slug"))
            else:
                matched_slugs.append(registry_components[0].get("component_slug"))
            resolved_mode = "local"
    elif not matched_slugs:
        # If no components are registered at all, we remain local
        resolved_mode = "local"
    elif matched_slugs and not any(
        isinstance(r, dict) and r.get("mode")
        for comp in registry_components
        for r in comp.get("owns_controls", [])
        if isinstance(r, dict)
    ):
        # If components matched but none specify rule mode, set resolved_mode based on origination flags
        if has_hybrid:
            resolved_mode = "hybrid"
        elif has_inherited:
            resolved_mode = "inherited"
        else:
            resolved_mode = "local"

    return matched_slugs, resolved_mode


def build_control_note(
    ssp_root: Dict[str, Any],
    req: Dict[str, Any],
    *,
    index: VaultIndex,
    role_index: Dict[str, str],
    plan_wikilink: Optional[str],
    parser_name: str,
    fips_199: Optional[str],
    parameters: Optional[List[Any]] = None,
    crm_entry: Optional[Any] = None,
    registry_components: Optional[List[Dict[str, Any]]] = None,
    registry_leveraged_auths: Optional[List[Dict[str, Any]]] = None,
    guidance: Optional[Any] = None,
) -> VaultNote:
    """Build the `Controls/<control_id>.md` note for one implemented-requirement.

    Emits the OSCAL + OKF type taxonomy:
      type: oscal.control
      oscal_model: control
      kind: canonical
    """
    control_id = req.get("control-id") or "Unknown"
    control_id = canonical_control_id(control_id)
    osid = _split_control_id(control_id)

    raw_state = (req.get("implementation-status") or {}).get("state")
    raw_flags = req.get("control-origination") or []
    # The normalized implementation-status target remains useful as a compact
    # controlled-vocabulary tag; ADR-026 removes only the duplicate per-note
    # frontmatter fields, not the derivation itself.
    status_mapped = vocab.map_implementation_status(raw_state, strict=False)

    # Resolve component attribution
    resolved_slugs, resolved_inheritance_mode = resolve_control_components(
        control_id,
        list(raw_flags) if raw_flags else ["system-specific"],
        registry_components or [],
    )

    sc = ssp_root.get("system-characteristics") or {}
    sys_slug = slugify((sc.get("system-name") or "Untitled System").strip())
    primary_slug = resolved_slugs[0] if resolved_slugs else sys_slug

    statement_ids = [
        s.get("statement-id")
        for s in req.get("statements", [])
        if s.get("statement-id")
    ]
    statements_with_narrative: List[Dict[str, Any]] = []
    for s in req.get("statements", []):
        sid = s.get("statement-id")
        desc = s.get("description") or ""
        label = sid or ""
        label_match = re.search(r"_stmt\.([A-Za-z0-9]+)$", label)
        part_label = label_match.group(1) if label_match else None
        statements_with_narrative.append(
            {
                "id": sid or "",
                "label": f"Part {part_label}" if part_label else None,
                "description": desc,
            }
        )

    responsible_roles = req.get("responsible-roles") or []
    responsible_role_ids = [
        r.get("role-id") for r in responsible_roles if r.get("role-id")
    ]

    fm: Dict[str, Any] = {
        # OSCAL + OKF type taxonomy (R1 migration)
        "type": "oscal.control",
        "oscal_model": "control",
        "kind": "canonical",
        # Backward-compat: keep note_type for one release cycle
        "note_type": "control",
        "control_id": control_id,
        "title": control_title(control_id),
        "family": osid["family"],
        "enhancement": osid["enhancement"],
        "system": _Literal(next(iter(index.systems.values())))
        if index.systems
        else None,
        "component": _Literal(_wl("Components", primary_slug)),
        "implementation_status": raw_state or "not-implemented",
        "origination": list(raw_flags) if raw_flags else ["system-specific"],
        "exportable": False,
        "status": "active",
        "owner": None,
        "reviewed": None,
    }
    # Recovered organization-defined parameters (ODPs), if any.
    param_list = list(parameters or [])
    if param_list:
        fm["parameters"] = [
            {"param_id": p.param_id, "value": p.value} for p in param_list
        ]
    # Secondary-CRM augmentation (inheritance / tenant responsibilities), if a
    # CRM row matched this control. Recorded in frontmatter so it round-trips to
    # canonical JSON + OSCAL props, and rendered in the CRM Guidance body below.
    if crm_entry is not None:
        crm_fm: Dict[str, Any] = {}
        for key in (
            "inheritance",
            "implementation_status",
            "impacted",
            "assessment_objective",
            "assessment_method",
            "evidence",
            "evidence_link",
        ):
            val = getattr(crm_entry, key, "")
            if val:
                crm_fm[key] = val
        # Carry unrecognized CRM columns (e.g. why/impact/control_name) forward
        # under `extra` rather than dropping them, so no source column is lost
        # between the CRM matrix and the control note (lossless pipeline, #43).
        extra = getattr(crm_entry, "extra", None)
        if isinstance(extra, dict):
            kept = {
                str(k): str(v)
                for k, v in extra.items()
                if v is not None and str(v).strip()
            }
            if kept:
                crm_fm["extra"] = kept
        if crm_fm:
            crm_fm["source"] = "secondary-crm"
            fm["crm"] = crm_fm

    # Inject inherited_from / hybrid_systems frontmatter if applicable
    if (
        resolved_inheritance_mode in ("inherited", "hybrid")
        or "inherited" in raw_flags
        or "hybrid" in raw_flags
    ):
        if resolved_inheritance_mode == "inherited" or (
            "inherited" in raw_flags and "hybrid" not in raw_flags
        ):
            fm["inherited_from"] = _Literal(_wl("Components", primary_slug))
        else:
            fm["hybrid_systems"] = [
                _Literal(_wl("Components", s)) for s in resolved_slugs
            ]
    # Resolve the system owner for the `owner` field
    role_map = _build_role_to_party_index(ssp_root)
    sys_owner = role_map.get("system-owner")
    if sys_owner:
        org_link = next(
            (
                v
                for k, v in index.organizations.items()
                if k.lower() == sys_owner.lower()
            ),
            None,
        )
        if org_link:
            fm["owner"] = _Literal(org_link)

    if plan_wikilink:
        # Single plan-provenance edge (ADR-025). `source_profile` was an
        # identical duplicate of `source_ssp`; both are vault provenance
        # metadata and are intentionally excluded from generic OSCAL export.
        fm["source_ssp"] = _Literal(plan_wikilink)
    fm["last_updated"] = _today()
    fm["parsed_by"] = parser_name
    if responsible_role_ids:
        # Resolve each role-id to the best org wikilink we can find.
        org_links: List[Any] = []
        seen: set = set()
        for rid in responsible_role_ids:
            resolved = _resolve_role_to_org(
                rid, role_index, {k: v for k, v in index.organizations.items()}
            )
            if resolved and resolved not in seen:
                seen.add(resolved)
                org_links.append(_Literal(resolved))
            else:
                # Use cleaned role text as fallback display name.
                cleaned = _clean_role_text(rid)
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    org_links.append(cleaned)
        fm["responsible_role"] = org_links
    if statement_ids:
        fm["statement_parts"] = statement_ids

    tags = [
        f"control/family/{osid['family'].lower() or 'unknown'}",
        f"control/status/{status_mapped.target}",
    ]
    if fips_199:
        tags.append(f"control/baseline/{fips_199}")
    fm["tags"] = tags

    # --- ExcaliBrain relationship fields ---
    # These populate the ExcaliBrain ontology so that every control note is
    # wired into the vault graph automatically.  Register these field names in
    # ExcaliBrain Settings → Hierarchy:
    #   Parents:      "up_control" (enhancements), "up_plan" (base controls)
    #   Children:     "sub_controls"
    #   Left Friends: "related_controls", "responsible_role"
    #
    # Graph semantics (ADR-025):
    #   - Enhancement control (e.g. AC-02(01)): Parent = base control (AC-02)
    #     via "up_control".
    #   - Base control (e.g. AC-02): Parent = Plan via "up_plan"; Children =
    #     its enhancement controls via "sub_controls" (populated below in
    #     build_vault's post-pass). We do NOT point a base control's
    #     "up_control" at the Plan — that produced a false 159-way star and
    #     mislabeled the Plan as a "parent control".
    if osid["enhancement"] is not None:
        # Enhancement: parent is the base control (e.g. AC-02).
        base_id = canonical_control_id(
            f"{osid['family']}-{re.sub(r'[^0-9]', '', control_id.split('-')[1].split('(')[0])}"
        )
        if base_id in index.controls:
            fm["up_control"] = _Literal(index.controls[base_id])
        elif plan_wikilink:
            # Orphan enhancement (no base control in this SSP): fall back to Plan.
            fm["up_plan"] = _Literal(plan_wikilink)
    else:
        # Base control: parent is the Plan.
        if plan_wikilink:
            fm["up_plan"] = _Literal(plan_wikilink)

    # "sub_controls" (base controls only) is populated in build_vault's
    # post-pass once every control id is known. Enhancements have no children.
    if osid["enhancement"] is None:
        fm["sub_controls"] = []

    # related_controls: wire heuristically-detected related controls
    related_for_excalibrain = _extract_related_controls(req)
    if related_for_excalibrain:
        fm["related_controls"] = [
            _Literal(f"[[Controls/{rid}]]") for rid in related_for_excalibrain
        ]

    # Body
    lines: List[str] = []
    lines.append(f"# {_control_wikilink(control_id).split('|')[1][:-2]}")
    lines.append("")

    # Template authoring guidance recovered from the source .docx (R18): the
    # blue (#0070C0) / "Instruction:" blocks and the CIS-table "Describe how..."
    # prompt for this control, rendered as a folded Obsidian callout.
    if guidance is not None:
        from compliance.lib.control_guidance import render_guidance_callout

        callout = render_guidance_callout(guidance)
        if callout:
            lines.extend(callout)
            lines.append("")

    # Upstream section: authoritative NIST SP 800-53 rev5 catalog statement
    # language for this control, with the SSP's organization-defined parameter
    # values substituted into the `{{ insert: param, ... }}` placeholders.
    lines.append("# Upstream")
    lines.append("")
    catalog_lines = _control_statements().get(_oscal_control_key(control_id))
    if catalog_lines:
        param_values = {p.param_id: p.value for p in param_list}
        for cl in catalog_lines:
            filled = _fill_catalog_prose(cl, param_values)
            if filled:
                lines.append(f"- {filled}")
    else:
        lines.append(
            f"> No NIST SP 800-53 rev5 catalog statement is vendored for "
            f"`{control_id}` (custom or non-catalog control)."
        )
    lines.append("")

    # Parameters are losslessly stored in frontmatter. Render that canonical
    # source in Obsidian instead of duplicating every value in Markdown.
    lines.append("# Parameters")
    lines.append("")
    lines.append("```dataview")
    lines.append('TABLE parameter.param_id AS "Parameter", parameter.value AS "Value"')
    lines.append("FROM [[]]")
    lines.append("FLATTEN parameters AS parameter")
    lines.append("```")
    lines.append("")

    # Statement Parts section
    lines.append("# Statement Parts")
    lines.append("")
    la_slug = None
    if (
        resolved_inheritance_mode in ("inherited", "hybrid")
        and registry_leveraged_auths
    ):
        primary_comp = next(
            (
                c
                for c in (registry_components or [])
                if c.get("component_slug") == primary_slug
            ),
            None,
        )
        if primary_comp:
            resp_party = primary_comp.get("responsible_party") or ""
            la = next(
                (
                    la
                    for la in registry_leveraged_auths
                    if (la.get("provider_system") or "").lower() == resp_party.lower()
                ),
                None,
            )
            if la:
                la_slug = slugify(la.get("id"))

    if statements_with_narrative:
        for st in statements_with_narrative:
            if st["label"]:
                lines.append(f"## {st['label']}")
                lines.append("")
                # Inline fields for Dataview
                part_code = st["label"].split()[-1] if st["label"] else ""
                lines.append(f"statement_part:: {part_code}")
                lines.append(f"inheritance_mode:: {resolved_inheritance_mode}")
                if la_slug:
                    lines.append(f"inherited_from:: [[inheritance/{la_slug}]]")
                lines.append(
                    f"implementation_status:: {raw_state or 'not-implemented'}"
                )
                lines.append("")
            if st["description"]:
                lines.append(st["description"])
                lines.append("")
            else:
                lines.append("_(No narrative recorded in source SSP.)_")
                lines.append("")
    else:
        lines.append("_(No part-level narrative was captured by the parser.)_")
        lines.append("")

    # CRM Guidance section — emitted only when a secondary-CRM entry exists
    # (ADR-025). The generic 4-bullet stub added no information to 100% of
    # controls; when there is no CRM augmentation the section is omitted.
    if crm_entry is not None and getattr(crm_entry, "inheritance", ""):
        lines.append("# CRM Guidance")
        lines.append("")
        lines.append("crm_guidance_level:: standard")
        lines.append("")
        lines.append("## Inheritance (secondary CRM)")
        lines.append("")
        lines.append(f"crm_inheritance:: {crm_entry.inheritance}")
        if getattr(crm_entry, "implementation_status", ""):
            lines.append(f"crm_status:: {crm_entry.implementation_status}")
        if getattr(crm_entry, "impacted", ""):
            lines.append(f"crm_impacted:: {crm_entry.impacted}")
        lines.append("")
        if getattr(crm_entry, "assessment_objective", ""):
            lines.append(f"**Assessment objective:** {crm_entry.assessment_objective}")
            lines.append("")
        if getattr(crm_entry, "assessment_method", ""):
            lines.append(f"**Assessment method:** {crm_entry.assessment_method}")
            lines.append("")
        if getattr(crm_entry, "evidence", ""):
            lines.append(f"**Evidence:** {crm_entry.evidence}")
            lines.append("")

    if fm.get("responsible_role") and isinstance(fm["responsible_role"][0], _Literal):
        lines.append("## Responsible Role")
        lines.append("")
        for r in fm["responsible_role"]:
            lines.append(f"- {r}")
        lines.append("")

    # Evidence section — omitted entirely when empty (ADR-025). Placeholder
    # prose ("_(No evidence references recorded…)_") polluted the thin-note /
    # content-integrity signals; absence is cleaner and still round-trips.
    ev_related = _extract_related_controls(req)
    if ev_related:
        lines.append("# Evidence")
        lines.append("")
        for rid in ev_related:
            if rid in index.controls:
                lines.append(f"- See [[Controls/{rid}]] for related evidence")
            else:
                lines.append(f"- See {rid} for related evidence")
        lines.append("")

    # Related Controls section — omitted when empty (ADR-025).
    related = _extract_related_controls(req)
    if related:
        lines.append("## Related Controls")
        lines.append("")
        for rid in related:
            if rid in index.controls:
                lines.append(f"- {_control_wikilink(rid)}")
            else:
                lines.append(f"- {rid}")
    body = "\n".join(lines).rstrip() + "\n"

    relpath = _note_path("Controls", _control_slug(control_id))
    return VaultNote(relpath=relpath, frontmatter=fm, body=body)


def _extract_related_controls(req: Dict[str, Any]) -> List[str]:
    """Look for related-control references inside `statements[].description`.

    fismatic doesn't emit `related-controls` explicitly; descriptions often
    narrate dependencies ("See AC-2 for account management"). Heuristically
    spotting them keeps the body useful without overclaiming coverage.

    The match is gated on the real NIST SP 800-53 control families so that
    document/policy identifiers that merely *look* like a control id do not
    produce dangling links — e.g. ``CIO-IT-18-90`` (an internal directive)
    used to yield a bogus ``[[Controls/IT-18]]``, and ``SP-800`` a
    ``[[Controls/SP-800]]``. A boundary that rejects a preceding ``-`` also
    stops matching the ``IT-18`` inside ``CIO-IT-18-90``.
    """
    related: List[str] = []
    seen: set[str] = set()
    # (?<![A-Za-z-]) rejects a family glued to a preceding word/hyphen so
    # 'CIO-IT-18' does not match 'IT-18'. Family must be a known 800-53 family.
    pattern = re.compile(r"(?<![A-Za-z-])([A-Za-z]{2})-(\d+)(?:\s*\(([^)]+)\))?")
    this_id = (req.get("control-id") or "").strip()
    for s in req.get("statements", []) or []:
        desc = s.get("description") or ""
        for m in pattern.finditer(desc):
            family = m.group(1).upper()
            if family not in NIST_CONTROL_FAMILIES:
                continue
            # A parenthetical suffix that is a *letter* (e.g. "AC-2 (a)",
            # "AC-2(c)") is a statement-PART citation, not a control
            # enhancement (enhancements are numeric, "AC-2(1)"). Treat the
            # base control as the reference and drop the part suffix so we
            # don't emit dangling [[Controls/AC-02(a)]] links.
            enh = (m.group(3) or "").strip()
            if enh and not any(ch.isdigit() for ch in enh):
                cid = f"{family}-{m.group(2)}"
            else:
                cid = m.group(0).strip()
            cid_norm = canonical_control_id(cid)
            this_norm = canonical_control_id(this_id) if this_id else this_id
            if cid_norm and cid_norm != this_norm and cid_norm not in seen:
                seen.add(cid_norm)
                related.append(cid_norm)
    return related


def build_system_note(
    ssp_root: Dict[str, Any],
    *,
    index: VaultIndex,
    plan_wikilink: Optional[str],
    parser_name: str,
    fips199_data: Optional[List[Dict[str, Any]]] = None,
) -> VaultNote:
    """Build the `Systems/<system-slug>.md` note.

    Emits the OSCAL + OKF type taxonomy:
      type: oscal.ssp
      oscal_model: system-security-plan
      kind: canonical
    """
    sc = ssp_root.get("system-characteristics") or {}
    system_name = (sc.get("system-name") or "Untitled System").strip()
    slug = slugify(system_name)
    relpath = _note_path("Systems", slug)

    index.systems[system_name] = _wl("Systems", slug)

    sil = (sc.get("security-sensitivity-level") or "").lower() or "moderate"
    impact = sc.get("security-impact-level") or {}
    fips_199 = {
        "low": "low",
        "moderate": "moderate",
        "high": "high",
    }.get(sil, sil or "moderate")
    conf = (impact.get("security-objective-confidentiality") or fips_199).lower()
    integ = (impact.get("security-objective-integrity") or fips_199).lower()
    avail = (impact.get("security-objective-availability") or fips_199).lower()
    state_block = ((sc.get("status") or {}).get("state") or "operational").lower()
    auth_boundary = (sc.get("authorization-boundary") or {}).get("description")

    org_links: List[Any] = []
    if index.organizations:
        org_links.append(_Literal(next(iter(index.organizations.values()))))

    fm: Dict[str, Any] = {
        # OSCAL + OKF type taxonomy (R1 migration)
        "type": "oscal.ssp",
        "oscal_model": "system-security-plan",
        "kind": "canonical",
        # Backward-compat
        "note_type": "system",
        "name": system_name,
        "acronym": None,
        "status": (
            state_block
            if state_block in ("operational", "under-development")
            else "other"
        ),
        "owner": None,
    }
    sys_ids = sc.get("system-ids") or []
    if sys_ids and isinstance(sys_ids, list):
        sid = sys_ids[0]
        if isinstance(sid, dict):
            fm["system_id"] = sid.get("id")
    fm["fips_199"] = fips_199
    fm["security_objective_confidentiality"] = conf
    fm["security_objective_integrity"] = integ
    fm["security_objective_availability"] = avail
    fm["impact_level"] = fips_199
    if auth_boundary:
        fm["authorization_boundary"] = str(auth_boundary).strip()
    # organization is required per schema
    role_index = _build_role_to_party_index(ssp_root)
    fm["organization"] = org_links[0] if org_links else ""

    # Surface system_owner/isso/authorizing_official from metadata roles
    for fm_key, role_id in [
        ("system_owner", "system-owner"),
        ("isso", "information-system-security-officer"),
        ("authorizing_official", "authorizing-official"),
    ]:
        party_name = role_index.get(role_id)
        if party_name:
            org_link = next(
                (
                    v
                    for k, v in index.organizations.items()
                    if k.lower() == party_name.lower()
                ),
                None,
            )
            if org_link:
                fm[fm_key] = _Literal(org_link)
    # Resolve owner from system_owner if present
    if fm.get("system_owner"):
        fm["owner"] = fm["system_owner"]

    # Store FIPS-199 information types in frontmatter when supplied.
    if fips199_data:
        fm["information_types"] = fips199_data

    fm["components"] = (
        [_Literal(v) for v in index.components.values()] if index.components else []
    )
    fm["plans"] = [plan_wikilink] if plan_wikilink else []
    fm["last_updated"] = _today()
    fm["tags"] = [
        f"system/fips/{fips_199}",
        f"system/status/{state_block or 'operational'}",
    ]

    # --- ExcaliBrain relationship fields ---
    # Register in ExcaliBrain Settings → Hierarchy:
    #   Parents:      "up_control"  (or default "Parent")
    #   Children:     "child_plans", "child_components"
    #   Left Friends: "organization"  (already a field; also add to Left Friends list)
    #
    # System sits below Plan and above Components in the vault graph.
    if plan_wikilink:
        fm["up_plan"] = _Literal(plan_wikilink)
    if index.components:
        fm["child_components"] = [_Literal(link) for link in index.components.values()]

    body_lines: List[str] = [
        f"# [[Systems/{slug}|{system_name}]]",
        "",
        "## Description",
        "",
        (sc.get("description") or "_No description present in source SSP._").strip(),
        "",
    ]
    if auth_boundary:
        body_lines += [
            "## Authorization Boundary",
            "",
            auth_boundary.strip(),
            "",
        ]
    comp_links = list(index.components.values())
    body_lines += [
        "## Components",
        "",
        *(
            [f"- {_Literal(lnk)}" for lnk in comp_links]
            if comp_links
            else [
                "_(Synthetic component reference; fismatic does not "
                "emit per-component implementation detail at this time.)_"
            ]
        ),
        "",
    ]
    # Append Information Types section when FIPS-199 data is provided.
    if fips199_data:
        body_lines += [
            "## Information Types",
            "",
            "| Title | C | I | A | SP 800-60 ID |",
            "|-------|---|---|---|--------------|",
        ]
        for it in fips199_data:
            title = it.get("title", "")
            cats = it.get("categorizations", [])
            sp_id = cats[0]["information-type-ids"][0] if cats else ""
            # For 1.0.x the impacts are on the dict directly.
            c_val = (it.get("confidentiality-impact") or {}).get("base", "")
            i_val = (it.get("integrity-impact") or {}).get("base", "")
            a_val = (it.get("availability-impact") or {}).get("base", "")
            body_lines.append(f"| {title} | {c_val} | {i_val} | {a_val} | {sp_id} |")
        body_lines.append("")
    body = "\n".join(body_lines).rstrip() + "\n"
    return VaultNote(relpath=relpath, frontmatter=fm, body=body)


def build_synthetic_component_note(
    ssp_root: Dict[str, Any],
    *,
    index: VaultIndex,
) -> Optional[VaultNote]:
    """Build a single synthetic `Components/<system>.md` note for the system.

    Emits the OSCAL + OKF type taxonomy:
      type: oscal.component_definition
      oscal_model: component-definition
      kind: canonical

    fismatic's OSCAL output has no per-component block, but the schema marks
    `components` as required on the System note. Emitting one synthetic
    component referencing the system itself lets the vault's graph view show
    a 'System -> Component -> Controls' edge and gives compliance staff a
    place to record per-component implementation narrative by hand. We mark
    it clearly in the body as a synthetic placeholder so nobody mistakes it
    for real input from the source SSP.
    """
    if not index.systems:
        return None
    system_name = next(iter(index.systems))
    system_link = index.systems[system_name]
    slug = slugify(system_name)
    relpath = _note_path("Components", slug)
    index.components[slug] = _wl("Components", slug)

    sc = ssp_root.get("system-characteristics") or {}
    sil = (sc.get("security-sensitivity-level") or "moderate").lower()
    fm = {
        # OSCAL + OKF type taxonomy (R1 migration)
        "type": "oscal.component_definition",
        "oscal_model": "component-definition",
        "kind": "canonical",
        # Backward-compat
        "note_type": "component",
        "name": system_name,
        "component_type": "system",
        "status": "operational",
        "version": None,
        "belongs_to": _Literal(system_link),
        "implementation_model": "paas",
        "responsible_party": _Literal(next(iter(index.organizations.values())))
        if index.organizations
        else "",
        "cloud_deployment_model": "public",
        "fips_199": sil,
        "fedramp_package": f"fedramp-{sil}",
        "tags": [
            "component/type/system",
            "component/synthetic",
            f"component/fedramp/{sil}",
        ],
        "owner": None,
    }
    # NB: we intentionally omit an `implements_controls` frontmatter list
    # (ADR-025). It was a mirror of every control's `component` link, which
    # Obsidian renders in reverse via backlinks; maintaining both doubled churn
    # for no navigational gain and is not read by any downstream consumer.

    # --- ExcaliBrain relationship fields ---
    # Register in ExcaliBrain Settings → Hierarchy:
    #   Parents:      "up_system"
    #   Left Friends: "responsible_party"  (already a field; add to Left Friends list)
    # Component sits below System and is a peer of its sibling components.
    fm["up_system"] = _Literal(system_link)

    body = (
        f"# [[Components/{slug}|{system_name}]]\n\n"
        "## Synthetic placeholder\n\n"
        "This component note was synthesized by `compliance/scripts/export_obsidian_vault.py` "
        "because the source SSP (parsed by fismatic) does not emit a per-component "
        "implementation block. Replace or extend it with real component information "
        "from a component-definition document or hand-edited.\n\n"
        "## Belongs to\n\n"
        f"- {_Literal(system_link)}\n"
    )
    return VaultNote(relpath=relpath, frontmatter=fm, body=body)


def enrich_component_note_from_narrative(
    note: Optional[VaultNote], narrative: Optional[Dict[str, Any]]
) -> Optional[VaultNote]:
    """Overlay recovered component/software inventory onto the Component note.

    The contract's ``oscal_component_definition`` object extracts real asset,
    software, external-service, and AWS-service inventory tables from Chapter
    10, but the synthetic component note is a hardcoded placeholder. This
    surfaces the extracted inventory as body sections so it is not silently
    dropped (D-5 / #41). No-op when there is no note or no extracted inventory.
    """
    if note is None or not narrative:
        return note
    objs = _narrative_object_index(narrative)
    comp = objs.get("oscal_component_definition")
    if not comp:
        return note
    props = comp.get("properties") or {}

    inventory_sections = [
        ("inventory_items", "Asset Inventory"),
        ("software_inventory", "Software Inventory"),
        ("external_services", "External Services"),
        ("aws_services_in_use", "AWS Services In Use"),
    ]
    rendered: List[str] = []
    table_values: List[Any] = []
    for prop_name, heading in inventory_sections:
        if prop_name not in props:
            continue
        table_values.append(props[prop_name])
        md = _narrative_value_to_md(props[prop_name])
        if md:
            rendered.append(f"## {heading}\n\n{md}")

    if not rendered:
        return note

    note.frontmatter["provenance"] = "heading_parser"
    note.frontmatter["source_chapter"] = "10"
    projections = table_projection(table_values)
    projections.pop("_records")
    if projections["table_record_count"]:
        note.frontmatter.update(projections)
    # Drop the synthetic tag now that the note carries real inventory.
    tags = [
        t for t in (note.frontmatter.get("tags") or []) if t != "component/synthetic"
    ]
    note.frontmatter["tags"] = tags
    header = note.body.split("## Synthetic placeholder")[0].rstrip()
    belongs = ""
    if "## Belongs to" in note.body:
        belongs = "\n\n" + note.body[note.body.index("## Belongs to") :].rstrip()
    note.body = header + "\n\n" + "\n\n".join(rendered) + belongs + "\n"
    return note


def build_real_component_notes(
    ssp_root: Dict[str, Any],
    registry_components: List[Dict[str, Any]],
    *,
    index: VaultIndex,
    registry_leveraged_auths: Optional[List[Dict[str, Any]]] = None,
) -> List[VaultNote]:
    """Build one ``Components/<slug>.md`` note per registry component.

    Called by :func:`build_vault` when the caller supplies *registry_components*
    from ``scripts/registry.yaml``.  Replaces :func:`build_synthetic_component_note`
    for systems that have curated component data.

    Each note is tagged ``component/type/<kind>`` (never ``component/synthetic``).
    The *index.components* mapping is populated so :func:`build_system_note` can
    emit wikilinks to all real notes.

    Parameters
    ----------
    ssp_root:
        The OSCAL SSP root dict (used for FIPS 199 level lookup).
    registry_components:
        The ``components:`` list from ``scripts/registry.yaml`` for this system.
    index:
        The :class:`VaultIndex` shared across all builders in this vault pass.
    registry_leveraged_auths:
        Optional ``leveraged_authorizations:`` list from the registry; embedded
        in each component note body for traceability.
    """
    sc = ssp_root.get("system-characteristics") or {}
    sil = (sc.get("security-sensitivity-level") or "moderate").lower()
    system_name = (sc.get("system-name") or "Untitled System").strip()
    system_link = _wl("Systems", slugify(system_name))
    auths = registry_leveraged_auths or []

    notes: List[VaultNote] = []
    for comp in registry_components:
        name = (comp.get("name") or "").strip()
        if not name:
            continue
        slug = comp.get("component_slug") or slugify(name)
        relpath = _note_path("Components", slug)
        # Register in index so System note can wikilink to this component.
        index.components[slug] = _wl("Components", slug)

        comp_kind = comp.get("component_kind") or comp.get("type") or "service"
        impl_model = comp.get("implementation_model") or "paas"
        fedramp_pkg = comp.get("fedramp_package") or f"fedramp-{sil}"
        responsible = comp.get("responsible_party") or ""

        fm: Dict[str, Any] = {
            "type": "oscal.component_definition",
            "oscal_model": "component-definition",
            "kind": "canonical",
            "note_type": "component",
            "name": name,
            "component_kind": comp_kind,
            # legacy field kept for backward-compat with existing Dataview queries
            "component_type": comp_kind,
            "status": "operational",
            "version": None,
            "belongs_to": _Literal(system_link),
            "implementation_model": impl_model,
            "responsible_party": responsible,
            "cloud_deployment_model": "public",
            "fips_199": sil,
            "fedramp_package": fedramp_pkg,
            "tags": [
                f"component/type/{comp_kind}",
                f"component/fedramp/{fedramp_pkg.replace('fedramp-', '')}",
            ],
            "owner": None,
        }

        note_field = (comp.get("note") or "").strip()

        # Build leveraged-authorization section for this component (where the
        # responsible_party matches a leveraged-auth provider_system).
        lev_lines: List[str] = []
        for la in auths:
            if (la.get("provider_system") or "").lower() == responsible.lower():
                lev_lines.append(
                    f"- **{la.get('name', la.get('id', 'Unknown'))}**"
                    + (f" ({la.get('mode', '')})" if la.get("mode") else "")
                    + (f"  \n  {la['href']}" if la.get("href") else "")
                )

        body_parts: List[str] = [
            f"# [[Components/{slug}|{name}]]",
            "",
            f"Part of: {_Literal(system_link)}",
            "",
        ]
        if note_field:
            body_parts += ["## Notes", "", note_field, ""]
        if lev_lines:
            body_parts += ["## Leveraged Authorizations", ""] + lev_lines + [""]
        body_parts += [
            "## Control Implementation",
            "",
            "_(Controls implemented by this component are shown in the "
            "backlinks pane: each control note links here via its "
            "`component` field.)_",
            "",
        ]
        notes.append(
            VaultNote(
                relpath=relpath,
                frontmatter=fm,
                body="\n".join(body_parts).rstrip() + "\n",
            )
        )
    return notes


def _control_wikilink_token(control_id: str) -> str:
    """Return the raw `[[Controls/AC-2]]` token (no display alias)."""
    return f"[[Controls/{control_id.strip()}]]"


def build_organization_notes(
    ssp_root: Dict[str, Any],
    *,
    index: VaultIndex,
    registry_leveraged_auths: Optional[List[Dict[str, Any]]] = None,
) -> List[VaultNote]:
    """Build `Organizations/<slug>.md` notes for each `metadata.parties` entry.

    Emits the OSCAL + OKF type taxonomy:
      type: oscal.party
      oscal_model: party
      kind: canonical
    """
    metadata = ssp_root.get("metadata") or {}
    parties = metadata.get("parties") or []
    roles = metadata.get("roles") or []
    roleTitles = {
        r.get("id"): r.get("title", r.get("id")) for r in roles if r.get("id")
    }
    party_roles: Dict[str, List[str]] = {}
    for responsible in metadata.get("responsible-parties") or []:
        role_id = responsible.get("role-id")
        if not role_id:
            continue
        for party_uuid in responsible.get("party-uuids") or []:
            role_ids = party_roles.setdefault(str(party_uuid), [])
            if role_id not in role_ids:
                role_ids.append(role_id)
    notes: List[VaultNote] = []
    for party in parties:
        if (party.get("type") or "").lower() != "organization":
            continue
        name = (party.get("name") or "Untitled Organization").strip()
        slug = slugify(name)
        if not slug:
            slug = f"org-{party.get('uuid', 'untitled')}"
        relpath = _note_path("Organizations", slug)
        role_ids = party_roles.get(str(party.get("uuid") or ""), [])
        role_summary = (
            ", ".join(roleTitles.get(r, r) for r in role_ids)
            if role_ids
            else "cloud-service-provider"
        )
        fm = {
            # OSCAL + OKF type taxonomy (R1 migration)
            "type": "oscal.party",
            "oscal_model": "party",
            "kind": "canonical",
            # Backward-compat
            "note_type": "organization",
            "name": name,
            "acronym": None,
            "org_type": "agency",
            "role": role_summary,
            "role_ids": role_ids,
            "email_addresses": party.get("email-addresses") or [],
            "telephone_numbers": party.get("telephone-numbers") or [],
            "status": "active",
            "owner": None,
        }
        if not fm["acronym"]:
            fm["acronym"] = name.split()[0].upper() if name else "ORG"
        fm["systems"] = [_Literal(link) for link in index.systems.values()]
        fm["responsible_for"] = []
        fm["tags"] = [
            "org/agency",
            "org/federal",
        ]
        # --- ExcaliBrain relationship fields ---
        # Register in ExcaliBrain Settings → Hierarchy:
        #   Children: "child_systems"
        # Organization sits above Systems in the vault graph.
        if index.systems:
            fm["child_systems"] = [_Literal(link) for link in index.systems.values()]
        if party.get("uuid"):
            index.organizations[str(party["uuid"])] = _wl("Organizations", slug)
        for rid in role_ids:
            index.organizations[str(rid)] = _wl("Organizations", slug)
        index.organizations.setdefault(name, _wl("Organizations", slug))
        body = (
            f"# [[Organizations/{slug}|{name}]]\n\n## Roles\n\n- {role_summary}\n\n## Systems\n\n"
            + (
                "".join(f"- {link}\n" for link in index.systems.values())
                or "_(none synthesized yet)_\n"
            )
        )
        notes.append(VaultNote(relpath=relpath, frontmatter=fm, body=body))
    # Fallback: if no organization parties present, emit a single generic
    # CSP note so System/Control notes always have a wikilink target.
    if not notes:
        slug = "untitled-csp"
        relpath = _note_path("Organizations", slug)
        index.organizations["default"] = _wl("Organizations", slug)
        fm = {
            # OSCAL + OKF type taxonomy (R1 migration)
            "type": "oscal.party",
            "oscal_model": "party",
            "kind": "canonical",
            # Backward-compat
            "note_type": "organization",
            "name": "Untitled Cloud Service Provider",
            "acronym": "CSP",
            "org_type": "agency",
            "role": "cloud-service-provider",
            "systems": [_Literal(link) for link in index.systems.values()],
            "responsible_for": [],
            "tags": ["org/agency", "org/synthetic"],
            "status": "active",
            "owner": None,
        }
        body = (
            "# [[Organizations/untitled-csp|Untitled Cloud Service Provider]]\n\n"
            "## Roles\n\n- cloud-service-provider\n\n"
            "## Notes\n\n"
            "Synthesized by `export_obsidian_vault.py` because the source SSP "
            "carried no `metadata.parties` block.\n"
        )
        notes.append(VaultNote(relpath=relpath, frontmatter=fm, body=body))

    # Add dynamic provider organizations if they are not already created/indexed
    if registry_leveraged_auths:
        for la in registry_leveraged_auths:
            provider_sys = la.get("provider_system")
            if not provider_sys:
                continue
            provider_slug = slugify(provider_sys)
            already_built = False
            for org_key in index.organizations:
                if org_key.lower() == provider_sys.lower():
                    already_built = True
                    break

            if not already_built:
                index.organizations[provider_sys] = _wl("Organizations", provider_slug)
                relpath = _note_path("Organizations", provider_slug)
                fm = {
                    "type": "oscal.party",
                    "oscal_model": "party",
                    "kind": "canonical",
                    "note_type": "organization",
                    "name": provider_sys,
                    "acronym": provider_sys.split()[0].upper()
                    if provider_sys
                    else "ORG",
                    "org_type": "agency",
                    "role": "cloud-service-provider",
                    "status": "active",
                    "owner": None,
                    "systems": [_Literal(link) for link in index.systems.values()],
                    "responsible_for": [],
                    "tags": ["org/agency", "org/federal"],
                }
                body = (
                    f"# [[Organizations/{provider_slug}|{provider_sys}]]\n\n"
                    "## Roles\n\n"
                    "- cloud-service-provider\n\n"
                    "## Systems\n\n"
                    + "".join(f"- {link}\n" for link in index.systems.values())
                )
                notes.append(VaultNote(relpath=relpath, frontmatter=fm, body=body))

    return notes


def build_plan_note(
    ssp_root: Dict[str, Any],
    *,
    index: VaultIndex,
    plan_id: str,
    parser_name: str,
    source_docx: Optional[str],
) -> VaultNote:
    """Build the `Plans/<slug>.md` note describing the SSP document itself.

    Emits the OSCAL + OKF type taxonomy:
      type: oscal.ssp
      oscal_model: system-security-plan
      kind: canonical
    """
    sc = ssp_root.get("system-characteristics") or {}
    metadata = ssp_root.get("metadata") or {}
    system_name = (sc.get("system-name") or "Untitled System").strip()
    title = f"{system_name} System Security Plan"
    slug = slugify(f"{system_name}-ssp")
    relpath = _note_path("Plans", slug)
    index.plans[plan_id] = _wl("Plans", slug)

    fm: Dict[str, Any] = {
        # OSCAL + OKF type taxonomy (R1 migration)
        "type": "oscal.ssp",
        "oscal_model": "system-security-plan",
        "kind": "canonical",
        # Backward-compat
        "note_type": "plan",
        "plan_id": plan_id,
        "title": title,
        "version": metadata.get("version") or "1.0",
        "date": _today(),
        "plan_type": "ssp",
        "import_profile": None,
        "system": _Literal(next(iter(index.systems.values()))) if index.systems else "",
        "status": "active",
        "template_version": "08282018",
        "parser": parser_name,
        "controls_parsed": len(index.controls),
        "oscasl_version": metadata.get("oscal-version") or "1.0.0",
        "source_docx": source_docx,
        "tags": ["plan/ssp"],
        "owner": None,
        "reviewed": None,
    }
    prof = ssp_root.get("import-profile", {})
    if isinstance(prof, dict) and prof.get("href"):
        fm["profile_ref"] = prof["href"]
    fips = (sc.get("security-sensitivity-level") or "low").lower()
    fm["tags"].append(f"plan/fedramp/{fips}")
    fm["tags"].append("plan/status/active")

    # --- ExcaliBrain relationship fields ---
    # Register in ExcaliBrain Settings → Hierarchy:
    #   Children: "child_systems"
    # Plan is the root of the SSP graph: Plan → System → Component → Controls.
    # We intentionally do NOT emit a `child_controls` frontmatter list (ADR-025):
    # it was a 159-item mirror of every control's `up_plan`/`source_ssp`, which
    # Obsidian already renders in reverse via backlinks. The human-navigable
    # control list still appears in the note body below.
    if index.systems:
        fm["child_systems"] = [_Literal(link) for link in index.systems.values()]

    body = (
        f"# [[Plans/{slug}|{title}]]\n\n"
        "## Summary\n\n"
        f"- System: {next(iter(index.systems.values()), '_unspecified_')}\n"
        f"- Plan ID: `{plan_id}`\n"
        f"- Parser: `{parser_name}`\n"
        f"- Controls parsed: {len(index.controls)}\n"
        f"- FIPS 199: `{fips}`\n\n"
    )
    if source_docx:
        body += (
            "## Source\n\n"
            "_(Not committed to the repo; see README.md for sourcing conventions.)_\n"
            f"- Original filename: `{source_docx}`\n\n"
        )
    body += "## Controls\n\n"
    for cid in sorted(index.controls.keys()):
        body += f"- {_control_wikilink(cid)}\n"
    return VaultNote(relpath=relpath, frontmatter=fm, body=body)


def build_coverage_note(
    controls: Dict[str, Dict[str, Any]],
    *,
    fips_199: str = "moderate",
    crm_only: Optional[List[str]] = None,
    baseline_gaps: Optional[List[str]] = None,
) -> "VaultNote":
    """Build a ``Coverage.md`` vault note with a coverage summary.

    Emits the OKF type taxonomy:
      type: okf.index
      note_type: coverage

    Parameters
    ----------
    controls:
        ``{control_id: frontmatter_dict}`` mapping as returned by
        ``compliance.scripts.coverage_report.read_vault_controls``.
    fips_199:
        The system's FIPS-199 categorization level (low/moderate/high).
    crm_only:
        Optional list of control IDs present in the CRM but not the SSP.
    baseline_gaps:
        Optional list of control IDs in the baseline but absent from the SSP.
    """
    from collections import Counter

    n_controls = len(controls)
    status_counter: Counter = Counter()
    for fm in controls.values():
        st = str(
            fm.get("implementation_status") or fm.get("normalized_status") or "unknown"
        )
        status_counter[st] += 1

    body_lines: List[str] = [
        "# Coverage Note",
        "",
        f"FIPS-199 level: **{fips_199}**",
        "",
        "## Implementation Status Distribution",
        "",
        "```dataview",
        "TABLE length(rows) AS Count",
        'FROM "Controls"',
        "FLATTEN tags",
        'WHERE startswith(tags, "control/status/")',
        "GROUP BY tags AS Status",
        "SORT Count DESC",
        "```",
        "",
        "### Counts (at vault-build time)",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for status, count in sorted(status_counter.items()):
        body_lines.append(f"| {status} | {count} |")
    body_lines += ["", f"Total controls: {n_controls}", ""]

    if baseline_gaps is not None:
        body_lines += [
            "## Baseline Gaps",
            "",
        ]
        if baseline_gaps:
            body_lines.append(
                f"{len(baseline_gaps)} controls in the FedRAMP {fips_199} baseline are not present in this SSP:"
            )
            body_lines.append("")
            body_lines.append(", ".join(sorted(baseline_gaps)))
        else:
            body_lines.append("None — full baseline coverage.")
        body_lines.append("")

    if crm_only:
        body_lines += [
            "## CRM-Only Controls",
            "",
            f"{len(crm_only)} controls in CRM but not SSP:",
            "",
            ", ".join(sorted(crm_only)),
            "",
        ]

    body = "\n".join(body_lines).rstrip() + "\n"
    return VaultNote(
        relpath="Coverage.md",
        frontmatter={
            "type": "okf.index",
            "note_type": "coverage",
            "fips_199": fips_199,
            "last_updated": _today(),
        },
        body=body,
    )


def build_index_note(index: VaultIndex) -> VaultNote:
    """Build the vault root `Index.md` with Dataview-powered dashboards.

    Emits the OKF type taxonomy:
      type: okf.index
    """
    body = (
        "# Compliance Vault Index\n\n"
        "This vault was generated by "
        "`compliance/scripts/export_obsidian_vault.py` from an OSCAL SSP. "
        "It follows the schema in `docs/OBSIDIAN_VAULT_SCHEMA.md`.\n\n"
        "## Counts\n\n"
        "> Live counts (Dataview). The static numbers below are a fallback for\n"
        "> renderers without Dataview.\n\n"
        "```dataview\n"
        'TABLE length(rows) AS Count FROM "Controls" OR "Systems" OR '
        '"Components" OR "Organizations" OR "Plans"\n'
        "GROUP BY type AS Type\n"
        "```\n\n"
        f"- Controls: {len(index.controls)}\n"
        f"- Systems: {len(index.systems)}\n"
        f"- Components: {len(index.components)}\n"
        f"- Organizations: {len(index.organizations)}\n"
        f"- Plans: {len(index.plans)}\n\n"
        "## Dashboards\n\n"
        "### Controls by status\n\n"
        "```dataview\n"
        "TABLE implementation_status AS Status, origination AS Origination\n"
        'FROM "Controls"\n'
        "SORT control_id ASC\n"
        "```\n\n"
        "### Controls requiring attention\n\n"
        "```dataview\n"
        "TABLE control_id AS ID, implementation_status AS Status, system AS System\n"
        'FROM "Controls"\n'
        'WHERE implementation_status != "implemented"\n'
        "SORT implementation_status ASC, control_id ASC\n"
        "```\n\n"
        "### Inherited controls with source\n\n"
        "```dataview\n"
        "TABLE control_id AS ID, origination AS Origination,\n"
        '      default(inherited_from, component) AS "Provider"\n'
        'FROM "Controls"\n'
        'WHERE contains(origination, "inherited") OR contains(origination, "hybrid")\n'
        "SORT control_id ASC\n"
        "```\n\n"
        "### System component breakdown\n\n"
        "```dataview\n"
        "TABLE component_kind AS Kind, implementation_model AS Model,\n"
        "      responsible_party AS Owner, fedramp_package AS Package\n"
        'FROM "Components"\n'
        'WHERE !contains(tags, "component/synthetic")\n'
        "SORT component_kind ASC, name ASC\n"
        "```\n\n"
        "### Inheritance burndown\n\n"
        "> Count of inherited controls vs. those still missing a resolved provider.\n\n"
        "```dataview\n"
        "TABLE control_id AS ID, origination AS Origination\n"
        'FROM "Controls"\n'
        'WHERE contains(origination, "inherited")\n'
        "SORT control_id ASC\n"
        "```\n\n"
        "### Tag-based status distribution\n\n"
        "```dataview\n"
        "TABLE length(rows) AS Count\n"
        'FROM "Controls"\n'
        "FLATTEN tags\n"
        'WHERE startswith(tags, "control/status/")\n'
        "GROUP BY tags AS Status\n"
        "SORT Count DESC\n"
        "```\n"
    )
    return VaultNote(
        relpath="Index.md",
        frontmatter={
            "type": "okf.index",
            "note_type": "index",
            "last_updated": _today(),
        },
        body=body,
    )


# ---------------------------------------------------------------------------
# Mapping notes (vocabulary crosswalks)
# ---------------------------------------------------------------------------


def build_template_notes() -> List[VaultNote]:
    """Emit the `_meta/templates/` skeletons.

    Updated with OSCAL + OKF type taxonomy.
    """
    control_template = VaultNote(
        relpath="_meta/templates/Control.md",
        frontmatter={
            "type": "oscal.control",
            "oscal_model": "control",
            "kind": "canonical",
            "note_type": "control",
            "control_id": "{{title}}",
            "title": "{{title}}",
            "family": "{{title|slice:0:2|upper}}",
            "enhancement": None,
            "system": "",
            "implementation_status": "not-implemented",
            "origination": ["system-specific"],
            "responsible_role": [],
            "statement_parts": [],
            "evidence": [],
            "up_plan": "",
            "sub_controls": [],
            "status": "active",
            "owner": None,
            "reviewed": None,
            "exportable": False,
            "tags": [
                "control/family/{{title|slice:0:2|lower}}",
                "control/status/not-implemented",
            ],
        },
        body=(
            "# {{title}}\n\n"
            "# Upstream\n\n"
            "> Original upstream control language.\n\n"
            "# Parameters\n\n"
            "```dataview\n"
            'TABLE parameter.param_id AS "Parameter", parameter.value AS "Value"\n'
            "FROM [[]]\n"
            "FLATTEN parameters AS parameter\n"
            "```\n\n"
            "# Statement Parts\n\n"
            "## Part a\n"
            "statement_part:: a\n"
            "inheritance_mode:: local\n"
            "implementation_status:: \n\n"
            "_(Describe how Part a is implemented.)_\n\n"
        ),
    )
    system_template = VaultNote(
        relpath="_meta/templates/System.md",
        frontmatter={
            "type": "oscal.ssp",
            "oscal_model": "system-security-plan",
            "kind": "canonical",
            "note_type": "system",
            "name": "{{title}}",
            "acronym": "",
            "status": "operational",
            "organization": "",
            "components": [],
            "owner": None,
            "tags": ["system/status/operational"],
        },
        body=(
            "# {{title}}\n\n"
            "## Description\n\n"
            "_(System description -- paste from system-characteristics.description.)_\n\n"
            "## Authorization Boundary\n\n"
            "_(Describe the authorization boundary.)_\n\n"
            "## Components\n\n"
            "_(Link to component notes.)_\n"
        ),
    )
    control_part_template = VaultNote(
        relpath="_meta/templates/ControlPart.md",
        frontmatter={
            "type": "oscal.x.control_part",
            "oscal_model": "control-part",
            "kind": "extension",
            "note_type": "control_part",
            "control_id": "{{control_id}}",
            "part_id": "{{part_id}}",
            "part_letter": "{{part_letter}}",
            "component": "",
            "inheritance_mode": "undetermined",
            "implementation_status": "not-implemented",
            "statement_part": "{{part_letter}}",
            "tags": [
                "control-part/mode/undetermined",
                "control-part/status/not-implemented",
            ],
        },
        body=(
            "# {{control_id}} — Part {{part_letter}}\n\n"
            "component:: {{component}}\n"
            "inheritance_mode:: undetermined\n"
            "implementation_status:: not-implemented\n\n"
            "## Implementation\n\n"
            "_(Describe how this part is implemented by the linked component.)_\n\n"
            "## Evidence\n\n"
            "_(Link to evidence notes.)_\n"
        ),
    )
    return [control_template, system_template, control_part_template]


def build_bases_views() -> List[VaultNote]:
    """Emit Obsidian **Bases** views (`_meta/views/*.base`) — the native
    successor to the Dataview code blocks (ADR-025).

    Bases files are YAML. They render frontmatter as sortable/filterable tables
    without a plugin. The Dataview blocks in `Index.md` are kept for backward
    compatibility with users who have not upgraded to Bases.

    One ``.base`` is emitted per queryable note folder so that *every* vault
    table has a Base-compatible, Dataview-generatable equivalent (not just
    Controls). Each view's `order`/`sort`/property names reference only fields
    declared in ``vault-schemas.json`` so they pass
    ``scripts/validate_dataview_queries.py`` ``_validate_bases_file``.
    """

    class _ObsidianBaseDumper(yaml.SafeDumper):
        """Match Obsidian's nested sequence indentation when serializing Bases."""

        def increase_indent(self, flow=False, indentless=False):  # type: ignore[no-untyped-def]
            return super().increase_indent(flow, False)

    def _base(folder: str, properties: dict, views: list) -> str:
        doc = {
            "filters": {"and": [f'file.inFolder("{folder}")']},
            "properties": properties,
            "views": views,
        }
        return yaml.dump(
            doc,
            Dumper=_ObsidianBaseDumper,
            sort_keys=False,
            allow_unicode=True,
        )

    specs: List[tuple[str, str, str]] = [
        (
            "controls",
            "Controls",
            _base(
                "Controls",
                {
                    "control_id": {"displayName": "Control"},
                    "implementation_status": {"displayName": "Status"},
                    "origination": {"displayName": "Origination"},
                    "family": {"displayName": "Family"},
                },
                [
                    {
                        "type": "table",
                        "name": "All controls",
                        "order": [
                            "control_id",
                            "implementation_status",
                            "origination",
                            "family",
                        ],
                        "sort": [{"property": "control_id", "direction": "ASC"}],
                    },
                    {
                        "type": "table",
                        "name": "Requiring attention",
                        "filters": {"and": ['implementation_status != "implemented"']},
                        "order": ["control_id", "implementation_status"],
                        "sort": [
                            {"property": "implementation_status", "direction": "ASC"}
                        ],
                    },
                    {
                        "type": "table",
                        "name": "Inherited / hybrid",
                        "filters": {
                            "or": [
                                'origination.contains("inherited")',
                                'origination.contains("hybrid")',
                            ]
                        },
                        "order": ["control_id", "origination", "component"],
                    },
                ],
            ),
        ),
        (
            "components",
            "Components",
            _base(
                "Components",
                {
                    "name": {"displayName": "Component"},
                    "component_type": {"displayName": "Type"},
                    "status": {"displayName": "Status"},
                    "fips_199": {"displayName": "FIPS 199"},
                    "table_record_count": {"displayName": "Typed tables"},
                    "table_kinds": {"displayName": "Table kinds"},
                    "table_structured_count": {"displayName": "Structured"},
                    "table_unmapped_count": {"displayName": "Unmapped"},
                },
                [
                    {
                        "type": "table",
                        "name": "All components",
                        "order": [
                            "name",
                            "component_type",
                            "status",
                            "fips_199",
                            "table_record_count",
                            "table_kinds",
                        ],
                        "sort": [{"property": "name", "direction": "ASC"}],
                    },
                    {
                        "type": "table",
                        "name": "Typed table coverage",
                        "filters": {"and": ["table_record_count > 0"]},
                        "order": [
                            "name",
                            "table_record_count",
                            "table_kinds",
                            "table_structured_count",
                            "table_unmapped_count",
                        ],
                        "sort": [
                            {"property": "table_unmapped_count", "direction": "DESC"}
                        ],
                    },
                ],
            ),
        ),
        (
            "relationships",
            "Relationships",
            _base(
                "Relationships",
                {
                    "title": {"displayName": "Relationship"},
                    "table_record_count": {"displayName": "Typed tables"},
                    "table_kinds": {"displayName": "Table kinds"},
                    "table_structured_count": {"displayName": "Structured"},
                    "table_unmapped_count": {"displayName": "Unmapped"},
                },
                [
                    {
                        "type": "table",
                        "name": "Typed protocol tables",
                        "filters": {"and": ["table_record_count > 0"]},
                        "order": [
                            "title",
                            "table_record_count",
                            "table_kinds",
                            "table_structured_count",
                            "table_unmapped_count",
                        ],
                    }
                ],
            ),
        ),
        (
            "inheritance",
            "Inheritance",
            _base(
                "Inheritance",
                {
                    "title": {"displayName": "Inheritance"},
                    "table_record_count": {"displayName": "Typed tables"},
                    "table_kinds": {"displayName": "Table kinds"},
                    "table_structured_count": {"displayName": "Structured"},
                    "table_unmapped_count": {"displayName": "Unmapped"},
                },
                [
                    {
                        "type": "table",
                        "name": "Typed provider tables",
                        "filters": {"and": ["table_record_count > 0"]},
                        "order": [
                            "title",
                            "table_record_count",
                            "table_kinds",
                            "table_structured_count",
                            "table_unmapped_count",
                        ],
                    }
                ],
            ),
        ),
        (
            "systems",
            "Systems",
            _base(
                "Systems",
                {
                    "name": {"displayName": "System"},
                    "fips_199": {"displayName": "FIPS 199"},
                    "impact_level": {"displayName": "Impact"},
                    "status": {"displayName": "Status"},
                },
                [
                    {
                        "type": "table",
                        "name": "All systems",
                        "order": ["name", "fips_199", "impact_level", "status"],
                        "sort": [{"property": "name", "direction": "ASC"}],
                    }
                ],
            ),
        ),
        (
            "plans",
            "Plans",
            _base(
                "Plans",
                {
                    "plan_id": {"displayName": "Plan"},
                    "plan_type": {"displayName": "Type"},
                    "controls_parsed": {"displayName": "Controls"},
                    "status": {"displayName": "Status"},
                },
                [
                    {
                        "type": "table",
                        "name": "All plans",
                        "order": ["plan_id", "plan_type", "controls_parsed", "status"],
                        "sort": [{"property": "plan_id", "direction": "ASC"}],
                    }
                ],
            ),
        ),
        (
            "organizations",
            "Organizations",
            _base(
                "Organizations",
                {
                    "name": {"displayName": "Organization"},
                    "org_type": {"displayName": "Type"},
                    "role": {"displayName": "Role"},
                    "status": {"displayName": "Status"},
                },
                [
                    {
                        "type": "table",
                        "name": "All organizations",
                        "order": ["name", "org_type", "role", "status"],
                        "sort": [{"property": "name", "direction": "ASC"}],
                    }
                ],
            ),
        ),
    ]

    return [
        VaultNote(relpath=f"_meta/views/{name}.base", body=body, raw=True)
        for name, _folder, body in specs
    ]


def build_meta_tags_index() -> VaultNote:
    """Emit `_meta/tags.md` documenting the controlled-vocabulary tag prefixes."""
    body = (
        "# Tags\n\n"
        "Tag prefixes used across this vault, per `docs/OBSIDIAN_VAULT_SCHEMA.md`.\n\n"
        "## control/*\n\n"
        "- `control/family/<ac>` — NIST 800-53 family code (lowercase).\n"
        "- `control/status/<status>` — OSBAPI `ImplementationStatus` enum value.\n"
        "- `control/baseline/<low|moderate|high>` — FedRAMP baseline level.\n\n"
        "## system/*\n\n"
        "- `system/fips/<low|moderate|high>` — FIPS 199 categorization.\n"
        "- `system/status/<operational|under-development|other>` — system state.\n\n"
        "## component/*\n\n"
        "- `component/type/<platform|service|infrastructure|tool|process|system>` — "
        "what kind of component this is. Synthetic component notes carry "
        "`component/synthetic` in addition.\n"
        "- `component/fedramp/<low|moderate|high>` — FedRAMP package level.\n\n"
        "## plan/* / assessment/* / evidence/* / org/*\n\n"
        "See the corresponding note-type sections of `docs/OBSIDIAN_VAULT_SCHEMA.md`.\n"
    )
    return VaultNote(
        relpath="_meta/tags.md",
        frontmatter={
            "type": "okf.index",
            "note_type": "index",
            "last_updated": _today(),
        },
        body=body,
    )


def build_mapping_notes(narrative: Optional[Dict[str, Any]] = None) -> List[VaultNote]:
    """Build the `Mappings/*.md` crosswalk notes.

    Emits the OKF type taxonomy:
      type: okf.mapping

    When ``narrative`` carries a populated ``okf_mapping`` object (extracted
    APPENDIX-B references + applicable laws), a `Mappings/references.md` note is
    added so that extracted content is not dropped (D-6 / #41).
    """
    notes: List[VaultNote] = []
    notes.append(_implementation_status_mapping_note())
    notes.append(_control_origination_mapping_note())
    notes.append(_interop_profile_mapping_note())
    ref_note = _references_mapping_note(narrative)
    if ref_note is not None:
        notes.append(ref_note)
    return notes


def _references_mapping_note(
    narrative: Optional[Dict[str, Any]],
) -> Optional[VaultNote]:
    """Build `Mappings/references.md` from extracted references / laws.

    No-op unless the ``okf_mapping`` extractor object carries content.
    """
    if not narrative:
        return None
    objs = _narrative_object_index(narrative)
    mapping_obj = objs.get("okf_mapping")
    if not mapping_obj:
        return None
    props = mapping_obj.get("properties") or {}
    sections: List[str] = []
    for prop_name, heading in (
        ("references", "References"),
        ("applicable_laws_and_regulations", "Applicable Laws & Regulations"),
    ):
        if prop_name not in props:
            continue
        md = _narrative_value_to_md(props[prop_name])
        if md:
            sections.append(f"## {heading}\n\n{md}")
    if not sections:
        return None
    body = (
        "# References & Applicable Laws\n\n"
        "> Recovered from SSP appendices by the contract-driven `heading_parser`.\n\n"
        + "\n\n".join(sections)
        + "\n"
    )
    return VaultNote(
        relpath=_note_path("Mappings", "references"),
        frontmatter={
            "type": "okf.mapping",
            "note_type": "mapping",
            "mapping": "references",
            "provenance": "heading_parser",
            "last_updated": _today(),
        },
        body=body,
    )


def _implementation_status_mapping_note() -> VaultNote:
    table = vocab._status_table()
    mapping = table.get("mapping") or {}
    default = table.get("default") or {}

    lines = [
        "# Implementation Status Crosswalk",
        "",
        "Maps raw OSCAL/FedRAMP `implementation-status.state` values to the "
        "coarser OSBAPI `ImplementationStatus` enum. Source of truth: "
        "`compliance/mappings/implementation-status-map.yaml`.",
        "",
        "| Raw OSCAL | OSBAPI enum | Remark |",
        "|---|---|---|",
    ]
    for raw, entry in sorted(mapping.items()):
        lines.append(
            f"| `{raw}` | `{entry.get('target', '')}` | {(entry.get('remark') or '').replace(chr(10), ' ').strip()} |"
        )
    if default:
        lines.append(
            f"| _(default)_ | `{default.get('target', '')}` | "
            f"{(default.get('remark') or '').replace(chr(10), ' ').strip()} |"
        )
    body = "\n".join(lines) + "\n"
    return VaultNote(
        relpath=_note_path("Mappings", "implementation-status"),
        frontmatter={
            "type": "okf.mapping",
            "note_type": "mapping",
            "mapping": "implementation-status",
            "last_updated": _today(),
        },
        body=body,
    )


def _control_origination_mapping_note() -> VaultNote:
    table = vocab._origination_table()
    mapping = table.get("mapping") or {}
    precedence = table.get("precedence") or []
    default = table.get("default") or {}

    lines = [
        "# Control Origination Crosswalk",
        "",
        "Maps raw FedRAMP `control-origination` flags onto the OSBAPI "
        "`ControlOrigination` enum. When multiple raw flags are set "
        "simultaneously, the precedence order picks the most "
        "specific/informative single OSBAPI value. Source of truth: "
        "`compliance/mappings/control-origination-map.yaml`.",
        "",
        "### Precedence (highest first)",
        "",
        *[f"1. `{p}`" for p in precedence],
        "",
        "### Mapping",
        "",
        "| Raw flag | OSBAPI enum | Remark |",
        "|---|---|---|",
    ]
    for raw, entry in sorted(mapping.items()):
        lines.append(
            f"| `{raw}` | `{entry.get('target', '')}` | {(entry.get('remark') or '').replace(chr(10), ' ').strip()} |"
        )
    if default:
        lines.append(
            f"| _(default)_ | `{default.get('target', '')}` | "
            f"{(default.get('remark') or '').replace(chr(10), ' ').strip()} |"
        )
    body = "\n".join(lines) + "\n"
    return VaultNote(
        relpath=_note_path("Mappings", "control-origination"),
        frontmatter={
            "type": "okf.mapping",
            "note_type": "mapping",
            "mapping": "control-origination",
            "last_updated": _today(),
        },
        body=body,
    )


def _interop_profile_mapping_note() -> VaultNote:
    body = (
        "# Interoperability Profile Reference\n\n"
        "This vault records raw OSCAL implementation-status and control-origination "
        "values on each control note. The versioned mapping notes derive the "
        "OSBAPI / Backstage projection used by downstream catalogs. The "
        "recommended projection mapping is documented under "
        "`docs/osbapi-backstage-interop/`. The local frontmatter schema is "
        "captured in `vault-schemas.json` at the repo root.\n"
    )
    return VaultNote(
        relpath=_note_path("Mappings", "interop-profile"),
        frontmatter={
            "type": "okf.mapping",
            "note_type": "mapping",
            "mapping": "interop-profile",
            "last_updated": _today(),
        },
        body=body,
    )


# ---------------------------------------------------------------------------
# Extension type builders (oscal.x.*) — R4 implementation
# ---------------------------------------------------------------------------


def build_extension_templates() -> List[VaultNote]:
    """Emit template notes for all `oscal.x.*` extension types.

    These are scaffolding notes for types the pipeline does not currently
    populate from the SSP (fismatic doesn't produce assessments, POA&Ms,
    real findings, etc.). They provide a starting point for hand-editing.
    """
    templates: List[VaultNote] = []

    # oscal.x.diagram — Mermaid-aware template
    templates.append(
        VaultNote(
            relpath="_meta/templates/Diagram.md",
            frontmatter={
                "type": "oscal.x.diagram",
                "note_type": "diagram",
                "extension_model": "diagram",
                "kind": "extension",
                "title": "{{title}}",
                "diagram_id": "diag-{{title}}",
                "diagram_kind": "architecture",
                "system_id": "",
                "status": "active",
                "owner": None,
                "reviewed": None,
                "relates_to": [],
                "tags": ["oscal/extension/diagram"],
            },
            body=(
                "# {{title}}\n\n"
                "```mermaid\n"
                "flowchart LR\n"
                "    A[Component A] -->|data flow| B[Component B]\n"
                "```\n\n"
                "## Nodes\n\n"
                "_(Describe each node's role in the architecture.)_\n\n"
                "## References\n\n"
                "_(Link to related systems, components, boundaries.)_\n"
            ),
        )
    )

    # oscal.x.evidence
    templates.append(
        VaultNote(
            relpath="_meta/templates/Evidence.md",
            frontmatter={
                "type": "oscal.x.evidence",
                "note_type": "evidence",
                "extension_model": "evidence",
                "kind": "extension",
                "title": "{{title}}",
                "evidence_id": "ev-{{title}}",
                "evidence_kind": "api_export",
                "system_id": "",
                "control_id": "",
                "component_id": "",
                "collected": None,
                "expires": None,
                "evidence_status": "valid",
                "source_uri": "",
                "status": "active",
                "owner": None,
                "tags": ["oscal/extension/evidence"],
            },
            body=(
                "# {{title}}\n\n"
                "## Collection Method\n\n"
                "_(Describe how this evidence was collected.)_\n\n"
                "## Artifact\n\n"
                "_(Link or embed the evidence artifact.)_\n"
            ),
        )
    )

    # oscal.x.finding
    templates.append(
        VaultNote(
            relpath="_meta/templates/Finding.md",
            frontmatter={
                "type": "oscal.x.finding",
                "note_type": "finding",
                "extension_model": "finding",
                "kind": "extension",
                "title": "{{title}}",
                "finding_id": "finding-{{title}}",
                "system_id": "",
                "assessment_results_ref": "",
                "control_id": "",
                "component_id": "",
                "severity": "moderate",
                "status": "open",
                "related_poam": "",
                "owner": None,
                "tags": ["oscal/extension/finding"],
            },
            body=(
                "# {{title}}\n\n"
                "## Description\n\n"
                "_(Describe the finding.)_\n\n"
                "## Remediation\n\n"
                "_(Describe remediation plan or link to POA&M.)_\n"
            ),
        )
    )

    # oscal.x.inheritance
    templates.append(
        VaultNote(
            relpath="_meta/templates/Inheritance.md",
            frontmatter={
                "type": "oscal.x.inheritance",
                "note_type": "inheritance",
                "extension_model": "inheritance",
                "kind": "extension",
                "title": "{{title}}",
                "inheritance_id": "inh-{{title}}",
                "system_id": "",
                "provider": "",
                "consumer": "",
                "controls": [],
                "status": "active",
                "owner": None,
                "tags": ["oscal/extension/inheritance"],
            },
            body=(
                "# {{title}}\n\n"
                "## Provider\n\n"
                "_(Describe the providing system or component.)_\n\n"
                "## Inherited Controls\n\n"
                "_(List controls inherited from this provider.)_\n"
            ),
        )
    )

    # oscal.x.boundary
    templates.append(
        VaultNote(
            relpath="_meta/templates/Boundary.md",
            frontmatter={
                "type": "oscal.x.boundary",
                "note_type": "boundary",
                "extension_model": "boundary",
                "kind": "extension",
                "title": "{{title}}",
                "boundary_id": "bnd-{{title}}",
                "system_id": "",
                "zones": [],
                "status": "active",
                "owner": None,
                "tags": ["oscal/extension/boundary"],
            },
            body=(
                "# {{title}}\n\n"
                "## Zones\n\n"
                "_(Describe trust zones within this boundary.)_\n\n"
                "## Diagram\n\n"
                "_(Link to a [[diagrams/]] note with a Mermaid architecture diagram.)_\n"
            ),
        )
    )

    # oscal.x.decision (ADR)
    templates.append(
        VaultNote(
            relpath="_meta/templates/Decision.md",
            frontmatter={
                "type": "oscal.x.decision",
                "note_type": "decision",
                "extension_model": "decision",
                "kind": "extension",
                "title": "{{title}}",
                "decision_id": "adr-{{title}}",
                "system_id": "",
                "status": "proposed",
                "date": None,
                "affects": [],
                "owner": None,
                "tags": ["oscal/extension/decision"],
            },
            body=(
                "# {{title}}\n\n"
                "## Context\n\n"
                "_(What is the decision about?)_\n\n"
                "## Decision\n\n"
                "_(What was decided?)_\n\n"
                "## Rationale\n\n"
                "_(Why was this decision made?)_\n\n"
                "## Consequences\n\n"
                "_(What are the implications?)_\n"
            ),
        )
    )

    # oscal.x.relationship
    templates.append(
        VaultNote(
            relpath="_meta/templates/Relationship.md",
            frontmatter={
                "type": "oscal.x.relationship",
                "note_type": "relationship",
                "extension_model": "relationship",
                "kind": "extension",
                "title": "{{title}}",
                "relationship_id": "rel-{{title}}",
                "subject_ref": "",
                "predicate": "implements",
                "object_ref": "",
                "system_id": "",
                "status": "active",
                "tags": ["oscal/extension/relationship"],
            },
            body=(
                "# {{title}}\n\n"
                "## Subject\n\n"
                "_(Link to the subject note.)_\n\n"
                "## Predicate\n\n"
                "implements\n\n"
                "## Object\n\n"
                "_(Link to the object note.)_\n"
            ),
        )
    )

    return templates


# ---------------------------------------------------------------------------
# Chapters 1-12 narrative notes (contract-driven, via heading_parser)
# ---------------------------------------------------------------------------
#
# fismatic (the primary parser) only reads Chapter 13 control tables, so the
# System note historically carried three hardcoded placeholder strings
# ("System Name" / "System Description" / boundary boilerplate) and none of
# the Chapters 1-12 narrative made it into the vault. `heading_parser.extract`
# (or `extract_from_docx`) CAN route that narrative to typed records, but it
# was only wired into the feedback/roundtrip tooling — never the vault export.
#
# `build_narrative_notes()` closes that gap: given the `heading_parser`
# extraction result (a `{"objects": [...], "summary": {...}}` dict), it emits
# `oscal.x.*` extension notes (boundary, diagram, inheritance, relationship,
# runbook, decision) populated with the real Chapters 1-12 content, and
# `enrich_system_note_from_narrative()` overlays the real system description /
# authorization boundary onto the System note body + frontmatter.


def _md_escape_cell(text: str) -> str:
    """Escape a value for use inside a markdown table cell."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s.replace("|", "\\|")


def _render_extracted_table(tbl: Dict[str, Any]) -> str:
    """Render a heading_parser table dict ({headers, rows}) as a markdown table.

    The extractor emits tables as ``{"table_index", "headers": [...],
    "rows": [[...], ...]}``. We render a GitHub-flavored markdown table; an
    empty/malformed table yields an empty string.
    """
    headers = tbl.get("headers") or []
    rows = tbl.get("rows") or []
    if not headers and not rows:
        return ""
    # Normalize row widths to the header width (pad/truncate).
    width = max([len(headers)] + [len(r) for r in rows]) if (headers or rows) else 0
    if width == 0:
        return ""
    hdr = [
        (_md_escape_cell(headers[i]) if i < len(headers) else "") for i in range(width)
    ]
    lines = [
        "| " + " | ".join(hdr) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for r in rows:
        cells = [(_md_escape_cell(r[i]) if i < len(r) else "") for i in range(width)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _narrative_value_to_md(value: Any) -> str:
    """Render one extracted property value (string, table dict, or list) as md."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict) and ("headers" in value or "rows" in value):
        return _render_extracted_table(value)
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            rendered = _narrative_value_to_md(item)
            if rendered:
                parts.append(rendered)
        return "\n\n".join(parts)
    return _md_escape_cell(str(value))


# Which extracted `oscal_x_*` objects become narrative notes, and how their
# properties map onto note sections. Order controls note/section order.
# Each entry: extractor object_name -> (folder, note_type, extension_model,
#   title, tag, [(property_name, section_heading), ...]).
_NARRATIVE_NOTE_SPECS: List[Dict[str, Any]] = [
    {
        "object": "oscal_x_boundary",
        "folder": "Boundaries",
        "note_type": "boundary",
        "extension_model": "boundary",
        "title_suffix": "Authorization Boundary",
        "id_prefix": "bnd",
        "tag": "oscal/extension/boundary",
        "sections": [
            ("description", "Network Architecture"),
            ("components_and_boundaries", "Components and Boundaries"),
            ("locations", "Locations"),
            ("website_urls", "Website URLs"),
            ("types_of_users", "Types of Users"),
            ("common_integrations", "Common Integrations"),
        ],
    },
    {
        "object": "oscal_x_relationship",
        "folder": "Relationships",
        "note_type": "relationship",
        "extension_model": "relationship",
        "title_suffix": "Interconnections & Data Flow",
        "id_prefix": "rel",
        "tag": "oscal/extension/relationship",
        "sections": [
            ("interconnections", "System Interconnections"),
            ("ports_protocols_services", "Ports, Protocols, and Services"),
        ],
    },
    {
        "object": "oscal_x_inheritance",
        "folder": "Inheritance",
        "note_type": "inheritance",
        "extension_model": "inheritance",
        "title_suffix": "Leveraged / Inherited Controls",
        "id_prefix": "inh",
        "tag": "oscal/extension/inheritance",
        "sections": [
            ("direction", "Direction"),
            ("provided_by", "Provided By"),
        ],
    },
    {
        "object": "oscal_x_runbook",
        "folder": "Runbooks",
        "note_type": "runbook",
        "extension_model": "runbook",
        "title_suffix": "DevSecOps & Operations",
        "id_prefix": "rbk",
        "tag": "oscal/extension/runbook",
        "sections": [
            ("discipline", "Discipline"),
            ("devops_devsecops", "DevOps / DevSecOps"),
            ("container_workload", "Container Workload"),
            ("aws_management", "AWS Management"),
        ],
    },
    {
        "object": "oscal_x_decision",
        "folder": "Decisions",
        "note_type": "decision",
        "extension_model": "decision",
        "title_suffix": "Digital Identity, IPv6 & Showstoppers",
        "id_prefix": "adr",
        "tag": "oscal/extension/decision",
        "sections": [
            ("decision_kind", "Decision Kind"),
            ("digital_identity_acceptance", "Digital Identity Acceptance"),
            ("ipv6_transition", "IPv6 Transition"),
            ("gsa_showstoppers", "GSA Showstoppers"),
        ],
    },
    {
        "object": "oscal_x_diagram",
        "folder": "Diagrams",
        "note_type": "diagram",
        "extension_model": "diagram",
        "title_suffix": "Data Flow & Architecture Diagrams",
        "id_prefix": "diag",
        "tag": "oscal/extension/diagram",
        "sections": [
            ("caption", "Captions"),
        ],
    },
    {
        "object": "oscal_x_crm",
        "folder": "CRM",
        "note_type": "crm",
        "extension_model": "crm",
        "title_suffix": "Customer Responsibility Matrix (Leveraged Controls)",
        "id_prefix": "crm",
        "tag": "oscal/extension/crm",
        "sections": [
            ("crm_narrative", "Overview"),
            ("crm_controls", "Customer Responsibility Controls"),
        ],
    },
    {
        "object": "oscal_x_privacy",
        "folder": "Privacy",
        "note_type": "privacy",
        "extension_model": "privacy",
        "title_suffix": "Privacy & PII Processing",
        "id_prefix": "priv",
        "tag": "oscal/extension/privacy",
        "sections": [
            ("pii_processing", "PII Processing"),
            ("transparency", "Transparency"),
        ],
    },
    {
        "object": "oscal_x_evidence",
        "folder": "Evidence",
        "note_type": "evidence",
        "extension_model": "evidence",
        "title_suffix": "Attachments & Evidence",
        "id_prefix": "ev",
        "tag": "oscal/extension/evidence",
        "sections": [
            ("attachments", "Attachments"),
        ],
    },
]


def _narrative_object_index(narrative: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Index a heading_parser extraction result by extractor object_name."""
    out: Dict[str, Dict[str, Any]] = {}
    for obj in narrative.get("objects") or []:
        name = obj.get("object_name")
        if name:
            out[name] = obj
    return out


def _index_section_guidance(
    section_guidance: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Re-key ``{heading: SectionGuidance}`` by a normalized heading token.

    Uses ``control_guidance._normalize_heading_key`` so section headings render
    on notes ("Network Architecture") match the template's heading text even
    when the template prefixes a section number ("4.1 Network Architecture").
    """
    if not section_guidance:
        return {}
    from compliance.lib.control_guidance import _normalize_heading_key

    out: Dict[str, Any] = {}
    for heading, guidance in section_guidance.items():
        out[_normalize_heading_key(heading)] = guidance
    return out


def _section_guidance_callout(
    guidance_by_key: Dict[str, Any], section_heading: str
) -> Optional[str]:
    """Return the folded guidance callout for a section heading, if any."""
    if not guidance_by_key:
        return None
    from compliance.lib.control_guidance import (
        _normalize_heading_key,
        render_section_guidance_callout,
    )

    guidance = guidance_by_key.get(_normalize_heading_key(section_heading))
    if guidance is None:
        return None
    lines = render_section_guidance_callout(guidance)
    return "\n".join(lines) if lines else None


def enrich_system_note_from_narrative(
    note: VaultNote, narrative: Optional[Dict[str, Any]]
) -> VaultNote:
    """Overlay real Chapters 1-12 system prose onto an already-built System note.

    fismatic emits placeholder strings for ``system-characteristics``; the
    contract-driven ``heading_parser`` recovers the true system description,
    authorization boundary, environment description, and data nature from
    Chapters 9-10. This replaces the System note's placeholder ``## Description``
    / ``## Authorization Boundary`` body with the recovered prose and adds a
    ``## System Environment`` section when available.
    """
    if not narrative:
        return note
    objs = _narrative_object_index(narrative)
    ssp = objs.get("oscal_ssp")
    if not ssp:
        return note
    props = ssp.get("properties") or {}

    description = props.get("description")
    boundary = props.get("authorization_boundary_description")
    environment = props.get("system_environment_description")
    data_nature = props.get("system_data_nature")
    info_type = props.get("information_system_type")
    cloud_service = props.get("cloud_service_model")
    cloud_deployment = props.get("cloud_deployment_model")

    # Only strings are usable as prose here; tables/lists stay in extension notes.
    def _prose(v: Any) -> Optional[str]:
        return v.strip() if isinstance(v, str) and v.strip() else None

    description = _prose(description)
    boundary = _prose(boundary)
    environment = _prose(environment)
    data_nature = _prose(data_nature)
    info_type = _prose(info_type)
    cloud_service = _prose(cloud_service)
    cloud_deployment = _prose(cloud_deployment)

    if not any(
        [
            description,
            boundary,
            environment,
            data_nature,
            info_type,
            cloud_service,
            cloud_deployment,
        ]
    ):
        return note

    # Frontmatter: prefer the recovered boundary over the fismatic placeholder,
    # and surface the recovered cloud service/deployment models (D-4 / #41).
    if boundary:
        note.frontmatter["authorization_boundary"] = boundary
    if cloud_service:
        note.frontmatter["cloud_service_model"] = cloud_service
    if cloud_deployment:
        note.frontmatter["cloud_deployment_model"] = cloud_deployment

    # Rebuild the body, preserving the H1 title line + Components section.
    lines = note.body.splitlines()
    title_line = lines[0] if lines else "# System"

    # Preserve whatever Components section the original body had.
    comp_section: List[str] = []
    if "## Components" in note.body:
        idx = note.body.index("## Components")
        comp_section = note.body[idx:].rstrip().splitlines()

    body_lines: List[str] = [title_line, ""]
    body_lines += [
        "## Description",
        "",
        description or "_No description present in source SSP._",
        "",
    ]
    if info_type:
        body_lines += ["## Information System Type", "", info_type, ""]
    if cloud_service:
        body_lines += ["## Cloud Service Model", "", cloud_service, ""]
    if cloud_deployment:
        body_lines += ["## Cloud Deployment Model", "", cloud_deployment, ""]
    if boundary:
        body_lines += ["## Authorization Boundary", "", boundary, ""]
    if environment:
        body_lines += ["## System Environment", "", environment, ""]
    if data_nature:
        body_lines += ["## System Data Nature", "", data_nature, ""]
    if comp_section:
        body_lines += comp_section + [""]

    note.body = "\n".join(body_lines).rstrip() + "\n"
    return note


def build_narrative_notes(
    narrative: Optional[Dict[str, Any]],
    *,
    index: VaultIndex,
    system_id: str = "",
    media_embeds: Optional[List[str]] = None,
    section_guidance: Optional[Dict[str, Any]] = None,
) -> List[VaultNote]:
    """Emit `oscal.x.*` extension notes carrying Chapters 1-12 narrative.

    ``narrative`` is the result of ``heading_parser.extract`` /
    ``extract_from_docx`` — a ``{"objects": [...], "summary": {...}}`` dict.
    For each populated ``oscal_x_*`` object we know how to render (see
    ``_NARRATIVE_NOTE_SPECS``) we emit one populated note. Objects with no
    extracted properties are skipped (their scaffolding template still ships
    via ``build_extension_templates``).

    ``media_embeds`` optionally lists attachment filenames (under
    ``Diagrams/media/``) that were extracted from the source ``.docx``; when
    present they are embedded in the Diagram note with Obsidian ``![[...]]``
    syntax so the real images render inline.

    ``section_guidance`` optionally maps Chapters 1-12 section-heading text to a
    ``control_guidance.SectionGuidance`` object (from
    ``extract_section_guidance``). When a rendered section's heading matches a
    recovered guidance section, its authoring instructions are appended as a
    folded ``> [!note]- Template guidance`` callout so the original template
    prompts travel with the narrative content (R19).

    Returns an empty list when ``narrative`` is None/empty so the fismatic-only
    path (and existing tests) are unaffected.
    """
    if not narrative:
        return []
    guidance_by_key = _index_section_guidance(section_guidance)
    objs = _narrative_object_index(narrative)
    system_name = next(iter(index.systems), "System")
    notes: List[VaultNote] = []

    for spec in _NARRATIVE_NOTE_SPECS:
        obj = objs.get(spec["object"])
        is_diagram = spec["extension_model"] == "diagram"
        props = (obj or {}).get("properties") or {}
        # A diagram note is worth emitting when there are real image files to
        # embed, even if the metadata-only image_rid/media_part props are empty.
        if not obj and not (is_diagram and media_embeds):
            continue
        if not props and not (is_diagram and media_embeds):
            continue

        # Render each mapped section that has content.
        rendered_sections: List[str] = []
        for prop_name, section_heading in spec["sections"]:
            if prop_name not in props:
                continue
            md = _narrative_value_to_md(props[prop_name])
            if md:
                section = f"## {section_heading}\n\n{md}"
                callout = _section_guidance_callout(guidance_by_key, section_heading)
                if callout:
                    section = f"{section}\n\n{callout}"
                rendered_sections.append(section)

        table_pairs = [
            (props[prop_name], section_heading)
            for prop_name, section_heading in spec["sections"]
            if prop_name in props
        ]

        # Embed extracted image files into the diagram note.
        if is_diagram and media_embeds:
            embeds = "\n\n".join(f"![[{fname}]]" for fname in media_embeds)
            rendered_sections.insert(0, f"## Diagrams\n\n{embeds}")

        if not rendered_sections:
            continue

        title = f"{system_name} — {spec['title_suffix']}"
        slug = slugify(f"{system_name}-{spec['note_type']}")
        relpath = _note_path(spec["folder"], slug)
        fm: Dict[str, Any] = {
            "type": f"oscal.x.{spec['extension_model']}",
            "oscal_model": None,
            "extension_model": spec["extension_model"],
            "kind": "extension",
            "note_type": spec["note_type"],
            "title": title,
            f"{spec['note_type']}_id": f"{spec['id_prefix']}-{slug}",
            "system_id": system_id or "",
            "status": "active",
            "owner": None,
            "source_chapter": "1-12",
            "provenance": "heading_parser",
            "tags": [spec["tag"], "narrative/chapters-1-12"],
        }
        projections = table_projection(
            [value for value, _heading in table_pairs],
            [heading for _value, heading in table_pairs],
        )
        projections.pop("_records")
        if projections["table_record_count"]:
            fm.update(projections)
        # The diagram note has a stricter concrete schema requiring
        # `diagram_kind`; supply a sensible default so it validates.
        if is_diagram:
            fm["diagram_kind"] = "dataflow"
            if media_embeds:
                fm["media_files"] = list(media_embeds)
        # The evidence note validates against the concrete Evidence schema,
        # which requires `evidence_id` + `evidence_kind` (not `<type>_id`).
        if spec["extension_model"] == "evidence":
            fm["evidence_id"] = fm.pop("evidence_id", f"{spec['id_prefix']}-{slug}")
            fm["evidence_kind"] = "attachment"
        body = (
            f"# {title}\n\n"
            "> Recovered from SSP Chapters 1-12 by the contract-driven "
            "`heading_parser` (content the primary Chapter-13 parser drops).\n\n"
            + "\n\n".join(rendered_sections)
            + "\n"
        )
        notes.append(VaultNote(relpath=relpath, frontmatter=fm, body=body))

    return notes


# ---------------------------------------------------------------------------
# Diagram image attachments (binary assets)
# ---------------------------------------------------------------------------

# Vault sub-folder (relative to the Diagrams/ note folder) for image bytes.
_MEDIA_SUBDIR = "media"


def _media_filename(system_name: str, media_stem: str, ext: str) -> str:
    """Deterministic, filesystem-safe attachment filename.

    Derived from the docx media part name (e.g. ``image3``) prefixed with the
    system slug, so it is stable across regenerations and human-readable:
    ``gsa-pages-image3.png``.
    """
    sys_slug = slugify(system_name)
    stem_slug = slugify(media_stem) or "image"
    ext = ext if ext.startswith(".") else f".{ext}"
    return f"{sys_slug}-{stem_slug}{ext}"


def build_media_notes(
    images: Optional[Dict[str, Any]],
    *,
    index: VaultIndex,
) -> List[VaultNote]:
    """Emit binary attachment notes for every extracted diagram image.

    ``images`` maps ``rId -> compliance.lib.docx_images.ExtractedImage`` (or any
    object exposing ``.data``, ``.ext``, ``.media_stem``). Each becomes a
    binary ``VaultNote`` written to ``Diagrams/media/<slug>-<stem><ext>``.

    Returns an empty list when ``images`` is None/empty. The companion Diagram
    markdown note (see ``build_narrative_notes``) embeds these files with
    Obsidian ``![[...]]`` syntax.
    """
    if not images:
        return []
    system_name = next(iter(index.systems), "System")
    notes: List[VaultNote] = []
    seen: set = set()
    for _rid, img in images.items():
        data = getattr(img, "data", None)
        if not data:
            continue
        ext = getattr(img, "ext", ".png")
        media_stem = getattr(img, "media_stem", "image")
        fname = _media_filename(system_name, media_stem, ext)
        if fname in seen:
            continue
        seen.add(fname)
        relpath = f"Diagrams/{_MEDIA_SUBDIR}/{fname}"
        # Record the embed target in the index so the diagram note can link it.
        index.media[str(_rid)] = fname
        notes.append(VaultNote(relpath=relpath, data=data))
    return notes


def build_okf_schema_note(
    plan_id: str, narrative: Optional[Dict[str, Any]] = None
) -> VaultNote:
    """Build `governance/schema.md` — the OKF schema registry.

    Documents all note types, their required fields, and controlled
    vocabularies in one discoverable place. When ``narrative`` carries a
    populated ``okf_schema_glossary`` object (APPENDIX-A terms), the extracted
    glossary is appended so it is not dropped (D-6 / #41).
    """
    body = (
        "# OKF Schema Registry\n\n"
        "This note documents the type taxonomy and field schemas used across\n"
        "this compliance vault. It serves as the single source of truth for\n"
        "authors, validators, and automation.\n\n"
        "## Type Taxonomy\n\n"
        "Types use a dotted namespace convention:\n"
        "- `oscal.*` — canonical NIST OSCAL model concepts\n"
        "- `oscal.x.*` — project extensions beyond NIST OSCAL\n"
        "- `okf.*` — vault governance infrastructure\n\n"
        "### Canonical OSCAL Types\n\n"
        "| Type | OSCAL Model | Folder | Key ID Field |\n"
        "|------|-------------|--------|-------------|\n"
        "| `oscal.control` | `control` | `controls/` | `control_id` |\n"
        "| `oscal.component_definition` | `component-definition` | `components/` | `component_id` |\n"
        "| `oscal.ssp` | `system-security-plan` | `systems/`, `plans/` | `plan_id` |\n"
        "| `oscal.party` | `party` | `organizations/` | `party_uuid` |\n\n"
        "### Extension Types\n\n"
        "| Type | Extension Model | Folder | Key ID Field |\n"
        "|------|----------------|--------|-------------|\n"
        "| `oscal.x.diagram` | `diagram` | `diagrams/` | `diagram_id` |\n"
        "| `oscal.x.evidence` | `evidence` | `evidence/` | `evidence_id` |\n"
        "| `oscal.x.finding` | `finding` | `findings/` | `finding_id` |\n"
        "| `oscal.x.inheritance` | `inheritance` | `inheritance/` | `inheritance_id` |\n"
        "| `oscal.x.boundary` | `boundary` | `boundaries/` | `boundary_id` |\n"
        "| `oscal.x.decision` | `decision` | `decisions/` | `decision_id` |\n"
        "| `oscal.x.relationship` | `relationship` | `relationships/` | `relationship_id` |\n\n"
        "Extension note types are authoring identities, not automatically custom "
        "OSCAL output. Active property adapters currently consume boundary "
        "description/types-of-users, relationship interconnections/PPS, and "
        "inheritance provided-by. Diagram and CRM narrative notes, plus the other "
        "boundary/inheritance properties, are explicitly unimplemented. CRM `.ods` "
        "data is instead merged into control frontmatter and exported through native "
        "SSP by-component export. Legacy `oscal.x.*` aliases remain for migration.\n\n"
        "The generated contract census is 13 `native_core`, 5 "
        "`authoring_extension`, 7 `true_extension`, and 19 `unimplemented` "
        "property mappings; 18 are active and adapter-backed. Run "
        "`compliance/scripts/validate_oscal_mappings.py` for the authoritative, "
        "schema-validated report.\n\n"
        "### OKF Infrastructure Types\n\n"
        "| Type | Description |\n"
        "|------|-------------|\n"
        "| `okf.schema` | Type registry and field definitions |\n"
        "| `okf.template` | Reusable note creation templates |\n"
        "| `okf.index` | Master vault index with Dataview dashboards |\n"
        "| `okf.validation_rule` | Automated vault health rules |\n"
        "| `okf.mapping` | Vocabulary crosswalk tables |\n\n"
        "## Required Fields (all domain notes)\n\n"
        "- `type` — dotted type identifier\n"
        "- `title` — human-readable note title\n"
        "- `status` — lifecycle status (active, draft, archived, etc.)\n"
        "- `owner` — responsible party wikilink\n\n"
        "## Required Fields (system-scoped notes)\n\n"
        "- `system_id` — the system this note belongs to\n\n"
        "## Controlled Vocabularies\n\n"
        "See `_meta/tags.md` for tag prefix conventions.\n"
    )
    # Append the extracted glossary (APPENDIX-A terms), if any (D-6 / #41).
    glossary_md = _glossary_section_from_narrative(narrative)
    if glossary_md:
        body += "\n" + glossary_md
    return VaultNote(
        relpath="governance/schema.md",
        frontmatter={
            "type": "okf.schema",
            "note_type": "schema",
            "title": "Vault Schema Registry",
            "status": "active",
            "owner": "grc",
            "last_updated": _today(),
            "tags": ["okf/schema", "governance"],
        },
        body=body,
    )


def _glossary_section_from_narrative(
    narrative: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Render the extracted APPENDIX-A glossary terms as a markdown section."""
    if not narrative:
        return None
    objs = _narrative_object_index(narrative)
    glossary = objs.get("okf_schema_glossary")
    if not glossary:
        return None
    props = glossary.get("properties") or {}
    if "terms" not in props:
        return None
    md = _narrative_value_to_md(props["terms"])
    if not md:
        return None
    return "## Glossary (from source SSP APPENDIX-A)\n\n" + md + "\n"


def build_okf_validation_rules() -> List[VaultNote]:
    """Build `governance/validation-rules/*.md` — OKF validation rules."""
    rules: List[VaultNote] = []

    rules.append(
        VaultNote(
            relpath="governance/validation-rules/implemented-controls-have-evidence.md",
            frontmatter={
                "type": "okf.validation_rule",
                "note_type": "validation_rule",
                "title": "Implemented controls require evidence",
                "rule_id": "rule-implemented-controls-have-evidence",
                "severity": "high",
                "status": "active",
                "applies_to": ["oscal.control"],
                "tags": ["okf/validation"],
            },
            body=(
                "# Implemented controls require evidence\n\n"
                "## Rule\n\n"
                "Every control with `implementation_status: implemented` must\n"
                "link to at least one evidence note in its `# Evidence` section.\n\n"
                "## Rationale\n\n"
                "FedRAMP and NIST 800-53 require that implemented controls are\n"
                "supported by auditable evidence. Controls asserted as\n"
                "implemented without evidence links are a finding risk.\n\n"
                "## Detection Query\n\n"
                "```dataview\n"
                "TABLE control_id, implementation_status, system\n"
                'FROM "Controls"\n'
                'WHERE implementation_status = "implemented"\n'
                "AND !evidence_refs\n"
                "SORT control_id ASC\n"
                "```\n"
            ),
        )
    )

    rules.append(
        VaultNote(
            relpath="governance/validation-rules/diagrams-have-review-dates.md",
            frontmatter={
                "type": "okf.validation_rule",
                "note_type": "validation_rule",
                "title": "Diagrams must have review dates",
                "rule_id": "rule-diagrams-have-review-dates",
                "severity": "medium",
                "status": "active",
                "applies_to": ["oscal.x.diagram"],
                "tags": ["okf/validation"],
            },
            body=(
                "# Diagrams must have review dates\n\n"
                "## Rule\n\n"
                "Every `oscal.x.diagram` note must have a `reviewed` date.\n"
                "Diagrams with `reviewed` older than 90 days are flagged as stale.\n\n"
                "## Rationale\n\n"
                "Architecture and data-flow diagrams become fiction quickly\n"
                "without regular review. Stale diagrams create audit risk.\n\n"
                "## Detection Query\n\n"
                "```dataview\n"
                "TABLE title, diagram_kind, reviewed\n"
                'FROM "diagrams"\n'
                'WHERE type = "oscal.x.diagram"\n'
                "AND reviewed <= date(today) - dur(90 days)\n"
                "SORT reviewed ASC\n"
                "```\n"
            ),
        )
    )

    return rules


# ---------------------------------------------------------------------------
# Top-level orchestrator (pure -- no I/O)
# ---------------------------------------------------------------------------


def build_vault(
    oscal_ssp: Dict[str, Any],
    *,
    plan_id: str,
    parser_name: str = "fismatic",
    source_docx: Optional[str] = None,
    control_parameters: Optional[Dict[str, List[Any]]] = None,
    crm_entries: Optional[Dict[str, Any]] = None,
    narrative: Optional[Dict[str, Any]] = None,
    images: Optional[Dict[str, Any]] = None,
    fips199_data: Optional[List[Dict[str, Any]]] = None,
    registry_components: Optional[List[Dict[str, Any]]] = None,
    registry_leveraged_auths: Optional[List[Dict[str, Any]]] = None,
    control_guidance: Optional[Dict[str, Any]] = None,
    section_guidance: Optional[Dict[str, Any]] = None,
) -> List[VaultNote]:
    """Build the full vault note set from an OSCAL SSP dict.

    Notes are returned in dependency order so callers can write them
    straight to disk. The output is deterministic given the same input +
    calendar day, so committed vault output (e.g. `examples/vault/`) can
    be regression-tested.

    ``control_parameters`` optionally maps a canonical control id to a list of
    recovered ODP values (``compliance.lib.control_parameters.ControlParameter``);
    fismatic's OSCAL output does not carry these, so the docx path supplies them
    separately.

    ``narrative`` optionally carries the ``heading_parser`` extraction result
    (a ``{"objects": [...], "summary": {...}}`` dict) recovered from the
    Chapters 1-12 prose. When present, the System note body is enriched with the
    real system description / authorization boundary / environment, and
    ``oscal.x.*`` narrative notes (boundary, relationships, inheritance,
    runbooks, decisions, diagrams) are emitted. When None, behavior is identical
    to the fismatic-only path (existing golden fixtures unaffected).

    ``images`` optionally maps ``rId -> ExtractedImage`` (from
    ``compliance.lib.docx_images.extract_images``). When present, each embedded
    diagram image is written as a binary attachment under ``Diagrams/media/``
    and embedded into the Diagram note with Obsidian ``![[...]]`` syntax.

    Includes:
      - All canonical OSCAL types (oscal.control, oscal.ssp,
        oscal.component_definition, oscal.party)
      - OKF infrastructure types (okf.index, okf.mapping, okf.schema,
        okf.validation_rule)
      - Extension type templates (oscal.x.*)
    """
    ssp_root = oscal_ssp.get("system-security-plan", oscal_ssp)
    index: VaultIndex = VaultIndex()

    requirements = (ssp_root.get("control-implementation") or {}).get(
        "implemented-requirements", []
    ) or []

    # Pre-pass: register every note's target path in the index so any note can
    # wikilink to any other note regardless of build order.
    sc = ssp_root.get("system-characteristics") or {}
    system_name = (sc.get("system-name") or "Untitled System").strip()
    sys_slug = slugify(system_name)
    index.systems[system_name] = _wl("Systems", sys_slug)
    # Pre-seed synthetic component slug only when no registry components are
    # provided; when real components are present, index.components is populated
    # during the build_real_component_notes() call below.
    if not registry_components:
        index.components[sys_slug] = _wl("Components", sys_slug)
    index.plans[plan_id] = _wl("Plans", slugify(f"{system_name}-ssp"))
    for req in requirements:
        cid = (req.get("control-id") or "").strip()
        if cid:
            cid = canonical_control_id(cid)
            index.controls[cid] = _wl("Controls", _control_slug(cid))

    plan_wikilink = _Literal(index.plans.get(plan_id, ""))
    role_index = _build_role_to_party_index(ssp_root)

    notes: List[VaultNote] = []
    notes.extend(build_organization_notes(ssp_root, index=index))

    # Map base control id -> its enhancement control ids, so base-control notes
    # can carry a real `sub_controls` children list (ADR-025).
    base_to_enhancements: Dict[str, List[str]] = {}
    for req in requirements:
        cid = (req.get("control-id") or "").strip()
        if not cid:
            continue
        cid = canonical_control_id(cid)
        osid = _split_control_id(cid)
        if osid["enhancement"] is not None:
            base_id = canonical_control_id(
                f"{osid['family']}-{re.sub(r'[^0-9]', '', cid.split('-')[1].split('(')[0])}"
            )
            base_to_enhancements.setdefault(base_id, []).append(cid)

    if registry_components:
        # Phase C2: emit one real component note per registry entry.
        real_comp_notes = build_real_component_notes(
            ssp_root,
            registry_components,
            index=index,
            registry_leveraged_auths=registry_leveraged_auths,
        )
        notes.extend(real_comp_notes)
    else:
        # Fallback: legacy synthetic placeholder (fismatic path).
        comp_note = build_synthetic_component_note(ssp_root, index=index)
        # Overlay recovered Chapter-10 component/software inventory when available
        # (D-5 / #41), replacing the synthetic placeholder's emptiness.
        comp_note = enrich_component_note_from_narrative(comp_note, narrative)
        if comp_note is not None:
            notes.append(comp_note)

    notes.append(
        build_plan_note(
            ssp_root,
            index=index,
            plan_id=plan_id,
            parser_name=parser_name,
            source_docx=source_docx,
        )
    )
    system_note = build_system_note(
        ssp_root,
        index=index,
        plan_wikilink=plan_wikilink,
        parser_name=parser_name,
        fips199_data=fips199_data,
    )
    # Overlay recovered Chapters 1-12 system prose onto the System note body,
    # replacing fismatic's placeholder description/boundary when available.
    system_note = enrich_system_note_from_narrative(system_note, narrative)
    notes.append(system_note)

    fips_199 = (sc.get("security-sensitivity-level") or "moderate").lower()
    control_parameters = control_parameters or {}
    crm_entries = crm_entries or {}
    control_guidance = control_guidance or {}
    section_guidance = section_guidance or {}
    # Re-key guidance through canonical_control_id so the extractor's raw ids
    # (e.g. "IA-02(2)", "SI-012") match the requirement ids build_control_note
    # canonicalizes to ("IA-02(02)", "SI-12").
    control_guidance = {canonical_control_id(k): v for k, v in control_guidance.items()}
    for req in requirements:
        if not (req.get("control-id") or "").strip():
            continue
        cid = canonical_control_id(req.get("control-id") or "")
        control_note = build_control_note(
            ssp_root,
            req,
            index=index,
            role_index=role_index,
            plan_wikilink=plan_wikilink,
            parser_name=parser_name,
            fips_199=fips_199,
            parameters=control_parameters.get(cid),
            crm_entry=crm_entries.get(cid),
            registry_components=registry_components,
            registry_leveraged_auths=registry_leveraged_auths,
            guidance=control_guidance.get(cid),
        )
        # Populate the base control's children (enhancements) — the real
        # control hierarchy the empty `sub_controls: []` never carried (ADR-025).
        enh_children = base_to_enhancements.get(cid)
        if enh_children and "sub_controls" in control_note.frontmatter:
            control_note.frontmatter["sub_controls"] = [
                _Literal(index.controls[e])
                for e in sorted(enh_children)
                if e in index.controls
            ]
        notes.append(control_note)

    notes.extend(build_mapping_notes(narrative))
    notes.extend(build_template_notes())
    notes.extend(build_extension_templates())
    # Diagram image attachments (binary) — extracted from the source .docx.
    # Emitted before narrative notes so the Diagram markdown note can embed the
    # written filenames via index.media.
    media_notes = build_media_notes(images, index=index)
    notes.extend(media_notes)
    media_embeds = [n.relpath.split("/")[-1] for n in media_notes]
    # Chapters 1-12 narrative notes (populated oscal.x.* extension notes) —
    # only emitted when a heading_parser extraction result is supplied.
    notes.extend(
        build_narrative_notes(
            narrative,
            index=index,
            system_id=str(system_note.frontmatter.get("system_id") or ""),
            media_embeds=media_embeds or None,
            section_guidance=section_guidance or None,
        )
    )
    notes.append(build_okf_schema_note(plan_id, narrative))
    notes.extend(build_okf_validation_rules())
    notes.append(build_meta_tags_index())
    notes.extend(build_bases_views())
    # Build in-vault coverage note (R25).
    controls_fm = {
        cid: build_control_note(
            ssp_root,
            req,
            index=index,
            role_index=role_index,
            plan_wikilink=plan_wikilink,
            parser_name=parser_name,
            fips_199=fips_199,
            parameters=control_parameters.get(cid),
            crm_entry=crm_entries.get(cid),
        ).frontmatter
        for req in requirements
        if (req.get("control-id") or "").strip()
        for cid in [canonical_control_id(req.get("control-id") or "")]
    }
    notes.append(build_coverage_note(controls_fm, fips_199=fips_199))
    notes.append(build_index_note(index))
    return notes
