"""Hero baseline number + top-contributors list.

The hero is the first thing a user sees: one plain-English line stating
the baseline token cost. Immediately below, a compact list of the top
composition contributors gives the user the biggest consumers of their
baseline at a glance.

Everything in this module returns Rich renderables — never writes to
stdout. The caller (``ui.output.render_header``) decides placement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.console import Group, RenderableType
from rich.text import Text

from unclog.ui.chrome import rounded_panel
from unclog.ui.theme import ACCENT, DIM, gradient_colour

CompositionKind = Literal["agents", "skills", "commands", "mcp"]


@dataclass(frozen=True)
class CompositionRow:
    """One row of the baseline's top-contributors list.

    ``kind`` drives both the human label (``"N agents"`` vs the MCP's own
    name) and the colour segment in the baseline panel's legend.
    """

    kind: CompositionKind
    label: str
    tokens: int
    scope_label: str | None = None


TOP_CONTRIBUTORS_MAX = 5


def render_hero(tokens: int) -> RenderableType:
    """Return ``N,NNN tokens in your Claude Code baseline``."""
    line = Text()
    line.append(f"{tokens:,}", style=f"bold {ACCENT}")
    line.append(" tokens in your Claude Code baseline", style=DIM)
    return line


def render_top_contributors(composition: list[CompositionRow]) -> RenderableType:
    """Return the top-contributors list for measurable composition rows.

    At typical installs one source dominates (~75%) so a stacked bar
    would collapse to a wide segment plus slivers — the legend carries
    the signal on its own.
    """
    if not composition:
        return Text("(no measurable composition yet)", style=DIM)

    top = composition[:TOP_CONTRIBUTORS_MAX]

    legend = Text()
    for i, row in enumerate(top):
        if i:
            legend.append("\n")
        legend.append("■ ", style=gradient_colour(i))
        legend.append(f"{row.tokens:>6,} tok  ", style=DIM)
        if row.kind == "mcp":
            legend.append("mcp ", style=DIM)
            legend.append(row.label, style="default")
        else:
            # Aggregate rows: split "22 agents" into bold count + dim tail
            # so the eye lands on the number, not the category noun.
            count, _, label = row.label.partition(" ")
            legend.append(count, style="bold default")
            legend.append(f" {label}", style=DIM)
        if row.scope_label is not None:
            legend.append(f"  [{row.scope_label}]", style=DIM)

    hidden = len(composition) - len(top)
    if hidden > 0:
        hidden_total = sum(r.tokens for r in composition[TOP_CONTRIBUTORS_MAX:])
        legend.append("\n")
        legend.append(f"  +{hidden} more  {hidden_total:,} tok", style=DIM)
    return legend


def render_baseline_panel(
    tokens: int, composition: list[CompositionRow]
) -> RenderableType:
    """Hero line + (when available) the top-contributors list, in one panel."""
    hero = render_hero(tokens)
    if composition:
        body: RenderableType = Group(hero, Text(""), render_top_contributors(composition))
    else:
        body = hero
    return rounded_panel(body, title="baseline")


__all__ = [
    "TOP_CONTRIBUTORS_MAX",
    "CompositionKind",
    "CompositionRow",
    "render_baseline_panel",
    "render_hero",
    "render_top_contributors",
]
