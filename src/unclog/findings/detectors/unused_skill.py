"""Detect skills with no evidence of use in ``thresholds.unused_days``.

Honest v0.1 caveat (spec §6): per-tool invocation counts land in v0.2
once we parse full session JSONL. In v0.1 the only per-entity signals
we can mine are user prompts mentioning ``@<slug>`` in ``history.jsonl``
— a weak heuristic. We therefore:

1. Require that the install itself is active (``last_active_overall``
   is recent) before emitting anything; if Claude Code hasn't been run
   in months, calling every skill "unused" is noise.
2. Only emit findings when the skill's file has existed longer than the
   threshold AND there is no ``@<slug>`` mention in history within the
   threshold window.
3. Never auto-check. Users must opt in — we cannot prove non-use.

The detector reports the skill's location; a concrete token-savings
estimate lands once the scan re-runs after a snapshot is applied.
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
        # No activity record at all — we can't claim anything is unused.
        return []
    install_idle_days = (now - activity.last_active_overall).days
    if install_idle_days >= thresholds.unused_days:
        # Whole install is dormant; flagging individual skills is
        # misleading — the user's situation is broader than a stale file.
        return []

    window_start = now - timedelta(days=thresholds.unused_days)
    findings: list[Finding] = []
    for skill in state.global_scope.skills:
        mention = activity.at_mention_last_used.get(skill.slug)
        if mention is not None and mention >= window_start:
            continue
        mention_name = activity.at_mention_last_used.get(skill.name)
        if mention_name is not None and mention_name >= window_start:
            continue

        age = _file_age_days(skill.skill_md_path, now)
        if age is None or age < thresholds.unused_days:
            # Recently added skills get the benefit of the doubt.
            continue

        findings.append(
            Finding(
                id=f"unused_skill:{skill.slug}",
                type="unused_skill",
                title=f"Remove skill {skill.name}",
                reason=f"no @{skill.slug} mention in {thresholds.unused_days}d",
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=skill.directory),
                auto_checked=False,
                token_savings=None,
                evidence={
                    "path": str(skill.skill_md_path),
                    "file_age_days": age,
                    "threshold_days": thresholds.unused_days,
                    "note": (
                        "v0.1 cannot prove non-use; flagged because no @mention "
                        "appears in history.jsonl within the threshold window"
                    ),
                },
            )
        )
    return findings
