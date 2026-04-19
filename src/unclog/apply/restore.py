"""Restore a snapshot by copying captured bytes back to the live tree.

Every action recorded in a manifest is reversed by:

- copying ``<snapshot>/files/<snapshot_path>`` back to ``original_path``
  (file or directory) when the captured bytes exist, or
- removing ``original_path`` if the captured bytes are absent — meaning
  the apply primitive produced a file that didn't exist at capture
  time (rare; happens for ``move_claude_md_section`` destinations).

``open_in_editor`` and ``flag_only`` actions have empty ``snapshot_path``
entries and are skipped — there are no bytes to restore. The user still
sees them in the summary so they know what was logged.

Partial restores are tolerated. If one action fails, the remaining
actions still run; the result object lists every failure so the CLI
can report them instead of aborting silently.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from unclog.apply.snapshot import Snapshot, SnapshotAction


@dataclass
class RestoreResult:
    """Outcome of a restore run.

    ``restored`` lists actions whose bytes were successfully returned
    to the live filesystem. ``failed`` pairs each failed action with
    the reason so the CLI can surface them.
    """

    snapshot: Snapshot
    restored: list[SnapshotAction] = field(default_factory=list)
    failed: list[tuple[SnapshotAction, str]] = field(default_factory=list)


def restore_snapshot(snapshot: Snapshot) -> RestoreResult:
    """Replay ``snapshot.actions`` in reverse order to undo an apply."""
    result = RestoreResult(snapshot=snapshot)
    # Reverse order so, for move actions, the destination is cleared
    # before the source is re-created.
    for action in reversed(snapshot.actions):
        try:
            _restore_one(snapshot, action)
        except (OSError, RuntimeError) as exc:
            result.failed.append((action, str(exc)))
        else:
            result.restored.append(action)
    return result


def _restore_one(snapshot: Snapshot, action: SnapshotAction) -> None:
    if not action.snapshot_path:
        # Informational actions (open_in_editor, flag_only) have no bytes.
        return
    source = snapshot.files_root / action.snapshot_path
    destination = Path(action.original_path)
    if source.is_symlink():
        # Captured symlinks are recreated as symlinks; don't walk into
        # their dereferenced targets (the backing tree was never part
        # of the apply and may be huge or shared).
        _clear_destination(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
    elif source.is_dir():
        _clear_destination(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=True)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    else:
        # Snapshot didn't capture bytes — original was absent at apply
        # time. Restoring means removing whatever the apply produced.
        _clear_destination(destination)


def _clear_destination(destination: Path) -> None:
    if destination.is_symlink():
        destination.unlink()
    elif destination.is_dir():
        shutil.rmtree(destination)
    elif destination.exists():
        destination.unlink()


__all__ = ["RestoreResult", "restore_snapshot"]
