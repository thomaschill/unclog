from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from unclog.apply.primitives import ApplyError, apply_action
from unclog.apply.snapshot import create_snapshot
from unclog.findings.base import Action, Finding, Scope

NOW = datetime(2026, 4, 17, 18, 42, tzinfo=UTC)


def _snapshot(tmp_path: Path, claude_home: Path, project_paths: tuple[Path, ...] = ()) -> object:
    return create_snapshot(
        tmp_path / "snapshots",
        claude_home=claude_home,
        project_paths=project_paths,
        now=NOW,
    )


def _finding(
    *,
    fid: str,
    type_: str,
    primitive: str,
    path: Path | None = None,
    heading: str | None = None,
    server_name: str | None = None,
    plugin_key: str | None = None,
    line_numbers: tuple[int, ...] = (),
    evidence: dict[str, object] | None = None,
) -> Finding:
    return Finding(
        id=fid,
        type=type_,  # type: ignore[arg-type]
        title="t",
        reason="r",
        scope=Scope(kind="global"),
        action=Action(
            primitive=primitive,  # type: ignore[arg-type]
            path=path,
            heading=heading,
            server_name=server_name,
            plugin_key=plugin_key,
            line_numbers=line_numbers,
        ),
        auto_checked=False,
        evidence=MappingProxyType(evidence or {}),
    )


# -- delete_file ------------------------------------------------------------


def test_delete_file_removes_target_and_captures_bytes(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    skill_md = claude_home / "skills" / "ghost" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    snap = _snapshot(tmp_path, claude_home)

    finding = _finding(
        fid="unused_skill:ghost", type_="unused_skill", primitive="delete_file", path=skill_md
    )
    record = apply_action(finding, snap, claude_home=claude_home)

    assert not skill_md.exists()
    # The parent directory should also be gone (empty after delete).
    assert not skill_md.parent.exists()
    # Snapshot copy is preserved.
    assert (snap.files_root / record.snapshot_path).read_text(encoding="utf-8") == "body\n"


def test_delete_file_raises_without_path(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    snap = _snapshot(tmp_path, claude_home)
    finding = _finding(fid="x", type_="unused_skill", primitive="delete_file")
    with pytest.raises(ApplyError):
        apply_action(finding, snap, claude_home=claude_home)


# -- comment_out_mcp --------------------------------------------------------


def test_comment_out_mcp_renames_server_key(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    config_path = claude_home / ".claude.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"github": {"command": "gh"}, "notion": {"command": "nt"}}}),
        encoding="utf-8",
    )
    snap = _snapshot(tmp_path, claude_home)
    finding = _finding(
        fid="unused_mcp:notion",
        type_="unused_mcp",
        primitive="comment_out_mcp",
        server_name="notion",
    )
    apply_action(finding, snap, claude_home=claude_home)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "notion" not in data["mcpServers"]
    assert "__unclog_disabled__notion" in data["mcpServers"]
    assert data["mcpServers"]["__unclog_disabled__notion"]["command"] == "nt"


# -- disable_plugin / uninstall_plugin -------------------------------------


def test_disable_plugin_flips_setting_to_false(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    settings = claude_home / "settings.json"
    settings.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}), encoding="utf-8")
    snap = _snapshot(tmp_path, claude_home)
    finding = _finding(
        fid="stale_plugin:foo@bar",
        type_="stale_plugin",
        primitive="disable_plugin",
        plugin_key="foo@bar",
    )
    apply_action(finding, snap, claude_home=claude_home)
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["enabledPlugins"]["foo@bar"] is False


