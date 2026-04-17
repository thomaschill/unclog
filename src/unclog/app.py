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
from unclog.scan.session import (
    SessionSystemBlock,
    latest_session_path,
    load_session_system_block,
)
from unclog.scan.stats import load_activity_index
from unclog.scan.tokens import TiktokenCounter
from unclog.state import GlobalScope, InstallationState
from unclog.util.paths import ClaudePaths, claude_paths


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _find_latest_session_across_projects(
    projects_dir: Path,
    counter: TiktokenCounter,
) -> SessionSystemBlock | None:
    """Return the most recent session block across every known project dir.

    Claude Code keeps one session-history directory per absolute project
    path. When unclog runs outside a project, we still want ground-truth
    MCP / system-prompt sizes, so we pick the single most recent JSONL
    across all of them.
    """
    if not projects_dir.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        latest = latest_session_path(project_dir)
        if latest is None:
            continue
        try:
            candidates.append((latest.stat().st_mtime, latest))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return load_session_system_block(candidates[0][1], counter)


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

    counter = TiktokenCounter()
    claude_md_text = _read_text(paths.claude_md)
    claude_local_md_text = _read_text(paths.claude_local_md)

    return GlobalScope(
        claude_home=paths.home,
        config=config,
        settings=settings,
        claude_md_bytes=len(claude_md_text.encode("utf-8")),
        claude_md_text=claude_md_text,
        claude_local_md_bytes=len(claude_local_md_text.encode("utf-8")),
        claude_local_md_text=claude_local_md_text,
        skills=enumerate_skills(paths.skills_dir),
        agents=enumerate_agents(paths.agents_dir),
        commands=enumerate_commands(paths.commands_dir),
        installed_plugins=load_installed_plugins(paths.installed_plugins_json),
        latest_session=_find_latest_session_across_projects(paths.projects_dir, counter),
        activity=load_activity_index(paths.stats_cache_json, paths.history_jsonl),
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
