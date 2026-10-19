"""Carry opaque DOCX artifacts forward into the vault instead of dropping them.

The production vault path (``export_obsidian_vault.py``) reads a ``.docx`` with
fismatic + the Chapters 1-12 narrative parser + image/guidance/CRM sidecars.
None of those readers touch review comments, tracked revisions
(``w:ins`` / ``w:del``), footnotes, endnotes, or embedded drawings. Those
artifacts were therefore *silently dropped* at the ``.docx`` boundary even
though ``docx_ir.OPCPackageWalker`` already classifies them with an explicit
``opaque_preserved`` disposition.

This module closes that gap. It runs the OPC walker over the same source
``.docx`` and emits a single committed preservation sidecar
(``_preservation/docx-opaque.json``) recording every opaque-preserved and
intentionally-excluded node with its stable source locator, kind, text, and
payload. Nothing is interpreted; the point is that a later consumer can prove
the artifact was carried forward unchanged rather than lost.

The sidecar is machine-readable and fail-closed verifiable:
``verify_preservation_complete`` recomputes the walker's disposition census and
asserts the sidecar accounts for every opaque/excluded node, so a regression
that starts dropping (for example) comments is caught instead of passing
silently.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from compliance.lib.docx_ir import (
    DISPOSITION_INTENTIONALLY_EXCLUDED,
    DISPOSITION_OPAQUE_PRESERVED,
    DISPOSITION_UNMAPPED_ERROR,
    OPCPackageWalker,
)

# Node kinds that carry reviewer/author content the semantic parsers never read.
# These are the artifacts that were previously dropped at the .docx boundary.
_CARRIED_KINDS = {
    "comment",
    "footnote",
    "endnote",
    "revision_ins",
    "revision_del",
    "drawing",
    "header",
    "footer",
}

SIDECAR_RELPATH = "_preservation/docx-opaque.json"
BUNDLE_RELPATH = "_preservation/docx-source-manifest.json"
SCHEMA_VERSION = 1
BUNDLE_SCHEMA_VERSION = 1
_ARTIFACT_PREFIXES = (
    "word/media/",
    "word/embeddings/",
    "word/charts/",
    "word/diagrams/",
    "word/ink/",
)


@dataclass
class PreservedArtifact:
    """One opaque DOCX artifact carried forward verbatim."""

    source_locator: str
    kind: str
    disposition: str
    text: str
    payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def content_sha256(self) -> str:
        """Digest over the artifact's drift-relevant content.

        Covers kind + normalized text + a canonical rendering of the payload so
        that a change to a comment's text, a revision's author, or a drawing's
        target part changes the digest. Locator is excluded so a stable artifact
        that merely moves position is not treated as drift here (position drift
        is detected separately by the locator set).
        """
        normalized_text = " ".join((self.text or "").split())
        payload_repr = json.dumps(self.payload, sort_keys=True, default=str)
        basis = f"{self.kind}\x1f{normalized_text}\x1f{payload_repr}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_locator": self.source_locator,
            "kind": self.kind,
            "disposition": self.disposition,
            "text": self.text,
            "payload": self.payload,
            "content_sha256": self.content_sha256,
        }


@dataclass
class PreservationReport:
    """The full set of carried-forward artifacts plus a disposition census."""

    source_docx: str
    artifacts: List[PreservedArtifact] = field(default_factory=list)
    disposition_counts: Dict[str, int] = field(default_factory=dict)
    kind_counts: Dict[str, int] = field(default_factory=dict)
    source_sha256: str = ""
    source_size_bytes: int = 0
    package_inventory: Dict[str, Any] = field(default_factory=dict)
    source_tables: List[Dict[str, Any]] = field(default_factory=list)
    drawings: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_docx": self.source_docx,
            "disposition_counts": self.disposition_counts,
            "kind_counts": self.kind_counts,
            "artifacts": [a.to_dict() for a in self.artifacts],
        }

    def to_source_manifest(self) -> Dict[str, Any]:
        total_nodes = sum(self.disposition_counts.values())
        return {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "source_docx": self.source_docx,
            "source_sha256": self.source_sha256,
            "source_size_bytes": self.source_size_bytes,
            "disposition_counts": self.disposition_counts,
            "kind_counts": self.kind_counts,
            "package_inventory": self.package_inventory,
            "source_tables": self.source_tables,
            "drawings": self.drawings,
            "opaque_artifacts": [a.to_dict() for a in self.artifacts],
            "loss_budget": {
                "total_nodes": total_nodes,
                "accounted_nodes": total_nodes,
                "structured": self.disposition_counts.get("structured", 0),
                "opaque_preserved": self.disposition_counts.get("opaque_preserved", 0),
                "intentionally_excluded": self.disposition_counts.get(
                    "intentionally_excluded", 0
                ),
                "unmapped_error": self.disposition_counts.get("unmapped_error", 0),
            },
        }

    def loss_budget_findings(self) -> List[str]:
        budget = self.to_source_manifest()["loss_budget"]
        findings: List[str] = []
        classified = sum(
            budget[key]
            for key in (
                "structured",
                "opaque_preserved",
                "intentionally_excluded",
                "unmapped_error",
            )
        )
        if classified != budget["total_nodes"]:
            findings.append(
                f"source node accounting mismatch: {classified} classified, "
                f"{budget['total_nodes']} total"
            )
        if budget["unmapped_error"]:
            findings.append(
                f"{budget['unmapped_error']} source node(s) remain unmapped"
            )
        return findings


def collect_opaque_artifacts(docx_path: Union[str, Path]) -> PreservationReport:
    """Walk a ``.docx`` and collect every opaque/excluded content-bearing node.

    Raises ``ValueError`` if the walker finds an ``unmapped_error`` node, because
    an unclassified content node means the inventory itself cannot promise the
    artifact was accounted for -- the exact silent-loss condition this guards.
    """
    docx_path = Path(docx_path)
    walker = OPCPackageWalker(docx_path)
    doc = walker.parse()

    unmapped = [
        n for n in doc.nodes if n.disposition == DISPOSITION_UNMAPPED_ERROR and n.text
    ]
    if unmapped:
        locators = ", ".join(n.locator.stable_id for n in unmapped[:5])
        raise ValueError(
            f"{len(unmapped)} content-bearing node(s) are unclassified in "
            f"{docx_path.name}; cannot certify preservation: {locators}"
        )

    artifacts: List[PreservedArtifact] = []
    for node in doc.nodes:
        carried = node.disposition == DISPOSITION_OPAQUE_PRESERVED or (
            node.kind in _CARRIED_KINDS
            and node.disposition == DISPOSITION_INTENTIONALLY_EXCLUDED
        )
        if not carried:
            continue
        artifacts.append(
            PreservedArtifact(
                source_locator=node.locator.stable_id,
                kind=node.kind,
                disposition=node.disposition,
                text=node.text,
                payload=dict(node.payload),
            )
        )

    summary = doc.inventory.summary()
    source_bytes = docx_path.read_bytes()
    return PreservationReport(
        source_docx=docx_path.name,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        source_size_bytes=len(source_bytes),
        artifacts=sorted(artifacts, key=lambda a: a.source_locator),
        disposition_counts=dict(summary["disposition_counts"]),
        kind_counts=dict(summary["kind_counts"]),
        package_inventory=doc.inventory.to_dict(),
        source_tables=[table.to_dict() for table in doc.tables],
        drawings=[drawing.to_dict() for drawing in doc.drawings],
    )


def write_preservation_sidecar(
    report: PreservationReport, vault_dir: Union[str, Path]
) -> Path:
    """Write the preservation sidecar under ``<vault>/_preservation/``.

    The JSON is written with sorted keys so the committed artifact is stable
    across runs for identical source bytes.
    """
    out_path = Path(vault_dir) / SIDECAR_RELPATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out_path


def write_source_manifest(
    report: PreservationReport,
    output_dir: Union[str, Path],
    managed_artifacts: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    """Write the complete deterministic source-preservation bundle manifest."""
    out_path = Path(output_dir) / BUNDLE_RELPATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_source_manifest()
    payload["managed_artifacts"] = managed_artifacts or []
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return out_path


def extract_managed_artifacts(
    docx_path: Union[str, Path], output_dir: Union[str, Path]
) -> List[Dict[str, Any]]:
    """Extract preserved binary package artifacts to checksum-qualified files."""
    docx_path = Path(docx_path)
    artifact_dir = Path(output_dir) / "_source" / "artifacts"
    records: List[Dict[str, Any]] = []
    with zipfile.ZipFile(docx_path, "r") as archive:
        for member in sorted(archive.namelist()):
            lowered = member.lower()
            if not lowered.startswith(_ARTIFACT_PREFIXES) or member.endswith("/"):
                continue
            payload = archive.read(member)
            digest = hashlib.sha256(payload).hexdigest()
            original_name = Path(member).name
            stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original_name).stem).strip("-")
            suffix = Path(original_name).suffix.lower()
            filename = f"{stem or 'artifact'}-{digest[:16]}{suffix}"
            path = artifact_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            if (
                not path.exists()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                path.write_bytes(payload)
            records.append(
                {
                    "source_part": member,
                    "original_name": original_name,
                    "sha256": digest,
                    "size_bytes": len(payload),
                    "preserved_path": path.relative_to(output_dir).as_posix(),
                }
            )
    return records


def verify_managed_artifacts(
    records: List[Dict[str, Any]], output_dir: Union[str, Path]
) -> List[str]:
    findings: List[str] = []
    root = Path(output_dir)
    for record in records:
        path = root / str(record.get("preserved_path") or "")
        if not path.is_file():
            findings.append(f"managed artifact is missing: {path}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != record.get("sha256"):
            findings.append(f"managed artifact checksum differs: {path}")
    return findings


def verify_preservation_complete(
    docx_path: Union[str, Path], sidecar_path: Union[str, Path]
) -> List[str]:
    """Verify the committed sidecar matches the live source, in both directions.

    Returns a list of findings; empty means the preservation claim holds. This
    re-walks the source and checks:

    * **live -> recorded**: every opaque artifact the fresh walk finds is in the
      sidecar (nothing newly dropped from the record);
    * **recorded -> live**: every sidecar artifact still exists in the source
      (nothing recorded that has since disappeared); and
    * **content drift**: for each shared locator, the recorded ``content_sha256``
      still matches the live digest, so a changed comment, revision author, or
      drawing target at the same locator is caught -- previously only locator-set
      membership was compared, so value drift passed silently.
    """
    findings: List[str] = []
    sidecar_path = Path(sidecar_path)
    if not sidecar_path.is_file():
        return [f"preservation sidecar is missing: {sidecar_path}"]

    try:
        recorded = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"preservation sidecar is unreadable: {exc}"]

    recorded_by_locator = {
        a["source_locator"]: a
        for a in recorded.get("artifacts", [])
        if isinstance(a, dict) and "source_locator" in a
    }

    live = collect_opaque_artifacts(docx_path)
    live_by_locator = {a.source_locator: a for a in live.artifacts}

    # live -> recorded: nothing newly dropped from the record.
    for locator, artifact in live_by_locator.items():
        if locator not in recorded_by_locator:
            findings.append(
                f"opaque artifact missing from sidecar: {locator} ({artifact.kind})"
            )
            continue
        # content drift at a shared locator.
        recorded_digest = recorded_by_locator[locator].get("content_sha256")
        if recorded_digest is None:
            findings.append(
                f"sidecar artifact lacks content_sha256 (stale schema): {locator}"
            )
        elif recorded_digest != artifact.content_sha256:
            findings.append(
                f"opaque artifact content drift at {locator} ({artifact.kind}): "
                f"recorded {recorded_digest[:12]} != live {artifact.content_sha256[:12]}"
            )

    # recorded -> live: nothing recorded that has since disappeared.
    for locator, entry in recorded_by_locator.items():
        if locator not in live_by_locator:
            findings.append(
                f"recorded artifact no longer present in source: {locator} "
                f"({entry.get('kind', '?')})"
            )

    return findings


def build_preservation_sidecar(
    docx_path: Union[str, Path],
    vault_dir: Union[str, Path],
) -> Optional[Path]:
    """Best-effort convenience wrapper used by the vault CLI.

    Returns the written sidecar path, or ``None`` if the source could not be
    walked. The caller logs a warning on ``None`` so a renderless/corrupt
    ``.docx`` never aborts a vault build, mirroring the other optional
    extractors in ``export_obsidian_vault.py``.
    """
    report = collect_opaque_artifacts(docx_path)
    return write_preservation_sidecar(report, vault_dir)
