from __future__ import annotations

import json
from pathlib import Path

import pytest

from unclog.app import run_scan
from unclog.util.paths import claude_home


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    claude_home.cache_clear()


def _build_minimal_home(tmp_path: Path) -> Path:
    home = tmp_path / ".claude"
    home.mkdir()
    (home / "CLAUDE.md").write_text("# Global rules\nUse yarn.\n", encoding="utf-8")
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "npx", "args": ["-y", "x"]},
                    "notion": {"command": "notion-mcp"},
                },
                "projects": {
                    "/Users/tom/draper": {"lastSessionId": "abc"},
                },
                "numStartups": 17,
            }
        ),
        encoding="utf-8",
    )
    (home / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {"superpower@antonin": True},
                "permissions": {"allow": ["Bash(git status)"]},
            }
        ),
        encoding="utf-8",
    )

    skills = home / "skills" / "code-reviewer"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: code-reviewer\ndescription: Reviews PRs.\n---\nbody\n",
        encoding="utf-8",
    )

    agents = home / "agents"
    agents.mkdir()
    (agents / "planner.md").write_text(
        "---\nname: planner\ndescription: plans work\n---\n", encoding="utf-8"
    )

    commands = home / "commands"
    commands.mkdir()
    (commands / "ship.md").write_text("ship!", encoding="utf-8")

    plugins_dir = home / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "superpower",
                        "marketplace": "antonin",
                        "version": "1.2.3",
                        "installedAt": "2026-01-15T10:00:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return home


def test_run_scan_builds_state_from_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _build_minimal_home(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))

    state = run_scan()
    assert state.claude_home == home.resolve()
    gs = state.global_scope
    assert gs.claude_md_bytes > 0
    assert gs.claude_local_md_bytes == 0
    assert len(gs.skills) == 1
    assert len(gs.agents) == 1
    assert len(gs.commands) == 1
    assert len(gs.installed_plugins) == 1
    assert gs.config is not None
    assert set(gs.config.mcp_servers) == {"github", "notion"}
    assert gs.settings is not None
    assert gs.settings.enabled_plugins == {"superpower@antonin": True}
    # Fixture registers /Users/tom/draper — a stale entry. Default scan
    # flags stale projects via one summary warning.
    assert all("no longer exist" in w for w in state.warnings)


def test_run_scan_warns_when_home_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    nowhere = tmp_path / "does-not-exist"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(nowhere))
    state = run_scan()
    assert any("does not exist" in w for w in state.warnings)
    assert state.global_scope.claude_md_bytes == 0


def test_run_scan_degrades_on_malformed_claude_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".claude"
    home.mkdir()
    (home / ".claude.json").write_text("{ not valid", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))

    state = run_scan()
    # Config parse error is captured as a warning, not raised — the rest
    # of the scan still runs.
    assert state.global_scope.config is None
    assert any("Could not parse" in w for w in state.warnings)


def test_run_scan_picks_up_latest_session_across_projects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _build_minimal_home(tmp_path)
    # Add a project session directory with a single session JSONL containing
    # a system record and a tools list.
    session_dir = home / "projects" / "-Users-tom-draper"
    session_dir.mkdir(parents=True)
    (session_dir / "abc.jsonl").write_text(
        json.dumps({"type": "system", "content": "You are Claude Code. Use CLAUDE.md."})
        + "\n"
        + json.dumps(
            {
                "type": "user",
                "tools": [
                    {
                        "name": "mcp__github__list_repos",
                        "description": "list repos",
                        "input_schema": {"type": "object"},
                    },
                    {
                        "name": "Read",
                        "description": "builtin",
                        "input_schema": {"type": "object"},
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))

    state = run_scan()
    assert state.global_scope.latest_session is not None
    assert state.global_scope.latest_session.total_tokens > 0
    assert len(state.global_scope.latest_session.tools) == 2
