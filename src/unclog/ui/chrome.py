"""Shared visual primitives for the Claude-Code-flavoured chrome.

Every panel, hint bar, status glyph, and section divider in the new
UI routes through this module. Centralising the shapes here means the
Claude Code aesthetic (rounded corners, thin borders, left-aligned
titles, generous padding, accent-coloured keys + dim labels) applies
uniformly without every call site spelling out box styles.

Palette comes from :mod:`unclog.ui.theme` — swap a colour there and the
whole product surface tracks it.
"""

from __future__ import annotations

from collections.abc import Iterable

from rich.box import ROUNDED
from rich.console import RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from unclog.ui.theme import ACCENT, DIM, SEVERITY_BAD, SEVERITY_OK

# Default panel padding — one row top/bottom, two columns left/right.
# Mirrors Claude Code's "breathe-able" interior; tighter padding makes
# the rounded corners feel cramped.
_PANEL_PADDING = (1, 2)


def rounded_panel(
    body: RenderableType,
    title: str | Text,
    *,
    border: str = DIM,
    accent: str = ACCENT,
    subtitle: str | Text | None = None,
) -> Panel:
    """Wrap ``body`` in a rounded thin-border panel with a left-aligned title.

    ``title`` accepts either a plain string (rendered bold accent) or a
    pre-styled ``rich.text.Text`` for cases where the caller needs
    mixed styles (e.g. a status glyph prefix).

    ``subtitle`` is optional and renders dim, right-aligned on the
    bottom border — used for provenance like ``session+tiktoken`` on
    the baseline panel.
    """
    if isinstance(title, str):
        title_renderable: Text | str = Text(title, style=f"bold {accent}")
    else:
        title_renderable = title

    subtitle_renderable: Text | str | None
    if isinstance(subtitle, str):
        subtitle_renderable = Text(subtitle, style=DIM)
    else:
        subtitle_renderable = subtitle

    return Panel(
        body,
        title=title_renderable,
        title_align="left",
        subtitle=subtitle_renderable,
        subtitle_align="right",
        box=ROUNDED,
        border_style=border,
        padding=_PANEL_PADDING,
    )


def hint_bar(pairs: Iterable[tuple[str, str]], *, accent: str = ACCENT) -> RenderableType:
    """Render ``key label · key label · …`` with accent keys + dim labels.

    Intended to sit *below* a panel (not inside it) so it reads as a
    legend for the thing above, matching Claude Code's out-of-panel
    keybind hints. Single-line, left-padded one column so it aligns
    optically with a panel's interior gutter.
    """
    text = Text()
    first = True
    for key, label in pairs:
        if not first:
            text.append("  ·  ", style=DIM)
        first = False
        text.append(key, style=f"bold {accent}")
        text.append(f" {label}", style=DIM)
    return Padding(text, (0, 1))


# Glyph kind → (character, style). ``running`` mirrors Claude Code's
# orange dot; we render it in our accent instead. ``attention`` uses
# amber (#eab308) to match the existing warning colour already in use
# throughout the product.
_GLYPHS: dict[str, tuple[str, str]] = {
    "running": ("⏺", ACCENT),
    "done": ("✓", SEVERITY_OK),
    "pending": ("○", DIM),
    "attention": ("!", "#eab308"),
    "error": ("✗", SEVERITY_BAD),
}


def status_glyph(kind: str) -> Text:
    """Return the glyph + trailing space for a status kind.

    Glyph lives in its own cell so the caller can concatenate it to a
    title line or list item without worrying about bleeding styles.
    Unknown ``kind`` falls back to a dim dot.
    """
    glyph, style = _GLYPHS.get(kind, ("·", DIM))
    return Text(f"{glyph} ", style=style)


def section_rule(label: str, *, accent: str = ACCENT) -> Rule:
    """Dim horizontal rule with an inline accent-tinted label.

    Used to separate the report's headline blocks (baseline /
    inventory / findings / also running). Rich's ``Rule`` pads the
    title with the rule's own characters, so the label sits centred
    in a sea of dim dashes — which is the Claude Code look.
    """
    title = Text(label, style=f"bold {accent}")
    return Rule(title=title, style=DIM, align="left")


__all__ = [
    "hint_bar",
    "rounded_panel",
    "section_rule",
    "status_glyph",
]
