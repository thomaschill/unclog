"""Rich-based multiselect picker, sectioned.

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

The picker takes a list of :class:`Section` so a single picker can
present "Apply N fixes" and "Curate K agents/skills/MCPs" as visually
separated groups inside one decision surface. Section headers render
as non-selectable rows; cursor movement skips them. A section with an
empty title renders without a header — useful for single-section
callers that want the historical look.

Viewport scrolling is done by hand: Rich's Live redraws the full
renderable each frame, so with 200+ rows we'd overflow the terminal.
We render at most ``visible_rows`` rows centred on the cursor and
update the slice as the cursor moves past the top/bottom margin.
Section headers count toward the visible-row budget so their presence
doesn't push selectable rows off-screen.

Keys:

- ``↑``/``↓`` (and ``k``/``j``) — move cursor; skips section headers
- ``Space`` — toggle current row
- ``Enter`` — confirm selection
- ``a`` — check every row in the cursor's current section
- ``A`` — check every row in every section
- ``n`` — clear every row in the cursor's current section
- ``N`` — clear every row in every section
- ``g`` / ``Home`` — jump to first selectable row
- ``G`` / ``End`` — jump to last selectable row
- ``PgUp`` / ``PgDn`` — page up/down
- ``q`` / ``Esc`` / ``Ctrl+C`` — quit without selecting
"""

from __future__ import annotations

from dataclasses import dataclass, field

import readchar
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.table import Table
from rich.text import Text

from unclog.findings.base import Finding
from unclog.ui.chrome import hint_bar, rounded_panel
from unclog.ui.theme import ACCENT, DIM

# Category → (short badge label, badge colour). Each finding type is
# rendered with a consistent badge so the user can scan the picker by
# colour and instantly see the split between agents, skills, plugins,
# and MCPs without reading every row.
_CATEGORY_STYLE: dict[str, tuple[str, str]] = {
    "stale_plugin": ("plugin", "#e879f9"),
    "disabled_plugin_residue": ("residue", "#f472b6"),
    "dead_mcp": ("mcp", "#fb923c"),
    "unused_mcp": ("mcp", "#fb923c"),
    "failed_mcp_probe": ("mcp", "#fb923c"),
    "unmeasured_mcp": ("mcp", "#fb923c"),
    "missing_claudeignore": ("ignore", "#facc15"),
    "claude_md_dead_ref": ("md-ref", "#fca5a5"),
    "claude_md_duplicate": ("md-dup", "#fca5a5"),
    "claude_md_oversized": ("md-big", "#fca5a5"),
    "scope_mismatch_global_to_project": ("scope", "#a3a3a3"),
    "scope_mismatch_project_to_global": ("scope", "#a3a3a3"),
    # Curate-picker types. Different palette from detector badges so
    # the user can tell at a glance this picker is "pick-what-to-prune"
    # rather than "fix-detected-problems".
    "agent_inventory": ("agent", "#60a5fa"),
    "skill_inventory": ("skill", "#2dd4bf"),
}

_DEFAULT_BADGE = ("other", "#9ca3af")
_MIN_VISIBLE_ROWS = 6
# Cap so the header block (welcome panel, baseline panel, inventory,
# findings summary) stays on-screen. On a 30-row terminal this leaves
# ~12 lines for the report above the picker.
_MAX_VISIBLE_ROWS = 12
_FRAME_OVERHEAD = 10  # panel borders + header + legend + footer
_CURSOR_MARGIN = 3  # keep this many rows visible above/below the cursor


@dataclass
class Section:
    """One visually-grouped block of findings inside a single picker.

    ``title`` renders as a dim header row above the section's findings.
    Pass an empty string to omit the header — used by callers that
    only have one section and want the historical look.

    ``preselected`` holds indices into ``findings`` that should start
    checked. Curate sections always pass ``set()`` (consent is
    per-item); detector sections pass auto-checked indices.
    """

    title: str
    findings: list[Finding]
    preselected: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class _HeaderRow:
    """Non-selectable section divider row."""

    section_idx: int
    title: str


