"""One-line after-apply stat.

Prints a single understated line showing how much leaner the baseline
is after applying fixes. Deliberately quiet — no panel, no brag-copy,
no URL. Just the numbers. If the user wants to share, the numbers are
already pasteable.

Kept separate from :mod:`unclog.ui.interactive` so the line stays
testable in isolation.
"""

from __future__ import annotations

from rich.console import Console

from unclog.ui.theme import ACCENT, DIM


def format_share_line(*, baseline_tokens: int, tokens_saved: int) -> str | None:
    """Return the one-liner, or ``None`` if not worth showing.

    We skip rendering when:

    - baseline was zero or unknown (can't compute a percent),
    - nothing was saved,
    - savings round below 1% *and* are under 200 tokens (too small to
      be worth the extra line).
    """
    if baseline_tokens <= 0 or tokens_saved <= 0:
        return None
    percent = tokens_saved / baseline_tokens * 100
    if percent < 1 and tokens_saved < 200:
        return None
    pct_label = _format_percent(percent)
    return f"-{tokens_saved:,} tokens  ·  {pct_label} leaner baseline"


def render_share_stat(
    console: Console,
    *,
    baseline_tokens: int,
    tokens_saved: int,
) -> bool:
    """Print the stat line to ``console``. Returns True if rendered."""
    line = format_share_line(
        baseline_tokens=baseline_tokens, tokens_saved=tokens_saved
    )
    if line is None:
        return False
    # Break the line so the numeric delta stands out but the framing
    # stays quiet — no shouty panel, no share-me copy.
    tokens_part, _, percent_part = line.partition("  ·  ")
    console.print("")
    console.print(f"[{ACCENT}]{tokens_part}[/{ACCENT}]  [{DIM}]·  {percent_part}[/{DIM}]")
    return True


def _format_percent(percent: float) -> str:
    if percent >= 10:
        return f"{round(percent)}%"
    if percent >= 1:
        return f"{percent:.1f}%"
    return "<1%"


__all__ = ["format_share_line", "render_share_stat"]
