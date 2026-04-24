from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from rich.console import Console

from unclog.findings.base import Action, Finding, Scope
from unclog.state import InstallationState
from unclog.ui.output import baseline_tokens, build_composition, render_header


def _state(tmp_path: Path) -> InstallationState:
    home = tmp_path / ".claude"
    home.mkdir(exist_ok=True)
    return InstallationState(
        generated_at=datetime(2026, 4, 19, 12, 0, tzinfo=UTC),
        claude_home=home,
        config=None,
        mcp_session_tokens=MappingProxyType({}),
    )


def _inventory(
    finding_type: str, slug: str, tokens: int | None, *, scope: Scope | None = None
) -> Finding:
    return Finding(
        id=f"{finding_type}:{slug}",
        type=finding_type,  # type: ignore[arg-type]
        title=slug,
        scope=scope or Scope(kind="global"),
        action=Action(primitive="delete_file", path=Path(f"/tmp/{slug}")),
        token_savings=tokens,
    )


def _mcp(name: str, tokens: int | None, *, scope: Scope | None = None) -> Finding:
    return Finding(
        id=f"mcp:{name}",
        type="mcp_inventory",
        title=name,
        scope=scope or Scope(kind="global"),
        action=Action(primitive="remove_mcp", server_name=name),
        token_savings=tokens,
    )


# -- build_composition ------------------------------------------------------


def test_build_composition_empty_state_returns_empty() -> None:
    assert build_composition([]) == []


def test_build_composition_rolls_up_aggregate_kinds() -> None:
    findings = [
        _inventory("agent_inventory", "a1", 100),
        _inventory("agent_inventory", "a2", 150),
        _inventory("skill_inventory", "s1", 80),
        _inventory("command_inventory", "c1", 40),
    ]
    rows = build_composition(findings)
    by_kind = {r.kind: r for r in rows}
    assert by_kind["agents"].label == "2 agents"
    assert by_kind["agents"].tokens == 250
    assert by_kind["skills"].label == "1 skills"
    assert by_kind["skills"].tokens == 80
    assert by_kind["commands"].tokens == 40


def test_build_composition_emits_one_row_per_mcp_with_tokens() -> None:
    findings = [
        _mcp("polymarket", 1200),
        _mcp("roblox", 800),
    ]
    rows = build_composition(findings)
    labels = [r.label for r in rows if r.kind == "mcp"]
    assert sorted(labels) == ["polymarket", "roblox"]


def test_build_composition_sorted_descending_by_tokens() -> None:
    findings = [
        _mcp("small", 50),
        _mcp("big", 5000),
        _mcp("mid", 500),
    ]
    rows = build_composition(findings)
    tokens = [r.tokens for r in rows]
    assert tokens == sorted(tokens, reverse=True)


def test_build_composition_drops_unmeasured_mcps() -> None:
    """Never-invoked MCPs have no session tokens — they don't belong in the hero."""
    findings = [
        _mcp("ghost", 0),
        _mcp("missing", None),
        _mcp("real", 100),
    ]
    rows = build_composition(findings)
    assert [r.label for r in rows if r.kind == "mcp"] == ["real"]


def test_build_composition_tags_project_scope_on_mcp_rows() -> None:
    project = Path("/Users/tom/proj")
    findings = [_mcp("notion", 500, scope=Scope(kind="project", project_path=project))]
    rows = build_composition(findings)
    assert rows[0].scope_label == f"project:{project}"


# -- baseline_tokens --------------------------------------------------------


def test_baseline_tokens_empty_is_zero() -> None:
    assert baseline_tokens([]) == 0


def test_baseline_tokens_sums_every_finding_with_tokens() -> None:
    findings = [
        _inventory("agent_inventory", "a", 100),
        _mcp("x", 250),
        _mcp("y", None),  # unmeasured — contributes 0
    ]
    assert baseline_tokens(findings) == 350


# -- render_header ----------------------------------------------------------


def test_render_header_prints_welcome_and_baseline(tmp_path: Path) -> None:
    findings = [_mcp("polymarket", 1200)]
    console = Console(record=True, width=120, color_system=None)
    render_header(_state(tmp_path), findings, console)
    output = console.export_text()
    assert "unclog" in output
    assert "polymarket" in output or "1,200" in output


def test_render_header_does_not_raise_on_empty_findings(tmp_path: Path) -> None:
    console = Console(record=True, width=120, color_system=None)
    render_header(_state(tmp_path), [], console)  # must not raise
