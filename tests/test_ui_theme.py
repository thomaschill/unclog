from __future__ import annotations

from unclog.ui.theme import (
    ACCENT,
    SEVERITY_CLOGGED,
    SEVERITY_LEAN,
    SEVERITY_TYPICAL,
    TREEMAP_GRADIENT,
    gradient_colour,
    tier_style,
)


def test_accent_colour_is_teal() -> None:
    assert ACCENT == "#14b8a6"


def test_tier_style_maps_each_tier_to_its_severity() -> None:
    assert tier_style("lean").colour == SEVERITY_LEAN
    assert tier_style("typical").colour == SEVERITY_TYPICAL
    assert tier_style("clogged").colour == SEVERITY_CLOGGED
    assert tier_style("lean").label == "lean"


def test_gradient_colour_wraps_around_palette() -> None:
    assert gradient_colour(0) == TREEMAP_GRADIENT[0]
    assert gradient_colour(len(TREEMAP_GRADIENT)) == TREEMAP_GRADIENT[0]
    assert gradient_colour(len(TREEMAP_GRADIENT) + 1) == TREEMAP_GRADIENT[1]
