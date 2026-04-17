"""Colour palette, severity tokens, and glyphs used by the hero view.

Spec §11.2-§11.3 keeps the palette deliberately narrow: one teal accent,
three severity colours, and dim metadata. The accent appears only on
the product name, the hero number, and the active prompt. Severity
colours show up on baseline tiers and finding badges. Everything else
uses the default foreground.

All colours are hex literals so Rich can emit 24-bit ``truecolor``
sequences where supported and downgrade cleanly to 256-color otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from unclog.state import BaselineTier

ACCENT = "#14b8a6"
SEVERITY_LEAN = "#22c55e"
SEVERITY_TYPICAL = "#eab308"
SEVERITY_CLOGGED = "#ef4444"
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


@dataclass(frozen=True)
class TierStyle:
    """How a tier renders: colour, one-word subtitle, and glyph."""

    colour: str
    label: str


TIER_STYLES: dict[BaselineTier, TierStyle] = {
    "lean": TierStyle(colour=SEVERITY_LEAN, label="lean"),
    "typical": TierStyle(colour=SEVERITY_TYPICAL, label="typical"),
    "clogged": TierStyle(colour=SEVERITY_CLOGGED, label="clogged"),
}


def tier_style(tier: BaselineTier) -> TierStyle:
    return TIER_STYLES[tier]


def gradient_colour(index: int) -> str:
    """Pick the next treemap segment colour, wrapping around the palette."""
    return TREEMAP_GRADIENT[index % len(TREEMAP_GRADIENT)]
