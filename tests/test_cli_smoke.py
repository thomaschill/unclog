from __future__ import annotations

import pytest
from typer.testing import CliRunner

from unclog import __version__
from unclog.cli import app
from unclog.util.paths import claude_home

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    claude_home.cache_clear()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_short_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
