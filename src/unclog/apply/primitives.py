"""Apply primitive implementations.

One function per :class:`~unclog.findings.base.ActionPrimitive`. Each
primitive:

1. Captures the pre-mutation state into the snapshot.
2. Mutates the live filesystem.
3. Returns the :class:`~unclog.apply.snapshot.SnapshotAction` record
   appended to the manifest.

Primitives refuse to run if the shape of the :class:`Action` record
doesn't match what they need (wrong fields, missing path, ...).
``ApplyError`` is raised and the caller decides whether to abort the
whole batch or skip the action.

``comment_out_mcp`` and ``disable_plugin`` edit JSON with string-level
splicing rather than re-serialising, so user formatting and trailing
comments (if any) survive the change untouched — the snapshot
guarantees a clean restore either way.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from unclog.apply.snapshot import Snapshot, SnapshotAction, action_snapshot_hint
from unclog.findings.base import Finding
from unclog.util.markdown import Section, parse_sections


class ApplyError(RuntimeError):
    """Raised when a primitive cannot complete its action."""


def apply_action(
    finding: Finding,
    snapshot: Snapshot,
    *,
    claude_home: Path,
) -> SnapshotAction:
    """Dispatch on ``finding.action.primitive`` and run the matching primitive.

    ``claude_home`` is passed through because ``comment_out_mcp`` and
    ``disable_plugin`` edit files the finding's ``Action`` record
    doesn't otherwise locate (``~/.claude.json`` and
    ``~/.claude/settings.json``).
    """
    action = finding.action
    handler = _DISPATCH.get(action.primitive)
    if handler is None:
        raise ApplyError(f"Unsupported primitive: {action.primitive}")
    return handler(finding, snapshot, claude_home)


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------


def _primitive_delete_file(
    finding: Finding, snapshot: Snapshot, claude_home: Path
) -> SnapshotAction:
    action = finding.action
    target = action.path
    if target is None:
        raise ApplyError(f"delete_file requires a path (finding {finding.id})")
    target = target.expanduser()
    # ``exists()`` follows symlinks, so a symlink pointing at a missing
    # target would look absent and get skipped. Check the link itself
    # separately — we want to be able to remove a dangling symlink too.
    if not target.exists() and not target.is_symlink():
        raise ApplyError(f"delete_file target does not exist: {target}")
    record = snapshot.capture_file(
        target,
        finding.id,
        action="delete_file",
        details=action_snapshot_hint(action),
    )
    try:
        if target.is_symlink():
            # Plugin-installed skills/agents arrive as symlinks into a
            # shared cache. Deleting the pointer is the correct action;
            # ``rmtree`` would refuse anyway (GH-46010) and touching the
            # dereferenced target could clobber a shared asset.
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
            # Remove the parent directory too when the target is the only
            # file in a dedicated skill folder — snapshots preserve the
            # tree so restore still works.
            parent = target.parent
            if parent != claude_home and parent.is_dir():
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    pass
    except OSError as exc:
        raise ApplyError(f"could not delete {target}: {exc}") from exc
    return record


# ---------------------------------------------------------------------------
# comment_out_mcp
# ---------------------------------------------------------------------------

_MCP_MARKER = "__unclog_disabled__"


def _primitive_comment_out_mcp(
    finding: Finding, snapshot: Snapshot, claude_home: Path
) -> SnapshotAction:
    """Rename an MCP server key so Claude Code stops loading it.

    JSON doesn't support comments, so the "comment out" semantics from
    spec §6.1 are realised by renaming the key to
    ``__unclog_disabled__<name>`` inside ``mcpServers``. Restore simply
    copies the snapshot bytes back, so the original key returns verbatim.
    """
    action = finding.action
    name = action.server_name
    if not name:
        raise ApplyError(f"comment_out_mcp requires a server_name (finding {finding.id})")
    config_path = _resolve_claude_json(claude_home)
    if not config_path.is_file():
        raise ApplyError(f".claude.json not found at {config_path}")
    record = snapshot.capture_file(
        config_path,
        finding.id,
        action="comment_out_mcp",
        details={**action_snapshot_hint(action), "server_name": name},
    )
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ApplyError(f".claude.json root is not an object: {config_path}")
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        raise ApplyError(f"MCP server {name!r} not present in {config_path}")
    disabled_key = f"{_MCP_MARKER}{name}"
    # Preserve insertion order by rebuilding the dict key-by-key.
    new_servers: dict[str, Any] = {}
    for key, value in servers.items():
        if key == name:
            new_servers[disabled_key] = value
        else:
            new_servers[key] = value
    data["mcpServers"] = new_servers
    config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return record


def _resolve_claude_json(claude_home: Path) -> Path:
    """Locate ``.claude.json`` the same way :class:`ClaudePaths` does.

    Honours both the inside layout (``<claude_home>/.claude.json`` —
    used when ``CLAUDE_CONFIG_DIR`` is set) and the traditional outside
    layout (``~/.claude.json`` alongside ``~/.claude/``).
    """
    inside = claude_home / ".claude.json"
    if inside.exists():
        return inside
    outside = claude_home.parent / ".claude.json"
    if outside.exists():
        return outside
    return inside


# ---------------------------------------------------------------------------
# disable_plugin / uninstall_plugin
# ---------------------------------------------------------------------------


def _primitive_disable_plugin(
    finding: Finding, snapshot: Snapshot, claude_home: Path
) -> SnapshotAction:
    """Flip ``enabledPlugins[<key>]`` to ``false`` in ``settings.json``.

    The plugin's cache directory is left untouched — unchecking the
    entry is enough for Claude Code to stop loading the plugin on the
    next session.
    """
    action = finding.action
    plugin_key = action.plugin_key
    if not plugin_key:
        raise ApplyError(f"disable_plugin requires a plugin_key (finding {finding.id})")
    settings_path = claude_home / "settings.json"
    if not settings_path.is_file():
        raise ApplyError(f"settings.json not found at {settings_path}")
    prior = _read_plugin_enabled_value(settings_path, plugin_key)
    record = snapshot.capture_file(
        settings_path,
        finding.id,
        action="disable_plugin",
        details={
            **action_snapshot_hint(action),
            "plugin_key": plugin_key,
            "prior_value": prior,
        },
    )
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ApplyError(f"settings.json root is not an object: {settings_path}")
    plugins = data.get("enabledPlugins")
    if not isinstance(plugins, dict):
        plugins = {}
    plugins[plugin_key] = False
    data["enabledPlugins"] = plugins
    settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return record


def _read_plugin_enabled_value(settings_path: Path, plugin_key: str) -> Any:
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    plugins = data.get("enabledPlugins")
    if not isinstance(plugins, dict):
        return None
    return plugins.get(plugin_key)


def _primitive_uninstall_plugin(
    finding: Finding, snapshot: Snapshot, claude_home: Path
) -> SnapshotAction:
    """Remove a plugin from ``installed_plugins.json`` and delete its cache dir.

    Only offered for ``disabled_plugin_residue`` findings (plugin
    disabled ≥ 90d). The plugins cache directory (if any) is captured
    into the snapshot before removal so restore can bring it back.
    """
    action = finding.action
    plugin_key = action.plugin_key
    if not plugin_key:
        raise ApplyError(f"uninstall_plugin requires a plugin_key (finding {finding.id})")
    installed_path = claude_home / "plugins" / "installed_plugins.json"
    if not installed_path.is_file():
        raise ApplyError(f"installed_plugins.json not found at {installed_path}")
    record = snapshot.capture_file(
        installed_path,
        finding.id,
        action="uninstall_plugin",
        details={**action_snapshot_hint(action), "plugin_key": plugin_key},
    )
    data: Any = json.loads(installed_path.read_text(encoding="utf-8"))
    name = plugin_key.split("@", 1)[0] if "@" in plugin_key else plugin_key
    if isinstance(data, dict):
        plugins_field = data.get("plugins")
        if isinstance(plugins_field, list):
            data["plugins"] = [
                p
                for p in plugins_field
                if not (isinstance(p, dict) and p.get("name") == name)
            ]
        elif name in data:
            data.pop(name, None)
    elif isinstance(data, list):
        data = [p for p in data if not (isinstance(p, dict) and p.get("name") == name)]
    installed_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    cache_dir = claude_home / "plugins" / "cache" / name
    if cache_dir.is_dir():
        snapshot.capture_file(
            cache_dir,
            finding.id,
            action="uninstall_plugin:cache",
            details={"plugin_key": plugin_key},
        )
        shutil.rmtree(cache_dir)
    return record


# ---------------------------------------------------------------------------
# remove_claude_md_section / remove_claude_md_lines / move_claude_md_section
# ---------------------------------------------------------------------------


def _primitive_remove_claude_md_section(
    finding: Finding, snapshot: Snapshot, claude_home: Path
) -> SnapshotAction:
    action = finding.action
    if action.path is None or action.heading is None:
        raise ApplyError(
            f"remove_claude_md_section requires path and heading (finding {finding.id})"
        )
    target = action.path.expanduser()
    if not target.is_file():
        raise ApplyError(f"CLAUDE.md not found: {target}")
    record = snapshot.capture_file(
        target,
        finding.id,
        action="remove_claude_md_section",
        details={**action_snapshot_hint(action), "heading": action.heading},
    )
    new_text = _strip_section(target.read_text(encoding="utf-8"), action.heading)
    target.write_text(new_text, encoding="utf-8")
    return record


def _strip_section(text: str, heading: str) -> str:
    """Return ``text`` with the first section matching ``heading`` removed.

    The section spans from its heading line up to (but not including)
    the next heading of equal-or-lower level, matching
    :func:`unclog.util.markdown.parse_sections`. If no section matches,
    the text is returned unchanged — the caller will notice because
    the byte count didn't shrink, but we don't raise: snapshot already
    captured the original.
    """
    sections = parse_sections(text)
    target = _find_section_by_heading(sections, heading)
    if target is None:
        return text
    start = target.byte_offset
    end = target.byte_offset + target.byte_length
    encoded = text.encode("utf-8")
    return (encoded[:start] + encoded[end:]).decode("utf-8")


def _find_section_by_heading(sections: Sequence[Section], heading: str) -> Section | None:
    for section in sections:
        if section.heading_level >= 1 and section.heading_text == heading:
            return section
    return None


def _primitive_remove_claude_md_lines(
    finding: Finding, snapshot: Snapshot, claude_home: Path
) -> SnapshotAction:
    action = finding.action
    if action.path is None or not action.line_numbers:
        raise ApplyError(
            f"remove_claude_md_lines requires path and line_numbers (finding {finding.id})"
        )
    target = action.path.expanduser()
    if not target.is_file():
        raise ApplyError(f"CLAUDE.md not found: {target}")
    record = snapshot.capture_file(
        target,
        finding.id,
        action="remove_claude_md_lines",
        details={
            **action_snapshot_hint(action),
            "line_numbers": list(action.line_numbers),
        },
    )
    to_drop = set(action.line_numbers)
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for idx, line in enumerate(lines, start=1) if idx not in to_drop]
    target.write_text("".join(kept), encoding="utf-8")
    return record


def _primitive_move_claude_md_section(
    finding: Finding, snapshot: Snapshot, claude_home: Path
) -> SnapshotAction:
    """Cross-scope move: strip the section from source, append to destination.

    ``finding.action.path`` is the *source* CLAUDE.md. The destination
    is carried in ``evidence["destination_path"]`` (written by the
    scope_mismatch detectors). Both files are captured into the
    snapshot before mutation so restore rebuilds both sides.
    """
    action = finding.action
    if action.path is None or action.heading is None:
        raise ApplyError(
            f"move_claude_md_section requires path and heading (finding {finding.id})"
        )
    destination_raw = finding.evidence.get("destination_path") if finding.evidence else None
    if not isinstance(destination_raw, str):
        raise ApplyError(
            f"move_claude_md_section missing evidence.destination_path (finding {finding.id})"
        )
    source = action.path.expanduser()
    destination = Path(destination_raw).expanduser()
    if not source.is_file():
        raise ApplyError(f"source CLAUDE.md not found: {source}")
    source_text = source.read_text(encoding="utf-8")
    section = _find_section_by_heading(parse_sections(source_text), action.heading)
    if section is None:
        raise ApplyError(f"heading {action.heading!r} not found in {source}")
    # Capture source first; destination gets captured below (it may not exist yet).
    record = snapshot.capture_file(
        source,
        finding.id,
        action="move_claude_md_section",
        details={
            **action_snapshot_hint(action),
            "heading": action.heading,
            "destination_path": str(destination),
        },
    )
    if destination.is_file():
        snapshot.capture_file(
            destination,
            finding.id,
            action="move_claude_md_section:destination",
            details={"heading": action.heading, "role": "destination"},
        )
    # 1. Extract section body bytes before stripping.
    encoded = source_text.encode("utf-8")
    section_bytes = encoded[section.byte_offset : section.byte_offset + section.byte_length]
    section_text = section_bytes.decode("utf-8")
    # 2. Strip from source.
    new_source = (
        encoded[: section.byte_offset] + encoded[section.byte_offset + section.byte_length :]
    ).decode("utf-8")
    source.write_text(new_source, encoding="utf-8")
    # 3. Append to destination (creating it if needed).
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = destination.read_text(encoding="utf-8") if destination.is_file() else ""
    separator = "" if existing == "" or existing.endswith("\n\n") else (
        "\n" if existing.endswith("\n") else "\n\n"
    )
    destination.write_text(existing + separator + section_text, encoding="utf-8")
    return record


# ---------------------------------------------------------------------------
# open_in_editor / flag_only
# ---------------------------------------------------------------------------


def _primitive_open_in_editor(
    finding: Finding, snapshot: Snapshot, claude_home: Path
) -> SnapshotAction:
    """Spawn ``$EDITOR`` on the target path. No snapshot of bytes is taken.

    We still record the action in the manifest so ``unclog restore``
    shows what the user was prompted to do, but no file copy is made
    — the user may or may not save changes.
    """
    action = finding.action
    if action.path is None:
        raise ApplyError(f"open_in_editor requires a path (finding {finding.id})")
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        raise ApplyError("open_in_editor requires $EDITOR or $VISUAL to be set")
    target = str(action.path.expanduser())
    args = [editor, target]
    if action.line_numbers:
        # nano, vim, emacs, code all accept a +N line hint (code uses --goto path:N).
        first_line = action.line_numbers[0]
        args.insert(1, f"+{first_line}")
    try:
        subprocess.run(args, check=False)
    except OSError as exc:
        raise ApplyError(f"could not spawn editor: {exc}") from exc
    # Record the intent, but no bytes to snapshot since we don't control the edit.
    record = SnapshotAction(
        finding_id=finding.id,
        action="open_in_editor",
        original_path=target,
        snapshot_path="",
        details={**action_snapshot_hint(action), "editor": editor},
    )
    snapshot.actions.append(record)
    return record


def _primitive_flag_only(
    finding: Finding, snapshot: Snapshot, claude_home: Path
) -> SnapshotAction:
    """Purely informational actions have no effect and no snapshot bytes."""
    action = finding.action
    record = SnapshotAction(
        finding_id=finding.id,
        action="flag_only",
        original_path=str(action.path) if action.path is not None else "",
        snapshot_path="",
        details=action_snapshot_hint(action),
    )
    snapshot.actions.append(record)
    return record


_DISPATCH = {
    "delete_file": _primitive_delete_file,
    "comment_out_mcp": _primitive_comment_out_mcp,
    "disable_plugin": _primitive_disable_plugin,
    "uninstall_plugin": _primitive_uninstall_plugin,
    "remove_claude_md_section": _primitive_remove_claude_md_section,
    "remove_claude_md_lines": _primitive_remove_claude_md_lines,
    "move_claude_md_section": _primitive_move_claude_md_section,
    "open_in_editor": _primitive_open_in_editor,
    "flag_only": _primitive_flag_only,
}


__all__ = ["ApplyError", "apply_action"]
