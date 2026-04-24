"""Scan orchestration — build an :class:`InstallationState` from disk."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from unclog.scan.config import ConfigParseError, load_claude_config
from unclog.scan.filesystem import (
    enumerate_agents,
    enumerate_commands,
    enumerate_skills,
)
from unclog.scan.session import latest_session_path
from unclog.scan.tokens import TiktokenCounter
from unclog.state import InstallationState
from unclog.util.paths import ClaudePaths, claude_paths

_MCP_TOOL_PREFIX = "mcp__"
_MAX_SESSION_RECORDS = 25


def _latest_session(projects_dir: Path) -> Path | None:
    """Return the most recent session JSONL across every project directory."""
    if not projects_dir.is_dir():
        return None
    best: tuple[float, Path] | None = None
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = latest_session_path(project_dir)
        if candidate is None:
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, candidate)
    return best[1] if best else None


def _mcp_session_tokens(session_path: Path | None) -> dict[str, int]:
    """Sum per-server tokens from the first tools array in the session JSONL.

    Returns ``{}`` when no session is available or no tools array is
    present (modern Claude Code JSONLs usually omit schemas). The picker
    falls back to showing ``— tok`` for MCPs that don't appear here.
    """
    if session_path is None:
        return {}
    counter = TiktokenCounter()
    try:
        with session_path.open("r", encoding="utf-8") as f:
            records = []
            for i, line in enumerate(f):
                if i >= _MAX_SESSION_RECORDS:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return {}

    tools: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        candidate = record.get("tools")
        if isinstance(candidate, list) and candidate:
            tools = [t for t in candidate if isinstance(t, dict)]
            break
        message = record.get("message")
        if isinstance(message, dict):
            nested = message.get("tools")
            if isinstance(nested, list) and nested:
                tools = [t for t in nested if isinstance(t, dict)]
                break

    per_server: dict[str, int] = {}
    for tool in tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name.startswith(_MCP_TOOL_PREFIX):
            continue
        remainder = name[len(_MCP_TOOL_PREFIX):]
        server, _, _ = remainder.partition("__")
        if not server:
            continue
        blob = json.dumps(tool, separators=(",", ":"))
        per_server[server] = per_server.get(server, 0) + counter.count(blob)
    return per_server


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

    session_path = _latest_session(paths.projects_dir)

    return InstallationState(
        generated_at=datetime.now(tz=UTC),
        claude_home=paths.home,
        config=config,
        skills=enumerate_skills(paths.skills_dir),
        agents=enumerate_agents(paths.agents_dir),
        commands=enumerate_commands(paths.commands_dir),
        mcp_session_tokens=MappingProxyType(_mcp_session_tokens(session_path)),
        warnings=tuple(warnings),
    )
