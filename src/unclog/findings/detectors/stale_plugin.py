"""Surface every enabled plugin as a candidate to disable.

v0.1 trades conservatism for transparency: every plugin whose
``enabledPlugins[<key>]`` is ``true`` emits a finding with a real
token-cost estimate (sum of its bundled skills' and agents'
name + description tokens). ``auto_checked`` is always ``False`` —
plugins bundle content the user may still rely on, and the action is
destructive-ish (disabling stops load on the next session).

Historical context: earlier drafts gated on ``installed_age_days ≥
thresholds.stale_plugin_days`` so recently-installed plugins were
ignored. In practice that hid the biggest wins; a fresh 6,000-token
plugin you never use is exactly what the user wants to see. The
evidence block still records age + install-activity for the JSON
consumers.

Action is ``disable_plugin`` (not uninstall): flipping
``enabledPlugins[<plugin_key>]`` to ``false`` stops the plugin's content
from loading on the next session without removing the cache dir. Fully
reversible by restoring the snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.filesystem import InstalledPlugin, PluginContent
from unclog.scan.stats import ActivityIndex
from unclog.scan.tokens import count_tokens
from unclog.state import InstallationState


def _plugin_key(plugin: InstalledPlugin) -> str:
    return f"{plugin.name}@{plugin.marketplace}" if plugin.marketplace else plugin.name


def _installed_age_days(plugin: InstalledPlugin, now: datetime) -> int | None:
    if plugin.installed_at is None:
        return None
    text = plugin.installed_at.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max((now - parsed).days, 0)


def _token_cost(content: PluginContent) -> int:
    total = 0
    for skill in content.skills:
        total += count_tokens(f"{skill.name}: {skill.description or ''}")
    for agent in content.agents:
        total += count_tokens(f"{agent.name}: {agent.description or ''}")
    return total


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,  # noqa: ARG001 — kept for stable detector signature
    *,
    now: datetime,
) -> list[Finding]:
    settings = state.global_scope.settings
    enabled_map = settings.enabled_plugins if settings else {}
    content_by_key = {c.plugin_key: c for c in state.global_scope.plugin_content}

    findings: list[Finding] = []
    for plugin in state.global_scope.installed_plugins:
        key = _plugin_key(plugin)
        if not enabled_map.get(key, False):
            # Disabled plugins belong to ``disabled_plugin_residue``.
            continue
        age = _installed_age_days(plugin, now)
        idle_days: int | None = None
        if activity.last_active_overall is not None:
            idle_days = max((now - activity.last_active_overall).days, 0)

        content = content_by_key.get(key)
        n_skills = len(content.skills) if content else 0
        n_agents = len(content.agents) if content else 0
        token_savings = _token_cost(content) if content else 0

        reason_parts: list[str] = []
        if n_skills or n_agents:
            pieces: list[str] = []
            if n_skills:
                pieces.append(f"{n_skills} skill(s)")
            if n_agents:
                pieces.append(f"{n_agents} agent(s)")
            reason_parts.append("bundles " + " + ".join(pieces))
        else:
            reason_parts.append("no bundled skills or agents found")
        if age is not None:
            reason_parts.append(f"installed {age}d ago")

        findings.append(
            Finding(
                id=f"stale_plugin:{key}",
                type="stale_plugin",
                title=f"Disable plugin {key}",
                reason="; ".join(reason_parts),
                scope=Scope(kind="global"),
                action=Action(primitive="disable_plugin", plugin_key=key),
                auto_checked=False,
                token_savings=token_savings if token_savings > 0 else None,
                evidence={
                    "plugin_key": key,
                    "installed_at": plugin.installed_at,
                    "installed_age_days": age,
                    "install_idle_days": idle_days,
                    "version": plugin.version,
                    "bundled_skill_count": n_skills,
                    "bundled_agent_count": n_agents,
                    "bundled_token_cost": token_savings,
                },
            )
        )
    return findings
