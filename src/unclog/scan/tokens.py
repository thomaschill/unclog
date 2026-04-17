"""Token counting primitives.

Uses tiktoken's ``cl100k_base`` encoding as a close approximation to
Claude's tokenizer — Anthropic does not publish theirs for Claude 3+.
Over-counts natural language by a few percent and under-counts
structured JSON; close enough to drive tier thresholds and the
share-based decisions the hero/treemap expose.

The ``--accurate`` mode (landing after M2) will route through
``anthropic.Client.beta.messages.count_tokens`` for exact counts.

A content-hash cache avoids re-tokenizing identical strings within a
scan (the same CLAUDE.md is read once; the same skill description may
be encountered per-scope during ``--all-projects``).
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Protocol

import tiktoken

DEFAULT_ENCODING = "cl100k_base"


class TokenCounter(Protocol):
    """Anything that can turn text into a token count."""

    def count(self, text: str) -> int: ...


class TiktokenCounter:
    """BPE token counter with an in-process content-hash cache."""

    def __init__(self, encoding_name: str = DEFAULT_ENCODING) -> None:
        self._encoding = tiktoken.get_encoding(encoding_name)
        self._cache: dict[str, int] = {}

    def count(self, text: str) -> int:
        if not text:
            return 0
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        # disallowed_special=() lets us tokenize arbitrary content
        # (e.g. user docs that happen to contain "<|endoftext|>").
        total = len(self._encoding.encode(text, disallowed_special=()))
        self._cache[key] = total
        return total


@lru_cache(maxsize=1)
def _default_counter() -> TiktokenCounter:
    return TiktokenCounter()


def count_tokens(text: str) -> int:
    """Count tokens with the process-wide default counter."""
    return _default_counter().count(text)


def reset_default_counter() -> None:
    """Discard the cached default counter (test helper)."""
    _default_counter.cache_clear()
