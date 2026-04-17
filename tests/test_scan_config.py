from __future__ import annotations

import json
from pathlib import Path

import pytest

from unclog.scan.config import (
    ClaudeConfig,
    ConfigParseError,
    Settings,
    load_claude_config,
    load_settings,
)


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_claude_config_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_claude_config(tmp_path / "nope.json") is None


def test_load_claude_config_parses_mcp_servers_and_projects(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".claude.json"
    _write_json(
        cfg_path,
        {
            "numStartups": 42,
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "redacted"},
                },
                "broken": 123,  # malformed entry — should be skipped, not crash
            },
            "projects": {
                "/Users/tom/proj-a": {
                    "lastSessionId": "abc-123",
                    "lastCost": 0.42,
                    "lastAPIDuration": 1800,
                    "mcpServers": {
                        "notion": {"command": "notion-mcp"},
                    },
                },
                "/Users/tom/proj-b": "not-an-object",  # malformed — skipped
            },
            "oauthAccount": {"email": "ignored"},
        },
    )

    cfg = load_claude_config(cfg_path)
    assert isinstance(cfg, ClaudeConfig)
    assert cfg.num_startups == 42
    assert "github" in cfg.mcp_servers
    assert "broken" not in cfg.mcp_servers

    github = cfg.mcp_servers["github"]
    assert github.command == "npx"
    assert github.args == ("-y", "@modelcontextprotocol/server-github")
    assert github.env == {"GITHUB_TOKEN": "redacted"}

    proj_a = cfg.projects[Path("/Users/tom/proj-a")]
    assert proj_a.last_session_id == "abc-123"
    assert proj_a.last_cost == pytest.approx(0.42)
    assert proj_a.last_api_duration_ms == 1800
    assert "notion" in proj_a.mcp_servers

    assert Path("/Users/tom/proj-b") not in cfg.projects


def test_load_claude_config_raises_on_malformed_json(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".claude.json"
    cfg_path.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ConfigParseError) as excinfo:
        load_claude_config(cfg_path)
    assert excinfo.value.path == cfg_path


def test_load_claude_config_handles_non_dict_root(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".claude.json"
    _write_json(cfg_path, ["unexpected", "array"])
    cfg = load_claude_config(cfg_path)
    assert isinstance(cfg, ClaudeConfig)
    assert cfg.mcp_servers == {}
    assert cfg.projects == {}


def test_load_settings_parses_plugins_and_permissions(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    _write_json(
        settings_path,
        {
            "model": "claude-opus-4-7",
            "enabledPlugins": {
                "superpower@antonin": True,
                "broken@x": "not-a-bool",
                "disabled@y": False,
            },
            "permissions": {
                "allow": ["Bash(git status)", "Read(*)"],
                "deny": ["Bash(rm *)"],
            },
            "unknownField": "ignored",
        },
    )

    s = load_settings(settings_path)
    assert isinstance(s, Settings)
    assert s.model == "claude-opus-4-7"
    assert s.enabled_plugins == {"superpower@antonin": True, "disabled@y": False}
    assert s.permissions_allow == ("Bash(git status)", "Read(*)")
    assert s.permissions_deny == ("Bash(rm *)",)
    assert s.raw["unknownField"] == "ignored"


def test_load_settings_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "nope.json") is None


def test_load_settings_handles_empty_file(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    _write_json(settings_path, {})
    s = load_settings(settings_path)
    assert isinstance(s, Settings)
    assert s.enabled_plugins == {}
    assert s.permissions_allow == ()
    assert s.permissions_deny == ()
    assert s.model is None


def test_load_settings_tolerates_missing_permissions_key(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    _write_json(settings_path, {"enabledPlugins": {"p@m": True}})
    s = load_settings(settings_path)
    assert s is not None
    assert s.permissions_allow == ()


def test_load_settings_raises_on_malformed_json(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("}}garbage", encoding="utf-8")
    with pytest.raises(ConfigParseError):
        load_settings(settings_path)
