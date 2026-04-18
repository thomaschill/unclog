from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from unclog.cli import app
from unclog.scan.config import ClaudeConfig, McpServer, ProjectRecord, Settings
from unclog.scan.filesystem import Agent, Skill
from unclog.scan.session import SessionSystemBlock
from unclog.scan.stats import ActivityIndex
from unclog.state import GlobalScope, InstallationState
from unclog.ui.output import (
    SCHEMA_ID,
    build_report,
    render_claude_md_listing_plain,
    render_json,
    render_plain,
)
from unclog.util.paths import claude_home

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    claude_home.cache_clear()


def _make_state(
    *,
    claude_md_text: str = "# Global rules\n" + ("Use yarn.\n" * 50),
    skills: tuple[Skill, ...] = (),
    agents: tuple[Agent, ...] = (),
    config: ClaudeConfig | None = None,
    settings: Settings | None = None,
    warnings: tuple[str, ...] = (),
    latest_session: SessionSystemBlock | None = None,
    activity: ActivityIndex | None = None,
    project_scopes: tuple = (),
    mcp_probes: dict | None = None,  # type: ignore[type-arg]
) -> InstallationState:
    from types import MappingProxyType

    home = Path("/fake/.claude")
    return InstallationState(
        generated_at=datetime(2026, 4, 17, 18, 42, tzinfo=UTC),
        claude_home=home,
        global_scope=GlobalScope(
            claude_home=home,
            config=config,
            settings=settings if settings is not None else Settings(),
            claude_md_bytes=len(claude_md_text.encode("utf-8")),
            claude_md_text=claude_md_text,
            claude_local_md_bytes=0,
            claude_local_md_text="",
            skills=skills,
            agents=agents,
            latest_session=latest_session,
            activity=activity if activity is not None else ActivityIndex(),
            mcp_probes=MappingProxyType(mcp_probes or {}),
        ),
        project_scopes=project_scopes,
        warnings=warnings,
    )


def test_build_report_schema_fields() -> None:
    state = _make_state()
    report = build_report(state)
    assert report["schema"] == SCHEMA_ID
    assert report["generated_at"] == "2026-04-17T18:42:00Z"
    assert report["claude_home"] == "/fake/.claude"
    assert report["baseline"]["tokens_source"] in {"tiktoken", "session+tiktoken"}
    assert report["findings"] == []


def test_build_report_measures_claude_md_with_tiktoken() -> None:
    state = _make_state(claude_md_text="hello world " * 200)
    report = build_report(state)
    claude_md_entry = next(e for e in report["composition"] if e["source"] == "global:CLAUDE.md")
    assert claude_md_entry["tokens_source"] == "tiktoken"
    assert claude_md_entry["tokens"] > 0
    assert report["baseline"]["attributed_tokens"] >= claude_md_entry["tokens"]


def test_build_report_marks_mcp_entries_unmeasured_without_session() -> None:
    config = ClaudeConfig(
        mcp_servers={"github": McpServer(name="github"), "notion": McpServer(name="notion")}
    )
    state = _make_state(config=config)
    report = build_report(state)
    mcp_entries = [e for e in report["composition"] if e["source"].startswith("mcp:")]
    assert {e["source"] for e in mcp_entries} == {"mcp:github", "mcp:notion"}
    assert all(e["tokens_source"] == "unmeasured" for e in mcp_entries)
    assert all(e["tokens"] is None for e in mcp_entries)
    assert report["baseline"]["unmeasured_sources"] == 2


def test_build_report_attributes_mcp_from_session_tools() -> None:
    config = ClaudeConfig(
        mcp_servers={"github": McpServer(name="github"), "notion": McpServer(name="notion")}
    )
    session = SessionSystemBlock(
        session_path=Path("/fake/.claude/projects/proj/abc.jsonl"),
        system_text="You are Claude.",
        tools_json='[{"name":"mcp__github__list"}]',
        tools=(
            {"name": "mcp__github__list_repos", "description": "lists", "input_schema": {}},
            {"name": "Read", "description": "built-in", "input_schema": {}},
        ),
        system_tokens=10,
        tools_tokens=25,
    )
    state = _make_state(config=config, latest_session=session)
    report = build_report(state)
    github = next(e for e in report["composition"] if e["source"] == "mcp:github")
    notion = next(e for e in report["composition"] if e["source"] == "mcp:notion")
    assert github["tokens_source"] == "session+tiktoken"
    assert github["tokens"] > 0
    # Notion is declared in config but absent from the session tools list.
    assert notion["tokens_source"] == "unmeasured"
    assert notion["tokens"] is None
    assert report["baseline"]["tokens_source"] == "session+tiktoken"
    assert report["baseline"]["estimated_tokens"] == session.total_tokens


