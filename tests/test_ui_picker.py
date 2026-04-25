"""Unit tests for the sectioned multiselect picker.

Driving the readchar+Live loop end-to-end is impractical in pytest, so
these tests cover the pure-data primitives that decide layout and
state transitions: row construction, cursor movement past headers,
preselection translation, and bulk selection helpers.
"""

from __future__ import annotations

from rich.console import Console

from unclog.findings.base import Action, Finding, Scope
from unclog.ui.picker import (
    Section,
    _build_rows,
    _FindingRow,
    _first_selectable,
    _format_title,
    _HeaderRow,
    _initial_selected,
    _last_selectable,
    _move_cursor,
    _State,
)


def _render_text(renderable: object) -> str:
    console = Console(record=True, width=120, color_system=None)
    console.print(renderable)
    return console.export_text()


def _f(fid: str, ftype: str = "agent_inventory", tokens: int | None = None) -> Finding:
    return Finding(
        id=fid,
        type=ftype,  # type: ignore[arg-type]
        title=f"title {fid}",
        scope=Scope(kind="global"),
        action=Action(primitive="delete_file", path=None),
        token_savings=tokens,
    )


def test_empty_title_section_renders_no_header_row() -> None:
    """Single-section callers using ``title=""`` get the historical
    flat-list layout — no divider row, no extra vertical space."""
    section = Section(title="", findings=[_f("a"), _f("b")])
    rows, flat = _build_rows([section])
    assert all(isinstance(r, _FindingRow) for r in rows)
    assert len(rows) == 2
    assert len(flat) == 2


def test_titled_section_inserts_header_row_above_findings() -> None:
    section = Section(title="Apply", findings=[_f("a")])
    rows, _ = _build_rows([section])
    assert isinstance(rows[0], _HeaderRow)
    assert rows[0].title == "Apply"
    assert isinstance(rows[1], _FindingRow)


def test_multiple_sections_interleave_headers_and_findings() -> None:
    sections = [
        Section(title="Apply", findings=[_f("a"), _f("b")]),
        Section(title="Curate agents", findings=[_f("c"), _f("d"), _f("e")]),
    ]
    rows, flat = _build_rows(sections)
    # Layout: header, finding, finding, header, finding, finding, finding
    assert isinstance(rows[0], _HeaderRow)
    assert isinstance(rows[3], _HeaderRow)
    assert rows[3].title == "Curate agents"
    # Flat indices stay sequential across section boundaries — the
    # selected-set semantics depend on this.
    assert [r.flat_idx for r in rows if isinstance(r, _FindingRow)] == [0, 1, 2, 3, 4]
    assert len(flat) == 5


def test_initial_selected_translates_per_section_indices_to_flat() -> None:
    sections = [
        Section(title="Apply", findings=[_f("a"), _f("b")], preselected={1}),
        Section(title="Curate", findings=[_f("c"), _f("d")], preselected={0}),
    ]
    selected = _initial_selected(sections)
    # First section's index-1 → flat 1; second section's index-0 → flat 2.
    assert selected == {1, 2}


def test_initial_selected_drops_out_of_range_indices() -> None:
    """Out-of-range preselection (e.g. stale defaults from a caller) is
    silently dropped rather than raising — defensive against drift
    between the picker layer and the caller's index semantics."""
    sections = [Section(title="", findings=[_f("a")], preselected={0, 1, 99})]
    assert _initial_selected(sections) == {0}


def test_first_and_last_selectable_skip_headers() -> None:
    sections = [
        Section(title="Apply", findings=[_f("a"), _f("b")]),
        Section(title="Curate", findings=[_f("c")]),
    ]
    rows, _ = _build_rows(sections)
    # First selectable is row 1 (after the "Apply" header at row 0).
    assert _first_selectable(rows) == 1
    # Last selectable is the final row (row 4: c).
    assert _last_selectable(rows) == len(rows) - 1


