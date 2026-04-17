"""Rich-based multiselect picker.

Replaces the curses-driven ``pick`` library with a ``rich.live.Live``
repaint loop driven by ``readchar`` for keyboard input. Compared to
``pick``:

- Respects the terminal's existing colour scheme and renders truecolor
  category badges, right-aligned token counts, and subtle dim reasons —
  not flat monochrome text.
- Doesn't depend on terminal Cursor Position Request (CPR) support the
  way prompt_toolkit/questionary do — works reliably in Terminal.app,
  iTerm2, Ghostty, and stdin-pty environments.
- Lets us show a live footer ("N selected · ~X,XXX tokens to save")
  that recomputes as the user toggles, which is the single most
  actionable number when the picker is open.

Viewport scrolling is done by hand: Rich's Live redraws the full
renderable each frame, so with 200+ rows we'd overflow the terminal.
We render at most ``visible_rows`` items centred on the cursor and
update the slice as the cursor moves past the top/bottom margin.

Keys:
- ``↑``/``↓`` (and ``k``/``j``) — move cursor
- ``Space`` — toggle current row
- ``Enter`` — confirm selection
- ``a`` — invert all
- ``A`` — check all
- ``n`` — uncheck all
- ``g`` / ``Home`` — jump to top
- ``G`` / ``End`` — jump to bottom
- ``PgUp`` / ``PgDn`` — page up/down
- ``q`` / ``Esc`` / ``Ctrl+C`` — quit without selecting
"""

from __future__ import annotations

from dataclasses import dataclass

import readchar
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from unclog.findings.base import Finding
from unclog.ui.theme import ACCENT, DIM

# Category → (short badge label, badge colour). Each finding type is
# rendered with a consistent badge so the user can scan the picker by
# colour and instantly see the split between agents, skills, plugins,
# and MCPs without reading every row.
_CATEGORY_STYLE: dict[str, tuple[str, str]] = {
    "unused_agent": ("agent", "#38bdf8"),
    "unused_skill": ("skill", "#22c55e"),
    "unused_command": ("cmd", "#a78bfa"),
    "stale_plugin": ("plugin", "#e879f9"),
    "disabled_plugin_residue": ("residue", "#f472b6"),
    "dead_mcp": ("mcp", "#fb923c"),
    "unused_mcp": ("mcp", "#fb923c"),
    "missing_claudeignore": ("ignore", "#facc15"),
    "claude_md_dead_ref": ("md-ref", "#fca5a5"),
    "claude_md_duplicate": ("md-dup", "#fca5a5"),
    "claude_md_oversized": ("md-big", "#fca5a5"),
    "scope_mismatch_global_to_project": ("scope", "#a3a3a3"),
    "scope_mismatch_project_to_global": ("scope", "#a3a3a3"),
}

_DEFAULT_BADGE = ("other", "#9ca3af")
_MIN_VISIBLE_ROWS = 6
# Cap so the header block (wordmark, hero, composition, inventory,
# findings summary) stays on-screen. On a 30-row terminal this leaves
# ~12 lines for the report above the picker.
_MAX_VISIBLE_ROWS = 12
_FRAME_OVERHEAD = 10  # panel borders + header + legend + footer
_CURSOR_MARGIN = 3  # keep this many rows visible above/below the cursor


def _category_badge(finding_type: str) -> tuple[str, str]:
    return _CATEGORY_STYLE.get(finding_type, _DEFAULT_BADGE)


def _format_tokens(value: int | None) -> Text:
    if value is None:
        return Text("    — tok", style=DIM)
    return Text(f"{value:>5,} tok", style="default")


def _format_scope(kind: str) -> Text:
    return Text(f"{kind:>7}", style=DIM)


@dataclass
class _State:
    """Mutable picker state — cursor index, selection set, viewport top."""

    cursor: int
    selected: set[int]
    viewport_top: int

    def toggle(self) -> None:
        if self.cursor in self.selected:
            self.selected.remove(self.cursor)
        else:
            self.selected.add(self.cursor)

    def select_all(self, n: int) -> None:
        self.selected = set(range(n))

    def select_none(self) -> None:
        self.selected = set()

    def invert(self, n: int) -> None:
        self.selected = set(range(n)) - self.selected


def _clamp_viewport(state: _State, total: int, visible: int) -> None:
    """Adjust ``viewport_top`` so the cursor is always on-screen."""
    if total <= visible:
        state.viewport_top = 0
        return
    # Cursor above viewport top + margin → scroll up.
    if state.cursor < state.viewport_top + _CURSOR_MARGIN:
        state.viewport_top = max(0, state.cursor - _CURSOR_MARGIN)
    # Cursor below viewport bottom minus margin -> scroll down.
    bottom = state.viewport_top + visible - 1
    if state.cursor > bottom - _CURSOR_MARGIN:
        state.viewport_top = min(total - visible, state.cursor + _CURSOR_MARGIN - visible + 1)
    state.viewport_top = max(0, min(state.viewport_top, total - visible))


