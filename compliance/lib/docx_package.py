"""Inventory WordprocessingML package parts and body nodes before extraction.

The inventory answers one question: can any content-bearing piece of a ``.docx``
package disappear without being reported? It therefore works at two levels:

1. **Package level** -- every ZIP member is enumerated, resolved against the OPC
   relationship graph (so reachability is a fact, not an assumption) and given an
   explicit disposition.
2. **Node level** -- every direct child of a content part body
   (``word/document.xml``, headers, footers, footnotes, endnotes, comments) is
   classified. Unknown content-bearing XML is an ``unmapped-error``, not silence.

Dispositions are deliberately conservative:

``structured``
    The content is parsed by the semantic extractors.
``opaque-preserved``
    The content is not parsed, but the bytes are inventoried with a digest so a
    later stage can prove the artifact was carried forward unchanged.
``intentionally-excluded``
    Package plumbing or presentation configuration with a recorded rationale.
``unmapped-error``
    Nothing claims this content. Strict mode fails.

This module is read-only, and it never mutates the source package.
"""

from __future__ import annotations

import hashlib
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

_CONTENT_TYPES = "[Content_Types].xml"
_ROOT_RELS = "_rels/.rels"
_RELS_SUFFIX = ".rels"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NAMESPACES = {
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "w": _W_NS,
}

# ---------------------------------------------------------------------------
# Decompression-bomb and parser limits.
#
# A DOCX is attacker-supplied review input. Every limit below is checked from
# the ZIP central directory *before* any member is decompressed, so a small
# archive cannot force an unbounded read.
# ---------------------------------------------------------------------------
MAX_MEMBERS = 4096
MAX_PART_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200

DISPOSITIONS = (
    "structured",
    "opaque-preserved",
    "intentionally-excluded",
    "unmapped-error",
)


class PackageLimitError(ValueError):
    """The package exceeds a decompression or structural safety limit."""


class PackageIntegrityError(ValueError):
    """The package is structurally ambiguous and cannot be inventoried."""


@dataclass(frozen=True)
class PackageRelationship:
    """One OPC relationship, retaining its source part and target mode."""

    source_part: str
    rels_part: str
    id: str
    type: str
    target: str
    target_mode: str
    resolved_target: str | None


@dataclass(frozen=True)
class PackagePart:
    """A package member and its current extraction disposition."""

    path: str
    content_type: str | None
    size: int
    sha256: str
    disposition: str
    reason: str
    reachable: bool
    referenced_by: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class PackageNode:
    """A content-part body node and its current extraction disposition."""

    part_uri: str
    xml_locator: str
    order: int
    kind: str
    disposition: str
    reason: str
    text_length: int

    @property
    def locator(self) -> str:
        return f"{self.part_uri}#{self.xml_locator}"


# ---------------------------------------------------------------------------
# Part-level classification tables
# ---------------------------------------------------------------------------

_STRUCTURED_PARTS = {
    "word/document.xml": "main document body is parsed by the semantic extractors",
    "word/document2.xml": "main document body is parsed by the semantic extractors",
}

# Exact filename grammars. Prefix matching is deliberately avoided so that an
# undeclared part such as ``word/headerUnsupported.xml`` cannot inherit a
# header's disposition.
_OPAQUE_EXACT = {
    "word/footnotes.xml": "footnote text is inventoried but not yet routed",
    "word/endnotes.xml": "endnote text is inventoried but not yet routed",
    "word/comments.xml": "comment text is inventoried but not yet routed",
    "word/commentsExtended.xml": "threaded comment metadata is not yet routed",
    "word/commentsIds.xml": "comment identity metadata is not yet routed",
    "word/commentsExtensible.xml": "comment extension metadata is not yet routed",
    "word/people.xml": "comment/revision author metadata is not yet routed",
}

