"""Source-located typed DOCX Intermediate Representation (IR) and OPC Package Inventory.

Fulfills:
  - Issue #52 (P0): Inventory DOCX package content and dispositions.
  - Issue #48 (P1): Source-located typed DOCX intermediate representation.
  - Issue #45 (P0): DOCX-to-canonical-to-OSCAL field lineage tracking.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# OpenXML Namespaces
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"

NS = {
    "w": W_NS,
    "r": R_NS,
    "rel": PACKAGE_RELS_NS,
    "ct": CONTENT_TYPES_NS,
    "a": A_NS,
    "wp": WP_NS,
    "pic": PIC_NS,
}

# Standard Dispositions
DISPOSITION_STRUCTURED = "structured"
DISPOSITION_OPAQUE_PRESERVED = "opaque_preserved"
DISPOSITION_INTENTIONALLY_EXCLUDED = "intentionally_excluded"
DISPOSITION_UNMAPPED_ERROR = "unmapped_error"

VALID_DISPOSITIONS = {
    DISPOSITION_STRUCTURED,
    DISPOSITION_OPAQUE_PRESERVED,
    DISPOSITION_INTENTIONALLY_EXCLUDED,
    DISPOSITION_UNMAPPED_ERROR,
}


@dataclass
class DocxLocator:
    """Stable XML and package locator for a content-bearing node."""

    part_uri: str
    xml_locator: str
    order: int
    kind: str

    @property
    def stable_id(self) -> str:
        return f"{self.part_uri}#{self.xml_locator}"


@dataclass
class DocxNode:
    """Source-located typed node in the DOCX Intermediate Representation."""

    id: str
    locator: DocxLocator
    kind: str  # heading, paragraph, table, table_cell, hyperlink, drawing, header, footer, footnote, endnote, comment, revision_ins, revision_del
    parent_id: Optional[str] = None
    text: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    raw_xml_or_package_reference: Optional[str] = None
    disposition: str = DISPOSITION_UNMAPPED_ERROR
    disposition_rationale: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "locator": {
                "part_uri": self.locator.part_uri,
                "xml_locator": self.locator.xml_locator,
                "order": self.locator.order,
                "kind": self.locator.kind,
                "stable_id": self.locator.stable_id,
            },
            "kind": self.kind,
            "parent_id": self.parent_id,
            "text": self.text,
            "payload": self.payload,
            "raw_xml_or_package_reference": self.raw_xml_or_package_reference,
            "disposition": self.disposition,
            "disposition_rationale": self.disposition_rationale,
        }


@dataclass
class DocxTableCell:
    """Cell inside a structured DocxTable preserving row/col spans and locator."""

    row_idx: int
    col_idx: int
    text: str
    grid_span: int = 1
    v_merge: Optional[str] = None  # None, "restart", or "continue"
    cell_locator: str = ""
    paragraphs: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DocxTable:
    """Structured table preserving grid spans, vertical merges, and cell locators."""

    node_id: str
    locator: DocxLocator
    num_rows: int
    num_cols: int
    cells: List[List[DocxTableCell]] = field(default_factory=list)

    def to_text_grid(self) -> List[List[str]]:
        """Compatibility projection returning 2D string grid for legacy extractors."""
        grid: List[List[str]] = []
        for row in self.cells:
            grid.append([c.text.strip() for c in row])
        return grid

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_table_id": self.node_id,
            "locator": {
                "part_uri": self.locator.part_uri,
                "xml_locator": self.locator.xml_locator,
                "order": self.locator.order,
                "stable_id": self.locator.stable_id,
            },
            "num_rows": self.num_rows,
            "num_cols": self.num_cols,
            "cells": [[cell.to_dict() for cell in row] for row in self.cells],
            "text_grid": self.to_text_grid(),
        }


@dataclass
class DocxDrawing:
    """Drawing element preserving placement, relationship ID, dimensions, and image hash."""

    node_id: str
    relationship_id: str
    target_part_uri: str
    media_type: str = ""
    sha256: str = ""
    width_emu: Optional[int] = None
    height_emu: Optional[int] = None
    alt_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DocxPartInfo:
    """Inventory entry for an OPC package part."""

    uri: str
    content_type: str
    size_bytes: int
    sha256: str
    disposition: str = DISPOSITION_OPAQUE_PRESERVED


@dataclass
class DocxRelInfo:
    """Inventory entry for an OPC relationship."""

    rel_id: str
    source_part: str
    target: str
    rel_type: str
    target_mode: str = "Internal"


@dataclass
class DocxPackageInventory:
    """Complete inventory of all reachable OPC parts, relationships, and nodes."""

    package_path: str
    parts: List[DocxPartInfo] = field(default_factory=list)
    relationships: List[DocxRelInfo] = field(default_factory=list)
    nodes: List[DocxNode] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        by_disp: Dict[str, int] = {}
        by_kind: Dict[str, int] = {}
        for n in self.nodes:
            by_disp[n.disposition] = by_disp.get(n.disposition, 0) + 1
            by_kind[n.kind] = by_kind.get(n.kind, 0) + 1

        return {
            "package_path": self.package_path,
            "total_parts": len(self.parts),
            "total_relationships": len(self.relationships),
            "total_nodes": len(self.nodes),
            "disposition_counts": by_disp,
            "kind_counts": by_kind,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary(),
            "parts": [asdict(p) for p in self.parts],
            "relationships": [asdict(r) for r in self.relationships],
            "nodes": [n.to_dict() for n in self.nodes],
        }

    def write_json(self, path: Union[str, Path]) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)


@dataclass
class LineageRecord:
    """Lineage tracking entry from DOCX source node to canonical and OSCAL."""

    source_locator: str
    canonical_pointer: Optional[str]
    oscal_pointer: Optional[str]
    normalized_sha256: str
    value: str
    disposition: str = DISPOSITION_STRUCTURED


class LineageTracker:
    """Tracks field lineage from DOCX source locators to canonical and OSCAL."""

    def __init__(self) -> None:
        self.records: List[LineageRecord] = []

    def record(
        self,
        source_locator: str,
        value: str,
        canonical_pointer: Optional[str] = None,
        oscal_pointer: Optional[str] = None,
        disposition: str = DISPOSITION_STRUCTURED,
    ) -> LineageRecord:
        norm_val = re.sub(r"\s+", " ", value or "").strip()
        h = hashlib.sha256(norm_val.encode("utf-8")).hexdigest()
        rec = LineageRecord(
            source_locator=source_locator,
            canonical_pointer=canonical_pointer,
            oscal_pointer=oscal_pointer,
            normalized_sha256=h,
            value=norm_val,
            disposition=disposition,
        )
        self.records.append(rec)
        return rec

    def to_dict(self) -> List[Dict[str, Any]]:
        return [asdict(r) for r in self.records]

    def verify_lossless(
        self,
        required_source_locators: List[str],
        *,
        require_pointers: bool = False,
    ) -> Tuple[bool, List[str]]:
        recorded = {r.source_locator: r for r in self.records}
        missing: List[str] = []
        for loc in required_source_locators:
            if loc not in recorded:
                missing.append(f"Required locator {loc} was not recorded in lineage")
            else:
                rec = recorded[loc]
                if rec.disposition == DISPOSITION_UNMAPPED_ERROR:
                    missing.append(
                        f"Required locator {loc} has unmapped_error disposition"
                    )
                elif require_pointers and rec.disposition == DISPOSITION_STRUCTURED:
                    if not rec.canonical_pointer:
                        missing.append(
                            f"Structured locator {loc} is missing required canonical_pointer"
                        )
                    if not rec.oscal_pointer:
                        missing.append(
                            f"Structured locator {loc} is missing required oscal_pointer"
                        )
        return len(missing) == 0, missing

    @staticmethod
    def _pointer_value(document: Dict[str, Any], pointer: str) -> Any:
        """Resolve a strict RFC 6901 JSON pointer against a generated artifact."""
        if not pointer.startswith("/"):
            raise ValueError(f"JSON pointer must start with '/': {pointer!r}")
        value: Any = document
        for token in pointer[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                try:
                    value = value[int(token)]
                except (ValueError, IndexError) as exc:
                    raise KeyError(pointer) from exc
            elif isinstance(value, dict) and token in value:
                value = value[token]
            else:
                raise KeyError(pointer)
        return value

    def verify_artifacts(
        self, canonical: Dict[str, Any], oscal: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """Validate structured lineage pointers and normalized values end to end."""
        findings: List[str] = []
        for record in self.records:
            if record.disposition != DISPOSITION_STRUCTURED:
                continue
            for artifact_name, artifact, pointer in (
                ("canonical", canonical, record.canonical_pointer),
                ("OSCAL", oscal, record.oscal_pointer),
            ):
                if not pointer:
                    findings.append(
                        f"{record.source_locator}: missing {artifact_name} pointer"
                    )
                    continue
                try:
                    actual = self._pointer_value(artifact, pointer)
                except (KeyError, ValueError):
                    findings.append(
                        f"{record.source_locator}: {artifact_name} pointer missing: {pointer}"
                    )
                    continue
                normalized = re.sub(r"\s+", " ", str(actual or "")).strip()
                actual_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                if actual_hash != record.normalized_sha256:
                    findings.append(
                        f"{record.source_locator}: {artifact_name} value hash differs at {pointer}"
                    )
        return not findings, findings


class DocxDocument:
    """Typed Document IR representation produced by the package walker."""

    def __init__(self, inventory: DocxPackageInventory) -> None:
        self.inventory = inventory
        self.nodes = inventory.nodes
        self._node_by_id = {n.id: n for n in self.nodes}
        self._node_by_locator = {n.locator.stable_id: n for n in self.nodes}
        self.tables: List[DocxTable] = []
        self.drawings: List[DocxDrawing] = []

    def classify_node(
        self, locator_or_id: str, disposition: str, rationale: Optional[str] = None
    ) -> bool:
        """Classify a node's disposition (structured, opaque_preserved, intentionally_excluded)."""
        node = self._node_by_id.get(locator_or_id) or self._node_by_locator.get(
            locator_or_id
        )
        if node is None:
            return False
        if disposition not in VALID_DISPOSITIONS:
            raise ValueError(f"Invalid disposition {disposition!r}")
        node.disposition = disposition
        node.disposition_rationale = rationale
        return True

    def unclassified_nodes(self) -> List[DocxNode]:
        """Return nodes that still have unmapped_error disposition."""
        return [n for n in self.nodes if n.disposition == DISPOSITION_UNMAPPED_ERROR]

    def verify_all_classified(self, strict: bool = True) -> Tuple[bool, List[str]]:
        """Verify that every content-bearing node has been explicitly classified."""
        unmapped = self.unclassified_nodes()
        if not unmapped:
            return True, []
        errors = [
            f"Unclassified node: {n.locator.stable_id} ({n.kind}): {n.text[:40]!r}"
            for n in unmapped
        ]
        if strict and errors:
            raise ValueError(
                f"{len(errors)} unclassified source node(s) found in strict mode:\n"
                + "\n".join(errors[:10])
            )
        return False, errors

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the typed IR, including lossless table and drawing projections."""
        return {
            "inventory": self.inventory.to_dict(),
            "tables": [table.to_dict() for table in self.tables],
            "drawings": [drawing.to_dict() for drawing in self.drawings],
        }


class OPCPackageWalker:
    """Walks all OPC parts and relationships in a .docx file and builds the DocxDocument IR."""

    def __init__(self, docx_path: Union[str, Path]) -> None:
        self.docx_path = Path(docx_path)
        self.order_counter = 0

    def _next_order(self) -> int:
        self.order_counter += 1
        return self.order_counter

    # Block-level WordprocessingML elements that legitimately carry no document
    # text of their own. These are classified `intentionally_excluded` (not an
    # error) when they appear as a direct body child.
    _NON_CONTENT_BLOCK_TAGS = frozenset(
        {
            "sectPr",
            "bookmarkStart",
            "bookmarkEnd",
            "proofErr",
            "commentRangeStart",
            "commentRangeEnd",
            "permStart",
            "permEnd",
            "moveFromRangeStart",
            "moveFromRangeEnd",
            "moveToRangeStart",
            "moveToRangeEnd",
            "customXmlInsRangeStart",
            "customXmlInsRangeEnd",
            "customXmlDelRangeStart",
            "customXmlDelRangeEnd",
        }
    )

    def _classify_unknown_block(self, element: ET.Element, base_loc: str) -> DocxNode:
        """Build a node for a body child the structured walker does not model.

        A content-bearing unknown block (any text under it) is an
        ``unmapped_error`` so strict verification and the package-coverage gate
        fail closed instead of silently dropping it. A known structural marker
        with no text is ``intentionally_excluded`` with a recorded rationale.
        """
        raw_tag = element.tag if isinstance(element.tag, str) else str(element.tag)
        local = raw_tag.rsplit("}", 1)[-1] if "}" in raw_tag else raw_tag
        text = "".join(element.itertext()).strip()
        order = self._next_order()
        locator = DocxLocator(
            part_uri="word/document.xml",
            xml_locator=f"{base_loc}/{local}[{order}]",
            order=order,
            kind=local,
        )
        if local in self._NON_CONTENT_BLOCK_TAGS and not text:
            disposition = DISPOSITION_INTENTIONALLY_EXCLUDED
            rationale = f"structural marker <w:{local}> carries no document text"
        elif not text:
            disposition = DISPOSITION_INTENTIONALLY_EXCLUDED
            rationale = f"unmodeled empty block <{local}>"
        else:
            disposition = DISPOSITION_UNMAPPED_ERROR
            rationale = (
                f"unmodeled content-bearing body element <{local}>; "
                "the structured walker has no extractor for it"
            )
        return DocxNode(
            id=locator.stable_id,
            locator=locator,
            kind=local,
            text=text,
            disposition=disposition,
            disposition_rationale=rationale,
        )

    def parse(self) -> DocxDocument:
        if not self.docx_path.is_file():
            raise FileNotFoundError(f"File not found: {self.docx_path}")

        parts_info: List[DocxPartInfo] = []
        rels_info: List[DocxRelInfo] = []
        nodes: List[DocxNode] = []
        tables: List[DocxTable] = []
        drawings: List[DocxDrawing] = []

        with zipfile.ZipFile(self.docx_path, "r") as zf:
            namelist = zf.namelist()

            self._style_colors: Dict[str, Optional[str]] = {}
            self._style_bases: Dict[str, str] = {}
            if "word/styles.xml" in namelist:
                styles_root = ET.fromstring(zf.read("word/styles.xml"))
                for style in styles_root.findall(f"{{{W_NS}}}style"):
                    style_id = style.get(f"{{{W_NS}}}styleId", "")
                    if not style_id:
                        continue
                    color = style.find(f"./{{{W_NS}}}rPr/{{{W_NS}}}color")
                    self._style_colors[style_id] = (
                        color.get(f"{{{W_NS}}}val", "").upper()
                        if color is not None
                        else None
                    )
                    based_on = style.find(f"{{{W_NS}}}basedOn")
                    if based_on is not None:
                        self._style_bases[style_id] = based_on.get(f"{{{W_NS}}}val", "")

            # 1. Content types
            content_types: Dict[str, str] = {}
            if "[Content_Types].xml" in namelist:
                ct_xml = zf.read("[Content_Types].xml")
                root_ct = ET.fromstring(ct_xml)
                # Read Default extension mappings first
                for default in root_ct.findall(
                    "{http://schemas.openxmlformats.org/package/2006/content-types}Default"
                ):
                    ext = default.get("Extension", "").lower()
                    ct = default.get("ContentType", "")
                    content_types[f"*.{ext}"] = ct
                # Read Overrides
                for override in root_ct.findall(
                    "{http://schemas.openxmlformats.org/package/2006/content-types}Override"
                ):
                    part_name = override.get("PartName", "").lstrip("/")
                    ct = override.get("ContentType", "")
                    content_types[part_name] = ct

            # 2. Inventory all parts
            for name in namelist:
                data = zf.read(name)
                sha = hashlib.sha256(data).hexdigest()
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                ct = content_types.get(
                    name,
                    content_types.get(
                        f"*.{ext}",
                        "application/octet-stream"
                        if not name.endswith(".xml")
                        else "application/xml",
                    ),
                )
                disp = DISPOSITION_OPAQUE_PRESERVED
                if name.startswith("word/media/"):
                    disp = DISPOSITION_OPAQUE_PRESERVED
                elif name in ("word/document.xml", "word/styles.xml"):
                    disp = DISPOSITION_STRUCTURED
                parts_info.append(
                    DocxPartInfo(
                        uri=name,
                        content_type=ct,
                        size_bytes=len(data),
                        sha256=sha,
                        disposition=disp,
                    )
                )

            # 3. Read package relationships (_rels/.rels and word/_rels/document.xml.rels)
            rels_map: Dict[str, Dict[str, str]] = {}  # {source_part: {rId: target}}
            for name in namelist:
                if name.endswith(".rels"):
                    rel_data = zf.read(name)
                    try:
                        root_rel = ET.fromstring(rel_data)
                        source_part = (
                            name.replace("_rels/", "").replace(".rels", "")
                            if name != "_rels/.rels"
                            else "/"
                        )
                        rels_map[source_part] = {}
                        for r in root_rel:
                            rid = r.get("Id", "")
                            target = r.get("Target", "")
                            rtype = r.get("Type", "")
                            tmode = r.get("TargetMode", "Internal")
                            rels_info.append(
                                DocxRelInfo(
                                    rel_id=rid,
                                    source_part=source_part,
                                    target=target,
                                    rel_type=rtype,
                                    target_mode=tmode,
                                )
                            )
                            rels_map[source_part][rid] = target
                    except Exception:
                        pass

            doc_rels = rels_map.get("word/document.xml", {})

            # 4. Parse word/document.xml body (including sdt wrappers)
            if "word/document.xml" in namelist:
                doc_xml = zf.read("word/document.xml")
                root_doc = ET.fromstring(doc_xml)
                body = root_doc.find(f"{{{W_NS}}}body")
                if body is not None:
                    p_idx = 0
                    tbl_idx = 0

                    def _extract_blocks(container: ET.Element, base_loc: str) -> None:
                        nonlocal p_idx, tbl_idx
                        for child in container:
                            tag = child.tag
                            if tag == f"{{{W_NS}}}p":
                                p_nodes, p_drawings = self._parse_paragraph(
                                    child,
                                    part_uri="word/document.xml",
                                    xml_loc=f"{base_loc}/p[{p_idx}]",
                                    doc_rels=doc_rels,
                                )
                                nodes.extend(p_nodes)
                                drawings.extend(p_drawings)
                                p_idx += 1
                            elif tag == f"{{{W_NS}}}tbl":
                                tbl_node, tbl_obj = self._parse_table(
                                    child,
                                    part_uri="word/document.xml",
                                    xml_loc=f"{base_loc}/tbl[{tbl_idx}]",
                                )
                                nodes.append(tbl_node)
                                tables.append(tbl_obj)
                                tbl_idx += 1
                            elif tag in (f"{{{W_NS}}}sdt", f"{{{W_NS}}}customXml"):
                                # Descend into transparent wrappers so content inside is not missed
                                content_elem = child.find(f"{{{W_NS}}}sdtContent")
                                target = (
                                    content_elem if content_elem is not None else child
                                )
                                _extract_blocks(target, base_loc)
                            else:
                                # Any other body child must be accounted for, not
                                # silently dropped. Without this branch an unknown
                                # or future w:* block vanished before classification,
                                # so package-part coverage reported 1.0 even when
                                # content was lost. Emit a node so the disposition is
                                # explicit and `verify_all_classified` / the coverage
                                # gate can see it.
                                nodes.append(
                                    self._classify_unknown_block(child, base_loc)
                                )

                    _extract_blocks(body, "body")

            # 5. Parse Headers and Footers
            for name in namelist:
                if re.match(r"^word/(header|footer)\d+\.xml$", name):
                    h_xml = zf.read(name)
                    try:
                        root_h = ET.fromstring(h_xml)
                        kind_prefix = "header" if "header" in name else "footer"
                        for idx, child in enumerate(root_h):
                            if child.tag == f"{{{W_NS}}}p":
                                p_nodes, _ = self._parse_paragraph(
                                    child,
                                    part_uri=name,
                                    xml_loc=f"{kind_prefix}/p[{idx}]",
                                    doc_rels=doc_rels,
                                    default_kind=kind_prefix,
                                )
                                nodes.extend(p_nodes)
                    except Exception:
                        pass

            # 6. Parse Footnotes and Endnotes
            for name, kind_name in [
                ("word/footnotes.xml", "footnote"),
                ("word/endnotes.xml", "endnote"),
            ]:
                if name in namelist:
                    fn_xml = zf.read(name)
                    try:
                        root_fn = ET.fromstring(fn_xml)
                        for idx, fn in enumerate(root_fn):
                            fn_id = fn.get(f"{{{W_NS}}}id", str(idx))
                            # Skip standard separators
                            fn_type = fn.get(f"{{{W_NS}}}type")
                            if fn_type in ("separator", "continuationSeparator"):
                                continue
                            fn_text = "".join(fn.itertext()).strip()
                            if fn_text:
                                loc = DocxLocator(
                                    part_uri=name,
                                    xml_locator=f"{kind_name}[{fn_id}]",
                                    order=self._next_order(),
                                    kind=kind_name,
                                )
                                nodes.append(
                                    DocxNode(
                                        id=loc.stable_id,
                                        locator=loc,
                                        kind=kind_name,
                                        text=fn_text,
                                        payload={"id": fn_id},
                                        disposition=DISPOSITION_OPAQUE_PRESERVED,
                                        disposition_rationale=f"Reachable {kind_name} preserved opaquely",
                                    )
                                )
                    except Exception:
                        pass

            # 7. Parse Comments
            if "word/comments.xml" in namelist:
                c_xml = zf.read("word/comments.xml")
                try:
                    root_c = ET.fromstring(c_xml)
                    for idx, c in enumerate(root_c):
                        cid = c.get(f"{{{W_NS}}}id", str(idx))
                        author = c.get(f"{{{W_NS}}}author", "")
                        date = c.get(f"{{{W_NS}}}date", "")
                        c_text = "".join(c.itertext()).strip()
                        loc = DocxLocator(
                            part_uri="word/comments.xml",
                            xml_locator=f"comment[{cid}]",
                            order=self._next_order(),
                            kind="comment",
                        )
                        nodes.append(
                            DocxNode(
                                id=loc.stable_id,
                                locator=loc,
                                kind="comment",
                                text=c_text,
                                payload={"author": author, "date": date, "id": cid},
                                disposition=DISPOSITION_OPAQUE_PRESERVED,
                                disposition_rationale="Word review comment preserved opaquely",
                            )
                        )
                except Exception:
                    pass

        inventory = DocxPackageInventory(
            package_path=str(self.docx_path),
            parts=parts_info,
            relationships=rels_info,
            nodes=nodes,
        )
        doc = DocxDocument(inventory)
        doc.tables = tables
        doc.drawings = drawings
        return doc

    def _style_color(self, style_id: str) -> Optional[str]:
        """Resolve a style's inherited explicit RGB color."""
        seen: set[str] = set()
        while style_id and style_id not in seen:
            seen.add(style_id)
            color = self._style_colors.get(style_id)
            if color and color != "AUTO":
                return color
            style_id = self._style_bases.get(style_id, "")
        return None

    def _paragraph_run_colors(
        self, p_elem: ET.Element
    ) -> Tuple[List[Optional[str]], List[Optional[str]]]:
        """Return direct and effective colors for each non-empty run."""
        paragraph_style = p_elem.find(f"./{{{W_NS}}}pPr/{{{W_NS}}}pStyle")
        paragraph_color = self._style_color(
            paragraph_style.get(f"{{{W_NS}}}val", "")
            if paragraph_style is not None
            else ""
        )
        direct_colors: List[Optional[str]] = []
        inherited_colors: List[Optional[str]] = []
        for run_elem in p_elem.findall(f".//{{{W_NS}}}r"):
            if not "".join(run_elem.itertext()).strip():
                continue
            color_elem = run_elem.find(f"./{{{W_NS}}}rPr/{{{W_NS}}}color")
            direct = (
                color_elem.get(f"{{{W_NS}}}val", "").upper()
                if color_elem is not None
                else None
            )
            run_style = run_elem.find(f"./{{{W_NS}}}rPr/{{{W_NS}}}rStyle")
            run_style_color = self._style_color(
                run_style.get(f"{{{W_NS}}}val", "") if run_style is not None else ""
            )
            direct_colors.append(direct)
            inherited_colors.append(direct or run_style_color or paragraph_color)
        effective_colors = direct_colors if any(direct_colors) else inherited_colors
        return direct_colors, effective_colors

    def _parse_paragraph(
        self,
        p_elem: ET.Element,
        part_uri: str,
        xml_loc: str,
        doc_rels: Dict[str, str],
        default_kind: str = "paragraph",
    ) -> Tuple[List[DocxNode], List[DocxDrawing]]:
        nodes: List[DocxNode] = []
        drawings: List[DocxDrawing] = []

        # Determine style/heading level
        style_elem = p_elem.find(f".//{{{W_NS}}}pStyle")
        style_val = (
            style_elem.get(f"{{{W_NS}}}val", "") if style_elem is not None else ""
        )
        kind = default_kind
        heading_level = None
        if style_val.lower().startswith("heading"):
            m = re.search(r"\d+", style_val)
            if m:
                heading_level = int(m.group(0))
                kind = "heading"

        full_text = "".join(p_elem.itertext()).strip()

        # Preserve run colors in the typed IR so downstream classifiers can
        # distinguish template guidance from ordinary prose without reopening
        # the DOCX through python-docx (which has no stable XML locators).
        run_colors, effective_run_colors = self._paragraph_run_colors(p_elem)

        # Check for tracked revisions
        has_ins = p_elem.find(f".//{{{W_NS}}}ins") is not None
        has_del = p_elem.find(f".//{{{W_NS}}}del") is not None

        # Check for hyperlinks
        links = []
        for hl in p_elem.findall(f".//{{{W_NS}}}hyperlink"):
            rid = hl.get(f"{{{R_NS}}}id")
            target = doc_rels.get(rid, "") if rid else ""
            hl_text = "".join(hl.itertext()).strip()
            links.append({"rId": rid, "target": target, "text": hl_text})

        # Check for drawings
        for d_idx, drawing_elem in enumerate(p_elem.findall(f".//{{{W_NS}}}drawing")):
            blip = drawing_elem.find(f".//{{{A_NS}}}blip")
            if blip is not None:
                rid = blip.get(f"{{{R_NS}}}embed")
                if rid and rid in doc_rels:
                    target_part = "word/" + doc_rels[rid].lstrip("/")
                    draw = DocxDrawing(
                        node_id=f"{part_uri}#{xml_loc}/drawing[{rid}]",
                        relationship_id=rid,
                        target_part_uri=target_part,
                    )
                    drawings.append(draw)
                    # Add drawing as an IR sub-node with opaque_preserved disposition
                    d_loc = DocxLocator(
                        part_uri=part_uri,
                        xml_locator=f"{xml_loc}/drawing[{d_idx}]",
                        order=self._next_order(),
                        kind="drawing",
                    )
                    nodes.append(
                        DocxNode(
                            id=d_loc.stable_id,
                            locator=d_loc,
                            kind="drawing",
                            text=f"Drawing {rid} targeting {target_part}",
                            payload={
                                "relationship_id": rid,
                                "target_part_uri": target_part,
                            },
                            disposition=DISPOSITION_OPAQUE_PRESERVED,
                            disposition_rationale="Embedded drawing/image part preserved opaquely",
                        )
                    )

        # Build primary paragraph/heading node
        loc = DocxLocator(
            part_uri=part_uri,
            xml_locator=xml_loc,
            order=self._next_order(),
            kind=kind,
        )
        disp = (
            DISPOSITION_STRUCTURED
            if (kind == "heading" or full_text)
            else DISPOSITION_INTENTIONALLY_EXCLUDED
        )
        rationale = (
            "Extracted body text / heading"
            if disp == DISPOSITION_STRUCTURED
            else "Empty structural paragraph"
        )

        node = DocxNode(
            id=loc.stable_id,
            locator=loc,
            kind=kind,
            text=full_text,
            payload={
                "style": style_val,
                "heading_level": heading_level,
                "hyperlinks": links,
                "has_revisions": has_ins or has_del,
                "drawings_count": len(drawings),
                "run_colors": run_colors,
                "effective_run_colors": effective_run_colors,
            },
            disposition=disp,
            disposition_rationale=rationale,
        )
        nodes.append(node)

        # If revisions exist, add sub-nodes
        if has_ins:
            for ins_idx, ins_elem in enumerate(p_elem.findall(f".//{{{W_NS}}}ins")):
                ins_loc = DocxLocator(
                    part_uri=part_uri,
                    xml_locator=f"{xml_loc}/ins[{ins_idx}]",
                    order=self._next_order(),
                    kind="revision_ins",
                )
                nodes.append(
                    DocxNode(
                        id=ins_loc.stable_id,
                        locator=ins_loc,
                        kind="revision_ins",
                        parent_id=loc.stable_id,
                        text="".join(ins_elem.itertext()).strip(),
                        payload={
                            "author": ins_elem.get(f"{{{W_NS}}}author"),
                            "date": ins_elem.get(f"{{{W_NS}}}date"),
                        },
                        disposition=DISPOSITION_OPAQUE_PRESERVED,
                        disposition_rationale="Tracked insertion revision preserved opaquely",
                    )
                )

        if has_del:
            for del_idx, del_elem in enumerate(p_elem.findall(f".//{{{W_NS}}}del")):
                del_loc = DocxLocator(
                    part_uri=part_uri,
                    xml_locator=f"{xml_loc}/del[{del_idx}]",
                    order=self._next_order(),
                    kind="revision_del",
                )
                nodes.append(
                    DocxNode(
                        id=del_loc.stable_id,
                        locator=del_loc,
                        kind="revision_del",
                        parent_id=loc.stable_id,
                        text="".join(del_elem.itertext()).strip(),
                        payload={
                            "author": del_elem.get(f"{{{W_NS}}}author"),
                            "date": del_elem.get(f"{{{W_NS}}}date"),
                        },
                        disposition=DISPOSITION_OPAQUE_PRESERVED,
                        disposition_rationale="Tracked deletion revision preserved opaquely",
                    )
                )

        return nodes, drawings

    def _parse_table(
        self,
        tbl_elem: ET.Element,
        part_uri: str,
        xml_loc: str,
    ) -> Tuple[DocxNode, DocxTable]:
        rows: List[List[DocxTableCell]] = []
        r_idx = 0
        for tr in tbl_elem.findall(f"{{{W_NS}}}tr"):
            row_cells: List[DocxTableCell] = []
            c_idx = 0
            for tc in tr.findall(f"{{{W_NS}}}tc"):
                cell_loc = f"{xml_loc}/tr[{r_idx}]/tc[{c_idx}]"
                paragraphs: List[Dict[str, Any]] = []
                for p_idx, p_elem in enumerate(tc.findall(f"{{{W_NS}}}p")):
                    paragraph_text = "".join(p_elem.itertext()).strip()
                    run_colors, effective_run_colors = self._paragraph_run_colors(
                        p_elem
                    )
                    paragraphs.append(
                        {
                            "locator": f"{part_uri}#{cell_loc}/p[{p_idx}]",
                            "text": paragraph_text,
                            "run_colors": run_colors,
                            "effective_run_colors": effective_run_colors,
                        }
                    )
                text = "\n".join(
                    paragraph["text"] for paragraph in paragraphs if paragraph["text"]
                )

                # Check gridSpan
                grid_span = 1
                gs_elem = tc.find(f".//{{{W_NS}}}gridSpan")
                if gs_elem is not None:
                    try:
                        grid_span = int(gs_elem.get(f"{{{W_NS}}}val", "1"))
                    except ValueError:
                        grid_span = 1

                # Check vMerge
                v_merge = None
                vm_elem = tc.find(f".//{{{W_NS}}}vMerge")
                if vm_elem is not None:
                    v_merge = vm_elem.get(f"{{{W_NS}}}val", "continue")

                row_cells.append(
                    DocxTableCell(
                        row_idx=r_idx,
                        col_idx=c_idx,
                        text=text,
                        grid_span=grid_span,
                        v_merge=v_merge,
                        cell_locator=f"{part_uri}#{cell_loc}",
                        paragraphs=paragraphs,
                    )
                )
                c_idx += 1
            rows.append(row_cells)
            r_idx += 1

        num_rows = len(rows)
        num_cols = max((len(r) for r in rows), default=0)

        tbl_loc = DocxLocator(
            part_uri=part_uri,
            xml_locator=xml_loc,
            order=self._next_order(),
            kind="table",
        )
        tbl_obj = DocxTable(
            node_id=tbl_loc.stable_id,
            locator=tbl_loc,
            num_rows=num_rows,
            num_cols=num_cols,
            cells=rows,
        )

        flat_text = " | ".join(" ".join(c.text for c in r if c.text) for r in rows if r)
        node = DocxNode(
            id=tbl_loc.stable_id,
            locator=tbl_loc,
            kind="table",
            text=flat_text,
            payload={
                "rows": num_rows,
                "cols": num_cols,
                "has_merged_cells": any(
                    c.grid_span > 1 or c.v_merge is not None for r in rows for c in r
                ),
            },
            disposition=DISPOSITION_STRUCTURED,
            disposition_rationale="Structured tabular content extracted",
        )
        return node, tbl_obj
