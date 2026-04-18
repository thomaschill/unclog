from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from unclog import __version__
from unclog.app import run_scan
from unclog.apply.restore import restore_snapshot
from unclog.apply.snapshot import SnapshotError, list_snapshots, load_snapshot
from unclog.state import InstallationState
from unclog.ui.display import DisplayOptions
from unclog.ui.interactive import InteractiveOptions, run_interactive
from unclog.ui.output import (
    baseline_tokens,
    render_claude_md_listing_plain,
    render_claude_md_listing_rich,
    render_json,
    render_plain,
    render_rich,
)
from unclog.util.paths import claude_paths

app = typer.Typer(
    name="unclog",
    help="Audit your Claude Code installation and clear context-window bloat. Local-only.",
    no_args_is_help=False,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"unclog {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit a single JSON document to stdout (schema unclog.v0.1).",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="ASCII-only, colour-free output (auto when piped or NO_COLOR is set).",
    ),
    project: Path | None = typer.Option(
        None,
        "--project",
        help="Narrow the audit to a single project (default: every known project).",
        dir_okay=True,
        file_okay=False,
        resolve_path=False,
    ),
    report_only: bool = typer.Option(
        False,
        "--report",
        help="Scan and report, then exit without running the interactive fix flow.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Apply every auto-checked finding without prompting.",
    ),
    no_animation: bool = typer.Option(
        False,
        "--no-animation",
        help="Disable motion (post-apply countdown); keeps colour.",
    ),
    list_claude_md: bool = typer.Option(
        False,
        "--list-claude-md",
        help=(
            "Print every CLAUDE.md file unclog can see (global + per-project) "
            "with token counts, then exit. Diagnostic for verifying scan coverage."
        ),
    ),
    no_probe_mcps: bool = typer.Option(
        False,
        "--no-probe-mcps",
        help=(
            "Skip the live MCP probe. By default unclog spawns each "
            "declared MCP server via stdio JSON-RPC (serial, 5s per server, "
            "minimal-env) to measure its real tools-schema size and surface "
            "startup failures. Pass this flag to keep the scan read-only."
        ),
    ),
) -> None:
    """Scan the current Claude Code installation and print a report.

    The default mode prints the report, then (if findings exist and
    stdout is a TTY) walks through the interactive fix selector.
    ``--report``, ``--json``, and ``--plain`` all suppress the fix
    flow. ``--yes`` skips prompts and applies auto-checked findings
    only.
    """
    if ctx.invoked_subcommand is not None:
        return
    if report_only and yes:
        raise typer.BadParameter("--report cannot be combined with --yes")

    display = DisplayOptions.resolve(
        as_json=as_json,
        plain_flag=plain,
        report_only=report_only,
        no_animation_flag=no_animation,
    )

    console = Console(no_color=not display.colour)

    state = run_scan(project=project, probe_mcps=not no_probe_mcps)

    if list_claude_md:
        if display.plain:
            typer.echo(render_claude_md_listing_plain(state), nl=False)
        else:
            render_claude_md_listing_rich(state, console)
        return

    if as_json:
        typer.echo(render_json(state))
        return

    if display.plain:
        typer.echo(render_plain(state), nl=False)
    else:
        render_rich(state, console, show_wordmark=display.show_wordmark)

    if report_only or as_json or display.plain:
        return

    _launch_interactive(
        state,
        console=console,
        yes=yes,
        no_animation=no_animation,
    )


def _launch_interactive(
    state: InstallationState,
    *,
    console: Console,
    yes: bool,
    no_animation: bool,
) -> None:
    """Gate the interactive fix flow behind the scan + findings layer."""
    from unclog.findings import detect, load_thresholds
    from unclog.findings.curate import build_curate_findings

    paths = claude_paths()
    thresholds = load_thresholds(paths.config_toml)
    findings = detect(
        state,
        state.global_scope.activity,
        thresholds,
        now=state.generated_at,
    )
    curate_findings = build_curate_findings(state)
    if not findings and not curate_findings:
        return
    project_paths = tuple(p.path for p in state.project_scopes if p.exists)
    run_interactive(
        findings,
        claude_home=state.claude_home,
        project_paths=project_paths,
        console=console,
        options=InteractiveOptions(
            yes=yes,
            no_animation=no_animation,
        ),
        baseline_tokens=baseline_tokens(state),
        curate_findings=curate_findings,
    )


@app.command("restore")
def restore(
    snapshot_id: str = typer.Argument(
        "latest",
        help="Snapshot id to restore. Use 'latest' (default) or run without an id to list.",
    ),
    list_only: bool = typer.Option(
        False,
        "--list",
        help="List every snapshot without restoring.",
    ),
) -> None:
    """Restore a previous ``unclog`` snapshot.

    With no arguments, ``unclog restore`` restores the most recent
    snapshot. ``unclog restore <id>`` targets a specific one.
    ``unclog restore --list`` enumerates every snapshot on disk.
    """
    paths = claude_paths()
    console = Console()
    if list_only:
        _render_snapshot_list(paths.snapshots_dir, console)
        return

    try:
        snapshot = load_snapshot(paths.snapshots_dir, snapshot_id)
    except SnapshotError as exc:
        console.print(f"[#ef4444]{exc}[/#ef4444]")
        raise typer.Exit(code=1) from exc

    result = restore_snapshot(snapshot)
    console.print(
        f"[#22c55e]\u2713[/#22c55e] Restored snapshot [bold]{snapshot.id}[/bold] "
        f"({len(result.restored)} action(s))"
    )
    if result.failed:
        console.print(
            f"[#ef4444]! {len(result.failed)} action(s) could not be restored:[/#ef4444]"
        )
        for action, reason in result.failed:
            console.print(f"  [dim]- {action.original_path}: {reason}[/dim]")
        raise typer.Exit(code=1)


def _render_snapshot_list(snapshots_dir: Path, console: Console) -> None:
    snapshots = list_snapshots(snapshots_dir)
    if not snapshots:
        console.print(f"[dim]No snapshots under {snapshots_dir}[/dim]")
        return
    console.print(f"[bold]{len(snapshots)} snapshot(s)[/bold] [dim]{snapshots_dir}[/dim]")
    for snap in snapshots:
        created = snap.created_at.isoformat().replace("+00:00", "Z")
        console.print(
            f"  [bold]{snap.id}[/bold] [dim]{created}  {len(snap.actions)} action(s)[/dim]"
        )