@dataclass(frozen=True)
class _FindingRow:
    """Selectable finding row.

    ``flat_idx`` is the finding's position in the cross-section flat
    list — the cardinality used for the ``selected`` set so toggling
    is independent of section boundaries.
    """

    section_idx: int
    flat_idx: int
    finding: Finding


_Row = _HeaderRow | _FindingRow


def _category_badge(finding_type: str) -> tuple[str, str]:
    return _CATEGORY_STYLE.get(finding_type, _DEFAULT_BADGE)


def _format_tokens(value: int | None) -> Text:
    if value is None:
        return Text("    — tok", style=DIM)
    return Text(f"{value:>5,} tok", style="default")


def _format_scope(kind: str) -> Text:
    return Text(f"{kind:>7}", style=DIM)


def _build_rows(sections: list[Section]) -> tuple[list[_Row], list[Finding]]:
    """Flatten ``sections`` into a row list + the cross-section finding list.

    The row list interleaves header rows (where the section has a
    non-empty title) with one ``_FindingRow`` per finding. The flat
    finding list is the canonical ordering used by ``selected``.
    """
    rows: list[_Row] = []
    flat: list[Finding] = []
    for section_idx, section in enumerate(sections):
        if section.title:
            rows.append(_HeaderRow(section_idx=section_idx, title=section.title))
        for finding in section.findings:
            flat_idx = len(flat)
            flat.append(finding)
            rows.append(
                _FindingRow(
                    section_idx=section_idx,
                    flat_idx=flat_idx,
                    finding=finding,
                )
            )
    return rows, flat


def _initial_selected(sections: list[Section]) -> set[int]:
    """Translate per-section ``preselected`` indices into flat indices."""
    selected: set[int] = set()
    flat_offset = 0
    for section in sections:
        for local_idx in section.preselected:
            if 0 <= local_idx < len(section.findings):
                selected.add(flat_offset + local_idx)
        flat_offset += len(section.findings)
    return selected


def _first_selectable(rows: list[_Row]) -> int:
    """Index of the first ``_FindingRow``, or 0 if there are none."""
    for i, row in enumerate(rows):
        if isinstance(row, _FindingRow):
            return i
    return 0


def _last_selectable(rows: list[_Row]) -> int:
    for i in range(len(rows) - 1, -1, -1):
        if isinstance(rows[i], _FindingRow):
            return i
    return 0


@dataclass
class _State:
    """Mutable picker state — cursor index (into ``rows``), selection
    set (flat finding indices), viewport top (row index)."""

    cursor: int
    selected: set[int]
    viewport_top: int

    def toggle(self, rows: list[_Row]) -> None:
        row = rows[self.cursor]
        if not isinstance(row, _FindingRow):
            return
        if row.flat_idx in self.selected:
            self.selected.remove(row.flat_idx)
        else:
            self.selected.add(row.flat_idx)

    def select_all(self, rows: list[_Row]) -> None:
        self.selected = {r.flat_idx for r in rows if isinstance(r, _FindingRow)}

    def select_none(self) -> None:
        self.selected = set()

    def select_section(self, rows: list[_Row], section_idx: int) -> None:
        self.selected |= {
            r.flat_idx
            for r in rows
            if isinstance(r, _FindingRow) and r.section_idx == section_idx
        }

    def deselect_section(self, rows: list[_Row], section_idx: int) -> None:
        self.selected -= {
            r.flat_idx
            for r in rows
            if isinstance(r, _FindingRow) and r.section_idx == section_idx
        }


def _move_cursor(state: _State, rows: list[_Row], delta: int) -> None:
    """Move cursor by ``delta`` finding rows, skipping headers.

    A header at the destination is jumped over in the same direction.
    If we run off the end, the cursor clamps to the nearest selectable
    row in the original direction of travel.
    """
    if not rows:
        return
    step = 1 if delta > 0 else -1
    remaining = abs(delta)
    pos = state.cursor
    while remaining > 0:
        next_pos = pos + step
        if next_pos < 0 or next_pos >= len(rows):
            break
        pos = next_pos
        if isinstance(rows[pos], _FindingRow):
            remaining -= 1
    # If we landed on a header (because we ran off the end) drift back
    # to the nearest selectable row.
    if not isinstance(rows[pos], _FindingRow):
        scan = -step
        scan_pos = pos
        while 0 <= scan_pos < len(rows) and not isinstance(rows[scan_pos], _FindingRow):
            scan_pos += scan
        if 0 <= scan_pos < len(rows) and isinstance(rows[scan_pos], _FindingRow):
            pos = scan_pos
    state.cursor = pos


