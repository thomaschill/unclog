from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from unclog.findings import detect
from unclog.findings.thresholds import Thresholds
from unclog.scan.config import ClaudeConfig, McpServer, Settings
from unclog.scan.filesystem import Agent, Command, InstalledPlugin, Skill
from unclog.scan.session import SessionSystemBlock
from unclog.scan.stats import ActivityIndex
from unclog.state import GlobalScope, InstallationState

NOW = datetime(2026, 4, 17, tzinfo=UTC)


def _state(
    *,
    claude_home: Path = Path("/fake/.claude"),
    skills: tuple[Skill, ...] = (),
    agents: tuple[Agent, ...] = (),
    commands: tuple[Command, ...] = (),
    plugins: tuple[InstalledPlugin, ...] = (),
    config: ClaudeConfig | None = None,
    settings: Settings | None = None,
    latest_session: SessionSystemBlock | None = None,
    activity: ActivityIndex | None = None,
) -> InstallationState:
    return InstallationState(
        generated_at=NOW,
        claude_home=claude_home,
        global_scope=GlobalScope(
            claude_home=claude_home,
            config=config,
            settings=settings if settings is not None else Settings(),
            claude_md_bytes=0,
            claude_md_text="",
            claude_local_md_bytes=0,
            claude_local_md_text="",
            skills=skills,
            agents=agents,
            commands=commands,
            installed_plugins=plugins,
            latest_session=latest_session,
            activity=activity if activity is not None else ActivityIndex(),
        ),
    )


def _make_command(tmp_path: Path, slug: str) -> Command:
    path = tmp_path / f"{slug}.md"
    path.write_text(f"{slug}!", encoding="utf-8")
    return Command(name=slug, slug=slug, path=path, total_bytes=path.stat().st_size)


def _age_file(path: Path, *, days: int) -> None:
    ts = time.time() - days * 86400
    os.utime(path, (ts, ts))


def _active_index(days_ago: int = 1) -> ActivityIndex:
    last = NOW - timedelta(days=days_ago)
    return ActivityIndex(last_active_overall=last)


# --- unused_command -----------------------------------------------------


def test_unused_command_flags_slug_never_invoked(tmp_path: Path) -> None:
    cmd = _make_command(tmp_path, "ship")
    state = _state(commands=(cmd,), activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(), now=NOW)
    ship = [f for f in findings if f.type == "unused_command"]
    assert len(ship) == 1
    assert ship[0].id == "unused_command:ship"
    assert ship[0].auto_checked is True
    assert ship[0].action.primitive == "delete_file"


def test_unused_command_ignores_recent_use(tmp_path: Path) -> None:
    cmd = _make_command(tmp_path, "ship")
    recent = NOW - timedelta(days=10)
    activity = ActivityIndex(
        last_active_overall=recent,
        slash_command_last_used=MappingProxyType({"ship": recent}),
    )
    state = _state(commands=(cmd,), activity=activity)
    findings = detect(state, state.global_scope.activity, Thresholds(unused_days=90), now=NOW)
    assert [f for f in findings if f.type == "unused_command"] == []


def test_unused_command_flags_stale_use(tmp_path: Path) -> None:
    cmd = _make_command(tmp_path, "ship")
    old = NOW - timedelta(days=200)
    activity = ActivityIndex(
        last_active_overall=NOW - timedelta(days=1),
        slash_command_last_used=MappingProxyType({"ship": old}),
    )
    state = _state(commands=(cmd,), activity=activity)
    findings = detect(state, state.global_scope.activity, Thresholds(unused_days=90), now=NOW)
    ship = [f for f in findings if f.type == "unused_command"]
    assert len(ship) == 1
    assert "200d" in ship[0].reason


# --- unused_skill / unused_agent ----------------------------------------


def _make_skill(tmp_path: Path, slug: str) -> Skill:
    d = tmp_path / "skills" / slug
    d.mkdir(parents=True)
    skill_md = d / "SKILL.md"
    skill_md.write_text("---\nname: {slug}\n---\nbody", encoding="utf-8")
    return Skill(
        name=slug,
        slug=slug,
        directory=d,
        skill_md_path=skill_md,
        description=None,
        model=None,
        frontmatter_bytes=32,
        body_bytes=4,
        total_dir_bytes=32,
    )


