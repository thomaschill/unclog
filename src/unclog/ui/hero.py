"""Hero baseline number and composition top-contributors (spec §11.5, §11.6).

The hero is the first thing a user sees: one plain-English line stating
the baseline token cost. Immediately below, a compact list of the top
composition contributors gives the user the 3-5 biggest consumers of
their baseline at a glance.

Everything in this module returns Rich renderables — never writes to
stdout. The caller (``ui.output.render_default``) decides how/where to
display them.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text

from unclog.ui.chrome import rounded_panel
from unclog.ui.theme import ACCENT, DIM, gradient_colour

# Kept for API stability; the stacked-bar treemap was removed but callers
# still pass ``width`` for historical reasons.
DEFAULT_TREEMAP_WIDTH = 76


def render_hero(baseline: dict[str, Any]) -> RenderableType:
    """Return the hero block: ``N,NNN tokens in your Claude Code baseline``.

    Provenance (session vs filesystem, how many MCPs are unmeasured) was
    removed from the hero in the v0.1 UX pass: the composition block
    below already shows which sources are measured and which read ``—``,
    so repeating that signal in the hero adds jargon without clarity.
    """
    tokens = int(baseline.get("estimated_tokens") or 0)

    line = Text()
    line.append(f"{tokens:,}", style=f"bold {ACCENT}")
    line.append(" tokens in your Claude Code baseline", style=DIM)
    return line


TOP_CONTRIBUTORS_MAX = 5


def render_treemap(
    composition: list[dict[str, Any]],
    *,
    width: int = DEFAULT_TREEMAP_WIDTH,
) -> RenderableType:
    """Return the top-contributors list for measurable composition sources.

    Earlier revisions drew a stacked horizontal bar as well, but at
    typical installs one source dominates (~75%) and the bar collapsed
    to a single wide segment with two slivers — adding a line of chrome
    without any new information. The legend already carries the signal.
    """
    measurable = [
        e for e in composition if isinstance(e.get("tokens"), int) and (e.get("tokens") or 0) > 0
    ]
    if not measurable:
        return Text("(no measurable composition yet)", style=DIM)

    measurable.sort(key=lambda e: int(e["tokens"]), reverse=True)
    top = measurable[:TOP_CONTRIBUTORS_MAX]

    legend = Text()
    for i, entry in enumerate(top):
        colour = gradient_colour(i)
        if i:
            legend.append("\n")
        legend.append("■ ", style=colour)
        legend.append(f"{int(entry['tokens']):>6,} tok  ", style=DIM)
        legend.append(str(entry["source"]), style="default")
        # Project-scoped rows (MCPs, etc.) only load inside that project —
        # surface the scope inline so the user doesn't mistake a per-project
        # cost for a global baseline contributor.
        scope = entry.get("scope")
        if isinstance(scope, str) and scope.startswith("project:"):
            legend.append(f"  [{scope}]", style=DIM)

    hidden = len(measurable) - len(top)
    if hidden > 0:
        hidden_total = sum(int(e["tokens"]) for e in measurable[TOP_CONTRIBUTORS_MAX:])
        legend.append("\n")
        legend.append(f"  +{hidden} more  {hidden_total:,} tok", style=DIM)
    return legend


def render_baseline_panel(
    baseline: dict[str, Any],
    composition: list[dict[str, Any]],
) -> RenderableType:
    """Return the hero + composition block wrapped in a rounded panel.

    Panel title is ``baseline``; the right-aligned subtitle carries the
    provenance of the headline number (``session+tiktoken``, ``tiktoken``,
    or ``empty``). That puts the "how was this number derived" answer
    directly on the chrome, next to the number itself, rather than
    forcing the user to cross-reference the JSON output.

    When the composition is empty (fresh install, no measurable sources)
    the panel still renders with just the hero line — the empty state
    reads as "we scanned; nothing to show" rather than an absent panel.
    """
    hero = render_hero(baseline)

    if composition:
        body: RenderableType = Group(hero, Text(""), render_treemap(composition))
    else:
        body = hero

    return rounded_panel(body, title="baseline")
