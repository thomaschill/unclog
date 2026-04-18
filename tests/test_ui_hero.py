from __future__ import annotations

from rich.console import Console

from unclog.ui.hero import (
    DEFAULT_TREEMAP_WIDTH,
    render_hero,
    render_treemap,
)
from unclog.ui.theme import ACCENT
from unclog.ui.wordmark import wordmark


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
    """Hero is plain English: ``N,NNN tokens in your Claude Code baseline``.

    Provenance (session vs filesystem) and unmeasured-MCP footnotes were
    removed in the v0.1 UX pass — the composition block below carries
    that information via per-row token counts vs ``—`` placeholders.
    """
    baseline = {
        "estimated_tokens": 42180,
        "tokens_source": "session+tiktoken",
        "attributed_tokens": 41000,
        "unmeasured_sources": 0,
    }
    out = _capture(render_hero(baseline))
    assert "42,180" in out
    assert "tokens in your Claude Code baseline" in out


def test_hero_number_uses_accent_colour() -> None:
    baseline = {
        "estimated_tokens": 8000,
        "tokens_source": "tiktoken",
        "attributed_tokens": 8000,
        "unmeasured_sources": 0,
    }
    ansi = _capture_ansi(render_hero(baseline))
    assert _rgb(ACCENT) in ansi


def test_hero_does_not_surface_provenance_jargon() -> None:
    """Regression: hero must not carry jargon footnotes.

    The old hero tacked on "from files (no session yet)" and
    "N MCP unmeasured" — both were opaque to users and are now
    represented by the composition block instead.
    """
    baseline = {
        "estimated_tokens": 80000,
        "tokens_source": "tiktoken",
        "attributed_tokens": 70000,
        "unmeasured_sources": 3,
    }
    plain = _capture(render_hero(baseline))
    assert "MCP unmeasured" not in plain
    assert "no session" not in plain
    assert "from files" not in plain


def test_treemap_renders_segment_labels_for_large_shares() -> None:
    composition = [
        {"source": "mcp:github", "tokens": 8000},
        {"source": "global:CLAUDE.md", "tokens": 1200},
        {"source": "skills:descriptions (n=22)", "tokens": 800},
    ]
    out = _capture(render_treemap(composition, width=DEFAULT_TREEMAP_WIDTH))
    # Largest segment (>= 12%) should carry an inline label.
    assert "mcp:github" in out
    # Legend shows exact counts for all entries.
    assert "8,000 tok" in out
    assert "1,200 tok" in out
    assert "800 tok" in out


def test_treemap_skips_unmeasured_entries() -> None:
    composition = [
        {"source": "mcp:github", "tokens": 5000},
        {"source": "mcp:notion", "tokens": None},
    ]
    out = _capture(render_treemap(composition))
    assert "mcp:github" in out
    assert "mcp:notion" not in out


def test_treemap_empty_when_nothing_measurable() -> None:
    out = _capture(render_treemap([{"source": "mcp:notion", "tokens": None}]))
    assert "no measurable composition" in out


def test_wordmark_includes_product_name() -> None:
    """Wordmark is name-only — version/subtitle removed in the UX trim."""
    out = _capture(wordmark())
    assert "unclog" in out
    # Regression: no subtitle chrome.
    assert "local-only audit" not in out
