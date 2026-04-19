"""Informational fallback for MCP probes that have no declaring config.

``dead_mcp`` is the primary detector for failed probes: every failed
probe of a *declared* server becomes a ``dead_mcp`` finding with an
actionable ``comment_out_mcp`` primitive. This detector covers the
remaining edge case — a probe entry for a server that no longer
appears in ``.claude.json`` or any project config (e.g. the server
was removed between scan steps, or the probe map and config view
diverge in some future layout).

Flag-only: with no declaring config to edit, there's nothing to
auto-fix. The user has to clean up whatever state carries the
orphan entry.
"""

from __future__ import annotations

from datetime import datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.state import InstallationState


def _all_declared_server_names(state: InstallationState) -> set[str]:
    """Return every server name Claude Code config declares.

    Mirrors ``dead_mcp._all_declared_servers`` but returns just the set
    of names — we only care whether dead_mcp will own the finding.
    """
    config = state.global_scope.config
    if config is None:
        return set()
    names: set[str] = set(config.mcp_servers)
    for project in config.projects.values():
        names.update(project.mcp_servers)
    return names


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
) -> list[Finding]:
    del activity, thresholds, now  # signature parity with other detectors

    probes = state.global_scope.mcp_probes
    if not probes:
        return []

    # dead_mcp already emits an actionable finding for every failed
    # probe of a *declared* server; duplicating it here would produce
    # two rows per failure with the same title, inflating the summary
    # count and confusing the picker. Only cover probes with no
    # declaring config (orphans).
    declared = _all_declared_server_names(state)

    findings: list[Finding] = []
    for name in sorted(probes):
        probe = probes[name]
        if probe.ok:
            continue
        if name in declared:
            continue
        error = probe.error or "probe failed"
        findings.append(
            Finding(
                id=f"failed_mcp_probe:{name}",
                type="failed_mcp_probe",
                title=f"MCP {name!r} failed to start (orphan probe entry)",
                reason=error,
                scope=Scope(kind="global"),
                action=Action(primitive="flag_only", server_name=name),
                auto_checked=False,
                token_savings=None,
                evidence={
                    "server_name": name,
                    "error": error,
                    "stderr_tail": probe.stderr_tail,
                    "duration_ms": probe.duration_ms,
                },
            )
        )
    return findings
