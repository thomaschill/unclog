from __future__ import annotations

import io

from rich.console import Console

from unclog.ui.share import format_share_line, render_share_stat


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf, force_terminal=False, no_color=True, width=80, record=False
    )
    return console, buf


def test_share_line_includes_absolute_tokens_and_percent() -> None:
    line = format_share_line(baseline_tokens=42_000, tokens_saved=12_600)
    assert line is not None
    assert "12,600" in line
    assert "30%" in line


def test_share_line_has_no_marketing_copy() -> None:
    line = format_share_line(baseline_tokens=42_000, tokens_saved=12_600)
    assert line is not None
    lowered = line.lower()
    # The stat is deliberately quiet — no brag framing, no URL,
    # no explicit "share" prompt, no mention of Claude Code.
    for banned in ("just unclogged", "claude code", "share", "github.com", "leaner per turn"):
        assert banned not in lowered, f"unexpected marketing token: {banned!r}"


def test_share_line_suppressed_when_baseline_zero() -> None:
    assert format_share_line(baseline_tokens=0, tokens_saved=500) is None


def test_share_line_suppressed_when_savings_zero() -> None:
    assert format_share_line(baseline_tokens=42_000, tokens_saved=0) is None


def test_share_line_suppressed_when_delta_is_trivial() -> None:
    # Half-a-percent + under 200 tokens: too small to bother showing.
    assert format_share_line(baseline_tokens=100_000, tokens_saved=50) is None


def test_share_line_shown_when_small_percent_but_meaningful_absolute() -> None:
    # 0.5% of 100_000 = 500 tokens. Absolute matters; surface the line.
    line = format_share_line(baseline_tokens=100_000, tokens_saved=500)
    assert line is not None
    assert "500" in line


def test_share_line_rounds_large_percents_to_integer() -> None:
    line = format_share_line(baseline_tokens=10_000, tokens_saved=3_750)
    assert line is not None
    assert "38%" in line  # 37.5% rounds to 38%


def test_render_share_stat_prints_line_with_numbers() -> None:
    console, buf = _console()
    printed = render_share_stat(
        console, baseline_tokens=42_000, tokens_saved=12_000
    )
    assert printed is True
    output = buf.getvalue()
    assert "12,000" in output
    assert "leaner" in output
    # No panel borders, no URL in the rendered output.
    assert "github.com" not in output
    assert "share the clear-out" not in output


def test_render_share_stat_returns_false_when_not_worth_showing() -> None:
    console, buf = _console()
    printed = render_share_stat(
        console, baseline_tokens=0, tokens_saved=500
    )
    assert printed is False
    assert buf.getvalue() == ""
