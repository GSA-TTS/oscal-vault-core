"""Source-located instruction ledger and rendered-output reconciliation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

import yaml

from compliance.lib.docx_ir import DocxDocument, OPCPackageWalker
from compliance.lib.instruction_anchors import (
    DEFAULT_INSTRUCTION_COLORS,
    DEFAULT_INSTRUCTION_TEXT_PREFIXES,
    instruction_text_kind,
)

POLICY_VERSION = 1
DISPOSITION_TEMPLATE_GUIDANCE_CALLOUT = "template_guidance_callout"
DISPOSITION_VISIBLE_TEMPLATE_PROSE = "visible_template_prose"
DISPOSITION_AUTHORING_PROMPT = "authoring_prompt"
DISPOSITION_DUPLICATE = "duplicate"
DISPOSITION_OPAQUE_PRESERVED = "opaque_preserved"
DISPOSITION_UNMAPPED_ERROR = "unmapped_error"

VALID_INSTRUCTION_DISPOSITIONS = {
    DISPOSITION_TEMPLATE_GUIDANCE_CALLOUT,
    DISPOSITION_VISIBLE_TEMPLATE_PROSE,
    DISPOSITION_AUTHORING_PROMPT,
    DISPOSITION_DUPLICATE,
    DISPOSITION_OPAQUE_PRESERVED,
    DISPOSITION_UNMAPPED_ERROR,
}
_OVERRIDE_FIELDS = {
    "destination",
    "disposition",
    "visibility",
    "rationale",
    "duplicate_of",
}
_CANDIDATE_PREFIX_RE = re.compile(
    r"^\s*(?:instruction|instructions|note|example)\s*:", re.IGNORECASE
)
_METADATA_RE = re.compile(r"^<!-- oscal-vault-instruction (\{.*\}) -->$", re.MULTILINE)


def normalized_sha256(text: str) -> str:
    """Return the stable hash used to reconcile equivalent instruction text."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _slug(text: Optional[str]) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (text or "front-matter").lower()).strip("-")
    return value[:48] or "front-matter"


@dataclass
class InstructionLedgerEntry:
    """A source instruction and its explicit rendered-output disposition."""

    locator: str
    source_hash: str
    stable_key: str
    text: str
    anchor: Optional[str]
    source_kind: str
    detection: str
    detected_prefix: Optional[str]
    source_colors: List[str]
    destination: Optional[str]
    disposition: str
    visibility: str
    duplicate_of: Optional[str] = None
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RenderedInstruction:
    """Instruction provenance read from a concrete rendered artifact."""

    source_locator: str
    source_hash: str
    destination: str
    rendered_hash: Optional[str] = None


@dataclass
class InstructionLedger:
    """Machine-readable source candidates and their output policy."""

    entries: List[InstructionLedgerEntry] = field(default_factory=list)
    candidate_locators: List[str] = field(default_factory=list)
    policy_version: int = POLICY_VERSION

    def to_dict(self) -> Dict[str, Any]:
        def counts_for(values: Iterable[str]) -> Dict[str, int]:
            counts: Dict[str, int] = {}
            for value in values:
                counts[value] = counts.get(value, 0) + 1
            return counts

        return {
            "policy_version": self.policy_version,
            "summary": {
                "total": len(self.entries),
                "candidates": len(self.candidate_locators),
                "dispositions": counts_for(entry.disposition for entry in self.entries),
                "detections": counts_for(entry.detection for entry in self.entries),
                "prefixes": counts_for(
                    entry.detected_prefix or "<blue-only>" for entry in self.entries
                ),
                "colors": counts_for(
                    color or "<none>"
                    for entry in self.entries
                    for color in (entry.source_colors or [""])
                ),
                "anchors": counts_for(
                    entry.anchor or "<before-heading>" for entry in self.entries
                ),
                "destinations": counts_for(
                    entry.destination or "<none>" for entry in self.entries
                ),
            },
            "candidate_locators": self.candidate_locators,
            "entries": [entry.to_dict() for entry in self.entries],
        }


