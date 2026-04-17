from __future__ import annotations

import io

from rich.console import Console

from unclog.ui.countdown import animate_countdown


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf, force_terminal=False, no_color=True, width=80, record=False
    )
    return console, buf


def test_static_path_emits_before_and_after() -> None:
    console, buf = _make_console()
    animate_countdown(console, before=42_180, after=41_589, animate=False)
    out = buf.getvalue()
    assert "42,180" in out
    assert "41,589" in out
    assert "Baseline" in out


def test_zero_delta_uses_static_path() -> None:
    # No arithmetic delta → nothing to animate, but we still print a
    # status line for the user.
    console, buf = _make_console()
    animate_countdown(console, before=30_000, after=30_000, animate=False)
    assert "30,000" in buf.getvalue()


def test_animated_path_terminates_with_after_value() -> None:
    # With animate=True the function drives rich.live — we only check
    # the final output contains the after number. Live transient=False
    # leaves the last frame on screen.
    console, buf = _make_console()
    animate_countdown(console, before=42_180, after=41_000, animate=True)
    assert "41,000" in buf.getvalue()


def test_after_cannot_go_negative() -> None:
    console, buf = _make_console()
    animate_countdown(console, before=100, after=-999, animate=False)
    out = buf.getvalue()
    assert "0" in out
    assert "-" not in out.replace("→", "")  # no stray minus sign (arrow aside)
