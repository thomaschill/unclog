from __future__ import annotations

from unclog.ui.theme import (
    ACCENT,
    SEVERITY_BAD,
    SEVERITY_OK,
    TREEMAP_GRADIENT,
    gradient_colour,
)


def test_accent_colour_is_teal() -> None:
    assert ACCENT == "#14b8a6"


def test_severity_constants_are_hex() -> None:
    assert SEVERITY_OK.startswith("#") and len(SEVERITY_OK) == 7
    assert SEVERITY_BAD.startswith("#") and len(SEVERITY_BAD) == 7


def test_gradient_colour_wraps_around_palette() -> None:
    assert gradient_colour(0) == TREEMAP_GRADIENT[0]
    assert gradient_colour(len(TREEMAP_GRADIENT)) == TREEMAP_GRADIENT[0]
    assert gradient_colour(len(TREEMAP_GRADIENT) + 1) == TREEMAP_GRADIENT[1]
