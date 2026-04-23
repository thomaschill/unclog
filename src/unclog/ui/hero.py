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

import re
from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text

from unclog.ui.chrome import rounded_panel
from unclog.ui.theme import ACCENT, DIM, gradient_colour

# Kept for API stability; the stacked-bar treemap was removed but callers
# still pass ``width`` for historical reasons.
DEFAULT_TREEMAP_WIDTH = 76

# Pre-compiled regexes that translate the machine-readable ``source``
# strings from :func:`unclog.ui.output.build_composition` into the
# parts we need for a human-friendly row label.
_SKILL_SOURCE_RE = re.compile(r"^skills:descriptions \(n=(?P<n>\d+)\)$")
_AGENT_SOURCE_RE = re.compile(r"^agents:descriptions \(n=(?P<n>\d+)\)$")
_COMMAND_SOURCE_RE = re.compile(r"^commands:descriptions \(n=(?P<n>\d+)\)$")
_MCP_SOURCE_RE = re.compile(r"^mcp:(?P<name>.+)$")


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
        _append_composition_label(legend, entry)
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


def _append_composition_label(text: Text, entry: dict[str, Any]) -> None:
    """Append a human-friendly label for ``entry`` to ``text``.

    Translates the machine-readable ``source`` strings produced by
    :func:`unclog.ui.output.build_composition` into lay-reader labels:

    - ``skills:descriptions (n=22)`` → ``22 skills``
    - ``agents:descriptions (n=156)`` → ``156 agents``
    - ``mcp:name`` → ``mcp name``
    - anything else → the raw source
    """
    source = str(entry.get("source", ""))

    if m := _SKILL_SOURCE_RE.match(source):
        text.append(m["n"], style="bold default")
        text.append(" skills", style=DIM)
        return
    if m := _AGENT_SOURCE_RE.match(source):
        text.append(m["n"], style="bold default")
        text.append(" agents", style=DIM)
        return
    if m := _COMMAND_SOURCE_RE.match(source):
        text.append(m["n"], style="bold default")
        text.append(" commands", style=DIM)
        return
    if m := _MCP_SOURCE_RE.match(source):
        text.append("mcp ", style=DIM)
        text.append(m["name"], style="default")
        return

    text.append(source, style="default")


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
