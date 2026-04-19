"""Snapshot creation, listing, and manifest persistence.

Every destructive apply runs inside a :class:`Snapshot`. Before a
primitive mutates a file, the original bytes (or directory tree) are
copied into the snapshot's ``files/`` tree — laid out as spec §9.1
describes:

::

    ~/.claude/.unclog/snapshots/<id>/
      manifest.json
      files/
        home/.claude/...
        projects/<project-name>/...

``home/`` mirrors everything under ``claude_home``. ``projects/<name>/``
mirrors everything under a project root. The ``name`` segment is the
project's resolved basename — the same label users already see in
``projects_audited`` output. If two scanned projects share a basename,
a disambiguator (``-1``, ``-2``, ...) is appended so snapshot paths
stay unique per run.

The manifest is the single source of truth for :func:`restore_snapshot`;
apply primitives never write to the live filesystem without first
recording their intent here.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from unclog import __version__
from unclog.findings.base import Action

SNAPSHOT_ID_FORMAT = "%Y-%m-%d-%H%M"


class SnapshotError(RuntimeError):
    """Raised when a snapshot cannot be created or persisted safely."""


@dataclass(frozen=True)
class SnapshotAction:
    """One applied action recorded in the manifest.

    ``original_path`` is the absolute path on the live filesystem whose
    bytes were captured. ``snapshot_path`` is relative to the snapshot
    root (``<snapshot>/``), so the manifest stays portable even if the
    snapshot tree is moved.

    ``action`` is the primitive name (matches
    :class:`~unclog.findings.base.ActionPrimitive`). ``details`` carries
    primitive-specific hints needed for replay (e.g. heading names,
    line numbers, prior plugin-enabled value). Primitives that overwrite
    a file — rather than delete it — also get ``post_apply_path``
    populated so ``restore`` can diff before clobbering.
    """

    finding_id: str
    action: str
    original_path: str
    snapshot_path: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "finding_id": self.finding_id,
            "action": self.action,
            "original_path": self.original_path,
            "snapshot_path": self.snapshot_path,
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SnapshotAction:
        return cls(
            finding_id=str(data["finding_id"]),
            action=str(data["action"]),
            original_path=str(data["original_path"]),
            snapshot_path=str(data["snapshot_path"]),
            details=dict(data.get("details") or {}),
        )


@dataclass
class Snapshot:
    """An in-progress or completed snapshot.

    Held as a *mutable* dataclass because actions are appended during
    apply. Once :meth:`persist` is called the manifest is written to
    disk and the object is effectively frozen — callers should not
    mutate ``actions`` after that.
    """

    id: str
    root: Path
    created_at: datetime
    unclog_version: str
    claude_home: Path
    project_paths: tuple[Path, ...]
    actions: list[SnapshotAction] = field(default_factory=list)
    # Tracks which snapshot-relative paths have already had bytes copied
    # this run. Second and subsequent captures of the same path are
    # recorded as actions (so reverse-order restore still replays them)
    # but don't re-copy — preserving the first-capture bytes, which are
    # the true pre-apply state. Without this, a multi-action batch
    # touching the same file (e.g. two disable_plugin on settings.json)
    # leaves the snapshot holding post-first-mutation bytes and a
    # subsequent restore can't reach the true original.
    _captured_rels: set[str] = field(default_factory=set, repr=False)

    @property
    def files_root(self) -> Path:
        return self.root / "files"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def capture_file(
        self, original: Path, finding_id: str, action: str, *, details: dict[str, Any] | None = None
    ) -> SnapshotAction:
        """Copy ``original`` into the snapshot and record the action.

        Returns the :class:`SnapshotAction` record that was appended to
        :attr:`actions`. The live filesystem is not touched beyond the
        read of ``original``.

        Supports both files and directories. Missing originals are
        recorded with an empty snapshot copy — restore simply removes
        the post-apply artefact if the capture didn't produce one.
        """
        rel = _relative_snapshot_path(original, self.claude_home, self.project_paths)
        rel_key = str(rel)
        snap_path = self.files_root / rel
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        # Second+ capture of the same path: skip the copy so the
        # first-capture bytes (true pre-apply state) are preserved. Still
        # append an action record below so reverse-order restore replays
        # this pointer.
        if rel_key in self._captured_rels:
            pass
        elif original.is_symlink():
            # Preserve the pointer itself. ``is_dir()``/``is_file()``
            # dereference symlinks, so without this branch a symlink to
            # a large directory would be copied as a full tree — wrong
            # semantics for restore, since the apply only removes the
            # link.
            if snap_path.is_symlink() or snap_path.exists():
                snap_path.unlink()
            shutil.copy2(original, snap_path, follow_symlinks=False)
            self._captured_rels.add(rel_key)
        elif original.is_dir():
            if snap_path.exists():
                shutil.rmtree(snap_path)
            shutil.copytree(original, snap_path, symlinks=True)
            self._captured_rels.add(rel_key)
        elif original.is_file():
            shutil.copy2(original, snap_path)
            self._captured_rels.add(rel_key)
        else:
            # Missing original: mark as captured so a later sibling
            # capture of the same path (now written by an earlier action)
            # doesn't shadow the "absent" signal restore relies on.
            self._captured_rels.add(rel_key)
        # If the original doesn't exist, we still record the intent so
        # restore knows to delete the post-apply file. ``snap_path`` will
        # simply not exist — restore treats that as "remove target".
        record = SnapshotAction(
            finding_id=finding_id,
            action=action,
            original_path=str(original),
            snapshot_path=str(rel),
            details=dict(details or {}),
        )
        self.actions.append(record)
        return record

    def persist(self) -> Path:
        """Write the manifest to disk and return its path."""
        payload: dict[str, Any] = {
            "id": self.id,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "unclog_version": self.unclog_version,
            "claude_home": str(self.claude_home),
            "project_paths": [str(p) for p in self.project_paths],
            "actions": [a.to_json() for a in self.actions],
        }
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8"
        )
        return self.manifest_path


def new_snapshot_id(now: datetime) -> str:
    """Return a snapshot id from a ``datetime`` (``YYYY-MM-DD-HHMM``)."""
    return now.strftime(SNAPSHOT_ID_FORMAT)


def _unique_snapshot_root(snapshots_dir: Path, base_id: str) -> Path:
    """Return a snapshot root under ``snapshots_dir`` that does not yet exist.

    Two runs in the same minute collide on ``base_id``; we append a
    numeric suffix (``-1``, ``-2``, ...) to disambiguate rather than
    overwriting an existing snapshot.
    """
    candidate = snapshots_dir / base_id
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = snapshots_dir / f"{base_id}-{n}"
        if not candidate.exists():
            return candidate
        n += 1


def _relative_snapshot_path(
    original: Path, claude_home: Path, project_paths: tuple[Path, ...]
) -> Path:
    """Return the snapshot-relative path for ``original``.

    ``files/home/<path-relative-to-claude-home>`` when the file lives
    under ``claude_home``, else ``files/projects/<name>/<rel>`` when it
    lives under a known project. Also accepts ``~/.claude.json`` (the
    "outside layout" config the user's installation may use) because
    primitives legitimately edit it.

    Anything else raises :class:`SnapshotError`. An external path means
    a detector asked to destructively edit a file outside every known
    scope — refusing is safer than silently capturing into a
    collision-prone ``files/external/<basename>`` bucket that would
    quietly overwrite earlier captures under the same basename.

    Only the parent is resolved. A plugin-installed skill that lives
    at ``<claude_home>/skills/gsap`` as a symlink into
    ``~/.agents/skills/gsap`` must still route to ``files/home/skills/gsap``
    — the link's own position — because that's where the delete_file
    primitive operates. Resolving the final path component would follow
    the link out of claude_home and refuse the capture.
    """
    expanded = original.expanduser()
    try:
        original_resolved = expanded.parent.resolve(strict=False) / expanded.name
    except (OSError, RuntimeError):
        original_resolved = expanded
    claude_home_resolved = claude_home.resolve(strict=False)
    try:
        rel = original_resolved.relative_to(claude_home_resolved)
        return Path("home") / rel
    except ValueError:
        pass
    # Accept the outside-layout .claude.json as "home" too.
    outside_claude_json = claude_home_resolved.parent / ".claude.json"
    if original_resolved == outside_claude_json:
        return Path("home") / ".claude.json"
    names_in_use: dict[str, int] = {}
    for project in project_paths:
        project_resolved = project.expanduser().resolve(strict=False)
        base = project_resolved.name or "project"
        count = names_in_use.get(base, 0)
        label = base if count == 0 else f"{base}-{count}"
        names_in_use[base] = count + 1
        try:
            rel = original_resolved.relative_to(project_resolved)
            return Path("projects") / label / rel
        except ValueError:
            continue
    raise SnapshotError(
        f"refusing to capture path outside claude_home and known projects: "
        f"{original_resolved}"
    )


def create_snapshot(
    snapshots_dir: Path,
    *,
    claude_home: Path,
    project_paths: tuple[Path, ...] = (),
    now: datetime,
) -> Snapshot:
    """Create and return a fresh empty :class:`Snapshot` rooted under ``snapshots_dir``.

    The manifest is NOT written yet — call :meth:`Snapshot.persist`
    after capturing actions. ``files/`` is created eagerly so capture
    helpers can assume it exists.
    """
    try:
        snapshots_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SnapshotError(f"Could not create snapshots dir {snapshots_dir}: {exc}") from exc
    base_id = new_snapshot_id(now)
    root = _unique_snapshot_root(snapshots_dir, base_id)
    root.mkdir(parents=True, exist_ok=False)
    (root / "files").mkdir(parents=True, exist_ok=True)
    # Use the final directory name as the snapshot id so the manifest id
    # always matches the on-disk path.
    actual_id = root.name
    return Snapshot(
        id=actual_id,
        root=root,
        created_at=now,
        unclog_version=__version__,
        claude_home=claude_home.resolve(strict=False),
        project_paths=tuple(p.resolve(strict=False) for p in project_paths),
    )


def list_snapshots(snapshots_dir: Path) -> list[Snapshot]:
    """Return every readable snapshot under ``snapshots_dir``, newest first.

    Sort key is the snapshot id (``YYYY-MM-DD-HHMM`` is
    lexicographically orderable) with a fallback to directory mtime for
    manually-renamed entries. Unreadable manifests are skipped silently
    rather than raising — restore should still work for other snapshots.
    """
    if not snapshots_dir.is_dir():
        return []
    entries: list[Snapshot] = []
    for child in snapshots_dir.iterdir():
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            snap = _load_manifest(manifest_path, child)
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            continue
        entries.append(snap)
    entries.sort(key=lambda s: (s.id, s.created_at.timestamp()), reverse=True)
    return entries


def load_snapshot(snapshots_dir: Path, snapshot_id: str) -> Snapshot:
    """Load a specific snapshot by id. Use ``"latest"`` to pick the newest."""
    if snapshot_id == "latest":
        candidates = list_snapshots(snapshots_dir)
        if not candidates:
            raise SnapshotError(f"No snapshots found under {snapshots_dir}")
        return candidates[0]
    manifest_path = snapshots_dir / snapshot_id / "manifest.json"
    if not manifest_path.is_file():
        raise SnapshotError(f"Snapshot not found: {snapshot_id}")
    return _load_manifest(manifest_path, snapshots_dir / snapshot_id)


def _load_manifest(manifest_path: Path, root: Path) -> Snapshot:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Malformed manifest: {manifest_path}")
    created_raw = str(data.get("created_at", ""))
    try:
        created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid created_at in {manifest_path}: {created_raw}") from exc
    actions_raw = data.get("actions") or []
    if not isinstance(actions_raw, list):
        raise ValueError(f"Malformed actions array in {manifest_path}")
    actions = [SnapshotAction.from_json(a) for a in actions_raw if isinstance(a, dict)]
    project_paths_raw = data.get("project_paths") or []
    project_paths = tuple(
        Path(str(p)) for p in project_paths_raw if isinstance(p, str)
    )
    return Snapshot(
        id=str(data.get("id") or root.name),
        root=root,
        created_at=created_at,
        unclog_version=str(data.get("unclog_version") or "unknown"),
        claude_home=Path(str(data.get("claude_home") or "")),
        project_paths=project_paths,
        actions=actions,
    )


def action_snapshot_hint(action: Action) -> dict[str, Any]:
    """Public-adjacent helper: compress an ``Action`` into manifest details.

    Kept next to the snapshot module because it's only used by the
    apply primitives when they record actions. Living here keeps the
    manifest details schema in one place.
    """
    details: dict[str, Any] = {}
    if action.heading is not None:
        details["heading"] = action.heading
    if action.line_numbers:
        details["line_numbers"] = list(action.line_numbers)
    if action.server_name is not None:
        details["server_name"] = action.server_name
    if action.plugin_key is not None:
        details["plugin_key"] = action.plugin_key
    return details


__all__ = [
    "SNAPSHOT_ID_FORMAT",
    "Snapshot",
    "SnapshotAction",
    "SnapshotError",
    "action_snapshot_hint",
    "create_snapshot",
    "list_snapshots",
    "load_snapshot",
    "new_snapshot_id",
]
