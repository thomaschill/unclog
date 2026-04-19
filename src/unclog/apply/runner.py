"""Top-level apply orchestration.

Creates a snapshot, runs every selected finding's primitive, and
persists the manifest. Callers (CLI, tests) get a structured
:class:`ApplyResult` that can drive a summary rendering without
re-deriving what ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from unclog.apply.primitives import ApplyError, apply_action
from unclog.apply.snapshot import Snapshot, SnapshotAction, create_snapshot
from unclog.findings.base import Finding


@dataclass
class ApplyResult:
    """Outcome of an apply pass.

    ``succeeded`` carries ``(finding, record)`` pairs in the order they
    ran. ``failed`` carries ``(finding, reason)`` — primitives that
    raised :class:`ApplyError` don't bring down the batch; they're
    logged and skipped.
    """

    snapshot: Snapshot
    succeeded: list[tuple[Finding, SnapshotAction]] = field(default_factory=list)
    failed: list[tuple[Finding, str]] = field(default_factory=list)

    @property
    def token_savings(self) -> int:
        return sum(
            (finding.token_savings or 0)
            for finding, _ in self.succeeded
            if finding.token_savings is not None
        )


def apply_findings(
    findings: list[Finding],
    *,
    claude_home: Path,
    snapshots_dir: Path,
    project_paths: tuple[Path, ...] = (),
    now: datetime | None = None,
) -> ApplyResult:
    """Create a snapshot and apply every finding in ``findings``.

    The snapshot manifest is persisted even if every primitive fails,
    so a partial run is still restorable. ``project_paths`` is used to
    route captured files into ``files/projects/<name>/...`` segments.
    """
    reference = now if now is not None else datetime.now(tz=UTC)
    snapshot = create_snapshot(
        snapshots_dir,
        claude_home=claude_home,
        project_paths=project_paths,
        now=reference,
    )
    result = ApplyResult(snapshot=snapshot)
    try:
        for finding in findings:
            try:
                record = apply_action(finding, snapshot, claude_home=claude_home)
            except ApplyError as exc:
                result.failed.append((finding, str(exc)))
            except OSError as exc:
                # A primitive that leaks a raw OSError is a bug, but
                # shouldn't nuke the entire batch — record it and keep
                # going so the user's other 165 items still apply.
                result.failed.append((finding, f"{type(exc).__name__}: {exc}"))
            else:
                result.succeeded.append((finding, record))
    finally:
        # Persist even on unexpected exit so captured bytes stay
        # restorable — a snapshot dir without a manifest is invisible
        # to ``unclog restore``.
        snapshot.persist()
    return result


__all__ = ["ApplyResult", "apply_findings"]
