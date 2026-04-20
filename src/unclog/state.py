"""Immutable snapshot of a scanned Claude Code installation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from unclog.scan.config import ClaudeConfig, Settings
from unclog.scan.filesystem import Agent, InstalledPlugin, PluginContent, Skill


@dataclass(frozen=True)
class InstallationState:
    """Everything unclog sees about one Claude Code installation.

    Only holds what the picker needs: agents, skills, MCP servers, and
    the plugin content we read for totals. CLAUDE.md, hooks, memory,
    stats, and session system blocks were removed in 0.2.
    """

    generated_at: datetime
    claude_home: Path
    config: ClaudeConfig | None
    settings: Settings | None
    skills: tuple[Skill, ...] = ()
    agents: tuple[Agent, ...] = ()
    installed_plugins: tuple[InstalledPlugin, ...] = ()
    plugin_content: tuple[PluginContent, ...] = ()
    # Per-MCP tokens derived from the latest session's tools array, when
    # present. Modern session JSONLs usually omit schemas, so this is
    # often empty; the picker then renders ``— tok`` for that server.
    mcp_session_tokens: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)
