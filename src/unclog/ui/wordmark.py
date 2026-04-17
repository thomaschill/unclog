"""Two-line ASCII wordmark drawn by hand (spec §11.4).

Renders in teal. Below it, a dim subtitle. Suppressed under ``--plain``
/ ``--json`` / ``--report`` per spec §11.4. Kept as a pure-data module
so tests can snapshot the frame without importing Rich.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text

from unclog import __version__
from unclog.ui.theme import ACCENT, DIM

# The leading glyph sequence evokes rising flow clearing a pipe.
# Kept to 2 lines so the wordmark is compact even at narrow widths.
WORDMARK_LINE_1 = " ▁▂▃  unclog"
SUBTITLE_SUFFIX = "local-only audit"


def wordmark() -> RenderableType:
    line = Text(WORDMARK_LINE_1, style=f"bold {ACCENT}")
    subtitle = Text(f"      v{__version__}  ·  {SUBTITLE_SUFFIX}", style=DIM)
    block = Text()
    block.append_text(line)
    block.append("\n")
    block.append_text(subtitle)
    return block
