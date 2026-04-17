from __future__ import annotations

import pytest

from unclog.scan.tokens import (
    DEFAULT_ENCODING,
    TiktokenCounter,
    count_tokens,
    reset_default_counter,
)


@pytest.fixture(autouse=True)
def _reset_default_counter() -> None:
    reset_default_counter()


def test_empty_string_is_zero_tokens() -> None:
    assert count_tokens("") == 0


def test_non_empty_string_has_positive_count() -> None:
    assert count_tokens("hello world") > 0


def test_longer_text_has_more_tokens() -> None:
    short = count_tokens("hello")
    long = count_tokens("hello " * 100)
    assert long > short


def test_counter_is_deterministic() -> None:
    text = "The quick brown fox jumps over the lazy dog."
    assert count_tokens(text) == count_tokens(text)


def test_counter_tolerates_special_token_literals() -> None:
    # Must not raise on content containing tiktoken special markers.
    count_tokens("this doc mentions <|endoftext|> literally")


def test_instance_cache_returns_same_count_on_repeat() -> None:
    counter = TiktokenCounter()
    first = counter.count("cache me")
    second = counter.count("cache me")
    assert first == second
    assert b"cache me" not in counter._cache  # keys are hashed, not raw


def test_default_encoding_constant() -> None:
    # Guards against accidental tokenizer swaps that would shift baselines
    # for every existing user.
    assert DEFAULT_ENCODING == "cl100k_base"
