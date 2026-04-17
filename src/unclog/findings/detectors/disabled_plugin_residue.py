"""Detect plugins already disabled but still occupying the plugins cache.

Per spec §6, this finding is flag-only: it never auto-checks and never
offers to delete anything directly. The reason is safety — the user
explicitly disabled the plugin, which may have been a diagnostic step
rather than a permanent decision. We surface the residue so the user
notices, but the ``uninstall_plugin`` action that actually reclaims
disk space is only surfaced after the plugin has been disabled for
≥ ``thresholds.stale_plugin_days`` (spec §6.1, §20 decision 3).
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
        is_enabled = enabled_map.get(key, False)
        if is_enabled:
            continue
        age = _installed_age_days(plugin, now)
        # Offer uninstall only when disabled and the install itself is
        # well-aged; otherwise flag only.
        offer_uninstall = age is not None and age >= thresholds.stale_plugin_days
        action = (
            Action(primitive="uninstall_plugin", plugin_key=key)
            if offer_uninstall
            else Action(primitive="flag_only", plugin_key=key)
        )
        findings.append(
            Finding(
                id=f"disabled_plugin_residue:{key}",
                type="disabled_plugin_residue",
                title=f"Disabled plugin {key} still on disk",
                reason=(
                    "disabled in settings.json but cache dir still present"
                    + (f" (install age {age}d)" if age is not None else "")
                ),
                scope=Scope(kind="global"),
                action=action,
                auto_checked=False,
                token_savings=None,
                evidence={
                    "plugin_key": key,
                    "installed_at": plugin.installed_at,
                    "installed_age_days": age,
                    "version": plugin.version,
                    "offer_uninstall": offer_uninstall,
                },
            )
        )
    return findings
