from __future__ import annotations

import json
from pathlib import Path

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


def test_json_mode_emits_valid_json_on_scan_error(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Regression (Fix #2): --json must never emit unstructured Rich text.

    When the scan blew up, the fallback handler used to print the
    ``[red]unclog hit an unexpected error:[/red] ...`` Rich banner
    even under ``--json``. That broke every automated consumer
    (CI pipelines, jq) because stdout was no longer parsable.

    The fix routes every error path through ``_emit_json_error``
    under ``--json``, so stdout always parses as JSON.
    """
    import unclog.cli as cli_module

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("synthetic scan failure")

    monkeypatch.setattr(cli_module, "run_scan", _boom)  # type: ignore[attr-defined]
    result = runner.invoke(app, ["--json"])
    assert result.exit_code == 1
    doc = json.loads(result.stdout)
    assert doc["schema"] == "unclog.v0.1"
    assert doc["error"]["kind"] == "unexpected"
    assert "synthetic scan failure" in doc["error"]["message"]


def test_json_mode_emits_json_error_for_config_parse_failure(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Regression (Fix #2 + #3): ConfigParseError also emits JSON under --json."""
    import unclog.cli as cli_module
    from unclog.scan.config import ConfigParseError

    def _boom(*args: object, **kwargs: object) -> object:
        raise ConfigParseError(Path("/nope/.claude.json"), OSError("permission denied"))

    monkeypatch.setattr(cli_module, "run_scan", _boom)  # type: ignore[attr-defined]
    result = runner.invoke(app, ["--json"])
    assert result.exit_code == 1
    doc = json.loads(result.stdout)
    assert doc["schema"] == "unclog.v0.1"
    assert doc["error"]["kind"] == "environment"
    assert "permission denied" in doc["error"]["message"]
