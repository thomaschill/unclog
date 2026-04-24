from __future__ import annotations

import json
from pathlib import Path

import pytest

from unclog.scan.config import ClaudeConfig, ConfigParseError, load_claude_config


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_claude_config_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_claude_config(tmp_path / "nope.json") is None


def test_load_claude_config_parses_mcp_servers_and_projects(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".claude.json"
    _write_json(
        cfg_path,
        {
            "mcpServers": {
                "github": {"command": "npx"},
                "notion": {"command": "notion-mcp"},
            },
            "projects": {
                "/Users/tom/proj-a": {"mcpServers": {"project-only": {"command": "x"}}},
                "/Users/tom/proj-b": "not-an-object",  # malformed — skipped
            },
            "oauthAccount": {"email": "ignored"},
        },
    )

    cfg = load_claude_config(cfg_path)
    assert isinstance(cfg, ClaudeConfig)
    assert cfg.mcp_servers == frozenset({"github", "notion"})

    proj_a = cfg.projects[Path("/Users/tom/proj-a")]
    assert proj_a.mcp_servers == frozenset({"project-only"})
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
    assert cfg.mcp_servers == frozenset()
    assert cfg.projects == {}


def test_load_claude_config_raises_config_parse_error_on_permission_denied(
    tmp_path: Path,
) -> None:
    """Regression (Fix #3): PermissionError must surface as ConfigParseError.

    Before the fix, an unreadable ``~/.claude.json`` escaped as a raw
    ``OSError`` and tripped the CLI's unexpected-error path ("please
    file a bug report"). It's a filesystem condition, not a bug —
    should be a typed error the CLI renders with a clean message.
    """
    import os
    import sys

    if sys.platform == "win32":
        pytest.skip("chmod semantics differ on Windows")
    cfg_path = tmp_path / ".claude.json"
    cfg_path.write_text("{}", encoding="utf-8")
    cfg_path.chmod(0o000)
    try:
        if os.geteuid() == 0:
            pytest.skip("root bypasses file mode permissions")
        with pytest.raises(ConfigParseError) as excinfo:
            load_claude_config(cfg_path)
        assert excinfo.value.path == cfg_path
    finally:
        # Restore so the tmp_path can be cleaned up.
        cfg_path.chmod(0o600)
