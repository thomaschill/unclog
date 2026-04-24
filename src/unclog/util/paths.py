"""Path resolution for the Claude Code installation.

All path construction in unclog routes through this module so the
``CLAUDE_CONFIG_DIR`` environment variable is always honoured and we
never hardcode ``~/.claude/`` deeper than here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path


@dataclass(frozen=True)
class ClaudePaths:
    """Resolved paths into a Claude Code installation."""

    home: Path

    @property
    def config_json(self) -> Path:
        """Resolve ``.claude.json`` with the two-location layout.

        Default installs place it at ``~/.claude.json`` alongside
        ``~/.claude/``. ``CLAUDE_CONFIG_DIR`` overrides nest it *inside*
        the chosen directory. We pick whichever exists, preferring the
        inside variant when both are present, so either layout works.
        """
        inside = self.home / ".claude.json"
        if inside.exists():
            return inside
        outside = self.home.parent / ".claude.json"
        if outside.exists():
            return outside
        # Neither exists: prefer the inside location as the write-target
        # so generated paths are consistent with the env-override layout.
        return inside

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def agents_dir(self) -> Path:
        return self.home / "agents"

    @property
    def commands_dir(self) -> Path:
        return self.home / "commands"

    @property
    def projects_dir(self) -> Path:
        return self.home / "projects"

    @property
    def unclog_dir(self) -> Path:
        """Where unclog stores its own sidecar state (error logs, sentinels)."""
        return self.home / ".unclog"


@cache
def claude_home() -> Path:
    """Resolve the Claude Code config directory.

    Honours ``CLAUDE_CONFIG_DIR`` if set, otherwise falls back to
    ``~/.claude/``. The result is expanded and made absolute but not
    guaranteed to exist.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".claude").resolve()


def claude_paths() -> ClaudePaths:
    """Return a ``ClaudePaths`` rooted at the resolved Claude home."""
    return ClaudePaths(home=claude_home())
