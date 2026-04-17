from __future__ import annotations

import io

from rich.console import Console

from unclog.ui.share import SHARE_REPO_URL, format_share_line, render_share_stat


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
    assert SHARE_REPO_URL in line


def test_share_line_suppressed_when_baseline_zero() -> None:
    assert format_share_line(baseline_tokens=0, tokens_saved=500) is None


def test_share_line_suppressed_when_savings_zero() -> None:
    assert format_share_line(baseline_tokens=42_000, tokens_saved=0) is None


def test_share_line_suppressed_when_delta_is_trivial() -> None:
    # Half-a-percent + under 200 tokens: not worth a brag.
    assert format_share_line(baseline_tokens=100_000, tokens_saved=50) is None


def test_share_line_shown_when_small_percent_but_meaningful_absolute() -> None:
    # 0.5% of 100_000 = 500 tokens. Absolute matters; surface the brag.
    line = format_share_line(baseline_tokens=100_000, tokens_saved=500)
    assert line is not None
    assert "500" in line


def test_share_line_rounds_large_percents_to_integer() -> None:
    line = format_share_line(baseline_tokens=10_000, tokens_saved=3_750)
    assert line is not None
    assert "38%" in line  # 37.5% rounds to 38%


def test_render_share_stat_prints_panel_with_url() -> None:
    console, buf = _console()
    printed = render_share_stat(
        console, baseline_tokens=42_000, tokens_saved=12_000
    )
    assert printed is True
    output = buf.getvalue()
    assert SHARE_REPO_URL in output
    assert "12,000" in output


def test_render_share_stat_returns_false_when_not_worth_showing() -> None:
    console, buf = _console()
    printed = render_share_stat(
        console, baseline_tokens=0, tokens_saved=500
    )
    assert printed is False
    assert buf.getvalue() == ""