def _clamp_viewport(state: _State, total: int, visible: int) -> None:
    """Adjust ``viewport_top`` so the cursor is always on-screen."""
    if total <= visible:
        state.viewport_top = 0
        return
    if state.cursor < state.viewport_top + _CURSOR_MARGIN:
        state.viewport_top = max(0, state.cursor - _CURSOR_MARGIN)
    bottom = state.viewport_top + visible - 1
    if state.cursor > bottom - _CURSOR_MARGIN:
        state.viewport_top = min(total - visible, state.cursor + _CURSOR_MARGIN - visible + 1)
    state.viewport_top = max(0, min(state.viewport_top, total - visible))


def _build_frame(
    rows: list[_Row],
    flat: list[Finding],
    state: _State,
    title: str,
    visible_rows: int,
) -> RenderableType:
    """Render the picker as panel + hint bar + running-total line.

    Returns a ``Group`` containing three vertically-stacked renderables:

    1. **Rounded panel** — table of rows + in-panel position indicator.
       Cursor row carries a thin left-edge accent bar (``▌``) in a
       dedicated leading column; text goes bold. Section header rows
       skip the cursor column entirely and span the rest with a dim
       title and a count.
    2. **Hint bar** — keybind legend below the panel (outside the frame).
    3. **Running total** — ``N selected · ~X,XXX tokens to save`` on
       its own line at the bottom.
    """
    total = len(rows)
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
    table.add_column("cursor", width=1, no_wrap=True)
    table.add_column("marker", width=2, no_wrap=True)
    table.add_column("badge", width=9, no_wrap=True)
    table.add_column("tokens", width=9, justify="right", no_wrap=True)
    table.add_column("scope", width=9, no_wrap=True)
    table.add_column("title", ratio=1, overflow="ellipsis", no_wrap=True)

    end = min(state.viewport_top + visible, total)
    for i in range(state.viewport_top, end):
        row = rows[i]
        if isinstance(row, _HeaderRow):
            # Headers render as a dim divider line that spans the row.
            # ``end_section=True`` style: leading blank columns, then a
            # bold-dim header text in the title column.
            header = Text(f"─ {row.title} ─", style=f"bold {DIM}")
            table.add_row(Text(" "), Text(""), Text(""), Text(""), Text(""), header)
            continue

        is_cursor = i == state.cursor
        is_selected = row.flat_idx in state.selected
        finding = row.finding

        badge_label, badge_colour = _category_badge(finding.type)
        cursor_bar = Text("▌" if is_cursor else " ", style=ACCENT if is_cursor else DIM)
        marker_text = Text("●" if is_selected else "○", style=badge_colour if is_selected else DIM)
        badge_text = Text(badge_label, style=f"bold {badge_colour}")
        tokens_text = _format_tokens(finding.token_savings)
        scope_text = _format_scope(finding.scope.kind)
        title_style = f"bold {ACCENT}" if is_cursor else "default"
        title_text = Text(finding.title, style=title_style)
        table.add_row(cursor_bar, marker_text, badge_text, tokens_text, scope_text, title_text)

    # Position indicator: count selectable rows only — headers are
    # navigation furniture, not items the user is choosing between.
    selectable_indices = [
        idx for idx, r in enumerate(rows) if isinstance(r, _FindingRow)
    ]
    finding_total = len(selectable_indices)
    above = state.viewport_top > 0
    below = end < total
    if finding_total:
        cursor_position = (
            selectable_indices.index(state.cursor) + 1
            if state.cursor in selectable_indices
            else 1
        )
        position = Text()
        position.append(f"  {cursor_position}", style=DIM)
        position.append(" of ", style=DIM)
        position.append(f"{finding_total}", style="default")
    else:
        position = Text("  (no items)", style=DIM)
    if above:
        position.append("   ↑ more above", style=DIM)
    if below:
        position.append("   ↓ more below", style=DIM)

    panel = rounded_panel(
        Group(table, Text(""), position),
        title=title,
    )

    legend = hint_bar(
        [
            ("↑↓", "move"),
            ("space", "toggle"),
            ("a/A", "section/all"),
            ("n/N", "clear section/all"),
            ("enter", "apply"),
            ("q", "quit"),
        ]
    )

    selected_count = len(state.selected)
    token_total = sum(
        f.token_savings or 0 for i, f in enumerate(flat) if i in state.selected
    )
    running_total = Text()
    running_total.append("  ", style=DIM)
    running_total.append(f"{selected_count}", style=f"bold {ACCENT}")
    running_total.append(" selected", style=DIM)
    if token_total:
        running_total.append("  ·  ", style=DIM)
        running_total.append(f"~{token_total:,}", style=f"bold {ACCENT}")
        running_total.append(" tokens to save", style=DIM)

    return Group(panel, legend, running_total)


