"""Scan orchestration.

Builds an :class:`InstallationState` from the Claude Code installation
at a given home directory. Intentionally IO-heavy; all other layers
consume the returned state as a pure value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from unclog.scan.config import ConfigParseError, Settings, load_claude_config, load_settings
from unclog.scan.filesystem import (
    InstalledPlugin,
    PluginContent,
    enumerate_agents,
    enumerate_commands,
    enumerate_plugin_content,
    enumerate_skills,
    load_installed_plugins,
)
from unclog.scan.project import ProjectScope, resolve_project_paths, scan_project
from unclog.scan.session import (
    SessionSystemBlock,
    latest_session_path,
    load_session_system_block,
)
from unclog.scan.stats import load_activity_index
from unclog.scan.tokens import TiktokenCounter
from unclog.state import GlobalScope, InstallationState
from unclog.util.paths import ClaudePaths, claude_paths


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _find_latest_session_across_projects(
    projects_dir: Path,
    counter: TiktokenCounter,
) -> SessionSystemBlock | None:
    """Return the most recent session block across every known project dir.

    Claude Code keeps one session-history directory per absolute project
    path. When unclog runs outside a project, we still want ground-truth
    MCP / system-prompt sizes, so we pick the single most recent JSONL
    across all of them.
    """
    if not projects_dir.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        latest = latest_session_path(project_dir)
        if latest is None:
            continue
        try:
            candidates.append((latest.stat().st_mtime, latest))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return load_session_system_block(candidates[0][1], counter)


def _plugin_key(plugin_name: str, marketplace: str | None) -> str:
    """Mirror settings.json's ``enabledPlugins`` key format."""
    return f"{plugin_name}@{marketplace}" if marketplace else plugin_name


def _enumerate_enabled_plugin_content(
    plugins: tuple[InstalledPlugin, ...],
    settings: Settings | None,
) -> tuple[PluginContent, ...]:
    """Scan every currently-enabled plugin's bundled skills + agents.

    Disabled plugins are skipped — their content isn't loaded into the
    context so counting it would inflate the baseline. ``stale_plugin``
    will still surface the enabled ones with their aggregated token cost.
    """
    enabled_map = settings.enabled_plugins if settings is not None else {}
    out: list[PluginContent] = []
    for plugin in plugins:
        key = _plugin_key(plugin.name, plugin.marketplace)
        if not enabled_map.get(key, False):
            continue
        if plugin.install_path is None:
            continue
        content = enumerate_plugin_content(key, plugin.install_path)
        if content is not None:
            out.append(content)
    return tuple(out)


def scan_global(
    paths: ClaudePaths,
    warnings: list[str],
) -> GlobalScope:
    """Scan everything under a single Claude home directory."""
    try:
        config = load_claude_config(paths.config_json)
    except ConfigParseError as exc:
        warnings.append(str(exc))
        config = None

    try:
        settings = load_settings(paths.settings_json)
    except ConfigParseError as exc:
        warnings.append(str(exc))
        settings = None

    counter = TiktokenCounter()
    claude_md_text = _read_text(paths.claude_md)
    claude_local_md_text = _read_text(paths.claude_local_md)

    skills = enumerate_skills(paths.skills_dir)
    agents = enumerate_agents(paths.agents_dir)
    commands = enumerate_commands(paths.commands_dir)
    plugins = load_installed_plugins(paths.installed_plugins_json)
    plugin_content = _enumerate_enabled_plugin_content(plugins, settings)

    latest_session = _find_latest_session_across_projects(paths.projects_dir, counter)

    activity = load_activity_index(paths.stats_cache_json, paths.history_jsonl)

    return GlobalScope(
        claude_home=paths.home,
        config=config,
        settings=settings,
        claude_md_bytes=len(claude_md_text.encode("utf-8")),
        claude_md_text=claude_md_text,
        claude_local_md_bytes=len(claude_local_md_text.encode("utf-8")),
        claude_local_md_text=claude_local_md_text,
        skills=skills,
        agents=agents,
        commands=commands,
        installed_plugins=plugins,
        plugin_content=plugin_content,
        latest_session=latest_session,
        activity=activity,
    )


def _scan_projects(
    paths: Path,
    scope: GlobalScope,
    *,
    project: Path | None,
    all_projects: bool,
    cwd: Path,
    warnings: list[str],
) -> tuple[ProjectScope, ...]:
    """Resolve and scan the projects requested by the CLI flags.

    Stale ``~/.claude.json`` entries (project paths that no longer
    exist on disk) are included with ``exists=False`` under
    ``--all-projects`` so they can be reported; elsewhere they are
    simply scanned and left to detectors to ignore.
    """
    known = tuple(scope.config.projects) if scope.config else ()
    targets = resolve_project_paths(
        explicit_project=project,
        all_projects=all_projects,
        cwd=cwd,
        known_projects=known,
    )
    scopes: list[ProjectScope] = []
    for target in targets:
        scoped = scan_project(target)
        if not scoped.exists and all_projects:
            warnings.append(f"Project path no longer exists: {target}")
        scopes.append(scoped)
    return tuple(scopes)


def run_scan(
    *,
    project: Path | None = None,
    all_projects: bool = False,
    cwd: Path | None = None,
) -> InstallationState:
    """Run a full scan using the environment's Claude home."""
    paths = claude_paths()
    warnings: list[str] = []
    if not paths.home.exists():
        warnings.append(f"Claude Code home does not exist: {paths.home}")
    scope = scan_global(paths, warnings)
    cwd_resolved = cwd if cwd is not None else Path.cwd()
    project_scopes = _scan_projects(
        paths.home,
        scope,
        project=project,
        all_projects=all_projects,
        cwd=cwd_resolved,
        warnings=warnings,
    )
    return InstallationState(
        generated_at=datetime.now(tz=UTC),
        claude_home=paths.home,
        global_scope=scope,
        project_scopes=project_scopes,
        warnings=tuple(warnings),
    )