def test_move_cursor_jumps_over_section_header() -> None:
    """↓ from the last finding of section A skips the header of section
    B and lands on B's first finding — headers are navigation
    furniture, not stops."""
    sections = [
        Section(title="A", findings=[_f("a")]),
        Section(title="B", findings=[_f("b")]),
    ]
    rows, _ = _build_rows(sections)
    # Rows: [header_A, a, header_B, b]
    state = _State(cursor=1, selected=set(), viewport_top=0)
    _move_cursor(state, rows, 1)
    assert state.cursor == 3  # Skipped header_B at index 2.
    assert isinstance(rows[state.cursor], _FindingRow)


def test_move_cursor_clamps_at_end_without_landing_on_header() -> None:
    """Moving past the last finding clamps to the last finding — never
    leaves the cursor parked on a non-selectable header row."""
    sections = [Section(title="A", findings=[_f("a"), _f("b")])]
    rows, _ = _build_rows(sections)
    state = _State(cursor=1, selected=set(), viewport_top=0)
    _move_cursor(state, rows, 999)
    assert isinstance(rows[state.cursor], _FindingRow)
    assert state.cursor == 2  # Last finding row.


def test_toggle_only_affects_finding_rows() -> None:
    """Space on a header row is a no-op — defends against an
    upstream-keybind change accidentally toggling section state."""
    sections = [
        Section(title="A", findings=[_f("a")]),
        Section(title="B", findings=[_f("b")]),
    ]
    rows, _ = _build_rows(sections)
    state = _State(cursor=0, selected=set(), viewport_top=0)
    state.toggle(rows)  # Cursor on header_A.
    assert state.selected == set()


def test_select_all_picks_every_finding_across_sections() -> None:
    sections = [
        Section(title="A", findings=[_f("a"), _f("b")]),
        Section(title="B", findings=[_f("c")]),
    ]
    rows, _ = _build_rows(sections)
    state = _State(cursor=1, selected=set(), viewport_top=0)
    state.select_all(rows)
    assert state.selected == {0, 1, 2}


def test_select_none_clears_across_sections() -> None:
    sections = [Section(title="", findings=[_f("a"), _f("b")], preselected={0, 1})]
    _build_rows(sections)
    state = _State(cursor=0, selected={0, 1}, viewport_top=0)
    state.select_none()
    assert state.selected == set()


def test_select_section_only_picks_rows_in_that_section() -> None:
    """``a`` sweeps the cursor's current section without touching
    selections in other sections — preserves a hand-curated other
    section while bulk-checking the active one."""
    sections = [
        Section(title="A", findings=[_f("a"), _f("b")]),
        Section(title="B", findings=[_f("c"), _f("d")]),
    ]
    rows, _ = _build_rows(sections)
    # Pre-existing selection in section B (flat 3) — should survive.
    state = _State(cursor=0, selected={3}, viewport_top=0)
    state.select_section(rows, section_idx=0)
    # Section A's flats are 0 + 1; section B's pre-existing 3 stays.
    assert state.selected == {0, 1, 3}


def test_deselect_section_clears_only_that_section() -> None:
    """``n`` clears the cursor's current section but leaves other
    sections' selections untouched."""
    sections = [
        Section(title="A", findings=[_f("a"), _f("b")]),
        Section(title="B", findings=[_f("c"), _f("d")]),
    ]
    rows, _ = _build_rows(sections)
    state = _State(cursor=0, selected={0, 1, 2, 3}, viewport_top=0)
    state.deselect_section(rows, section_idx=1)
    # Section B (flat 2, 3) cleared; section A untouched.
    assert state.selected == {0, 1}


# -- _format_title (MCP usage badges) --------------------------------------


def _mcp(name: str, invocations: int | None) -> Finding:
    return Finding(
        id=f"mcp:{name}",
        type="mcp_inventory",
        title=name,
        scope=Scope(kind="global"),
        action=Action(primitive="remove_mcp", server_name=name),
        invocations=invocations,
    )


def test_format_title_appends_invocation_count_for_mcp() -> None:
    text = _render_text(_format_title(_mcp("notion", 35), is_cursor=False))
    assert "notion" in text
    assert "35 in 30d" in text


def test_format_title_marks_zero_invocations_as_unused() -> None:
    text = _render_text(_format_title(_mcp("polymarket-docs", 0), is_cursor=False))
    assert "polymarket-docs" in text
    assert "0 in 30d" in text
    assert "[unused]" in text


