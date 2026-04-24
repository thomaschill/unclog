"""Single-command CLI — scan, render, open the picker, apply."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from unclog import __version__
from unclog.app import run_scan
from unclog.findings.curate import build_curate_findings
from unclog.scan.config import ConfigParseError
from unclog.ui.interactive import run_interactive
from unclog.ui.output import baseline_tokens, render_header
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
) -> None:
    """Scan, render the baseline, then open the picker to curate items."""
    if ctx.invoked_subcommand is not None:
        return

    console = Console()
    try:
        state = run_scan()
        findings = build_curate_findings(state)
        render_header(state, findings, console)
        if not findings:
            console.print(
                "[dim]Nothing to curate — no agents, skills, or MCP servers found.[/dim]"
            )
            return
        run_interactive(
            findings,
            claude_home=state.claude_home,
            console=console,
            baseline_tokens=baseline_tokens(findings),
        )
    except (typer.Exit, typer.Abort, typer.BadParameter, SystemExit):
        raise
    except KeyboardInterrupt as exc:
        console.print()
        console.print("[dim]Cancelled.[/dim]")
        raise typer.Exit(code=130) from exc
    except ConfigParseError as exc:
        console.print()
        console.print(f"[#ef4444]unclog can't continue:[/#ef4444] {exc}")
        console.print(
            "[dim]This looks like a filesystem or config issue, not a bug. "
            "Check the file named above and retry.[/dim]"
        )
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        _handle_unexpected_error(console, exc)
        raise typer.Exit(code=1) from exc


def _handle_unexpected_error(console: Console, exc: BaseException) -> None:
    """Render an unexpected crash cleanly; persist the trace for bug reports."""
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
        pass

    console.print()
    console.print(
        f"[#ef4444]unclog hit an unexpected error:[/#ef4444] "
        f"[bold]{type(exc).__name__}[/bold]: {exc}"
    )
    if error_log is not None:
        console.print(f"[dim]Full trace: {error_log}[/dim]")
    console.print(
        "[dim]Please report this at https://github.com/thomaschill/unclog/issues[/dim]"
    )
