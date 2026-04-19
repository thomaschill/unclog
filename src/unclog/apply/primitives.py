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
import shlex
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


def _load_json_config(path: Path, finding_id: str) -> Any:
    """Read + json-decode a config file, converting every fail mode to ApplyError.

    The three JSON primitives all need to read ``.claude.json`` /
    ``settings.json`` / ``installed_plugins.json`` after snapshot
    capture. A malformed or unreadable config is not a primitive bug —
    it's a user-data condition — so it needs to become ``ApplyError``
    rather than escape as ``JSONDecodeError`` (a ``ValueError``) and
    take down the whole batch.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApplyError(f"could not read {path} (finding {finding_id}): {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ApplyError(f"{path} is not valid UTF-8 (finding {finding_id}): {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApplyError(f"{path} is not valid JSON (finding {finding_id}): {exc}") from exc


def _write_json_config(path: Path, data: Any, finding_id: str) -> None:
    """Write JSON back to a config file, converting OSError to ApplyError."""
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        raise ApplyError(f"could not write {path} (finding {finding_id}): {exc}") from exc


def _read_utf8(path: Path, finding_id: str) -> str:
    """Read a CLAUDE.md-style file as UTF-8, converting failures to ApplyError.

    ``claude_md_dead_ref`` and the scope detectors happily run on
    files their scan read with ``errors="replace"``, so a user with a
    Latin-1 or Windows-1252 CLAUDE.md can see findings that the apply
    primitives can't act on. Refuse explicitly instead of corrupting
    non-ASCII bytes via a lossy re-encode.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApplyError(f"could not read {path} (finding {finding_id}): {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ApplyError(
            f"{path} is not UTF-8; unclog will not edit non-UTF-8 "
            f"CLAUDE.md files in v0.1 (finding {finding_id}): {exc}"
        ) from exc


