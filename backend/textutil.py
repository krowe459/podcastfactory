"""Small text helpers shared across modules."""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z0-9$€£%][\w'’.,%$€£-]*")


def count_words(text: str) -> int:
    """Count spoken words the way a reader would: whitespace-separated tokens with content."""
    return sum(1 for tok in text.split() if _WORD.search(tok))
