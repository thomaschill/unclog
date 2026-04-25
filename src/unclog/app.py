"""Scan orchestration — build an :class:`InstallationState` from disk.

Deliberately fast: enumerates the filesystem, parses ``~/.claude.json``,
peeks at the latest session JSONL for any MCP token attribution, and
returns. The expensive 30-day MCP-invocation walk lives in
:mod:`unclog.scan.session` and is launched separately on a background
thread by the CLI so the picker can mount before it finishes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

from unclog.scan.config import ConfigParseError, load_claude_config
from unclog.scan.filesystem import (
    enumerate_agents,
    enumerate_commands,
    enumerate_skills,
)
from unclog.scan.session import latest_session_across_projects, mcp_session_tokens
from unclog.state import InstallationState
from unclog.util.paths import ClaudePaths, claude_paths


def run_scan() -> InstallationState:
    """Scan the Claude Code installation at the environment's home."""
    paths = claude_paths()
    warnings: list[str] = []
    if not paths.home.exists():
        warnings.append(f"Claude Code home does not exist: {paths.home}")
    return _scan(paths, warnings)


def _scan(paths: ClaudePaths, warnings: list[str]) -> InstallationState:
    try:
        config = load_claude_config(paths.config_json)
    except ConfigParseError as exc:
        warnings.append(str(exc))
        config = None

    session_path = latest_session_across_projects(paths.projects_dir)

    return InstallationState(
        generated_at=datetime.now(tz=UTC),
        claude_home=paths.home,
        config=config,
        skills=enumerate_skills(paths.skills_dir),
        agents=enumerate_agents(paths.agents_dir),
        commands=enumerate_commands(paths.commands_dir),
        mcp_session_tokens=MappingProxyType(mcp_session_tokens(session_path)),
        # mcp_invocation_counts is left empty here — the heavy 30-day
        # walk is launched on a background thread by cli.py so the
        # picker can render before it finishes.
        warnings=tuple(warnings),
    )
