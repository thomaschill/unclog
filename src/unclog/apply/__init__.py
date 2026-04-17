"""Apply phase — snapshots, primitives, and restore.

Public surface:

- :class:`~unclog.apply.snapshot.Snapshot` and :class:`SnapshotAction`
- :func:`~unclog.apply.snapshot.create_snapshot`,
  :func:`~unclog.apply.snapshot.list_snapshots`,
  :func:`~unclog.apply.snapshot.load_snapshot`
- :func:`~unclog.apply.primitives.apply_action` — dispatches on
  ``Action.primitive`` and returns the :class:`SnapshotAction` record to
  append to the manifest.
- :func:`~unclog.apply.runner.apply_findings` — end-to-end:
  create snapshot, run primitives, persist manifest.
- :func:`~unclog.apply.restore.restore_snapshot` — copy files back.
"""

from __future__ import annotations

from unclog.apply.primitives import ApplyError, apply_action
from unclog.apply.restore import RestoreResult, restore_snapshot
from unclog.apply.runner import ApplyResult, apply_findings
from unclog.apply.snapshot import (
    Snapshot,
    SnapshotAction,
    create_snapshot,
    list_snapshots,
    load_snapshot,
    new_snapshot_id,
)

__all__ = [
    "ApplyError",
    "ApplyResult",
    "RestoreResult",
    "Snapshot",
    "SnapshotAction",
    "apply_action",
    "apply_findings",
    "create_snapshot",
    "list_snapshots",
    "load_snapshot",
    "new_snapshot_id",
    "restore_snapshot",
]
