"""Parse Claude Code config files.

Reads ``~/.claude.json`` and ``settings.json`` (global or project-scoped)
into typed, immutable records. Defensive by design — unknown fields are
preserved on ``raw`` and never cause errors. Missing files return ``None``
so callers can distinguish "not configured" from "configured but empty."
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


@dataclass(frozen=True)
class Hook:
    """A single hook command registered under a Claude Code event.

    Every hook that fires injects its stdout into context. Events like
    ``SessionStart`` and ``UserPromptSubmit`` fire every turn, so their
    hooks are paid for on every prompt. unclog surfaces hooks so users
    can audit what's running silently on their behalf.
    """

    event: str
    matcher: str | None
    command: str
    source_scope: str  # "global", "project", "plugin"
    source_path: Path


@dataclass(frozen=True)
class Settings:
    """Parsed ``settings.json`` (global or project-scoped)."""

    enabled_plugins: Mapping[str, bool] = field(default_factory=lambda: MappingProxyType({}))
    permissions_allow: tuple[str, ...] = ()
    permissions_deny: tuple[str, ...] = ()
    model: str | None = None
    hooks: tuple[Hook, ...] = ()
    raw: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


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


def _parse_hooks(raw: object, *, source_scope: str, source_path: Path) -> tuple[Hook, ...]:
    """Flatten the nested ``hooks`` block from a settings.json.

    Claude Code's shape is ``{event: [{matcher, hooks: [{type, command}]}]}``
    — two layers of arrays plus an event key. We flatten into a single
    tuple of :class:`Hook` records, one per command, so downstream code
    treats them uniformly regardless of which event or matcher they came
    from. Unknown shapes are skipped silently rather than erroring.
    """
    if not isinstance(raw, dict):
        return ()
    out: list[Hook] = []
    for event_name, event_entries in raw.items():
        if not isinstance(event_name, str) or not isinstance(event_entries, list):
            continue
        for entry in event_entries:
            if not isinstance(entry, dict):
                continue
            matcher = entry.get("matcher") if isinstance(entry.get("matcher"), str) else None
            commands = entry.get("hooks")
            if not isinstance(commands, list):
                continue
            for cmd in commands:
                if not isinstance(cmd, dict):
                    continue
                command_str = cmd.get("command")
                if not isinstance(command_str, str) or not command_str.strip():
                    continue
                out.append(
                    Hook(
                        event=event_name,
                        matcher=matcher,
                        command=command_str,
                        source_scope=source_scope,
                        source_path=source_path,
                    )
                )
    return tuple(out)


def load_settings(path: Path, *, source_scope: str = "global") -> Settings | None:
    """Load a ``settings.json`` (global or project-scoped).

    ``source_scope`` is stamped onto each parsed :class:`Hook` so the
    UI can distinguish a hook running globally on every project from
    one that only runs inside a specific project.

    Returns ``None`` if the file does not exist. Raises
    :class:`ConfigParseError` if it exists but is not valid JSON.
    """
    raw = _read_json(path)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return Settings()

    enabled_plugins_raw = raw.get("enabledPlugins")
    enabled_plugins: Mapping[str, bool]
    if isinstance(enabled_plugins_raw, dict):
        enabled_plugins = MappingProxyType(
            {
                str(k): bool(v)
                for k, v in enabled_plugins_raw.items()
                if isinstance(k, str) and isinstance(v, bool)
            }
        )
    else:
        enabled_plugins = MappingProxyType({})

    permissions_raw = raw.get("permissions")
    if isinstance(permissions_raw, dict):
        permissions_allow = _coerce_str_tuple(permissions_raw.get("allow"))
        permissions_deny = _coerce_str_tuple(permissions_raw.get("deny"))
    else:
        permissions_allow = ()
        permissions_deny = ()

    hooks = _parse_hooks(raw.get("hooks"), source_scope=source_scope, source_path=path)

    return Settings(
        enabled_plugins=enabled_plugins,
        permissions_allow=permissions_allow,
        permissions_deny=permissions_deny,
        model=raw.get("model") if isinstance(raw.get("model"), str) else None,
        hooks=hooks,
        raw=MappingProxyType(dict(raw)),
    )