def test_build_report_uses_probe_tokens_when_probe_ok() -> None:
    """When --probe-mcps ran, composition rows carry probe+tiktoken tokens."""
    from unclog.scan.mcp_probe import ProbeResult

    config = ClaudeConfig(mcp_servers={"github": McpServer(name="github")})
    probes = {"github": ProbeResult(name="github", ok=True, tool_count=5, tools_tokens=1234)}
    state = _make_state(config=config, mcp_probes=probes)
    report = build_report(state)
    github = next(e for e in report["composition"] if e["source"] == "mcp:github")
    assert github["tokens_source"] == "probe+tiktoken"
    assert github["tokens"] == 1234


def test_build_report_probe_failure_renders_unmeasured_with_note() -> None:
    """A failed probe keeps the composition row unmeasured, but notes the failure."""
    from unclog.scan.mcp_probe import ProbeResult

    config = ClaudeConfig(mcp_servers={"bad": McpServer(name="bad")})
    probes = {
        "bad": ProbeResult(
            name="bad",
            ok=False,
            error="command not found: bad-server",
            stderr_tail="bad-server: not found",
        ),
    }
    state = _make_state(config=config, mcp_probes=probes)
    report = build_report(state)
    row = next(e for e in report["composition"] if e["source"] == "mcp:bad")
    assert row["tokens_source"] == "unmeasured"
    assert row["tokens"] is None
    assert "probe failed" in row["note"]


def test_render_json_is_valid_json_with_stable_keys() -> None:
    state = _make_state()
    out = render_json(state)
    parsed = json.loads(out)
    for key in [
        "schema",
        "unclog_version",
        "generated_at",
        "claude_home",
        "baseline",
        "inventory",
        "composition",
        "findings",
        "warnings",
        "projects_audited",
    ]:
        assert key in parsed


def test_render_plain_includes_baseline_and_inventory() -> None:
    state = _make_state()
    out = render_plain(state)
    assert "unclog" in out
    assert "baseline" in out
    assert "inventory" in out


def test_render_plain_is_ascii_only() -> None:
    state = _make_state()
    out = render_plain(state)
    # spec §11.9: --plain enforces ASCII
    assert out == out.encode("ascii", errors="replace").decode("ascii")


