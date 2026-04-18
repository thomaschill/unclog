"""Immutable snapshot of a scanned Claude Code installation.

Populated by :mod:`unclog.app`. Findings detectors and UI renderers are
pure functions of this state — apply actions are the only code that
mutates the underlying filesystem.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from unclog.scan.config import ClaudeConfig, Settings
from unclog.scan.filesystem import Agent, Command, InstalledPlugin, PluginContent, Skill
from unclog.scan.mcp_probe import ProbeResult
from unclog.scan.project import ProjectScope
from unclog.scan.session import SessionSystemBlock
from unclog.scan.stats import ActivityIndex


@dataclass(frozen=True)
class GlobalScope:
    """Everything scanned under ``~/.claude/``."""

    claude_home: Path
    config: ClaudeConfig | None
    settings: Settings | None
    claude_md_bytes: int
    claude_md_text: str
    claude_local_md_bytes: int
    claude_local_md_text: str
    skills: tuple[Skill, ...] = ()
    agents: tuple[Agent, ...] = ()
    commands: tuple[Command, ...] = ()
    installed_plugins: tuple[InstalledPlugin, ...] = ()
    plugin_content: tuple[PluginContent, ...] = ()
    latest_session: SessionSystemBlock | None = None
    # Path to the most recent session JSONL found across all project
    # session directories, or ``None`` when no JSONL exists anywhere.
    # Independent of ``latest_session`` — modern Claude Code JSONLs no
    # longer carry a parseable system block (see
    # ``unclog.scan.session`` module docstring), so the welcome-panel
    # "session" row reads this path + its mtime rather than relying on
    # the (usually empty) ``SessionSystemBlock``.
    latest_session_path: Path | None = None
    activity: ActivityIndex = field(default_factory=ActivityIndex)
    # Per-MCP-server invocation count, aggregated across the latest session
    # of every project. Zero (or absent) means "never invoked" in the
    # window unclog can see; it is the signal unused_mcp detection uses.
    mcp_invocations: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    # Per-MCP-server probe results from ``--probe-mcps``. Empty unless
    # the user opted in. When present, probe-attributed tokens/errors
    # take precedence over session-inferred state for composition and
    # ``dead_mcp`` findings.
    mcp_probes: Mapping[str, ProbeResult] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class InstallationState:
    """Top-level immutable scan result."""

    generated_at: datetime
    claude_home: Path
    global_scope: GlobalScope
    project_scopes: tuple[ProjectScope, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
