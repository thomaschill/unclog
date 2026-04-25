from __future__ import annotations

from unclog.ui.theme import (
    ACCENT,
    LEGEND_GRADIENT,
    SEVERITY_BAD,
    SEVERITY_OK,
    gradient_colour,
)


def test_accent_colour_is_teal() -> None:
    assert ACCENT == "#14b8a6"


def test_severity_constants_are_hex() -> None:
    assert SEVERITY_OK.startswith("#") and len(SEVERITY_OK) == 7
    assert SEVERITY_BAD.startswith("#") and len(SEVERITY_BAD) == 7


def test_gradient_colour_wraps_around_palette() -> None:
    assert gradient_colour(0) == LEGEND_GRADIENT[0]
    assert gradient_colour(len(LEGEND_GRADIENT)) == LEGEND_GRADIENT[0]
    assert gradient_colour(len(LEGEND_GRADIENT) + 1) == LEGEND_GRADIENT[1]
