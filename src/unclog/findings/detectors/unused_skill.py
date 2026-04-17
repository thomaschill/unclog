"""Surface every installed skill as a removable candidate.

Historical context: earlier drafts gated this detector on file age + install
activity to stay conservative. In practice the gates produced a dead-end
UX — a user with 22 recently-installed skills saw zero findings even
though several were objectively never going to be used.

v0.1 therefore trades conservatism for transparency:

1. Emit one finding per skill, regardless of age or install-activity.
2. Carry a ``token_savings`` estimate so the interactive picker can show
   the cost of each skill (name + description tokens, the bytes Claude
   loads on every session).
3. Pre-check (``auto_checked=True``) only skills with zero ``@slug``
   mentions in history — a weak but honest "I have never referenced
   this by name" signal. Anything with even a single @mention is left
   un-checked so the user has to opt in.

We still cannot prove non-use (per-tool invocation counts require full
session JSONL parsing — deferred to v0.2), so the evidence block is
explicit about that limitation.
"""

from __future__ import annotations

from datetime import datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.scan.tokens import count_tokens
from unclog.state import InstallationState


def _description_tokens(name: str, description: str | None) -> int:
    # Matches the composition model in ui/output.py: name + ": " + description
    # is the line that ends up in Claude's system preamble per skill.
    return count_tokens(f"{name}: {description or ''}")


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,  # noqa: ARG001 — unused now, kept for stable detector signature
    *,
    now: datetime,  # noqa: ARG001
) -> list[Finding]:
    findings: list[Finding] = []
    for skill in state.global_scope.skills:
        mentioned = (
            skill.slug in activity.at_mention_last_used
            or skill.name in activity.at_mention_last_used
        )
        auto_checked = not mentioned
        reason = (
            f"no @{skill.slug} mention in history"
            if not mentioned
            else f"mentioned as @{skill.slug} in history — opt in to remove"
        )
        findings.append(
            Finding(
                id=f"unused_skill:{skill.slug}",
                type="unused_skill",
                title=f"Remove skill {skill.name}",
                reason=reason,
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=skill.directory),
                auto_checked=auto_checked,
                token_savings=_description_tokens(skill.name, skill.description),
                evidence={
                    "path": str(skill.skill_md_path),
                    "mentioned_in_history": mentioned,
                    "note": (
                        "v0.1 can't prove non-use from session JSONL; auto_checked "
                        "reflects only the absence of an @slug mention in history."
                    ),
                },
            )
        )
    return findings
