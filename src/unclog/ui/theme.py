"""Colour palette used by the hero view and interactive flow.

One teal accent, one green OK, one red BAD, and dim metadata. The
accent appears on the product name, the hero number, and the active
prompt. OK/BAD render apply-result status. Everything else uses the
default foreground.

All colours are hex literals so Rich can emit 24-bit ``truecolor``
sequences where supported and downgrade cleanly to 256-color otherwise.
"""

from __future__ import annotations

ACCENT = "#14b8a6"
SEVERITY_OK = "#22c55e"
SEVERITY_BAD = "#ef4444"
DIM = "#6b7280"

# Treemap gradient — cool palette, teal → slate → deeper teal.
# Segments wrap around this list by index.
TREEMAP_GRADIENT: tuple[str, ...] = (
    "#14b8a6",
    "#0d9488",
    "#475569",
    "#0f766e",
    "#64748b",
    "#115e59",
)


def gradient_colour(index: int) -> str:
    """Pick the next treemap segment colour, wrapping around the palette."""
    return TREEMAP_GRADIENT[index % len(TREEMAP_GRADIENT)]
