from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from unclog.apply.primitives import apply_action
from unclog.apply.restore import restore_snapshot
from unclog.apply.runner import apply_findings
from unclog.apply.snapshot import create_snapshot, load_snapshot
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
