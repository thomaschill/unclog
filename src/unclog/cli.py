from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from unclog import __version__
from unclog.app import run_scan
from unclog.apply.restore import restore_snapshot
from unclog.apply.snapshot import SnapshotError, list_snapshots, load_snapshot
from unclog.scan.config import ConfigParseError
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
    --report, --json, and --plain all suppress the fix flow. --yes
    skips prompts and applies auto-checked findings only.
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

    try:
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
    except (typer.Exit, typer.Abort, typer.BadParameter, SystemExit):
        raise
    except KeyboardInterrupt as exc:
        # Ctrl+C anywhere below — confirm prompt, countdown, mid-apply.
        # Print a short acknowledgement and exit 130 instead of letting
        # Python dump the traceback. ``_launch_interactive`` already
        # persists any partial snapshot, so ``unclog restore`` can still
        # recover work that was mid-flight.
        console.print()
        console.print("[dim]Cancelled.[/dim]")
        raise typer.Exit(code=130) from exc
    except (ConfigParseError, SnapshotError) as exc:
        # User-environment errors (permission denied on config, read-only
        # snapshot dir, corrupt JSON, …). These are not bugs — render a
        # clean actionable message and don't point the user at the
        # unexpected-error flow that says "file a bug report".
        _handle_environment_error(console, exc, as_json=as_json)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        _handle_unexpected_error(console, exc, as_json=as_json)
        raise typer.Exit(code=1) from exc


def _emit_json_error(kind: str, exc: BaseException) -> None:
    """Print a minimal JSON error document to stdout.

    Keeps ``--json`` consumers (CI pipelines, ``jq`` users) able to
    parse stdout even on failure. The schema mirrors the success
    document so a consumer can branch on ``"error" in doc``.
    """
    import json as _json

    doc = {
        "schema": "unclog.v0.1",
        "error": {
            "kind": kind,
            "type": type(exc).__name__,
            "message": str(exc),
        },
    }
    typer.echo(_json.dumps(doc))


def _handle_environment_error(
    console: Console, exc: BaseException, *, as_json: bool
) -> None:
    """Render a user-environment error (permissions, corrupt config, …).

    These are not bugs — the fix is in the user's filesystem or config,
    not in unclog. We say *what* is wrong and *where* to look, and we
    don't point at the issue tracker.
    """
    if as_json:
        _emit_json_error("environment", exc)
        return
    console.print()
    console.print(
        f"[#ef4444]unclog can't continue:[/#ef4444] {exc}"
    )
    console.print(
        "[dim]This looks like a filesystem or config issue, not a bug. "
        "Check the file named above and retry.[/dim]"
    )


def _handle_unexpected_error(
    console: Console, exc: BaseException, *, as_json: bool = False
) -> None:
    """Render an unexpected crash as a clean user-facing error.

    The trace is still written to ``~/.claude/.unclog/last-error.log``
    so the user can share it in a bug report without needing to
    reproduce. Any existing apply snapshot is discoverable via
    ``unclog restore --list``; we point users at that rather than
    guessing a specific id, because the snapshot (if any) was created
    inside a nested call and we don't have its id here.
    """
    import traceback

    error_log: Path | None = None
    try:
        log_path = claude_paths().unclog_dir / "last-error.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
        error_log = log_path
    except OSError:
        # If we can't even write the log, don't compound the problem.
        pass

    if as_json:
        _emit_json_error("unexpected", exc)
        return

    console.print()
    console.print(
        f"[#ef4444]unclog hit an unexpected error:[/#ef4444] "
        f"[bold]{type(exc).__name__}[/bold]: {exc}"
    )
    if error_log is not None:
        console.print(f"[dim]Full trace: {error_log}[/dim]")
    console.print(
        "[dim]If an apply was in progress, run [bold]unclog restore --list[/bold] "
        "to see recoverable snapshots.[/dim]"
    )
    console.print(
        "[dim]Please report this at https://github.com/thomaschill/unclog/issues[/dim]"
    )


def _launch_interactive(
    state: InstallationState,
    *,
    console: Console,
    yes: bool,
    no_animation: bool,
) -> None:
    """Gate the interactive fix flow behind the scan + findings layer.

    Detector warnings are NOT re-printed here — they've already been
    surfaced by the scan report (via ``build_report``'s merged warnings
    field) rendered moments earlier. Printing them again would create a
    duplicate, confusing noise block above the picker.
    """
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
        help="Snapshot id to restore. Use 'latest' (default) or pass a specific id. Use --list to enumerate.",
    ),
    list_only: bool = typer.Option(
        False,
        "--list",
        help="List every snapshot without restoring.",
    ),
) -> None:
    """Restore a previous unclog snapshot.

    With no arguments, 'unclog restore' restores the most recent
    snapshot. 'unclog restore <id>' targets a specific one.
    Pass --list to enumerate every snapshot on disk without restoring.
    """
    paths = claude_paths()
    console = Console()
    try:
        if list_only:
            _render_snapshot_list(paths.snapshots_dir, console)
            return

        try:
            snapshot = load_snapshot(paths.snapshots_dir, snapshot_id)
        except SnapshotError as exc:
            console.print(f"[#ef4444]{exc}[/#ef4444]")
            raise typer.Exit(code=1) from exc

        result = restore_snapshot(snapshot)
        if result.failed:
            # Lead with the failure so the green ✓ doesn't hide the
            # partial state — users skim output and can miss a secondary
            # block underneath a success line.
            console.print(
                f"[#eab308]![/#eab308] Partially restored snapshot [bold]{snapshot.id}[/bold] "
                f"[dim]({len(result.restored)} ok, {len(result.failed)} failed)[/dim]"
            )
            console.print(
                f"[#ef4444]! {len(result.failed)} action(s) could not be restored:[/#ef4444]"
            )
            for action, reason in result.failed:
                console.print(f"  [dim]- {action.original_path}: {reason}[/dim]")
            raise typer.Exit(code=1)
        console.print(
            f"[#22c55e]\u2713[/#22c55e] Restored snapshot [bold]{snapshot.id}[/bold] "
            f"({len(result.restored)} action(s))"
        )
    except (typer.Exit, typer.Abort, typer.BadParameter, SystemExit):
        raise
    except KeyboardInterrupt as exc:
        console.print()
        console.print("[dim]Cancelled.[/dim]")
        raise typer.Exit(code=130) from exc
    except Exception as exc:
        _handle_unexpected_error(console, exc)
        raise typer.Exit(code=1) from exc


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
