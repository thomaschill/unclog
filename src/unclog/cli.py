from __future__ import annotations

import typer

from unclog import __version__
from unclog.app import run_scan
from unclog.ui.output import render_default, render_json

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
) -> None:
    """Scan the current Claude Code installation and print a report.

    The interactive fix flow ships in M5. M1 is read-only: the scan
    produces a byte-count baseline and inventory summary, with real
    token measurements arriving in M2.
    """
    if ctx.invoked_subcommand is not None:
        return
    state = run_scan()
    output = render_json(state) if as_json else render_default(state)
    typer.echo(output, nl=False)
