from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from unclog.apply.snapshot import (
    SnapshotError,
    create_snapshot,
    list_snapshots,
    load_snapshot,
    new_snapshot_id,
)

NOW = datetime(2026, 4, 17, 18, 42, tzinfo=UTC)


def test_new_snapshot_id_is_minute_precise() -> None:
    assert new_snapshot_id(NOW) == "2026-04-17-1842"


def test_create_snapshot_produces_files_dir_and_no_manifest_until_persist(tmp_path: Path) -> None:
    snap = create_snapshot(tmp_path / "snapshots", claude_home=tmp_path / ".claude", now=NOW)
    assert snap.root.is_dir()
    assert snap.files_root.is_dir()
    assert not snap.manifest_path.exists()  # persist() hasn't been called
    snap.persist()
    assert snap.manifest_path.is_file()


def test_create_snapshot_disambiguates_same_minute_collision(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    snaps_dir = tmp_path / "snapshots"
    a = create_snapshot(snaps_dir, claude_home=claude_home, now=NOW)
    b = create_snapshot(snaps_dir, claude_home=claude_home, now=NOW)
    assert a.id != b.id
    assert b.id.startswith("2026-04-17-1842")


def test_capture_file_copies_into_home_tree(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    skill_md = claude_home / "skills" / "ghost" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    snap = create_snapshot(tmp_path / "snapshots", claude_home=claude_home, now=NOW)
    record = snap.capture_file(skill_md, "unused_skill:ghost", action="delete_file")
    copied = snap.files_root / record.snapshot_path
    assert copied.is_file()
    assert copied.read_text(encoding="utf-8") == "body\n"
    # Layout: <snapshot>/files/home/skills/ghost/SKILL.md
    assert str(record.snapshot_path).replace("\\", "/") == "home/skills/ghost/SKILL.md"


def test_capture_file_routes_project_paths_under_projects_label(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    project = tmp_path / "draper"
    (project / ".claude").mkdir(parents=True)
    claude_md = project / "CLAUDE.md"
    claude_md.write_text("# hi\n", encoding="utf-8")
    snap = create_snapshot(
        tmp_path / "snapshots",
        claude_home=claude_home,
        project_paths=(project,),
        now=NOW,
    )
    record = snap.capture_file(claude_md, "claude_md_duplicate:draper", action="x")
    assert str(record.snapshot_path).replace("\\", "/") == "projects/draper/CLAUDE.md"


def test_capture_file_copies_entire_directory(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    skill_dir = claude_home / "skills" / "ghost"
    (skill_dir / "nested").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("one\n", encoding="utf-8")
    (skill_dir / "nested" / "data.txt").write_text("two\n", encoding="utf-8")
    snap = create_snapshot(tmp_path / "snapshots", claude_home=claude_home, now=NOW)
    record = snap.capture_file(skill_dir, "unused_skill:ghost", action="delete_file")
    root = snap.files_root / record.snapshot_path
    assert (root / "SKILL.md").is_file()
    assert (root / "nested" / "data.txt").is_file()


def test_persist_and_roundtrip_via_load_snapshot(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    target = claude_home / "CLAUDE.md"
    target.parent.mkdir(parents=True)
    target.write_text("x\n", encoding="utf-8")
    snap = create_snapshot(tmp_path / "snapshots", claude_home=claude_home, now=NOW)
    snap.capture_file(target, "claude_md:g", action="remove_claude_md_section")
    snap.persist()

    loaded = load_snapshot(tmp_path / "snapshots", snap.id)
    assert loaded.id == snap.id
    assert len(loaded.actions) == 1
    assert loaded.actions[0].finding_id == "claude_md:g"


def test_load_snapshot_latest_picks_newest(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    snaps_dir = tmp_path / "snapshots"
    older = create_snapshot(snaps_dir, claude_home=claude_home, now=datetime(2026, 1, 1, tzinfo=UTC))
    older.persist()
    newer = create_snapshot(snaps_dir, claude_home=claude_home, now=datetime(2026, 4, 17, tzinfo=UTC))
    newer.persist()
    loaded = load_snapshot(snaps_dir, "latest")
    assert loaded.id == newer.id


def test_load_snapshot_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        load_snapshot(tmp_path / "snapshots", "nonexistent-id")


def test_list_snapshots_skips_malformed(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    snaps_dir = tmp_path / "snapshots"
    good = create_snapshot(snaps_dir, claude_home=claude_home, now=NOW)
    good.persist()
    # Plant a broken snapshot.
    broken = snaps_dir / "2026-04-17-0000"
    broken.mkdir()
    (broken / "manifest.json").write_text("not json", encoding="utf-8")
    results = list_snapshots(snaps_dir)
    assert [s.id for s in results] == [good.id]


def test_manifest_has_expected_schema_keys(tmp_path: Path) -> None:
    snap = create_snapshot(tmp_path / "snapshots", claude_home=tmp_path / ".claude", now=NOW)
    snap.persist()
    payload = json.loads(snap.manifest_path.read_text(encoding="utf-8"))
    for key in ("id", "created_at", "unclog_version", "claude_home", "actions"):
        assert key in payload
