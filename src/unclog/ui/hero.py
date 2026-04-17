"""Hero baseline number and composition treemap (spec §11.5, §11.6).

The hero is the first thing a user sees: a big, comma-formatted token
count coloured by tier, with the tier word underneath in dim. The
treemap is a single-row horizontal stacked bar where each segment's
width is proportional to its share of the measured baseline.

Everything in this module returns Rich renderables — never writes to
stdout. The caller (``ui.output.render_default``) decides how/where to
display them.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text

from unclog.state import BaselineTier
from unclog.ui.theme import DIM, gradient_colour, tier_style

# Minimum share a segment needs before we place its label inside the bar.
# Below this threshold the segment is filled with a solid block and the
# reader relies on the legend.
INLINE_LABEL_MIN_SHARE = 0.12

# Target width for the treemap. Caller may pass a narrower one.
DEFAULT_TREEMAP_WIDTH = 76


def _provenance(baseline: dict[str, Any]) -> str:
    source = baseline.get("tokens_source", "unknown")
    unmeasured = baseline.get("unmeasured_sources", 0)
    attributed = baseline.get("attributed_tokens", 0)
    total = baseline.get("estimated_tokens", 0)
    parts: list[str] = []

    if source == "session+tiktoken":
        parts.append("measured from latest session")
    elif source == "tiktoken":
        parts.append("measured from file content (no session yet)")
    else:
        parts.append(source)

    if total and attributed and attributed < total:
        other = total - attributed
        parts.append(f"{other:,} tok unattributed")

    if unmeasured:
        parts.append(f"{unmeasured} MCP source(s) unmeasured")

    return "  ·  ".join(parts)


def render_hero(baseline: dict[str, Any]) -> RenderableType:
    """Return the hero block: big number + tier label + provenance line."""
    tokens = int(baseline.get("estimated_tokens") or 0)
    tier: BaselineTier = baseline.get("tier", "lean")
    style = tier_style(tier)

    number_line = Text()
    number_line.append(f"{tokens:,}", style=f"bold {style.colour}")
    number_line.append("   ")
    number_line.append(style.label, style=DIM)
    number_line.append(" tokens baseline", style=DIM)

    return Group(number_line, Text(_provenance(baseline), style=DIM))


def _shorten(source: str, width: int) -> str:
    """Fit a composition ``source`` label into ``width`` characters."""
    if width <= 0:
        return ""
    if len(source) <= width:
        return source
    if width <= 1:
        return source[:width]
    return source[: width - 1] + "…"


def _apportion_widths(shares: list[float], total_width: int) -> list[int]:
    """Distribute ``total_width`` columns across segments proportionally.

    Every share with a non-zero value gets at least one column so small
    sources remain visible. Any rounding residue from the first pass is
    absorbed into the largest segment.
    """
    if not shares or total_width <= 0:
        return [0] * len(shares)
    raw = [s * total_width for s in shares]
    widths = [max(1, int(r)) if r > 0 else 0 for r in raw]
    residue = total_width - sum(widths)
    if residue and widths:
        largest = max(range(len(widths)), key=lambda i: widths[i])
        widths[largest] += residue
    return widths


def render_treemap(
    composition: list[dict[str, Any]],
    *,
    width: int = DEFAULT_TREEMAP_WIDTH,
) -> RenderableType:
    """Return the stacked-bar treemap and its legend for measurable sources."""
    measurable = [
        e for e in composition if isinstance(e.get("tokens"), int) and (e.get("tokens") or 0) > 0
    ]
    if not measurable:
        return Text("(no measurable composition yet)", style=DIM)

    measurable.sort(key=lambda e: int(e["tokens"]), reverse=True)
    total = sum(int(e["tokens"]) for e in measurable)
    shares = [int(e["tokens"]) / total for e in measurable]
    widths = _apportion_widths(shares, width)

    bar = Text()
    for i, (entry, seg_width, share) in enumerate(zip(measurable, widths, shares, strict=True)):
        if seg_width <= 0:
            continue
        colour = gradient_colour(i)
        if share >= INLINE_LABEL_MIN_SHARE and seg_width >= 4:
            label = _shorten(str(entry["source"]), seg_width - 2)
            pad_total = seg_width - len(label)
            left = pad_total // 2
            right = pad_total - left
            bar.append(" " * left + label + " " * right, style=f"on {colour}")
        else:
            bar.append(" " * seg_width, style=f"on {colour}")

    legend = Text()
    for i, entry in enumerate(measurable):
        colour = gradient_colour(i)
        legend.append("■ ", style=colour)
        legend.append(f"{entry['source']}", style="default")
        legend.append(f"  {int(entry['tokens']):,} tok\n", style=DIM)

    # Strip the trailing newline on the last legend line for cleaner output.
    if legend.plain.endswith("\n"):
        legend = legend[:-1]

    return Group(bar, Text(""), legend)
