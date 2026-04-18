"""Promote MCP probe failures from hero-line footnotes to real findings.

When ``--probe-mcps`` (default in v0.1) spawns a declared MCP server
and the server fails to start — command not on PATH, crashes during
the JSON-RPC handshake, times out — we previously surfaced the count
as a ``"N MCP unmeasured"`` note in the hero line. That was dense
jargon that didn't tell the user which server was broken or what to
do about it.

This detector reads ``state.global_scope.mcp_probes`` (populated by
:mod:`unclog.scan.mcp_probe`) and emits one informational finding per
failed probe so the user sees a concrete ``MCP 'X' failed to start``
row in the findings summary, with the truncated stderr in ``evidence``
for ``--json`` consumers.

Flag-only (spec §6.1): we can't auto-fix a broken server config. The
user needs to check the command, the env, or reinstall the server.
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
    probes = state.global_scope.mcp_probes
    if not probes:
        return []

    findings: list[Finding] = []
    for name in sorted(probes):
        probe = probes[name]
        if probe.ok:
            continue
        error = probe.error or "probe failed"
        findings.append(
            Finding(
                id=f"failed_mcp_probe:{name}",
                type="failed_mcp_probe",
                title=f"MCP {name!r} failed to start",
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
