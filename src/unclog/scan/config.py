"""Parse ``~/.claude.json`` into typed, immutable records.

Defensive by design — unknown fields are ignored and never cause errors.
A missing file returns ``None`` so callers can distinguish "not
configured" from "configured but empty."
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType


class ConfigParseError(RuntimeError):
    """Raised when ``.claude.json`` exists but cannot be read or parsed.

    Covers both content problems (invalid JSON) and access problems
    (permission denied, transient I/O error). The CLI catches this
    class explicitly so these user-environment issues don't surface
    as "unexpected error → please file a bug report".
    """

    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"Could not parse {path}: {cause}")
        self.path = path
        self.cause = cause


@dataclass(frozen=True)
class ProjectRecord:
    """A project entry from ``~/.claude.json``'s ``projects`` map."""

    path: Path
    mcp_servers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ClaudeConfig:
    """Parsed ``~/.claude.json``.

    Only the MCP server names are retained — every other field unclog
    reads (command/args/env, per-project session metadata, startup
    counts) was used by pre-0.2 detectors that have since been removed.
    """

    mcp_servers: frozenset[str] = frozenset()
    projects: Mapping[Path, ProjectRecord] = field(
        default_factory=lambda: MappingProxyType({})
    )


def _read_json(path: Path) -> object | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data: object = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigParseError(path, exc) from exc
    except OSError as exc:
        # PermissionError, transient I/O, FS-level weirdness. Surfacing
        # these as ConfigParseError lets the CLI render a clean message
        # naming the file, instead of the generic unexpected-error path.
        raise ConfigParseError(path, exc) from exc
    return data


def _mcp_server_names(value: object) -> frozenset[str]:
    if not isinstance(value, dict):
        return frozenset()
    return frozenset(name for name in value if isinstance(name, str))


def load_claude_config(path: Path) -> ClaudeConfig | None:
    """Load ``~/.claude.json`` into a :class:`ClaudeConfig`.

    Returns ``None`` if the file does not exist. Raises
    :class:`ConfigParseError` if it exists but is not valid JSON.
    """
    raw = _read_json(path)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return ClaudeConfig()

    projects: dict[Path, ProjectRecord] = {}
    projects_raw = raw.get("projects")
    if isinstance(projects_raw, dict):
        for abs_path, project_data in projects_raw.items():
            if not isinstance(abs_path, str) or not isinstance(project_data, dict):
                continue
            projects[Path(abs_path)] = ProjectRecord(
                path=Path(abs_path),
                mcp_servers=_mcp_server_names(project_data.get("mcpServers")),
            )

    return ClaudeConfig(
        mcp_servers=_mcp_server_names(raw.get("mcpServers")),
        projects=MappingProxyType(projects),
    )
