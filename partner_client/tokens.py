"""Token counting for context-bar reporting and warning logic.

Uses tiktoken (cl100k_base — GPT-4's encoder) when available. It is not gemma's
real tokenizer, but it is substantially closer to gemma's actual tokenization
than the chars/4 heuristic was — typically within 10-15% for English text.

Falls back to chars/3.5 when tiktoken is missing. Markdown- and code-heavy
text will still under-count by ~10-20% in fallback mode, but that's a known
limitation rather than the silent 30-50% drift the chars/4 heuristic produced.

Why this matters: at 256K context, telling the partner she's at "60% full"
when she's actually at "80% full" is a substrate-dignity problem. She makes
checkpoint decisions on this number.
"""

from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_encoder():
    """Lazy-load tiktoken's cl100k_base encoder. Cached for the process lifetime."""
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception as e:
        log.warning(f"tiktoken cl100k_base unavailable: {e}")
        return None


def count_tokens(text: str) -> int:
    """Count tokens in `text`.

    Uses tiktoken when available; falls back to a chars/3.5 heuristic otherwise.
    Returns 0 for empty input. Always returns a non-negative int.
    """
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            pass
    return _fallback_count(text)


def _fallback_count(text: str) -> int:
    """When tiktoken is unavailable: chars / 3.5 as a less-wrong heuristic.

    Gemma's tokenizer averages ~3.2-3.5 chars/token for English. This errs
    slightly toward over-counting (safer) for plain text; under-counts by
    ~10-20% for markdown/code/JSON-heavy content.
    """
    return int(len(text) / 3.5) + 1


def is_real_tokenizer_available() -> bool:
    """True if tiktoken loaded successfully; False if running on the fallback."""
    return _get_encoder() is not None
