"""Immutable snapshot of a scanned Claude Code installation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from unclog.scan.config import ClaudeConfig
from unclog.scan.filesystem import Agent, Command, Skill


@dataclass(frozen=True)
class InstallationState:
    """Everything unclog sees about one Claude Code installation.

    Holds what the picker needs: agents, skills, slash commands, and the
    MCP servers declared in ``~/.claude.json``.
    """

    generated_at: datetime
    claude_home: Path
    config: ClaudeConfig | None
    skills: tuple[Skill, ...] = ()
    agents: tuple[Agent, ...] = ()
    commands: tuple[Command, ...] = ()
    # Per-MCP tokens derived from the latest session's tools array, when
    # present. Modern session JSONLs usually omit schemas, so this is
    # often empty; the picker then renders ``— tok`` for that server.
    mcp_session_tokens: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)
