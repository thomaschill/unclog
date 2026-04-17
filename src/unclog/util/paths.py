"""Path resolution for the Claude Code installation.

All path construction in unclog routes through this module so the
`CLAUDE_CONFIG_DIR` environment variable is always honoured and we never
hardcode `~/.claude/` deeper than here.
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
        return self.home / ".claude.json"

    @property
    def settings_json(self) -> Path:
        return self.home / "settings.json"

    @property
    def claude_md(self) -> Path:
        return self.home / "CLAUDE.md"

    @property
    def claude_local_md(self) -> Path:
        return self.home / "CLAUDE.local.md"

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
    def plugins_dir(self) -> Path:
        return self.home / "plugins"

    @property
    def installed_plugins_json(self) -> Path:
        return self.plugins_dir / "installed_plugins.json"

    @property
    def projects_dir(self) -> Path:
        return self.home / "projects"

    @property
    def stats_cache_json(self) -> Path:
        return self.home / "stats-cache.json"

    @property
    def history_jsonl(self) -> Path:
        return self.home / "history.jsonl"

    @property
    def unclog_dir(self) -> Path:
        return self.home / ".unclog"

    @property
    def snapshots_dir(self) -> Path:
        return self.unclog_dir / "snapshots"

    @property
    def cache_dir(self) -> Path:
        return self.unclog_dir / "cache"

    @property
    def config_toml(self) -> Path:
        return self.unclog_dir / "config.toml"

    def project_session_dir(self, project_path: Path) -> Path:
        """Return the session-history directory Claude Code keeps for a project."""
        return self.projects_dir / encode_project_path(project_path)


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


def encode_project_path(project_path: Path) -> str:
    """Encode an absolute project path the way Claude Code does for session storage.

    Claude Code stores per-project session JSONL under
    ``~/.claude/projects/<encoded>/`` where ``<encoded>`` is the absolute
    path with every ``/`` replaced by ``-``. A leading slash therefore
    becomes a leading ``-``.
    """
    absolute = project_path.expanduser().resolve()
    return str(absolute).replace("/", "-")
