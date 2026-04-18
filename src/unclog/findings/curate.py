"""Enumerate every local agent and skill as an individually-actionable Finding.

The detectors in :mod:`unclog.findings.detectors` only flag items unclog
can *prove* are suspect (dead MCPs, residue, scope mismatches). Agents
and skills don't get detector flags at all — we can't reliably infer
"used" from session JSONL (Claude dispatches agents via the ``Task``
tool with ``subagent_type``, which we don't fingerprint), so any
detector would be guessing.

Instead, this module builds a parallel *curate* list: every local agent
and every local skill, with real per-item token costs and a
:class:`~unclog.findings.base.Action` ready to apply. The UI offers
these as an opt-in secondary picker after the primary findings picker,
so users who want to prune by hand get the full list without being
drowned in it by default.

Plugin-bundled content is intentionally excluded — plugins already
appear as one row each in ``stale_plugin`` findings, and deleting files
inside a plugin install path gets undone on the next plugin update.
The correct action for plugin content is ``disable_plugin`` on the
whole plugin, which already exists in the primary picker.
"""

from __future__ import annotations

from unclog.findings.base import Action, Finding, Scope
from unclog.scan.tokens import TiktokenCounter
from unclog.state import InstallationState


def build_curate_findings(state: InstallationState) -> list[Finding]:
    """Return every local agent and skill as a Finding, sorted by token cost desc.

    Each Finding has ``auto_checked=False`` — curate is always opt-in
    per-item selection; we never pre-check anything in this list.
    """
    counter = TiktokenCounter()
    findings: list[Finding] = []
    gs = state.global_scope

    for agent in gs.agents:
        descriptor = f"{agent.name}: {agent.description or ''}"
        tokens = counter.count(descriptor)
        findings.append(
            Finding(
                id=f"agent_inventory:{agent.slug}",
                type="agent_inventory",
                title=f"Agent {agent.name!r}",
                reason=(agent.description or "no description").strip()[:160],
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=agent.path),
                auto_checked=False,
                token_savings=tokens if tokens > 0 else None,
                evidence={
                    "slug": agent.slug,
                    "path": str(agent.path),
                    "description": agent.description or "",
                    "note": (
                        "name + description are loaded into every session; "
                        "body content only loads when the agent is invoked"
                    ),
                },
            )
        )

    for skill in gs.skills:
        descriptor = f"{skill.name}: {skill.description or ''}"
        tokens = counter.count(descriptor)
        findings.append(
            Finding(
                id=f"skill_inventory:{skill.slug}",
                type="skill_inventory",
                title=f"Skill {skill.name!r}",
                reason=(skill.description or "no description").strip()[:160],
                scope=Scope(kind="global"),
                action=Action(primitive="delete_file", path=skill.directory),
                auto_checked=False,
                token_savings=tokens if tokens > 0 else None,
                evidence={
                    "slug": skill.slug,
                    "directory": str(skill.directory),
                    "description": skill.description or "",
                    "note": (
                        "name + description are loaded into every session; "
                        "full SKILL.md body only loads when the skill is invoked"
                    ),
                },
            )
        )

    findings.sort(key=lambda f: (-(f.token_savings or 0), f.title))
    return findings


__all__ = ["build_curate_findings"]
