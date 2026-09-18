"""Shared fallback only; server-rendered token counts remain authoritative."""
import math


def estimate_tokens(text: str) -> int:
    """Conservative UTF-8 byte estimate plus small message framing allowance.

    Unlike character/4, this does not count a Chinese character like an ASCII
    letter. This is not a tokenizer and must never be reported as measured usage.
    """
    return math.ceil(len(text.encode('utf-8')) / 2) + 4 if text else 0