def test_uninstall_plugin_removes_record_and_cache(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    installed = claude_home / "plugins" / "installed_plugins.json"
    installed.parent.mkdir(parents=True)
    installed.write_text(
        json.dumps({"plugins": [{"name": "foo", "version": "1"}]}), encoding="utf-8"
    )
    cache_dir = claude_home / "plugins" / "cache" / "foo"
    cache_dir.mkdir(parents=True)
    (cache_dir / "manifest.json").write_text("{}", encoding="utf-8")

    snap = _snapshot(tmp_path, claude_home)
    finding = _finding(
        fid="disabled_plugin_residue:foo@bar",
        type_="disabled_plugin_residue",
        primitive="uninstall_plugin",
        plugin_key="foo@bar",
    )
    apply_action(finding, snap, claude_home=claude_home)
    data = json.loads(installed.read_text(encoding="utf-8"))
    assert data["plugins"] == []
    assert not cache_dir.exists()


# -- remove_claude_md_section / remove_claude_md_lines --------------------


def test_remove_claude_md_section_strips_named_heading(tmp_path: Path) -> None:
    md_path = tmp_path / "CLAUDE.md"
    md_path.write_text("# Keep\nalpha\n# Drop\nbeta\n# Keep2\ngamma\n", encoding="utf-8")
    snap = _snapshot(tmp_path, tmp_path)
    finding = _finding(
        fid="claude_md_duplicate:1",
        type_="claude_md_duplicate",
        primitive="remove_claude_md_section",
        path=md_path,
        heading="Drop",
    )
    apply_action(finding, snap, claude_home=tmp_path)
    text = md_path.read_text(encoding="utf-8")
    assert "Drop" not in text
    assert "Keep" in text and "Keep2" in text


def test_remove_claude_md_lines_drops_specified_lines(tmp_path: Path) -> None:
    md_path = tmp_path / "CLAUDE.md"
    md_path.write_text("one\n/nope/a.py\n/nope/b.py\nfour\n", encoding="utf-8")
    snap = _snapshot(tmp_path, tmp_path)
    finding = _finding(
        fid="claude_md_dead_ref:line-only",
        type_="claude_md_dead_ref",
        primitive="remove_claude_md_lines",
        path=md_path,
        line_numbers=(2, 3),
    )
    apply_action(finding, snap, claude_home=tmp_path)
    assert md_path.read_text(encoding="utf-8") == "one\nfour\n"


# -- move_claude_md_section ------------------------------------------------


def test_move_claude_md_section_cross_scope(tmp_path: Path) -> None:
    source = tmp_path / "global_CLAUDE.md"
    destination = tmp_path / "proj_CLAUDE.md"
    source.write_text("# Keep\nk\n# Draper-only\nbody\n", encoding="utf-8")
    destination.write_text("# existing\nstuff\n", encoding="utf-8")
    snap = _snapshot(tmp_path, tmp_path)
    finding = _finding(
        fid="scope_mismatch:1",
        type_="scope_mismatch_global_to_project",
        primitive="move_claude_md_section",
        path=source,
        heading="Draper-only",
        evidence={"destination_path": str(destination)},
    )
    apply_action(finding, snap, claude_home=tmp_path)
    assert "Draper-only" not in source.read_text(encoding="utf-8")
    dest_text = destination.read_text(encoding="utf-8")
    assert "Draper-only" in dest_text
    assert "existing" in dest_text


def test_move_claude_md_section_creates_destination_when_missing(tmp_path: Path) -> None:
    source = tmp_path / "global_CLAUDE.md"
    destination = tmp_path / "new" / "CLAUDE.md"
    source.write_text("# Move\nx\n", encoding="utf-8")
    snap = _snapshot(tmp_path, tmp_path)
    finding = _finding(
        fid="scope_mismatch:2",
        type_="scope_mismatch_global_to_project",
        primitive="move_claude_md_section",
        path=source,
        heading="Move",
        evidence={"destination_path": str(destination)},
    )
    apply_action(finding, snap, claude_home=tmp_path)
    assert destination.is_file()
    assert "# Move" in destination.read_text(encoding="utf-8")


# -- open_in_editor / flag_only -------------------------------------------


def test_open_in_editor_runs_editor_and_records_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_home = tmp_path / ".claude"
    md_path = tmp_path / "CLAUDE.md"
    md_path.write_text("body\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(args: list[str], check: bool = False) -> object:
        calls.append(args)

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr("unclog.apply.primitives.subprocess.run", fake_run)
    monkeypatch.setenv("EDITOR", "vim")
    snap = _snapshot(tmp_path, claude_home)
    finding = _finding(
        fid="claude_md_oversized:1",
        type_="claude_md_oversized",
        primitive="open_in_editor",
        path=md_path,
        line_numbers=(4,),
    )
    apply_action(finding, snap, claude_home=claude_home)
    assert calls and calls[0][0] == "vim"
    assert str(md_path) in calls[0]
    # Intent recorded even though no bytes captured.
    assert any(a.action == "open_in_editor" for a in snap.actions)


def test_flag_only_records_intent_without_mutating(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    snap = _snapshot(tmp_path, claude_home)
    finding = _finding(
        fid="missing_claudeignore:proj",
        type_="missing_claudeignore",
        primitive="flag_only",
    )
    apply_action(finding, snap, claude_home=claude_home)
    assert any(a.action == "flag_only" for a in snap.actions)
