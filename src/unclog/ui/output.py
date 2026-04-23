"""Render the pre-picker frame: welcome panel + baseline panel.

The default flow is:

    welcome panel  →  baseline panel  →  picker (in ui.interactive)

No JSON, no plain-text listing, no CLAUDE.md diagnostic — the 0.2 scope
is a single Rich-TTY curation flow. Non-TTY invocations print a minimal
fallback and exit.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from unclog.scan.tokens import TiktokenCounter
from unclog.state import InstallationState
from unclog.ui.hero import render_baseline_panel
from unclog.ui.welcome import welcome_panel


def build_composition(state: InstallationState) -> list[dict[str, Any]]:
    """Return the top-contributors list for the baseline panel.

    One row per measurable bucket: agents-descriptions, skills-descriptions,
    commands-descriptions, and every MCP server with a known
    session-tokens value. MCPs with no session attribution are dropped
    from this list (they'd render as ``— tok`` and crowd out the real
    contributors); the picker still lists them so users can curate them.
    """
    counter = TiktokenCounter()
    entries: list[dict[str, Any]] = []

    if state.agents:
        tokens = sum(
            counter.count(f"{a.name}: {a.description or ''}") for a in state.agents
        )
        if tokens:
            entries.append(
                {
                    "source": f"agents:descriptions (n={len(state.agents)})",
                    "tokens": tokens,
                    "scope": "global",
                }
            )

    if state.skills:
        tokens = sum(
            counter.count(f"{s.name}: {s.description or ''}") for s in state.skills
        )
        if tokens:
            entries.append(
                {
                    "source": f"skills:descriptions (n={len(state.skills)})",
                    "tokens": tokens,
                    "scope": "global",
                }
            )

    if state.commands:
        tokens = sum(
            counter.count(f"{c.name}: {c.description or ''}") for c in state.commands
        )
        if tokens:
            entries.append(
                {
                    "source": f"commands:descriptions (n={len(state.commands)})",
                    "tokens": tokens,
                    "scope": "global",
                }
            )

    for name, tokens in state.mcp_session_tokens.items():
        if tokens:
            entries.append(
                {
                    "source": f"mcp:{name}",
                    "tokens": int(tokens),
                    "scope": "global",
                }
            )

    entries.sort(key=lambda e: -int(e["tokens"]))
    return entries


def baseline_tokens(state: InstallationState) -> int:
    """Sum of every measurable contributor — used for the post-apply line."""
    return sum(int(e["tokens"]) for e in build_composition(state))


def render_header(state: InstallationState, console: Console) -> None:
    """Print the welcome panel + baseline panel, then a blank line."""
    console.print(welcome_panel(state))
    console.print("")
    composition = build_composition(state)
    baseline = {"estimated_tokens": baseline_tokens(state)}
    console.print(render_baseline_panel(baseline, composition))
    console.print("")


__all__ = ["baseline_tokens", "build_composition", "render_header"]