def _build_frame(
    findings: list[Finding],
    state: _State,
    title: str,
    visible_rows: int,
) -> RenderableType:
    total = len(findings)
    visible = max(_MIN_VISIBLE_ROWS, min(visible_rows, total))
    _clamp_viewport(state, total, visible)

    table = Table(
        show_header=False,
        show_edge=False,
        box=None,
        padding=(0, 1),
        pad_edge=False,
        expand=True,
    )
    table.add_column("marker", width=2, no_wrap=True)
    table.add_column("badge", width=9, no_wrap=True)
    table.add_column("tokens", width=9, justify="right", no_wrap=True)
    table.add_column("scope", width=9, no_wrap=True)
    table.add_column("title", ratio=1, overflow="ellipsis", no_wrap=True)

    end = min(state.viewport_top + visible, total)
    for i in range(state.viewport_top, end):
        finding = findings[i]
        is_cursor = i == state.cursor
        is_selected = i in state.selected

        badge_label, badge_colour = _category_badge(finding.type)
        marker_text = Text("●" if is_selected else "○", style=badge_colour if is_selected else DIM)
        badge_text = Text(badge_label, style=f"bold {badge_colour}")
        tokens_text = _format_tokens(finding.token_savings)
        scope_text = _format_scope(finding.scope.kind)
        title_text = Text(finding.title, style="default")

        if is_cursor:
            row_style = f"reverse {ACCENT}"
            table.add_row(
                marker_text,
                badge_text,
                tokens_text,
                scope_text,
                title_text,
                style=row_style,
            )
        else:
            table.add_row(marker_text, badge_text, tokens_text, scope_text, title_text)

    # Scroll indicators: compact "12-30 of 196" so the user always
    # knows where they are in the list, plus tiny arrows when more
    # content exists above or below the viewport.
    above = state.viewport_top > 0
    below = end < total
    position = Text()
    position.append(f"  {state.viewport_top + 1}-{end}", style=DIM)
    position.append(" of ", style=DIM)
    position.append(f"{total}", style="default")
    if above:
        position.append("   ↑ more above", style=DIM)
    if below:
        position.append("   ↓ more below", style=DIM)

    selected_count = len(state.selected)
    token_total = sum(
        f.token_savings or 0 for i, f in enumerate(findings) if i in state.selected
    )
    footer = Text()
    footer.append(f"  {selected_count}", style=f"bold {ACCENT}")
    footer.append(" selected  ", style=DIM)
    if token_total:
        footer.append("·  ", style=DIM)
        footer.append(f"~{token_total:,}", style=f"bold {ACCENT}")
        footer.append(" tokens to save", style=DIM)

    legend = Text()
    for label, keys in (
        ("move", "↑↓"),
        ("toggle", "space"),
        ("invert", "a"),
        ("all", "A"),
        ("none", "n"),
        ("submit", "enter"),
        ("quit", "q"),
    ):
        legend.append(keys, style=f"bold {ACCENT}")
        legend.append(f" {label}   ", style=DIM)

    body = Group(
        table,
        Text(""),
        position,
        footer,
        Text(""),
        Padding(legend, (0, 1)),
    )
    return Panel(
        body,
        title=Text(title, style=f"bold {ACCENT}"),
        title_align="left",
        border_style=DIM,
        padding=(1, 1),
    )


def _compute_visible_rows(console: Console) -> int:
    """Reserve room for panel chrome + status + keybinds; rest is rows.

    Capped at ``_MAX_VISIBLE_ROWS`` so the scan report above the picker
    (wordmark, hero, top contributors, inventory, findings summary)
    remains on-screen on normal laptop terminals. Users can scroll the
    picker with ↑↓/PgUp/PgDn when the list exceeds the cap.
    """
    height = console.size.height or 24
    available = height - _FRAME_OVERHEAD
    return max(_MIN_VISIBLE_ROWS, min(_MAX_VISIBLE_ROWS, available))


def _move_cursor(state: _State, total: int, delta: int) -> None:
    if total <= 0:
        return
    state.cursor = max(0, min(total - 1, state.cursor + delta))


def run_rich_multiselect(
    findings: list[Finding],
    *,
    title: str,
    preselected: set[int],
    console: Console,
) -> list[Finding]:
    """Drive a Rich Live multiselect picker and return chosen findings.

    Returns ``[]`` when the user quits with ``q`` or ``Esc``. An empty
    confirm (Enter with nothing selected) also returns ``[]``.
    """
    if not findings:
        return []

    state = _State(cursor=0, selected=set(preselected), viewport_top=0)
    visible_rows = _compute_visible_rows(console)

    with Live(
        _build_frame(findings, state, title, visible_rows),
        console=console,
        screen=False,
        refresh_per_second=30,
        transient=True,
    ) as live:
        while True:
            try:
                key = readchar.readkey()
            except KeyboardInterrupt:
                return []

            n = len(findings)
            match key:
                case readchar.key.UP | "k":
                    _move_cursor(state, n, -1)
                case readchar.key.DOWN | "j":
                    _move_cursor(state, n, 1)
                case readchar.key.PAGE_UP:
                    _move_cursor(state, n, -max(1, visible_rows - 1))
                case readchar.key.PAGE_DOWN:
                    _move_cursor(state, n, max(1, visible_rows - 1))
                case readchar.key.HOME | "g":
                    state.cursor = 0
                case readchar.key.END | "G":
                    state.cursor = n - 1
                case readchar.key.SPACE:
                    state.toggle()
                case "a":
                    state.invert(n)
                case "A":
                    state.select_all(n)
                case _ if key in ("n", "N"):
                    state.select_none()
                case readchar.key.ENTER:
                    return [findings[i] for i in sorted(state.selected)]
                case readchar.key.ESC | "q":
                    return []
                case readchar.key.CTRL_C:
                    return []

            visible_rows = _compute_visible_rows(console)
            live.update(_build_frame(findings, state, title, visible_rows))


__all__ = ["run_rich_multiselect"]
