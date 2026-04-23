from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from rich.console import Console

from unclog.scan.filesystem import Agent, Command, Skill
from unclog.state import InstallationState
from unclog.ui.output import baseline_tokens, build_composition, render_header


def _state(
    tmp_path: Path,
    *,
    agents: tuple[Agent, ...] = (),
    skills: tuple[Skill, ...] = (),
    commands: tuple[Command, ...] = (),
    mcp_session_tokens: dict[str, int] | None = None,
) -> InstallationState:
    home = tmp_path / ".claude"
    home.mkdir(exist_ok=True)
    return InstallationState(
        generated_at=datetime(2026, 4, 19, 12, 0, tzinfo=UTC),
        claude_home=home,
        config=None,
        settings=None,
        agents=agents,
        skills=skills,
        commands=commands,
        mcp_session_tokens=MappingProxyType(dict(mcp_session_tokens or {})),
    )


def _agent(slug: str, description: str) -> Agent:
    return Agent(
        name=slug,
        slug=slug,
        path=Path(f"/tmp/claude/agents/{slug}.md"),
        description=description,
        frontmatter_bytes=len(description),
        body_bytes=100,
    )


def _skill(slug: str, description: str) -> Skill:
    d = Path(f"/tmp/claude/skills/{slug}")
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


def _command(slug: str, description: str) -> Command:
    return Command(
        name=slug,
        slug=slug,
        path=Path(f"/tmp/claude/commands/{slug}.md"),
        description=description,
        frontmatter_bytes=len(description),
        body_bytes=50,
    )


# -- build_composition ------------------------------------------------------


def test_build_composition_empty_state_returns_empty(tmp_path: Path) -> None:
    assert build_composition(_state(tmp_path)) == []


def test_build_composition_emits_one_row_per_bucket(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        agents=(_agent("alpha", "does alpha things"),),
        skills=(_skill("beta", "beta skill description"),),
        commands=(_command("ship", "ships the branch"),),
        mcp_session_tokens={"polymarket": 1200, "roblox": 800},
    )
    sources = [e["source"] for e in build_composition(state)]
    assert "agents:descriptions (n=1)" in sources
    assert "skills:descriptions (n=1)" in sources
    assert "commands:descriptions (n=1)" in sources
    assert "mcp:polymarket" in sources
    assert "mcp:roblox" in sources


def test_build_composition_sorted_descending_by_tokens(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        mcp_session_tokens={"small": 50, "big": 5000, "mid": 500},
    )
    tokens = [e["tokens"] for e in build_composition(state)]
    assert tokens == sorted(tokens, reverse=True)


def test_build_composition_drops_zero_token_mcps(tmp_path: Path) -> None:
    """Never-invoked MCPs have no session tokens — they don't belong in the hero."""
    state = _state(tmp_path, mcp_session_tokens={"ghost": 0, "real": 100})
    sources = [e["source"] for e in build_composition(state)]
    assert sources == ["mcp:real"]


# -- baseline_tokens --------------------------------------------------------


def test_baseline_tokens_empty_is_zero(tmp_path: Path) -> None:
    assert baseline_tokens(_state(tmp_path)) == 0


def test_baseline_tokens_sums_every_contributor(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        mcp_session_tokens={"a": 100, "b": 250},
    )
    assert baseline_tokens(state) == 350


# -- render_header ----------------------------------------------------------


def test_render_header_prints_welcome_and_baseline(tmp_path: Path) -> None:
    state = _state(tmp_path, mcp_session_tokens={"polymarket": 1200})
    console = Console(record=True, width=120, color_system=None)
    render_header(state, console)
    output = console.export_text()
    assert "unclog" in output
    assert "polymarket" in output or "1,200" in output


def test_render_header_does_not_raise_on_empty_state(tmp_path: Path) -> None:
    console = Console(record=True, width=120, color_system=None)
    render_header(_state(tmp_path), console)  # must not raise
