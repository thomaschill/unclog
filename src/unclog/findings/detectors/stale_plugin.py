"""Detect plugins enabled for ≥ ``thresholds.stale_plugin_days`` since install.

Spec §6 auto-check is ``No`` — plugins bundle skills/agents/commands/MCPs
that the user may still rely on. We surface the signal and let the user
decide. Action is ``disable_plugin`` (not uninstall): flipping
``enabledPlugins[<plugin_key>]`` to ``false`` stops the plugin's content
from loading on the next session without removing the cache dir. Fully
reversible by restoring the snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.filesystem import InstalledPlugin
from unclog.scan.stats import ActivityIndex
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


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
) -> list[Finding]:
    settings = state.global_scope.settings
    enabled_map = settings.enabled_plugins if settings else {}

    findings: list[Finding] = []
    for plugin in state.global_scope.installed_plugins:
        key = _plugin_key(plugin)
        if not enabled_map.get(key, False):
            # Disabled plugins belong to ``disabled_plugin_residue``.
            continue
        age = _installed_age_days(plugin, now)
        if age is None or age < thresholds.stale_plugin_days:
            continue
        # "Unused" proxy: no project activity within the threshold would
        # imply idle install; on an active install, the ``age`` check
        # alone is the signal we trust in v0.1. Record idle days if
        # available for evidence.
        idle_days: int | None = None
        if activity.last_active_overall is not None:
            idle_days = max((now - activity.last_active_overall).days, 0)

        findings.append(
            Finding(
                id=f"stale_plugin:{key}",
                type="stale_plugin",
                title=f"Disable plugin {key}",
                reason=f"installed {age}d ago; threshold {thresholds.stale_plugin_days}d",
                scope=Scope(kind="global"),
                action=Action(primitive="disable_plugin", plugin_key=key),
                auto_checked=False,
                token_savings=None,
                evidence={
                    "plugin_key": key,
                    "installed_at": plugin.installed_at,
                    "installed_age_days": age,
                    "threshold_days": thresholds.stale_plugin_days,
                    "install_idle_days": idle_days,
                    "version": plugin.version,
                },
            )
        )
    return findings
