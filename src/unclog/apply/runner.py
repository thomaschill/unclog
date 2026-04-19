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

from unclog.apply.primitives import apply_action
from unclog.apply.snapshot import Snapshot, SnapshotAction, create_snapshot
from unclog.findings.base import Finding


@dataclass
class ApplyResult:
    """Outcome of an apply pass.

    ``succeeded`` carries ``(finding, record)`` pairs in the order they
    ran. ``failed`` carries ``(finding, reason)`` — any primitive
    exception is recorded here instead of bringing down the batch.
    ``persist_error`` is set when the snapshot manifest itself couldn't
    be written, which is a rare but user-visible degradation (restore
    won't find the snapshot).
    """

    snapshot: Snapshot
    succeeded: list[tuple[Finding, SnapshotAction]] = field(default_factory=list)
    failed: list[tuple[Finding, str]] = field(default_factory=list)
    persist_error: str | None = None

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
            except Exception as exc:
                # Any failure — ApplyError for expected cases, OSError /
                # PermissionError / arbitrary bugs for the rest — is
                # recorded against this finding so the remaining items
                # in the batch still get their chance. ``KeyboardInterrupt``
                # is a ``BaseException`` and deliberately not caught here.
                result.failed.append((finding, _format_failure(exc)))
            else:
                result.succeeded.append((finding, record))
    finally:
        # Persist the manifest no matter how the loop exited — a
        # snapshot dir without a manifest is invisible to ``unclog
        # restore``, so a persist failure would hide every successful
        # capture made before the crash.
        try:
            snapshot.persist()
        except Exception as exc:
            result.persist_error = _format_failure(exc)
    return result


def _format_failure(exc: BaseException) -> str:
    """Render an exception for the ``failed``/``persist_error`` fields.

    ``ApplyError`` messages are already user-formatted; everything else
    gets the class name prefix so the user can tell a bug from an
    expected condition.
    """
    from unclog.apply.primitives import ApplyError

    if isinstance(exc, ApplyError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


__all__ = ["ApplyResult", "apply_findings"]
