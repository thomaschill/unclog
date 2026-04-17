from __future__ import annotations

import io

from rich.console import Console

from unclog.ui.spinner import (
    DONE_MESSAGE,
    FRAMES,
    INITIAL_MESSAGE,
    scan_spinner,
)


def test_frame_set_matches_spec() -> None:
    # Spec §11.7 specifies this exact seven-frame sequence.
    assert FRAMES == (
        "·   ",
        "··  ",
        "·•• ",
        "••• ",
        " ••·",
        "  •·",
        "   ·",
    )


def test_initial_and_done_messages_are_strings() -> None:
    assert isinstance(INITIAL_MESSAGE, str) and INITIAL_MESSAGE
    assert isinstance(DONE_MESSAGE, str) and DONE_MESSAGE


def test_static_phase_prints_dim_bullets_when_not_animating() -> None:
    buf = io.StringIO()
    console = Console(
        file=buf, force_terminal=False, no_color=True, record=False, width=80
    )
    with scan_spinner(console, animate=False) as phase:
        phase("Reading config…")
        phase("Reading config…")  # duplicate, should be suppressed
        phase("Measuring CLAUDE.md composition…")
    output = buf.getvalue()
    assert "Reading config" in output
    assert "Measuring CLAUDE.md composition" in output
    # Duplicate suppressed — only one "Reading config" line appears.
    assert output.count("Reading config") == 1


def test_scan_spinner_yields_callable_even_when_animating() -> None:
    # We don't actually want to run a live render inside pytest (it
    # would need a TTY). What we can verify: entering the context with
    # animate=True still yields a callable that doesn't raise when
    # invoked with a message. Rich swallows output cleanly when the
    # console is redirected.
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=80, record=False)
    with scan_spinner(console, animate=True) as phase:
        assert callable(phase)
        phase("Phase A")
        phase("Phase B")
    # Transient=True clears the live render cleanly — no persistent
    # "Scan complete" line; the hero that follows is the done-signal.
    assert DONE_MESSAGE not in buf.getvalue()
