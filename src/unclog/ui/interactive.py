"""Interactive fix flow: prompt → select → apply → summary.

Two-phase flow per spec §3.1:

1. After the scan report is printed, prompt ``Fix these? [y/N]``. The
   default is No so mashing enter exits cleanly.
2. If the user accepts, show a curses-backed pick checkbox list. Findings
   whose ``auto_checked`` bit is ``True`` are pre-ticked; everything
   else starts unchecked. The user toggles selections, hits enter.
3. A second prompt ``Apply N changes? [y/N]`` confirms the shortlist.
4. If accepted, create a snapshot and run :mod:`unclog.apply.runner`.
   The result is rendered with file paths and token savings.

Safety defaults (spec §3.2):

- Both Y/N prompts default to No.
- Dead-MCP and "broken" findings are never pre-ticked even when the
  detector's auto_checked says so (``dead_mcp`` comes back false from
  the detector already; this is a belt-and-braces filter).
- ``--dry-run`` short-circuits right before apply: the user sees the
  plan, no snapshot is created, no files change.
- ``--yes`` skips both prompts and applies every auto-checked finding.
  Opt-in findings are silently *excluded* — consent is still required
  for them even in yes-mode.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pick import Picker
from rich.console import Console

from unclog.apply.runner import ApplyResult, apply_findings
from unclog.findings.base import Finding
from unclog.ui.countdown import animate_countdown
from unclog.ui.share import render_share_stat
from unclog.util.paths import ClaudePaths


class Prompter(Protocol):
    """Pluggable I/O so tests can drive the flow without a real TTY."""

    def confirm(self, message: str, default: bool) -> bool: ...

    def multiselect(
        self, message: str, choices: list[tuple[str, Finding]], defaults: set[str]
    ) -> list[Finding]: ...


class PickPrompter:
    """Default prompter backed by the ``pick`` library (curses-based).

    We deliberately avoid ``questionary``/``prompt_toolkit`` here because
    their rendering path depends on terminal Cursor Position Request
    (CPR) support. Some terminals (and all ``expect`` ptys) silently
    drop those queries, causing the picker to collapse into "accept
    every default" with no way to toggle. ``pick`` uses curses directly,
    which works reliably in every interactive terminal we've tested.
    """

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
        self, message: str, choices: list[tuple[str, Finding]], defaults: set[str]
    ) -> list[Finding]:
        if not choices:
            return []
        titles = [title for title, _ in choices]
        findings = [finding for _, finding in choices]
        preselected = [i for i, title in enumerate(titles) if title in defaults]
        picker = Picker(
            options=titles,
            title=(
                f"{message}\n"
                "↑/↓ move · space toggle · enter submit · q quit"
            ),
            multiselect=True,
            min_selection_count=0,
        )
        # ``pick`` exposes a mutable attribute for pre-selection — this is
        # the documented way to check items before the picker starts.
        picker.selected_indexes = list(preselected)
        result = picker.start()
        # Multiselect returns list[tuple[option, index]]; single returns tuple.
        if not isinstance(result, list):
            return []
        return [findings[index] for _, index in result]


@dataclass
class InteractiveOptions:
    """Runtime knobs for :func:`run_interactive`.

    - ``dry_run``: prompts still run, but the apply phase is skipped.
    - ``yes``: no prompts; apply every auto-checked finding.
    - ``no_animation``: reserved for M6 polish; accepted now so CLI
      plumbing stabilises.
    """

    dry_run: bool = False
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
) -> ApplyResult | None:
    """Run the interactive fix flow. Returns the apply result, or None.

    ``None`` means the user exited before apply (said No to either
    prompt, deselected everything, or we're in dry-run mode). Callers
    should treat ``None`` as "no mutations happened."
    """
    if not findings:
        return None

    applicable = [f for f in findings if f.action.primitive != "flag_only"]
    if not applicable:
        _render_informational_next_steps(findings, console)
        return None

    if options.yes:
        auto = [f for f in applicable if f.auto_checked]
        if not auto:
            console.print("[dim]--yes: no auto-checked findings to apply.[/dim]")
            return None
        return _execute(
            auto,
            claude_home=claude_home,
            project_paths=project_paths,
            console=console,
            dry_run=options.dry_run,
            animate=not options.no_animation,
            baseline_tokens=baseline_tokens,
        )

    if prompter is None:
        if not _stdin_is_tty():
            # No interactive input available and --yes wasn't set. Silently
            # skip the fix flow; the report already printed.
            return None
        active_prompter: Prompter = PickPrompter()
    else:
        active_prompter = prompter

    # Sort descending by token weight so the biggest wins are at the top
    # of the picker regardless of check state. Entries without a measured
    # savings value sort last.
    sorted_applicable = sorted(
        applicable,
        key=lambda f: (f.token_savings is None, -(f.token_savings or 0), f.title),
    )
    console.print("")
    console.print(
        "[dim]Signal note: v0.1 only sees [/dim][bold]@mentions[/bold]"
        "[dim] in history. Agents invoked via the Task tool or by other "
        "agents leave no trace and will appear pre-checked even if active. "
        "Review before confirming.[/dim]"
    )
    choices = [(_format_choice(f), f) for f in sorted_applicable]
    defaults = {title for title, f in choices if f.auto_checked}
    selected = active_prompter.multiselect(
        "Select fixes to apply:", choices=choices, defaults=defaults
    )
    if not selected:
        console.print("[dim]Nothing selected — exiting without changes.[/dim]")
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
        dry_run=options.dry_run,
        animate=not options.no_animation,
        baseline_tokens=baseline_tokens,
    )


def _execute(
    findings: list[Finding],
    *,
    claude_home: Path,
    project_paths: tuple[Path, ...],
    console: Console,
    dry_run: bool,
    animate: bool,
    baseline_tokens: int | None,
) -> ApplyResult | None:
    if dry_run:
        console.print(f"[dim]--dry-run: would apply {len(findings)} change(s).[/dim]")
        for f in findings:
            console.print(f"  [dim]-[/dim] {f.title}")
        return None
    paths = ClaudePaths(home=claude_home)
    result = apply_findings(
        findings,
        claude_home=claude_home,
        snapshots_dir=paths.snapshots_dir,
        project_paths=project_paths,
    )
    _render_result(result, console)
    if baseline_tokens is not None and result.token_savings:
        console.print("")
        animate_countdown(
            console,
            before=baseline_tokens,
            after=baseline_tokens - result.token_savings,
            animate=animate,
        )
        render_share_stat(
            console,
            baseline_tokens=baseline_tokens,
            tokens_saved=result.token_savings,
        )
    _maybe_warn_retention(paths, console)
    return result


def _render_result(result: ApplyResult, console: Console) -> None:
    console.print("")
    console.print(f"[#22c55e]\u2713[/#22c55e] Snapshot  [dim]{result.snapshot.root}[/dim]")
    console.print(
        f"[#22c55e]\u2713[/#22c55e] Applied {len(result.succeeded)} change(s)"
    )
    if result.token_savings:
        console.print(
            f"  [dim]Saved ~{result.token_savings:,} tokens.[/dim]"
        )
    if result.failed:
        console.print("")
        console.print(f"[#ef4444]! {len(result.failed)} action(s) failed:[/#ef4444]")
        for finding, reason in result.failed:
            console.print(f"  [dim]- {finding.title}: {reason}[/dim]")
    console.print("")
    console.print(f"[dim]Undo:  unclog restore {result.snapshot.id}[/dim]")


def _render_informational_next_steps(
    findings: list[Finding], console: Console
) -> None:
    """Print a manual-remediation hint block when nothing is auto-applicable.

    Flag-only findings are surfaced by detectors that can identify a
    problem but intentionally decline to fix it automatically (missing
    ``.claudeignore``, recently-disabled plugin residue, etc. — spec §6).
    Rather than exiting silently, we give the user a concrete next step
    per finding type so they know what to do.
    """
    console.print("")
    console.print(
        f"[bold]No auto-fixable issues.[/bold] "
        f"[dim]{len(findings)} informational finding(s) — handle manually:[/dim]"
    )
    seen_hints: set[str] = set()
    for f in findings:
        hint = _manual_hint_for(f)
        key = f"{f.type}:{hint}"
        if key in seen_hints:
            continue
        seen_hints.add(key)
        console.print(f"  [dim]·[/dim] {f.title}  [dim]→ {hint}[/dim]")
    console.print("")
    console.print(
        "[dim]Run [/dim][bold]unclog --json[/bold][dim] for full evidence "
        "on each finding.[/dim]"
    )


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
    "PickPrompter",
    "Prompter",
    "run_interactive",
]