def _write_utf8(path: Path, text: str, finding_id: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ApplyError(f"could not write {path} (finding {finding_id}): {exc}") from exc


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

    Handles both scopes:
    - global: edits ``mcpServers`` at the root of ``.claude.json``.
    - project: edits ``projects[<abs-path>].mcpServers`` for the exact
      project the finding is scoped to (detectors set
      ``finding.scope.project_path`` when the server lives there).
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
    data = _load_json_config(config_path, finding.id)
    if not isinstance(data, dict):
        raise ApplyError(f".claude.json root is not an object: {config_path}")

    servers_container, container_label = _locate_mcp_servers(data, finding, config_path)
    if name not in servers_container:
        raise ApplyError(f"MCP server {name!r} not present in {container_label}")
    disabled_key = f"{_MCP_MARKER}{name}"
    # Preserve insertion order by rebuilding the dict key-by-key.
    new_servers: dict[str, Any] = {}
    for key, value in servers_container.items():
        if key == name:
            new_servers[disabled_key] = value
        else:
            new_servers[key] = value
    # Write back into the container's parent (global root or project record).
    _replace_mcp_servers(data, finding, new_servers)
    _write_json_config(config_path, data, finding.id)
    return record


def _locate_mcp_servers(
    data: dict[str, Any], finding: Finding, config_path: Path
) -> tuple[dict[str, Any], str]:
    """Return the ``mcpServers`` dict the finding targets, plus a human label.

    Raises :class:`ApplyError` if the expected container is absent or
    malformed — e.g. a project-scoped finding whose project key was
    removed from ``.claude.json`` between scan and apply.
    """
    scope = finding.scope
    if scope.kind == "project" and scope.project_path is not None:
        projects = data.get("projects")
        if not isinstance(projects, dict):
            raise ApplyError(
                f"projects section missing or malformed in {config_path} "
                f"(finding {finding.id})"
            )
        project_key = _match_project_key(projects, scope.project_path)
        if project_key is None:
            raise ApplyError(
                f"project {scope.project_path} not present in {config_path} "
                f"(finding {finding.id})"
            )
        project_record = projects[project_key]
        if not isinstance(project_record, dict):
            raise ApplyError(
                f"project record for {project_key!r} is not an object in {config_path}"
            )
        servers = project_record.get("mcpServers")
        if not isinstance(servers, dict):
            raise ApplyError(
                f"projects.{project_key!r}.mcpServers missing or malformed "
                f"in {config_path}"
            )
        return servers, f"projects.{project_key!r}.mcpServers"
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        raise ApplyError(f"mcpServers missing or malformed in {config_path}")
    return servers, f"{config_path}:mcpServers"


def _replace_mcp_servers(
    data: dict[str, Any], finding: Finding, new_servers: dict[str, Any]
) -> None:
    """Write ``new_servers`` back into the same container `_locate_mcp_servers` read from."""
    scope = finding.scope
    if scope.kind == "project" and scope.project_path is not None:
        projects = data["projects"]
        project_key = _match_project_key(projects, scope.project_path)
        # _locate already validated; re-derive to mutate.
        assert project_key is not None
        projects[project_key]["mcpServers"] = new_servers
        return
    data["mcpServers"] = new_servers


def _match_project_key(projects: dict[str, Any], target: Path) -> str | None:
    """Match ``target`` against a key in ``projects`` tolerantly.

    Config keys are whatever strings the user's Claude Code wrote —
    usually absolute paths, but not guaranteed to be resolved the same
    way Python resolves them (``/tmp`` vs ``/private/tmp`` on macOS,
    trailing slashes, ``~`` expansion). Try exact match first, then
    compare resolved paths, so a scan-vs-apply divergence in how we
    represent the path doesn't break the lookup.
    """
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
    data = _load_json_config(settings_path, finding.id)
    if not isinstance(data, dict):
        raise ApplyError(f"settings.json root is not an object: {settings_path}")
    plugins = data.get("enabledPlugins")
    if not isinstance(plugins, dict):
        plugins = {}
    plugins[plugin_key] = False
    data["enabledPlugins"] = plugins
    _write_json_config(settings_path, data, finding.id)
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
    data: Any = _load_json_config(installed_path, finding.id)
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
    _write_json_config(installed_path, data, finding.id)

    cache_dir = claude_home / "plugins" / "cache" / name
    # ``is_symlink()`` first: a plugin manager that symlinks the cache
    # into a shared location would crash ``rmtree`` (same class of bug
    # as the 166-skill symlink incident). Capture the link as a link
    # and ``unlink()`` the pointer; leave the backing tree untouched.
    if cache_dir.is_symlink():
        snapshot.capture_file(
            cache_dir,
            finding.id,
            action="uninstall_plugin:cache",
            details={"plugin_key": plugin_key, "symlink": True},
        )
        try:
            cache_dir.unlink()
        except OSError as exc:
            raise ApplyError(f"could not remove plugin cache link {cache_dir}: {exc}") from exc
    elif cache_dir.is_dir():
        snapshot.capture_file(
            cache_dir,
            finding.id,
            action="uninstall_plugin:cache",
            details={"plugin_key": plugin_key},
        )
        try:
            shutil.rmtree(cache_dir)
        except OSError as exc:
            raise ApplyError(f"could not remove plugin cache {cache_dir}: {exc}") from exc
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
    new_text = _strip_section(_read_utf8(target, finding.id), action.heading)
    _write_utf8(target, new_text, finding.id)
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
    lines = _read_utf8(target, finding.id).splitlines(keepends=True)
    kept = [line for idx, line in enumerate(lines, start=1) if idx not in to_drop]
    _write_utf8(target, "".join(kept), finding.id)
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
    source_text = _read_utf8(source, finding.id)
    section = _find_section_by_heading(parse_sections(source_text), action.heading)
    if section is None:
        raise ApplyError(f"heading {action.heading!r} not found in {source}")
    # Capture source first; capture destination unconditionally so the
    # snapshot-path sandbox (_relative_snapshot_path → SnapshotError)
    # runs even when the destination doesn't exist yet. A tampered
    # finding with destination_path pointing outside claude_home +
    # project_paths will be refused here instead of silently writing
    # a file restore can't clean up.
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
    # Always capture the destination — capture_file handles missing
    # originals by recording an "absent" action (restore removes the
    # post-apply file). Present destinations get their bytes captured so
    # restore can rewrite them.
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
    _write_utf8(source, new_source, finding.id)
    # 3. Append to destination (creating it if needed).
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_utf8(destination, finding.id) if destination.is_file() else ""
    separator = "" if existing == "" or existing.endswith("\n\n") else (
        "\n" if existing.endswith("\n") else "\n\n"
    )
    _write_utf8(destination, existing + separator + section_text, finding.id)
    return record


# ---------------------------------------------------------------------------
# open_in_editor / flag_only
# ---------------------------------------------------------------------------


_FORK_RETURN_EDITORS: frozenset[str] = frozenset(
    {"code", "code-insiders", "cursor", "windsurf", "subl", "mate", "atom"}
)
_WAIT_FLAGS: frozenset[str] = frozenset({"--wait", "-w", "-W"})


def _primitive_open_in_editor(
    finding: Finding, snapshot: Snapshot, claude_home: Path
) -> SnapshotAction:
    """Spawn ``$EDITOR`` on the target path. No snapshot of bytes is taken.

    We still record the action in the manifest so ``unclog restore``
    shows what the user was prompted to do, but no file copy is made
    — the user may or may not save changes.

    ``$EDITOR`` is parsed with :func:`shlex.split` so compound values
    like ``code --wait`` work. For GUI editors that fork-and-return
    by default (``code``, ``subl``, ``mate``, ...), a ``--wait`` flag
    is inserted if the user hasn't already supplied one, so the user
    returns to the unclog apply flow only after they actually close
    the file.
    """
    action = finding.action
    if action.path is None:
        raise ApplyError(f"open_in_editor requires a path (finding {finding.id})")
    editor_env = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor_env:
        raise ApplyError("open_in_editor requires $EDITOR or $VISUAL to be set")
    try:
        editor_argv = shlex.split(editor_env)
    except ValueError as exc:
        raise ApplyError(f"$EDITOR could not be parsed: {editor_env!r}: {exc}") from exc
    if not editor_argv:
        raise ApplyError("$EDITOR is empty after parsing")
    editor_bin = Path(editor_argv[0]).name
    # If the user picked a GUI fork-return editor and didn't pass a wait
    # flag themselves, add one. Terminal editors (vim, nano, emacs,
    # hx, micro) block on their own, so we leave their argv alone.
    if editor_bin in _FORK_RETURN_EDITORS and not any(
        arg in _WAIT_FLAGS for arg in editor_argv[1:]
    ):
        editor_argv.append("--wait")
    target = str(action.path.expanduser())
    args = list(editor_argv)
    if action.line_numbers:
        # nano, vim, emacs accept +N; VS Code-family uses --goto path:N.
        first_line = action.line_numbers[0]
        if editor_bin in {"code", "code-insiders", "cursor", "windsurf"}:
            args.extend(["--goto", f"{target}:{first_line}"])
        else:
            args.extend([f"+{first_line}", target])
    else:
        args.append(target)
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
        details={**action_snapshot_hint(action), "editor": editor_env},
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