def test_cli_plain_flag_outputs_ascii(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(app, ["--plain"])
    assert result.exit_code == 0
    assert "unclog" in result.stdout
    assert "baseline" in result.stdout


def test_cli_non_tty_falls_back_to_plain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    # CliRunner captures stdout as a StringIO, so isatty() is False.
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "unclog" in result.stdout


def test_build_report_includes_projects_audited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Use a real run_scan with --project so project_scopes populate.
    from unclog.app import run_scan

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# p\nbody\n", encoding="utf-8")
    state = run_scan(project=project, cwd=tmp_path)
    report = build_report(state)
    audited = report["projects_audited"]
    assert len(audited) == 1
    assert audited[0]["path"] == str(project.resolve())
    assert audited[0]["exists"] is True


def test_cli_json_flag_emits_valid_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(app, ["--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["schema"] == SCHEMA_ID
    assert parsed["findings"] == []


def test_build_report_emits_dead_mcp_finding_as_json() -> None:
    config = ClaudeConfig(
        mcp_servers={"github": McpServer(name="github"), "notion": McpServer(name="notion")}
    )
    # A session that loaded github's tools but not notion's.
    session = SessionSystemBlock(
        session_path=Path("/fake/.claude/projects/x/a.jsonl"),
        system_text="sys",
        tools_json="[]",
        tools=(
            {"name": "mcp__github__list_repos", "description": "...", "input_schema": {}},
        ),
        system_tokens=5,
        tools_tokens=5,
    )
    # Active install so dead_mcp isn't suppressed by dormant-install rules.
    activity = ActivityIndex(last_active_overall=datetime(2026, 4, 16, tzinfo=UTC))
    state = _make_state(config=config, latest_session=session, activity=activity)
    report = build_report(state)
    types = [f["type"] for f in report["findings"]]
    assert "dead_mcp" in types
    notion_finding = next(f for f in report["findings"] if f["id"] == "dead_mcp:notion")
    assert notion_finding["auto_checked"] is False
    assert notion_finding["action"]["primitive"] == "comment_out_mcp"
    assert notion_finding["action"]["server_name"] == "notion"


def test_build_report_surfaces_project_scoped_mcps_in_composition() -> None:
    config = ClaudeConfig(
        mcp_servers={},
        projects={
            Path("/tmp/a"): ProjectRecord(
                path=Path("/tmp/a"),
                mcp_servers={"polymarket-docs": McpServer(name="polymarket-docs")},
            ),
            Path("/tmp/b"): ProjectRecord(
                path=Path("/tmp/b"),
                mcp_servers={"Roblox_Studio": McpServer(name="Roblox_Studio")},
            ),
        },
    )
    state = _make_state(config=config)
    report = build_report(state)
    sources = {e["source"] for e in report["composition"]}
    assert "mcp:polymarket-docs" in sources
    assert "mcp:Roblox_Studio" in sources
    poly = next(e for e in report["composition"] if e["source"] == "mcp:polymarket-docs")
    assert poly["scope"] == "project:/tmp/a"
    assert poly["tokens_source"] == "unmeasured"


def test_build_report_collapses_shared_project_mcps() -> None:
    # Same MCP name + command + args declared in two projects → one row
    # labelled "project:2 projects" rather than two separate rows.
    shared = McpServer(name="shared", command="run", args=("--x",))
    config = ClaudeConfig(
        mcp_servers={},
        projects={
            Path("/tmp/a"): ProjectRecord(path=Path("/tmp/a"), mcp_servers={"shared": shared}),
            Path("/tmp/b"): ProjectRecord(path=Path("/tmp/b"), mcp_servers={"shared": shared}),
        },
    )
    state = _make_state(config=config)
    report = build_report(state)
    shared_rows = [e for e in report["composition"] if e["source"] == "mcp:shared"]
    assert len(shared_rows) == 1
    assert shared_rows[0]["scope"] == "project:2 projects"
    assert "declared in 2 projects" in (shared_rows[0]["note"] or "")


def test_inventory_counts_project_scoped_mcp_servers() -> None:
    config = ClaudeConfig(
        mcp_servers={},
        projects={
            Path("/tmp/a"): ProjectRecord(
                path=Path("/tmp/a"),
                mcp_servers={"sse-server": McpServer(name="sse-server")},
            ),
            Path("/tmp/b"): ProjectRecord(
                path=Path("/tmp/b"),
                mcp_servers={
                    "polymarket-docs": McpServer(name="polymarket-docs"),
                    "Roblox_Studio": McpServer(name="Roblox_Studio"),
                },
            ),
        },
    )
    state = _make_state(config=config)
    report = build_report(state)
    assert report["inventory"]["mcp_servers"] == 3
    assert report["inventory"]["mcp_servers_project"] == 3
    assert report["inventory"]["mcp_servers_global"] == 0


def test_render_plain_surfaces_project_scoped_mcp_label() -> None:
    config = ClaudeConfig(
        mcp_servers={"notion": McpServer(name="notion")},
        projects={
            Path("/tmp/a"): ProjectRecord(
                path=Path("/tmp/a"),
                mcp_servers={"sse-server": McpServer(name="sse-server")},
            ),
        },
    )
    state = _make_state(config=config)
    out = render_plain(state)
    assert "2 MCP servers (1 project-scoped)" in out


def _hook_record(event: str, command: str, *, scope: str = "global"):  # type: ignore[no-untyped-def]
    from unclog.scan.config import Hook

    return Hook(
        event=event,
        matcher=None,
        command=command,
        source_scope=scope,
        source_path=Path(f"/fake/{scope}/settings.json"),
    )


def _project_scope_with_hooks(path: Path, hooks: tuple):  # type: ignore[no-untyped-def]
    from unclog.scan.project import ProjectScope

    return ProjectScope(
        path=path,
        name=path.name,
        exists=True,
        claude_md_path=path / "CLAUDE.md",
        claude_md_text="",
        claude_md_bytes=0,
        claude_local_md_path=path / "CLAUDE.local.md",
        claude_local_md_text="",
        claude_local_md_bytes=0,
        has_claudeignore=False,
        hooks=hooks,
    )


def test_inventory_counts_hooks_across_scopes() -> None:
    settings = Settings(hooks=(_hook_record("SessionStart", "seed"),))
    project = _project_scope_with_hooks(
        Path("/tmp/a"),
        (
            _hook_record("UserPromptSubmit", "local", scope="project"),
            _hook_record("PreToolUse", "audit", scope="project"),
        ),
    )
    state = _make_state(settings=settings, project_scopes=(project,))
    report = build_report(state)
    assert report["inventory"]["hooks"] == 3
    assert report["inventory"]["hooks_global"] == 1
    assert report["inventory"]["hooks_project"] == 2


def test_render_plain_surfaces_hooks_label_with_project_breakdown() -> None:
    settings = Settings(hooks=(_hook_record("SessionStart", "g"),))
    project = _project_scope_with_hooks(
        Path("/tmp/a"), (_hook_record("UserPromptSubmit", "p", scope="project"),)
    )
    state = _make_state(settings=settings, project_scopes=(project,))
    out = render_plain(state)
    assert "2 hooks (1 project-scoped)" in out


def test_render_plain_surfaces_heavy_hook_informational_finding() -> None:
    settings = Settings(hooks=(_hook_record("SessionStart", "echo primed"),))
    state = _make_state(settings=settings)
    out = render_plain(state)
    assert "heavy_hook" in out or "SessionStart hook fires every prompt" in out


def test_projects_audited_includes_claude_md_token_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unclog.app import run_scan

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# project rules\n" + ("body\n" * 20), encoding="utf-8")
    state = run_scan(project=project, cwd=tmp_path)
    report = build_report(state)
    audited = report["projects_audited"][0]
    assert audited["claude_md_tokens"] > 0
    assert audited["claude_local_md_tokens"] == 0


def test_list_claude_md_plain_shows_global_and_projects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unclog.app import run_scan

    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    (claude_home / "CLAUDE.md").write_text("# global\n" + ("rule\n" * 10), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

    project = tmp_path / "proj"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# project\nhello\n", encoding="utf-8")

    state = run_scan(project=project, cwd=tmp_path)
    out = render_claude_md_listing_plain(state)
    assert "Auto-injected context files found" in out
    assert "global CLAUDE.md" in out
    assert "project CLAUDE.md" in out
    assert "auto-memory" in out
    assert "proj" in out
    assert "tok" in out
    assert "totals:" in out


def test_list_claude_md_plain_includes_auto_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Auto-memory MEMORY.md is discovered under ~/.claude/projects/<encoded>/memory/."""
    from unclog.app import run_scan
    from unclog.util.paths import encode_project_path

    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

    project = tmp_path / "proj"
    project.mkdir()

    memory_dir = claude_home / "projects" / encode_project_path(project) / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text(
        "- [user](user.md) — Tom, senior engineer\n" * 30,
        encoding="utf-8",
    )

    state = run_scan(project=project, cwd=tmp_path)
    out = render_claude_md_listing_plain(state)
    # Memory file should show up in the memory section.
    assert "MEMORY.md" in out
    # Memory contribution should roll up into the total.
    project_scope = state.project_scopes[0]
    assert project_scope.memory_md_text != ""
    assert project_scope.memory_md_bytes > 0


def test_composition_includes_auto_memory_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The baseline composition should surface auto-memory as its own row."""
    from unclog.app import run_scan
    from unclog.util.paths import encode_project_path

    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

    project = tmp_path / "proj"
    project.mkdir()
    memory_dir = claude_home / "projects" / encode_project_path(project) / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("line\n" * 50, encoding="utf-8")

    state = run_scan(project=project, cwd=tmp_path)
    report = build_report(state)
    memory_entries = [e for e in report["composition"] if e["source"].startswith("auto-memory")]
    assert len(memory_entries) == 1
    assert memory_entries[0]["tokens"] > 0
    assert memory_entries[0]["tokens_source"] == "tiktoken"


def test_list_claude_md_flags_missing_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stale entries in ~/.claude.json should surface as path_missing rows."""
    from unclog.app import run_scan
    from unclog.scan.config import ClaudeConfig, ProjectRecord

    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    missing_path = tmp_path / "does-not-exist"
    config = ClaudeConfig(
        projects={missing_path: ProjectRecord(path=missing_path)},
    )
    state = _make_state(config=config)
    # _make_state doesn't run _scan_projects; build one manually.
    from unclog.scan.project import ProjectScope

    missing_scope = ProjectScope(
        path=missing_path,
        name="does-not-exist",
        exists=False,
        claude_md_path=missing_path / "CLAUDE.md",
        claude_md_text="",
        claude_md_bytes=0,
        claude_local_md_path=missing_path / "CLAUDE.local.md",
        claude_local_md_text="",
        claude_local_md_bytes=0,
        has_claudeignore=False,
    )
    from dataclasses import replace

    state = replace(state, project_scopes=(missing_scope,))
    out = render_claude_md_listing_plain(state)
    assert "path missing" in out.lower()
    del run_scan  # unused import guard


def test_cli_list_claude_md_exits_after_listing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(app, ["--list-claude-md"])
    assert result.exit_code == 0
    assert "Auto-injected context files found" in result.stdout
    # Should NOT run the normal report.
    assert "baseline" not in result.stdout


def test_render_plain_lists_findings_with_selection_markers() -> None:
    config = ClaudeConfig(mcp_servers={"notion": McpServer(name="notion")})
    session = SessionSystemBlock(
        session_path=Path("/fake/.claude/projects/x/a.jsonl"),
        system_text="sys",
        tools_json="[]",
        tools=(),
        system_tokens=1,
        tools_tokens=0,
    )
    activity = ActivityIndex(last_active_overall=datetime(2026, 4, 16, tzinfo=UTC))
    state = _make_state(config=config, latest_session=session, activity=activity)
    out = render_plain(state)
    assert "findings:" in out
    # Dead MCP is opt-in, so it renders with the empty marker.
    assert "[ ]" in out
    assert "dead_mcp" not in out  # detector emits a human title, not the type string
    assert "notion" in out
