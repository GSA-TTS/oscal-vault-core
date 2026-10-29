"""Validation primitives for CycloneDX threat-model artifacts built from a vault.

One place for the enum sets, the redaction leak scan (URLs, e-mail addresses, IPv4) and the
threat/control cross-reference lint, so every entry point agrees.
"""

from __future__ import annotations

import re

from .redaction import EMAIL_RE, IPV4_RE, SECTION_CONTEXT_RE, URL_RE

CATEGORIES = frozenset({"preventive", "detective", "corrective"})
STATUSES = frozenset(
    {"implemented", "recommended", "verified", "rejected", "planned", "in-progress"}
)
RISK_LEVELS = frozenset({"low", "medium", "high", "critical", "info"})


FORBIDDEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (URL_RE, "URL"),
    (EMAIL_RE, "email"),
]


def find_ipv4_leak(text: str) -> re.Match[str] | None:
    """Return the first non-NIST-section-cited IPv4 in ``text`` (or None)."""
    for m in IPV4_RE.finditer(text):
        preceding = text[max(0, m.start() - 40) : m.start()]
        if SECTION_CONTEXT_RE.search(preceding):
            continue
        return m
    return None


def find_leaks(text: str) -> list[tuple[str, str]]:
    """Return (label, matched_substring) for every recon-useful leak."""
    findings: list[tuple[str, str]] = []
    for pattern, label in FORBIDDEN_PATTERNS:
        m = pattern.search(text)
        if m:
            findings.append((label, m.group(0)))
    m = find_ipv4_leak(text)
    if m:
        findings.append(("IPv4", m.group(0)))
    return findings


def bidirectional_control_threat_errors(
    threats: list[dict], controls: list[dict]
) -> list[str]:
    """Return a list of human-readable messages for broken threat<->control links."""
    control_by_ref = {c["bom-ref"]: c for c in controls}
    errors: list[str] = []
    for t in threats:
        for cref in t.get("mitigations", []) or []:
            ctrl = control_by_ref.get(cref)
            if ctrl is None:
                continue
            # CycloneDX 2.0 defines this relationship on the threat. Enforce
            # symmetry only for legacy artifacts that still emit the retired
            # control-side mitigations extension.
            control_links = ctrl.get("mitigations")
            if control_links is not None and t["bom-ref"] not in control_links:
                errors.append(
                    f"bidirectional link broken: control {cref} does not link back to threat {t['bom-ref']}"
                )
    return errors
