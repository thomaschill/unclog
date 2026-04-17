"""Surface every installed agent as a removable candidate.

Mirrors ``unused_skill``: one finding per agent, carries a token-savings
estimate, pre-checked only when there is no ``@slug`` mention in
history. See that module's docstring for the rationale behind dropping
the age/install-activity gates in v0.1.
"""

from __future__ import annotations

from datetime import datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.scan.tokens import count_tokens
from unclog.state import InstallationState


def _description_tokens(name: str, description: str | None) -> int:
    return count_tokens(f"{name}: {description or ''}")


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,  # noqa: ARG001 — kept for stable detector signature
    *,
    now: datetime,  # noqa: ARG001
) -> list[Finding]:
    findings: list[Finding] = []
    for agent in state.global_scope.agents:
        mentioned = (
            agent.slug in activity.at_mention_last_used
            or agent.name in activity.at_mention_last_used
        )
        # Default: unchecked. @mention is a weak negative signal (Task-tool
        # invocations leave no trace), and when most users have 100+ agents
        # installed that were never mentioned, pre-checking everything makes
        # the picker arduous. Safer to opt in than opt out.
        auto_checked = False
        reason = (
            f"no @{agent.slug} mention in history"
            if not mentioned
            else f"mentioned as @{agent.slug} in history"
        )
        findings.append(
            Finding(
                id=f"unused_agent:{agent.slug}",
                type="unused_agent",
                title=f"Remove agent {agent.name}",
                reason=reason,
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=agent.path),
                auto_checked=auto_checked,
                token_savings=_description_tokens(agent.name, agent.description),
                evidence={
                    "path": str(agent.path),
                    "mentioned_in_history": mentioned,
                    "note": (
                        "v0.1 can't see Task-tool agent invocations; auto_checked "
                        "reflects only the absence of an @slug mention in history."
                    ),
                },
            )
        )
    return findings
