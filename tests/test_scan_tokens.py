from __future__ import annotations

from unclog.scan.tokens import DEFAULT_ENCODING, TiktokenCounter


def test_empty_string_is_zero_tokens() -> None:
    assert TiktokenCounter().count("") == 0


def test_non_empty_string_has_positive_count() -> None:
    assert TiktokenCounter().count("hello world") > 0


def test_longer_text_has_more_tokens() -> None:
    counter = TiktokenCounter()
    assert counter.count("hello " * 100) > counter.count("hello")


def test_counter_is_deterministic() -> None:
    counter = TiktokenCounter()
    text = "The quick brown fox jumps over the lazy dog."
    assert counter.count(text) == counter.count(text)


def test_counter_tolerates_special_token_literals() -> None:
    # Must not raise on content containing tiktoken special markers.
    TiktokenCounter().count("this doc mentions <|endoftext|> literally")


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
