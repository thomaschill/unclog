"""Render an :class:`InstallationState` to stdout in the requested format.

M2 swaps the byte-count estimates used in M1 for real tiktoken
measurements. Every composition entry now reports a concrete token
count. MCP servers are attributed from the ``tools`` array of the
most recent session JSONL across all known projects — we measure what
Claude Code actually injected rather than what might theoretically
load.

When no session JSONL is available anywhere (fresh installs, or a home
that has never opened a project), MCP entries stay marked
``unmeasured`` and the baseline degrades to the sum of file-backed
sources. Tokens for CLAUDE.md, skills, and agents are always measured
(we have the source bytes on disk).

``--json`` output keeps the stable ``unclog.v0.1`` schema shape. Field
names that used to advertise ``bytes_estimate`` now read ``tiktoken``
(or ``unmeasured`` for MCP without a session).
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.text import Text

from unclog import __version__
from unclog.findings import detect as detect_findings
from unclog.findings import load_thresholds
from unclog.findings.base import Finding
from unclog.findings.curate import build_curate_findings
from unclog.scan.session import SessionSystemBlock
from unclog.scan.tokens import TiktokenCounter, TokenCounter
from unclog.state import InstallationState
from unclog.ui.chrome import hint_bar, section_rule
from unclog.ui.hero import render_baseline_panel
from unclog.ui.theme import ACCENT, DIM, SEVERITY_OK
from unclog.ui.welcome import (
    first_run_tip_line,
    is_first_run,
    mark_first_run_seen,
    welcome_panel,
)
from unclog.util.paths import ClaudePaths

# Claude Code's nested-detail connector — anchors grouped child rows
# to the summary line above them. Same glyph used in the interactive
# flow's applied/failed blocks.
_CONNECTOR = "⎿"

SCHEMA_ID = "unclog.v0.1"

# Token naming convention for MCP tools in Claude Code sessions:
# ``mcp__<server>__<tool>``. Built-in tools (Read, Write, Bash, etc.)
# do not carry this prefix.
_MCP_TOOL_PREFIX = "mcp__"

# Category colours — deliberately mirror the picker's badge palette so
# the user sees the same colour for "skill" in the scan summary and in
# the picker, and their eye learns the taxonomy across screens.
_INVENTORY_CHIP_COLOUR: dict[str, str] = {
    "skills": "#22c55e",
    "agents": "#38bdf8",
    "commands": "#a78bfa",
    "plugins": "#e879f9",
    "mcp": "#fb923c",
    "hooks": "#f43f5e",
    "projects": "#a3a3a3",
}

# Hook events that fire on every prompt — the highest-cost ones to leave
# unaudited. ``PreToolUse`` / ``PostToolUse`` fire per tool invocation
# which is usually per turn in practice but we only surface the
# guaranteed-every-turn events so the "heavy" signal stays truthful.
_EVERY_TURN_HOOK_EVENTS: frozenset[str] = frozenset({"SessionStart", "UserPromptSubmit"})


def _mcp_attribution(session: SessionSystemBlock, counter: TokenCounter) -> dict[str, int]:
    """Group session tools by MCP server name and sum per-tool token counts.

    Tools that don't match the ``mcp__<server>__`` convention (built-in
    Claude Code tools like ``Read`` / ``Bash``) are excluded.
    """
    per_server: dict[str, int] = {}
    for tool in session.tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name.startswith(_MCP_TOOL_PREFIX):
            continue
        remainder = name[len(_MCP_TOOL_PREFIX) :]
        server_name, _, _ = remainder.partition("__")
        if not server_name:
            continue
        blob = json.dumps(tool, separators=(",", ":"))
        per_server[server_name] = per_server.get(server_name, 0) + counter.count(blob)
    return per_server


def _mcp_entry(
    name: str,
    measured: int | None,
    *,
    scope: str,
    probed: int | None = None,
    probe_failed: bool = False,
    extra_note: str | None = None,
) -> dict[str, Any]:
    """Build a composition row for a single MCP server.

    Three token sources, ranked by authority:

    - ``probed``: ``--probe-mcps`` spawned the server and counted its
      tools schema directly. Most accurate; render as ``probe+tiktoken``.
    - ``measured``: per-server cost derived from the latest session's
      tools array. Current Claude Code session JSONLs rarely carry
      schemas, so this is usually ``None`` in practice.
    - Neither: ``unmeasured``. Fallback.

    ``probe_failed`` documents that the probe ran but the server
    couldn't start — the composition still renders as ``unmeasured``
    (there's no schema to measure) but the note makes it clear the
    server is broken, not merely unseen.
    """
    if probed is not None:
        note = extra_note
        tokens_source = "probe+tiktoken"
        tokens: int | None = probed
    elif measured is not None:
        note = extra_note
        tokens_source = "session+tiktoken"
        tokens = measured
    else:
        if probe_failed:
            base_note = "probe failed — see findings for stderr"
        else:
            base_note = (
                "schema not in session JSONL; run with --probe-mcps to measure live"
            )
        note = f"{base_note}; {extra_note}" if extra_note else base_note
        tokens_source = "unmeasured"
        tokens = None
    return {
        "source": f"mcp:{name}",
        "scope": scope,
        "bytes": None,
        "tokens": tokens,
        "tokens_source": tokens_source,
        "note": note,
    }


def build_composition(state: InstallationState, counter: TokenCounter) -> list[dict[str, Any]]:
    """Return the composition breakdown, largest first."""
    entries: list[dict[str, Any]] = []
    gs = state.global_scope

    if gs.claude_md_text:
        entries.append(
            {
                "source": "global:CLAUDE.md",
                "scope": "global",
                "bytes": gs.claude_md_bytes,
                "tokens": counter.count(gs.claude_md_text),
                "tokens_source": "tiktoken",
            }
        )
    if gs.claude_local_md_text:
        entries.append(
            {
                "source": "global:CLAUDE.local.md",
                "scope": "global",
                "bytes": gs.claude_local_md_bytes,
                "tokens": counter.count(gs.claude_local_md_text),
                "tokens_source": "tiktoken",
            }
        )

    if gs.skills:
        skill_tokens = sum(counter.count(f"{s.name}: {s.description or ''}") for s in gs.skills)
        skill_bytes = sum(s.frontmatter_bytes for s in gs.skills)
        entries.append(
            {
                "source": f"skills:descriptions (n={len(gs.skills)})",
                "scope": "global",
                "bytes": skill_bytes,
                "tokens": skill_tokens,
                "tokens_source": "tiktoken",
                "note": "name + description per skill, loaded on every session",
            }
        )

    if gs.agents:
        agent_tokens = sum(counter.count(f"{a.name}: {a.description or ''}") for a in gs.agents)
        agent_bytes = sum(a.frontmatter_bytes for a in gs.agents)
        entries.append(
            {
                "source": f"agents:descriptions (n={len(gs.agents)})",
                "scope": "global",
                "bytes": agent_bytes,
                "tokens": agent_tokens,
                "tokens_source": "tiktoken",
            }
        )

    for content in gs.plugin_content:
        if not content.skills and not content.agents:
            continue
        plugin_tokens = sum(
            counter.count(f"{s.name}: {s.description or ''}") for s in content.skills
        ) + sum(
            counter.count(f"{a.name}: {a.description or ''}") for a in content.agents
        )
        plugin_bytes = sum(s.frontmatter_bytes for s in content.skills) + sum(
            a.frontmatter_bytes for a in content.agents
        )
        breakdown = f"n_skills={len(content.skills)}, n_agents={len(content.agents)}"
        entries.append(
            {
                "source": f"plugin:{content.plugin_key}:bundled ({breakdown})",
                "scope": "global",
                "bytes": plugin_bytes,
                "tokens": plugin_tokens,
                "tokens_source": "tiktoken",
                "note": "bundled by plugin; disable plugin in settings.json to skip",
            }
        )

    memory_projects = [p for p in state.project_scopes if p.memory_md_text]
    if memory_projects:
        memory_tokens = sum(counter.count(p.memory_md_text) for p in memory_projects)
        memory_bytes = sum(p.memory_md_bytes for p in memory_projects)
        entries.append(
            {
                "source": f"auto-memory (n={len(memory_projects)})",
                "scope": "project",
                "bytes": memory_bytes,
                "tokens": memory_tokens,
                "tokens_source": "tiktoken",
                "note": (
                    "~/.claude/projects/<encoded>/memory/MEMORY.md files — "
                    "auto-injected into every session prompt, truncated past ~200 lines"
                ),
            }
        )

    session = gs.latest_session
    attribution = _mcp_attribution(session, counter) if session else {}

    # Global MCPs: load unconditionally into every session.
    global_mcp = gs.config.mcp_servers if gs.config else {}
    probes = gs.mcp_probes
    for name in sorted(global_mcp):
        measured = attribution.get(name)
        probe = probes.get(name) if probes else None
        probed = probe.tools_tokens if probe is not None and probe.ok else None
        probe_failed = probe is not None and not probe.ok
        entries.append(
            _mcp_entry(
                name,
                measured,
                scope="global",
                probed=probed,
                probe_failed=probe_failed,
            )
        )

    # Project-scoped MCPs: only load when that project is open, but
    # they still bloat the baseline for users who spend most of their
    # time in a single project. Collapse identical configs across
    # projects (name + command + args) so a shared MCP appears once
    # with the list of projects that declare it.
    project_groups: dict[tuple[str, str | None, tuple[str, ...]], list[str]] = {}
    project_attribution: dict[tuple[str, str | None, tuple[str, ...]], int] = {}
    if gs.config:
        for project in gs.config.projects.values():
            for srv_name, srv in project.mcp_servers.items():
                key = (srv_name, srv.command, srv.args)
                project_groups.setdefault(key, []).append(str(project.path))
                m = attribution.get(srv_name)
                if m is not None and key not in project_attribution:
                    project_attribution[key] = m
    for (srv_name, _cmd, _args), project_paths in sorted(project_groups.items()):
        measured = project_attribution.get((srv_name, _cmd, _args))
        probe = probes.get(srv_name) if probes else None
        probed = probe.tools_tokens if probe is not None and probe.ok else None
        probe_failed = probe is not None and not probe.ok
        scope_label = (
            f"project:{project_paths[0]}"
            if len(project_paths) == 1
            else f"project:{len(project_paths)} projects"
        )
        entries.append(
            _mcp_entry(
                srv_name,
                measured,
                scope=scope_label,
                probed=probed,
                probe_failed=probe_failed,
                extra_note=(
                    None
                    if len(project_paths) == 1
                    else f"declared in {len(project_paths)} projects"
                ),
            )
        )

    def _rank(entry: dict[str, Any]) -> int:
        tokens = entry.get("tokens")
        return tokens if isinstance(tokens, int) else -1

    entries.sort(key=_rank, reverse=True)
    return entries


def _mcp_label(inv: dict[str, Any]) -> str:
    """Render the MCP-server count with project-scope breakdown when relevant.

    Project-scoped MCPs live in ``~/.claude.json``'s per-project sections
    and only load when that project is open. Surfacing them separately
    prevents the "0 MCP servers" confusion for users whose entire MCP
    footprint is project-scoped.
    """
    total = inv["mcp_servers"]
    project_scoped = inv.get("mcp_servers_project", 0)
    if total == 0:
        return "0 MCP servers"
    if project_scoped and project_scoped == total:
        return f"{total} MCP servers (project-scoped)"
    if project_scoped:
        return f"{total} MCP servers ({project_scoped} project-scoped)"
    return f"{total} MCP servers"


def _hooks_label(inv: dict[str, Any]) -> str:
    """Render the hooks count with project-scope breakdown when relevant.

    Claude Code fires hooks silently on every session; every hook's
    stdout is injected into context, so users routinely underestimate
    what is running on their behalf. Always surface the count, even
    zero, so the absence is explicit.
    """
    total = inv.get("hooks", 0)
    project_scoped = inv.get("hooks_project", 0)
    if total == 0:
        return "0 hooks"
    if project_scoped and project_scoped == total:
        return f"{total} hooks (project-scoped)"
    if project_scoped:
        return f"{total} hooks ({project_scoped} project-scoped)"
    return f"{total} hooks"


def _inventory(state: InstallationState) -> dict[str, int]:
    gs = state.global_scope
    global_mcp = len(gs.config.mcp_servers) if gs.config else 0
    project_mcp = 0
    if gs.config:
        for project in gs.config.projects.values():
            project_mcp += len(project.mcp_servers)
    global_hooks = len(gs.settings.hooks) if gs.settings else 0
    project_hooks = sum(len(p.hooks) for p in state.project_scopes)
    return {
        "skills": len(gs.skills),
        "agents": len(gs.agents),
        "commands": len(gs.commands),
        "plugins": len(gs.installed_plugins),
        "mcp_servers": global_mcp + project_mcp,
        "mcp_servers_global": global_mcp,
        "mcp_servers_project": project_mcp,
        "hooks": global_hooks + project_hooks,
        "hooks_global": global_hooks,
        "hooks_project": project_hooks,
        "projects_known": len(gs.config.projects) if gs.config else 0,
    }


def _baseline(
    composition: list[dict[str, Any]], session: SessionSystemBlock | None
) -> dict[str, Any]:
    attributed_tokens = sum(e["tokens"] for e in composition if isinstance(e.get("tokens"), int))
    unmeasured_sources = sum(1 for e in composition if e["tokens_source"] == "unmeasured")

    if session is not None:
        total = session.total_tokens
        tokens_source = "session+tiktoken"
    else:
        total = attributed_tokens
        tokens_source = "tiktoken" if attributed_tokens else "empty"

    return {
        "estimated_tokens": total,
        "attributed_tokens": attributed_tokens,
        "tokens_source": tokens_source,
        "unmeasured_sources": unmeasured_sources,
        "session_path": str(session.session_path) if session else None,
    }


def _load_findings(
    state: InstallationState, *, warnings: list[str] | None = None
) -> list[Finding]:
    """Run all detectors against ``state`` using the user's thresholds.

    The threshold config is loaded relative to the scanned ``claude_home``
    so the same state can be reported from different CWDs without
    picking up ambient config. ``warnings`` is populated with any
    detector-skipped notices so the caller can surface them alongside
    the scan warnings already on ``state.warnings``.
    """
    paths = ClaudePaths(home=state.claude_home)
    thresholds = load_thresholds(paths.config_toml)
    return detect_findings(
        state,
        state.global_scope.activity,
        thresholds,
        now=state.generated_at,
        warnings=warnings,
    )


def build_report(state: InstallationState) -> dict[str, Any]:
    """Return the full machine-readable report as a plain dict.

    Detector warnings (from broken detectors that were skipped) are
    merged into the ``warnings`` field alongside scan warnings so JSON
    consumers see the same signal as the TTY renderer. Without this
    merge, a CI pipeline reading ``--json`` would silently miss a
    category whose detector crashed on bad input.
    """
    counter = TiktokenCounter()
    composition = build_composition(state, counter)
    session = state.global_scope.latest_session
    detector_warnings: list[str] = []
    findings = _load_findings(state, warnings=detector_warnings)
    report: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "unclog_version": __version__,
        "generated_at": state.generated_at.isoformat().replace("+00:00", "Z"),
        "claude_home": str(state.claude_home),
        "baseline": _baseline(composition, session),
        "inventory": _inventory(state),
        "composition": composition,
        "findings": [f.to_json() for f in findings],
        "warnings": [*state.warnings, *detector_warnings],
        "projects_audited": _projects_audited(state),
    }
    probes = state.global_scope.mcp_probes
    if probes:
        report["mcp_probes"] = [probes[name].to_json() for name in sorted(probes)]
    return report


def _projects_audited(state: InstallationState) -> list[dict[str, Any]]:
    """Summarise the project scopes the scan actually read.

    Used by the JSON schema and the rich/plain renderers so users see
    which CLAUDE.md files the cross-scope detectors had access to —
    the difference between "no findings because nothing's wrong" and
    "no findings because we didn't scan any projects".
    """
    counter = TiktokenCounter()
    return [
        {
            "path": str(project.path),
            "name": project.name,
            "exists": project.exists,
            "claude_md_bytes": project.claude_md_bytes,
            "claude_md_tokens": (
                counter.count(project.claude_md_text) if project.claude_md_text else 0
            ),
            "claude_local_md_bytes": project.claude_local_md_bytes,
            "claude_local_md_tokens": (
                counter.count(project.claude_local_md_text)
                if project.claude_local_md_text
                else 0
            ),
            "memory_md_bytes": project.memory_md_bytes,
            "memory_md_tokens": (
                counter.count(project.memory_md_text) if project.memory_md_text else 0
            ),
            "has_claudeignore": project.has_claudeignore,
        }
        for project in state.project_scopes
    ]


def _claude_md_rows(state: InstallationState) -> list[dict[str, Any]]:
    """Flat per-file rows for the CLAUDE.md + auto-memory listing.

    Includes the global ``CLAUDE.md`` / ``CLAUDE.local.md``, per-project
    CLAUDE.md pairs, and the per-project auto-memory index file Claude
    Code persists at ``~/.claude/projects/<encoded>/memory/MEMORY.md``.
    Missing files are reported as rows with ``tokens=None`` and a
    ``status`` string so users can distinguish "file not present" from
    "project path missing on disk".
    """
    counter = TiktokenCounter()
    rows: list[dict[str, Any]] = []
    gs = state.global_scope

    rows.append(
        {
            "scope": "global",
            "name": "CLAUDE.md",
            "path": str(gs.claude_home / "CLAUDE.md"),
            "tokens": counter.count(gs.claude_md_text) if gs.claude_md_text else None,
            "bytes": gs.claude_md_bytes,
            "status": "present" if gs.claude_md_text else "absent",
        }
    )
    rows.append(
        {
            "scope": "global",
            "name": "CLAUDE.local.md",
            "path": str(gs.claude_home / "CLAUDE.local.md"),
            "tokens": (
                counter.count(gs.claude_local_md_text) if gs.claude_local_md_text else None
            ),
            "bytes": gs.claude_local_md_bytes,
            "status": "present" if gs.claude_local_md_text else "absent",
        }
    )

    for project in state.project_scopes:
        if not project.exists:
            rows.append(
                {
                    "scope": "project",
                    "name": project.name,
                    "path": str(project.claude_md_path),
                    "tokens": None,
                    "bytes": 0,
                    "status": "path_missing",
                }
            )
            continue
        rows.append(
            {
                "scope": "project",
                "name": project.name,
                "path": str(project.claude_md_path),
                "tokens": (
                    counter.count(project.claude_md_text) if project.claude_md_text else None
                ),
                "bytes": project.claude_md_bytes,
                "status": "present" if project.claude_md_text else "absent",
            }
        )
        if project.claude_local_md_text:
            rows.append(
                {
                    "scope": "project",
                    "name": f"{project.name} (CLAUDE.local.md)",
                    "path": str(project.claude_local_md_path),
                    "tokens": counter.count(project.claude_local_md_text),
                    "bytes": project.claude_local_md_bytes,
                    "status": "present",
                }
            )

    for project in state.project_scopes:
        if not project.memory_md_text:
            continue
        rows.append(
            {
                "scope": "memory",
                "name": project.name,
                "path": str(project.memory_md_path),
                "tokens": counter.count(project.memory_md_text),
                "bytes": project.memory_md_bytes,
                "status": "present",
            }
        )
    return rows


def _claude_md_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Aggregate counts for the listing footer."""
    present = [r for r in rows if r["status"] == "present"]
    project_rows = [r for r in rows if r["scope"] == "project"]
    return {
        "files_present": len(present),
        "tokens_total": sum(r["tokens"] or 0 for r in present),
        "projects_scanned": len(project_rows),
        "projects_missing": sum(1 for r in rows if r["status"] == "path_missing"),
        "projects_empty": sum(
            1 for r in project_rows if r["status"] == "absent"
        ),
    }


def render_claude_md_listing_plain(state: InstallationState) -> str:
    """Plain-text listing of every auto-injected context file unclog sees.

    Diagnostic output for ``--list-claude-md`` — confirms the scan is
    actually finding every CLAUDE.md and auto-memory file before the
    user starts tuning thresholds or chasing missing-finding bugs.
    """
    rows = _claude_md_rows(state)
    totals = _claude_md_totals(rows)
    lines: list[str] = []
    lines.append("Auto-injected context files found:")
    lines.append("")
    lines.append("global CLAUDE.md:")
    for r in rows:
        if r["scope"] != "global":
            continue
        tokens = "—" if r["tokens"] is None else f"{r['tokens']:>8,} tok"
        status = "" if r["status"] == "present" else f"  ({r['status']})"
        lines.append(f"  {tokens}  {r['name']}{status}  {r['path']}")
    lines.append("")
    lines.append("project CLAUDE.md:")
    project_rows = [r for r in rows if r["scope"] == "project"]
    content_rows = [r for r in project_rows if r["status"] == "present"]
    empty = totals["projects_empty"]
    missing = totals["projects_missing"]
    hidden: list[str] = []
    if empty:
        hidden.append(f"{empty} without a CLAUDE.md")
    if missing:
        hidden.append(f"{missing} path missing")
    if not project_rows:
        lines.append("  (no projects scanned)")
    elif not content_rows:
        lines.append("  (none of the known projects have a CLAUDE.md)")
        if hidden:
            lines.append(f"  (hidden: {' · '.join(hidden)})")
    else:
        for r in content_rows:
            tokens = f"{r['tokens']:>8,} tok"
            lines.append(f"  {tokens}  {r['name']}  {r['path']}")
        if hidden:
            lines.append(f"  (hidden: {' · '.join(hidden)})")
    lines.append("")
    lines.append("auto-memory (MEMORY.md):")
    memory_rows = [r for r in rows if r["scope"] == "memory"]
    if not memory_rows:
        lines.append("  (no auto-memory files found)")
    else:
        for r in memory_rows:
            tokens = f"{r['tokens']:>8,} tok"
            lines.append(f"  {tokens}  {r['name']}  {r['path']}")
    lines.append("")
    lines.append(
        f"totals: {totals['files_present']} file(s) present  ·  "
        f"{totals['tokens_total']:,} tokens total  ·  "
        f"{totals['projects_scanned']} project(s) scanned"
        + (f"  ·  {totals['projects_missing']} missing" if totals["projects_missing"] else "")
    )
    return "\n".join(lines) + "\n"


def render_claude_md_listing_rich(state: InstallationState, console: Console) -> None:
    """Rich TTY rendering of the auto-injected context listing.

    Uses the same per-category colouring as the inventory chips so the
    diagnostic output reads as part of the same product surface.
    """
    rows = _claude_md_rows(state)
    totals = _claude_md_totals(rows)
    console.print(Text("Auto-injected context files found:", style=f"bold {ACCENT}"))
    console.print("")
    console.print(Text("global CLAUDE.md", style=DIM))
    for r in rows:
        if r["scope"] != "global":
            continue
        _print_claude_md_row(console, r)
    console.print("")
    project_rows = [r for r in rows if r["scope"] == "project"]
    content_rows = [r for r in project_rows if r["status"] == "present"]
    empty = totals["projects_empty"]
    missing = totals["projects_missing"]
    hidden: list[str] = []
    if empty:
        hidden.append(f"{empty} without a CLAUDE.md")
    if missing:
        hidden.append(f"{missing} path missing")
    console.print(
        Text(
            f"project CLAUDE.md ({len(content_rows)}/{len(project_rows)} with content)",
            style=DIM,
        )
    )
    if not project_rows:
        console.print("  [dim](no projects scanned)[/dim]")
    elif not content_rows:
        console.print("  [dim](none of the known projects have a CLAUDE.md)[/dim]")
        if hidden:
            console.print(f"  [dim](hidden: {' · '.join(hidden)})[/dim]")
    else:
        for r in content_rows:
            _print_claude_md_row(console, r)
        if hidden:
            console.print(f"  [dim](hidden: {' · '.join(hidden)})[/dim]")
    console.print("")
    memory_rows = [r for r in rows if r["scope"] == "memory"]
    console.print(Text(f"auto-memory MEMORY.md ({len(memory_rows)})", style=DIM))
    if not memory_rows:
        console.print("  [dim](no auto-memory files found)[/dim]")
    else:
        for r in memory_rows:
            _print_claude_md_row(console, r)
    console.print("")
    footer = Text()
    footer.append(f"{totals['files_present']} file(s) present", style=f"bold {ACCENT}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"{totals['tokens_total']:,} tokens total", style=f"bold {ACCENT}")
    footer.append("  ·  ", style=DIM)
    footer.append(f"{totals['projects_scanned']} project(s) scanned", style=DIM)
    if totals["projects_missing"]:
        footer.append("  ·  ", style=DIM)
        footer.append(f"{totals['projects_missing']} missing", style="#eab308")
    console.print(footer)


def _print_claude_md_row(console: Console, row: dict[str, Any]) -> None:
    line = Text("  ")
    if row["tokens"] is None:
        line.append(f"{'—':>8}    ", style=DIM)
    else:
        line.append(f"{row['tokens']:>8,} tok", style=f"bold {ACCENT}")
    line.append("  ")
    line.append(row["name"])
    if row["status"] == "path_missing":
        line.append("  (path missing)", style="#eab308")
    elif row["status"] == "absent":
        line.append("  (no CLAUDE.md)", style=DIM)
    line.append("  ")
    line.append(row["path"], style=DIM)
    console.print(line)


def baseline_tokens(state: InstallationState) -> int:
    """Compute the current baseline-token total for ``state``.

    Used by the interactive flow to seed the post-apply countdown (spec
    §11.8). Separate from :func:`build_report` to keep the countdown
    path light — no findings detection needed.
    """
    counter = TiktokenCounter()
    composition = build_composition(state, counter)
    session = state.global_scope.latest_session
    return int(_baseline(composition, session)["estimated_tokens"] or 0)


def render_json(state: InstallationState) -> str:
    """Render the state as a single JSON document."""
    return json.dumps(build_report(state), indent=2, sort_keys=False)


def render_plain(state: InstallationState) -> str:
    """ASCII-only, colour-free text render. Used for ``--plain`` and non-TTY."""
    report = build_report(state)
    lines: list[str] = []
    lines.append(f"unclog {report['unclog_version']}")
    lines.append(f"claude_home: {report['claude_home']}")
    lines.append("")
    baseline = report["baseline"]
    lines.append(f"baseline: ~{baseline['estimated_tokens']:,} tokens")
    lines.append("")
    inv = report["inventory"]
    lines.append(
        "inventory: "
        f"{inv['skills']} skills | {inv['agents']} agents | "
        f"{inv['commands']} commands | {inv['plugins']} plugins | "
        f"{_mcp_label(inv)} | {_hooks_label(inv)} | "
        f"{inv['projects_known']} known projects"
    )
    if report["composition"]:
        lines.append("")
        lines.append("composition (largest first):")
        for entry in report["composition"]:
            tokens = entry.get("tokens")
            size = "unmeasured" if tokens is None else f"{tokens:>8,} tok"
            scope = entry.get("scope")
            scope_suffix = (
                f"  [{scope}]" if isinstance(scope, str) and scope.startswith("project:") else ""
            )
            lines.append(f"  {size}  {entry['source']}{scope_suffix}")
    if report["findings"]:
        lines.append("")
        auto = sum(1 for f in report["findings"] if f.get("auto_checked"))
        info = sum(
            1
            for f in report["findings"]
            if f.get("action", {}).get("primitive") == "flag_only"
        )
        opt_in = len(report["findings"]) - auto - info
        parts = [f"{auto} auto-fix"]
        if opt_in:
            parts.append(f"{opt_in} opt-in")
        if info:
            parts.append(f"{info} informational")
        lines.append(
            f"findings: {len(report['findings'])} ({', '.join(parts)})"
        )
        for f in report["findings"]:
            primitive = f.get("action", {}).get("primitive")
            if f.get("auto_checked"):
                marker = "[x]"
            elif primitive == "flag_only":
                marker = "[i]"
            else:
                marker = "[ ]"
            scope = f["scope"].get("kind", "global")
            lines.append(f"  {marker} [{scope:>7}] {f['title']} - {f['reason']}")
    else:
        lines.append("")
        lines.append("findings: none")
    if report["warnings"]:
        lines.append("")
        lines.append("warnings:")
        for w in report["warnings"]:
            lines.append(f"  ! {w}")
    return "\n".join(lines) + "\n"


def render_rich(
    state: InstallationState,
    console: Console,
    *,
    show_wordmark: bool = True,
    verbose: bool = False,
) -> None:
    """Pretty TTY render in the Claude-Code visual vocabulary.

    Frame order: welcome panel → baseline panel → inventory section →
    findings section → also-running section → warnings → hint bar.

    ``show_wordmark`` is preserved for backwards compatibility: when
    False the welcome panel is suppressed (used by ``--report`` and
    non-TTY fallback paths). Spec §11.4 still applies —
    ``--json``/``--plain`` never route through this renderer.

    ``verbose`` toggles the full pre-picker chrome (scan-meta block,
    persistent tips, "also running" footer). Default is the trimmed
    view; ``--verbose`` restores the historical layout.
    """
    report = build_report(state)
    baseline = report["baseline"]

    if show_wordmark:
        console.print(welcome_panel(state, verbose=verbose))
        # First-run reassurance lives below the panel so the panel
        # itself stays a stable, recognisable nameplate. The marker is
        # written *after* a successful render so a crash on the
        # baseline panel doesn't suppress the tip on retry.
        paths = ClaudePaths(home=state.claude_home)
        if not verbose and is_first_run(paths):
            console.print(first_run_tip_line())
            mark_first_run_seen(paths)
        console.print("")
    console.print(render_baseline_panel(baseline, report["composition"]))
    console.print("")
    console.print(section_rule("findings"))
    curate = build_curate_findings(state)
    _render_findings_rich(report["findings"], console, curate_findings=curate)
    if verbose:
        _render_also_running(state, report["findings"], console)
    if report["warnings"]:
        console.print("")
        console.print(section_rule("warnings"))
        console.print("")
        for warning in report["warnings"]:
            row = Text()
            row.append(" ! ", style="#eab308")
            row.append(warning, style=DIM)
            console.print(row)
    if show_wordmark:
        console.print("")
        console.print(
            hint_bar(
                [
                    ("enter", "interactive fix"),
                    ("--json", "machine-readable"),
                    ("q", "quit"),
                ]
            )
        )


def _curate_breakdown(n_agents: int, n_skills: int, n_mcps: int = 0) -> str:
    """Return ``N agents, M skills, K remote MCPs`` with zero-category terms omitted."""
    parts: list[str] = []
    if n_agents:
        parts.append(f"{n_agents} agent{'s' if n_agents != 1 else ''}")
    if n_skills:
        parts.append(f"{n_skills} skill{'s' if n_skills != 1 else ''}")
    if n_mcps:
        parts.append(f"{n_mcps} remote MCP{'s' if n_mcps != 1 else ''}")
    return ", ".join(parts)


def _render_curate_clause(
    console: Console,
    *,
    n_agents: int,
    n_skills: int,
    n_mcps: int,
    total: int,
    standalone: bool,
) -> None:
    """Render a standalone curate-count line for the zero-findings path.

    When there are detector findings, the curate count rides on the same
    summary line. With zero findings we still want to pre-announce the
    curate step so the ``No issues found`` message isn't immediately
    followed by a surprise 179-row picker.
    """
    if not standalone or total == 0:
        return
    detail = _curate_breakdown(n_agents, n_skills, n_mcps)
    line = Text()
    line.append(f"{total}", style=f"bold {ACCENT}")
    line.append(" to curate", style=DIM)
    if detail:
        line.append(f" ({detail})", style=DIM)
    console.print(line)


def _render_findings_rich(
    findings: list[dict[str, Any]],
    console: Console,
    *,
    curate_findings: list[Finding] | None = None,
) -> None:
    """Print a one-line summary + grouped informational hints.

    The picker is the real findings UI — it shows every removable item
    with a live selection total. This block exists to (a) give users a
    count before the picker opens and (b) surface the flag_only items
    the picker can't show, grouped by type so we don't emit nine nearly
    identical lines when every project is missing a ``.claudeignore``.

    When ``curate_findings`` is non-empty, the summary tacks on a second
    clause (``179 items to curate (157 agents, 22 skills)``) so the
    user sees the full decision surface — detector issues + the
    curate-step inventory — before the picker opens. Prevents the
    surprise of a second picker appearing after "step 1 of 1" finishes.

    ``--report``/``--plain`` paths route through :func:`render_plain`
    instead and never hit this renderer.
    """
    console.print("")
    curate_findings = curate_findings or []
    n_curate_agents = sum(1 for f in curate_findings if f.type == "agent_inventory")
    n_curate_skills = sum(1 for f in curate_findings if f.type == "skill_inventory")
    n_curate_mcps = sum(1 for f in curate_findings if f.type == "unmeasured_mcp")

    if not findings:
        console.print(f"[{SEVERITY_OK}]✓[/{SEVERITY_OK}] [dim]No issues found.[/dim]")
        _render_curate_clause(
            console,
            n_agents=n_curate_agents,
            n_skills=n_curate_skills,
            n_mcps=n_curate_mcps,
            total=len(curate_findings),
            standalone=True,
        )
        return

    removable = [f for f in findings if f.get("action", {}).get("primitive") != "flag_only"]
    informational = [f for f in findings if f.get("action", {}).get("primitive") == "flag_only"]
    removable_tokens = sum(f.get("token_savings") or 0 for f in removable)

    summary = Text()
    summary.append(f"{len(findings)}", style=f"bold {ACCENT}")
    summary.append(" issue(s)", style=DIM)
    if removable:
        summary.append("  ·  ", style=DIM)
        summary.append(f"{len(removable)}", style=f"bold {SEVERITY_OK}")
        summary.append(" removable", style=DIM)
        if removable_tokens:
            summary.append(f" (~{removable_tokens:,} tok)", style=DIM)
    if informational:
        summary.append("  ·  ", style=DIM)
        summary.append(f"{len(informational)}", style=DIM)
        summary.append(" informational", style=DIM)
    if curate_findings:
        summary.append("  ·  ", style=DIM)
        summary.append(f"{len(curate_findings)}", style=f"bold {ACCENT}")
        summary.append(" to curate", style=DIM)
        detail = _curate_breakdown(n_curate_agents, n_curate_skills, n_curate_mcps)
        if detail:
            summary.append(f" ({detail})", style=DIM)
    console.print(summary)


def _render_also_running(
    state: InstallationState,
    findings: list[dict[str, Any]],
    console: Console,
) -> None:
    """Acknowledge MCPs and hooks we saw but chose not to flag.

    Verbose-only: in the trimmed default view this footer is too noisy
    — every row simply restates a row from the baseline panel above.
    ``--verbose`` brings it back for users who want the full audit
    trail.

    Without this block, a heavily-used MCP like ``Roblox_Studio``
    appears in the composition (because it contributes ~5k tokens) but
    is silent in the findings — users reasonably wonder why. Same for
    sound hooks that don't fire every turn. Listing them here closes
    the loop: "we saw these, they're working as intended, no action
    needed."

    Duplicate-safe: skips any server/hook already surfaced in
    ``findings`` so users never see the same name twice across the
    findings summary and the footer.
    """
    flagged_mcp_names: set[str] = set()
    for f in findings:
        server = f.get("action", {}).get("server_name")
        if isinstance(server, str):
            flagged_mcp_names.add(server)

    # MCPs: probed OK + has invocations → working as intended.
    invocations = state.global_scope.mcp_invocations
    probes = state.global_scope.mcp_probes or {}
    mcp_rows: list[tuple[str, int, int]] = []
    for name, probe in probes.items():
        if not probe.ok or name in flagged_mcp_names:
            continue
        count = invocations.get(name, 0)
        if count <= 0:
            continue
        tokens = probe.tools_tokens
        if tokens is None:
            continue
        mcp_rows.append((name, count, tokens))
    mcp_rows.sort(key=lambda r: (-r[1], r[0]))

    # Hooks: anything flagged as heavy_hook is already in the findings
    # summary, so we skip those commands and show the rest.
    heavy_cmds: set[str] = set()
    for f in findings:
        if f.get("type") != "heavy_hook":
            continue
        evidence = f.get("evidence") or {}
        cmd = evidence.get("command")
        if isinstance(cmd, str):
            heavy_cmds.add(cmd)

    hook_rows: list[tuple[str, str]] = []
    gs = state.global_scope
    if gs.settings is not None:
        for hook in gs.settings.hooks:
            if hook.command in heavy_cmds:
                continue
            hook_rows.append((hook.event, "global"))
    for project in state.project_scopes:
        for hook in project.hooks:
            if hook.command in heavy_cmds:
                continue
            hook_rows.append((hook.event, hook.source_scope))

    if not mcp_rows and not hook_rows:
        return

    console.print("")
    console.print(section_rule("also running"))
    console.print("")
    console.print(Text("no action needed — measured in baseline above", style=DIM))

    for name, count, tokens in mcp_rows:
        row = Text()
        row.append(f"  {_CONNECTOR} ", style=DIM)
        row.append("mcp ", style=f"bold {_INVENTORY_CHIP_COLOUR['mcp']}")
        row.append(name, style="default")
        row.append(f"  {count:,} invocations", style=DIM)
        if tokens:
            row.append(f", ~{tokens:,} tok/session", style=DIM)
        console.print(row)

    for event, source_scope in hook_rows:
        row = Text()
        row.append(f"  {_CONNECTOR} ", style=DIM)
        row.append("hook ", style=f"bold {_INVENTORY_CHIP_COLOUR['hooks']}")
        row.append(event, style="default")
        row.append(f"  ({source_scope})", style=DIM)
        console.print(row)


