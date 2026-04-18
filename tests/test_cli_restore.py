from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from unclog.apply.primitives import apply_action
from unclog.apply.snapshot import create_snapshot
from unclog.cli import app
from unclog.findings.base import Action, Finding, Scope
from unclog.util.paths import claude_home as claude_home_cache

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    claude_home_cache.cache_clear()


def _make_skill_snapshot(claude_home: Path) -> Path:
    skill_md = claude_home / "skills" / "ghost" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    snapshots_dir = claude_home / ".unclog" / "snapshots"
    snap = create_snapshot(
        snapshots_dir,
        claude_home=claude_home,
        now=datetime(2026, 4, 17, 18, 42, tzinfo=UTC),
    )
    finding = Finding(
        id="unused_skill:ghost",
        type="unused_skill",
        title="t",
        reason="r",
        scope=Scope(kind="global"),
        action=Action(primitive="delete_file", path=skill_md),
        auto_checked=False,
    )
    apply_action(finding, snap, claude_home=claude_home)
    snap.persist()
    return skill_md


def test_restore_latest_reverses_delete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    skill_md = _make_skill_snapshot(claude_home)
    assert not skill_md.exists()
    result = runner.invoke(app, ["restore", "latest"])
    assert result.exit_code == 0, result.stdout
    assert skill_md.read_text(encoding="utf-8") == "body\n"


def test_restore_list_prints_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    _make_skill_snapshot(claude_home)
    result = runner.invoke(app, ["restore", "--list"])
    assert result.exit_code == 0, result.stdout
    assert "snapshot" in result.stdout.lower()


def test_restore_missing_id_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    result = runner.invoke(app, ["restore", "does-not-exist"])
    assert result.exit_code == 1


