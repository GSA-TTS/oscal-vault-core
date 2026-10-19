"""Instrumented, residual-aware transformations used by OSCAL adapters."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Generic, List, TypeVar

from compliance.lib.table_records import parse_table_records, table_rows

T = TypeVar("T")

_RECOVERY_CALLOUT = re.compile(r"^>\s*Recovered from\s+.+$", re.IGNORECASE)


def digest_value(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TransformEvent:
    operator: str
    input_sha256: str
    output_sha256: str
    changed: bool
    lossiness: str
    consumed: tuple[str, ...] = ()
    residual: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        value = asdict(self)
        for key in ("consumed", "residual", "warnings"):
            value[key] = list(value[key])
        return value


@dataclass(frozen=True)
class TransformResult(Generic[T]):
    value: T
    events: tuple[TransformEvent, ...] = ()


@dataclass(frozen=True)
class AdapterResult(Generic[T]):
    value: T
    events: tuple[TransformEvent, ...] = ()
    preserved_extensions: tuple[dict, ...] = ()


@dataclass(frozen=True)
class PortRange:
    start: int
    end: int
    transport: str | None = None

    def to_oscal(self) -> dict:
        value = {"start": self.start, "end": self.end}
        if self.transport:
            value["transport"] = self.transport
        return value


def filter_generated_recovery_callout(text: str) -> TransformResult[str]:
    """Remove only generated recovery callouts; preserve authored blockquotes."""
    original = text or ""
    lines = original.splitlines()
    removed = tuple(line for line in lines if _RECOVERY_CALLOUT.fullmatch(line.strip()))
    output = "\n".join(line for line in lines if line not in removed).strip()
    if not removed:
        return TransformResult(output)
    return TransformResult(
        output,
        (
            TransformEvent(
                operator="generated_callout_filter",
                input_sha256=digest_value(original),
                output_sha256=digest_value(output),
                changed=True,
                lossiness="provenance_only",
                consumed=removed,
            ),
        ),
    )


def parse_gfm_table(text: str) -> TransformResult[List[dict[str, str]]]:
    """Parse a GFM table and report whether table syntax was consumed."""
    original = text or ""
    records = parse_table_records(original)
    rows = table_rows(records[0]) if records else []
    if not records:
        return TransformResult(rows)
    return TransformResult(
        rows,
        (
            TransformEvent(
                operator="gfm_table_parse",
                input_sha256=digest_value(original),
                output_sha256=digest_value(rows),
                changed=True,
                lossiness="structural",
                consumed=(original,),
            ),
        ),
    )


def parse_markdown_list(text: str) -> TransformResult[List[str]]:
    """Parse bullet lines; plain single-line values are not reported as a split."""
    original = text or ""
    nonblank = [line.strip() for line in original.splitlines() if line.strip()]
    bullet = all(re.match(r"^[-*+]\s+", line) for line in nonblank) and bool(nonblank)
    values = [re.sub(r"^[-*+]\s+", "", line).strip() for line in nonblank]
    if not bullet or len(values) <= 1:
        return TransformResult(values)
    return TransformResult(
        values,
        (
            TransformEvent(
                operator="markdown_list_parse",
                input_sha256=digest_value(original),
                output_sha256=digest_value(values),
                changed=True,
                lossiness="structural",
                consumed=tuple(nonblank),
            ),
            TransformEvent(
                operator="record_fan_out",
                input_sha256=digest_value(original),
                output_sha256=digest_value(values),
                changed=True,
                lossiness="none",
                consumed=tuple(values),
            ),
        ),
    )


_PORT_TOKEN = re.compile(
    r"(?P<label>ephemeral\s*)?\(?\s*(?P<start>\d{1,5})"
    r"(?:(?P<separator>\s*[-/]\s*)(?P<end>\d{1,5}))?\s*\)?"
    r"(?:\s*\(?\s*(?P<transport>TCP|UDP|T|U)(?:\s*/\s*(?P<transport2>TCP|UDP|T|U))?\s*\)?)?",
    re.IGNORECASE,
)


def parse_port_expression(text: str) -> TransformResult[List[PortRange]]:
    """Parse ports/ranges with complete residual accounting."""
    original = (text or "").strip()
    ranges: List[PortRange] = []
    spans: List[tuple[int, int]] = []
    consumed: List[str] = []
    for match in _PORT_TOKEN.finditer(original):
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        separator = (match.group("separator") or "").strip()
        if not (0 <= start <= 65535 and 0 <= end <= 65535):
            continue
        transports = [match.group("transport"), match.group("transport2")]
        normalized = [
            {"T": "TCP", "U": "UDP"}.get(str(item).upper(), str(item).upper())
            for item in transports
            if item
        ] or [None]
        bounds = [(start, end)] if separator != "/" else [(start, start), (end, end)]
        for range_start, range_end in bounds:
            if range_start > range_end:
                continue
            for transport in normalized:
                ranges.append(
                    PortRange(
                        start=range_start,
                        end=range_end,
                        transport=transport,
                    )
                )
        spans.append(match.span())
        consumed.append(match.group(0).strip())
    residual_text = original
    for start, end in reversed(spans):
        residual_text = residual_text[:start] + " " + residual_text[end:]
    residual = tuple(
        part.strip(" ,;:/")
        for part in re.split(r"\band\b|[,;]", residual_text, flags=re.IGNORECASE)
        if part.strip(" ,;:/")
    )
    event = TransformEvent(
        operator="port_expression_parse",
        input_sha256=digest_value(original),
        output_sha256=digest_value([item.to_oscal() for item in ranges]),
        changed=bool(ranges),
        lossiness="partial" if residual else "none",
        consumed=tuple(consumed),
        residual=residual,
        warnings=("unparsed port expression preserved",) if residual else (),
    )
    return TransformResult(ranges, (event,))
