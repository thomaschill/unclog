"""Apply primitives — one function per :class:`~unclog.findings.base.ActionPrimitive`.

Two primitives in 0.2:

- ``delete_file`` removes an agent file or skill directory.
- ``comment_out_mcp`` renames an MCP server key in ``~/.claude.json``
  so Claude Code stops loading it. Reversible by editing the JSON back;
  unclog no longer snapshots.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from unclog.findings.base import Finding


class ApplyError(RuntimeError):
    """Raised when a primitive cannot complete its action."""


def apply_action(finding: Finding, *, claude_home: Path) -> None:
    """Dispatch on ``finding.action.primitive`` and run the matching primitive."""
    primitive = finding.action.primitive
    if primitive == "delete_file":
        _delete_file(finding)
    elif primitive == "comment_out_mcp":
        _comment_out_mcp(finding, claude_home=claude_home)
    else:  # pragma: no cover - Literal exhaustion
        raise ApplyError(f"Unsupported primitive: {primitive}")


def _delete_file(finding: Finding) -> None:
    target = finding.action.path
    if target is None:
        raise ApplyError("delete_file action is missing its target path")
    target = target.expanduser()
    if not target.exists() and not target.is_symlink():
        raise ApplyError(f"target does not exist: {target}")
    try:
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)
    except OSError as exc:
        raise ApplyError(f"could not delete {target}: {exc}") from exc


_MCP_DISABLED_PREFIX = "__unclog_disabled__"


def _comment_out_mcp(finding: Finding, *, claude_home: Path) -> None:
    """Rename ``mcpServers[name]`` to ``__unclog_disabled__<name>``."""
    name = finding.action.server_name
    if not name:
        raise ApplyError("comment_out_mcp action is missing the server name")
    config_path = _resolve_claude_json(claude_home)
    if not config_path.is_file():
        raise ApplyError(f".claude.json not found at {config_path}")
    data = _load_json(config_path)
    if not isinstance(data, dict):
        raise ApplyError(f"{config_path} is not a JSON object")

    container, label = _locate_mcp_servers(data, finding, config_path)
    if name not in container:
        raise ApplyError(
            f"MCP server {name!r} is no longer listed under {label} — "
            f"it may have been removed since the scan. Re-run unclog and try again."
        )
    disabled_key = f"{_MCP_DISABLED_PREFIX}{name}"
    rebuilt: dict[str, Any] = {}
    for key, value in container.items():
        rebuilt[disabled_key if key == name else key] = value
    _write_mcp_servers(data, finding, rebuilt)
    _write_json(config_path, data)


def _resolve_claude_json(claude_home: Path) -> Path:
    inside = claude_home / ".claude.json"
    if inside.exists():
        return inside
    outside = claude_home.parent / ".claude.json"
    if outside.exists():
        return outside
    return inside


def _locate_mcp_servers(
    data: dict[str, Any], finding: Finding, config_path: Path
) -> tuple[dict[str, Any], str]:
    scope = finding.scope
    if scope.kind == "project" and scope.project_path is not None:
        projects = data.get("projects")
        if not isinstance(projects, dict):
            raise ApplyError(
                f"{config_path} no longer has a 'projects' section — "
                f"re-run unclog and try again."
            )
        key = _match_project_key(projects, scope.project_path)
        if key is None:
            raise ApplyError(
                f"project {scope.project_path} is no longer in {config_path}. "
                f"Re-run unclog and try again."
            )
        record = projects[key]
        if not isinstance(record, dict):
            raise ApplyError(f"project entry for {scope.project_path} is not an object.")
        servers = record.get("mcpServers")
        if not isinstance(servers, dict):
            raise ApplyError(
                f"project {scope.project_path} has no MCP servers section."
            )
        return servers, str(scope.project_path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        raise ApplyError(
            f"{config_path} has no MCP servers section — re-run unclog and try again."
        )
    return servers, str(config_path)


def _write_mcp_servers(
    data: dict[str, Any], finding: Finding, new_servers: dict[str, Any]
) -> None:
    scope = finding.scope
    if scope.kind == "project" and scope.project_path is not None:
        projects = data["projects"]
        key = _match_project_key(projects, scope.project_path)
        assert key is not None
        projects[key]["mcpServers"] = new_servers
        return
    data["mcpServers"] = new_servers


def _match_project_key(projects: dict[str, Any], target: Path) -> str | None:
    target_str = str(target)
    if target_str in projects:
        return target_str
    try:
        target_resolved = target.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        target_resolved = target
    for key in projects:
        try:
            candidate = Path(key).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if candidate == target_resolved:
            return key
    return None


def _load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApplyError(f"could not read {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApplyError(f"{path} is not valid JSON: {exc}") from exc


def _write_json(path: Path, data: Any) -> None:
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        raise ApplyError(f"could not write {path}: {exc}") from exc


__all__ = ["ApplyError", "apply_action"]
