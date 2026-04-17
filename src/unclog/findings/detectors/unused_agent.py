"""Detect agents with no evidence of use in ``thresholds.unused_days``.

Same v0.1 caveat as ``unused_skill``: per-entity invocation counts need
full session JSONL parsing, which ships in v0.2. The weak-signal we
have is a literal ``@<slug>`` mention in ``history.jsonl``. Agents are
rarely invoked by name in prompts (Claude Code usually spawns them via
the Task tool), so this detector is deliberately conservative:

1. Only emit while the install is overall active.
2. Require that the agent file has existed longer than the threshold.
3. Never auto-check — user must opt in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState


def _file_age_days(path: Path, now: datetime) -> int | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    seen = datetime.fromtimestamp(mtime, tz=UTC)
    return max((now - seen).days, 0)


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
) -> list[Finding]:
    if activity.last_active_overall is None:
        return []
    if (now - activity.last_active_overall).days >= thresholds.unused_days:
        return []

    window_start = now - timedelta(days=thresholds.unused_days)
    findings: list[Finding] = []
    for agent in state.global_scope.agents:
        mention = activity.at_mention_last_used.get(agent.slug)
        if mention is not None and mention >= window_start:
            continue
        mention_name = activity.at_mention_last_used.get(agent.name)
        if mention_name is not None and mention_name >= window_start:
            continue

        age = _file_age_days(agent.path, now)
        if age is None or age < thresholds.unused_days:
            continue

        findings.append(
            Finding(
                id=f"unused_agent:{agent.slug}",
                type="unused_agent",
                title=f"Remove agent {agent.name}",
                reason=f"no @{agent.slug} mention in {thresholds.unused_days}d",
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=agent.path),
                auto_checked=False,
                token_savings=None,
                evidence={
                    "path": str(agent.path),
                    "file_age_days": age,
                    "threshold_days": thresholds.unused_days,
                    "note": (
                        "v0.1 cannot see Task-tool agent invocations; "
                        "flagged because no @mention appears in history.jsonl"
                    ),
                },
            )
        )
    return findings
