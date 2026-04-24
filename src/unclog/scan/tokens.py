"""Token counting.

Uses tiktoken's ``cl100k_base`` encoding as a close approximation to
Claude's tokenizer — Anthropic doesn't publish theirs for Claude 3+.
Over-counts natural language by a few percent and under-counts
structured JSON; close enough to drive the baseline the hero shows and
the per-item savings the picker surfaces.

A per-instance content-hash cache avoids re-tokenizing identical
strings within one scan.
"""

from __future__ import annotations

import hashlib

import tiktoken

DEFAULT_ENCODING = "cl100k_base"


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