def _compute_visible_rows(console: Console) -> int:
    """Reserve room for panel chrome + status + keybinds; rest is rows."""
    height = console.size.height or 24
    available = height - _FRAME_OVERHEAD
    return max(_MIN_VISIBLE_ROWS, min(_MAX_VISIBLE_ROWS, available))


def run_rich_multiselect(
    sections: list[Section],
    *,
    title: str,
    console: Console,
) -> list[Finding]:
    """Drive a Rich Live sectioned multiselect picker and return chosen findings.

    Returns ``[]`` when the user quits with ``q``/``Esc`` or confirms
    with nothing selected. Returns ``[]`` immediately when no section
    contains any findings.

    Each section renders with a dim header above its rows when its
    ``title`` is non-empty; an empty title omits the header so a single
    section with no title looks identical to the historical
    flat-list picker.

    ``a``/``n`` operate on the cursor's current section so users can
    sweep one bucket (e.g. "check everything in Curate agents") without
    touching the others. ``A``/``N`` sweep every section at once.
    """
    rows, flat = _build_rows(sections)
    if not flat:
        return []

    state = _State(
        cursor=_first_selectable(rows),
        selected=_initial_selected(sections),
        viewport_top=0,
    )
    visible_rows = _compute_visible_rows(console)

    with Live(
        _build_frame(rows, flat, state, title, visible_rows),
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

            match key:
                case readchar.key.UP | "k":
                    _move_cursor(state, rows, -1)
                case readchar.key.DOWN | "j":
                    _move_cursor(state, rows, 1)
                case readchar.key.PAGE_UP:
                    _move_cursor(state, rows, -max(1, visible_rows - 1))
                case readchar.key.PAGE_DOWN:
                    _move_cursor(state, rows, max(1, visible_rows - 1))
                case readchar.key.HOME | "g":
                    state.cursor = _first_selectable(rows)
                case readchar.key.END | "G":
                    state.cursor = _last_selectable(rows)
                case readchar.key.SPACE:
                    state.toggle(rows)
                case "a":
                    current = rows[state.cursor]
                    if isinstance(current, _FindingRow):
                        state.select_section(rows, current.section_idx)
                case "A":
                    state.select_all(rows)
                case "n":
                    current = rows[state.cursor]
                    if isinstance(current, _FindingRow):
                        state.deselect_section(rows, current.section_idx)
                case "N":
                    state.select_none()
                case readchar.key.ENTER:
                    return [flat[i] for i in sorted(state.selected)]
                case readchar.key.ESC | "q":
                    return []
                case readchar.key.CTRL_C:
                    return []

            visible_rows = _compute_visible_rows(console)
            live.update(_build_frame(rows, flat, state, title, visible_rows))


__all__ = ["Section", "run_rich_multiselect"]
