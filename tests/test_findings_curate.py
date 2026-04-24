from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from unclog.findings.curate import build_curate_findings
from unclog.scan.config import ClaudeConfig, McpServer, ProjectRecord
from unclog.scan.filesystem import Agent, Command, Skill
from unclog.state import InstallationState


def _state_with(
    *,
    agents: tuple[Agent, ...] = (),
    skills: tuple[Skill, ...] = (),
    commands: tuple[Command, ...] = (),
    config: ClaudeConfig | None = None,
    mcp_session_tokens: dict[str, int] | None = None,
    claude_home: Path = Path("/tmp/claude"),
) -> InstallationState:
    return InstallationState(
        generated_at=datetime.now(tz=UTC),
        claude_home=claude_home,
        config=config,
        agents=agents,
        skills=skills,
        commands=commands,
        mcp_session_tokens=MappingProxyType(dict(mcp_session_tokens or {})),
    )


def _agent(slug: str, description: str, path: Path | None = None) -> Agent:
    return Agent(
        name=slug,
        slug=slug,
        path=path or Path(f"/tmp/claude/agents/{slug}.md"),
        description=description,
        frontmatter_bytes=len(description),
        body_bytes=100,
    )


def _skill(slug: str, description: str, directory: Path | None = None) -> Skill:
    d = directory or Path(f"/tmp/claude/skills/{slug}")
    return Skill(
        name=slug,
        slug=slug,
        directory=d,
        skill_md_path=d / "SKILL.md",
        description=description,
        model=None,
        frontmatter_bytes=len(description),
        body_bytes=100,
        total_dir_bytes=500,
    )


def _command(slug: str, description: str | None, path: Path | None = None) -> Command:
    return Command(
        name=slug,
        slug=slug,
        path=path or Path(f"/tmp/claude/commands/{slug}.md"),
        description=description,
        frontmatter_bytes=len(description) if description else 0,
        body_bytes=50,
    )


def test_build_curate_findings_empty_state_returns_empty() -> None:
    assert build_curate_findings(_state_with()) == []


def test_build_curate_findings_enumerates_agents_and_skills() -> None:
    state = _state_with(
        agents=(_agent("alpha", "does alpha things"),),
        skills=(_skill("beta", "beta skill description"),),
    )
    findings = build_curate_findings(state)
    assert {f.type for f in findings} == {"agent_inventory", "skill_inventory"}


def test_build_curate_findings_sorts_by_tokens_descending() -> None:
    state = _state_with(
        agents=(
            _agent("tiny", "x"),
            _agent(
                "huge",
                "a very long and elaborate description that will cost significantly "
                "more tokens than the short one because it just keeps going and going",
            ),
        ),
    )
    findings = build_curate_findings(state)
    assert findings[0].id == "agent:huge"
    assert findings[1].id == "agent:tiny"


def test_build_curate_findings_uses_delete_file_action_on_correct_target(tmp_path: Path) -> None:
    agent_path = tmp_path / "agents" / "foo.md"
    skill_dir = tmp_path / "skills" / "bar"
    command_path = tmp_path / "commands" / "baz.md"
    state = _state_with(
        agents=(_agent("foo", "desc", path=agent_path),),
        skills=(_skill("bar", "desc", directory=skill_dir),),
        commands=(_command("baz", "desc", path=command_path),),
    )
    findings = build_curate_findings(state)
    by_type = {f.type: f for f in findings}
    assert by_type["agent_inventory"].action.primitive == "delete_file"
    assert by_type["agent_inventory"].action.path == agent_path
    assert by_type["skill_inventory"].action.primitive == "delete_file"
    assert by_type["skill_inventory"].action.path == skill_dir
    assert by_type["command_inventory"].action.primitive == "delete_file"
    assert by_type["command_inventory"].action.path == command_path


def test_build_curate_findings_enumerates_commands() -> None:
    state = _state_with(commands=(_command("ship", "Ship the current branch"),))
    findings = build_curate_findings(state)
    assert len(findings) == 1
    assert findings[0].type == "command_inventory"
    assert findings[0].id == "command:ship"
    assert findings[0].title == "ship"
    assert findings[0].token_savings is not None and findings[0].token_savings > 0


def test_build_curate_findings_handles_command_without_description() -> None:
    state = _state_with(commands=(_command("plain", None),))
    findings = build_curate_findings(state)
    assert len(findings) == 1
    # Bare name still yields a small positive token count from tiktoken.
    assert findings[0].token_savings is not None


def test_build_curate_findings_includes_every_mcp_as_comment_out() -> None:
    """Every declared MCP (local + remote) surfaces as a curate row."""
    http = McpServer(
        name="polymarket-docs",
        raw=MappingProxyType({"type": "http", "url": "https://docs.polymarket.com/mcp"}),
    )
    stdio = McpServer(
        name="Roblox_Studio",
        command="/path/cmd",
        raw=MappingProxyType({"type": "stdio", "command": "/path/cmd"}),
    )
    config = ClaudeConfig(
        mcp_servers=MappingProxyType({"polymarket-docs": http, "Roblox_Studio": stdio}),
    )
    findings = build_curate_findings(_state_with(config=config))
    mcps = [f for f in findings if f.type == "mcp_inventory"]
    assert sorted(f.action.server_name for f in mcps) == ["Roblox_Studio", "polymarket-docs"]
    assert all(f.action.primitive == "comment_out_mcp" for f in mcps)


def test_build_curate_findings_attaches_session_tokens_to_mcp() -> None:
    """When session attribution is present, the finding carries token_savings."""
    stdio = McpServer(name="Roblox_Studio", command="x")
    config = ClaudeConfig(mcp_servers=MappingProxyType({"Roblox_Studio": stdio}))
    findings = build_curate_findings(
        _state_with(config=config, mcp_session_tokens={"Roblox_Studio": 4500}),
    )
    mcp = next(f for f in findings if f.type == "mcp_inventory")
    assert mcp.token_savings == 4500


def test_build_curate_findings_dedupes_mcp_across_projects_preferring_global() -> None:
    """Same MCP in multiple projects collapses to one row; global scope wins."""
    stdio = McpServer(name="shared", command="x")
    p1 = ProjectRecord(
        path=Path("/a"), mcp_servers=MappingProxyType({"shared": stdio})
    )
    p2 = ProjectRecord(
        path=Path("/b"), mcp_servers=MappingProxyType({"shared": stdio})
    )
    config = ClaudeConfig(
        mcp_servers=MappingProxyType({"shared": stdio}),
        projects=MappingProxyType({Path("/a"): p1, Path("/b"): p2}),
    )
    findings = build_curate_findings(_state_with(config=config))
    mcps = [f for f in findings if f.type == "mcp_inventory"]
    assert len(mcps) == 1
    assert mcps[0].scope.kind == "global"