def test_unused_skill_emits_one_finding_per_skill_even_without_activity(tmp_path: Path) -> None:
    # Fresh skills, no activity record — detector still surfaces them so
    # the user can triage. (v0.1 intentional: age/idle gates removed.)
    skill_a = _make_skill(tmp_path, "fashion")
    skill_b = _make_skill(tmp_path, "pitch")
    state = _state(skills=(skill_a, skill_b), activity=ActivityIndex())
    findings = [f for f in detect(state, state.global_scope.activity, Thresholds(), now=NOW)
                if f.type == "unused_skill"]
    assert {f.id for f in findings} == {"unused_skill:fashion", "unused_skill:pitch"}
    # No @mention ever → pre-checked.
    assert all(f.auto_checked for f in findings)
    # Every finding carries a concrete token-savings estimate for the UI.
    assert all(f.token_savings is not None and f.token_savings > 0 for f in findings)


def test_unused_skill_auto_check_cleared_when_mentioned_in_history(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path, "fashion")
    recent_mention = NOW - timedelta(days=10)
    activity = ActivityIndex(
        last_active_overall=NOW - timedelta(days=1),
        at_mention_last_used=MappingProxyType({"fashion": recent_mention}),
    )
    state = _state(skills=(skill,), activity=activity)
    findings = [f for f in detect(state, state.global_scope.activity, Thresholds(), now=NOW)
                if f.type == "unused_skill"]
    assert len(findings) == 1
    # Still emitted so the user can choose, but requires an explicit opt-in.
    assert findings[0].auto_checked is False
    assert "opt in" in findings[0].reason.lower()


def test_unused_skill_emits_regardless_of_install_activity(tmp_path: Path) -> None:
    # Historically this was gated on install-active; now it isn't.
    skill = _make_skill(tmp_path, "fashion")
    state = _state(
        skills=(skill,),
        activity=ActivityIndex(last_active_overall=NOW - timedelta(days=400)),
    )
    findings = [f for f in detect(state, state.global_scope.activity, Thresholds(), now=NOW)
                if f.type == "unused_skill"]
    assert len(findings) == 1


def _make_agent(tmp_path: Path, slug: str) -> Agent:
    d = tmp_path / "agents"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text("---\nname: planner\n---\n", encoding="utf-8")
    return Agent(
        name=slug,
        slug=slug,
        path=p,
        description=None,
        frontmatter_bytes=22,
        body_bytes=0,
    )


def test_unused_agent_emits_one_finding_per_agent(tmp_path: Path) -> None:
    agent_a = _make_agent(tmp_path, "planner")
    agent_b = _make_agent(tmp_path, "reviewer")
    state = _state(agents=(agent_a, agent_b), activity=ActivityIndex())
    findings = [f for f in detect(state, state.global_scope.activity, Thresholds(), now=NOW)
                if f.type == "unused_agent"]
    assert {f.id for f in findings} == {"unused_agent:planner", "unused_agent:reviewer"}
    assert all(f.auto_checked for f in findings)
    assert all(f.token_savings is not None for f in findings)
    assert all(f.action.primitive == "delete_file" for f in findings)


def test_unused_agent_clears_auto_check_when_mentioned(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path, "planner")
    activity = ActivityIndex(
        last_active_overall=NOW - timedelta(days=1),
        at_mention_last_used=MappingProxyType({"planner": NOW - timedelta(days=3)}),
    )
    state = _state(agents=(agent,), activity=activity)
    findings = [f for f in detect(state, state.global_scope.activity, Thresholds(), now=NOW)
                if f.type == "unused_agent"]
    assert len(findings) == 1
    assert findings[0].auto_checked is False


# --- dead_mcp / unused_mcp -----------------------------------------------


def _session_with_tools(*tool_names: str) -> SessionSystemBlock:
    tools = tuple({"name": name, "description": "...", "input_schema": {}} for name in tool_names)
    return SessionSystemBlock(
        session_path=Path("/fake/.claude/projects/x/a.jsonl"),
        system_text="sys",
        tools_json="[...]",
        tools=tools,
        system_tokens=5,
        tools_tokens=5,
    )


def test_dead_mcp_flags_configured_but_absent_from_session() -> None:
    config = ClaudeConfig(
        mcp_servers=MappingProxyType(
            {
                "github": McpServer(name="github"),
                "notion": McpServer(name="notion"),
            }
        )
    )
    session = _session_with_tools("mcp__github__list_repos", "Read")
    state = _state(config=config, latest_session=session, activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(), now=NOW)
    dead = [f for f in findings if f.type == "dead_mcp"]
    assert [f.id for f in dead] == ["dead_mcp:notion"]
    assert dead[0].auto_checked is False
    assert dead[0].action.primitive == "comment_out_mcp"
    assert dead[0].action.server_name == "notion"


def test_dead_mcp_skipped_when_no_session() -> None:
    config = ClaudeConfig(mcp_servers=MappingProxyType({"github": McpServer(name="github")}))
    state = _state(config=config, latest_session=None, activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(), now=NOW)
    assert [f for f in findings if f.type == "dead_mcp"] == []