_EXCLUDED_EXACT = {
    _CONTENT_TYPES: "package content-type map",
    _ROOT_RELS: "package root relationship map",
    "word/styles.xml": "presentation configuration, not semantic SSP content",
    "word/stylesWithEffects.xml": "presentation configuration, not semantic SSP content",
    "word/settings.xml": "editor configuration, not semantic SSP content",
    "word/webSettings.xml": "editor configuration, not semantic SSP content",
    "word/fontTable.xml": "font metadata, not semantic SSP content",
    "word/numbering.xml": "list numbering definitions, not semantic SSP content",
    "word/glossary/document.xml": (
        "glossary/building-block storage is not SSP body content"
    ),
}

_EXCLUDED_PREFIXES = {
    "docprops/": "document metadata is not semantic SSP content",
    "word/theme/": "theme/presentation configuration, not semantic SSP content",
    "word/glossary/": "glossary/building-block storage is not SSP body content",
}

_OPAQUE_PREFIXES = {
    "word/media/": "binary media part retained by package inventory",
    "word/embeddings/": "embedded object retained by package inventory",
    "word/charts/": "chart part retained by package inventory",
    "word/diagrams/": "SmartArt/diagram part retained by package inventory",
    "word/ink/": "ink annotation part retained by package inventory",
    "customxml/": "custom XML storage retained by package inventory",
}

_HEADER_FOOTER_STEMS = ("header", "footer")

# Content-type classification. OPC content types are the authoritative part
# identity, so they are consulted whenever the part path is not already a known
# fixed name. This keeps real-world casing/layout variants (``customXML/`` vs
# ``customXml/``, embedded font directories) from falling through to
# ``unmapped-error`` purely on spelling.
_CONTENT_TYPE_DISPOSITIONS: dict[str, tuple[str, str]] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": (
        "structured",
        "main document body is parsed by the semantic extractors",
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml": (
        "opaque-preserved",
        "header text is inventoried but not routed",
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml": (
        "opaque-preserved",
        "footer text is inventoried but not routed",
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml": (
        "opaque-preserved",
        "footnote text is inventoried but not yet routed",
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml": (
        "opaque-preserved",
        "endnote text is inventoried but not yet routed",
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml": (
        "opaque-preserved",
        "comment text is inventoried but not yet routed",
    ),
    "application/vnd.openxmlformats-officedocument.customXmlProperties+xml": (
        "opaque-preserved",
        "custom XML property store retained by package inventory",
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml": (
        "intentionally-excluded",
        "font metadata, not semantic SSP content",
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml": (
        "intentionally-excluded",
        "presentation configuration, not semantic SSP content",
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml": (
        "intentionally-excluded",
        "editor configuration, not semantic SSP content",
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.webSettings+xml": (
        "intentionally-excluded",
        "editor configuration, not semantic SSP content",
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml": (
        "intentionally-excluded",
        "list numbering definitions, not semantic SSP content",
    ),
    "application/vnd.openxmlformats-officedocument.theme+xml": (
        "intentionally-excluded",
        "theme/presentation configuration, not semantic SSP content",
    ),
    "application/x-font-ttf": (
        "intentionally-excluded",
        "embedded font program, not semantic SSP content",
    ),
    "application/vnd.openxmlformats-officedocument.obfuscatedFont": (
        "intentionally-excluded",
        "embedded font program, not semantic SSP content",
    ),
}

# Content-type prefixes for binary payloads that are preserved verbatim.
_CONTENT_TYPE_PREFIX_DISPOSITIONS: tuple[tuple[str, tuple[str, str]], ...] = (
    (
        "image/",
        ("opaque-preserved", "binary media part retained by package inventory"),
    ),
    (
        "video/",
        ("opaque-preserved", "binary media part retained by package inventory"),
    ),
    (
        "audio/",
        ("opaque-preserved", "binary media part retained by package inventory"),
    ),
)


def _is_header_or_footer(path: str) -> bool:
    if not path.startswith("word/"):
        return False
    name = path[len("word/") :]
    for stem in _HEADER_FOOTER_STEMS:
        if (
            name.startswith(stem)
            and name.endswith(".xml")
            and name[len(stem) : -len(".xml")].isdigit()
        ):
            return True
    return False


