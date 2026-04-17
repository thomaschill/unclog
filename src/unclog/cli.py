from __future__ import annotations

import os
import sys

import typer
from rich.console import Console

from unclog import __version__
from unclog.app import run_scan
from unclog.ui.output import render_json, render_plain, render_rich

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


def _should_go_plain(plain_flag: bool) -> bool:
    """Decide whether to route output through the plain renderer.

    Respects ``--plain``, the ``NO_COLOR`` convention, and auto-downgrades
    when stdout isn't a TTY (piped into a file, CI logs, etc.) per
    spec §11.9.
    """
    if plain_flag:
        return True
    if os.environ.get("NO_COLOR"):
        return True
    return not sys.stdout.isatty()


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
) -> None:
    """Scan the current Claude Code installation and print a report.

    The interactive fix flow ships in M5. M2 is read-only: the scan
    produces a real token baseline via tiktoken, attributes MCP cost
    from the latest project session JSONL, and renders the result as
    the hero number + treemap in the default TTY mode.
    """
    if ctx.invoked_subcommand is not None:
        return
    state = run_scan()
    if as_json:
        typer.echo(render_json(state))
        return
    if _should_go_plain(plain):
        typer.echo(render_plain(state), nl=False)
        return
    console = Console()
    render_rich(state, console)
