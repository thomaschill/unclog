from __future__ import annotations

import os
from pathlib import Path

import pytest

from unclog.util.paths import ClaudePaths, claude_home, claude_paths, encode_project_path


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
    assert p.settings_json == tmp_path.resolve() / "settings.json"
    assert p.skills_dir == tmp_path.resolve() / "skills"
    assert p.projects_dir == tmp_path.resolve() / "projects"
    assert p.unclog_dir == tmp_path.resolve() / ".unclog"
    assert p.snapshots_dir == tmp_path.resolve() / ".unclog" / "snapshots"
    assert p.installed_plugins_json == tmp_path.resolve() / "plugins" / "installed_plugins.json"


def test_encode_project_path_replaces_slashes_with_dashes() -> None:
    # Absolute path: each "/" becomes "-", leading "/" becomes leading "-".
    encoded = encode_project_path(Path("/Users/tom/Desktop/unclog/unclog"))
    assert encoded == "-Users-tom-Desktop-unclog-unclog"


def test_encode_project_path_resolves_relative() -> None:
    # Relative paths get resolved against cwd before encoding.
    encoded = encode_project_path(Path("."))
    assert encoded.startswith("-")
    assert encoded == str(Path.cwd()).replace("/", "-")


def test_project_session_dir_uses_encoded_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    p = claude_paths()
    session = p.project_session_dir(Path("/Users/tom/proj"))
    assert session == tmp_path.resolve() / "projects" / "-Users-tom-proj"


def test_claude_home_ignores_empty_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty string shouldn't be treated as an override.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")
    assert claude_home() == (Path.home() / ".claude").resolve()


def test_claude_home_is_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    first = claude_home()
    # Changing the env after first resolution must not affect subsequent calls
    # (resolve once at startup, per spec §12.2).
    os.environ["CLAUDE_CONFIG_DIR"] = str(tmp_path / "other")
    assert claude_home() == first
