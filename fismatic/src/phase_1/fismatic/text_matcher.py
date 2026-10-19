"""Simple text matching utilities to replace spacy dependency"""

import re
from typing import List, Optional


def normalize_text(text: str) -> str:
    """Normalize text while preserving important formatting"""
    if not text:
        return ""

    # Preserve checkbox symbols
    text = text.replace("\u2612", "☒")
    text = text.replace("\u2611", "☑")
    text = text.replace("\u2610", "☐")

    # Normalize whitespace while preserving newlines
    lines = text.split("\n")
    lines = [line.strip() for line in lines]
    text = "\n".join(line for line in lines if line)

    return text


def is_control_heading(text: str) -> bool:
    """Improved control heading detection"""
    if not text:
        return False

    # Common control heading indicators
    indicators = [
        r"control\s+summary",
        r"implementation\s+status",
        r"control\s+origination",
        r"describe\s+how\s+the\s+control",
        r"control\s+enhancement\s+summary",
    ]

    text = text.lower()
    return any(re.search(pattern, text) for pattern in indicators)


def extract_control_id(text: str) -> Optional[str]:
    """Extract control ID with improved pattern matching"""
    if not text:
        return None

    # Handle control IDs with enhancements
    enhancement_pattern = r"([A-Z]{2}-\d+)\s*\((\d+)\)"
    enh_match = re.search(enhancement_pattern, text)
    if enh_match:
        return f"{enh_match.group(1)} ({enh_match.group(2)})"

    # Handle basic control IDs
    basic_pattern = r"([A-Z]{2}-\d+)"
    basic_match = re.search(basic_pattern, text)
    if basic_match:
        return basic_match.group(1)

    return None


def extract_implementation_status(text: str) -> Optional[str]:
    """Extract implementation status preserving original text"""
    if not text:
        return None

    # Match status patterns but return original text
    text_lower = text.lower()

    # Find the first matching status and return the original text
    if "☒" in text or "x" in text_lower:
        for status in [
            "implemented",
            "partially implemented",
            "planned",
            "alternative implementation",
            "not applicable",
        ]:
            if status in text_lower:
                # Find the original text segment
                start = text_lower.find(status)
                end = start + len(status)
                return text[start:end]

    return None


def extract_control_origination(text: str) -> List[str]:
    """Extract control origination preserving original text"""
    if not text:
        return []

    originations = []
    text_lower = text.lower()

    # Common origination terms to look for
    terms = [
        "service provider corporate",
        "service provider system specific",
        "service provider hybrid",
        "configured by customer",
        "provided by customer",
        "shared",
        "inherited",
    ]

    # Find all checked items and preserve their original text
    for term in terms:
        # Look for checkbox followed by term
        if term in text_lower:
            # Find the original text segment
            start = text_lower.find(term)
            # Include checkbox if present
            if start > 0 and text[start - 1] in "☒Xx":
                start -= 1
            end = start + len(term)
            orig_text = text[start:end].strip()
            if orig_text:
                originations.append(orig_text)

    return originations


def extract_responsible_roles(text: str) -> List[str]:
    """Extract responsible roles preserving original text"""
    if not text:
        return []

    roles = []

    # Split on common delimiters but preserve original text
    delimiters = ["\n", ";", ","]
    current_pos = 0
    text_lower = text.lower()

    # Find role segments
    segments = []
    for delimiter in delimiters:
        if delimiter in text:
            segments.extend(text.split(delimiter))

    if not segments:
        segments = [text]

    # Process each segment
    for segment in segments:
        segment = segment.strip()
        if segment and not any(
            x in segment.lower() for x in ["none", "n/a", "not applicable"]
        ):
            roles.append(segment)

    return roles


def extract_related_controls(text: str) -> List[str]:
    """Extract related control references from text"""
    text = normalize_text(text)
    # Match control IDs in various formats (AC-1, AC-2(3), etc.)
    pattern = r"([a-z]{2}-\d+(?:\s*\(\d+\))?)"
    matches = re.finditer(pattern, text, re.IGNORECASE)
    return [match.group(1).upper() for match in matches]
