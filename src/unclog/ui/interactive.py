"""Interactive curate flow: pick items → confirm → apply → summary.

One sectioned picker lists every agent, skill, slash command, and MCP
server unclog found. The user toggles rows, presses enter, confirms,
and the apply layer deletes the selected items. Nothing is mutated
until the confirm clears.

There is no snapshot or undo — every delete is immediate and
irreversible. The confirm prompt is the only safety gate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

from rich.console import Console, Group, RenderableType
from rich.text import Text

from unclog import __repo_url__
from unclog.apply.runner import ApplyResult, apply_findings
from unclog.findings.base import Finding
from unclog.ui.chrome import rounded_panel, status_glyph
from unclog.ui.picker import InvocationView, Section, run_rich_multiselect
from unclog.ui.theme import ACCENT, DIM, SEVERITY_BAD, SEVERITY_OK
from unclog.util.paths import claude_paths

# Connector glyph anchoring nested result rows under the summary headline.
_CONNECTOR = "⎿"


class Prompter(Protocol):
    """Pluggable I/O so tests can drive the flow without a real TTY."""

    def confirm(self, message: str, default: bool) -> bool: ...

    def multiselect_sections(
        self,
        title: str,
        sections: list[Section],
        *,
        invocation_view: InvocationView | None = None,
    ) -> list[Finding]: ...


class RichPrompter:
    """Default prompter — Rich Live picker + input() confirm."""

    def __init__(self, console: Console) -> None:
        self._console = console

    def confirm(self, message: str, default: bool) -> bool:
        suffix = " [Y/n] " if default else " [y/N] "
        try:
            answer = input(message + suffix).strip().lower()
        except EOFError:
            return False
        if not answer:
            return default
        return answer.startswith("y")

    def multiselect_sections(
        self,
        title: str,
        sections: list[Section],
        *,
        invocation_view: InvocationView | None = None,
    ) -> list[Finding]:
        if not any(section.findings for section in sections):
            return []
        return run_rich_multiselect(
            sections,
            title=title,
            console=self._console,
            invocation_view=invocation_view,
        )


def run_interactive(
    curate_findings: list[Finding],
    *,
    claude_home: Path,
    console: Console,
    baseline_tokens: int,
    prompter: Prompter | None = None,
    invocation_view: InvocationView | None = None,
) -> ApplyResult | None:
    """Run the picker + apply flow. Returns ``None`` when nothing mutates.

    ``invocation_view`` is forwarded to the picker so MCP rows can pull
    invocation counts from a background-loaded source. ``None`` keeps
    the synchronous behaviour where each ``Finding.invocations`` is
    already populated.
    """
    if not curate_findings:
        return None

    if prompter is None:
        if not _stdin_is_tty():
            return None
        active_prompter: Prompter = RichPrompter(console)
    else:
        active_prompter = prompter

    sections = _build_picker_sections(curate_findings)
    if not sections:
        return None

    selected = active_prompter.multiselect_sections(
        "Select items to remove", sections, invocation_view=invocation_view
    )
    if not selected:
        console.print("[dim]Nothing selected — exiting without changes.[/dim]")
        return None

    if not active_prompter.confirm(
        f"Delete {len(selected)} item(s)? This cannot be undone.", default=False
    ):
        return None

    result = apply_findings(selected, claude_home=claude_home)
    _render_result(result, console, baseline_tokens=baseline_tokens)
    return result


def _build_picker_sections(curate_findings: list[Finding]) -> list[Section]:
    """Partition findings into Curate agents / skills / commands / MCPs sections.

    ``build_curate_findings`` already sorts by token desc; preserving that
    per-type keeps the biggest wins at the top of each section. Empty
    sections are dropped so small installs don't see "Curate MCPs" with
    a single empty header row.
    """
    agents = [f for f in curate_findings if f.type == "agent_inventory"]
    skills = [f for f in curate_findings if f.type == "skill_inventory"]
    commands = [f for f in curate_findings if f.type == "command_inventory"]
    mcps = [f for f in curate_findings if f.type == "mcp_inventory"]

    groups: list[tuple[str, list[Finding]]] = []
    if agents:
        groups.append(("Curate agents", agents))
    if skills:
        groups.append(("Curate skills", skills))
    if commands:
        groups.append(("Curate commands", commands))
    if mcps:
        groups.append(("Curate MCPs", mcps))

    if not groups:
        return []

    suppress_title = len(groups) == 1
    return [
        Section(
            title="" if suppress_title else title,
            findings=findings,
            preselected=set(),
        )
        for title, findings in groups
    ]


def _render_result(
    result: ApplyResult, console: Console, *, baseline_tokens: int
) -> None:
    """Render the post-apply panel and the single baseline-update line."""
    console.print("")
    blocks: list[RenderableType] = []

    summary = Text()
    summary.append(f"{len(result.succeeded)}", style=f"bold {SEVERITY_OK}")
    summary.append(" item(s) removed", style=DIM)
    if result.token_savings:
        summary.append("  ·  ", style=DIM)
        summary.append(f"~{result.token_savings:,}", style=f"bold {SEVERITY_OK}")
        summary.append(" tokens saved", style=DIM)
    blocks.append(summary)

    if result.succeeded:
        blocks.append(Text(""))
        for finding in result.succeeded:
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
        fail_header.append("! ", style="bold #eab308")
        fail_header.append(f"{len(result.failed)} action(s) failed", style=SEVERITY_BAD)
        blocks.append(fail_header)
        for finding, reason in result.failed:
            row = Text()
            row.append(f"  {_CONNECTOR} ", style=DIM)
            row.append("✗ ", style=SEVERITY_BAD)
            row.append(finding.title, style="default")
            row.append(f"  — {reason}", style=DIM)
            blocks.append(row)

    degraded = bool(result.failed)
    title = Text()
    if degraded:
        title.append_text(status_glyph("attention"))
        title.append("Applied with failures", style=f"bold {ACCENT}")
        border = SEVERITY_BAD
    else:
        title.append_text(status_glyph("running"))
        title.append("Applied", style=f"bold {ACCENT}")
        border = SEVERITY_OK

    console.print(rounded_panel(Group(*blocks), title=title, border=border))

    if result.token_savings:
        after = max(0, baseline_tokens - result.token_savings)
        line = Text()
        line.append("saved ", style=DIM)
        line.append(f"{result.token_savings:,}", style=f"bold {SEVERITY_OK}")
        line.append(" tokens  ·  baseline now ", style=DIM)
        line.append(f"{after:,}", style=f"bold {ACCENT}")
        console.print("")
        console.print(line)

    _maybe_show_star_line(result, console)


_STAR_SENTINEL_NAME = "star_shown"
_STAR_REPO_DISPLAY = __repo_url__.removeprefix("https://")


def _maybe_show_star_line(result: ApplyResult, console: Console) -> None:
    """Show a one-time 'star the repo' line after the first successful apply.

    Rendered at most once ever — a sentinel file under ``~/.claude/.unclog/``
    records that we've shown it. Any OS error reading or writing the
    sentinel fails open (no line shown) so a cosmetic nag never breaks
    the apply path.
    """
    if not result.succeeded:
        return
    try:
        sentinel = claude_paths().unclog_dir / _STAR_SENTINEL_NAME
        if sentinel.exists():
            return
    except OSError:
        return

    star_line = Text()
    star_line.append("★ Enjoying unclog? Star it on GitHub: ", style=DIM)
    star_line.append(_STAR_REPO_DISPLAY, style=f"dim {ACCENT}")
    console.print("")
    console.print(star_line)

    try:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
    except OSError:
        pass


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


__all__ = ["Prompter", "RichPrompter", "run_interactive"]