# Content parts whose body nodes are enumerated individually.
_NODE_SCOPED_PARTS = {
    "word/document.xml",
    "word/footnotes.xml",
    "word/endnotes.xml",
    "word/comments.xml",
}

# ... plus any part whose declared OPC content type is a WordprocessingML text
# container, so a differently-named header/footnote part is still enumerated.
_NODE_SCOPED_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
}


def _is_node_scoped(path: str, content_type: str | None) -> bool:
    if path in _NODE_SCOPED_PARTS or _is_header_or_footer(path):
        return True
    return content_type in _NODE_SCOPED_CONTENT_TYPES


# ---------------------------------------------------------------------------
# Node-level classification tables
# ---------------------------------------------------------------------------

# Elements the semantic extractors consume directly.
_STRUCTURED_NODE_TAGS = {
    "p": "paragraph",
    "tbl": "table",
}

# Transparent wrappers: the inventory descends into these rather than treating
# the wrapper itself as a leaf, so content inside a content control or a tracked
# revision cannot hide behind the wrapper's own disposition.
_TRANSPARENT_NODE_TAGS = {
    "sdt",
    "sdtContent",
    "customXml",
    "smartTag",
    "ins",
    "del",
    "moveFrom",
    "moveTo",
    "body",
    "footnote",
    "endnote",
    "comment",
    "txbxContent",
}

# Structural or range markers that carry no text of their own.
_NON_CONTENT_NODE_TAGS = {
    "sectPr",
    "bookmarkStart",
    "bookmarkEnd",
    "proofErr",
    "commentRangeStart",
    "commentRangeEnd",
    "permStart",
    "permEnd",
    "customXmlInsRangeStart",
    "customXmlInsRangeEnd",
    "customXmlDelRangeStart",
    "customXmlDelRangeEnd",
    "customXmlMoveFromRangeStart",
    "customXmlMoveFromRangeEnd",
    "customXmlMoveToRangeStart",
    "customXmlMoveToRangeEnd",
    "moveFromRangeStart",
    "moveFromRangeEnd",
    "moveToRangeStart",
    "moveToRangeEnd",
    "sdtPr",
    "sdtEndPr",
    "customXmlPr",
    "smartTagPr",
    "tblPr",
    "footnotePr",
    "endnotePr",
}

