from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from unclog.cli import app
from unclog.scan.config import ClaudeConfig, McpServer, Settings
from unclog.scan.filesystem import Agent, Skill
from unclog.state import GlobalScope, InstallationState
from unclog.ui.output import SCHEMA_ID, build_report, render_default, render_json
from unclog.util.paths import claude_home

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    claude_home.cache_clear()


def _make_state(
    *,
    claude_md_bytes: int = 2000,
    skills: tuple[Skill, ...] = (),
    agents: tuple[Agent, ...] = (),
    config: ClaudeConfig | None = None,
    warnings: tuple[str, ...] = (),
) -> InstallationState:
    home = Path("/fake/.claude")
    return InstallationState(
        generated_at=datetime(2026, 4, 17, 18, 42, tzinfo=UTC),
        claude_home=home,
        global_scope=GlobalScope(
            claude_home=home,
            config=config,
            settings=Settings(),
            claude_md_bytes=claude_md_bytes,
            claude_local_md_bytes=0,
            skills=skills,
            agents=agents,
        ),
        warnings=warnings,
    )


def test_build_report_schema_fields() -> None:
    state = _make_state()
    report = build_report(state)
    assert report["schema"] == SCHEMA_ID
    assert report["generated_at"] == "2026-04-17T18:42:00Z"
    assert report["claude_home"] == "/fake/.claude"
    assert report["baseline"]["tokens_source"] == "bytes_estimate"
    assert report["baseline"]["tier"] in {"lean", "typical", "clogged"}
    assert report["findings"] == []


def test_build_report_estimates_tokens_from_bytes() -> None:
    state = _make_state(claude_md_bytes=4000)
    report = build_report(state)
    assert report["baseline"]["estimated_tokens"] == 1000  # 4000 // 4
    assert report["baseline"]["measured_bytes"] == 4000


def test_build_report_marks_mcp_entries_unmeasured() -> None:
    config = ClaudeConfig(
        mcp_servers={"github": McpServer(name="github"), "notion": McpServer(name="notion")}
    )
    state = _make_state(config=config)
    report = build_report(state)
    mcp_entries = [e for e in report["composition"] if e["source"].startswith("mcp:")]
    assert {e["source"] for e in mcp_entries} == {"mcp:github", "mcp:notion"}
    assert all(e["tokens_source"] == "unmeasured" for e in mcp_entries)
    assert all(e["bytes"] is None for e in mcp_entries)
    assert report["baseline"]["unmeasured_sources"] == 2


def test_render_json_is_valid_json_with_stable_keys() -> None:
    state = _make_state()
    out = render_json(state)
    parsed = json.loads(out)
    for key in [
        "schema",
        "unclog_version",
        "generated_at",
        "claude_home",
        "baseline",
        "inventory",
        "composition",
        "findings",
        "warnings",
        "projects_audited",
    ]:
        assert key in parsed


def test_render_default_includes_baseline_and_inventory() -> None:
    state = _make_state()
    out = render_default(state)
    assert "unclog" in out
    assert "baseline" in out
    assert "inventory" in out


def test_cli_default_outputs_plain_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "unclog" in result.stdout
    assert "baseline" in result.stdout


def test_cli_json_flag_emits_valid_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(app, ["--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["schema"] == SCHEMA_ID
    assert parsed["findings"] == []
