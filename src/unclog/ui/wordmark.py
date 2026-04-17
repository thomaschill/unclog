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
# Kept to a single compact line so CLI output budget goes to content,
# not chrome.
WORDMARK_GLYPHS = "▁▂▃"
WORDMARK_NAME = "unclog"
SUBTITLE_SUFFIX = "local-only audit"


def wordmark() -> RenderableType:
    block = Text()
    block.append(f"{WORDMARK_GLYPHS} ", style=f"bold {ACCENT}")
    block.append(WORDMARK_NAME, style=f"bold {ACCENT}")
    block.append(f"  v{__version__}  ·  {SUBTITLE_SUFFIX}", style=DIM)
    return block
