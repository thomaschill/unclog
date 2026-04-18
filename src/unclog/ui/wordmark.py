"""Two-line ASCII wordmark drawn by hand (spec §11.4).

Renders in teal. Below it, a dim subtitle. Suppressed under ``--plain``
/ ``--json`` / ``--report`` per spec §11.4. Kept as a pure-data module
so tests can snapshot the frame without importing Rich.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text

from unclog.ui.theme import ACCENT

# The leading glyph sequence evokes rising flow clearing a pipe.
# Kept to a single compact line so CLI output budget goes to content,
# not chrome. Version + "local-only audit" subtitle dropped in the UX
# trim — users opening a TTY tool know they're using it, and the
# version is already in ``--json`` output for anyone who needs it.
WORDMARK_GLYPHS = "▁▂▃"
WORDMARK_NAME = "unclog"


def wordmark() -> RenderableType:
    block = Text()
    block.append(f"{WORDMARK_GLYPHS} ", style=f"bold {ACCENT}")
    block.append(WORDMARK_NAME, style=f"bold {ACCENT}")
    return block
