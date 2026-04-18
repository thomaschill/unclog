"""Scan orchestration.

Builds an :class:`InstallationState` from the Claude Code installation
at a given home directory. Intentionally IO-heavy; all other layers
consume the returned state as a pure value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

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
    count_mcp_invocations,
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


def _latest_sessions_across_projects(projects_dir: Path) -> list[Path]:
    """Return each project's latest session JSONL, newest first.

    Claude Code keeps one session-history directory per absolute project
    path. Unused-MCP detection aggregates invocations across every
    project's latest session so the signal is not just "did you use this
    MCP in the one session you happened to run last" — which would be
    noisy — but "did any of your recent sessions touch this MCP."
    """
    if not projects_dir.is_dir():
        return []
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
    candidates.sort(reverse=True)
    return [p for _, p in candidates]


def _find_latest_session_across_projects(
    projects_dir: Path,
    counter: TiktokenCounter,
) -> SessionSystemBlock | None:
    """Return the most recent session block across every known project dir."""
    sessions = _latest_sessions_across_projects(projects_dir)
    if not sessions:
        return None
    return load_session_system_block(sessions[0], counter)


def _aggregate_mcp_invocations(session_paths: list[Path]) -> dict[str, int]:
    """Sum per-MCP invocation counts across many session JSONLs."""
    totals: dict[str, int] = {}
    for path in session_paths:
        for name, count in count_mcp_invocations(path).items():
            totals[name] = totals.get(name, 0) + count
    return totals


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

    session_paths = _latest_sessions_across_projects(paths.projects_dir)
    latest_session = (
        load_session_system_block(session_paths[0], counter) if session_paths else None
    )
    mcp_invocations = _aggregate_mcp_invocations(session_paths)

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
        mcp_invocations=MappingProxyType(dict(mcp_invocations)),
    )


def _scan_projects(
    scope: GlobalScope,
    *,
    project: Path | None,
    cwd: Path,
    warnings: list[str],
) -> tuple[ProjectScope, ...]:
    """Resolve and scan every project relevant to this invocation.

    Without ``--project``, every known project in ``~/.claude.json`` is
    audited so cross-project CLAUDE.md dupes and scope-mismatch findings
    actually surface. Stale entries (paths that no longer exist on disk)
    are reported as warnings rather than scanned silently.
    """
    known = tuple(scope.config.projects) if scope.config else ()
    narrowed = project is not None
    targets = resolve_project_paths(
        explicit_project=project,
        cwd=cwd,
        known_projects=known,
    )
    paths = claude_paths()
    scopes: list[ProjectScope] = []
    for target in targets:
        scoped = scan_project(target, memory_file=paths.project_memory_file(target))
        if not scoped.exists and not narrowed:
            warnings.append(f"Project path no longer exists: {target}")
        scopes.append(scoped)
    return tuple(scopes)


def run_scan(
    *,
    project: Path | None = None,
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
        scope,
        project=project,
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