def load_instruction_policy(path: Union[str, Path]) -> Mapping[str, Mapping[str, Any]]:
    """Load and validate a versioned YAML/JSON locator override policy."""
    policy_path = Path(path)
    payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Instruction policy must be an object")
    unknown = set(payload) - {"policy_version", "overrides"}
    if unknown:
        raise ValueError(f"Unknown instruction policy fields: {sorted(unknown)}")
    if payload.get("policy_version") != POLICY_VERSION:
        raise ValueError(
            f"Unsupported instruction policy_version {payload.get('policy_version')!r}; "
            f"expected {POLICY_VERSION}"
        )
    overrides = payload.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("Instruction policy overrides must be an object")
    for locator, override in overrides.items():
        if not isinstance(locator, str) or not isinstance(override, dict):
            raise ValueError(
                "Instruction policy overrides must map locators to objects"
            )
        fields = set(override)
        if fields - _OVERRIDE_FIELDS:
            raise ValueError(
                f"Unknown override fields for {locator}: {sorted(fields - _OVERRIDE_FIELDS)}"
            )
    return overrides


def _is_blue(run_colors: Iterable[Optional[str]], colors: set[str]) -> bool:
    explicit = [str(color).upper() for color in run_colors if color]
    return bool(explicit) and all(color in colors for color in explicit)


def _candidate_inventory(doc: DocxDocument, colors: set[str]) -> List[str]:
    """Inventory candidates independently from ledger entry classification."""
    candidates: List[str] = []
    for node in doc.nodes:
        if (
            node.kind == "paragraph"
            and node.text
            and (
                _CANDIDATE_PREFIX_RE.match(node.text)
                or _is_blue(node.payload.get("effective_run_colors", []), colors)
            )
        ):
            candidates.append(node.locator.stable_id)
    for table in doc.tables:
        for row in table.cells:
            for cell in row:
                for paragraph in cell.paragraphs:
                    if paragraph["text"] and (
                        _CANDIDATE_PREFIX_RE.match(paragraph["text"])
                        or _is_blue(paragraph["effective_run_colors"], colors)
                    ):
                        candidates.append(paragraph["locator"])
    return sorted(set(candidates))


def _default_entry(
    *,
    locator: str,
    text: str,
    anchor: Optional[str],
    source_kind: str,
    detection: str,
    source_colors: Iterable[Optional[str]],
) -> InstructionLedgerEntry:
    source_hash = normalized_sha256(text)
    locator_hash = hashlib.sha256(locator.encode("utf-8")).hexdigest()[:10]
    stable_key = f"{_slug(anchor)}-{source_hash[:12]}-{locator_hash}"
    lexical = instruction_text_kind(text, DEFAULT_INSTRUCTION_TEXT_PREFIXES)
    return InstructionLedgerEntry(
        locator=locator,
        source_hash=source_hash,
        stable_key=stable_key,
        text=text,
        anchor=anchor,
        source_kind=source_kind,
        detection=detection,
        detected_prefix=lexical,
        source_colors=sorted({color for color in source_colors if color}),
        destination=f"guidance/{stable_key}.md",
        disposition=(
            DISPOSITION_TEMPLATE_GUIDANCE_CALLOUT
            if lexical is not None
            else DISPOSITION_AUTHORING_PROMPT
        ),
        visibility="collapsed" if lexical is not None else "visible",
        rationale=(
            "Explicit labelled guidance is retained as a collapsed callout."
            if lexical is not None
            else "Blue-only source text remains visible pending review."
        ),
    )