def test_unused_mcp_is_noop_in_v0_1() -> None:
    config = ClaudeConfig(mcp_servers=MappingProxyType({"github": McpServer(name="github")}))
    session = _session_with_tools("mcp__github__list_repos")
    state = _state(config=config, latest_session=session, activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(), now=NOW)
    assert [f for f in findings if f.type == "unused_mcp"] == []


# --- stale_plugin / disabled_plugin_residue ------------------------------


def _plugin(name: str, marketplace: str | None, installed_at_iso: str) -> InstalledPlugin:
    return InstalledPlugin(
        name=name,
        marketplace=marketplace,
        version="1.0.0",
        install_path=None,
        installed_at=installed_at_iso,
        git_commit_sha=None,
    )


def test_stale_plugin_flags_old_enabled_plugin() -> None:
    plugin = _plugin("superpower", "antonin", "2025-10-01T00:00:00Z")
    settings = Settings(
        enabled_plugins=MappingProxyType({"superpower@antonin": True}),
    )
    state = _state(plugins=(plugin,), settings=settings, activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(stale_plugin_days=90), now=NOW)
    stale = [f for f in findings if f.type == "stale_plugin"]
    assert len(stale) == 1
    assert stale[0].id == "stale_plugin:superpower@antonin"
    assert stale[0].action.primitive == "disable_plugin"
    assert stale[0].auto_checked is False


def test_stale_plugin_ignores_recent_plugin() -> None:
    plugin = _plugin("fresh", None, (NOW - timedelta(days=10)).isoformat().replace("+00:00", "Z"))
    settings = Settings(enabled_plugins=MappingProxyType({"fresh": True}))
    state = _state(plugins=(plugin,), settings=settings, activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(stale_plugin_days=90), now=NOW)
    assert [f for f in findings if f.type == "stale_plugin"] == []


def test_disabled_plugin_residue_flag_only_when_recent() -> None:
    plugin = _plugin(
        "superpower", "antonin", (NOW - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    )
    settings = Settings(enabled_plugins=MappingProxyType({"superpower@antonin": False}))
    state = _state(plugins=(plugin,), settings=settings, activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(stale_plugin_days=90), now=NOW)
    residue = [f for f in findings if f.type == "disabled_plugin_residue"]
    assert len(residue) == 1
    assert residue[0].action.primitive == "flag_only"


def test_disabled_plugin_residue_offers_uninstall_once_aged() -> None:
    plugin = _plugin("old-plug", None, "2024-01-01T00:00:00Z")
    settings = Settings(enabled_plugins=MappingProxyType({"old-plug": False}))
    state = _state(plugins=(plugin,), settings=settings, activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(stale_plugin_days=90), now=NOW)
    residue = [f for f in findings if f.type == "disabled_plugin_residue"]
    assert len(residue) == 1
    assert residue[0].action.primitive == "uninstall_plugin"


# --- missing_claudeignore ------------------------------------------------


def test_missing_claudeignore_flags_project_with_node_modules(tmp_path: Path) -> None:
    project_dir = tmp_path / "draper"
    project_dir.mkdir()
    (project_dir / "node_modules").mkdir()
    config = ClaudeConfig(
        projects=MappingProxyType(
            {
                project_dir.resolve(): _project_record(project_dir.resolve()),
            }
        )
    )
    state = _state(config=config, activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(), now=NOW)
    miss = [f for f in findings if f.type == "missing_claudeignore"]
    assert len(miss) == 1
    assert miss[0].scope.project_path == project_dir.resolve()
    assert miss[0].action.primitive == "flag_only"


def test_missing_claudeignore_skipped_when_ignore_present(tmp_path: Path) -> None:
    project_dir = tmp_path / "draper"
    project_dir.mkdir()
    (project_dir / "node_modules").mkdir()
    (project_dir / ".claudeignore").write_text("node_modules\n", encoding="utf-8")
    config = ClaudeConfig(
        projects=MappingProxyType(
            {project_dir.resolve(): _project_record(project_dir.resolve())}
        )
    )
    state = _state(config=config, activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(), now=NOW)
    assert [f for f in findings if f.type == "missing_claudeignore"] == []


def test_missing_claudeignore_ignores_stale_project_paths(tmp_path: Path) -> None:
    nowhere = tmp_path / "gone"  # directory does not exist
    config = ClaudeConfig(
        projects=MappingProxyType({nowhere: _project_record(nowhere)})
    )
    state = _state(config=config, activity=_active_index())
    findings = detect(state, state.global_scope.activity, Thresholds(), now=NOW)
    assert [f for f in findings if f.type == "missing_claudeignore"] == []


def _project_record(path: Path):  # type: ignore[no-untyped-def]
    from unclog.scan.config import ProjectRecord

    return ProjectRecord(path=path)
