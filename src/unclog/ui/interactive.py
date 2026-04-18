"""Interactive fix flow: prompt → select → apply → summary.

Two-phase flow per spec §3.1:

1. Print the scan report, then immediately open a Rich Live multiselect
   picker. The picker is the decision surface — no pre-prompt is needed
   and an empty selection exits without mutating anything.
2. After selection, confirm with ``Apply N changes? [y/N]`` (default No).
3. On accept, create a snapshot and run :mod:`unclog.apply.runner`.
   The result is rendered with file paths and token savings.

Safety defaults (spec §3.2):

- The apply confirm defaults to No — mashing enter exits cleanly.
- Findings start unchecked regardless of detector ``auto_checked``;
  the bulk ``A``/``a``/``n`` keybinds cover the sweep case.
- ``--yes`` skips the picker and applies every auto-checked finding.
  Opt-in findings are silently *excluded* — consent is still required
  for them even in yes-mode.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from unclog.apply.runner import ApplyResult, apply_findings
from unclog.findings.base import Finding
from unclog.ui.chrome import rounded_panel, status_glyph
from unclog.ui.countdown import animate_countdown
from unclog.ui.picker import run_rich_multiselect
from unclog.ui.share import render_share_stat
from unclog.ui.theme import ACCENT, DIM, SEVERITY_BAD, SEVERITY_OK
from unclog.util.paths import ClaudePaths

# Same connector used in the scan report to anchor nested child rows
# to their parent headline.
_CONNECTOR = "⎿"


class Prompter(Protocol):
    """Pluggable I/O so tests can drive the flow without a real TTY."""

    def confirm(self, message: str, default: bool) -> bool: ...

    def multiselect(
        self,
        message: str,
        choices: list[tuple[str, Finding]],
        defaults: set[str],
        *,
        subtitle: str | None = None,
    ) -> list[Finding]: ...


class RichPrompter:
    """Default prompter backed by :mod:`unclog.ui.picker`.

    The picker is a Rich ``Live`` repaint loop driven by ``readchar``.
    Compared to the curses-backed ``pick`` library or prompt_toolkit's
    CPR-based loop, this gives us truecolor category badges, a live
    running-total footer, and reliable input handling across every
    terminal emulator we've tested.
    """

    def __init__(self, console: Console) -> None:
        self._console = console

    def confirm(self, message: str, default: bool) -> bool:
        suffix = " [Y/n] " if default else " [y/N] "
        try:
            answer = input(message + suffix).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if not answer:
            return default
        return answer.startswith("y")

    def multiselect(
        self,
        message: str,
        choices: list[tuple[str, Finding]],
        defaults: set[str],
        *,
        subtitle: str | None = None,
    ) -> list[Finding]:
        if not choices:
            return []
        findings = [finding for _, finding in choices]
        preselected = {
            i for i, (title, _) in enumerate(choices) if title in defaults
        }
        return run_rich_multiselect(
            findings,
            title=message,
            preselected=preselected,
            console=self._console,
            subtitle=subtitle,
        )


@dataclass
class InteractiveOptions:
    """Runtime knobs for :func:`run_interactive`.

    - ``yes``: no prompts; apply every auto-checked finding.
    - ``no_animation``: reserved for M6 polish; accepted now so CLI
      plumbing stabilises.
    """

    yes: bool = False
    no_animation: bool = False


def run_interactive(
    findings: list[Finding],
    *,
    claude_home: Path,
    project_paths: tuple[Path, ...],
    console: Console,
    options: InteractiveOptions,
    prompter: Prompter | None = None,
    baseline_tokens: int | None = None,
    curate_findings: list[Finding] | None = None,
) -> ApplyResult | None:
    """Run the interactive fix flow. Returns the last apply result, or None.

    Two sequential decision surfaces:

    1. **Primary picker** — detector findings (real problems). Auto-check
       state mirrors ``Finding.auto_checked``. Confirm then apply.
    2. **Curate picker** — opt-in secondary picker over
       ``curate_findings`` (every local agent + skill, enumerated).
       Offered with a y/N prompt *after* primary resolves, so users who
       want to prune by hand aren't drowned by 200 rows at startup.

    ``None`` means no mutations happened. A non-None result is the last
    apply's result (curate if curate ran, else primary).
    """
    curate_findings = curate_findings or []
    if not findings and not curate_findings:
        return None

    applicable = [f for f in findings if f.action.primitive != "flag_only"]

    # --yes path: apply every auto-checked primary finding non-interactively.
    # Curate is never auto-applied — consent is always per-item.
    if options.yes:
        if not applicable:
            return None
        auto = [f for f in applicable if f.auto_checked]
        if not auto:
            console.print("[dim]--yes: no auto-checked findings to apply.[/dim]")
            return None
        return _execute(
            auto,
            claude_home=claude_home,
            project_paths=project_paths,
            console=console,
            animate=not options.no_animation,
            baseline_tokens=baseline_tokens,
        )

    if prompter is None:
        if not _stdin_is_tty():
            # No interactive input available and --yes wasn't set. Silently
            # skip the fix flow; the report already printed.
            return None
        active_prompter: Prompter = RichPrompter(console)
    else:
        active_prompter = prompter

    # Primary flow first. When there are only informational (flag-only)
    # findings, render the manual-remediation hints — even if curate is
    # also available, since the hints are independent actionable next
    # steps (".claudeignore" creation, etc.).
    #
    # When curate is available, defer the countdown + retention warn
    # out of primary's _execute so the user sees one cumulative
    # "N,NNN → M,MMM" animation at the end rather than two separate ones.
    # Titles pick up a "Step 1 of 2 — ..." prefix only when a second
    # curate step will actually fire, so single-step runs don't suggest
    # the user is missing something.
    defer_final_tally = bool(curate_findings)
    two_step = bool(curate_findings)
    primary_subtitle = "Step 1 of 2" if two_step else None
    primary_result: ApplyResult | None = None
    if applicable:
        primary_result = _run_primary_picker(
            applicable,
            active_prompter=active_prompter,
            console=console,
            claude_home=claude_home,
            project_paths=project_paths,
            animate=not options.no_animation,
            baseline_tokens=baseline_tokens,
            include_final_tally=not defer_final_tally,
            title="Select fixes to apply",
            subtitle=primary_subtitle,
        )
    elif findings:
        _render_informational_next_steps(findings, console)

    if not curate_findings:
        return primary_result

    primary_savings = primary_result.token_savings if primary_result is not None else 0
    # Only label curate as "Step 2 of 2" when a primary picker was
    # actually shown — otherwise the step numbering implies a step the
    # user never saw.
    curate_subtitle = "Step 2 of 2" if applicable else None
    curate_result = _maybe_run_curate(
        curate_findings,
        active_prompter=active_prompter,
        console=console,
        claude_home=claude_home,
        project_paths=project_paths,
        animate=not options.no_animation,
        baseline_tokens=baseline_tokens,
        previous_savings=primary_savings,
        title="Select items to delete",
        subtitle=curate_subtitle,
    )

    # If curate was declined but primary applied, the deferred countdown
    # still needs to fire — otherwise the user never sees the animation
    # for their primary savings.
    if (
        curate_result is None
        and primary_result is not None
        and primary_savings
        and baseline_tokens is not None
    ):
        _render_final_tally(
            console=console,
            claude_home=claude_home,
            baseline_tokens=baseline_tokens,
            tokens_saved=primary_savings,
            animate=not options.no_animation,
        )

    return curate_result if curate_result is not None else primary_result


def _run_primary_picker(
    applicable: list[Finding],
    *,
    active_prompter: Prompter,
    console: Console,
    claude_home: Path,
    project_paths: tuple[Path, ...],
    animate: bool,
    baseline_tokens: int | None,
    include_final_tally: bool = True,
    title: str = "Select fixes to apply",
    subtitle: str | None = None,
) -> ApplyResult | None:
    # Sort descending by token weight so the biggest wins are at the top
    # of the picker regardless of check state. Entries without a measured
    # savings value sort last.
    sorted_applicable = sorted(
        applicable,
        key=lambda f: (f.token_savings is None, -(f.token_savings or 0), f.title),
    )
    choices = [(_format_choice(f), f) for f in sorted_applicable]
    defaults = {title_str for title_str, f in choices if f.auto_checked}
    selected = active_prompter.multiselect(
        title, choices=choices, defaults=defaults, subtitle=subtitle
    )
    if not selected:
        console.print("[dim]Nothing selected — continuing.[/dim]")
        return None

    if not active_prompter.confirm(
        f"Apply {len(selected)} change(s)?", default=False
    ):
        return None

    return _execute(
        selected,
        claude_home=claude_home,
        project_paths=project_paths,
        console=console,
        animate=animate,
        baseline_tokens=baseline_tokens,
        include_final_tally=include_final_tally,
    )


def _maybe_run_curate(
    curate_findings: list[Finding],
    *,
    active_prompter: Prompter,
    console: Console,
    claude_home: Path,
    project_paths: tuple[Path, ...],
    animate: bool,
    baseline_tokens: int | None,
    previous_savings: int = 0,
    title: str = "Select items to delete",
    subtitle: str | None = None,
) -> ApplyResult | None:
    """Offer the opt-in per-item curate picker; return apply result or None.

    Shows a one-line summary (count + total token cost) so the user can
    decide whether a hand-prune is worth it before opening a 200-row
    picker. ``n``/empty answer declines quietly — the report already
    printed, so no mutation is a valid outcome.
    """
    total_tokens = sum(f.token_savings or 0 for f in curate_findings)
    summary = _format_curate_summary(curate_findings, total_tokens)
    console.print("")
    if not active_prompter.confirm(summary, default=False):
        return None

    choices = [(_format_choice(f), f) for f in curate_findings]
    selected = active_prompter.multiselect(
        title, choices=choices, defaults=set(), subtitle=subtitle
    )
    if not selected:
        console.print("[dim]Nothing selected — exiting without changes.[/dim]")
        return None

    if not active_prompter.confirm(
        f"Delete {len(selected)} item(s)?", default=False
    ):
        return None

    return _execute(
        selected,
        claude_home=claude_home,
        project_paths=project_paths,
        console=console,
        animate=animate,
        baseline_tokens=baseline_tokens,
        previous_savings=previous_savings,
    )


def _format_curate_summary(findings: list[Finding], total_tokens: int) -> str:
    n_agents = sum(1 for f in findings if f.type == "agent_inventory")
    n_skills = sum(1 for f in findings if f.type == "skill_inventory")
    n_mcps = sum(1 for f in findings if f.type == "unmeasured_mcp")
    parts: list[str] = []
    if n_agents:
        parts.append(f"{n_agents} agent(s)")
    if n_skills:
        parts.append(f"{n_skills} skill(s)")
    if n_mcps:
        parts.append(f"{n_mcps} remote MCP(s)")
    label = " + ".join(parts) or f"{len(findings)} item(s)"
    tokens = f"~{total_tokens:,} tok" if total_tokens else "unmeasured"
    return f"Review {label} one-by-one ({tokens})?"


def _execute(
    findings: list[Finding],
    *,
    claude_home: Path,
    project_paths: tuple[Path, ...],
    console: Console,
    animate: bool,
    baseline_tokens: int | None,
    include_final_tally: bool = True,
    previous_savings: int = 0,
) -> ApplyResult | None:
    """Apply ``findings``, render the result, and optionally show the tally.

    ``include_final_tally=False`` skips the countdown + share stat +
    retention warn so the caller can show a single cumulative version
    later. ``previous_savings`` offsets the countdown's starting point
    so the animation represents *total session* savings, not just this
    call's.
    """
    paths = ClaudePaths(home=claude_home)
    result = apply_findings(
        findings,
        claude_home=claude_home,
        snapshots_dir=paths.snapshots_dir,
        project_paths=project_paths,
    )
    _render_result(result, console)
    if include_final_tally:
        total_saved = previous_savings + result.token_savings
        if baseline_tokens is not None and total_saved:
            console.print("")
            animate_countdown(
                console,
                before=baseline_tokens,
                after=baseline_tokens - total_saved,
                animate=animate,
            )
            render_share_stat(
                console,
                baseline_tokens=baseline_tokens,
                tokens_saved=total_saved,
            )
        _maybe_warn_retention(paths, console)
    return result


def _render_final_tally(
    *,
    console: Console,
    claude_home: Path,
    baseline_tokens: int,
    tokens_saved: int,
    animate: bool,
) -> None:
    """Deferred countdown + retention warn for the primary-only path.

    When curate is offered and declined but primary applied, we need to
    fire the tally that primary's ``_execute`` skipped (since it was
    waiting to be combined with curate).
    """
    console.print("")
    animate_countdown(
        console,
        before=baseline_tokens,
        after=baseline_tokens - tokens_saved,
        animate=animate,
    )
    render_share_stat(
        console,
        baseline_tokens=baseline_tokens,
        tokens_saved=tokens_saved,
    )
    _maybe_warn_retention(ClaudePaths(home=claude_home), console)


def _render_result(result: ApplyResult, console: Console) -> None:
    """Render the post-apply panel in the Claude-Code visual vocabulary.

    Panel title carries a status glyph — ``⏺ Applied`` on success,
    ``! Applied with failures`` when any action failed. The headline
    row (``N changes applied · ~X,XXX tokens saved``) sits at the top
    of the body; per-action rows use ``⎿ ✓`` connectors; snapshot +
    undo sit in a two-row label grid mirroring the welcome panel.
    Border colour is the one place in the product where colour encodes
    semantics (success vs failure) — kept.
    """
    console.print("")
    blocks: list[object] = []

    summary = Text()
    summary.append(f"{len(result.succeeded)}", style=f"bold {SEVERITY_OK}")
    summary.append(" changes applied", style=DIM)
    if result.token_savings:
        summary.append("  ·  ", style=DIM)
        summary.append(f"~{result.token_savings:,}", style=f"bold {SEVERITY_OK}")
        summary.append(" tokens saved", style=DIM)
    blocks.append(summary)

    if result.succeeded:
        blocks.append(Text(""))
        for finding, _ in result.succeeded:
            row = Text()
            row.append(f"  {_CONNECTOR} ", style=DIM)
            row.append("✓ ", style=SEVERITY_OK)
            savings = finding.token_savings
            if savings is not None:
                row.append(f"{savings:>6,} tok  ", style=DIM)
            else:
                row.append("     — tok  ", style=DIM)
            row.append(finding.title, style="default")
            blocks.append(row)

    if result.failed:
        blocks.append(Text(""))
        fail_header = Text()
        fail_header.append("! ", style=f"bold #eab308")
        fail_header.append(f"{len(result.failed)} action(s) failed", style=SEVERITY_BAD)
        blocks.append(fail_header)
        for finding, reason in result.failed:
            row = Text()
            row.append(f"  {_CONNECTOR} ", style=DIM)
            row.append("✗ ", style=SEVERITY_BAD)
            row.append(finding.title, style="default")
            row.append(f"  — {reason}", style=DIM)
            blocks.append(row)

    blocks.append(Text(""))
    meta = Table.grid(padding=(0, 2))
    meta.add_column(style=DIM, no_wrap=True)
    meta.add_column(no_wrap=False)
    meta.add_row("snapshot", Text(str(result.snapshot.root), style="default"))
    meta.add_row(
        "undo",
        Text(f"unclog restore {result.snapshot.id}", style=f"bold {ACCENT}"),
    )
    blocks.append(meta)

    # Title: glyph carries the headline. ⏺ on success, ! on partial
    # failure. Border colour still encodes state for at-a-glance scan.
    if result.failed:
        title = Text()
        title.append_text(status_glyph("attention"))
        title.append("Applied with failures", style=f"bold {ACCENT}")
        border = SEVERITY_BAD
    else:
        title = Text()
        title.append_text(status_glyph("running"))
        title.append("Applied", style=f"bold {ACCENT}")
        border = SEVERITY_OK

    console.print(rounded_panel(Group(*blocks), title=title, border=border))


def _render_informational_next_steps(
    findings: list[Finding], console: Console
) -> None:
    """Print a manual-remediation hint block when nothing is auto-applicable.

    Flag-only findings are surfaced by detectors that can identify a
    problem but intentionally decline to fix it automatically (missing
    ``.claudeignore``, recently-disabled plugin residue, etc. — spec §6).
    Rather than exiting silently, we give the user a concrete next step
    per finding type so they know what to do.

    Rendered in the same rounded-panel vocabulary as the applied panel:
    ``!`` amber glyph on the title, ``⎿`` connectors for each row.
    """
    console.print("")
    blocks: list[object] = []

    header = Text()
    header.append("No auto-fixable issues.", style="bold")
    header.append(
        f"  {len(findings)} informational finding(s) — handle manually:",
        style=DIM,
    )
    blocks.append(header)
    blocks.append(Text(""))

    seen_hints: set[str] = set()
    for f in findings:
        hint = _manual_hint_for(f)
        key = f"{f.type}:{hint}"
        if key in seen_hints:
            continue
        seen_hints.add(key)
        row = Text()
        row.append(f"  {_CONNECTOR} ", style=DIM)
        row.append(f.title, style="default")
        row.append(f"  → {hint}", style=DIM)
        blocks.append(row)

    blocks.append(Text(""))
    footer = Text()
    footer.append("Run ", style=DIM)
    footer.append("unclog --json", style=f"bold {ACCENT}")
    footer.append(" for full evidence on each finding.", style=DIM)
    blocks.append(footer)

    title = Text()
    title.append_text(status_glyph("attention"))
    title.append("Manual next steps", style=f"bold {ACCENT}")
    console.print(rounded_panel(Group(*blocks), title=title))


def _manual_hint_for(finding: Finding) -> str:
    """Human next-step for a flag-only finding."""
    path = finding.action.path
    plugin_key = finding.action.plugin_key
    match finding.type:
        case "missing_claudeignore":
            target = str(path) if path else ".claudeignore"
            return f"create {target} with node_modules/ .venv/ etc."
        case "disabled_plugin_residue":
            key = plugin_key or finding.id.split(":", 1)[-1]
            return (
                f"leave in place; unclog will offer removal once "
                f"{key!r} is long-disabled"
            )
        case "claude_md_dead_ref":
            target = str(path) if path else "the referenced CLAUDE.md"
            return f"review and rewrite surrounding prose in {target}"
        case "failed_mcp_probe":
            server = finding.action.server_name or "the server"
            return (
                f"check that {server!r}'s command is on PATH and starts "
                f"cleanly; see --json evidence for captured stderr"
            )
        case _:
            return "see --json output for evidence"


_RETENTION_WARN_THRESHOLD = 20


def _maybe_warn_retention(paths: ClaudePaths, console: Console) -> None:
    if not paths.snapshots_dir.is_dir():
        return
    count = sum(1 for child in paths.snapshots_dir.iterdir() if child.is_dir())
    if count > _RETENTION_WARN_THRESHOLD:
        console.print(
            f"[#eab308]![/#eab308] [dim]{count} snapshots stored at "
            f"{paths.snapshots_dir} — consider pruning (coming in v0.2).[/dim]"
        )


def _format_choice(finding: Finding) -> str:
    """Format one row of the multiselect picker.

    Token count is positioned on the LEFT (right-padded to a fixed width)
    so any terminal-width truncation never drops the most
    important piece of information. Example line:

        4,192 tok  [global] Remove agent Frontend Developer
    """
    savings = (
        f"{finding.token_savings:>6,} tok"
        if finding.token_savings is not None
        else "     — tok"
    )
    scope_kind = finding.scope.kind
    return f"{savings}  [{scope_kind}] {finding.title}"


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


__all__ = [
    "InteractiveOptions",
    "Prompter",
    "RichPrompter",
    "run_interactive",
]
