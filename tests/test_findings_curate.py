from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from unclog.findings.curate import build_curate_findings
from unclog.scan.filesystem import Agent, Skill
from unclog.state import GlobalScope, InstallationState


def _state_with(
    *,
    agents: tuple[Agent, ...] = (),
    skills: tuple[Skill, ...] = (),
    claude_home: Path = Path("/tmp/claude"),
) -> InstallationState:
    gs = GlobalScope(
        claude_home=claude_home,
        config=None,
        settings=None,
        claude_md_bytes=0,
        claude_md_text="",
        claude_local_md_bytes=0,
        claude_local_md_text="",
        agents=agents,
        skills=skills,
    )
    return InstallationState(
        generated_at=datetime.now(tz=UTC),
        claude_home=claude_home,
        global_scope=gs,
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


def test_build_curate_findings_empty_state_returns_empty() -> None:
    assert build_curate_findings(_state_with()) == []


def test_build_curate_findings_enumerates_agents_and_skills() -> None:
    state = _state_with(
        agents=(_agent("alpha", "does alpha things"),),
        skills=(_skill("beta", "beta skill description"),),
    )
    findings = build_curate_findings(state)
    assert len(findings) == 2
    types = {f.type for f in findings}
    assert types == {"agent_inventory", "skill_inventory"}


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
    assert findings[0].id == "agent_inventory:huge"
    assert findings[1].id == "agent_inventory:tiny"
    # Every curate finding must be explicitly opt-in.
    assert all(f.auto_checked is False for f in findings)


def test_build_curate_findings_uses_delete_file_action_on_correct_target(tmp_path: Path) -> None:
    agent_path = tmp_path / "agents" / "foo.md"
    skill_dir = tmp_path / "skills" / "bar"
    state = _state_with(
        agents=(_agent("foo", "desc", path=agent_path),),
        skills=(_skill("bar", "desc", directory=skill_dir),),
    )
    findings = build_curate_findings(state)
    by_type = {f.type: f for f in findings}
    assert by_type["agent_inventory"].action.primitive == "delete_file"
    assert by_type["agent_inventory"].action.path == agent_path
    assert by_type["skill_inventory"].action.primitive == "delete_file"
    assert by_type["skill_inventory"].action.path == skill_dir


def test_build_curate_findings_handles_missing_description() -> None:
    state = _state_with(agents=(replace(_agent("x", ""), description=None),))
    findings = build_curate_findings(state)
    assert len(findings) == 1
    assert findings[0].reason == "no description"
