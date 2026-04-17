"""Detect MCP servers that are loaded every session but never invoked.

Every MCP an installation ships with loads its tool schemas into every
session whether the user calls it or not. A server with, say, 40 tools
at ~200 tokens each is 8k tokens of schema overhead on every single
prompt — perpetual cost for zero value if nobody's calling it.

v0.1 signal: the server shows up in the latest session's ``tools`` array
(so it is being loaded) but zero ``tool_use`` records across the latest
session of every project reference it. We parse those counts up-front in
``scan_global`` and hang them off :class:`GlobalScope.mcp_invocations`.

Auto-check is never set for ``unused_mcp`` (spec §6): removing an MCP a
user is mid-setup on would be counterproductive. Action is
``comment_out_mcp`` so the config is preserved and snapshot-reversible.
Token savings come from the session's own tools-schema attribution, so
the number shown is the real per-session cost.
"""

from __future__ import annotations

import json
from datetime import datetime

from unclog.findings.base import Action, Finding, Scope
from unclog.findings.thresholds import Thresholds
from unclog.scan.stats import ActivityIndex
from unclog.scan.tokens import TiktokenCounter
from unclog.state import InstallationState

_MCP_TOOL_PREFIX = "mcp__"


def _loaded_server_tokens(state: InstallationState) -> dict[str, int]:
    """Map each MCP server loaded in the latest session to its schema tokens."""
    session = state.global_scope.latest_session
    if session is None:
        return {}
    counter = TiktokenCounter()
    per_server: dict[str, int] = {}
    for tool in session.tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name.startswith(_MCP_TOOL_PREFIX):
            continue
        remainder = name[len(_MCP_TOOL_PREFIX) :]
        server, _, _ = remainder.partition("__")
        if not server:
            continue
        blob = json.dumps(tool, separators=(",", ":"))
        per_server[server] = per_server.get(server, 0) + counter.count(blob)
    return per_server


def detect(
    state: InstallationState,
    activity: ActivityIndex,
    thresholds: Thresholds,
    *,
    now: datetime,
) -> list[Finding]:
    del activity, thresholds, now  # signature parity with other detectors
    loaded = _loaded_server_tokens(state)
    if not loaded:
        return []

    invocations = state.global_scope.mcp_invocations
    session = state.global_scope.latest_session
    session_note = str(session.session_path) if session is not None else ""

    findings: list[Finding] = []
    for name in sorted(loaded):
        count = invocations.get(name, 0)
        if count > 0:
            continue
        tokens = loaded[name]
        findings.append(
            Finding(
                id=f"unused_mcp:{name}",
                type="unused_mcp",
                title=f"MCP {name!r} loads every session but was never invoked",
                reason="tool schemas loaded; zero tool_use records across recent sessions",
                scope=Scope(kind="global"),
                action=Action(primitive="comment_out_mcp", server_name=name),
                auto_checked=False,
                token_savings=tokens or None,
                evidence={
                    "server_name": name,
                    "session_path": session_note,
                    "invocations_observed": 0,
                    "note": (
                        "invocation count aggregated across each project's latest "
                        "session; disable with comment_out_mcp, reversible via snapshot"
                    ),
                },
            )
        )
    return findings
