from __future__ import annotations

import os
from pathlib import Path

import pytest

from unclog.util.paths import ClaudePaths, claude_home, claude_paths


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    claude_home.cache_clear()


def test_claude_home_respects_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert claude_home() == tmp_path.resolve()


def test_claude_home_falls_back_to_dot_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert claude_home() == (Path.home() / ".claude").resolve()


def test_claude_home_expands_tilde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/custom-claude")
    assert claude_home() == (Path.home() / "custom-claude").resolve()


def test_claude_paths_derives_all_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    p = claude_paths()
    assert isinstance(p, ClaudePaths)
    assert p.home == tmp_path.resolve()
    assert p.config_json == tmp_path.resolve() / ".claude.json"
    assert p.skills_dir == tmp_path.resolve() / "skills"
    assert p.agents_dir == tmp_path.resolve() / "agents"
    assert p.commands_dir == tmp_path.resolve() / "commands"
    assert p.projects_dir == tmp_path.resolve() / "projects"
    assert p.unclog_dir == tmp_path.resolve() / ".unclog"


def test_claude_home_ignores_empty_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty string shouldn't be treated as an override.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")
    assert claude_home() == (Path.home() / ".claude").resolve()


def test_config_json_prefers_inside_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / ".claude.json").write_text("{}", encoding="utf-8")
    p = claude_paths()
    assert p.config_json == tmp_path.resolve() / ".claude.json"


def test_config_json_falls_back_to_parent_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Default install puts ~/.claude.json alongside ~/.claude/, not inside.
    parent = tmp_path / "root"
    home = parent / ".claude"
    home.mkdir(parents=True)
    (parent / ".claude.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    p = claude_paths()
    assert p.config_json == parent.resolve() / ".claude.json"


def test_config_json_defaults_to_inside_when_neither_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    p = claude_paths()
    assert p.config_json == tmp_path.resolve() / ".claude.json"


def test_claude_home_is_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    first = claude_home()
    # Changing the env after first resolution must not affect subsequent calls.
    os.environ["CLAUDE_CONFIG_DIR"] = str(tmp_path / "other")
    assert claude_home() == first
