from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from unclog import __version__
from unclog.state import InstallationState
from unclog.ui.welcome import welcome_panel


def _state(tmp_path: Path) -> InstallationState:
    home = tmp_path / ".claude"
    home.mkdir()
    return InstallationState(
        generated_at=datetime(2026, 4, 19, 12, 0, tzinfo=UTC),
        claude_home=home,
        config=None,
        settings=None,
    )


def _render(panel: object) -> str:
    console = Console(width=120, record=True, color_system=None)
    console.print(panel)
    return console.export_text()


def test_welcome_panel_shows_title_tagline_and_version(tmp_path: Path) -> None:
    text = _render(welcome_panel(_state(tmp_path)))
    assert "unclog" in text
    assert "local-only audit" in text
    assert __version__ in text


def test_welcome_panel_has_no_verbose_chrome(tmp_path: Path) -> None:
    """0.2 dropped scan-meta, tips list, and first-run markers entirely."""
    text = _render(welcome_panel(_state(tmp_path)))
    assert "scanning" not in text
    assert "projects" not in text
    assert "tips:" not in text
    assert "snapshots" not in text
