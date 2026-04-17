from __future__ import annotations

from typer.testing import CliRunner

from unclog import __version__
from unclog.cli import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_bare_invocation_exits_cleanly() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
