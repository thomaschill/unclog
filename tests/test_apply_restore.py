from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from unclog.apply.primitives import apply_action
from unclog.apply.restore import restore_snapshot
from unclog.apply.runner import apply_findings
from unclog.apply.snapshot import (
    SnapshotAction,
    SnapshotError,
    create_snapshot,
    load_snapshot,
)
from unclog.findings.base import Action, Finding, Scope

NOW = datetime(2026, 4, 17, 18, 42, tzinfo=UTC)


def _finding(
    *,
    fid: str,
    primitive: str,
    path: Path | None = None,
    heading: str | None = None,
    server_name: str | None = None,
    plugin_key: str | None = None,
    line_numbers: tuple[int, ...] = (),
    type_: str = "unused_skill",
    evidence: dict[str, object] | None = None,
    token_savings: int | None = None,
) -> Finding:
    return Finding(
        id=fid,
        type=type_,  # type: ignore[arg-type]
        title=f"title {fid}",
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
        token_savings=token_savings,
        evidence=MappingProxyType(evidence or {}),
    )


def test_delete_file_restore_roundtrip(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    skill_md = claude_home / "skills" / "ghost" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    snap = create_snapshot(tmp_path / "snapshots", claude_home=claude_home, now=NOW)
    finding = _finding(fid="unused_skill:ghost", primitive="delete_file", path=skill_md)
    apply_action(finding, snap, claude_home=claude_home)
    snap.persist()
    assert not skill_md.exists()
    result = restore_snapshot(snap)
    assert not result.failed
    assert skill_md.read_text(encoding="utf-8") == "body\n"


def test_delete_symlinked_skill_restore_roundtrip(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    skill_link = claude_home / "skills" / "gsap"
    skill_link.parent.mkdir(parents=True)
    backing = tmp_path / ".agents" / "skills" / "gsap"
    backing.mkdir(parents=True)
    (backing / "SKILL.md").write_text("shared\n", encoding="utf-8")
    skill_link.symlink_to(backing)

    snap = create_snapshot(tmp_path / "snapshots", claude_home=claude_home, now=NOW)
    finding = _finding(fid="unused_skill:gsap", primitive="delete_file", path=skill_link)
    apply_action(finding, snap, claude_home=claude_home)
    snap.persist()
    assert not skill_link.is_symlink()

    result = restore_snapshot(snap)
    assert not result.failed
    assert skill_link.is_symlink()
    assert skill_link.resolve() == backing.resolve()
    # Backing store untouched throughout.
    assert (backing / "SKILL.md").read_text(encoding="utf-8") == "shared\n"


def test_comment_out_mcp_restore_roundtrip(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    config_path = claude_home / ".claude.json"
    original = {"mcpServers": {"notion": {"command": "nt"}, "github": {"command": "gh"}}}
    config_path.write_text(json.dumps(original, indent=2), encoding="utf-8")
    snap = create_snapshot(tmp_path / "snapshots", claude_home=claude_home, now=NOW)
    finding = _finding(
        fid="unused_mcp:notion",
        primitive="comment_out_mcp",
        server_name="notion",
        type_="unused_mcp",
    )
    apply_action(finding, snap, claude_home=claude_home)
    snap.persist()
    assert "__unclog_disabled__notion" in config_path.read_text(encoding="utf-8")
    restore_snapshot(snap)
    restored = json.loads(config_path.read_text(encoding="utf-8"))
    assert set(restored["mcpServers"]) == {"notion", "github"}


def test_remove_claude_md_section_restore_roundtrip(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    md_path = claude_home / "CLAUDE.md"
    original_text = "# Keep\nalpha\n# Drop\nbeta\n"
    md_path.write_text(original_text, encoding="utf-8")
    snap = create_snapshot(tmp_path / "snapshots", claude_home=claude_home, now=NOW)
    finding = _finding(
        fid="dup:1",
        primitive="remove_claude_md_section",
        path=md_path,
        heading="Drop",
        type_="claude_md_duplicate",
    )
    apply_action(finding, snap, claude_home=claude_home)
    snap.persist()
    assert "Drop" not in md_path.read_text(encoding="utf-8")
    restore_snapshot(snap)
    assert md_path.read_text(encoding="utf-8") == original_text


def test_move_section_restore_undoes_both_sides(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    source = claude_home / "CLAUDE.md"
    destination = tmp_path / "proj" / "CLAUDE.md"
    destination.parent.mkdir(parents=True)
    source_text = "# Keep\nk\n# Draper-only\nbody\n"
    dest_text = "# existing\ns\n"
    source.write_text(source_text, encoding="utf-8")
    destination.write_text(dest_text, encoding="utf-8")
    snap = create_snapshot(
        tmp_path / "snapshots",
        claude_home=claude_home,
        project_paths=(destination.parent,),
        now=NOW,
    )
    finding = _finding(
        fid="scope:1",
        primitive="move_claude_md_section",
        path=source,
        heading="Draper-only",
        type_="scope_mismatch_global_to_project",
        evidence={"destination_path": str(destination)},
    )
    apply_action(finding, snap, claude_home=claude_home)
    snap.persist()
    restore_snapshot(snap)
    assert source.read_text(encoding="utf-8") == source_text
    assert destination.read_text(encoding="utf-8") == dest_text


def test_apply_findings_then_load_and_restore(tmp_path: Path) -> None:
    """End-to-end: apply two findings via the runner, persist, reload, restore."""
    claude_home = tmp_path / ".claude"
    md_path = claude_home / "CLAUDE.md"
    md_path.parent.mkdir()
    md_path.write_text("# A\nx\n# Drop\ny\n", encoding="utf-8")
    skill_md = claude_home / "skills" / "ghost" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")

    findings = [
        _finding(
            fid="unused_skill:ghost", primitive="delete_file", path=skill_md, token_savings=100
        ),
        _finding(
            fid="dup:1",
            primitive="remove_claude_md_section",
            path=md_path,
            heading="Drop",
            type_="claude_md_duplicate",
            token_savings=50,
        ),
    ]
    snapshots_dir = claude_home / ".unclog" / "snapshots"
    result = apply_findings(
        findings,
        claude_home=claude_home,
        snapshots_dir=snapshots_dir,
        now=NOW,
    )
    assert len(result.succeeded) == 2
    assert result.token_savings == 150
    assert result.snapshot.manifest_path.is_file()
    assert not skill_md.exists()
    assert "Drop" not in md_path.read_text(encoding="utf-8")

    reloaded = load_snapshot(snapshots_dir, "latest")
    restore_result = restore_snapshot(reloaded)
    assert not restore_result.failed
    assert skill_md.read_text(encoding="utf-8") == "body\n"
    assert "Drop" in md_path.read_text(encoding="utf-8")


def test_apply_findings_survives_one_bad_primitive(tmp_path: Path) -> None:
    """Regression: a raw OSError in one primitive used to crash the whole batch.

    The user had 166 selected items; a single symlinked skill hitting
    ``shutil.rmtree`` aborted the run and left the snapshot manifest
    unwritten (so even partial work wasn't restorable).
    """
    claude_home = tmp_path / ".claude"
    good_skill = claude_home / "skills" / "good" / "SKILL.md"
    good_skill.parent.mkdir(parents=True)
    good_skill.write_text("keep-bytes\n", encoding="utf-8")
    ghost = claude_home / "skills" / "ghost"
    # Path doesn't exist and isn't a symlink — delete_file will raise
    # ApplyError, but the batch should carry on to ``good``.
    findings = [
        _finding(fid="unused_skill:ghost", primitive="delete_file", path=ghost),
        _finding(fid="unused_skill:good", primitive="delete_file", path=good_skill),
    ]
    snapshots_dir = claude_home / ".unclog" / "snapshots"
    result = apply_findings(
        findings, claude_home=claude_home, snapshots_dir=snapshots_dir, now=NOW
    )
    assert len(result.failed) == 1
    assert len(result.succeeded) == 1
    assert not good_skill.exists()
    # Manifest must be on disk even though one item failed.
    assert result.snapshot.manifest_path.is_file()
    # ``unclog restore`` must be able to find the snapshot.
    reloaded = load_snapshot(snapshots_dir, "latest")
    assert reloaded.id == result.snapshot.id


def test_apply_findings_survives_malformed_json_config(tmp_path: Path) -> None:
    """Regression: json.JSONDecodeError escaped the runner and killed the batch."""
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    # Malformed .claude.json will make comment_out_mcp fail.
    (claude_home / ".claude.json").write_text("{broken", encoding="utf-8")
    # Good item that should still succeed after the bad one fails.
    good_md = claude_home / "CLAUDE.md"
    good_md.write_text("# Drop\nbody\n", encoding="utf-8")
    findings = [
        _finding(
            fid="unused_mcp:x",
            primitive="comment_out_mcp",
            server_name="notion",
            type_="unused_mcp",
        ),
        _finding(
            fid="claude_md_oversized:1",
            primitive="remove_claude_md_section",
            path=good_md,
            heading="Drop",
            type_="claude_md_oversized",
        ),
    ]
    snapshots_dir = claude_home / ".unclog" / "snapshots"
    result = apply_findings(
        findings, claude_home=claude_home, snapshots_dir=snapshots_dir, now=NOW
    )
    assert len(result.failed) == 1
    assert len(result.succeeded) == 1
    assert result.persist_error is None
    assert result.snapshot.manifest_path.is_file()
    assert "Drop" not in good_md.read_text(encoding="utf-8")


def test_capture_refuses_path_outside_claude_home_and_projects(tmp_path: Path) -> None:
    """Regression: ``external/<basename>`` used to silently collide on
    two captures with the same basename, overwriting the earlier one in
    the snapshot store. Refusing external paths outright is safer.
    """
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    stray = tmp_path / "not-a-project" / "CLAUDE.md"
    stray.parent.mkdir()
    stray.write_text("body\n", encoding="utf-8")
    snap = create_snapshot(tmp_path / "snapshots", claude_home=claude_home, now=NOW)
    with pytest.raises(SnapshotError, match="outside claude_home"):
        snap.capture_file(stray, "x", action="delete_file")


def test_restore_refuses_tampered_original_path(tmp_path: Path) -> None:
    """Regression: a malicious/corrupt manifest could redirect
    ``shutil.copy2`` to anywhere the user has write access
    (~/.ssh/authorized_keys, ~/.zshrc). Restore must refuse paths outside
    the capture roots before touching the filesystem.
    """
    import json as _json

    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    victim = tmp_path / "outside" / "zshrc"
    victim.parent.mkdir()
    victim.write_text("safe\n", encoding="utf-8")
    snap = create_snapshot(tmp_path / "snapshots", claude_home=claude_home, now=NOW)
    # Legitimately capture a file inside claude_home so the snapshot has
    # bytes on disk to potentially copy.
    inside = claude_home / "CLAUDE.md"
    inside.write_text("evil payload\n", encoding="utf-8")
    snap.capture_file(inside, "x", action="remove_claude_md_section")
    snap.persist()
    # Tamper: rewrite the manifest so its single action points at the
    # victim path with the snapshot's real captured bytes as the source.
    manifest = _json.loads(snap.manifest_path.read_text(encoding="utf-8"))
    manifest["actions"][0]["original_path"] = str(victim)
    snap.manifest_path.write_text(_json.dumps(manifest), encoding="utf-8")
    reloaded = load_snapshot(tmp_path / "snapshots", snap.id)
    result = restore_snapshot(reloaded)
    assert not result.restored
    assert len(result.failed) == 1
    assert "outside capture roots" in result.failed[0][1]
    # Victim untouched.
    assert victim.read_text(encoding="utf-8") == "safe\n"


def test_restore_refuses_dotdot_snapshot_path(tmp_path: Path) -> None:
    """Regression: a ``snapshot_path`` with ``..`` escapes ``files/``."""
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    inside = claude_home / "CLAUDE.md"
    inside.write_text("ok\n", encoding="utf-8")
    snap = create_snapshot(tmp_path / "snapshots", claude_home=claude_home, now=NOW)
    # Hand-build a malicious action — no primitive produces this.
    snap.actions.append(
        SnapshotAction(
            finding_id="evil",
            action="remove_claude_md_section",
            original_path=str(inside),
            snapshot_path="../../../../etc/passwd",
            details={},
        )
    )
    result = restore_snapshot(snap)
    assert not result.restored
    assert len(result.failed) == 1
    assert "outside files/" in result.failed[0][1]


def test_restore_two_plugins_returns_to_true_original(tmp_path: Path) -> None:
    """Fix B round-trip: disabling two plugins and restoring must undo both.

    Before the capture_file dedupe, the second action's capture
    overwrote the first with post-first-mutation bytes. Reverse-order
    restore then reached "after plugin A disabled" as the best state,
    leaving plugin A permanently disabled even after a user ran
    ``unclog restore``.
    """
    claude_home = tmp_path / ".claude"
    claude_home.mkdir()
    settings = claude_home / "settings.json"
    original = {"enabledPlugins": {"a@m": True, "b@m": True}}
    settings.write_text(json.dumps(original), encoding="utf-8")

    snapshots_dir = tmp_path / "snapshots"
    findings = [
        _finding(fid="stale_plugin:a", type_="stale_plugin",
                 primitive="disable_plugin", plugin_key="a@m"),
        _finding(fid="stale_plugin:b", type_="stale_plugin",
                 primitive="disable_plugin", plugin_key="b@m"),
    ]
    result = apply_findings(
        findings,
        claude_home=claude_home,
        snapshots_dir=snapshots_dir,
        now=NOW,
    )
    assert len(result.succeeded) == 2
    # Both disabled post-apply.
    live = json.loads(settings.read_text(encoding="utf-8"))
    assert live["enabledPlugins"] == {"a@m": False, "b@m": False}

    # Now restore — must return to TRUE original (both enabled).
    snap = load_snapshot(snapshots_dir, result.snapshot.id)
    restore = restore_snapshot(snap)
    assert not restore.failed
    final = json.loads(settings.read_text(encoding="utf-8"))
    assert final == original
