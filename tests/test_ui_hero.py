from __future__ import annotations

from rich.console import Console

from unclog.ui.hero import CompositionRow, render_hero, render_top_contributors
from unclog.ui.theme import ACCENT


def _capture(renderable: object) -> str:
    console = Console(
        record=True, width=120, force_terminal=True, no_color=False, color_system="truecolor"
    )
    console.print(renderable)
    return console.export_text()


def _capture_ansi(renderable: object) -> str:
    console = Console(record=True, width=120, force_terminal=True, color_system="truecolor")
    console.print(renderable)
    return console.export_text(styles=True)


def _rgb(hex_colour: str) -> str:
    h = hex_colour.lstrip("#")
    return f"{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}"


def test_hero_renders_number_in_plain_english() -> None:
    out = _capture(render_hero(42180))
    assert "42,180" in out
    assert "tokens in your Claude Code baseline" in out


def test_hero_number_uses_accent_colour() -> None:
    ansi = _capture_ansi(render_hero(8000))
    assert _rgb(ACCENT) in ansi


def test_top_contributors_renders_aggregate_and_mcp_rows() -> None:
    composition = [
        CompositionRow(kind="mcp", label="github", tokens=8000),
        CompositionRow(kind="skills", label="22 skills", tokens=1200),
        CompositionRow(kind="agents", label="5 agents", tokens=800),
    ]
    out = _capture(render_top_contributors(composition))
    assert "mcp github" in out
    assert "22 skills" in out
    assert "5 agents" in out
    assert "8,000 tok" in out
    assert "1,200 tok" in out
    assert "800 tok" in out


def test_top_contributors_includes_scope_label() -> None:
    composition = [
        CompositionRow(
            kind="mcp",
            label="notion",
            tokens=3000,
            scope_label="project:/Users/tom/proj",
        ),
    ]
    out = _capture(render_top_contributors(composition))
    assert "project:/Users/tom/proj" in out


def test_top_contributors_empty_composition() -> None:
    out = _capture(render_top_contributors([]))
    assert "no measurable composition" in out


def test_top_contributors_collapses_overflow() -> None:
    composition = [
        CompositionRow(kind="mcp", label=f"m{i}", tokens=100 - i) for i in range(7)
    ]
    out = _capture(render_top_contributors(composition))
    assert "+2 more" in out
