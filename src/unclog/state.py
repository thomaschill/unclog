"""Immutable snapshot of a scanned Claude Code installation.

Populated by :mod:`unclog.app`. Findings detectors and UI renderers are
pure functions of this state — apply actions are the only code that
mutates the underlying filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from unclog.scan.config import ClaudeConfig, Settings
from unclog.scan.filesystem import Agent, Command, InstalledPlugin, Skill
from unclog.scan.session import SessionSystemBlock
from unclog.scan.stats import ActivityIndex

BaselineTier = Literal["lean", "typical", "clogged"]

TIER_LEAN_UPPER_BOUND = 20_000
TIER_CLOGGED_LOWER_BOUND = 50_000


def tier_for_baseline(tokens: int) -> BaselineTier:
    """Classify an estimated baseline token count into a tier.

    Thresholds (spec section 11.5):
    - ``< 20,000`` -> ``lean``
    - ``20,000 - 50,000`` -> ``typical``
    - ``> 50,000`` -> ``clogged``
    """
    if tokens < TIER_LEAN_UPPER_BOUND:
        return "lean"
    if tokens > TIER_CLOGGED_LOWER_BOUND:
        return "clogged"
    return "typical"


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
    latest_session: SessionSystemBlock | None = None
    activity: ActivityIndex = field(default_factory=ActivityIndex)


@dataclass(frozen=True)
class InstallationState:
    """Top-level immutable scan result."""

    generated_at: datetime
    claude_home: Path
    global_scope: GlobalScope
    warnings: tuple[str, ...] = field(default_factory=tuple)
