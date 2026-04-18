"""Post-apply baseline countdown animation (spec §11.8).

After a successful apply, unclog animates the baseline from the pre-apply
number to the post-apply number over ~400 ms via ``rich.live``. The
three tick values are ``before → midpoint → after`` (matching the spec's
``42,180 → 41,993 → 41,589`` illustration).

Static fallback: when animation is disabled (``--no-animation``,
non-TTY), the countdown prints a single line showing both numbers with
an arrow between them.
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.live import Live
from rich.text import Text

from unclog.ui.theme import ACCENT, DIM

TOTAL_DURATION_S = 0.4
STEPS = 3  # before → midpoint → after
REFRESH_PER_SECOND = 30


def _render_line(value: int) -> Text:
    line = Text()
    line.append("Baseline: ", style="bold")
    line.append(f"{value:,}", style=f"bold {ACCENT}")
    return line


def _render_static(before: int, after: int) -> Text:
    line = Text()
    line.append("Baseline: ", style="bold")
    line.append(f"{before:,}", style=DIM)
    line.append(" → ")
    line.append(f"{after:,}", style=f"bold {ACCENT}")
    return line


def animate_countdown(
    console: Console,
    *,
    before: int,
    after: int,
    animate: bool,
) -> None:
    """Render the baseline transition from ``before`` to ``after``.

    When there's no delta, we still print the static line so users have
    context for the final number. When ``after`` would go negative
    (shouldn't happen, but defensive), we clamp to zero.
    """
    after = max(0, after)
    if not animate or before == after:
        console.print(_render_static(before, after))
        return

    midpoint = before - (before - after) // 2
    values = [before, midpoint, after]
    step_delay = TOTAL_DURATION_S / len(values)

    with Live(
        _render_line(before),
        console=console,
        refresh_per_second=REFRESH_PER_SECOND,
        transient=False,
    ) as live:
        for value in values:
            live.update(_render_line(value))
            time.sleep(step_delay)


__all__ = ["animate_countdown"]
