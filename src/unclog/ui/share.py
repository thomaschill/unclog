"""One-line shareable stat rendered after a successful apply.

Gives the user a short, copy-pastable sentence they can drop into a
tweet, Slack message, or PR description — the kind of line that makes
them want to tell someone the tool exists. Intentionally conservative:
we only show it when there's a non-trivial delta to brag about.

Kept separate from :mod:`unclog.ui.interactive` so the share line stays
testable in isolation and so future tweaks (different copy, multiple
variants) don't touch the apply flow.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from unclog.ui.theme import ACCENT, DIM

SHARE_REPO_URL = "github.com/thomaschill/unclog"


def format_share_line(*, baseline_tokens: int, tokens_saved: int) -> str | None:
    """Return the share-worthy one-liner, or ``None`` if not worth showing.

    We skip rendering when:

    - baseline was zero or unknown (can't compute a percent),
    - nothing was saved,
    - savings round below 1% *and* are under 200 tokens (too small to
      feel like a win).
    """
    if baseline_tokens <= 0 or tokens_saved <= 0:
        return None
    percent = tokens_saved / baseline_tokens * 100
    if percent < 1 and tokens_saved < 200:
        return None
    pct_label = _format_percent(percent)
    return (
        f"Just unclogged {tokens_saved:,} tokens from my Claude Code baseline "
        f"— {pct_label} leaner per turn. {SHARE_REPO_URL}"
    )


def render_share_stat(
    console: Console,
    *,
    baseline_tokens: int,
    tokens_saved: int,
) -> bool:
    """Print the share line to ``console``. Returns True if rendered."""
    line = format_share_line(
        baseline_tokens=baseline_tokens, tokens_saved=tokens_saved
    )
    if line is None:
        return False
    body = Text()
    body.append(line, style=ACCENT)
    console.print("")
    console.print(
        Panel(
            body,
            title=Text("share the clear-out", style=DIM),
            title_align="left",
            border_style=DIM,
            padding=(0, 1),
        )
    )
    return True


def _format_percent(percent: float) -> str:
    if percent >= 10:
        return f"{round(percent)}%"
    if percent >= 1:
        return f"{percent:.1f}%"
    return "<1%"


__all__ = ["SHARE_REPO_URL", "format_share_line", "render_share_stat"]
