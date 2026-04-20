"""Welcome panel — the product frame rendered above the baseline.

One rounded panel: title ``unclog``, one-line tagline, version in the
subtitle. No verbose mode, no scan-meta grid, no tips list — the picker
below it is the decision surface and needs every row of screen space.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text

from unclog import __version__
from unclog.state import InstallationState
from unclog.ui.chrome import rounded_panel
from unclog.ui.theme import ACCENT, DIM


def welcome_panel(state: InstallationState) -> RenderableType:
    """Return the welcome panel: title + tagline + version subtitle."""
    del state  # reserved for future per-install context; unused today.
    title = Text("unclog", style=f"bold {ACCENT}")
    subtitle = Text(f"v{__version__}", style=DIM)
    tagline = Text("local-only audit of your Claude Code installation", style=DIM)
    return rounded_panel(tagline, title=title, subtitle=subtitle)


__all__ = ["welcome_panel"]
