from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest
from rich.console import Console

from unclog.scan.config import Settings
from unclog.scan.stats import ActivityIndex
from unclog.state import GlobalScope, InstallationState
from unclog.ui.welcome import (
    first_run_tip_line,
    is_first_run,
    mark_first_run_seen,
    welcome_panel,
)
from unclog.util.paths import ClaudePaths


@pytest.fixture
def state(tmp_path: Path) -> InstallationState:
    home = tmp_path / ".claude"
    home.mkdir()
    return InstallationState(
        generated_at=datetime(2026, 4, 19, 12, 0, tzinfo=UTC),
        claude_home=home,
        global_scope=GlobalScope(
            claude_home=home,
            config=None,
            settings=Settings(),
            claude_md_bytes=0,
            claude_md_text="",
            claude_local_md_bytes=0,
            claude_local_md_text="",
            skills=(),
            agents=(),
            latest_session=None,
            activity=ActivityIndex(),
            mcp_probes=MappingProxyType({}),
            mcp_invocations=MappingProxyType({}),
        ),
        project_scopes=(),
        warnings=(),
    )


def _render(panel) -> str:
    console = Console(width=120, record=True, color_system=None)
    console.print(panel)
    return console.export_text()


def test_default_panel_is_slim(state: InstallationState) -> None:
    """Default mode renders only the title + tagline — no scan-meta, no tips."""
    text = _render(welcome_panel(state, verbose=False))
    assert "unclog" in text
    assert "local-only audit" in text
    assert "scanning" not in text
    assert "projects" not in text
    assert "tips:" not in text
    assert "nothing is mutated" not in text


def test_verbose_panel_restores_full_chrome(state: InstallationState) -> None:
    """Verbose mode emits scan-meta + persistent tips list inside the panel."""
    text = _render(welcome_panel(state, verbose=True))
    assert "scanning" in text
    assert "projects" in text
    assert "session" in text
    assert "tips:" in text
    assert "nothing is mutated" in text


def test_first_run_tip_line_mentions_safety_and_verbose() -> None:
    text = _render(first_run_tip_line())
    assert "first run" in text
    assert "nothing is mutated" in text
    assert "snapshots" in text
    assert "--verbose" in text


def test_first_run_marker_starts_unwritten_then_flips(tmp_path: Path) -> None:
    paths = ClaudePaths(home=tmp_path / ".claude")
    assert is_first_run(paths)
    mark_first_run_seen(paths)
    assert not is_first_run(paths)
    # Idempotent — calling again is a no-op, not an error.
    mark_first_run_seen(paths)
    assert not is_first_run(paths)


def test_mark_first_run_seen_swallows_permission_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort marker write must not crash the run."""
    paths = ClaudePaths(home=tmp_path / ".claude")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("read-only fs")

    monkeypatch.setattr(Path, "mkdir", _boom)
    # Should return cleanly; subsequent runs will keep showing the tip.
    mark_first_run_seen(paths)
    assert is_first_run(paths)
