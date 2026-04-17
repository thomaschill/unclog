"""Detect slash commands that have not been invoked in ``thresholds.unused_days``.

Slash commands are the one entity type v0.1 can track with confidence:
user invocations appear verbatim in ``history.jsonl`` as ``/<slug>``
prefixes. A command whose slug has no timestamp in the activity index,
or whose most-recent invocation is older than the threshold, is
recommended for deletion.

Auto-checked by default (spec §6) because the file is small and the
action is cleanly reversible via snapshot.
"""

from __future__ import annotations

from datetime import datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
) -> list[Finding]:
    findings: list[Finding] = []
    for command in state.global_scope.commands:
        last_used = activity.slash_command_last_used.get(command.slug)
        age_days = activity.age_days(now, of=last_used)
        if last_used is not None and age_days is not None and age_days < thresholds.unused_days:
            continue
        reason = (
            "never invoked"
            if last_used is None
            else f"0 uses in {age_days}d"
        )
        findings.append(
            Finding(
                id=f"unused_command:{command.slug}",
                type="unused_command",
                title=f"Remove slash command /{command.slug}",
                reason=reason,
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=command.path),
                auto_checked=True,
                token_savings=None,
                evidence={
                    "path": str(command.path),
                    "last_used": last_used.isoformat() if last_used else None,
                    "age_days": age_days,
                    "threshold_days": thresholds.unused_days,
                },
            )
        )
    return findings