def test_format_title_omits_usage_for_non_mcp() -> None:
    """Agents/skills/commands carry invocations=None — no suffix at all."""
    finding = Finding(
        id="agent:reviewer",
        type="agent_inventory",
        title="reviewer",
        scope=Scope(kind="global"),
        action=Action(primitive="delete_file"),
        invocations=None,
    )
    text = _render_text(_format_title(finding, is_cursor=False)).strip()
    assert text == "reviewer"


def test_format_title_thousands_separator_for_busy_servers() -> None:
    text = _render_text(_format_title(_mcp("github", 1234), is_cursor=False))
    assert "1,234 in 30d" in text


# -- InvocationView (async-fed MCP usage) ----------------------------------


def _mcp_finding_with_static_count(name: str, count: int) -> Finding:
    return Finding(
        id=f"mcp:{name}",
        type="mcp_inventory",
        title=name,
        scope=Scope(kind="global"),
        action=Action(primitive="remove_mcp", server_name=name),
        invocations=count,
    )


def test_format_title_renders_loading_placeholder_when_view_counts_is_none() -> None:
    """View provided but not yet populated → MCP row shows ``· …``."""
    from unclog.ui.picker import InvocationView

    finding = _mcp_finding_with_static_count("notion", 99)  # static value should be ignored
    view = InvocationView(counts=None)
    text = _render_text(_format_title(finding, is_cursor=False, invocation_view=view))
    assert "·" in text
    assert "…" in text
    assert "99 in 30d" not in text  # static fallback must not leak


def test_format_title_uses_view_counts_once_view_is_ready() -> None:
    """Empty dict counts as ready — server missing → 0 (legitimate post-walk answer)."""
    from unclog.ui.picker import InvocationView

    finding = _mcp_finding_with_static_count("notion", 99)  # static ignored when view ready
    view = InvocationView(counts={"notion": 12})
    text = _render_text(_format_title(finding, is_cursor=False, invocation_view=view))
    assert "12 in 30d" in text
    assert "[unused]" not in text


def test_format_title_view_with_zero_count_marks_unused() -> None:
    from unclog.ui.picker import InvocationView

    finding = _mcp_finding_with_static_count("ghost", 50)
    view = InvocationView(counts={})  # walk completed; server not seen anywhere
    text = _render_text(_format_title(finding, is_cursor=False, invocation_view=view))
    assert "0 in 30d" in text
    assert "[unused]" in text


def test_format_title_no_view_falls_back_to_static_invocations() -> None:
    """The synchronous code path (no view passed) still works."""
    finding = _mcp_finding_with_static_count("notion", 7)
    text = _render_text(_format_title(finding, is_cursor=False))  # no view kwarg
    assert "7 in 30d" in text


def test_format_title_non_mcp_ignores_view_entirely() -> None:
    """Agents/skills/commands never get a usage suffix even with a view."""
    from unclog.ui.picker import InvocationView

    finding = Finding(
        id="agent:reviewer",
        type="agent_inventory",
        title="reviewer",
        scope=Scope(kind="global"),
        action=Action(primitive="delete_file"),
    )
    view = InvocationView(counts={"reviewer": 100})  # red herring
    text = _render_text(_format_title(finding, is_cursor=False, invocation_view=view)).strip()
    assert text == "reviewer"


def test_invocation_view_starts_unloaded() -> None:
    """Default constructor: counts=None signals the picker to render placeholders."""
    from unclog.ui.picker import InvocationView

    assert InvocationView().counts is None


def test_invocation_view_mutation_visible_to_picker_format() -> None:
    """Mutating view.counts after construction is observed by _format_title.

    This is the contract the async cli flow depends on — the background
    thread sets counts, and the *next* render picks it up.
    """
    from unclog.ui.picker import InvocationView

    view = InvocationView()
    finding = _mcp_finding_with_static_count("notion", 0)
    before = _render_text(_format_title(finding, is_cursor=False, invocation_view=view))
    assert "…" in before

    view.counts = {"notion": 42}  # background thread did its work
    after = _render_text(_format_title(finding, is_cursor=False, invocation_view=view))
    assert "42 in 30d" in after