# Content-bearing elements that are knowingly not routed yet.
_OPAQUE_NODE_TAGS = {
    "altChunk": "external content import is inventoried but not parsed",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Safe archive access
# ---------------------------------------------------------------------------


def _check_limits(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise PackageLimitError(
            f"package declares {len(infos)} members; limit is {MAX_MEMBERS}"
        )
    total = 0
    for info in infos:
        if info.file_size > MAX_PART_BYTES:
            raise PackageLimitError(
                f"part {info.filename!r} declares {info.file_size} bytes; "
                f"limit is {MAX_PART_BYTES}"
            )
        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > MAX_COMPRESSION_RATIO and info.file_size > 1024 * 1024:
                raise PackageLimitError(
                    f"part {info.filename!r} compression ratio {ratio:.0f} exceeds "
                    f"limit {MAX_COMPRESSION_RATIO}"
                )
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise PackageLimitError(
                "package uncompressed size exceeds "
                f"{MAX_TOTAL_UNCOMPRESSED_BYTES} bytes"
            )


def _check_unique_members(archive: zipfile.ZipFile) -> None:
    counts = Counter(info.filename for info in archive.infolist())
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    if duplicates:
        raise PackageIntegrityError(
            "package contains duplicate member names, so part identity is "
            f"ambiguous: {duplicates}"
        )


def _read_parts(archive: zipfile.ZipFile) -> dict[str, bytes]:
    """Read every non-directory member once, keyed by member name."""
    payloads: dict[str, bytes] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        with archive.open(info) as handle:
            payload = handle.read(info.file_size + 1)
        if len(payload) > info.file_size:
            raise PackageLimitError(
                f"part {info.filename!r} is larger than its declared size"
            )
        payloads[info.filename] = payload
    return payloads


def _parse_xml(part: str, payload: bytes) -> ET.Element:
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:  # pragma: no cover - exercised via tests
        raise PackageIntegrityError(
            f"part {part!r} is not parseable XML: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Content types and relationships
# ---------------------------------------------------------------------------


def _content_types(payloads: dict[str, bytes]) -> dict[str, str | None]:
    if _CONTENT_TYPES not in payloads:
        raise PackageIntegrityError("package is missing [Content_Types].xml")
    root = _parse_xml(_CONTENT_TYPES, payloads[_CONTENT_TYPES])
    defaults = {
        element.attrib["Extension"].lower(): element.attrib["ContentType"]
        for element in root.findall("ct:Default", _NAMESPACES)
        if "Extension" in element.attrib and "ContentType" in element.attrib
    }
    overrides = {
        element.attrib["PartName"].lstrip("/"): element.attrib["ContentType"]
        for element in root.findall("ct:Override", _NAMESPACES)
        if "PartName" in element.attrib and "ContentType" in element.attrib
    }
    result: dict[str, str | None] = {}
    for name in payloads:
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        result[name] = overrides.get(name) or defaults.get(extension)
    return result


def _resolve_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    base = posixpath.dirname(source_part)
    return posixpath.normpath(posixpath.join(base, target))


def _rels_part_for(part: str) -> str:
    base = posixpath.dirname(part)
    name = posixpath.basename(part)
    return (
        posixpath.join(base, "_rels", f"{name}{_RELS_SUFFIX}")
        if base
        else posixpath.join("_rels", f"{name}{_RELS_SUFFIX}")
    )


def _source_part_for_rels(rels_part: str) -> str:
    if rels_part == _ROOT_RELS:
        return ""
    directory = posixpath.dirname(posixpath.dirname(rels_part))
    name = posixpath.basename(rels_part)[: -len(_RELS_SUFFIX)]
    return posixpath.join(directory, name) if directory else name


def _relationships(payloads: dict[str, bytes]) -> list[PackageRelationship]:
    relationships: list[PackageRelationship] = []
    for name in sorted(payloads):
        if not name.endswith(_RELS_SUFFIX):
            continue
        root = _parse_xml(name, payloads[name])
        source_part = _source_part_for_rels(name)
        for element in root.findall("rel:Relationship", _NAMESPACES):
            target = element.attrib.get("Target", "")
            mode = element.attrib.get("TargetMode", "Internal")
            resolved = (
                _resolve_target(source_part, target)
                if mode == "Internal" and target
                else None
            )
            relationships.append(
                PackageRelationship(
                    source_part=source_part,
                    rels_part=name,
                    id=element.attrib.get("Id", ""),
                    type=element.attrib.get("Type", ""),
                    target=target,
                    target_mode=mode,
                    resolved_target=resolved,
                )
            )
    return relationships


def _reachable_parts(
    relationships: Iterable[PackageRelationship],
    payloads: dict[str, bytes],
) -> tuple[set[str], dict[str, list[str]], list[str]]:
    """Walk the OPC graph from the root relationship part.

    Returns the reachable part set, a map of part -> referring relationship
    locators, and the list of internal relationships whose target is missing.
    """
    by_source: dict[str, list[PackageRelationship]] = {}
    for relationship in relationships:
        by_source.setdefault(relationship.source_part, []).append(relationship)

    referenced_by: dict[str, list[str]] = {}
    dangling: list[str] = []
    reachable: set[str] = set()
    queue: list[str] = [""]
    seen_sources: set[str] = set()

    while queue:
        source = queue.pop()
        if source in seen_sources:
            continue
        seen_sources.add(source)
        rels_part = _ROOT_RELS if source == "" else _rels_part_for(source)
        if rels_part in payloads:
            reachable.add(rels_part)
        for relationship in by_source.get(source, []):
            if (
                relationship.target_mode != "Internal"
                or not relationship.resolved_target
            ):
                continue
            target = relationship.resolved_target
            locator = f"{relationship.rels_part}#{relationship.id}"
            referenced_by.setdefault(target, []).append(locator)
            if target not in payloads:
                dangling.append(f"{locator} targets missing part {target!r}")
                continue
            reachable.add(target)
            queue.append(target)

    reachable.add(_CONTENT_TYPES)
    return reachable, referenced_by, sorted(dangling)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify_part(
    path: str, *, content_type: str | None, reachable: bool
) -> tuple[str, str]:
    if path.endswith(_RELS_SUFFIX):
        return "intentionally-excluded", "relationship map"
    if path in _EXCLUDED_EXACT:
        return "intentionally-excluded", _EXCLUDED_EXACT[path]
    if path in _STRUCTURED_PARTS:
        return "structured", _STRUCTURED_PARTS[path]
    if path in _OPAQUE_EXACT:
        return "opaque-preserved", _OPAQUE_EXACT[path]
    if _is_header_or_footer(path):
        return "opaque-preserved", "header/footer text is inventoried but not routed"
    lowered = path.lower()
    for prefix, reason in sorted(_EXCLUDED_PREFIXES.items()):
        if lowered.startswith(prefix):
            return "intentionally-excluded", reason
    for prefix, reason in sorted(_OPAQUE_PREFIXES.items()):
        if lowered.startswith(prefix):
            return "opaque-preserved", reason
    # The OPC content type is authoritative part identity; consult it before
    # declaring a part unmapped so that casing/layout variants of a known part
    # kind are classified by what they are rather than by where they sit.
    if content_type:
        declared = _CONTENT_TYPE_DISPOSITIONS.get(content_type)
        if declared:
            return declared
        for prefix, result in _CONTENT_TYPE_PREFIX_DISPOSITIONS:
            if content_type.startswith(prefix):
                return result
    if not reachable:
        return (
            "unmapped-error",
            "package member is not reachable from the OPC relationship graph",
        )
    return "unmapped-error", "reachable package part has no declared disposition"


def _classify_nodes(part_uri: str, payload: bytes) -> list[PackageNode]:
    root = _parse_xml(part_uri, payload)
    nodes: list[PackageNode] = []
    counter = {"order": 0}

    def text_length(element: ET.Element) -> int:
        return len("".join(element.itertext()).strip())

    def walk(element: ET.Element, path: str) -> None:
        index_by_tag: Counter[str] = Counter()
        for child in element:
            if not isinstance(child.tag, str):  # comments / processing instructions
                continue
            tag = _local(child.tag)
            index_by_tag[tag] += 1
            locator = f"{path}/{tag}[{index_by_tag[tag]}]"
            if tag in _TRANSPARENT_NODE_TAGS:
                walk(child, locator)
                continue
            if tag in _STRUCTURED_NODE_TAGS:
                counter["order"] += 1
                nodes.append(
                    PackageNode(
                        part_uri=part_uri,
                        xml_locator=locator,
                        order=counter["order"],
                        kind=_STRUCTURED_NODE_TAGS[tag],
                        disposition="structured",
                        reason="node kind is consumed by the semantic extractors",
                        text_length=text_length(child),
                    )
                )
                continue
            if tag in _OPAQUE_NODE_TAGS:
                counter["order"] += 1
                nodes.append(
                    PackageNode(
                        part_uri=part_uri,
                        xml_locator=locator,
                        order=counter["order"],
                        kind=tag,
                        disposition="opaque-preserved",
                        reason=_OPAQUE_NODE_TAGS[tag],
                        text_length=text_length(child),
                    )
                )
                continue
            length = text_length(child)
            if tag in _NON_CONTENT_NODE_TAGS and length == 0:
                counter["order"] += 1
                nodes.append(
                    PackageNode(
                        part_uri=part_uri,
                        xml_locator=locator,
                        order=counter["order"],
                        kind=tag,
                        disposition="intentionally-excluded",
                        reason="structural marker carries no document text",
                        text_length=0,
                    )
                )
                continue
            counter["order"] += 1
            nodes.append(
                PackageNode(
                    part_uri=part_uri,
                    xml_locator=locator,
                    order=counter["order"],
                    kind=tag,
                    disposition="unmapped-error"
                    if length
                    else "intentionally-excluded",
                    reason="content-bearing node has no declared disposition"
                    if length
                    else "empty node with no declared disposition",
                    text_length=length,
                )
            )

    walk(root, _local(root.tag))
    return nodes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _part_record(part: PackagePart) -> dict[str, object]:
    record = asdict(part)
    record["referenced_by"] = list(part.referenced_by)
    return record


def inspect_package(path: Path) -> dict[str, object]:
    """Return an exhaustive package-part and body-node inventory for a DOCX.

    The report is deterministic for identical package bytes: it is keyed by the
    package file name and content digest, never by the caller's working
    directory.
    """
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        _check_limits(archive)
        _check_unique_members(archive)
        payloads = _read_parts(archive)

    content_types = _content_types(payloads)
    relationships = _relationships(payloads)
    reachable, referenced_by, dangling = _reachable_parts(relationships, payloads)

    parts: list[PackagePart] = []
    for name in sorted(payloads):
        is_reachable = name in reachable
        content_type = content_types.get(name)
        disposition, reason = _classify_part(
            name, content_type=content_type, reachable=is_reachable
        )
        parts.append(
            PackagePart(
                path=name,
                content_type=content_type,
                size=len(payloads[name]),
                sha256=_sha256(payloads[name]),
                disposition=disposition,
                reason=reason,
                reachable=is_reachable,
                referenced_by=tuple(sorted(referenced_by.get(name, []))),
            )
        )

    nodes: list[PackageNode] = []
    for name in sorted(payloads):
        if _is_node_scoped(name, content_types.get(name)):
            nodes.extend(_classify_nodes(name, payloads[name]))

    part_dispositions = Counter(part.disposition for part in parts)
    node_dispositions = Counter(node.disposition for node in nodes)
    return {
        "docx": path.name,
        "docx_sha256": _sha256(path.read_bytes()),
        "parts": [_part_record(part) for part in parts],
        "relationships": [asdict(relationship) for relationship in relationships],
        "nodes": [asdict(node) for node in nodes],
        "summary": {
            "part_count": len(parts),
            "node_count": len(nodes),
            "relationship_count": len(relationships),
            "external_relationship_count": sum(
                1 for rel in relationships if rel.target_mode != "Internal"
            ),
            "unreachable_part_count": sum(1 for part in parts if not part.reachable),
            "dispositions": dict(sorted(part_dispositions.items())),
            "node_dispositions": dict(sorted(node_dispositions.items())),
            "unmapped_parts": [
                part.path for part in parts if part.disposition == "unmapped-error"
            ],
            "unmapped_nodes": [
                node.locator for node in nodes if node.disposition == "unmapped-error"
            ],
            "dangling_relationships": dangling,
        },
    }


def unresolved_parts(report: dict[str, object]) -> list[str]:
    """Return package parts that lack a declared disposition."""
    summary: dict[str, object] = report["summary"]  # type: ignore[assignment]
    return list(summary["unmapped_parts"])  # type: ignore[arg-type]


def unresolved_nodes(report: dict[str, object]) -> list[str]:
    """Return content-bearing body nodes that lack a declared disposition."""
    summary: dict[str, object] = report["summary"]  # type: ignore[assignment]
    return list(summary["unmapped_nodes"])  # type: ignore[arg-type]


def strict_findings(report: dict[str, object]) -> list[str]:
    """Return every fail-closed finding: unmapped parts, nodes, dangling refs."""
    summary: dict[str, object] = report["summary"]  # type: ignore[assignment]
    findings = [f"unmapped-part: {item}" for item in unresolved_parts(report)]
    findings += [f"unmapped-node: {item}" for item in unresolved_nodes(report)]
    findings += [
        f"dangling-relationship: {item}"
        for item in summary["dangling_relationships"]  # type: ignore[index]
    ]
    return findings
