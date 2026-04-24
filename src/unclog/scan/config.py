"""Parse ``~/.claude.json`` into typed, immutable records.

Defensive by design — unknown fields are preserved on ``raw`` and never
cause errors. A missing file returns ``None`` so callers can distinguish
"not configured" from "configured but empty."
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType


class ConfigParseError(RuntimeError):
    """Raised when a config file exists but cannot be read or parsed.

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
class McpServer:
    """A single MCP server definition."""

    name: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    raw: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ProjectRecord:
    """A project entry from ``~/.claude.json``'s ``projects`` map."""

    path: Path
    mcp_servers: Mapping[str, McpServer] = field(default_factory=lambda: MappingProxyType({}))
    last_session_id: str | None = None
    last_cost: float | None = None
    last_api_duration_ms: int | None = None
    raw: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ClaudeConfig:
    """Parsed ``~/.claude.json``."""

    mcp_servers: Mapping[str, McpServer] = field(default_factory=lambda: MappingProxyType({}))
    projects: Mapping[Path, ProjectRecord] = field(default_factory=lambda: MappingProxyType({}))
    num_startups: int | None = None


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


def _coerce_str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _coerce_str_mapping(value: object) -> Mapping[str, str]:
    if not isinstance(value, dict):
        return MappingProxyType({})
    return MappingProxyType(
        {str(k): str(v) for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
    )


def _parse_mcp_server(name: str, data: object) -> McpServer | None:
    if not isinstance(data, dict):
        return None
    return McpServer(
        name=name,
        command=data.get("command") if isinstance(data.get("command"), str) else None,
        args=_coerce_str_tuple(data.get("args")),
        env=_coerce_str_mapping(data.get("env")),
        raw=MappingProxyType(dict(data)),
    )


def _parse_mcp_server_map(value: object) -> Mapping[str, McpServer]:
    if not isinstance(value, dict):
        return MappingProxyType({})
    servers: dict[str, McpServer] = {}
    for name, data in value.items():
        if not isinstance(name, str):
            continue
        server = _parse_mcp_server(name, data)
        if server is not None:
            servers[name] = server
    return MappingProxyType(servers)


def _parse_project_record(abs_path: str, data: object) -> ProjectRecord | None:
    if not isinstance(data, dict):
        return None
    return ProjectRecord(
        path=Path(abs_path),
        mcp_servers=_parse_mcp_server_map(data.get("mcpServers")),
        last_session_id=data.get("lastSessionId")
        if isinstance(data.get("lastSessionId"), str)
        else None,
        last_cost=data.get("lastCost") if isinstance(data.get("lastCost"), (int, float)) else None,
        last_api_duration_ms=data.get("lastAPIDuration")
        if isinstance(data.get("lastAPIDuration"), int)
        else None,
        raw=MappingProxyType(dict(data)),
    )


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
            if not isinstance(abs_path, str):
                continue
            record = _parse_project_record(abs_path, project_data)
            if record is not None:
                projects[record.path] = record

    return ClaudeConfig(
        mcp_servers=_parse_mcp_server_map(raw.get("mcpServers")),
        projects=MappingProxyType(projects),
        num_startups=raw.get("numStartups") if isinstance(raw.get("numStartups"), int) else None,
    )


