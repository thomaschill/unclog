"""Scan orchestration.

Builds an :class:`InstallationState` from the Claude Code installation
at a given home directory. Intentionally IO-heavy; all other layers
consume the returned state as a pure value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from unclog.scan.config import ConfigParseError, load_claude_config, load_settings
from unclog.scan.filesystem import (
    enumerate_agents,
    enumerate_commands,
    enumerate_skills,
    load_installed_plugins,
)
from unclog.state import GlobalScope, InstallationState
from unclog.util.paths import ClaudePaths, claude_paths


def _file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def scan_global(paths: ClaudePaths, warnings: list[str]) -> GlobalScope:
    """Scan everything under a single Claude home directory."""
    try:
        config = load_claude_config(paths.config_json)
    except ConfigParseError as exc:
        warnings.append(str(exc))
        config = None

    try:
        settings = load_settings(paths.settings_json)
    except ConfigParseError as exc:
        warnings.append(str(exc))
        settings = None

    return GlobalScope(
        claude_home=paths.home,
        config=config,
        settings=settings,
        claude_md_bytes=_file_bytes(paths.claude_md),
        claude_local_md_bytes=_file_bytes(paths.claude_local_md),
        skills=enumerate_skills(paths.skills_dir),
        agents=enumerate_agents(paths.agents_dir),
        commands=enumerate_commands(paths.commands_dir),
        installed_plugins=load_installed_plugins(paths.installed_plugins_json),
    )


def run_scan() -> InstallationState:
    """Run a full scan using the environment's Claude home."""
    paths = claude_paths()
    warnings: list[str] = []
    if not paths.home.exists():
        warnings.append(f"Claude Code home does not exist: {paths.home}")
    scope = scan_global(paths, warnings)
    return InstallationState(
        generated_at=datetime.now(tz=UTC),
        claude_home=paths.home,
        global_scope=scope,
        warnings=tuple(warnings),
    )
