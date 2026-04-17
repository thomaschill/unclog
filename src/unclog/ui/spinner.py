"""Custom flow spinner for the scan phase (spec §11.7, §11.8).

Frame set evokes debris clearing a pipe — drawn from the spec:

    "·   ", "··  ", "·•• ", "••• ", " ••·", "  •·", "   ·"

The spinner is paired with a live status line (``Reading config…``,
``Measuring CLAUDE.md composition…``) that updates as the scan progresses.
When the scan ends, the spinner is held for ~150 ms before snapping to a
green checkmark so the rhythm feels intentional rather than abrupt.

The spec only permits two animated elements in the UI (§11.8). This is
one of them; :mod:`unclog.ui.countdown` is the other.

Usage:

    with scan_spinner(console, animate=True) as phase:
        phase("Reading config…")
        ...
        phase("Parsing last session per project…")

If ``animate`` is False (``--no-animation``, ``--report``, non-TTY),
the context manager still yields a working ``phase`` callable but it
prints dim status lines instead of driving Rich Live.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from rich.console import Console, RenderableType
from rich.live import Live
from rich.text import Text

from unclog.ui.theme import ACCENT, DIM, SEVERITY_LEAN

FRAMES: tuple[str, ...] = (
    "·   ",
    "··  ",
    "·•• ",
    "••• ",
    " ••·",
    "  •·",
    "   ·",
)

FRAMES_PER_SECOND = 10
HOLD_AFTER_COMPLETE_S = 0.15
INITIAL_MESSAGE = "Scanning your Claude Code installation…"
DONE_MESSAGE = "Scan complete"


class _SpinnerRenderable:
    """Stateful Rich renderable that advances a frame based on wall time."""

    def __init__(self, message: str) -> None:
        self.message = message
        self._started_at = time.monotonic()

    def __rich__(self) -> RenderableType:
        elapsed = time.monotonic() - self._started_at
        idx = int(elapsed * FRAMES_PER_SECOND) % len(FRAMES)
        glyph = FRAMES[idx]
        line = Text()
        line.append(glyph, style=ACCENT)
        line.append("  ")
        line.append(self.message, style=DIM)
        return line


@contextmanager
def scan_spinner(
    console: Console, *, animate: bool
) -> Iterator[Callable[[str], None]]:
    """Context manager that yields a phase-setter callable.

    When ``animate`` is True the spinner runs inside ``rich.live.Live``
    and the status line mutates in place. When False, each phase call
    prints a dim bullet line so CI logs still show scan progress.
    """
    if not animate:
        yield _static_phase(console)
        return

    spinner = _SpinnerRenderable(INITIAL_MESSAGE)
    with Live(
        spinner,
        console=console,
        refresh_per_second=FRAMES_PER_SECOND,
        transient=True,
    ):

        def set_phase(message: str) -> None:
            spinner.message = message

        yield set_phase
        time.sleep(HOLD_AFTER_COMPLETE_S)

    console.print(
        Text.assemble(
            ("✓ ", SEVERITY_LEAN),
            (DONE_MESSAGE, DIM),
        )
    )


def _static_phase(console: Console) -> Callable[[str], None]:
    """Phase callback used when animation is disabled.

    Emits at most one line per phase so non-TTY logs stay scannable.
    Lines are dimmed to match the animated status styling.
    """
    last: list[str] = []

    def phase(message: str) -> None:
        if last and last[-1] == message:
            return
        last.append(message)
        console.print(Text.assemble(("· ", ACCENT), (message, DIM)))

    return phase


__all__ = [
    "DONE_MESSAGE",
    "FRAMES",
    "INITIAL_MESSAGE",
    "scan_spinner",
]
