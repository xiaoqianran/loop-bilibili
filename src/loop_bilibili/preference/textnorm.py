"""Text normalization and lightweight tokenization for preference matching."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# Latin / digit technical tokens (c++, c#, node.js, go, rust…)
_LATIN_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.]*", re.IGNORECASE)
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

# fullwidth A-Z a-z 0-9 → halfwidth
_FW_START = 0xFF01
_FW_END = 0xFF5E
_HW_START = 0x21


def normalize_text(text: str) -> str:
    """NFKC + lower + collapse whitespace. Safe for empty input."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    # map remaining fullwidth ascii range
    chars: list[str] = []
    for ch in s:
        o = ord(ch)
        if _FW_START <= o <= _FW_END:
            chars.append(chr(o - _FW_START + _HW_START))
        else:
            chars.append(ch)
    s = "".join(chars).lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def char_ngrams(text: str, n: int = 2) -> frozenset[str]:
    """Character n-grams over CJK + alnum runs (no spaces)."""
    if n < 1 or not text:
        return frozenset()
    compact = re.sub(r"\s+", "", text)
    if len(compact) < n:
        return frozenset({compact}) if compact else frozenset()
    return frozenset(compact[i : i + n] for i in range(len(compact) - n + 1))


@lru_cache(maxsize=4096)
def term_ngrams(term: str, n: int = 2) -> frozenset[str]:
    t = normalize_text(term)
    if not t:
        return frozenset()
    # prefer CJK n-grams; for short latin keep whole token
    if _CJK_RUN.search(t):
        return char_ngrams(t, n)
    return frozenset({t}) if len(t) >= 2 else frozenset()


def latin_tokens(text: str) -> frozenset[str]:
    return frozenset(m.group(0).lower() for m in _LATIN_TOKEN.finditer(text))


def contains_phrase(haystack: str, needle: str) -> bool:
    """Substring match on already-normalized strings."""
    if not needle or not haystack:
        return False
    return needle in haystack


def soft_term_coverage(haystack: str, term: str, *, min_len: int = 2) -> float:
    """
    Soft affinity in [0, 1]: fraction of term char-bigrams found in haystack.

    Catches near-forms (e.g. keyword 大模型 vs title …大型语言模型…).
    Latin terms fall back to whole-token containment (0 or 1).
    """
    term_n = normalize_text(term)
    if len(term_n) < min_len:
        return 0.0
    if not _CJK_RUN.search(term_n):
        # latin / code-like: exact token or substring
        if contains_phrase(haystack, term_n):
            return 1.0
        toks = latin_tokens(haystack)
        return 1.0 if term_n in toks else 0.0

    grams = term_ngrams(term_n, 2)
    if not grams:
        return 1.0 if contains_phrase(haystack, term_n) else 0.0
    hay_grams = char_ngrams(haystack, 2)
    if not hay_grams:
        return 0.0
    hit = sum(1 for g in grams if g in hay_grams)
    return hit / len(grams)
