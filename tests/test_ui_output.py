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
    warnings: tuple[str, ...] = (),
    latest_session: SessionSystemBlock | None = None,
    activity: ActivityIndex | None = None,
) -> InstallationState:
    home = Path("/fake/.claude")
    return InstallationState(
        generated_at=datetime(2026, 4, 17, 18, 42, tzinfo=UTC),
        claude_home=home,
        global_scope=GlobalScope(
            claude_home=home,
            config=config,
            settings=Settings(),
            claude_md_bytes=len(claude_md_text.encode("utf-8")),
            claude_md_text=claude_md_text,
            claude_local_md_bytes=0,
            claude_local_md_text="",
            skills=skills,
            agents=agents,
            latest_session=latest_session,
            activity=activity if activity is not None else ActivityIndex(),
        ),
        warnings=warnings,
    )


def test_build_report_schema_fields() -> None:
    state = _make_state()
    report = build_report(state)
    assert report["schema"] == SCHEMA_ID
    assert report["generated_at"] == "2026-04-17T18:42:00Z"
    assert report["claude_home"] == "/fake/.claude"
    assert report["baseline"]["tokens_source"] in {"tiktoken", "session+tiktoken"}
    assert report["baseline"]["tier"] in {"lean", "typical", "clogged"}
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
    assert "CLAUDE.md files found" in out
    assert "global CLAUDE.md" in out
    assert "proj" in out
    assert "project CLAUDE.md" in out
    assert "totals:" in out


def test_list_claude_md_flags_missing_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unclog.app import run_scan

    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

    # Seed ~/.claude.json with a stale project path.
    import json as _json

    gone = tmp_path / "gone"
    (claude_home / ".claude.json").write_text(
        _json.dumps({"projects": {str(gone): {}}}),
        encoding="utf-8",
    )
    state = run_scan(cwd=tmp_path)
    out = render_claude_md_listing_plain(state)
    assert "path missing on disk" in out


def test_cli_list_claude_md_exits_after_listing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    result = runner.invoke(app, ["--list-claude-md", "--plain"])
    assert result.exit_code == 0
    assert "CLAUDE.md files found" in result.stdout
    # The listing short-circuits before the normal scan summary.
    assert "baseline:" not in result.stdout
