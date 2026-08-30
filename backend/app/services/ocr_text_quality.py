"""Conservative quality checks for reusing an existing PDF text layer."""

import re
from collections import Counter
from typing import Tuple


_REASONING_MARKERS = (
    "<think",
    "</think>",
    "wait no",
    "let me think",
    "i need to",
    "we need to",
    "let's analyze",
    "the image shows",
    "the document shows",
)


def assess_pdf_text_quality(text: str, page_count: int = 1) -> Tuple[bool, str]:
    """Return whether an existing PDF text layer is safe to reuse.

    The check deliberately accepts ordinary OCR errors.  It only rejects text
    that is missing, implausibly small, dominated by non-text characters, or
    looks like an LLM reasoning/repetition loop.
    """
    normalized = (text or "").strip()
    if not normalized:
        return False, "kein auslesbarer Text"

    # Keep the historic lower bound, but scale it very gently for documents
    # with many pages so one stray footer cannot make a whole PDF look valid.
    minimum_chars = max(50, min(max(page_count, 1), 10) * 25)
    if len(normalized) <= minimum_chars:
        return False, f"zu wenig Text ({len(normalized)} Zeichen)"

    lowered = normalized.lower()
    marker_hits = sum(lowered.count(marker) for marker in _REASONING_MARKERS)
    if "<think" in lowered or marker_hits >= 3:
        return False, "Modell-Denktext erkannt"

    non_space = [char for char in normalized if not char.isspace()]
    readable = sum(char.isalnum() or char in ".,;:!?€$%&@+-/()[]" for char in non_space)
    if non_space and readable / len(non_space) < 0.65:
        return False, "zu viele unlesbare Sonderzeichen"

    words = re.findall(r"[\wÄÖÜäöüß]{2,}", lowered, flags=re.UNICODE)
    if len(words) < 8:
        return False, f"zu wenige erkennbare Wörter ({len(words)})"

    # Long model loops have very low vocabulary diversity even when individual
    # lines vary slightly (for example repeated "Wait, no ..." reasoning).
    if len(words) >= 100 and len(set(words)) / len(words) < 0.12:
        return False, "auffällige Wortwiederholungen"

    meaningful_lines = [
        re.sub(r"\s+", " ", line.strip().lower())
        for line in normalized.splitlines()
        if len(line.strip()) >= 8
    ]
    if len(meaningful_lines) >= 10:
        most_common_line_count = Counter(meaningful_lines).most_common(1)[0][1]
        if most_common_line_count / len(meaningful_lines) >= 0.45:
            return False, "auffällige Zeilenwiederholungen"

    return True, f"plausibler Text ({len(normalized)} Zeichen)"