def build_instruction_ledger(
    source: Union[str, Path, DocxDocument],
    *,
    instruction_colors: Iterable[str] = DEFAULT_INSTRUCTION_COLORS,
    overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> InstructionLedger:
    """Build a ledger from DOCX IR and reject unused locator overrides."""
    doc = (
        source if isinstance(source, DocxDocument) else OPCPackageWalker(source).parse()
    )
    colors = {str(color).lstrip("#").upper() for color in instruction_colors}
    candidates = _candidate_inventory(doc, colors)
    entries: List[InstructionLedgerEntry] = []
    headings = sorted(
        (
            (node.locator.order, node.text)
            for node in doc.nodes
            if node.kind == "heading" and node.text
        ),
        key=lambda item: item[0],
    )

    def anchor_for(order: int) -> Optional[str]:
        preceding = [text for heading_order, text in headings if heading_order < order]
        return preceding[-1] if preceding else None

    for node in sorted(doc.nodes, key=lambda item: item.locator.order):
        if node.kind != "paragraph" or not node.text:
            continue
        lexical = instruction_text_kind(node.text, DEFAULT_INSTRUCTION_TEXT_PREFIXES)
        effective_colors = node.payload.get("effective_run_colors", [])
        blue = _is_blue(effective_colors, colors)
        if lexical is None and not blue:
            continue
        entries.append(
            _default_entry(
                locator=node.locator.stable_id,
                text=node.text,
                anchor=anchor_for(node.locator.order),
                source_kind="paragraph",
                source_colors=effective_colors,
                detection=(
                    "text+color"
                    if lexical is not None and blue
                    else "text"
                    if lexical is not None
                    else "blue_only"
                ),
            )
        )

    for table in sorted(doc.tables, key=lambda item: item.locator.order):
        for row in table.cells:
            for cell in row:
                for paragraph in cell.paragraphs:
                    text = paragraph["text"]
                    lexical = instruction_text_kind(
                        text, DEFAULT_INSTRUCTION_TEXT_PREFIXES
                    )
                    effective_colors = paragraph["effective_run_colors"]
                    blue = _is_blue(effective_colors, colors)
                    if lexical is None and not blue:
                        continue
                    entries.append(
                        _default_entry(
                            locator=paragraph["locator"],
                            text=text,
                            anchor=anchor_for(table.locator.order),
                            source_kind="table_paragraph",
                            source_colors=effective_colors,
                            detection=(
                                "table_text+color"
                                if lexical is not None and blue
                                else "table_text"
                                if lexical is not None
                                else "table_blue_only"
                            ),
                        )
                    )

    overrides = overrides or {}
    entry_locators = {entry.locator for entry in entries}
    unused = set(overrides) - entry_locators
    if unused:
        raise ValueError(f"Unused or unknown override locators: {sorted(unused)}")
    for entry in entries:
        override = overrides.get(entry.locator, {})
        unknown = set(override) - _OVERRIDE_FIELDS
        if unknown:
            raise ValueError(
                f"Unknown override fields for {entry.locator}: {sorted(unknown)}"
            )
        for field_name, value in override.items():
            setattr(entry, field_name, value)
        if entry.disposition not in VALID_INSTRUCTION_DISPOSITIONS:
            raise ValueError(
                f"Invalid instruction disposition for {entry.locator}: {entry.disposition}"
            )

    return InstructionLedger(entries=entries, candidate_locators=candidates)


def render_instruction_outputs(
    ledger: InstructionLedger, output_dir: Union[str, Path]
) -> None:
    """Render ledger entries with provenance metadata at concrete destinations."""
    root = Path(output_dir)
    for entry in ledger.entries:
        if entry.disposition in {
            DISPOSITION_DUPLICATE,
            DISPOSITION_OPAQUE_PRESERVED,
            DISPOSITION_UNMAPPED_ERROR,
        }:
            continue
        if not entry.destination:
            continue
        path = root / entry.destination
        if root.resolve() not in path.resolve().parents:
            raise ValueError(
                f"Instruction destination escapes output directory: {entry.destination}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = json.dumps(
            {"source_locator": entry.locator, "source_hash": entry.source_hash},
            sort_keys=True,
        )
        if entry.visibility == "collapsed":
            body = "> [!note]- Instruction\n" + "\n".join(
                f"> {line}" for line in entry.text.splitlines()
            )
        else:
            body = entry.text
        frontmatter = yaml.safe_dump(
            {
                "type": "okf.instruction",
                "title": entry.anchor or "Source instruction",
                "status": "active",
                "owner": None,
                "source_locator": entry.locator,
                "source_hash": entry.source_hash,
                "disposition": entry.disposition,
                "visibility": entry.visibility,
                "source_kind": entry.source_kind,
                "detection": entry.detection,
                "anchor": entry.anchor,
            },
            sort_keys=False,
        )
        path.write_text(
            f"---\n{frontmatter}---\n"
            f"<!-- oscal-vault-instruction {metadata} -->\n\n{body}\n",
            encoding="utf-8",
        )


def inventory_rendered_instructions(
    output_dir: Union[str, Path],
) -> List[RenderedInstruction]:
    """Read instruction provenance from actual markdown export destinations."""
    root = Path(output_dir)
    rendered: List[RenderedInstruction] = []
    if not root.is_dir():
        return rendered
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        matches = list(_METADATA_RE.finditer(text))
        for index, match in enumerate(matches):
            try:
                metadata = json.loads(match.group(1))
                if (
                    not isinstance(metadata, dict)
                    or set(metadata) != {"source_locator", "source_hash"}
                    or not isinstance(metadata["source_locator"], str)
                    or not isinstance(metadata["source_hash"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", metadata["source_hash"])
                ):
                    raise ValueError
                end = (
                    matches[index + 1].start()
                    if index + 1 < len(matches)
                    else len(text)
                )
                rendered_body = text[match.end() : end].strip()
                if rendered_body.startswith("> [!note]- Instruction\n"):
                    quoted_lines: List[str] = []
                    for line in rendered_body.splitlines()[1:]:
                        if not line.startswith("> "):
                            break
                        quoted_lines.append(line[2:])
                    rendered_hash = normalized_sha256("\n".join(quoted_lines))
                else:
                    rendered_hash = None
                rendered.append(
                    RenderedInstruction(
                        source_locator=metadata["source_locator"],
                        source_hash=metadata["source_hash"],
                        destination=path.relative_to(root).as_posix(),
                        rendered_hash=rendered_hash,
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                rendered.append(
                    RenderedInstruction(
                        "<invalid>",
                        "<invalid>",
                        path.relative_to(root).as_posix(),
                    )
                )
    return rendered


def inventory_canonical_instructions(
    output_dir: Union[str, Path],
) -> List[RenderedInstruction]:
    """Read instruction provenance from generated canonical sidecars."""
    root = Path(output_dir)
    snapshot_root = root / ".snapshots" / "current"
    rendered: List[RenderedInstruction] = []
    if not snapshot_root.is_dir():
        return rendered
    for path in sorted(snapshot_root.rglob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sources = document.get("instruction_sources") or []
        generated_from = str(document.get("_meta", {}).get("generated_from") or "")
        for source in sources:
            if not isinstance(source, dict):
                continue
            rendered.append(
                RenderedInstruction(
                    source_locator=str(source.get("source_locator") or "<invalid>"),
                    source_hash=str(source.get("source_hash") or "<invalid>"),
                    destination=generated_from,
                )
            )
    return rendered


def canonical_reconciliation_findings(
    ledger: InstructionLedger, canonical: Iterable[RenderedInstruction]
) -> List[str]:
    """Require one matching canonical provenance record per rendered entry."""
    findings: List[str] = []
    expected = {
        entry.locator: entry
        for entry in ledger.entries
        if entry.destination
        and entry.disposition
        not in {
            DISPOSITION_DUPLICATE,
            DISPOSITION_OPAQUE_PRESERVED,
            DISPOSITION_UNMAPPED_ERROR,
        }
    }
    actual: Dict[str, List[RenderedInstruction]] = {}
    for item in canonical:
        actual.setdefault(item.source_locator, []).append(item)
        if item.source_locator not in expected:
            findings.append(
                f"{item.destination}: canonical provenance references unknown source "
                f"locator {item.source_locator}"
            )
    for locator, entry in expected.items():
        matches = actual.get(locator, [])
        if not matches:
            findings.append(f"{locator}: no canonical sidecar provenance found")
        if len(matches) > 1:
            findings.append(f"{locator}: canonical provenance appears multiple times")
        for item in matches:
            if item.source_hash != entry.source_hash:
                findings.append(f"{locator}: canonical source hash does not match")
            if item.destination != entry.destination:
                findings.append(
                    f"{locator}: canonical destination {item.destination}, "
                    f"expected {entry.destination}"
                )
    return findings


def reconciliation_findings(
    ledger: InstructionLedger,
    rendered: Optional[Iterable[RenderedInstruction]] = None,
) -> List[str]:
    """Reconcile independent source candidates against actual output artifacts."""
    findings: List[str] = []
    entries_by_locator = {entry.locator: entry for entry in ledger.entries}
    for locator in sorted(set(ledger.candidate_locators) - set(entries_by_locator)):
        findings.append(f"{locator}: candidate inventory node was not classified")

    rendered_list = list(rendered) if rendered is not None else []
    by_locator: Dict[str, List[RenderedInstruction]] = {}
    for item in rendered_list:
        by_locator.setdefault(item.source_locator, []).append(item)
        if item.source_locator not in entries_by_locator:
            findings.append(
                f"{item.destination}: rendered metadata references unknown source locator "
                f"{item.source_locator}"
            )

    for entry in ledger.entries:
        if entry.disposition not in VALID_INSTRUCTION_DISPOSITIONS:
            findings.append(f"{entry.locator}: missing or invalid disposition")
        if entry.disposition == DISPOSITION_UNMAPPED_ERROR:
            findings.append(f"{entry.locator}: unclassified instruction node")
        actual = by_locator.get(entry.locator, [])
        should_render = entry.disposition not in {
            DISPOSITION_DUPLICATE,
            DISPOSITION_OPAQUE_PRESERVED,
            DISPOSITION_UNMAPPED_ERROR,
        }
        if should_render and not actual:
            findings.append(f"{entry.locator}: no rendered destination found")
        if not should_render and actual:
            findings.append(f"{entry.locator}: disposition forbids rendered output")
        if len(actual) > 1:
            findings.append(f"{entry.locator}: rendered at multiple destinations")
        for item in actual:
            if item.source_hash != entry.source_hash:
                findings.append(f"{entry.locator}: rendered source hash does not match")
            if (
                item.rendered_hash is not None
                and item.rendered_hash != entry.source_hash
            ):
                findings.append(
                    f"{entry.locator}: rendered instruction text does not match"
                )
            if item.destination != entry.destination:
                findings.append(
                    f"{entry.locator}: rendered at {item.destination}, expected {entry.destination}"
                )
        if entry.disposition == DISPOSITION_DUPLICATE:
            primary = entries_by_locator.get(entry.duplicate_of or "")
            if primary is None:
                findings.append(
                    f"{entry.locator}: duplicate references no ledger entry"
                )
            elif primary.source_hash != entry.source_hash:
                findings.append(
                    f"{entry.locator}: duplicate source hash differs from primary"
                )
            elif primary.destination != entry.destination:
                findings.append(
                    f"{entry.locator}: duplicate destination differs from primary"
                )
        elif entry.duplicate_of:
            findings.append(
                f"{entry.locator}: duplicate_of requires duplicate disposition"
            )
    return findings


def assert_reconciled(
    ledger: InstructionLedger, rendered: Iterable[RenderedInstruction]
) -> None:
    """Fail closed unless every candidate reaches an authorized real output."""
    findings = reconciliation_findings(ledger, rendered)
    if findings:
        raise ValueError("Instruction reconciliation failed:\n" + "\n".join(findings))
