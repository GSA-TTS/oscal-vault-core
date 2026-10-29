"""Redact URLs, e-mail addresses and IPv4 addresses from vault prose before it leaves a vault.

IPv4 matching requires every octet to be 0-255 and ignores dotted numbers that follow
"section", "para." or the section sign, so citations such as "section 5.1.1.1" survive.
"""

from __future__ import annotations

import re

URL_RE = re.compile(r"https?://[^\s\"'>,)\]]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IPV4_RE = re.compile(
    r"(?<![\w.])"
    r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)){3}"
    r"(?:/\d{1,2})?"
    r"(?![\w.])"
)
SECTION_CONTEXT_RE = re.compile(r"(?i)(?:section|sections|§|para\.?|paragraph)\s+$")

REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (URL_RE, "<REDACTED_URL>"),
    (EMAIL_RE, "<REDACTED_EMAIL>"),
]


def redact_ipv4(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        preceding = text[max(0, match.start() - 40) : match.start()]
        if SECTION_CONTEXT_RE.search(preceding):
            return match.group(0)
        return "<REDACTED_IP>"

    return IPV4_RE.sub(replace, text)


def redact(text: str | None, *, internal: bool = False) -> str:
    """Redact sensitive tokens. ``internal=True`` keeps URLs and e-mails (build-only output).

    IP addresses are redacted either way.
    """
    if not text:
        return ""
    if internal:
        return redact_ipv4(text)
    out = text
    for pattern, token in REDACTIONS:
        out = pattern.sub(token, out)
    return redact_ipv4(out)
